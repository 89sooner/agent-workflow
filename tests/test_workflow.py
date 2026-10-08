import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from agent_workflow.core import (Store, WorkflowError, complete, create_task,
                                 git, snapshot)
from agent_workflow.runner import (execute, process, record_review, run_checks,
                                   validate_result)
from agent_workflow.cli import argv_from, export_handoff


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="workflow-test-")
        self.root = Path(self.temp.name)
        self.project = self.root / "project 한글 with spaces"
        self.project.mkdir()
        git(self.project, "init", "-q")
        (self.project / ".gitignore").write_text("__pycache__/\n")
        (self.project / "app.py").write_text("VALUE = 1\n")
        git(self.project, "add", ".")
        git(self.project, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-qm", "initial")
        self.store = Store(self.root / "state")

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def task(self, task_id="WP-001", reviewer="none", checks=None):
        return create_task(self.store, task_id, self.project, "VALUE 검증", ["VALUE는 1"], [],
                           checks if checks is not None else [[sys.executable, "-c", "import app; assert app.VALUE == 1"]],
                           "claude", reviewer, "local only")

    def checked(self, task):
        with self.store.lock(task["project"]) as fd:
            self.assertTrue(run_checks(self.store, task, 5, fd))
        return task

    @staticmethod
    def result(outcome="pass", findings=None):
        return {"outcome": outcome, "summary": "actual review", "next_action": "finish",
                "findings": findings or []}

    def test_check_finish_handoff_and_restart(self):
        t = self.checked(self.task())
        complete(self.store, t)
        directory = Path(export_handoff(self.store, t))
        data = json.loads((directory / "state.json").read_text())
        self.assertEqual(data["task"]["status"], "completed")
        self.assertIn("VALUE 검증", (directory / "resume.txt").read_text())
        self.store.close()
        self.store = Store(self.root / "state")
        self.assertEqual(self.store.get(t["id"])["status"], "completed")

    def test_missing_or_failed_check_cannot_finish(self):
        t = self.task(checks=[])
        with self.assertRaises(WorkflowError):
            complete(self.store, t)
        t["checks"] = [[sys.executable, "-c", "raise SystemExit(7)"]]
        with self.store.lock(t["project"]) as fd:
            self.assertFalse(run_checks(self.store, t, 5, fd))
        with self.assertRaises(WorkflowError):
            complete(self.store, t)

    def test_source_change_invalidates_check(self):
        t = self.checked(self.task())
        (self.project / "app.py").write_text("VALUE = 2\n")
        with self.assertRaises(WorkflowError):
            complete(self.store, t)

    def test_untracked_file_content_invalidates_check(self):
        p = self.project / "new.txt"
        p.write_text("first")
        t = self.checked(self.task())
        p.write_text("other")
        with self.assertRaises(WorkflowError):
            complete(self.store, t)

    def test_head_and_index_participate(self):
        first = snapshot(self.project)
        (self.project / "app.py").write_text("VALUE = 2\n")
        second = snapshot(self.project)
        git(self.project, "add", "app.py")
        third = snapshot(self.project)
        self.assertNotEqual(first["fingerprint"], second["fingerprint"])
        self.assertNotEqual(second["fingerprint"], third["fingerprint"])

    def test_check_that_changes_code_does_not_pass(self):
        t = self.task(checks=[[sys.executable, "-c", "from pathlib import Path; Path('app.py').write_text('VALUE=2')"]])
        with self.store.lock(t["project"]) as fd:
            self.assertFalse(run_checks(self.store, t, 5, fd))
        self.assertTrue(t["verification"]["code_changed_during_checks"])

    def test_review_needed_for_finish_and_must_be_independent(self):
        t = self.checked(self.task(reviewer="codex"))
        fp = snapshot(self.project)["fingerprint"]
        with self.assertRaises(WorkflowError):
            complete(self.store, t)
        with self.assertRaises(WorkflowError):
            record_review(self.store, t, self.result(), fp, "claude")
        record_review(self.store, t, self.result(), fp, "codex")
        self.assertEqual(complete(self.store, t)["status"], "completed")

    def test_stale_review_rejected(self):
        t = self.checked(self.task(reviewer="codex"))
        fp = snapshot(self.project)["fingerprint"]
        (self.project / "new.txt").write_text("new code")
        with self.assertRaises(WorkflowError):
            record_review(self.store, t, self.result(), fp, "codex")

    def test_result_schema_rejects_false_pass_and_missing_evidence(self):
        with self.assertRaises(WorkflowError):
            validate_result(self.result(findings=[{"blocking": True, "detail": "failure", "evidence": "app.py:1"}]), "review")
        with self.assertRaises(WorkflowError):
            validate_result(self.result(findings=[{"blocking": False, "detail": "hint", "evidence": ""}]), "review")
        with self.assertRaises(WorkflowError):
            validate_result(self.result("ready"), "review")

    def test_two_stores_cannot_lock_same_project(self):
        second = Store(self.root / "state")
        try:
            with self.store.lock(self.project):
                with self.assertRaises(WorkflowError):
                    with second.lock(self.project):
                        pass
        finally:
            second.close()

    def test_other_worktree_can_lock_independently(self):
        other = self.root / "other"
        git(self.project, "worktree", "add", "-b", "other", str(other))
        with self.store.lock(self.project):
            with self.store.lock(other):
                pass

    def test_different_state_home_still_shares_project_lock(self):
        second = Store(self.root / "second-state")
        try:
            with self.store.lock(self.project):
                with self.assertRaises(WorkflowError):
                    with second.lock(self.project):
                        pass
        finally:
            second.close()

    def test_interrupted_recheck_invalidates_previous_pass(self):
        t = self.checked(self.task())
        with self.store.lock(self.project) as fd:
            with patch("agent_workflow.runner.process", side_effect=KeyboardInterrupt):
                with self.assertRaises(WorkflowError):
                    run_checks(self.store, t, 5, fd)
        t = self.store.get(t["id"])
        self.assertIsNone(t["verification"])
        with self.assertRaises(WorkflowError):
            complete(self.store, t)

    def test_handoff_marks_stale_and_has_interactive_resume(self):
        t = self.checked(self.task())
        complete(self.store, t)
        (self.project / "app.py").write_text("VALUE=2")
        d = Path(export_handoff(self.store, t))
        state = json.loads((d / "state.json").read_text())
        self.assertFalse(state["completion_is_current"])
        resume = (d / "resume.txt").read_text()
        self.assertIn(str(self.store.home), resume)
        self.assertIn("재구현하지", resume)
        self.assertNotIn("Return JSON", resume)

    def test_submodule_untracked_content_changes_fingerprint(self):
        other = self.root / "sub-source"
        subprocess.run(["git", "clone", "-q", str(self.project), str(other)], check=True)
        git(self.project, "-c", "protocol.file.allow=always", "submodule", "add", str(other), "child")
        p = self.project / "child" / "untracked.py"
        p.write_text("one")
        first = snapshot(self.project)
        p.write_text("two")
        self.assertNotEqual(first["fingerprint"], snapshot(self.project)["fingerprint"])

    def test_metadata_failure_reaps_child(self):
        import agent_workflow.runner as runner
        actual = runner.subprocess.Popen
        children = []
        def start(*args, **kwargs):
            proc = actual(*args, **kwargs)
            children.append(proc)
            return proc
        with self.store.lock(self.project) as fd:
            with patch.object(runner.subprocess, "Popen", side_effect=start), patch.object(runner, "atomic_write", side_effect=OSError("disk full")):
                with self.assertRaises(OSError):
                    process([sys.executable, "-c", "import time; time.sleep(30)"], self.project,
                            self.root / "write-error", 5, fd)
        self.assertIsNotNone(children[0].poll())

    def test_id_traversal_duplicate_and_empty_goal_rejected(self):
        with self.assertRaises(WorkflowError):
            self.task("../../escape")
        self.task()
        with self.assertRaises(WorkflowError):
            self.task()
        with self.assertRaises(WorkflowError):
            create_task(self.store, "empty", self.project, " ", ["ok"], [], [], "claude", "none", "")

    def test_timeout_stops_process(self):
        with self.store.lock(self.project) as fd:
            result = process([sys.executable, "-c", "import time; time.sleep(30)"],
                             self.project, self.root / "timeout", 0.1, fd)
        self.assertTrue(result["timed_out"])
        self.assertNotEqual(result["exit_code"], 0)

    def test_stdin_and_arguments_are_literal(self):
        with self.store.lock(self.project) as fd:
            result = process([sys.executable, "-c", "import sys; print(sys.argv[1]); print(sys.stdin.read())", "$(touch oops)"],
                             self.project, self.root / "literal", 5, fd, "한글 `shell` $HOME")
        self.assertFalse((self.project / "oops").exists())
        self.assertIn("한글 `shell` $HOME", Path(result["stdout"]).read_text())
        self.assertIn("$(touch oops)", Path(result["stdout"]).read_text())

    def test_shell_pipeline_not_silently_interpreted(self):
        with self.assertRaises(WorkflowError):
            argv_from("echo ok && rm -rf x")
        self.assertEqual(argv_from('python3 "hello world.py"'), ["python3", "hello world.py"])

    def fake_command(self, agent, role, directory, model=None):
        answer = self.result("pass" if role == "review" else "ready")
        # An actual child process exercises argv/stdin/logging and result parsing.
        if agent == "codex":
            code = "from pathlib import Path; import sys; sys.stdin.read(); Path(sys.argv[1]).write_text(sys.argv[2])"
            return [sys.executable, "-c", code, str(directory / "answer.json"), json.dumps(answer)]
        return [sys.executable, "-c", "import sys; sys.stdin.read(); print(sys.argv[1])",
                json.dumps({"subtype": "success", "structured_output": answer})]

    def test_full_pipeline_through_cli_contract_subprocesses(self):
        t = self.task(reviewer="codex")
        with patch("agent_workflow.runner.command_for", side_effect=self.fake_command):
            result = execute(self.store, t["id"], timeout=5, repairs=0)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["review"]["reviewer"], "codex")

    def test_interrupted_review_resumes_without_reimplementing(self):
        t = self.checked(self.task(reviewer="codex"))
        roles = []
        def cmd(agent, role, directory, model=None):
            roles.append(role)
            return self.fake_command(agent, role, directory, model)
        with patch("agent_workflow.runner.command_for", side_effect=cmd):
            execute(self.store, t["id"], timeout=5, repairs=0)
        self.assertEqual(roles, ["review"])

    def test_valid_check_and_review_resume_directly_to_finish(self):
        t = self.checked(self.task(reviewer="codex"))
        record_review(self.store, t, self.result(), snapshot(self.project)["fingerprint"], "codex")
        with patch("agent_workflow.runner.invoke", side_effect=AssertionError("unnecessary repeat")):
            result = execute(self.store, t["id"], timeout=5, repairs=0)
        self.assertEqual(result["status"], "completed")

    def test_invalid_agent_response_is_blocked(self):
        t = self.task()
        with patch("agent_workflow.runner.command_for", return_value=[sys.executable, "-c", "print('{}')"]):
            with self.assertRaises(WorkflowError):
                execute(self.store, t["id"], timeout=5, repairs=0)
        self.assertEqual(self.store.get(t["id"])["status"], "blocked")

    def test_repair_budget_terminates(self):
        t = self.task(reviewer="codex")
        roles = []
        def answer(store, task, agent, role, *args, **kwargs):
            roles.append(role)
            return self.result("changes_requested" if role == "review" else "ready",
                               [{"blocking": True, "detail": "bug", "evidence": "app.py:1"}] if role == "review" else [])
        with patch("agent_workflow.runner.invoke", side_effect=answer):
            with self.assertRaises(WorkflowError):
                execute(self.store, t["id"], timeout=5, repairs=1)
        self.assertEqual(roles, ["implement", "review", "implement", "review"])
        self.assertEqual(self.store.get(t["id"])["status"], "blocked")


if __name__ == "__main__":
    unittest.main()
