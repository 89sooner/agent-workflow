"""Interrupt actual controller processes on either side of a durable SQLite receipt."""
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import unittest

import test_workflow as fixtures
from agent_workflow import katakuri_goal, profiles
from agent_workflow.core import WorkflowError, git, reference_fingerprint, snapshot
from agent_workflow.local_recovery import local_diagnosis


DRIVER = """
import os, sys
from contextlib import closing
from agent_workflow import runner
from agent_workflow.core import Store
fault, home = sys.argv[1:]
process, save = runner.process, Store.save

def cut_before_response(command, *args, **kwargs):
    outcome = process(command, *args, **kwargs)
    if fault == 'owner' and command[0].endswith('/claude'):
        os._exit(73)
    return outcome

def cut_after_receipt(self, task, kind, *args, **kwargs):
    save(self, task, kind, *args, **kwargs)
    if fault == 'checks' and kind == 'checks_finished':
        os._exit(74)

runner.process = cut_before_response
Store.save = cut_after_receipt
with closing(Store(home)) as store:
    runner.execute(store, 'P1', timeout=5, repairs=0)
"""

MODEL = """#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
args, prompt = sys.argv, sys.stdin.read()
log = Path(os.environ['P1_LOG'])
owner = Path(args[0]).name == 'claude'
if owner:
    readonly = 'Write' not in args[args.index('--allowedTools') + 1]
    role = 'inspect' if readonly else 'owner'
else:
    role = 'review'
with log.open('a') as out: print(role, file=out)
if owner and not readonly:
    if 'inspect' in log.read_text().splitlines() and os.environ['P1_GOAL_COUNT'] == '2':
        with Path('app.py').open('a') as app: print('SECOND = True', file=app)
        with log.open('a') as out: print('write-second', file=out)
    else:
        Path('app.py').write_text("VALUE = 2\\n")
        with log.open('a') as out: print('write', file=out)
summary = dict(summary='worktree inspected', next_action='review')
if owner:
    schema = json.loads(args[args.index('--json-schema') + 1])
    if 'criterion' in schema['properties']:
        # The inspection identifies one recovered step; only further pending work needs a writer.
        criterion = 2 if 'inspect' in log.read_text().splitlines()[:-1] else 1
        answer = dict(**summary, outcome='ready', criterion=criterion)
    else:
        answer = dict(**summary, outcome='ready', findings=[])
    print(json.dumps(dict(structured_output=answer)))
else:
    answer = dict(**summary, outcome='pass', findings=[])
    schema = json.loads(Path(args[args.index('--output-schema') + 1]).read_text())
    if 'coverage' in schema['properties']:
        n = int(os.environ['P1_GOAL_COUNT'])
        answer['coverage'] = [dict(criterion=i, accepted=True, evidence='app.py and registered check') for i in range(1,n+1)]
    Path(args[args.index('--output-last-message')+1]).write_text(json.dumps(answer))
"""


