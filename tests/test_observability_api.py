import json
import sys
import unittest

import test_server as fixtures
from agent_workflow.core import Store
from agent_workflow.runner import run_checks


class ObservationAPITests(unittest.TestCase):
    setUp = fixtures.DashboardTests.setUp
    tearDown = fixtures.DashboardTests.tearDown
    request = fixtures.DashboardTests.request

    def trace(self):
        store = Store(self.home)
        try:
            task = store.get("WEB-1")
            task["checks"] = [[sys.executable, "-c", "print('local log fixture')"]]
            store.save(task, "check_added")
            with store.lock(task["project"]) as fd:
                self.assertTrue(run_checks(store, task, 5, fd))
        finally:
            store.close()

    def test_trace_and_export_are_bounded_local_readonly_responses(self):
        self.trace()
        code, body, headers = self.request("/api/observability?task_id=WEB-1&limit=2")
        self.assertEqual(code, 200)
        result = json.loads(body)
        self.assertEqual(result["schema_version"], 1)
        self.assertEqual(len(result["spans"]), 1)
        self.assertEqual(len(result["events"]), 2)
        self.assertTrue(result["pagination"]["has_more"])
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(result["task"]["goal"], "<script>bad()</script>")
        self.assertNotIn("local log fixture", body.decode())
        span = result["spans"][0]
        status, raw, _ = self.request(f"/api/observability/artifacts/{span['id']}/stdout")
        self.assertEqual(status, 200)
        self.assertIn("local log fixture", json.loads(raw)["text"])
        status, raw, headers = self.request("/api/observability/export?task_id=WEB-1")
        self.assertEqual(status, 200)
        self.assertIn("attachment", headers["Content-Disposition"])
        self.assertFalse(json.loads(raw)["coverage"]["cli_internal_tools"])
        self.assertEqual(self.request("/api/observability?task_id=WEB-1&limit=invalid")[0], 400)
        self.assertEqual(self.request("/api/observability?task_id=WEB-1&limit=999999")[0], 400)
        self.assertEqual(self.request("/api/observability?task_id=WEB-1&run_id=missing")[0], 400)
        self.assertEqual(self.request("/api/observability")[0], 400)
        self.assertEqual(self.request("/api/observability/artifacts/missing/../../core.py")[0], 400)

    def test_hold_api_uses_csrf_and_blocks_preexisting_queue(self):
        headers = {"X-Workflow-Token": "test-token"}
        status, _, _ = self.request("/api/queue", {"task_id": "WEB-1"}, headers)
        self.assertEqual(status, 200)
        self.assertEqual(self.request("/api/hold", {"task_id": "WEB-1", "reason": "확인 대기"})[0], 403)
        self.assertEqual(self.request("/api/hold", {"task_id": "WEB-1", "reason": []}, headers)[0], 400)
        status, raw, _ = self.request("/api/hold", {"task_id": "WEB-1", "reason": "확인 대기"}, headers)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(raw)["task"]["hold"]["reason"], "확인 대기")
        store = Store(self.home)
        try:
            from agent_workflow.daemon import _claim
            self.assertIsNone(_claim(store))
            self.assertEqual(self.request("/api/unhold", {"task_id": "WEB-1"})[0], 403)
            self.assertEqual(self.request("/api/unhold", {"task_id": "WEB-1"}, headers)[0], 200)
            self.assertEqual(_claim(store)["task_id"], "WEB-1")
        finally:
            store.close()

    def test_unobservable_worktree_has_explicit_reason_not_loading_forever(self):
        (self.root / "project").rename(self.root / "moved-project")
        status, body, _ = self.request("/api/tasks/WEB-1")
        self.assertEqual(status, 200)
        result = json.loads(body)
        self.assertEqual(result["observation"]["status"], "unavailable")
        self.assertTrue(result["observation"]["reason"])
        self.assertEqual(result["freshness"], {})
        self.assertEqual(result["task"]["status"], "ready")


if __name__ == "__main__":
    unittest.main()
