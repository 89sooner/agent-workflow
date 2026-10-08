"""Opt-in live CLI check on a disposable repository; never run by unittest discovery."""
import argparse
import json
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent_workflow.core import Store, WorkflowError, create_task, git
from agent_workflow.runner import execute
from agent_workflow.cli import export_handoff


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--owner", choices=["claude", "codex"], default="claude")
    parser.add_argument("--reviewer", choices=["claude", "codex", "none"], default="codex")
    parser.add_argument("--timeout", type=int, default=180)
    args = parser.parse_args()
    artifacts = Path(__file__).resolve().parents[1] / "artifacts"
    artifacts.mkdir(exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="live-", dir=artifacts))
    project = root / "project"
    project.mkdir()
    (project / "SPEC.md").write_text("Create answer.txt containing exactly 42 followed by a newline. Do not change any other file.\n")
    (project / "check.py").write_text("from pathlib import Path\nassert Path('answer.txt').read_text() == '42\\n'\nprint('answer verified')\n")
    git(project, "init", "-q")
    git(project, "add", ".")
    git(project, "-c", "user.name=Smoke", "-c", "user.email=smoke@example.invalid", "commit", "-qm", "fixture")
    store = Store(root / "state")
    task = create_task(store, "LIVE-SMOKE", project, "Create answer.txt as specified in SPEC.md",
                       ["answer.txt is exactly 42 plus a newline; other files unchanged"], ["SPEC.md"],
                       [[sys.executable, "check.py"]], args.owner, args.reviewer, "Only local answer.txt creation")
    print(f"fixture: {root}", flush=True)
    error = None
    try:
        execute(store, task["id"], args.timeout, repairs=0)
    except WorkflowError as exc:
        error = str(exc)
    finally:
        task = store.get(task["id"])
        path = export_handoff(store, task)
        summary = {"fixture": str(root), "owner": args.owner, "reviewer": args.reviewer,
                   "status": task["status"], "error": error, "handoff": path}
        (root / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2))
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        store.close()
    return 0 if task["status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
