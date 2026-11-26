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
        self.assertEqual(self.request("/static/app.js")[0], 404)
        self.assertEqual(self.request("/")[1], self.request("/mission")[1])
        self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])

    def test_react_mission_assets_are_local_and_allowlisted(self):
        for path, mime in (("/mission", "text/html"), ("/mission/", "text/html"),
                           ("/static/mission.js", "text/javascript"),
                           ("/static/mission.css", "text/css"),
                           ("/static/mission-icon.svg", "image/svg+xml"),
                           ("/static/MISSION_LICENSES.txt", "text/plain")):
            code, body, headers = self.request(path)
            self.assertEqual(code, 200)
            self.assertTrue(body)
            self.assertTrue(headers["Content-Type"].startswith(mime))
            self.assertIn("script-src 'self'", headers["Content-Security-Policy"])
        html = self.request("/mission")[1].decode()
        self.assertIn('/static/mission.js', html)
        self.assertNotIn('https://', html)
        self.assertEqual(self.request('/static/../server.py')[0], 404)
        self.assertEqual(json.loads(self.request('/api/state')[1])['jobs'], [])

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
                                         "check_groups": {"syntax": [[sys.executable, "--version"]]},
                                         "execution": {"timeout": 1200, "recoveries": 2}})
        finally:
            with_store.close()
        headers = {"X-Workflow-Token": "test-token"}
        payload = {"id": "WEB-NEW", "profile": "fixture", "goal": "new goal", "criteria": ["criterion"], "isolate": True}
        payload["check_groups"] = ["syntax"]
        self.assertEqual(self.request("/api/tasks", payload)[0], 403)
        self.assertEqual(self.request("/api/tasks", {**payload, "check_groups": ["missing"]}, headers)[0], 400)
        code, body, _ = self.request("/api/tasks", payload, headers)
        self.assertEqual(code, 201)
        task = json.loads(body)["task"]
        self.assertNotEqual(task["project"], str(self.root / "project"))
        self.assertEqual(task["checks"][-1], [sys.executable, "--version"])
        details = json.loads(self.request("/api/tasks/WEB-NEW")[1])
        self.assertEqual(details["release_status"]["source"], "local_ledger")
        code, body, _ = self.request("/api/queue", {"task_id": "WEB-NEW", "timeout": 123, "repairs": 0, "recoveries": 0}, headers)
        self.assertEqual(code, 200)
        job = json.loads(body)["job"]
        self.assertEqual((job["timeout"], job["repairs"]), (123, 0))
        self.assertEqual(json.loads(job["options"])["recoveries"], 0)
        self.assertEqual(self.request("/api/queue", {"task_id": "WEB-1", "timeout": True}, headers)[0], 400)

    def test_project_goal_routes_use_registered_contract_and_keep_domains_distinct(self):
        from contextlib import closing
        from agent_workflow.profiles import save
        project = self.root / "project"
        (project / "guide.md").write_text("Check app.txt and work locally.")
        git(project, "add", "guide.md")
        git(project, "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "guide")
        with closing(Store(self.home)) as store:
            save(store, "local-goal", {
                "project": str(project), "allowed_paths": ["app.txt"], "checks": [["git", "diff", "--check"]],
                "goal_mode": {"version": 1, "scope": "로컬 파일 점검", "instructions": ["guide.md"],
                              "input_extensions": [".json"]},
            })
        headers = {"X-Workflow-Token": "test-token"}
        payload = {"id": "PROJECT", "profile": "local-goal", "goal": "파일 점검", "criteria": ["확인"]}
        capability, = json.loads(self.request("/api/state")[1])["profiles"]
        self.assertTrue(capability["goal_mode_available"])
        self.assertFalse(capability["katakuri_goal_available"])
        self.assertEqual(self.request("/api/goals", payload)[0], 403)
        self.assertEqual(self.request("/api/goals?ledger=foreign", payload, headers)[0], 403)
        self.assertEqual(self.request("/api/katakuri-goals", payload, headers)[0], 400)
        for overridden in ({"owner": "codex"}, {"reviewer": "none"}, {"isolate": False},
                           {"workspace": {}}, {"milestone": "M6"}, {"instrument": "HSI"}):
            with self.subTest(overridden=overridden):
                self.assertEqual(self.request("/api/goals", {**payload, **overridden}, headers)[0], 400)
        code, body, _ = self.request("/api/goals", payload, headers)
        self.assertEqual(code, 201, body)
        task = json.loads(body)["task"]
        detail = json.loads(self.request("/api/tasks/PROJECT")[1])
        self.assertEqual(detail["goal_mode"]["kind"], "project")
        self.assertIsNone(detail["katakuri_goal"])
        self.assertNotEqual(task["project"], str(project))
        budget = {"task_id": "PROJECT", "max_agent_calls": 12}
        self.assertEqual(self.request("/api/katakuri-goals/budget", budget, headers)[0], 400)
        self.assertEqual(self.request("/api/goals/budget", budget, headers)[0], 200)
        with closing(Store(self.home)) as store:
            task = store.get("PROJECT")
            task["goal_mode"]["phase"] = "waiting"
            task["goal_mode"]["items"][0]["state"] = "waiting"
            task["status"] = "blocked"
            store.save(task, "fixture_wait")
        evidence = {"task_id": "PROJECT", "criterion": 1, "evidence": '{"checked": true}'}
        self.assertEqual(self.request("/api/katakuri-goals/input", evidence, headers)[0], 400)
        self.assertEqual(self.request("/api/goals/input", {**evidence, "extension": ".txt"}, headers)[0], 400)
        code, body, _ = self.request("/api/goals/input", {**evidence, "extension": ".json"}, headers)
        self.assertEqual(code, 200, body)
        self.assertEqual(json.loads(body)["goal_mode"]["criteria"][0]["state"], "pending")

    def test_advisor_requires_unchanged_preview_and_limits_concurrent_calls(self):
        from concurrent.futures import ThreadPoolExecutor
        from unittest.mock import patch
        from agent_workflow.profiles import save

        store = Store(self.home)
        try:
            save(store, "fixture", {"project": str(self.root / "project"),
                                    "check_groups": {"syntax": [["git", "diff", "--check"]]}})
        finally:
            store.close()
        options = {"profile":"fixture", "criteria":["validate code"]}
        headers = {"X-Workflow-Token":"test-token"}
        preview_path, run_path = "/api/check-advisor/preview", "/api/check-advisor/run"
        started, finish = threading.Event(), threading.Event()

        def answer(*args):
            started.set()
            if not finish.wait(4):
                raise AssertionError("concurrent request did not finish")
            return {"groups":["syntax"], "reason":"test"}

        with patch("agent_workflow.check_advisor_models.ask", side_effect=answer) as ask:
            self.assertEqual(self.request(preview_path, options)[0], 403)
            self.assertEqual(self.request(preview_path, options, {**headers, "Origin":"https://other.invalid"})[0], 400)
            self.assertEqual(self.request(preview_path, {**options, "project":"/tmp/outside"}, headers)[0], 400)
            code, raw, _ = self.request(preview_path, options, headers)
            self.assertEqual(code, 200)
            preview = json.loads(raw)
            self.assertEqual(preview["status"], "preview")
            run = {**options, "input_digest":preview["input_digest"], "providers":preview["providers"]}
            self.assertEqual(self.request(run_path, options, headers)[0], 400)
            self.assertEqual(self.request(run_path, {**run, "providers":["codex", "claude"]}, headers)[0], 400)
            self.assertEqual(self.request(run_path, {**run, "criteria":["other"]}, headers)[0], 400)
            self.assertEqual(self.request(run_path, {**run, "record_result":True}, headers)[0], 400)
            tracked = self.root / "project/app.txt"
            tracked.write_text("changed since preview")
            self.assertEqual(self.request(run_path, run, headers)[0], 400)
            ask.assert_not_called()
            # Confirm the current packet after changing the worktree. Rewriting the old
            # bytes need not restore the previous Git change listing on every runner.
            code, raw, _ = self.request(preview_path, options, headers)
            self.assertEqual(code, 200)
            refreshed = json.loads(raw)
            run = {**options, "input_digest":refreshed["input_digest"], "providers":refreshed["providers"]}

            with ThreadPoolExecutor(1) as pool:
                future = pool.submit(self.request, run_path, run, headers)
                try:
                    if not started.wait(3):
                        self.fail(f"confirmed call did not reach the model: {future.result(timeout=5)[:2]}")
                    self.assertEqual(self.request(run_path, run, headers)[0], 409)
                    self.assertEqual(self.request(preview_path, options, headers)[0], 200)
                finally:
                    finish.set()
                code, raw, _ = future.result()
            self.assertEqual(code, 200)
            report = json.loads(raw)
            self.assertEqual(report["suggested_groups"], ["syntax"])
            self.assertNotIn("recommendation_id", report)
            ask.assert_called_once()
            self.assertEqual(self.request(run_path, run, headers)[0], 200)
        recs = json.loads(self.request("/api/recommendations?profile=fixture")[1])
        self.assertEqual(recs["recommendations"], [])

    def test_recorded_recommendation_and_feedback(self):
        from unittest.mock import patch
        from agent_workflow.profiles import save
        from agent_workflow.check_advisor import recommend
        store = Store(self.home)
        try:
            save(store, "fixture", {"project": str(self.root / "project"),
                                    "check_groups": {"syntax": [["git", "diff", "--check"]]}})
        finally:
            store.close()
        with patch("agent_workflow.check_advisor_models.ask", return_value={"groups":["syntax"], "reason":"check"}):
            report = recommend(self.home, "fixture", ["criterion"], reviewer="none", record_result=True)
        rid = report["recommendation_id"]
        headers = {"X-Workflow-Token": "test-token"}
        self.assertEqual(json.loads(self.request("/api/recommendations/" + rid)[1])["suggested_groups"], ["syntax"])
        self.assertEqual(self.request("/api/recommendations")[0], 400)
        self.assertEqual(self.request("/api/recommendations?profile=%2Fbad")[0], 400)
        code, body, _ = self.request("/api/recommendations?profile=fixture")
        self.assertEqual(code, 200)
        self.assertEqual([r["id"] for r in json.loads(body)["recommendations"]], [rid])
        self.assertEqual(json.loads(self.request("/api/recommendations?profile=other")[1])["recommendations"], [])
        payload = {"id":"LINKED", "profile":"fixture", "goal":"job", "criteria":["criterion"],
                   "recommendation_id":rid, "isolate":False}
        self.assertEqual(self.request("/api/tasks", payload, headers)[0], 201)
        labels = {"task_id":"LINKED", "expected_groups":[]}
        self.assertEqual(self.request("/api/recommendations/feedback", labels)[0], 403)
        self.assertEqual(self.request("/api/recommendations/feedback", labels, headers)[0], 200)
        linked = json.loads(self.request("/api/tasks/LINKED")[1])["recommendation"]
        self.assertEqual(linked["recommendation_id"], rid)
        self.assertEqual(linked["adopted_groups"], ["syntax"])
        self.assertEqual(linked["expected_groups"], [])

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
