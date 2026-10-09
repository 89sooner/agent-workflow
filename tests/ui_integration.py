"""Serve a disposable empty state and exercise the dashboard with intercepted API fixtures."""
import json
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time


def main():
    source = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="workflow-ui-") as tmp:
        root = Path(tmp)
        with (root / "server.log").open("w") as log:
            process = subprocess.Popen([sys.executable, "-m", "agent_workflow", "--home", str(root), "serve", "--port", "0"],
                                       cwd=source, stdout=log, stderr=log, start_new_session=True)
            try:
                deadline = time.monotonic() + 15
                while not (root / "server.json").exists():
                    if process.poll() is not None or time.monotonic() >= deadline:
                        raise RuntimeError("Isolated server failed: " + (root / "server.log").read_text())
                    time.sleep(.05)
                url = json.loads((root / "server.json").read_text())["url"]
                result = subprocess.run(["node", "tests/ui_scenarios.mjs", url], cwd=source, timeout=180)
                return result.returncode
            finally:
                if process.poll() is None:
                    process.send_signal(signal.SIGTERM)
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    process.kill(); process.wait()


if __name__ == "__main__":
    raise SystemExit(main())
