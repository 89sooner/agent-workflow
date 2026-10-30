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
                   encode, now, reference_fingerprint, references_current, snapshot)
from .runner import RESULT_SCHEMA, brief, execute, record_review, run_checks
from .workspaces import observed_snapshot


def freshness(task, current):
    fp = current["fingerprint"]
    release = task.get("release") or {}
    # The release receipt binds the verified contents across staging/commit metadata changes.
    bound = bool(release.get("commit") == current["head"] and not current["dirty"])
    refs = reference_fingerprint(task)
    from .policy import protected_current
    protected = protected_current(task)
    verified_refs = references_current(task, task.get("verification"), refs)
    reviewed_refs = task.get("reviewer") == "none" or references_current(task, task.get("review"), refs)
    return {"verification_is_current": protected and verified_refs and ((task.get("verification") or {}).get("fingerprint") == fp or (bound and bool(task.get("verification")))),
            "review_is_current": protected and reviewed_refs and ((task.get("review") or {}).get("fingerprint") == fp or (bound and bool(task.get("review")))),
            "completion_is_current": protected and verified_refs and reviewed_refs and (task.get("completed_fingerprint") == fp or (bound and task["status"] == "completed")),
            "evidence_bound_via_release": bound and protected and verified_refs and reviewed_refs,
            "protected_paths_are_current": protected,
            "refs_fingerprint": refs}


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
수동 보류: {encode(task.get('hold'))}

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
    current = observed_snapshot(task)
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
    from .closure import worklog
    _, rendered = worklog(store, task, flags)
    atomic_write(directory / "worklog.md", rendered)
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
    c = commands.add_parser("browser-check", help="격리 HTTP 화면의 Playwright 수용 검사")
    c.add_argument("--file", type=Path, required=True)
    c = commands.add_parser("create", help="기존 WP/문서를 참조하는 작업 등록")
    c.add_argument("id")
    c.add_argument("--project")
    c.add_argument("--profile", help="저장한 프로젝트 설정 사용")
    c.add_argument("--isolate", action=argparse.BooleanOptionalAction, default=None)
    c.add_argument("--goal", required=True)
    c.add_argument("--criterion", action="append", required=True)
    c.add_argument("--ref", action="append", default=[])
    c.add_argument("--check", action="append", default=[])
    c.add_argument("--owner", choices=["codex", "claude"])
    c.add_argument("--reviewer", choices=["codex", "claude", "codex-session", "none"])
    c.add_argument("--scope", default="지정 목표의 로컬 구현·검증. 커밋·푸시·병합·배포 제외.")
    c = commands.add_parser("profile", help="프로젝트 실행 설정 저장·조회")
    c.add_argument("operation", choices=["save", "show", "list"])
    c.add_argument("name", nargs="?")
    c.add_argument("--file", type=Path)
    c = commands.add_parser("prepare", help="작업 환경 준비; 실패한 명령은 확인 후 재시도")
    c.add_argument("id")
    c.add_argument("--retry-setup", action="store_true")
    c.add_argument("--timeout", type=int, default=900)
    c = commands.add_parser("cleanup", help="소유한 작업 폴더 정리; 미반영 변경 보존")
    c.add_argument("id")
    c.add_argument("--remove-worktree", action="store_true")
    c.add_argument("--stop-services", action="store_true", help="강제 종료 뒤 남은 이 작업의 검사 서버만 정리")
    c = commands.add_parser("finalize", help="실제 상태로 인계·작업일지 내보내기")
    c.add_argument("id")
    c = commands.add_parser("execution-config", help="역할별 모델·호출·시간 정책 설정; 기존 사용량 보존")
    c.add_argument("id")
    c.add_argument("--file", type=Path, required=True)
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
    c = commands.add_parser("hold", help="기존 큐와 다음 실행 단계를 수동 보류; 실행 중 프로세스는 현 단계 종료")
    c.add_argument("id")
    c.add_argument("--reason", required=True)
    c = commands.add_parser("unhold", help="수동 보류 해제; 기존 대기 작업은 다시 실행 가능")
    c.add_argument("id")
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
    c.add_argument("--refs-fingerprint", help="참조 문서가 있을 때 검토 시작 시 show의 refs_fingerprint")
    c.add_argument("--reviewer", choices=["codex", "claude", "codex-session"], required=True)
    for name in ("check", "run"):
        c = commands.add_parser(name, help="필수 검사 실행" if name == "check" else "유한 구현→검사→리뷰→마감; 중단 후 같은 ID로 재개")
        c.add_argument("id")
        c.add_argument("--timeout", type=int, default=900 if name == "check" else None, help="각 프로세스 최대 초; 프로젝트 설정 또는 900")
        if name == "run":
            c.add_argument("--repairs", type=int, help="추가 수정 횟수; 프로젝트 설정 또는 1, 최대 3")
            c.add_argument("--recoveries", type=int, help="시간 초과·일시 오류 복구 횟수 0~3; 작업 설정 사용")
            c.add_argument("--dry-run", action="store_true", help="실행 계획만 출력")
    c = commands.add_parser("assign", help="중단/대기 작업의 담당 에이전트 변경")
    c.add_argument("id")
    c.add_argument("--owner", choices=["codex", "claude"], required=True)
    c.add_argument("--reviewer", choices=["codex", "claude", "codex-session", "none"], required=True)
    commands.add_parser("result-schema", help="리뷰 결과 JSON Schema 출력")
    c = commands.add_parser("release-config", help="자동 커밋·푸시·병합·배포 대상 등록")
    c.add_argument("id")
    c.add_argument("--remote", default="origin")
    c.add_argument("--base", default="main")
    c.add_argument("--branch", required=True)
    c.add_argument("--path", action="append", required=True)
    c.add_argument("--mode", choices=["github", "local"], default="github")
    c.add_argument("--repo", help="GitHub owner/repo")
    c.add_argument("--hostname", default="github.com", help="GitHub.com 또는 명시한 GitHub Enterprise 호스트")
    c.add_argument("--deploy", help="병합된 checkout에서 실행할 배포 명령")
    c.add_argument("--health", help="배포된 버전을 확인할 명령")
    c.add_argument("--allow-no-ci", action="store_true")
    c = commands.add_parser("release", help="완료 작업을 설정한 대상에 푸시·병합·배포")
    c.add_argument("id")
    c.add_argument("--timeout", type=int, default=300)
    c.add_argument("--retry-deploy", action="store_true")
    c = commands.add_parser("release-clear", help="시작 전 릴리스 설정 해제")
    c.add_argument("id")
    c = commands.add_parser("pipeline", help="구현·검증·리뷰와 설정된 릴리스를 연속 실행")
    c.add_argument("id")
    c.add_argument("--timeout", type=int)
    c.add_argument("--repairs", type=int)
    c.add_argument("--recoveries", type=int, help="복구 횟수 0~3; 작업 설정 사용")
    c.add_argument("--dry-run", action="store_true")
    c = commands.add_parser("queue", help="상주 데몬에 작업 등록")
    c.add_argument("id")
    c.add_argument("--action", choices=["pipeline", "release"], default="pipeline")
    c.add_argument("--timeout", type=int)
    c.add_argument("--repairs", type=int)
    c.add_argument("--recoveries", type=int, help="복구 횟수 0~3; 작업 설정 사용")
    commands.add_parser("jobs", help="대기·실행·종료 작업 확인")
    c = commands.add_parser("cancel", help="대기 중인 큐 작업 취소")
    c.add_argument("job_id")
    for name in ("stop", "resume"):
        c = commands.add_parser(name, help="실행 중인 작업 중단" if name == "stop" else "중단된 실행을 같은 설정으로 재개")
        c.add_argument("job_id")
    commands.add_parser("notifications", help="완료·실패·중단 알림 기록")
    c = commands.add_parser("depends", help="작업 시작 전 선행 조건 지정")
    c.add_argument("id")
    c.add_argument("--on", action="append", default=[])
    c.add_argument("--require", choices=["completed", "merged"], default="completed")
    c = commands.add_parser("batch", help="명시한 작업 목록의 제한된 연속 실행")
    c.add_argument("operation", choices=["create", "show", "list", "start", "pause"])
    c.add_argument("name", nargs="?")
    c.add_argument("--task", action="append", default=[])
    c.add_argument("--max-jobs", type=int, default=3)
    c.add_argument("--max-seconds", type=int, default=3600)
    c = commands.add_parser("service-install", help="systemd 또는 Bash 로그인 시 데몬 시작 설치")
    c.add_argument("--port", type=int, default=8776)
    c.add_argument("--enable", action="store_true")
    c.add_argument("--backend", choices=["auto", "systemd", "shell"], default="auto")
    c = commands.add_parser("serve", help="루프백 대시보드와 큐 데몬 전경 실행")
    c.add_argument("--port", type=int, default=8765)
    c = commands.add_parser("daemon", help="백그라운드 데몬 관리")
    c.add_argument("operation", choices=["start", "stop", "status"])
    c.add_argument("--port", type=int, default=8765)
    commands.add_parser("demo-setup", help="로컬 원격 저장소와 배포 시범 작업 준비")
    return p


