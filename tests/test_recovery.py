import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import test_workflow as fixtures
from agent_workflow.core import WorkflowError, snapshot
from agent_workflow.runner import execute
from agent_workflow.recovery import classify


class RecoveryTests(unittest.TestCase):
    setUp = fixtures.WorkflowTests.setUp
    tearDown = fixtures.WorkflowTests.tearDown
    task = fixtures.WorkflowTests.task
    checked = fixtures.WorkflowTests.checked
    result = staticmethod(fixtures.WorkflowTests.result)
    fake_command = fixtures.WorkflowTests.fake_command

    def test_timeout_partial_code_checked_then_independent_review(self):
        task = self.task(reviewer="codex", checks=[[sys.executable, "-c", "import app; assert app.VALUE == 2"]])
        roles = []
        def command(agent, role, directory, model=None):
            roles.append(role)
            if role == "implement":
                return [sys.executable, "-c", "from pathlib import Path; import time; Path('app.py').write_text('VALUE = 2\\n'); time.sleep(5)"]
            return self.fake_command(agent, role, directory, model)
        with patch("agent_workflow.runner.command_for", side_effect=command):
            result = execute(self.store, task["id"], timeout=.2, repairs=0)
        self.assertEqual(roles, ["implement", "review"])
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["implementation"]["source"], "controller_recovery")
        self.assertEqual(result["review"]["fingerprint"], snapshot(self.project)["fingerprint"])
        self.assertEqual(result["recoveries"][0]["action"], "verify_partial_implementation")

    def test_partial_result_never_bypasses_failed_check(self):
        task = self.task(reviewer="codex")
        def command(*args, **kwargs):
            return [sys.executable, "-c", "from pathlib import Path; import time; Path('app.py').write_text('VALUE = 999\\n'); time.sleep(5)"]
        with patch("agent_workflow.runner.command_for", side_effect=command) as calls:
            with self.assertRaises(WorkflowError):
                execute(self.store, task["id"], timeout=.2, repairs=0)
        self.assertEqual(calls.call_count, 1)
        self.assertFalse(self.store.get(task["id"])["verification"]["passed"])
        self.assertIsNone(self.store.get(task["id"])["review"])

    def test_permission_quota_and_invalid_output_are_not_retried(self):
        for index, stderr in enumerate(["Permission denied", "insufficient_quota", "Unauthorized", "Your workspace is out of credits"]):
            task = self.task("FAIL-" + str(index), reviewer="codex")
            cmd = [sys.executable, "-c", "import sys; print(sys.argv[1], file=sys.stderr); sys.exit(2)", stderr]
            with patch("agent_workflow.runner.command_for", return_value=cmd) as calls:
                with self.assertRaises(WorkflowError):
                    execute(self.store, task["id"], timeout=5, repairs=2, recoveries=3)
            self.assertEqual(calls.call_count, 1)
            self.assertNotIn("recoveries", self.store.get(task["id"]))

    def test_transient_failure_is_bounded_and_preserved(self):
        task = self.task(reviewer="codex")
        cmd = [sys.executable, "-c", "import sys; print('overloaded_error', file=sys.stderr); sys.exit(2)"]
        with patch("agent_workflow.runner.command_for", return_value=cmd) as calls, patch("agent_workflow.runner.time.sleep"):
            with self.assertRaises(WorkflowError):
                execute(self.store, task["id"], timeout=5, repairs=0, recoveries=1)
        self.assertEqual(calls.call_count, 2)
        saved = self.store.get(task["id"])
        self.assertEqual(saved["failure"]["kind"], "transient")
        self.assertEqual(len(saved["recoveries"]), 1)
        self.assertEqual(saved["status"], "blocked")

    def test_review_timeout_retries_only_review(self):
        task = self.checked(self.task(reviewer="codex"))
        roles = []
        def command(agent, role, directory, model=None):
            roles.append(role)
            if len(roles) == 1:
                return [sys.executable, "-c", "import time; time.sleep(5)"]
            return self.fake_command(agent, role, directory, model)
        with patch("agent_workflow.runner.command_for", side_effect=command), patch("agent_workflow.runner.time.sleep"):
            result = execute(self.store, task["id"], timeout=.2, repairs=0)
        self.assertEqual(roles, ["review", "review"])
        self.assertEqual(result["status"], "completed")

    def test_cancel_is_never_recovered(self):
        task = self.task(reviewer="codex")
        with patch("agent_workflow.runner.invoke", side_effect=KeyboardInterrupt) as calls:
            with self.assertRaises(WorkflowError):
                execute(self.store, task["id"], recoveries=3)
        self.assertEqual(calls.call_count, 1)
        self.assertNotIn("recoveries", self.store.get(task["id"]))

    def test_partial_result_without_independent_review_stays_blocked(self):
        task = self.task()
        cmd = [sys.executable, "-c", "from pathlib import Path; import time; Path('note').write_text('partial'); time.sleep(5)"]
        with patch("agent_workflow.runner.command_for", return_value=cmd):
            with self.assertRaises(WorkflowError):
                execute(self.store, task["id"], timeout=.2, repairs=0)
        self.assertEqual(self.store.get(task["id"])["status"], "blocked")

    def test_tool_output_does_not_classify_as_provider_error(self):
        directory = self.root / 'classification'; directory.mkdir()
        (directory / 'stdout.log').write_text(json.dumps({'type':'item.completed','item':{'aggregated_output':'Forbidden paths: permission denied 502'}}) + '\n')
        self.assertEqual(classify({'timed_out':True}, directory), 'timeout')
        self.assertEqual(classify({'timed_out':False}, directory), 'process_failure')
        (directory / 'stdout.log').write_text(json.dumps({'type':'turn.failed','error':{'message':'insufficient_quota'}}))
        self.assertEqual(classify({'timed_out':False}, directory), 'quota')
        (directory / 'stdout.log').write_text(json.dumps({'type':'result','is_error':True,'result':'server_error'}, indent=2))
        self.assertEqual(classify({'timed_out':False}, directory), 'transient')
        (directory / 'stdout.log').write_text(json.dumps({'type':'result','is_error':True,'result':"You've hit your session limit"}))
        self.assertEqual(classify({'timed_out':False}, directory), 'quota')


if __name__ == "__main__":
    unittest.main()
