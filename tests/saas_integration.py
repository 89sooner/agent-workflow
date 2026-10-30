"""SaaS shell and actual Three.js acceptance on disposable local workflow data.

python3 tests/saas_integration.py
python3 tests/saas_integration.py --serve-only

The controller, Git worktrees, SQLite, HTTP API and queue are real. Agent CLI
processes are explicitly synthetic and never contact a model or deploy a service.
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

SOURCE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE))
from agent_workflow.core import Store, git
from agent_workflow.profiles import save as save_profile
from agent_workflow.workspaces import create_from_profile
from observability_integration import prepare


def prepare_saas(root, gated=True):
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
        for name, project in (("primary-demo", fixture["project"]), ("secondary-demo", str(other))):
            save_profile(store, name, {"version": 1, "project": project, "checks": checks})
        isolated = create_from_profile(store, "SAAS-WORKTREE", "primary-demo",
                                       "시각화 검증 데모 · 같은 원본 저장소의 별도 작업 폴더", ["원본 프로젝트에 함께 표시한다"])
        create_from_profile(store, "SAAS-OTHER", "secondary-demo",
                            "시각화 검증 데모 · 두 번째 프로젝트의 독립 작업", ["프로젝트 선택으로 분리한다"], isolate=False)
    finally:
        store.close()
    release = root / "release-agent"
    if gated:
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serve-only", action="store_true")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="workflow-saas-ui-") as tmp:
        root = Path(tmp)
        fixture = prepare_saas(root, gated=not args.serve_only)
        with (root / "server.log").open("w") as log:
            process = subprocess.Popen(
                [sys.executable, "-m", "agent_workflow", "--home", fixture["home"], "serve", "--port", "0"],
                cwd=SOURCE, stdout=log, stderr=log, start_new_session=True,
                env={**os.environ, "PATH": fixture["bin"] + os.pathsep + os.environ["PATH"],
                     "WORKFLOW_SAAS_RELEASE": fixture["release_file"], "PYTHONDONTWRITEBYTECODE": "1"})
            try:
                deadline = time.monotonic() + 15
                meta = Path(fixture["home"]) / "server.json"
                while not meta.exists():
                    if process.poll() is not None or time.monotonic() >= deadline:
                        raise RuntimeError("Isolated SaaS server failed: " + (root / "server.log").read_text())
                    time.sleep(.05)
                fixture["url"] = json.loads(meta.read_text())["url"]
                manifest = root / "fixture.json"
                manifest.write_text(json.dumps(fixture))
                print("SYNTHETIC 3D SAAS DEMO " + fixture["url"], flush=True)
                if args.serve_only:
                    process.wait()
                    return process.returncode
                result = subprocess.run(["node", "tests/saas_scenarios.mjs", str(manifest)], cwd=SOURCE, timeout=240)
                return result.returncode
            except KeyboardInterrupt:
                return 0
            finally:
                Path(fixture["release_file"]).touch()
                if process.poll() is None:
                    process.send_signal(signal.SIGTERM)
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()


if __name__ == "__main__":
    raise SystemExit(main())
