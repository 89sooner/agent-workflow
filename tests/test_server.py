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


if __name__ == "__main__":
    unittest.main()
