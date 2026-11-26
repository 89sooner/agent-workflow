"""Human and agent entry point; all project mutations are explicit commands."""
from __future__ import annotations

import argparse
from datetime import date
import json
import os
from pathlib import Path
import shlex
import shutil
import signal
import subprocess
import sys

from . import __version__
from .communication import AGENT_COMMUNICATION_POLICY
from .core import (Store, WaitingForInput, WorkflowError, atomic_write, complete, create_task,
                   encode, now, snapshot)
from .runner import RESULT_SCHEMA, brief, execute, record_review, run_checks
from .task_observation import freshness, observe


def continuation(store, task, current=None, *, view=None):
    view = observe(task, store.home, current=current) if view is None else view
    command = shlex.join(["agent-workflow", "--home", str(store.home)])
    task_id = task["id"]
    lines = [AGENT_COMMUNICATION_POLICY, "Interactive task handoff.",
             "Read the current ledger packet before acting. Treat it as evidence within the recorded scope.",
             f"{command} resume-view {task_id}",
             f"Read original receipt pointers under task in: {command} show {task_id}",
             "This saved packet records one observation. Refresh it after the worktree or ledger changes.",
             encode(view["resume_packet"])]
    if task.get("status") == "completed":
        lines.append("Do not implement this task again.")
        if not view["freshness"].get("completion_is_current"):
            lines.append("Create a new task to verify the current code.")
        if task.get("release"):
            lines.append(f"To resume the authorized release, follow the current packet before using {command} release {task_id}.")
    else:
        lines.append("If the user requested automatic execution, follow the next action and existing authorization.")
    return "\n".join(lines) + "\n"


def write_handoff_snapshot(store, task, view):
    directory = store.directory(task["id"]) / "handoff"
    record = {"task": task, "generated_at": now(), "observed_git": view["observed_git"], **view["freshness"],
              "recovery": view["recovery"], "resume_packet": view["resume_packet"]}
    atomic_write(directory / "state.json", encode(record))
    atomic_write(directory / "resume.txt", continuation(store, task, view=view))
    return directory


