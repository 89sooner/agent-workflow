import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from agent_workflow.core import Store, WorkflowError
from agent_workflow.daemon import enqueue, list_jobs, resume_job, run_daemon, stop_job
from agent_workflow.operations import install_service, notifications, publish_notification, service_unit


class OperationsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = Store(self.root / "state")
        self.store.save({"id": "first", "status": "ready", "goal": "fixture"}, "created", create=True)
        self.store.save({"id": "other", "status": "ready", "goal": "fixture"}, "created", create=True)

    def tearDown(self):
        self.store.close(); self.temp.cleanup()

    def test_stop_only_selected_job_and_resume_same_settings(self):
        job = enqueue(self.store, "first", timeout=44, repairs=2, recoveries=1)
        other = enqueue(self.store, "other")
        started = threading.Event()
        original = subprocess.Popen
        children = []
        def fake(command, **kwargs):
            process = original([sys.executable, "-c", "import time; time.sleep(30)"], **kwargs)
            children.append(process); started.set(); return process
        with patch("agent_workflow.daemon.subprocess.Popen", side_effect=fake):
            thread = threading.Thread(target=run_daemon, args=(self.store.home, threading.Event()), kwargs={"once": True})
            thread.start()
            self.assertTrue(started.wait(5))
            result = stop_job(self.store, job["id"])
            self.assertEqual(result["cancel_requested"], 1)
            thread.join(8)
            self.assertFalse(thread.is_alive())
        jobs = {j["id"]: j for j in list_jobs(self.store)}
        self.assertEqual(jobs[job["id"]]["status"], "cancelled")
        self.assertEqual(jobs[other["id"]]["status"], "queued")
        self.assertIsNotNone(children[0].poll())
        resumed = resume_job(self.store, job["id"])
        self.assertEqual((resumed["timeout"], resumed["repairs"]), (44, 2))
        self.assertEqual(json.loads(resumed["options"])["recoveries"], 1)

    def test_explicit_notification_command_delivered_once(self):
        task = self.store.get("first")
        task["project_config"] = {"notifications": {"command": [sys.executable, "-c", "from pathlib import Path; p=Path('delivery.txt'); p.write_text(p.read_text()+'x' if p.exists() else 'x')"]}}
        first = publish_notification(self.store, task)
        second = publish_notification(self.store, task)
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(first["delivery"], "delivered")
        self.assertEqual(len(notifications(self.store)), 1)
        delivery = self.store.directory("first") / "notifications" / first["id"] / "delivery.txt"
        self.assertEqual(delivery.read_text(), "x")

    def test_user_service_install_is_explicit_and_preserves_foreign_file(self):
        result = install_service(self.store, user_home=self.root)
        self.assertFalse(result["enabled"])
        path = Path(result["path"])
        self.assertIn("KillMode=control-group", path.read_text())
        self.assertIn(str(self.store.home), path.read_text())
        path.write_text("# user's service\n")
        with self.assertRaises(WorkflowError):
            install_service(self.store, user_home=self.root)
        self.assertEqual(path.read_text(), "# user's service\n")

    def test_unit_escapes_specifiers_and_literal_dollars(self):
        unit = service_unit(self.root / 'with space $name %x', 8776)
        self.assertIn('$$name %%x', unit)
        with self.assertRaises(WorkflowError):
            service_unit(self.root / 'bad\npath', 8776)

    def test_shell_start_preserves_existing_profile_and_is_idempotent(self):
        profile = self.root / '.bash_profile'
        profile.write_text('export USER_FIXTURE=yes\n')
        with patch('agent_workflow.server.start_background', return_value={'running': True}):
            result = install_service(self.store, enable=True, user_home=self.root, backend='shell')
            install_service(self.store, enable=True, user_home=self.root, backend='shell')
        self.assertEqual(result['trigger'], 'interactive-login-shell')
        self.assertTrue(profile.read_text().startswith('export USER_FIXTURE=yes\n'))
        self.assertEqual(profile.read_text().count('# >>> agent-workflow >>>'), 1)
        self.assertFalse((self.root / '.profile').exists())
        self.assertEqual(subprocess.run(['sh', '-n', result['path']], capture_output=True).returncode, 0)

    def test_shell_install_without_enable_does_not_change_login_files(self):
        profile = self.root / '.profile'; profile.write_text('# preserved\n')
        result = install_service(self.store, user_home=self.root, backend='shell')
        self.assertFalse(result['enabled'])
        self.assertEqual(profile.read_text(), '# preserved\n')

    def test_shell_start_preserves_profile_symlink(self):
        target = self.root / 'dotfiles/login'; target.parent.mkdir(); target.write_text('# my settings\n')
        profile = self.root / '.profile'; profile.symlink_to(target)
        with patch('agent_workflow.server.start_background', return_value={'running': True}):
            install_service(self.store, enable=True, user_home=self.root, backend='shell')
        self.assertTrue(profile.is_symlink())
        self.assertTrue(target.read_text().startswith('# my settings\n'))

    def test_noninteractive_shell_does_not_start_daemon(self):
        import shlex
        with patch('agent_workflow.server.start_background', return_value={'running': True}):
            result = install_service(self.store, enable=True, user_home=self.root, backend='shell')
        marker = self.root / 'unexpected-start'
        Path(result['path']).write_text('#!/bin/sh\ntouch ' + shlex.quote(str(marker)) + '\n')
        subprocess.run(['bash', '--noprofile', '--norc', '-c', '. ' + shlex.quote(result['profile'])], check=True)
        self.assertFalse(marker.exists())


if __name__ == "__main__":
    unittest.main()
