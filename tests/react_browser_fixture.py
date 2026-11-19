"""Disposable Git repositories, SQLite receipts, and synthetic agent processes for React browser tests."""
import json
from pathlib import Path
import sys
from unittest.mock import patch

from agent_workflow.core import Store, WorkflowError, create_task, git
from agent_workflow.profiles import save as save_profile
from agent_workflow.runner import execute
from agent_workflow.workspaces import create_from_profile


def answer(outcome, summary):
    return {"outcome": outcome, "summary": summary, "next_action": "검사와 독립 리뷰 확인", "findings": []}


def fixture_command(agent, role, directory, result):
    if agent == "codex":
        return [sys.executable, "-c", "from pathlib import Path; import sys; sys.stdin.read(); Path(sys.argv[1]).write_text(sys.argv[2]); print('synthetic codex fixture')",
                str(directory / "answer.json"), json.dumps(result)]
    return [sys.executable, "-c", "import sys; sys.stdin.read(); print(sys.argv[1])",
            json.dumps({"subtype": "success", "structured_output": result})]


def prepare(root):
    project = root / "demo-project"
    project.mkdir()
    git(project, "init", "-q")
    (project / "app.py").write_text("VALUE = 1\n")
    (project / ".gitignore").write_text("__pycache__/\n")
    git(project, "add", ".")
    git(project, "-c", "user.name=UI Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-qm", "Synthetic observability fixture")
    home = root / "state"
    store = Store(home)
    checks = [[sys.executable, "-c", "import app; assert app.VALUE == 1; print('demo check: VALUE=1'); print('<script>window.unsafe=true</script>')"]]
    try:
        create_task(store, "OBS-LIVE", project, "시각화 검증 데모 · 실시간 구현과 보류", ["실제 검사 프로세스가 통과한다"], [], checks, "claude", "codex", "임시 데모 저장소만 사용")
        task = create_task(store, "OBS-TRACE", project, "시각화 검증 데모 · 검색 결과 정렬을 구현하고 리뷰하기", ["검사와 독립 리뷰를 통과한다", "동일 점수의 결과도 안정적인 순서로 표시한다"], [], checks, "claude", "codex", "임시 데모 저장소만 사용")
        with patch("agent_workflow.runner.command_for", side_effect=lambda agent, role, directory, model=None: fixture_command(agent, role, directory, answer("blocked", "데모: 입력 확인이 필요해 중단"))):
            try:
                execute(store, task["id"], timeout=5, repairs=0)
            except WorkflowError:
                pass
        reviews = 0
        def command(agent, role, directory, model=None):
            nonlocal reviews
            if role == "review":
                reviews += 1
                result = answer("changes_requested" if reviews == 1 else "pass", "데모 리뷰: 경계 조건 수정 요청" if reviews == 1 else "데모 리뷰: 수정 후 검증 통과")
            else:
                result = answer("ready", "데모 구현: 검색 결과 정렬과 경계 조건 처리")
            return fixture_command(agent, role, directory, result)
        with patch("agent_workflow.runner.command_for", side_effect=command):
            execute(store, task["id"], timeout=5, repairs=1)
    finally:
        store.close()
    binary = root / "fixture-bin"; binary.mkdir()
    script = """#!/usr/bin/env python3
import json, os, sys, time
from pathlib import Path
sys.stdin.read()
time.sleep(4)
review = Path(sys.argv[0]).name == 'codex'
answer = {'outcome': 'pass' if review else 'ready', 'summary': 'SYNTHETIC FIXTURE: 실제 CLI가 아닌 브라우저 검증용 프로세스 결과', 'next_action': '검사와 리뷰 확인', 'findings': []}
if review:
    Path(sys.argv[sys.argv.index('--output-last-message') + 1]).write_text(json.dumps(answer))
    print(json.dumps({'type':'turn.completed','usage':{'input_tokens':11,'output_tokens':4}}))
else:
    print(json.dumps({'subtype':'success','structured_output':answer}))
"""
    for name in ("claude", "codex"):
        path = binary / name; path.write_text(script); path.chmod(0o755)
    return {"home": str(home), "project": str(project), "bin": str(binary)}


def prepare_workspace(root):
    fixture = prepare(root)
    other = root / "other-project"
    other.mkdir()
    git(other, "init", "-q")
    (other / "app.py").write_text("VALUE = 1\n")
    git(other, "add", ".")
    git(other, "-c", "user.name=UI Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-qm", "Independent synthetic workspace")
    store = Store(fixture["home"])
    checks = [[sys.executable, "-c", "import app; assert app.VALUE == 1"]]
    try:
        live = store.get("OBS-LIVE")
        live["agent_names"] = {"implement": "지우 · 실제 배정", "review": "민수 · 독립 리뷰"}
        store.save(live, "agent_names_assigned", {"names": live["agent_names"]})
        for name, project in (("primary-demo", fixture["project"]), ("secondary-demo", str(other))):
            save_profile(store, name, {"version": 1, "project": project, "checks": checks})
        isolated = create_from_profile(store, "SAAS-WORKTREE", "primary-demo",
                                       "시각화 검증 데모 · 같은 원본 저장소의 별도 작업 폴더", ["원본 프로젝트에 함께 표시한다"])
        create_from_profile(store, "SAAS-OTHER", "secondary-demo",
                            "시각화 검증 데모 · 두 번째 프로젝트의 독립 작업", ["프로젝트 선택으로 분리한다"], isolate=False)
    finally:
        store.close()
    release = root / "release-agent"
    # Browser checks release this process only after setting hold, avoiding
    # timing-dependent races between software rendering and a short sleep.
    wait = """release = Path(os.environ['WORKFLOW_SAAS_RELEASE'])
deadline = time.monotonic() + 90
while not release.exists() and time.monotonic() < deadline:
    time.sleep(.05)
if not release.exists():
    raise SystemExit('Synthetic browser controller did not release the fixture')"""
    for name in ("claude", "codex"):
        path = Path(fixture["bin"]) / name
        path.write_text(path.read_text().replace("time.sleep(4)", wait))
    fixture.update(other_project=str(other), isolated_project=isolated["project"], release_file=str(release))
    return fixture