def export_handoff(store, task):
    """Record one observed packet in both the machine handoff and the interactive prompt."""
    task = store.get(task["id"])
    view = observe(task, store.home)
    directory = write_handoff_snapshot(store, task, view)
    from .closure import worklog
    _, rendered = worklog(store, task, view)
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
    c = commands.add_parser("patterns", help="Claude·Codex 입력 이력의 반복 업무와 세션별 동시 사용 집계")
    c.add_argument("--source", action="append", type=Path, help="history.jsonl 경로. 여러 번 지정할 수 있습니다.")
    c.add_argument("--since", type=date.fromisoformat, help="집계 시작일 (UTC, YYYY-MM-DD)")
    c.add_argument("--until", type=date.fromisoformat, help="집계 종료일 (UTC, 해당일 포함)")
    for name, description in (("goal", "프로젝트 프로필로 로컬 개발 목표 연속 실행"),
                              ("katakuri-goal", "Katakuri 오프라인 개발 목표 연속 실행")):
        c = commands.add_parser(name, help=description)
        actions = c.add_subparsers(dest="operation", required=True)
        sub = actions.add_parser("create")
        sub.add_argument("id")
        sub.add_argument("--profile", required=True)
        sub.add_argument("--goal", required=True)
        sub.add_argument("--criterion", action="append", default=[])
        sub.add_argument("--check-group", action="append", default=[])
        sub.add_argument("--max-reported-tokens", type=int)
        if name == "katakuri-goal":
            sub.add_argument("--instrument")
            sub.add_argument("--milestone", choices=[f"M{i}" for i in range(1, 7)])
        for operation in ("run", "status"):
            actions.add_parser(operation).add_argument("id")
        sub = actions.add_parser("supply", help="대기 조건에 새 오프라인 증거 연결")
        sub.add_argument("id")
        sub.add_argument("--criterion", type=int, required=True)
        sub.add_argument("--ref", required=True)
        sub.add_argument("--queue", action="store_true", help="근거 저장 후 데몬 실행 대기열에 등록")
        sub = actions.add_parser("budget", help="누적 사용량을 보존하며 예산 조정")
        sub.add_argument("id")
        sub.add_argument("--max-agent-calls", type=int)
        sub.add_argument("--max-seconds", type=int)
        tokens = sub.add_mutually_exclusive_group()
        tokens.add_argument("--max-reported-tokens", type=int)
        tokens.add_argument("--clear-token-limit", action="store_true")
    c = commands.add_parser("recommend-checks", help="Git 변경과 완료 조건으로 등록된 검사 묶음 추천")
    c.add_argument("profile")
    c.add_argument("--criterion", action="append", required=True)
    c.add_argument("--project", help="등록 프로젝트 또는 연결된 Git 작업 트리")
    c.add_argument("--base", help="비교 기준 Git 커밋 또는 ref (기본 HEAD)")
    c.add_argument("--provider", choices=["codex", "claude"], default="codex")
    c.add_argument("--reviewer", choices=["auto", "none", "codex", "claude"], default="auto")
    c.add_argument("--timeout", type=int, default=90, help="모델 호출당 최대 초 (1~600)")
    recording = c.add_mutually_exclusive_group()
    recording.add_argument("--preview", action="store_true", help="모델에 보낼 입력과 공급자만 출력")
    recording.add_argument("--record", action="store_true", help="완전한 추천의 선택 기록을 원장에 저장하고 ID 반환")
    c = commands.add_parser("recommendation", help="저장된 추천, 작업별 검사 실측 및 사람의 평가")
    actions = c.add_subparsers(dest="operation", required=True)
    actions.add_parser("show").add_argument("id")
    actions.add_parser("report").add_argument("--profile")
    f = actions.add_parser("feedback")
    f.add_argument("task_id")
    labels = f.add_mutually_exclusive_group(required=True)
    labels.add_argument("--expected-group", action="append", metavar="GROUP")
    labels.add_argument("--expected-none", action="store_true")
    c = commands.add_parser("evaluate-checks", help="저장한 추천과 필요한 검사 묶음 대조")
    c.add_argument("report", type=Path)
    labels = c.add_mutually_exclusive_group(required=True)
    labels.add_argument("--expected-group", action="append", metavar="GROUP")
    labels.add_argument("--expected-none", action="store_true")
    for command, description in (
        ("measure-checks", "같은 변경에서 단독·보조 추천과 두 리비전의 전체 등록 검사 결과 비교"),
        ("measure-review", "같은 변경에서 구현 보고 공개 여부에 따른 독립 코드 리뷰 비교"),
    ):
        c = commands.add_parser(command, help=description)
        c.add_argument("profile")
        c.add_argument("--base", required=True, help="기준 로컬 Git 리비전")
        c.add_argument("--candidate", required=True, help="측정할 변경의 로컬 Git 리비전")
        c.add_argument("--criterion", action="append", required=True)
        c.add_argument("--reviewer", choices=["codex", "claude"], default="claude")
        c.add_argument("--timeout", type=int, default=90, help="모델 호출당 제한 시간(초)")
        if command == "measure-checks":
            c.add_argument("--provider", choices=["codex", "claude"], default="codex")
            c.add_argument("--check-timeout", type=int, default=30, help="검사 명령당 제한 시간(초)")
        else:
            c.add_argument("--implementation-report", required=True, help="보고 공개 조건에서만 모델에 제공할 문구")
            c.add_argument("--report-first", action="store_true", help="보고서 공개 조건을 먼저 측정")
            truth = c.add_mutually_exclusive_group(required=True)
            truth.add_argument("--clean", action="store_true", help="독립적으로 정상임을 확인한 대조 변경")
            truth.add_argument("--expected-defect", type=Path, help="모델에 보내지 않는 알려진 결함 채점 규칙 JSON")
    c = commands.add_parser("close-session", help="작업 ID 없이 현재 Git 상태, 작업일지와 다음 행동 저장")
    c.add_argument("--project", default=".")
    c.add_argument("--summary", required=True, help="이번 세션에서 수행한 작업과 확인 결과")
    c.add_argument("--next", dest="next_action", required=True, help="다음 세션이 이어받을 행동")
    c.add_argument("--worklog-dir", type=Path, help="작업일지를 함께 저장할 기존 폴더")
    c = commands.add_parser("resume-view", help="선택한 원장에서 현재 목표·남은 조건·검증 근거·다음 행동 조회")
    c.add_argument("id")
    c = commands.add_parser("pickup", help="현재 브랜치의 마지막 인계 및 저장 후 Git 상태 변화 확인")
    c.add_argument("--project", default=".")
    c.add_argument("--classic", action="store_true", help="기존 체크포인트 형식으로 조회")
    c.add_argument("--measure", action="store_true", help="이 재개의 첫 작업 시작까지 측정")
    c = commands.add_parser("resume-time", help="현재 작업 트리·브랜치의 재개 및 마감 시간 측정")
    actions = c.add_subparsers(dest="operation", required=True)
    for name in ("start", "start-close", "report"):
        sub = actions.add_parser(name)
        sub.add_argument("--project", default=".")
    sub = actions.add_parser("finish", help="처음 올바른 작업을 시작할 때 측정 종료")
    sub.add_argument("id", nargs="?")
    sub.add_argument("--project", default=".")
    mistakes = sub.add_mutually_exclusive_group()
    mistakes.add_argument("--wrong-pick", action="store_true", help="올바른 작업 전에 잘못된 작업을 한 번 골랐음")
    mistakes.add_argument("--wrong-picks", type=int, help="올바른 작업 전에 잘못 고른 작업 횟수")
    sub.add_argument("--reexplanations", type=int, default=0, help="다시 설명한 횟수")
    used = sub.add_mutually_exclusive_group()
    used.add_argument("--used-stale-evidence", dest="stale_use", action="store_true", help="오래된 근거를 현재 판정에 사용")
    used.add_argument("--no-stale-evidence", dest="stale_use", action="store_false", help="오래된 근거를 현재 판정에 사용하지 않음")
    sub.set_defaults(stale_use=None)
    actions.add_parser("abandon").add_argument("id")
    c = commands.add_parser("opencode", help="OpenCode 서버 읽기 전용 관측 연결; 모델 실행 없음")
    oc = c.add_subparsers(dest="opencode_command", required=True)
    o = oc.add_parser("connect", help="로컬 OpenCode 서버를 한 번 연결하면 새 세션 자동 관측")
    o.add_argument("url")
    o.add_argument("--project", required=True, help="관측할 OpenCode 작업 디렉터리")
    o.add_argument("--name", default="OpenCode")
    o.add_argument("--password-env", help="OpenCode 서버 비밀번호를 담은 환경변수 이름")
    o.add_argument("--username", default="opencode")
    o.add_argument("--follow-local-service", action="store_true",
                   help="OpenCode 2.2.7 로컬 서비스의 현재 주소와 인증 사용")
    oc.add_parser("list", help="저장된 OpenCode 연결 조회")
    o = oc.add_parser("disconnect", help="관측 연결만 해제; OpenCode 세션·서버 보존")
    o.add_argument("id")
    c = commands.add_parser("native", help="WP 없는 세 도구 관측 설치·등록·진단; 실행 없음")
    c.add_argument("operation", choices=["install", "uninstall", "register", "unregister", "status"])
    c.add_argument("--provider", choices=["claude", "codex", "opencode"], default="claude")
    c.add_argument("--project", required=True)
    c = commands.add_parser("connect", help="프로젝트 원장 자동 발견·연결; 기존 원장 변경·실행 없음")
    c.add_argument("project", nargs="?", default=".")
    c.add_argument("--ledger", action="append", default=[], help="관측할 기존 상태 경로; 여러 번 지정 가능")
    c.add_argument("--profile", help="새 작업에 재사용할 저장된 프로필")
    c.add_argument("--criterion", action="append", help="새 작업의 기본 완료 조건")
    c.add_argument("--scope", help="새 작업의 기본 허용 범위")
    c.add_argument("--registration-home", help="연결된 원장 중 새 작업을 등록할 경로")
    c = commands.add_parser("start", help="연결한 프로젝트에 작업 자동 ID 등록; 실행은 별도")
    c.add_argument("goal")
    c.add_argument("--isolate", action=argparse.BooleanOptionalAction, default=None)
    c.add_argument("--project", default=".")
    c.add_argument("--criterion", action="append")
    c.add_argument("--check", action="append", default=[])
    c.add_argument("--recommendation", metavar="ID", help="저장한 추천 선택을 사용")
    selection = c.add_mutually_exclusive_group()
    selection.add_argument("--check-group", action="append", metavar="GROUP", help="채택할 추가 검사 묶음")
    selection.add_argument("--base-checks-only", dest="check_group", action="store_const", const=[],
                           help="추가 묶음을 모두 제외하고 공통 검사만 사용")
    c.add_argument("--owner", choices=["codex", "claude"])
    c.add_argument("--reviewer", choices=["codex", "claude", "codex-session", "none"])
    c.add_argument("--name", help="구현 에이전트에게 실제 할당한 이름")
    c.add_argument("--reviewer-name", help="검토 에이전트에게 실제 할당한 이름")
    c = commands.add_parser("watch", help="연결된 여러 원장을 조회만 하는 관제실; worker 시작 안 함")
    c.add_argument("--port", type=int, default=8877)
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
    c.add_argument("--recommendation", metavar="ID", help="저장한 추천 선택을 사용")
    selection = c.add_mutually_exclusive_group()
    selection.add_argument("--check-group", action="append", metavar="GROUP", help="채택할 추가 검사 묶음")
    selection.add_argument("--base-checks-only", dest="check_group", action="store_const", const=[],
                           help="추가 묶음을 모두 제외하고 공통 검사만 사용")
    c.add_argument("--owner", choices=["codex", "claude"])
    c.add_argument("--reviewer", choices=["codex", "claude", "codex-session", "none"])
    c.add_argument("--scope", default="지정 목표의 로컬 구현·검증. 커밋·푸시·병합·배포 제외.")
    c = commands.add_parser("profile", help="프로젝트 실행 설정 저장·조회")
    c.add_argument("operation", choices=["save", "show", "list", "checks"])
    c.add_argument("--check-group", action="append", default=[], metavar="GROUP",
                   help="profile checks에서 미리 볼 검사 묶음")
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
    c = commands.add_parser("release-status", help="원장에 기록된 로컬 검증·병합·배포·상태 검사 결과 조회")
    c.add_argument("id")
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
    c.add_argument("--observe-revision", help="배포 결과 불명 시 대상에 적용된 전체 커밋 SHA를 stdout으로 읽는 명령")
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
    c.add_argument("--read-only", action="store_true", help="기존 DB 조회만 제공; 큐 worker·스키마 초기화·변경 API 비활성화")
    c = commands.add_parser("daemon", help="백그라운드 데몬 관리")
    c.add_argument("operation", choices=["start", "stop", "status"])
    c.add_argument("--port", type=int, default=8765)
    commands.add_parser("demo-setup", help="로컬 원격 저장소와 배포 시범 작업 준비")
    return p


