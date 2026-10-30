from pathlib import Path
import socket
import sys
import time
import unittest
from unittest.mock import patch

import test_workflow as fixtures
from agent_workflow.core import WorkflowError
from agent_workflow.profiles import save
from agent_workflow.workspaces import create_from_profile
from agent_workflow.runner import run_checks
from agent_workflow.services import running
from agent_workflow.server import identity


class ServiceTests(unittest.TestCase):
    setUp = fixtures.WorkflowTests.setUp
    tearDown = fixtures.WorkflowTests.tearDown

    def config(self, port):
        return {"project": str(self.project), "checks": [[sys.executable, "-c", "import os,urllib.request; assert urllib.request.urlopen(os.environ['WORKFLOW_SERVICE_WEB_URL']).status == 200"]],
                "services": [{"name": "web", "argv": [sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1"],
                              "port": port, "ready_url": f"http://127.0.0.1:{port}/", "startup_seconds": 5}]}

    def test_server_lifecycle_and_evidence_environment(self):
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0)); port = probe.getsockname()[1]
        save(self.store, "web", self.config(port))
        task = create_from_profile(self.store, "WEB", "web", "HTTP response", ["200"])
        with self.store.lock(task["project"]) as fd:
            self.assertTrue(run_checks(self.store, task, 5, fd))
        service = task["active_services"][0]
        self.assertEqual(service["state"], "stopped")
        self.assertNotEqual(identity(service["pid"]), service["process_identity"])
        self.assertTrue(task["verification"]["passed"])

    def test_occupied_port_not_reused_or_killed(self):
        with socket.socket() as other:
            other.bind(("127.0.0.1", 0)); other.listen()
            port = other.getsockname()[1]
            save(self.store, "web", self.config(port))
            task = create_from_profile(self.store, "BUSY", "web", "HTTP response", ["200"])
            with self.store.lock(task["project"]) as fd:
                with self.assertRaisesRegex(WorkflowError, "포트"):
                    run_checks(self.store, task, 5, fd)
            self.assertEqual(other.getsockname()[1], port)
            self.assertIsNone(task["verification"])

    def test_check_failure_still_reaps_owned_server(self):
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0)); port = probe.getsockname()[1]
        cfg = self.config(port); cfg["checks"] = [[sys.executable, "-c", "raise SystemExit(8)"]]
        save(self.store, "web", cfg)
        task = create_from_profile(self.store, "BAD", "web", "failing check", ["must fail"])
        with self.store.lock(task["project"]) as fd:
            self.assertFalse(run_checks(self.store, task, 5, fd))
        self.assertEqual(task["active_services"][0]["state"], "stopped")

    def test_expired_global_deadline_never_starts_a_service(self):
        save(self.store, "expired", self.config(55432))
        task = create_from_profile(self.store, "EXPIRED", "expired", "service", ["bounded"])
        with patch("agent_workflow.services.subprocess.Popen") as start:
            with self.assertRaisesRegex(WorkflowError, "시간 한도"):
                with running(self.store, task, None, deadline=time.monotonic() - 1):
                    self.fail("expired deadline entered the service context")
        start.assert_not_called()

    def test_global_deadline_caps_service_startup_and_reaps_owned_child(self):
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0)); port = probe.getsockname()[1]
        cfg = self.config(port)
        cfg["services"][0]["argv"] = [sys.executable, "-c", "import time; time.sleep(30)"]
        save(self.store, "slow", cfg)
        task = create_from_profile(self.store, "SLOW", "slow", "service", ["bounded"])
        started = time.monotonic()
        with self.store.lock(task["project"]) as fd:
            with self.assertRaisesRegex(WorkflowError, "준비 시간 초과"):
                with running(self.store, task, fd, deadline=started + .25):
                    self.fail("unready service entered the context")
        self.assertLess(time.monotonic() - started, 2)
        self.assertEqual(task["active_services"][0]["state"], "stopped")
        record = task["active_services"][0]
        self.assertNotEqual(identity(record["pid"]), record["process_identity"])


if __name__ == "__main__":
    unittest.main()