def dispatch(args):
    if args.command == "browser-check":
        command = ["node", str(Path(__file__).with_name("browser-check.mjs")), str(args.file.resolve())]
        result = subprocess.run(command)
        if result.returncode:
            raise WorkflowError("브라우저 수용 검사 실패")
        return {"passed": True, "evidence": os.environ.get("WORKFLOW_EVIDENCE_DIR")}
    if args.command == "doctor":
        return doctor()
    if args.command == "result-schema":
        return RESULT_SCHEMA
    store = Store(args.home)
    try:
        if args.command == "batch":
            from . import scheduling
            if args.operation == "list":
                return scheduling.list_batches(store)
            if not args.name:
                raise WorkflowError("연속 실행 이름이 필요합니다.")
            if args.operation == "create":
                return scheduling.create(store, args.name, args.task, args.max_jobs, args.max_seconds)
            if args.operation == "show":
                return scheduling.get(store, args.name)
            return scheduling.control(store, args.name, args.operation)
        if args.command == "notifications":
            from .operations import notifications
            return notifications(store)
        if args.command == "service-install":
            from .operations import install_service
            return install_service(store, args.port, args.enable, backend=args.backend)
        if args.command == "profile":
            from . import profiles
            if args.operation == "list":
                return profiles.list_profiles(store)
            if not args.name:
                raise WorkflowError("프로필 이름이 필요합니다.")
            if args.operation == "show":
                return profiles.get(store, args.name)
            if not args.file:
                raise WorkflowError("profile save에는 --file JSON이 필요합니다.")
            return profiles.save(store, args.name, json.loads(args.file.read_text()))
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
        if args.command in ("queue", "jobs", "cancel", "stop", "resume"):
            from .daemon import enqueue, list_jobs, cancel, stop_job, resume_job
            if args.command == "queue":
                return enqueue(store, args.id, args.action, args.timeout, args.repairs, args.recoveries)
            if args.command == "jobs":
                return list_jobs(store)
            return {"cancel": cancel, "stop": stop_job, "resume": resume_job}[args.command](store, args.job_id)
        if args.command == "list":
            return [{k: t[k] for k in ("id", "status", "goal", "project", "owner", "reviewer", "updated_at")}
                    for t in store.all()]
        if args.command == "create":
            if args.profile:
                if args.project:
                    raise WorkflowError("프로필의 project를 사용합니다. --project와 함께 지정하지 마세요.")
                from .workspaces import create_from_profile
                return create_from_profile(store, args.id, args.profile, args.goal, args.criterion,
                                           args.ref, [argv_from(x) for x in args.check], args.owner,
                                           args.reviewer, args.scope, args.isolate)
            if args.isolate:
                raise WorkflowError("자동 worktree는 --profile과 함께 사용하세요.")
            return create_task(store, args.id, args.project or ".", args.goal, args.criterion,
                               args.ref, [argv_from(x) for x in args.check], args.owner or "claude",
                               args.reviewer or "codex", args.scope)
        task = store.get(args.id)
        if args.command in {"hold", "unhold"}:
            from .core import set_hold, clear_hold
            return set_hold(store, args.id, args.reason) if args.command == "hold" else clear_hold(store, args.id)
        if args.command == "depends":
            from .scheduling import set_dependencies
            return set_dependencies(store, args.id, args.on, args.require)
        if args.command == "execution-config":
            from .usage import configure
            return configure(store, args.id, json.loads(args.file.read_text()))
        if args.command == "release-clear":
            from .release import clear_configuration
            return clear_configuration(store, args.id)
        if args.command == "finalize":
            from .closure import finalize
            return finalize(store, task)
        if args.command == "cleanup":
            from .workspaces import cleanup
            if args.stop_services:
                from .services import stop_recorded
                stop_recorded(store, task)
            return cleanup(store, task, args.remove_worktree)
        if args.command == "prepare":
            from .workspaces import prepare
            from .policy import reservation
            if task["status"] == "completed":
                raise WorkflowError("완료된 작업의 환경을 변경하지 않습니다.")
            with store.lock(task["project"]) as fd, reservation(store, task) as resource_fd:
                return prepare(store, store.get(args.id), args.timeout, (fd, resource_fd), args.retry_setup)
        if args.command == "release-config":
            from .release import configure
            return configure(store, args.id, args.remote, args.base, args.branch, args.path,
                             args.mode, args.repo, argv_from(args.deploy) if args.deploy else None,
                             argv_from(args.health) if args.health else None, args.allow_no_ci, args.hostname)
        if args.command == "release":
            from .release import release_task
            from .observability import run_context
            if not 1 <= args.timeout <= 86400:
                raise WorkflowError("timeout 범위 오류")
            try:
                with run_context(store, task, "release"):
                    task = release_task(store, args.id, args.timeout, args.retry_deploy)
                    from .closure import finalize
                    return {"task": task, "closure": finalize(store, task)}
            finally:
                export_handoff(store, store.get(args.id))
        if args.command == "show":
            current = observed_snapshot(task)
            return {"task": task, "observed_git": current, **freshness(task, current)}
        if args.command == "brief":
            if args.role == "implement":
                return continuation(store, task, snapshot(task["project"]))
            return brief(task, args.role, state_home=store.home) + "\nJSON schema:\n" + encode(RESULT_SCHEMA)
        if args.command in ("run", "pipeline"):
            settings = task.get("execution") or {}
            args.timeout = settings.get("timeout", 900) if args.timeout is None else args.timeout
            args.repairs = settings.get("repairs", 1) if args.repairs is None else args.repairs
            if not 0 <= args.repairs <= 3 or not 1 <= args.timeout <= 86400:
                raise WorkflowError("repairs는 0~3, timeout은 1~86400초입니다.")
            if args.recoveries is not None and not 0 <= args.recoveries <= 3:
                raise WorkflowError("recoveries는 0~3입니다.")
            if args.dry_run:
                from .profiles import execution_config
                policy = execution_config(task.get("execution"))
                recoveries = args.recoveries if args.recoveries is not None else task.get("execution", {}).get("recoveries", 0)
                max_calls = min(max(0, policy["max_agent_calls"] - (task.get("usage") or {}).get("agent_calls", 0)),
                                (1 if task["reviewer"] == "none" else 2) * (args.repairs + 1) + recoveries)
                return {"task": task["id"], "owner": task["owner"], "reviewer": task["reviewer"],
                        "checks": task["checks"], "max_agent_calls": max_calls, "execution": policy, "usage": task.get("usage"),
                        "timeout_per_process": args.timeout, "project": task["project"],
                        "recoveries": args.recoveries if args.recoveries is not None else task.get("execution", {}).get("recoveries", 0),
                        "release": task.get("release"),
                        "mutations": "로컬 구현·검사 후 release-config가 있으면 커밋·푸시·병합·배포"}
            try:
                from .core import ensure_not_held
                from .observability import run_context
                with run_context(store, task, args.command):
                    ensure_not_held(store, task)
                    if task["status"] != "completed":
                        task = execute(store, task["id"], args.timeout, args.repairs, args.recoveries)
                    elif not task.get("release") and not freshness(task, snapshot(task["project"]))["completion_is_current"]:
                        raise WorkflowError("완료 이후 코드가 바뀌었습니다. 새 작업으로 검증하세요.")
                    if task.get("release"):
                        from .release import release_task
                        task = release_task(store, task["id"], args.timeout)
                    from .closure import finalize
                    return {"task": task, "closure": finalize(store, task)}
            except WorkflowError:
                export_handoff(store, store.get(task["id"]))
                if not os.environ.get("AGENT_WORKFLOW_JOB_ID"):
                    from .operations import publish_notification
                    publish_notification(store, store.get(task["id"]))
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
                task.update(owner=args.owner, reviewer=args.reviewer, review=None, native_review_receipt=None, status="ready")
                store.save(task, "assigned")
            elif args.command == "check":
                from .policy import reservation
                if not 1 <= args.timeout <= 86400:
                    raise WorkflowError("timeout은 1~86400초입니다.")
                with reservation(store, task) as resource_fd:
                    passed = run_checks(store, task, args.timeout, (fd, resource_fd))
                if not passed:
                    export_handoff(store, task)
                    raise WorkflowError(task["blocker"])
            elif args.command == "review-import":
                result = json.loads(args.file.read_text())
                record_review(store, task, result, args.fingerprint, args.reviewer, args.refs_fingerprint)
            elif args.command == "finish":
                complete(store, task)
                from .closure import finalize
                return {"task": task, "closure": finalize(store, task, locked=True)}
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