def dispatch(args):
    from . import goal_policy
    if args.command in ("goal", "katakuri-goal"):
        if args.command == "katakuri-goal":
            from . import katakuri_goal as engine
        else:
            from . import goals as engine
        store = Store(args.home, read_only=args.operation == "status")
        try:
            if args.operation == "create":
                domain = {"instrument": args.instrument, "milestone": args.milestone} if args.command == "katakuri-goal" else {}
                created = engine.create(store, args.id, args.profile, args.goal, args.criterion,
                                        args.check_group, args.max_reported_tokens, **domain)
                return engine.status(created)
            task = store.get(args.id)
            engine.contract(task)
            if args.operation == "run":
                return engine.status(execute(store, args.id, task["execution"]["timeout"]))
            if args.operation == "supply":
                result = engine.supply(store, args.id, args.criterion, args.ref)
                if args.queue:
                    from .daemon import enqueue
                    result["job"] = enqueue(store, args.id, "pipeline")
                return result
            if args.operation == "budget":
                return engine.budget(store, args.id, args.max_agent_calls, args.max_seconds, args.max_reported_tokens, args.clear_token_limit)
            result = engine.status(task)
            view = observe(task, store.home)
            result.update(recovery=view["recovery"], resume_packet=view["resume_packet"])
            if any(row.get("check_evidence", {}).get("result_id") for row in result["criteria"]):
                result["verification_is_current"] = view["freshness"].get("verification_is_current")
            if view["observed_git"].get("error"):
                result["observation_error"] = view["observed_git"]["error"]
            return result
        finally:
            store.close()
    if args.command == "recommend-checks":
        from .check_advisor import recommend
        return recommend(args.home, args.profile, args.criterion, project=args.project, base=args.base,
                         provider=args.provider, reviewer=args.reviewer, preview=args.preview, timeout=args.timeout,
                         record_result=args.record)
    if args.command == "recommendation":
        from . import recommendation_records as records
        if args.operation == "show":
            return records.show(args.home, args.id)
        if args.operation == "feedback":
            return records.feedback(args.home, args.task_id, args.expected_group or [])
        return records.summary(args.home, args.profile)
    if args.command == "evaluate-checks":
        from .check_advisor import evaluate
        return evaluate(args.report, args.expected_group or [])
    if args.command == "measure-checks":
        from .check_measurement import measure
        return measure(args.home, args.profile, args.base, args.candidate, args.criterion,
                       provider=args.provider, reviewer=args.reviewer, timeout=args.timeout,
                       check_timeout=args.check_timeout)
    if args.command == "measure-review":
        from .review_measurement import measure
        defect = None
        if args.expected_defect is not None:
            with args.expected_defect.open("rb") as stream:
                raw = stream.read(16385)
            if len(raw) > 16384:
                raise WorkflowError("채점 규칙은 16 KiB 이내의 JSON 객체여야 합니다.")
            try:
                defect = json.loads(raw)
            except (ValueError, RecursionError) as exc:
                raise WorkflowError("채점 규칙 파일의 JSON을 읽을 수 없습니다.") from exc
            if not isinstance(defect, dict):
                raise WorkflowError("채점 규칙에는 결함을 설명하는 JSON 객체가 필요합니다.")
        return measure(args.home, args.profile, args.base, args.candidate, args.criterion,
                       reviewer=args.reviewer, implementation_report=args.implementation_report,
                       expected_defect=defect, timeout=args.timeout, blind_first=not args.report_first)
    if args.command == "release-status":
        from .release_status import status
        return status(args.home, args.id)
    if args.command == "create" and (args.check_group is not None or args.recommendation) and not args.profile:
        raise WorkflowError("검사 묶음과 추천 ID를 사용하려면 --profile이 필요합니다.")
    if args.command == "profile":
        if args.operation == "checks":
            from . import profiles
            if not args.name:
                raise WorkflowError("검사 계획을 조회할 프로필 이름이 필요합니다.")
            store = Store(args.home, read_only=True)
            try:
                config = profiles.get(store, args.name)["config"]
                return {"profile": args.name, "available_groups": sorted(config.get("check_groups", {})),
                        **profiles.check_plan(config, args.check_group)}
            finally:
                store.close()
        if args.check_group:
            raise WorkflowError("profile --check-group은 checks에서 사용하세요.")
    if args.command == "patterns":
        from .history_patterns import summarize
        return summarize(args.source, since=args.since, until=args.until)
    if args.command in {"close-session", "pickup"}:
        from .session_notes import close_note, pickup
        if args.command == "close-session":
            return close_note(args.home, args.project, args.summary, args.next_action, args.worklog_dir)
        return pickup(args.home, args.project, measure=args.measure, classic=args.classic)
    if args.command == "resume-time":
        from . import resume_metrics
        if args.operation == "start":
            return resume_metrics.start_manual(args.home, args.project)
        if args.operation == "start-close":
            return resume_metrics.start_close(args.home, args.project)
        if args.operation == "abandon":
            return resume_metrics.abandon(args.home, args.id)
        if args.operation == "finish":
            return resume_metrics.finish(args.home, args.id, args.project, wrong_pick=args.wrong_pick,
                                         wrong_picks=args.wrong_picks, reexplanations=args.reexplanations,
                                         stale_evidence_used=args.stale_use)
        return resume_metrics.summary(args.home, args.project)
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
    if args.command == "opencode":
        from . import opencode
        if args.opencode_command == "connect":
            return opencode.connect(args.home, args.url, args.project, args.name, args.password_env,
                                    args.username, follow_local_service=args.follow_local_service)
        if args.opencode_command == "disconnect":
            return opencode.disconnect(args.home, args.id)
        return opencode.sources(args.home)
    if args.command == "native":
        from .native import install, register, status
        if args.operation == "status":
            return status(args.home, args.project, args.provider)
        if args.operation in {"register", "unregister"}:
            return register(args.home, args.project, args.provider, args.operation == "unregister")
        return install(args.home, args.project, args.operation == "uninstall", args.provider)
    if args.command == "connect":
        from .connections import connect
        return connect(args.home, args.project, args.ledger, args.profile, args.criterion, args.scope,
                       args.registration_home)
    if args.command == "start":
        from .connections import start_registered
        return start_registered(args.home, args.goal, args.project, args.criterion,
                                checks=[argv_from(x) for x in args.check], owner=args.owner,
                                reviewer=args.reviewer, name=args.name, reviewer_name=args.reviewer_name,
                                check_groups=args.check_group, recommendation_id=args.recommendation, isolate=args.isolate)
    if args.command == "watch":
        from .connections import state_home
        from .server import serve
        if not 0 <= args.port <= 65535:
            raise WorkflowError("포트 범위 오류")
        serve(state_home(args.home), args.port, read_only=True, observer=True)
        return {"stopped": True}
    if args.command == "serve" and args.read_only:
        from .server import serve
        if not 0 <= args.port <= 65535:
            raise WorkflowError("포트 범위 오류")
        from .connections import state_home
        serve(state_home(args.home), args.port, read_only=True)
        return {"stopped": True}
    store = Store(args.home, read_only=args.command in ("show", "resume-view", "brief"))
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
                                           args.reviewer, args.scope, args.isolate, check_groups=args.check_group,
                                           recommendation_id=args.recommendation)
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
                             argv_from(args.health) if args.health else None, args.allow_no_ci, args.hostname,
                             observe_revision=argv_from(args.observe_revision) if args.observe_revision else None)
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
        if args.command in ("show", "resume-view"):
            view = observe(task, store.home)
            if args.command == "resume-view":
                return view["resume_packet"]
            return {"task": task, "observed_git": view["observed_git"], **view["freshness"],
                    "recovery": view["recovery"], "resume_packet": view["resume_packet"]}
        if args.command == "brief":
            if args.role == "implement":
                return continuation(store, task)
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
                if goal_policy.state(task):
                    from .goals import status
                    return {**status(task), "checks": task["checks"], "dry_run": True}
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
                if goal_policy.state(task) and args.reviewer == "none":
                    raise WorkflowError("목표의 독립 검토자를 지정하세요.")
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
        if args.command == "recommend-checks" and result["status"] == "review_incomplete":
            raise SystemExit(3)
        if args.command in ("measure-checks", "measure-review") and result["status"] != "complete":
            raise SystemExit(3)
    except (WorkflowError, OSError, ValueError, subprocess.TimeoutExpired, KeyboardInterrupt) as exc:
        print(f"agent-workflow: {exc}", file=sys.stderr)
        raise SystemExit(WaitingForInput.exit_code if isinstance(exc, WaitingForInput) else 2) from exc


if __name__ == "__main__":
    main()
