"""Human and agent entry point; all project mutations are explicit commands."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shlex
import shutil
import signal
import subprocess
import sys

from . import __version__
from .core import (Store, WorkflowError, atomic_write, complete, create_task,
                   encode, now, snapshot)
from .runner import RESULT_SCHEMA, brief, execute, record_review, run_checks


def freshness(task, current):
    fp = current["fingerprint"]
    release = task.get("release") or {}
    # The release receipt binds the verified contents across staging/commit metadata changes.
    bound = bool(release.get("commit") == current["head"] and not current["dirty"])
    return {"verification_is_current": (task.get("verification") or {}).get("fingerprint") == fp or (bound and bool(task.get("verification"))),
            "review_is_current": (task.get("review") or {}).get("fingerprint") == fp or (bound and bool(task.get("review"))),
            "completion_is_current": task.get("completed_fingerprint") == fp or (bound and task["status"] == "completed"),
            "evidence_bound_via_release": bound}


def continuation(store, task, current):
    command = shlex.join(["agent-workflow", "--home", str(store.home)])
    text = f"""대화형 작업 인계: {task['id']}
목표: {task['goal']}
프로젝트: {task['project']}
완료 조건: {encode(task['criteria'])}
허용 범위: {task['scope']}
참고 문서: {encode(task['refs'])}
저장 상태: {task['status']}
현재 증거 유효성: {encode(freshness(task, current))}
체크포인트: {encode(task['checkpoint'])}
차단 사유: {task.get('blocker') or '없음'}

먼저 다음 명령으로 현재 상태를 다시 확인하고 관련 소스·문서와 대조하세요.
{command} show {task['id']}
과거 보고를 현재 완료·승인 증거로 복사하지 마세요.
"""
    if task["status"] == "completed":
        text += "이 작업은 기록상 완료됐습니다. 재구현하지 말고 다음 WP/통합 단계를 확인하세요.\n"
        if task.get("release"):
            text += f"릴리스 단계: {task['release']['stage']}. 이어가기: {command} release {task['id']}\n"
        if not freshness(task, current)["completion_is_current"]:
            text += "완료 이후 코드가 변경되어 현재 코드에는 과거 완료 증거가 유효하지 않습니다. 새 작업으로 검증하세요.\n"
    else:
        text += f"""다음 행동에서 이어서 작업하고, 의미 있는 진전이나 중단 시 checkpoint를 갱신하세요.
