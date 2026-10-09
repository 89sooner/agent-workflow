import json
from pathlib import Path
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from agent_workflow.core import Store, create_task, git
from agent_workflow.server import handler


class DashboardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="workflow-web-test-")
        self.root = Path(self.tmp.name)
        p = self.root / "project"
        p.mkdir()
        git(p, "init", "-q")
        (p / "app.txt").write_text("fixture")
        git(p, "add", ".")
        git(p, "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "fixture")
        self.home = self.root / "state"
        s = Store(self.home)
        create_task(s, "WEB-1", p, "<script>bad()</script>", ["local"], [], [], "claude", "none", "fixture")
        s.close()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler(self.home, "test-token"))
        self.thread = threading.Thread(target=self.server.serve_forever)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.tmp.cleanup()

    def request(self, path, body=None, headers=None):
        req = Request(self.url + path, data=json.dumps(body).encode() if body is not None else None,
                      headers=headers or {})
        try:
            with urlopen(req, timeout=5) as r:
                return r.status, r.read(), r.headers
        except HTTPError as exc:
            return exc.code, exc.read(), exc.headers

    def test_live_state_and_static_assets(self):
        code, body, headers = self.request("/api/state")
        self.assertEqual(code, 200)
        state = json.loads(body)
        self.assertEqual(state["tasks"][0]["id"], "WEB-1")
        self.assertEqual(state["csrf_token"], "test-token")
        self.assertEqual(self.request("/")[0], 200)
        self.assertEqual(self.request("/static/app.js")[0], 200)
        self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])

    def test_missing_csrf_cannot_queue(self):
        code, _, _ = self.request("/api/queue", {"task_id": "WEB-1"})
        self.assertEqual(code, 403)
        self.assertEqual(json.loads(self.request("/api/state")[1])["jobs"], [])

    def test_queue_duplicate_and_cancel(self):
        headers = {"X-Workflow-Token": "test-token"}
        code, body, _ = self.request("/api/queue", {"task_id": "WEB-1"}, headers)
        self.assertEqual(code, 200)
        job = json.loads(body)["job"]
        self.assertEqual(self.request("/api/queue", {"task_id": "WEB-1"}, headers)[0], 400)
        code, body, _ = self.request("/api/cancel", {"job_id": job["id"]}, headers)
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(body)["job"]["status"], "cancelled")

    def test_other_origin_and_host_rejected(self):
        self.assertEqual(self.request("/api/state", headers={"Host": "malicious.invalid"})[0], 400)
        headers = {"Origin": "https://malicious.invalid", "X-Workflow-Token": "test-token"}
        self.assertEqual(self.request("/api/queue", {"task_id": "WEB-1"}, headers)[0], 400)

    def test_no_arbitrary_file_or_release_without_config(self):
        self.assertEqual(self.request("/static/../../core.py")[0], 404)
        code, _, _ = self.request("/api/queue", {"task_id": "WEB-1", "action": "release"},
                                   {"X-Workflow-Token": "test-token"})
        self.assertEqual(code, 400)

    def test_create_from_registered_profile_and_queue_options(self):
        import sys
        from agent_workflow.profiles import save
        with_store = Store(self.home)
        try:
            save(with_store, "fixture", {"project": str(self.root / "project"), "checks": [[sys.executable, "-c", "pass"]],
                                         "execution": {"timeout": 1200, "recoveries": 2}})
        finally:
            with_store.close()
        headers = {"X-Workflow-Token": "test-token"}
        payload = {"id": "WEB-NEW", "profile": "fixture", "goal": "new goal", "criteria": ["criterion"], "isolate": True}
        self.assertEqual(self.request("/api/tasks", payload)[0], 403)
        code, body, _ = self.request("/api/tasks", payload, headers)
        self.assertEqual(code, 201)
        task = json.loads(body)["task"]
        self.assertNotEqual(task["project"], str(self.root / "project"))
        code, body, _ = self.request("/api/queue", {"task_id": "WEB-NEW", "timeout": 123, "repairs": 0, "recoveries": 0}, headers)
        self.assertEqual(code, 200)
        job = json.loads(body)["job"]
        self.assertEqual((job["timeout"], job["repairs"]), (123, 0))
        self.assertEqual(json.loads(job["options"])["recoveries"], 0)
        self.assertEqual(self.request("/api/queue", {"task_id": "WEB-1", "timeout": True}, headers)[0], 400)

    def test_stop_and_resume_use_csrf_and_preserve_job_options(self):
        headers = {"X-Workflow-Token": "test-token"}
        _, body, _ = self.request("/api/queue", {"task_id": "WEB-1", "timeout": 111}, headers)
        job = json.loads(body)["job"]
        self.assertEqual(self.request("/api/stop", {"job_id": job["id"]})[0], 403)
        self.assertEqual(self.request("/api/stop", {"job_id": job["id"]}, headers)[0], 200)
        code, body, _ = self.request("/api/resume", {"job_id": job["id"]}, headers)
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(body)["job"]["timeout"], 111)

    def test_job_log_is_bounded_and_cannot_read_another_file(self):
        headers = {"X-Workflow-Token": "test-token"}
        _, body, _ = self.request("/api/queue", {"task_id": "WEB-1"}, headers)
        job = json.loads(body)["job"]
        store = Store(self.home)
        try:
            log = self.home / "jobs" / (job["id"] + ".log")
            log.parent.mkdir(); log.write_text('x' * 70000 + 'end')
            with store.db:
                store.db.execute("UPDATE jobs SET log_path=? WHERE id=?", (str(log), job["id"]))
            code, body, _ = self.request('/api/job-log/' + job['id'])
            self.assertEqual(code, 200)
            data = json.loads(body)
            self.assertTrue(data['truncated']); self.assertTrue(data['text'].endswith('end'))
            self.assertLessEqual(len(data['text']), 65536)
            with store.db:
                store.db.execute("UPDATE jobs SET log_path=? WHERE id=?", (str(self.root / 'project/app.txt'), job["id"]))
            self.assertEqual(self.request('/api/job-log/' + job['id'])[0], 400)
        finally:
            store.close()


if __name__ == "__main__":
    unittest.main()
