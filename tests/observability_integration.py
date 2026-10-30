"""Real HTTP/SQLite/queue browser acceptance with explicitly synthetic agent CLIs.

python3 tests/observability_integration.py             # browser assertions
python3 tests/observability_integration.py --serve-only # inspect disposable demo
No model service, user repository, or external deployment is contacted.
"""
import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE))
from agent_workflow.core import Store, WorkflowError, create_task, git
from agent_workflow.runner import execute


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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serve-only", action="store_true")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="workflow-observatory-ui-") as tmp:
        root = Path(tmp)
        fixture = prepare(root)
        with (root / "server.log").open("w") as log:
            process = subprocess.Popen([sys.executable, "-m", "agent_workflow", "--home", fixture["home"], "serve", "--port", "0"],
                                       cwd=SOURCE, stdout=log, stderr=log, start_new_session=True,
                                       env={**os.environ, "PATH": fixture["bin"] + os.pathsep + os.environ["PATH"], "PYTHONDONTWRITEBYTECODE": "1"})
            try:
                deadline = time.monotonic() + 15
                meta = Path(fixture["home"]) / "server.json"
                while not meta.exists():
                    if process.poll() is not None or time.monotonic() >= deadline:
                        raise RuntimeError("Isolated server failed: " + (root / "server.log").read_text())
                    time.sleep(.05)
                fixture["url"] = json.loads(meta.read_text())["url"]
                manifest = root / "fixture.json"; manifest.write_text(json.dumps(fixture))
                print("SYNTHETIC OBSERVATORY DEMO " + fixture["url"], flush=True)
                if args.serve_only:
                    process.wait()
                    return process.returncode
                result = subprocess.run(["node", "tests/observability_scenarios.mjs", str(manifest)], cwd=SOURCE, timeout=180)
                return result.returncode
            except KeyboardInterrupt:
                return 0
            finally:
                if process.poll() is None:
                    process.send_signal(signal.SIGTERM)
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    process.kill(); process.wait()


if __name__ == "__main__":
    raise SystemExit(main())