필수 검증: {command} check {task['id']}
독립 검토: {task['reviewer']}. 필요한 경우 brief --role review로 입력을 만들어 전달하세요.
검사·지정 리뷰 완료 후: {command} finish {task['id']}
인계 갱신: {command} handoff {task['id']}
실제 실행 결과·미검증 범위·다음 행동을 사용자에게 보고하세요.
자동 실행을 요청받은 경우에만 {command} run {task['id']}를 사용하세요.
"""
    return text


def export_handoff(store, task):
    """One state snapshot yields both machine continuation and a human worklog draft."""
    current = snapshot(task["project"])
    directory = store.directory(task["id"]) / "handoff"
    flags = freshness(task, current)
    record = {"task": task, "observed_git": current, "generated_at": now(), **flags}
    atomic_write(directory / "state.json", encode(record))
    atomic_write(directory / "resume.txt", continuation(store, task, current))
    lines = [f"# {task['id']} — {task['goal']}", "", f"생성 시각: {now()}",
             f"상태: {task['status']}", f"프로젝트: {task['project']}",
             f"실행: {task['owner']} / 검토: {task['reviewer']}", "",
             f"현재 HEAD: {current['head']}", f"코드 지문: {current['fingerprint']}",
             f"현재 코드에 검사/리뷰/완료 증거 유효: {encode(flags)}",
             "실제 배포/병합 여부: 이 도구의 completed 상태로는 확인되지 않음", "",
             "릴리스 기록: " + encode(task.get("release")), "",
             "## 결과", task["checkpoint"]["summary"], "",
             "## 다음 행동", task["checkpoint"]["next"], "",
             "## 차단 사유", task.get("blocker") or "없음", "",
             "## 필수 검사"]
    for r in (task.get("verification") or {}).get("results", []):
        lines.append(f"- `{shlex.join(r['argv'])}` → exit {r['exit_code']}; {r['stdout']}")
    lines.extend(["", "## 리뷰", (task.get("review") or {}).get("summary", "미실행/미등록"),
                  "", "## 참고 문서", *[f"- {p}" for p in task["refs"]]])
    atomic_write(directory / "worklog.md", "\n".join(lines) + "\n")
    return str(directory)


def argv_from(text):
    try:
        argv = shlex.split(text)
    except ValueError as exc:
        raise WorkflowError("검사 명령의 따옴표 오류") from exc
    if not argv:
        raise WorkflowError("빈 검사 명령")
    if any(x in {"&&", "||", ";", "|", ">", ">>", "<"} for x in argv):
        raise WorkflowError("검사 명령은 shell 없이 실행합니다. 각 명령을 별도 --check로 등록하세요.")
    return argv


def doctor():
    result = {"version": __version__, "python": sys.version.split()[0], "agents": {}}
    for name in ("git", "codex", "claude"):
        path = shutil.which(name)
        item = {"path": path, "available": bool(path)}
        if path:
            try:
                p = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=15)
                item.update(version=p.stdout.strip(), exit_code=p.returncode)
            except subprocess.TimeoutExpired:
                item["error"] = "version query timed out"
        result["agents"][name] = item
    result["note"] = "설치 확인입니다. 계정 한도·인증·실제 모델 응답 성공은 run 결과로 확인합니다."
    return result


def parser():
    p = argparse.ArgumentParser(description="Codex/Claude 공용 로컬 작업·검증·인계 도구")
    p.add_argument("--version", action="version", version=__version__)
    p.add_argument("--home", help="상태 저장 경로 (기본 ~/.local/state/agent-workflow)")
    commands = p.add_subparsers(dest="command", required=True)
    commands.add_parser("doctor", help="설치된 도구 확인; 모델 호출 없음")
    commands.add_parser("list", help="등록된 작업 목록")
    c = commands.add_parser("create", help="기존 WP/문서를 참조하는 작업 등록")
    c.add_argument("id")
    c.add_argument("--project", default=".")
    c.add_argument("--goal", required=True)
    c.add_argument("--criterion", action="append", required=True)
    c.add_argument("--ref", action="append", default=[])
    c.add_argument("--check", action="append", default=[])
    c.add_argument("--owner", choices=["codex", "claude"], default="claude")
    c.add_argument("--reviewer", choices=["codex", "claude", "none"], default="codex")
    c.add_argument("--scope", default="지정 목표의 로컬 구현·검증. 커밋·푸시·병합·배포 제외.")
    for name, help_text in [("show", "실제 Git 상태와 저장된 증거 보기"),
                            ("finish", "검사·리뷰 증거 확인 후 완료 처리"),
                            ("handoff", "재개 프롬프트와 worklog 초안 생성")]:
        s = commands.add_parser(name, help=help_text)
        s.add_argument("id")
    c = commands.add_parser("checkpoint", help="대화형 작업에서 상태·다음 행동 저장")
    c.add_argument("id")
    c.add_argument("--summary", required=True)
    c.add_argument("--next", required=True)
    c.add_argument("--blocked", help="외부 입력 등 구체적 차단 사유")
    c = commands.add_parser("check-add", help="사용자가 승인한 검사 명령 추가; 기존 증거 무효화")
    c.add_argument("id")
    c.add_argument("argv", help="예: 'python3 -m unittest discover -s tests'")
    c = commands.add_parser("brief", help="현재 상태에서 실행/리뷰 입력 생성; 모델 호출 없음")
    c.add_argument("id")
    c.add_argument("--role", choices=["implement", "review"], default="implement")
    c = commands.add_parser("review-import", help="대화형 독립 검토 결과 JSON 등록")
    c.add_argument("id")
    c.add_argument("--file", type=Path, required=True)
    c.add_argument("--fingerprint", required=True, help="검토 시작 시 show의 observed_git.fingerprint")
    c.add_argument("--reviewer", choices=["codex", "claude"], required=True)
    for name in ("check", "run"):
        c = commands.add_parser(name, help="필수 검사 실행" if name == "check" else "유한 구현→검사→리뷰→마감; 중단 후 같은 ID로 재개")
        c.add_argument("id")
        c.add_argument("--timeout", type=int, default=900, help="각 프로세스 최대 초; 기본 900")
        if name == "run":
            c.add_argument("--repairs", type=int, default=1, help="수정 재시도 최대 횟수; 기본 1, 최대 3")
            c.add_argument("--dry-run", action="store_true", help="실행 계획만 출력")
    c = commands.add_parser("assign", help="중단/대기 작업의 담당 에이전트 변경")
    c.add_argument("id")
    c.add_argument("--owner", choices=["codex", "claude"], required=True)
    c.add_argument("--reviewer", choices=["codex", "claude", "none"], required=True)
    commands.add_parser("result-schema", help="리뷰 결과 JSON Schema 출력")
    c = commands.add_parser("release-config", help="자동 커밋·푸시·병합·배포 대상 등록")
    c.add_argument("id")
    c.add_argument("--remote", default="origin")
    c.add_argument("--base", default="main")
    c.add_argument("--branch", required=True)
    c.add_argument("--path", action="append", required=True)
    c.add_argument("--mode", choices=["github", "local"], default="github")
    c.add_argument("--repo", help="GitHub owner/repo")
    c.add_argument("--deploy", help="병합된 checkout에서 실행할 배포 명령")
    c.add_argument("--health", help="배포된 버전을 확인할 명령")
    c.add_argument("--allow-no-ci", action="store_true")
    c = commands.add_parser("release", help="완료 작업을 설정한 대상에 푸시·병합·배포")
    c.add_argument("id")
    c.add_argument("--timeout", type=int, default=300)
    c.add_argument("--retry-deploy", action="store_true")
    c = commands.add_parser("pipeline", help="구현·검증·리뷰와 설정된 릴리스를 연속 실행")
    c.add_argument("id")
    c.add_argument("--timeout", type=int, default=900)
    c.add_argument("--repairs", type=int, default=1)
    c.add_argument("--dry-run", action="store_true")
    c = commands.add_parser("queue", help="상주 데몬에 작업 등록")
    c.add_argument("id")
    c.add_argument("--action", choices=["pipeline", "release"], default="pipeline")
    c.add_argument("--timeout", type=int, default=900)
    c.add_argument("--repairs", type=int, default=1)
    commands.add_parser("jobs", help="대기·실행·종료 작업 확인")
    c = commands.add_parser("cancel", help="대기 중인 큐 작업 취소")
    c.add_argument("job_id")
    c = commands.add_parser("serve", help="루프백 대시보드와 큐 데몬 전경 실행")
    c.add_argument("--port", type=int, default=8765)
    c = commands.add_parser("daemon", help="백그라운드 데몬 관리")
    c.add_argument("operation", choices=["start", "stop", "status"])
    c.add_argument("--port", type=int, default=8765)
    commands.add_parser("demo-setup", help="로컬 원격 저장소와 배포 시범 작업 준비")
    return p


def dispatch(args):
    if args.command == "doctor":
        return doctor()
    if args.command == "result-schema":
        return RESULT_SCHEMA
    store = Store(args.home)
    try:
        if args.command == "demo-setup":
            from .demo import setup_demo
            return setup_demo(store)
        if args.command in ("daemon", "serve"):
            from .server import start_background, stop_background, status, serve
            if not 0 <= args.port <= 65535:
                raise WorkflowError("포트 범위 오류")
            if args.command == "serve":
                serve(store.home, args.port)
                return {"stopped": True}
            if args.operation == "start":
                return start_background(store.home, args.port)
            return stop_background(store.home) if args.operation == "stop" else status(store.home)
        if args.command in ("queue", "jobs", "cancel"):
            from .daemon import enqueue, list_jobs, cancel
            if args.command == "queue":
                return enqueue(store, args.id, args.action, args.timeout, args.repairs)
            return list_jobs(store) if args.command == "jobs" else cancel(store, args.job_id)
        if args.command == "list":
            return [{k: t[k] for k in ("id", "status", "goal", "project", "owner", "reviewer", "updated_at")}
                    for t in store.all()]
        if args.command == "create":
            return create_task(store, args.id, args.project, args.goal, args.criterion,
                               args.ref, [argv_from(x) for x in args.check], args.owner,
                               args.reviewer, args.scope)
        task = store.get(args.id)
        if args.command == "release-config":
            from .release import configure
            return configure(store, args.id, args.remote, args.base, args.branch, args.path,
                             args.mode, args.repo, argv_from(args.deploy) if args.deploy else None,
                             argv_from(args.health) if args.health else None, args.allow_no_ci)
        if args.command == "release":
            from .release import release_task
            if not 1 <= args.timeout <= 86400:
                raise WorkflowError("timeout 범위 오류")
            try:
                task = release_task(store, args.id, args.timeout, args.retry_deploy)
                return {"task": task, "handoff": export_handoff(store, task)}
            finally:
                export_handoff(store, store.get(args.id))
        if args.command == "show":
            current = snapshot(task["project"])
            return {"task": task, "observed_git": current, **freshness(task, current)}
        if args.command == "brief":
            if args.role == "implement":
                return continuation(store, task, snapshot(task["project"]))
            return brief(task, args.role) + "\nJSON schema:\n" + encode(RESULT_SCHEMA)
        if args.command in ("run", "pipeline"):
            if not 0 <= args.repairs <= 3 or not 1 <= args.timeout <= 86400:
                raise WorkflowError("repairs는 0~3, timeout은 1~86400초입니다.")
            if args.dry_run:
                return {"task": task["id"], "owner": task["owner"], "reviewer": task["reviewer"],
                        "checks": task["checks"], "max_agent_calls": (1 if task["reviewer"] == "none" else 2) * (args.repairs + 1),
                        "timeout_per_process": args.timeout, "project": task["project"],
                        "release": task.get("release"),
                        "mutations": "로컬 구현·검사 후 release-config가 있으면 커밋·푸시·병합·배포"}
            try:
                if task["status"] != "completed":
                    task = execute(store, task["id"], args.timeout, args.repairs)
                elif not task.get("release") and not freshness(task, snapshot(task["project"]))["completion_is_current"]:
                    raise WorkflowError("완료 이후 코드가 바뀌었습니다. 새 작업으로 검증하세요.")
                if task.get("release"):
                    from .release import release_task
                    task = release_task(store, task["id"], args.timeout)
                return {"task": task, "handoff": export_handoff(store, task)}
            except WorkflowError:
                export_handoff(store, store.get(task["id"]))
                raise
        with store.lock(task["project"]) as fd:
            task = store.get(task["id"])
            if args.command == "handoff":
                return {"directory": export_handoff(store, task)}
            if task["status"] == "completed":
                raise WorkflowError("완료된 작업의 계약은 변경하지 않습니다. 새 작업을 만드세요.")
            if args.command == "checkpoint":
                task["checkpoint"] = {"summary": args.summary, "next": args.next, "at": now()}
                task["blocker"] = args.blocked
                task["status"] = "blocked" if args.blocked else "ready"
                store.save(task, "checkpoint", task["checkpoint"])
            elif args.command == "check-add":
                task["checks"].append(argv_from(args.argv))
                task.update(verification=None, review=None, status="ready")
                store.save(task, "check_added")
            elif args.command == "assign":
                if args.owner == args.reviewer:
                    raise WorkflowError("실행 담당과 검토자는 달라야 합니다.")
                task.update(owner=args.owner, reviewer=args.reviewer, review=None, status="ready")
                store.save(task, "assigned")
            elif args.command == "check":
                if not 1 <= args.timeout <= 86400:
                    raise WorkflowError("timeout은 1~86400초입니다.")
                if not run_checks(store, task, args.timeout, fd):
                    export_handoff(store, task)
                    raise WorkflowError(task["blocker"])
            elif args.command == "review-import":
                result = json.loads(args.file.read_text())
                record_review(store, task, result, args.fingerprint, args.reviewer)
            elif args.command == "finish":
                complete(store, task)
            return {"task": task, "handoff": export_handoff(store, task)}
    finally:
        store.close()


def main():
    os.umask(0o077)
    def interrupted(signum, frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, interrupted)
    args = parser().parse_args()
    try:
        result = dispatch(args)
        print(result if isinstance(result, str) else encode(result))
    except (WorkflowError, OSError, ValueError, subprocess.TimeoutExpired, KeyboardInterrupt) as exc:
        print(f"agent-workflow: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc


if __name__ == "__main__":
    main()