class LocalResumeTests(unittest.TestCase):
    setUp = fixtures.WorkflowTests.setUp
    tearDown = fixtures.WorkflowTests.tearDown
    task = fixtures.WorkflowTests.task

    def test_show_old_checkpoint_without_creating_or_migrating_ledger(self):
        from agent_workflow.cli import dispatch, parser
        home = self.root / "old-ledger"
        args = parser().parse_args(["--home", str(home), "show", "P1"])
        with self.assertRaises(WorkflowError):
            dispatch(args)
        self.assertFalse(home.exists())
        home.mkdir()
        original = self.task("P1", reviewer="codex")
        original["verification"] = {"passed": True, "fingerprint": snapshot(self.project)["fingerprint"]}
        database = home / "state.sqlite3"
        with sqlite3.connect(database) as db:
            db.execute("CREATE TABLE tasks (id TEXT PRIMARY KEY, data TEXT NOT NULL)")
            db.execute("INSERT INTO tasks VALUES (?,?)", ("P1", json.dumps(original)))
        before = database.read_bytes()
        self.assertEqual(dispatch(args)["recovery"]["action"], "recheck")
        self.assertEqual(database.read_bytes(), before)
        self.assertEqual(list(home.iterdir()), [database])

    def configure(self, *, goal_count=0, environment=False):
        self.log = self.root / "effects"
        self.bin = self.root / "bin"
        self.bin.mkdir()
        for name in ("codex", "claude"):
            script = self.bin / name
            script.write_text(MODEL)
            script.chmod(0o755)
        self.env = {**os.environ, "PATH": str(self.bin) + os.pathsep + os.environ["PATH"],
                    "P1_LOG": str(self.log), "P1_GOAL_COUNT": str(goal_count)}
        check = [sys.executable, "-c", "import app,sys; from pathlib import Path; "
                 "f=Path(sys.argv[1]).open('a'); print('check',file=f); f.close(); assert app.VALUE == 2", str(self.log)]
        if not goal_count and not environment:
            return self.task("P1", reviewer="codex", checks=[check])
        for name in ("AGENTS.md", katakuri_goal.DIRECTIVE, katakuri_goal.HANDOFF):
            path = self.project / name
            path.parent.mkdir(exist_ok=True)
            path.write_text("Offline development")
        (self.project / ".gitignore").write_text("__pycache__/\nignored-input\n")
        git(self.project, "add", ".")
        git(self.project, "-c", "user.name=Fixture", "-c", "user.email=f@example.invalid", "commit", "-qm", "goal")
        cfg = {"project": str(self.project), "owner": "claude", "reviewer": "codex",
               "allowed_paths": ["app.py"], "checks": [check]}
        if environment:
            cfg["check_inputs"] = ["ignored-input"]
        profiles.save(self.store, "p1", cfg)
        if goal_count:
            return katakuri_goal.create(self.store, "P1", "p1", "local development",
                                        [f"criterion {i}" for i in range(goal_count)])
        from agent_workflow.workspaces import create_from_profile
        return create_from_profile(self.store, "P1", "p1", "local development", ["value is two"])

    def execute_child(self, fault, expected_exit):
        result = subprocess.run([sys.executable, "-B", "-c", DRIVER, fault, str(self.store.home)],
                                env=self.env, cwd=Path(__file__).resolve().parents[1],
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, expected_exit, result.stdout + result.stderr)

    def cli(self, *args):
        command = [sys.executable, "-B", "-m", "agent_workflow", "--home", str(self.store.home), *args]
        output = subprocess.check_output(command, env=self.env, cwd=Path(__file__).resolve().parents[1],
                                         timeout=30, text=True)
        return json.loads(output)

    def reproduce(self, fault, *, goal_count=0):
        task = self.configure(goal_count=goal_count)
        self.execute_child(fault, 73 if fault == "owner" else 74)
        before = self.store.get("P1")
        diagnosis = self.cli("show", "P1")["recovery"]
        self.assertEqual(diagnosis, local_diagnosis(before, snapshot(task["project"])))
        self.assertEqual(self.cli("show", "P1")["recovery"], diagnosis)
        if goal_count:
            self.assertEqual(self.cli("katakuri-goal", "status", "P1")["recovery"], diagnosis)
        self.assertEqual(diagnosis["action"], "verify_partial" if fault == "owner" else "review")
        self.assertTrue(diagnosis["reason"] and diagnosis["confirmed_effects"] and diagnosis["next_action"])
        counts_before = self.log.read_text().splitlines()
        self.assertEqual(counts_before, ["owner", "write"] + (["check"] if fault == "checks" else []))
        self.execute_child("none", 0)
        after = self.store.get("P1")
        effects = self.log.read_text().splitlines()
        expected = ["owner", "write", "check", "review"]
        self.assertEqual(effects, expected)
        self.assertEqual(after["status"], "completed")
        self.assertEqual(after["usage"]["agent_calls"], 2)
        if fault == "owner":
            self.assertEqual(after["usage"]["uncertain_seconds"], before["usage"]["pending"]["reserved_seconds"])
        self.assertIsNone(local_diagnosis(after, snapshot(task["project"])))
        repeated = self.cli("run", "P1")["task"]
        self.assertEqual(repeated["status"], "completed")
        self.assertEqual(repeated["usage"]["agent_calls"], 2)
        self.assertEqual(self.log.read_text().splitlines(), effects)
        self.replay_evidence = {"diagnosis": diagnosis, "before": counts_before, "after": effects,
                                "agent_calls": after["usage"]["agent_calls"], "status": after["status"],
                                "uncertain_seconds": after["usage"]["uncertain_seconds"],
                                "after_repeated_run": self.log.read_text().splitlines()}

    def test_owner_crash_hands_preserved_change_to_checks(self):
        self.reproduce("owner")

    def test_checks_crash_resumes_at_review(self):
        self.reproduce("checks")

    def test_goal_owner_crash_hands_single_pending_condition_to_checks_and_review(self):
        self.reproduce("owner", goal_count=1)

    def test_goal_recovers_unidentified_criterion_with_one_readonly_inspection(self):
        task = self.configure(goal_count=2)
        self.execute_child("owner", 73)
        diagnosis = local_diagnosis(self.store.get("P1"), snapshot(task["project"]))
        self.assertIn("읽기 전용", diagnosis["next_action"])
        self.execute_child("none", 0)
        self.assertEqual(self.log.read_text().splitlines(), ["owner", "write", "inspect", "owner", "write-second", "check", "review"])
        self.assertEqual(self.store.get("P1")["status"], "completed")

    def test_changed_ignored_input_rechecks_without_owner(self):
        task = self.configure(environment=True)
        self.execute_child("checks", 74)
        (Path(task["project"]) / "ignored-input").write_text("new fixture")
        diagnosis = local_diagnosis(self.store.get("P1"), snapshot(task["project"]))
        self.assertEqual(diagnosis["action"], "recheck")
        self.assertIn("환경", diagnosis["reason"])
        self.execute_child("none", 0)
        self.assertEqual(self.log.read_text().splitlines(), ["owner", "write", "check", "check", "review"])

    def test_legacy_receipt_requires_check_once_without_owner(self):
        task = self.configure()
        self.execute_child("checks", 74)
        task = self.store.get("P1")
        del task["verification"]["check_context"]
        task.pop("local_step")
        self.store.save(task, "legacy_fixture")
        self.assertEqual(local_diagnosis(task, snapshot(task["project"]))["action"], "recheck")
        self.execute_child("none", 0)
        self.assertEqual(self.log.read_text().splitlines(), ["owner", "write", "check", "check", "review"])

    def test_legacy_parked_goal_stays_asleep_when_code_and_references_match(self):
        task = self.configure(goal_count=1)
        key = snapshot(task["project"])["fingerprint"] + ":" + reference_fingerprint(task)
        task["katakuri_goal"]["items"][0].update(state="waiting", retry_after_change=key)
        task["katakuri_goal"]["phase"] = "waiting"
        task["status"] = "blocked"
        self.store.save(task, "legacy_wait_fixture")
        self.execute_child("none", 1)
        self.assertFalse(self.log.exists())
        self.assertEqual(self.store.get("P1")["katakuri_goal"]["phase"], "waiting")

    def test_existing_user_edit_is_not_attributed_to_interrupted_call(self):
        (self.project / "notes.txt").write_text("user draft")
        task = self.configure()
        self.execute_child("owner", 73)
        diagnosis = local_diagnosis(self.store.get("P1"), snapshot(task["project"]))
        paths = " ".join(diagnosis["confirmed_effects"])
        self.assertIn("app.py", paths)
        self.assertNotIn("notes.txt", paths)
        self.execute_child("none", 0)
        self.assertEqual((self.project / "notes.txt").read_text(), "user draft")

    def test_unreviewed_partial_change_requires_inspection_before_another_writer(self):
        task = self.configure()
        task["reviewer"] = "none"
        self.store.save(task, "assigned")
        self.execute_child("owner", 73)
        self.assertEqual(local_diagnosis(self.store.get("P1"), snapshot(task["project"]))["action"], "assign_reviewer")
        self.execute_child("none", 1)
        self.assertEqual(self.log.read_text().splitlines(), ["owner", "write"])

    def test_changed_worktree_rechecks_and_does_not_accept_a_failed_partial(self):
        task = self.configure()
        self.execute_child("checks", 74)
        (self.project / "app.py").write_text("VALUE = 3")
        self.assertEqual(local_diagnosis(self.store.get("P1"), snapshot(task["project"]))["action"], "recheck")
        self.execute_child("none", 1)
        self.assertFalse(self.store.get("P1")["verification"]["passed"])
        self.assertEqual(self.log.read_text().splitlines(), ["owner", "write", "check", "check"])
        self.execute_child("none", 0)
        self.assertEqual(self.log.read_text().splitlines(), ["owner", "write", "check", "check", "owner", "write", "check", "review"])
