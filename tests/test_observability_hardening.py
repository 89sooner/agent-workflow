"""Cross-entry-point trace regressions with actual CLI controller and Git effects."""
import json
from pathlib import Path
import sys
import threading
import time
import unittest
from unittest.mock import patch

import test_release as fixtures
from agent_workflow.cli import dispatch, parser
from agent_workflow.core import Store, WorkflowError, set_hold
from agent_workflow.observability import view
from agent_workflow.profiles import save
from agent_workflow.runner import execute
from agent_workflow.usage import managed_process
from agent_workflow.workspaces import create_from_profile


class ObservationHardeningTests(unittest.TestCase):
    setUp = fixtures.ReleaseTests.setUp
    tearDown = fixtures.ReleaseTests.tearDown
    configure = fixtures.ReleaseTests.configure
    finish = fixtures.ReleaseTests.finish

    def cli(self, *args):
        return dispatch(parser().parse_args(["--home", str(self.store.home), *args]))

    def command(self, agent, role, directory, model=None):
        answer = {"outcome": "pass" if role == "review" else "ready", "summary": "actual fixture",
                  "next_action": "finish" if role == "review" else "check", "findings": []}
        if role == "review":
            return [sys.executable, "-c", "from pathlib import Path; import sys; sys.stdin.read(); Path(sys.argv[1]).write_text(sys.argv[2])",
                    str(directory / "answer.json"), json.dumps(answer)]
        return [sys.executable, "-c", "from pathlib import Path; import sys; sys.stdin.read(); Path('app.txt').write_text('new\\n'); print(sys.argv[1])",
                json.dumps({"subtype": "success", "structured_output": answer})]

    def assert_complete_pipeline(self, command):
        self.cli("assign", self.task["id"], "--owner", "claude", "--reviewer", "codex")
        self.configure(deploy=True)
        with patch("agent_workflow.runner.command_for", side_effect=self.command):
            result = self.cli(command, self.task["id"], "--timeout", "10", "--repairs", "0")
        self.assertEqual(result["task"]["release"]["stage"], "deployed")
        observed = view(self.store, self.task["id"])
        self.assertEqual(len(observed["runs"]), 1)
        self.assertEqual(observed["runs"][0]["action"], command)
        self.assertEqual(observed["runs"][0]["status"], "succeeded")
        self.assertEqual([span["role"] for span in observed["spans"]],
                         ["implement", "check", "review", "integration_check", "deploy", "health"])
        self.assertTrue(all(span["run_id"] == observed["selected_run_id"] for span in observed["spans"]))
        kinds = [event["kind"] for event in observed["events"]]
        self.assertIn("closure_finished", kinds)
        self.assertLess(kinds.index("completed"), kinds.index("release_deployed"))
        self.assertLess(kinds.index("release_deployed"), kinds.index("closure_finished"))
        self.assertEqual(kinds[-1], "run_finished")

    def test_cli_pipeline_keeps_implementation_release_and_closure_in_one_trace(self):
        self.assert_complete_pipeline("pipeline")

    def test_cli_run_alias_keeps_complete_pipeline_in_one_trace(self):
        self.assert_complete_pipeline("run")

    def test_cli_release_keeps_its_closure_in_release_trace(self):
        self.finish()
        self.configure(deploy=True)
        self.cli("release", self.task["id"], "--timeout", "10")
        observed = view(self.store, self.task["id"])
        self.assertEqual(observed["runs"][0]["action"], "release")
        self.assertEqual([span["role"] for span in observed["spans"]], ["integration_check", "deploy", "health"])
        self.assertIn("closure_finished", [event["kind"] for event in observed["events"]])

    def test_historical_actor_identity_survives_reassignment_and_later_hold(self):
        task = self.store.get(self.task["id"])
        managed_process(self.store, task, [sys.executable, "-c", "pass"], self.project,
                        self.store.directory(task["id"]) / "runs/history", 5, None,
                        agent="claude", role="implement")
        run_id = view(self.store, task["id"])["selected_run_id"]
        self.cli("assign", task["id"], "--owner", "codex", "--reviewer", "claude")
        set_hold(self.store, task["id"], "current hold, after old run")
        observed = view(self.store, task["id"], run_id)
        actors = {actor["id"]: actor for actor in observed["actors"]}
        self.assertIn("claude:implement", actors)
        self.assertEqual(actors["claude:implement"]["status"], "succeeded")
        self.assertEqual(observed["spans"][0]["actor"], "claude:implement")

    def test_setup_role_is_explicit_when_task_id_matches_checks_directory(self):
        save(self.store, "setup-role", {"project": str(self.project), "workspace": {"isolate": False},
             "setup": [[sys.executable, "-c", "pass"]]})
        task = create_from_profile(self.store, "checks", "setup-role", "prepare fixture", ["prepared"])
        self.cli("prepare", task["id"], "--timeout", "5")
        observed = view(self.store, task["id"])
        self.assertEqual(self.store.get(task["id"])["workspace"]["setup_state"], "passed")
        self.assertEqual([span["role"] for span in observed["spans"]], ["setup"])
        self.assertEqual([span["actor"] for span in observed["spans"]], ["controller"])

    def test_default_view_keeps_live_run_visible_after_many_rejected_duplicate_attempts(self):
        started, release = self.root / "started", self.root / "continue"
        errors = []
        answer = {"outcome": "ready", "summary": "actual fixture", "next_action": "check", "findings": []}
        code = ("import sys,time; from pathlib import Path; sys.stdin.read(); Path('app.txt').write_text('new\\n'); "
                "Path(sys.argv[1]).touch(); deadline=time.monotonic()+12\n"
                "while not Path(sys.argv[2]).exists() and time.monotonic()<deadline: time.sleep(.01)\n"
                "print(sys.argv[3])")
        command = [sys.executable, "-c", code, str(started), str(release),
                   json.dumps({"subtype": "success", "structured_output": answer})]

        def worker():
            store = Store(self.store.home)
            try:
                execute(store, self.task["id"], timeout=15, repairs=0)
            except BaseException as exc:
                errors.append(exc)
            finally:
                store.close()

        with patch("agent_workflow.runner.command_for", return_value=command):
            thread = threading.Thread(target=worker)
            thread.start()
            try:
                deadline = time.monotonic() + 5
                while not started.exists() and time.monotonic() < deadline:
                    time.sleep(.01)
                self.assertTrue(started.exists())
                first_run = self.store.db.execute("SELECT id FROM observation_runs WHERE status='running' ORDER BY rowid DESC LIMIT 1").fetchone()[0]
                for _ in range(32):
                    with self.assertRaisesRegex(WorkflowError, "다른 작업"):
                        execute(self.store, self.task["id"], timeout=5, repairs=0)
                observed = view(self.store, self.task["id"])
                self.assertEqual(observed["selected_run_id"], first_run)
                self.assertEqual(len(observed["spans"]), 1)
                self.assertEqual(observed["spans"][0]["status"], "running")
                self.assertTrue(observed["runs_truncated"])
                self.assertIn(first_run, [run["id"] for run in observed["runs"]])
            finally:
                release.touch()
                thread.join(timeout=18)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
