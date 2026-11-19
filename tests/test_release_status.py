import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from agent_workflow.core import WorkflowError
from agent_workflow.release_status import status, summarize_release


class ReleaseStatusTests(unittest.TestCase):
    def setUp(self):
        self.task = {"id": "demo", "status": "ready", "project": "/deleted/worktree"}
        self.release = {"stage": "configured", "config": {"deploy": ["apply"], "health": ["probe"]}}
        self.merge_sha = "b" * 40

    def test_completed_locally_without_release(self):
        self.task.update(status="completed", completed_fingerprint="tree", verification={"passed": True})
        summary = summarize_release(self.task)
        self.assertEqual(summary["source"], "local_ledger")
        self.assertEqual(summary["local_verification"], "succeeded")
        self.assertEqual(summary["local_completion"], "succeeded")
        for phase in ("merge", "deployment", "health"):
            self.assertEqual(summary[phase], "not_configured")

    def test_configuration_and_failure_do_not_attest_to_later_steps(self):
        self.task.update(status="blocked", verification={"passed": False}, release=self.release)
        summary = summarize_release(self.task)
        self.assertEqual(summary["local_verification"], "failed")
        self.assertEqual(summary["local_completion"], "not_completed")
        for phase in ("merge", "deployment", "health"):
            self.assertEqual(summary[phase], "not_attempted")
        self.release.update(stage="pull_request", pr=7, error="CI could not be read")
        summary = summarize_release(self.task)
        self.assertEqual(summary["merge"], "uncertain")
        self.assertEqual(summary["error"], "CI could not be read")
        self.assertIsNone(summary["merge_commit"])

    def test_candidate_check_and_remote_merge_have_separate_receipts(self):
        self.task["release"] = self.release
        receipt = {"revision": self.merge_sha, "passed": False, "started_at": "T0"}
        self.release.update(stage="pushed", local_merge_candidate=self.merge_sha, integration_verification=receipt)
        self.assertEqual(summarize_release(self.task)["integration_check"], "uncertain")
        receipt.update(passed=True, finished_at="T1")
        summary = summarize_release(self.task)
        self.assertEqual(summary["integration_check"], "succeeded")
        self.assertEqual(summary["merge"], "uncertain")
        self.release.update(stage="merged", merge_commit="c" * 40)
        summary = summarize_release(self.task)
        self.assertEqual(summary["integration_check"], "uncertain")
        self.assertEqual(summary["integration_revision"], self.merge_sha)
        self.assertEqual(summary["merge"], "succeeded")
        self.release["config"].update(deploy=None, health=None)
        self.assertEqual(summarize_release(self.task)["deployment"], "not_configured")

    def test_interrupted_deployment_and_health_retry(self):
        self.task["release"] = self.release
        failure = {"timed_out": False, "exit_code": 7}
        success = {"timed_out": False, "exit_code": 0}
        self.release.update(stage="deploying", merge_commit=self.merge_sha, deployment_state="failed",
                            deployment_result=failure)
        self.assertEqual(summarize_release(self.task)["deployment"], "failed")
        self.release["deployment_state"] = "started"
        self.assertEqual(summarize_release(self.task)["deployment"], "uncertain")
        self.release.update(deployment_state="applied", deployment_result=success)
        summary = summarize_release(self.task)
        self.assertEqual(summary["deployment"], "applied")
        self.assertEqual(summary["health"], "not_recorded")
        self.release["health_result"] = failure
        self.assertEqual(summarize_release(self.task)["health"], "failed")
        self.release["health_result"] = success
        self.assertEqual(summarize_release(self.task)["health"], "uncertain")
        self.release.update(stage="deployed", deployed_revision=self.merge_sha, deployed_at="T2")
        summary = summarize_release(self.task)
        self.assertEqual(summary["health"], "succeeded")
        self.assertEqual(summary["deployment_revision"], self.merge_sha)
        self.release["merge_commit"] = "other"
        self.assertEqual(summarize_release(self.task)["health"], "uncertain")

    def test_legacy_read_only_lookup_in_absence_of_worktree_and_no_missing_home_creation(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            missing = home / "missing"
            with self.assertRaises(WorkflowError):
                status(missing, "demo")
            self.assertFalse(missing.exists())
            path = home / "state.sqlite3"
            db = sqlite3.connect(path)
            with db:
                db.execute("CREATE TABLE tasks(id TEXT PRIMARY KEY, data TEXT)")
                db.execute("INSERT INTO tasks VALUES(?, ?)", ("demo", json.dumps(self.task)))
            db.close()
            original = path.read_bytes()
            with patch("subprocess.Popen", side_effect=AssertionError("unexpected external operation")):
                self.assertEqual(status(home, "demo")["task_id"], "demo")
                with self.assertRaisesRegex(WorkflowError, "작업 없음"):
                    status(home, "absent")
            self.assertEqual(path.read_bytes(), original)
            self.assertEqual(list(home.iterdir()), [path])
