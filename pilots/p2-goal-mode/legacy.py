"""Exercise an existing Katakuri ledger across a goal-engine upgrade, without native accounts."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[2]
HISTORICAL = "9b0c443da3e72fc490ed487289b5ef62ae0242cf"
ID = "KB-P2-LEGACY"
OPERATOR_INPUT = "HSI M2 offline fixture: observation received."
DOCS = ["AGENTS.md", "REFACTORING_SUPREME_DIRECTIVE.md", "agent-context/session-handoff.md"]
MODEL = '''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
case = Path(os.environ["P2_FIXTURE"])
prompt = sys.stdin.read()
queue = case / "answers.json"
steps = json.loads(queue.read_text())
action = steps.pop(0)
queue.write_text(json.dumps(steps))
if "write" in action:
    path = action.pop("write")
    if path not in ("app.py", "protected.txt", "notes.txt"):
        raise ValueError("unknown fixture action")
    Path(path).write_text("done")
    with (case / "writes").open("a") as out:
        print(path, file=out)
if "require_input" in action:
    assert action.pop("require_input") in prompt
tokens = {"input_tokens": 7, "output_tokens": 3}
if Path(sys.argv[0]).name == "codex":
    assert Path("app.py").read_text() == "done"
    Path(sys.argv[sys.argv.index("--output-last-message") + 1]).write_text(json.dumps(action))
    print(json.dumps({"type":"turn.completed", "usage":tokens}))
else:
    print(json.dumps({"subtype":"success", "structured_output":action, "usage":tokens}))
'''
CHECK = '''from pathlib import Path
import os
assert Path("app.py").read_text() == "done"
assert Path("protected.txt").read_text() == "baseline"
with (Path(os.environ["P2_FIXTURE"]) / "checks").open("a") as log:
    print("checked", file=log)
'''


def package_digest(engine):
    h = hashlib.sha256()
    for p in sorted((engine / "agent_workflow").glob("*.py")):
        h.update(p.name.encode() + b"\0" + p.read_bytes() + b"\0")
    return h.hexdigest()


def archived_engine(directory):
    """Keep only Python sources from the known pre-generalization Git revision."""
    files = subprocess.check_output(["git", "-C", str(ROOT), "ls-tree", "--name-only", "-r", HISTORICAL,
                                     "--", "agent_workflow"], text=True).splitlines()
    (directory / "agent_workflow").mkdir(parents=True)
    for name in files:
        if Path(name).parent == Path("agent_workflow") and name.endswith(".py"):
            (directory / name).write_bytes(subprocess.check_output(
                ["git", "-C", str(ROOT), "show", f"{HISTORICAL}:{name}"]))
    return directory


def step(number, outcome="ready", **kwargs):
    return {"criterion": number, "outcome": outcome, "summary": f"Offline criterion {number}.",
            "next_action": "Check the registered evidence.", **kwargs}


class Pilot:
    def __init__(self, root):
        self.root = Path(root)
        self.repo = self.root / "katakuri-band"
        self.home = self.root / "ledger"
        self.env = {"HOME": str(self.root), "P2_FIXTURE": str(self.root),
                    "PATH": os.pathsep.join([str(self.root / "bin"), str(Path(sys.executable).parent), os.defpath]),
                    "PYTHONDONTWRITEBYTECODE": "1", "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
                    "GIT_AUTHOR_DATE": "2026-01-01T00:00:00Z", "GIT_COMMITTER_DATE": "2026-01-01T00:00:00Z"}

    def cli(self, engine, *args, code=0):
        result = subprocess.run([sys.executable, "-B", "-m", "agent_workflow", "--home", str(self.home), *args],
                                cwd=self.root, env={**self.env, "PYTHONPATH": str(engine)}, text=True,
                                capture_output=True, timeout=45)
        assert result.returncode == code, (args, result.returncode, result.stdout[-1500:], result.stderr)
        return result

    def status(self, engine, family):
        return json.loads(self.cli(engine, family, "status", ID).stdout)

    def task(self, engine, ident=ID):
        return json.loads(self.cli(engine, "show", ident).stdout)["task"]

    def queue(self, *actions):
        (self.root / "answers.json").write_text(json.dumps(actions))

    def sql(self, sql, *params):
        with sqlite3.connect((self.home / "state.sqlite3").as_uri() + "?mode=ro", uri=True) as db:
            return db.execute(sql, params).fetchall()

    def before(self, engine):
        self.repo.mkdir()
        for name in [*DOCS, "app.py", "protected.txt", "notes.txt"]:
            p = self.repo / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("baseline")
        for argv in (["init", "-qb", "main"], ["add", "."],
                     ["-c", "user.name=Pilot", "-c", "user.email=pilot@example.invalid", "commit", "-qm", "baseline"]):
            subprocess.run(["git", *argv], cwd=self.repo, env=self.env, check=True)
        (self.root / "bin").mkdir()
        for name in ("claude", "codex"):
            exe = self.root / "bin" / name
            exe.write_text(MODEL)
            exe.chmod(0o700)
        cfg = {"project": str(self.repo), "allowed_paths": ["app.py"], "forbidden_paths": ["protected.txt"],
               "checks": [[sys.executable, "-B", "-c", CHECK]], "owner": "claude", "reviewer": "codex-session"}
        (self.root / "profile.json").write_text(json.dumps(cfg))
        self.cli(engine, "profile", "save", "legacy", "--file", str(self.root / "profile.json"))
        self.cli(engine, "katakuri-goal", "create", ID, "--profile", "legacy", "--goal", "Complete offline fixture",
                 "--instrument", "HSI", "--milestone", "M2", "--criterion", "Local file matches",
                 "--criterion", "An external fixture observation was received", "--max-reported-tokens", "30")
        self.queue(step(1, write="app.py"), step(2, "waiting"))
        self.cli(engine, "katakuri-goal", "run", ID, code=4)
        legacy = self.task(engine)
        status = self.status(engine, "katakuri-goal")
        assert status["phase"] == "waiting" and [i["state"] for i in status["criteria"]] == ["proposed", "waiting"]
        assert (status["agent_calls"], status["reported_tokens"]) == (2, 20)
        assert legacy["verification"]["passed"] and "goal_mode" not in legacy
        boundary = {k: legacy[k] for k in ("id", "created_at", "baseline", "project", "scope", "execution",
                                          "profile", "project_config", "workspace", "refs")}
        return {"source_package_sha256": package_digest(engine), "boundary": boundary, "status": status,
                "rowid": self.sql("SELECT rowid FROM tasks WHERE id=?", ID)[0][0],
                "verification": legacy["verification"], "revision": legacy["revision"]}

    def negative_paths(self, engine):
        out = {}
        for filename, ident in [("notes.txt", "KB-P2-OUTSIDE"), ("protected.txt", "KB-P2-PROTECTED")]:
            self.cli(engine, "katakuri-goal", "create", ident, "--profile", "legacy",
                     "--goal", "Offline scope check", "--criterion", "Stay within app.py")
            self.queue(step(1, write=filename))
            failure = self.cli(engine, "goal", "run", ident, code=2)
            task = self.task(engine, ident)
            assert filename in failure.stderr and task["status"] == "blocked"
            assert not task.get("review") and not task.get("completed_fingerprint")
            assert (self.repo / filename).read_text() == "baseline"
            out[filename] = {"status": task["status"], "error": failure.stderr.strip(), "source_unchanged": True}
        cfg = json.loads((self.root / "profile.json").read_text())
        cfg["goal_mode"] = {"version": 1, "scope": "General local project", "instructions": ["AGENTS.md"],
                            "input_extensions": [".txt"]}
        path = self.root / "generic.json"
        path.write_text(json.dumps(cfg))
        self.cli(engine, "profile", "save", "generic", "--file", str(path))
        self.cli(engine, "goal", "create", "KB-P2-BYPASS", "--profile", "generic",
                 "--goal", "Offline scope check", "--criterion", "Use the project profile", code=2)
        assert not self.sql("SELECT id FROM tasks WHERE id=?", "KB-P2-BYPASS")
        return out

    def after(self, engine, before):
        a, b = self.status(engine, "goal"), self.status(engine, "katakuri-goal")
        for key in ("id", "phase", "criteria", "instrument", "milestone", "agent_calls", "reported_tokens"):
            assert a[key] == b[key] == before["status"][key]
        before_task = self.task(engine)
        assert before_task["revision"] == before["revision"] and before_task["verification"] == before["verification"]
        for key, value in before["boundary"].items():
            assert before_task[key] == value
        rejected = {}
        for key, argv in {
            "release": ["release-config", ID, "--branch", "fixture", "--path", "app.py"],
            "no_reviewer": ["assign", ID, "--owner", "claude", "--reviewer", "none"],
            "premature_finish": ["finish", ID],
        }.items():
            rejected[key] = self.cli(engine, *argv, code=2).stderr.strip()
        self.queue()  # No model work while the external condition is still open.
        self.cli(engine, "goal", "run", ID, code=4)
        assert self.status(engine, "katakuri-goal")["agent_calls"] == 2
        # Pre-P1 receipts did not identify the check environment. The first
        # resume rechecks the preserved code once before it can reuse that proof.
        refreshed_old_check = not bool(before["verification"].get("check_context"))
        assert len((self.root / "checks").read_text().splitlines()) == 1 + int(refreshed_old_check)
        (self.root / "evidence.bin").write_bytes(b"fixture")
        rejected["unregistered_input_type"] = self.cli(engine, "goal", "supply", ID, "--criterion", "2",
                                                       "--ref", str(self.root / "evidence.bin"), code=2).stderr.strip()
        observation = self.root / "evidence.md"
        observation.write_text(OPERATOR_INPUT)
        digest = hashlib.sha256(observation.read_bytes()).hexdigest()
        self.cli(engine, "goal", "supply", ID, "--criterion", "2", "--ref", str(observation))
        self.queue(step(2, require_input=OPERATOR_INPUT))
        stop = self.cli(engine, "goal", "run", ID, code=2)
        stopped = self.status(engine, "katakuri-goal")
        assert "토큰" in stop.stderr and (stopped["agent_calls"], stopped["reported_tokens"]) == (3, 30)
        self.cli(engine, "katakuri-goal", "budget", ID, "--max-reported-tokens", "40")
        self.queue({"outcome": "pass", "findings": [], "summary": "Both criteria verified.", "next_action": "Finish.",
                    "require_input": OPERATOR_INPUT, "coverage": [
                        {"criterion": 1, "accepted": True, "evidence": "app.py contains done and the check passed."},
                        {"criterion": 2, "accepted": True, "evidence": OPERATOR_INPUT}]})
        self.cli(engine, "katakuri-goal", "run", ID)
        final = self.status(engine, "goal")
        final_legacy = self.status(engine, "katakuri-goal")
        for status in (final, final_legacy):
            assert (status["phase"], status["agent_calls"], status["reported_tokens"]) == ("complete", 4, 40)
            assert (status["instrument"], status["milestone"]) == ("HSI", "M2")
            assert [i["state"] for i in status["criteria"]] == ["accepted", "accepted"]
        task = self.task(engine)
        assert "goal_mode" not in task and task["katakuri_goal"]["items"][1]["inputs"] == [digest]
        for key, value in before["boundary"].items():
            if key == "refs":
                assert task[key][:-1] == value and Path(task[key][-1]).stem == digest
            else:
                assert task[key] == value, key
        assert self.sql("SELECT rowid FROM tasks WHERE id=?", ID) == [(before["rowid"],)]
        assert self.sql("SELECT COUNT(*) FROM events WHERE task_id=? AND kind='created'", ID) == [(1,)]
        assert task["verification"]["passed"] and task["verification"]["fingerprint"] == task["completed_fingerprint"]
        assert task["review"]["fingerprint"] == task["completed_fingerprint"] and task["review"]["verdict"] == "pass"
        assert task["review"]["independent_run"] == task["native_review_receipt"]["directory"]
        for family in ("goal", "katakuri-goal"):
            self.cli(engine, family, "run", ID, code=2)
        assert self.task(engine)["revision"] == task["revision"]
        assert (self.root / "writes").read_text().splitlines() == ["app.py"]
        check_count = len((self.root / "checks").read_text().splitlines())
        assert check_count == 2 + int(refreshed_old_check)
        launch_records = []
        task_dir = self.home / "tasks" / ID
        required_scope = ["No broker access", "paper/live orders", "credentials", "trading permissions",
                          "operational DB/service changes", "deployment", "automatic milestone promotion"]
        for folder in sorted((task_dir / "runs").iterdir()):
            argv = json.loads((folder / "process.json").read_text())["command"]
            prompt = (folder / "prompt.txt").read_text()
            assert all(term in prompt for term in required_scope)
            assert all(doc in prompt for doc in DOCS)
            if Path(argv[0]).name == "claude":
                assert all(opt in argv for opt in ("--safe-mode", "--restricted", "--no-chrome", "--strict-mcp-config"))
                assert argv[argv.index("--tools") + 1] == argv[argv.index("--allowedTools") + 1] == "Read,Glob,Grep,Edit,Write"
                assert argv[argv.index("--permission-mode") + 1] == "dontAsk"
                assert json.loads(argv[argv.index("--mcp-config")+1]) == {"mcpServers": {}}
                role = "owner"
            else:
                assert Path(argv[0]).name == "codex" and argv[argv.index("--sandbox") + 1] == "read-only"
                assert argv[argv.index("-a") + 1] == "never" and "--ignore-user-config" in argv and "--ephemeral" in argv
                assert {"plugins", "hooks", "apps", "multi_agent", "multi_agent_v2"} <= {
                    argv[i+1] for i,v in enumerate(argv) if v == "--disable"}
                assert {"sandbox_workspace_write.network_access=false", 'web_search="disabled"', "mcp_servers={}"} <= set(argv)
                role = "reviewer"
            launch_records.append({"role": role, "scope_in_prompt": True, "bound_input_in_prompt": OPERATOR_INPUT in prompt})
        assert sum(r["role"] == "reviewer" for r in launch_records) == 1
        assert sum(r["bound_input_in_prompt"] for r in launch_records) == 2
        assert Path(task["project"]) != self.repo and (self.repo / "app.py").read_text() == "baseline"
        return {"id": ID, "model_mode": "deterministic native CLI doubles; controller, Git, SQLite and checks are real",
                "old_package_sha256": before["source_package_sha256"], "new_package_sha256": package_digest(engine),
                "old_phase": a["phase"], "old_calls": a["agent_calls"], "old_reported_tokens": a["reported_tokens"],
                "old_receipt_preserved_on_read": True, "same_row_id": before["rowid"], "created_events": 1,
                "old_code_fingerprint": before["verification"]["fingerprint"],
                "old_refs_fingerprint": before["verification"]["refs_fingerprint"],
                "new_refs_fingerprint": task["verification"]["refs_fingerprint"],
                "input_sha256": digest, "budget_stop_calls": stopped["agent_calls"],
                "budget_stop_reported_tokens": stopped["reported_tokens"],
                "status": final["status"], "phase": final["phase"], "instrument": final["instrument"],
                "milestone": final["milestone"], "calls": final["agent_calls"], "reported_tokens": final["reported_tokens"],
                "writes": 1, "checks": check_count, "reviewer": task["review"]["reviewer"],
                "audit": task["katakuri_goal"]["audit"]["rows"],
                "verified_reviewed_completion_fingerprint": task["completed_fingerprint"],
                "launches": launch_records, "denied": rejected, "scope_controls": self.negative_paths(engine),
                "new_generic_registration_on_katakuri_rejected": True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--old-engine", type=Path, help="pre-upgrade package parent; defaults to tracked commit " + HISTORICAL)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="aw-p2-legacy-") as temp:
        root = Path(temp)
        old = args.old_engine.resolve() if args.old_engine else archived_engine(root / "old")
        pilot = Pilot(root)
        before = pilot.before(old)
        report = pilot.after(ROOT, before)
        report["old_source"] = "supplied pre-upgrade Python package" if args.old_engine else HISTORICAL
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf8")
    print(f'{ID}: {report["phase"]}, {report["writes"]} file write, {report["checks"]} successful checks, 1 independent review.')


if __name__ == "__main__":
    main()
