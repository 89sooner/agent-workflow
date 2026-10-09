import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import test_workflow as fixtures
from agent_workflow.core import WorkflowError
from agent_workflow.runner import execute
from agent_workflow.usage import configure, observation, reconcile_pending


class UsageTests(unittest.TestCase):
    setUp = fixtures.WorkflowTests.setUp
    tearDown = fixtures.WorkflowTests.tearDown
    task = fixtures.WorkflowTests.task
    result = staticmethod(fixtures.WorkflowTests.result)
    fake_command = fixtures.WorkflowTests.fake_command

    def test_call_budget_survives_resume_and_configuration(self):
        task = self.task(reviewer="codex")
        configure(self.store, task["id"], {"max_agent_calls": 1})
        with patch("agent_workflow.runner.command_for", side_effect=self.fake_command) as command:
            with self.assertRaisesRegex(WorkflowError, "호출 한도"):
                execute(self.store, task["id"], timeout=5, repairs=0)
            self.assertEqual(command.call_count, 1)
            with self.assertRaisesRegex(WorkflowError, "호출 한도"):
                execute(self.store, task["id"], timeout=5, repairs=0)
            self.assertEqual(command.call_count, 1)
        configure(self.store, task["id"], {"max_agent_calls": 2})
        with patch("agent_workflow.runner.command_for", side_effect=self.fake_command):
            completed = execute(self.store, task["id"], timeout=5, repairs=0)
        self.assertEqual(completed["usage"]["agent_calls"], 2)
        self.assertEqual(completed["status"], "completed")

    def test_model_routes_only_use_explicit_transient_fallbacks(self):
        task = self.task()
        configure(self.store, task["id"], {"models": {"implement": "primary-model"}, "fallback_models": {"implement": ["backup-model"]}})
        seen = []
        def command(agent, role, directory, model=None):
            seen.append(model)
            if len(seen) == 1:
                return [sys.executable, "-c", "import sys; print('server_error',file=sys.stderr); sys.exit(2)"]
            return self.fake_command(agent, role, directory, model)
        with patch("agent_workflow.runner.command_for", side_effect=command), patch("agent_workflow.runner.time.sleep"):
            result = execute(self.store, task["id"], timeout=5, repairs=0)
        self.assertEqual(seen, ["primary-model", "backup-model"])
        self.assertEqual(result["usage"]["agent_calls"], 2)
        self.assertTrue(all(x["reported_cost_usd"] is None for x in result["usage"]["observations"]))

    def test_reported_usage_and_unknown_values(self):
        directory = self.root / "usage"; directory.mkdir()
        (directory / "stdout.log").write_text(json.dumps({"subtype": "success", "usage": {"input_tokens": 11, "output_tokens": 4}, "total_cost_usd": .03}, indent=2))
        known = observation("claude", directory)
        self.assertEqual(known["tokens"]["output_tokens"], 4)
        self.assertEqual(known["reported_cost_usd"], .03)
        (directory / "stdout.log").write_text('not usage\n')
        unknown = observation("codex", directory)
        self.assertFalse(unknown["known"])
        self.assertIsNone(unknown["tokens"])

    def test_crash_keeps_unknown_runtime_reserved(self):
        task = self.task()
        task["usage"] = {"agent_calls": 1, "seconds": 3, "uncertain_seconds": 0, "observations": [], "reported_cost_usd": 0,
                         "pending": {"reserved_seconds": 50, "directory": "unknown"}}
        self.store.save(task, "fixture_crash")
        usage = reconcile_pending(self.store, task)
        self.assertEqual(usage["uncertain_seconds"], 50)
        self.assertEqual(reconcile_pending(self.store, task)["uncertain_seconds"], 50)

    def test_total_time_limit_stops_subprocess(self):
        task = self.task()
        configure(self.store, task["id"], {"max_seconds": 1, "recoveries": 0})
        with patch("agent_workflow.runner.command_for", return_value=[sys.executable, "-c", "import time; time.sleep(30)"]):
            with self.assertRaises(WorkflowError):
                execute(self.store, task["id"], timeout=20, repairs=0)
        saved = self.store.get(task["id"])
        self.assertGreaterEqual(saved["usage"]["seconds"], 1)
        with patch("agent_workflow.runner.command_for") as command:
            with self.assertRaisesRegex(WorkflowError, "시간 한도"):
                execute(self.store, task["id"])
            command.assert_not_called()


if __name__ == "__main__":
    unittest.main()
