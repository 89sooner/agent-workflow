"""Exercise the documented OpenCode recovery after an interrupted operator shell."""

import json
import os
from pathlib import Path
import re
import secrets
import signal
import socket
import subprocess
import sys
import tempfile
import time
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, build_opener

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from agent_workflow.connections import connect, ledger_id
from agent_workflow.core import Store


def runbook_blocks():
    text = (REPO / "docs/OPERATIONS.ko.md").read_text(encoding="utf-8")
    pattern = r"<!-- opencode-runbook:([a-z-]+) -->\n```bash\n(.*?)^```$"
    blocks = {}
    for name, commands in re.findall(pattern, text, re.M | re.S):
        assert name not in blocks, f"Duplicate OpenCode runbook block: {name}"
        blocks[name] = commands
    return blocks


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def wait_for(check, label, *, process=None):
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        if process is not None and process.poll() is not None:
            raise RuntimeError(f"{label}: process exited with {process.returncode}")
        try:
            value = check()
            if value:
                return value
        except (FileNotFoundError, URLError):
            pass
        time.sleep(0.2)
    raise TimeoutError(label)


def wait_for_ledgers(dashboard, source, endpoint, workflow_ids):
    # The operator waits for the address to change on the same page. The existing
    # Chromium test covers that page, so wait on its connection endpoint here.
    opener = build_opener(ProxyHandler({}))

    def current():
        with opener.open(dashboard + "/api/connections", timeout=5) as response:
            rows = json.load(response)["ledgers"]
        ledgers = {row["id"]: row for row in rows}
        if set(ledgers) != workflow_ids | {source}:
            return None
        if any(row["status"] != "available" for row in rows) or ledgers[source]["home"] != endpoint:
            return None
        return ledgers

    return wait_for(current, "Dashboard did not expose the original ledger IDs at " + endpoint)


def stop_process(process):
    if process is not None and process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def main(binary):
    blocks = runbook_blocks()

    with tempfile.TemporaryDirectory(prefix="workflow-opencode-runbook-") as directory:
        root = Path(directory)
        project = root / "project"
        project.mkdir()
        executable = root / "bin"
        executable.mkdir()
        (executable / "opencode").symlink_to(binary)
        subprocess.run(["git", "init", "--quiet", str(project)], check=True)

        common_env = {
            "PATH": os.pathsep.join([str(executable), str(Path(sys.executable).parent), os.defpath]),
            "HOME": str(root),
            "OPENCODE_DISABLE_AUTOUPDATE": "true",
            "OPENCODE_DISABLE_MODELS_FETCH": "true",
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        service_env = {
            **common_env,
            "TMPDIR": str(root),
            **{f"XDG_{name}_HOME": str(root / name.lower()) for name in ("CONFIG", "STATE", "DATA", "CACHE")},
        }
        config = root / "config/opencode/service.json"
        config.parent.mkdir(parents=True)
        runtime = root / "state/opencode/service.json"
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            original_port = listener.getsockname()[1]
        config.write_text(json.dumps({"port": original_port, "password": secrets.token_urlsafe(32)}) + "\n")
        config.chmod(0o600)
        original_bytes = config.read_bytes()

        home = root / ".local/state/agent-workflow"
        other = root / "other-ledger"
        for ledger in (home, other):
            Store(ledger).close()
        connect(home, project, [home, other], include_default=False,
                include_processes=False, include_temporary=False)
        workflow_ids = {ledger_id(home), ledger_id(other)}

        workflow = [sys.executable, "-m", "agent_workflow", "--home", str(home)]
        native = ["opencode", "service"]
        dashboard = first_shell = None
        try:
            subprocess.run([*native, "start"], env=service_env, cwd=project, check=True, timeout=35)
            original = read_json(runtime)
            assert original["version"] == "2.2.7"
            assert original["url"] == f"http://127.0.0.1:{original_port}"
            connection = subprocess.check_output(
                [*workflow, "opencode", "connect", original["url"], "--project", str(project),
                 "--follow-local-service"], cwd=REPO, env=service_env, text=True, timeout=15,
            )
            source = json.loads(connection)["source"]["id"]
            registries = {path: path.read_bytes() for path in (home / "connections.json", home / "opencode.json")}
            dashboard = subprocess.Popen([*workflow, "serve", "--port", "0"], cwd=REPO, env=service_env)
            server_file = home / "server.json"
            server = wait_for(lambda: read_json(server_file), "Dashboard readiness", process=dashboard)
            dashboard_url = server["url"]
            wait_for_ledgers(dashboard_url, source, original["url"], workflow_ids)

            ready = root / "rotation-complete"
            first_script = root / "change.bash"
            first_script.write_text("".join(blocks[name] for name in (
                "prepare", "ledger-helpers", "preflight", "service-helpers", "rotate",
            )) + '\nprintf "%s\\n" rotated > "$1"\nread -r wait_for_close\n')
            first_shell = subprocess.Popen(
                ["bash", "-e", str(first_script), str(ready)], stdin=subprocess.PIPE,
                cwd=project, env={**service_env, "AW_DASHBOARD_URL": dashboard_url},
            )
            wait_for(ready.is_file, "Documented port and password change", process=first_shell)
            first_shell.kill()
            assert first_shell.wait(timeout=5) == -signal.SIGKILL
            rotated = read_json(runtime)
            assert rotated["pid"] != original["pid"] and rotated["url"] != original["url"]
            assert rotated["password"] != original["password"], "Effective password did not change"
            print("First Bash terminated with SIGKILL after changing the port and password.", flush=True)
            wait_for_ledgers(dashboard_url, source, rotated["url"], workflow_ids)

            saved_paths, = root.glob("aw-opencode-rotation.*/paths.bash")
            recovery_env = {**common_env, "AW_OPENCODE_RECOVERY_DIR": str(saved_paths.parent)}
            # No XDG paths or Bash functions from the killed shell are inherited.
            recovery = "".join(blocks[name] for name in (
                "load-saved-paths", "ledger-helpers", "service-helpers", "restore",
            ))
            recovery += """
for attempt in {1..40}; do
  if aw_ledgers "$oc_original_url" > /dev/null; then break; fi
  sleep 1
done
"""
            subprocess.run(["bash", "-e"], input=recovery + blocks["verify-restore"],
                           text=True, cwd=project, env=recovery_env, check=True, timeout=80)
            wait_for_ledgers(dashboard_url, source, original["url"], workflow_ids)

            restored = read_json(runtime)
            assert restored["pid"] != rotated["pid"]
            assert restored["url"] == original["url"]
            assert restored["password"] == original["password"], "Original credential was not restored"
            assert config.read_bytes() == original_bytes, "Original settings bytes were not restored"
            assert read_json(server_file)["pid"] == server["pid"]
            assert all(file.read_bytes() == contents for file, contents in registries.items())
            rotated_port = urlsplit(rotated["url"]).port
            print(f"OpenCode runbook recovery passed: {original_port} -> {rotated_port} -> {original_port}, "
                  f"original credential 200, temporary credential 401, connection {source}, "
                  f"{len(workflow_ids)} existing workflow ledgers preserved.", flush=True)
        finally:
            stop_process(first_shell)
            if first_shell is not None:
                first_shell.stdin.close()
            stop_process(dashboard)
            if runtime.exists():
                subprocess.run([*native, "stop"], env=service_env, cwd=project, check=True, timeout=20)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python tests/opencode_runbook_recovery.py OPEN_CODE_2_2_7_BINARY")
    main(Path(sys.argv[1]).resolve(strict=True))
