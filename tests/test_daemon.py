import fcntl
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from agent_workflow.core import Store, WorkflowError
from agent_workflow.daemon import cancel, enqueue, list_jobs, run_daemon


class DaemonTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name)
        self.store = Store(self.home)
        self.store.save({"id": "task"}, "created", create=True)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_queue_duplicate_cancel_and_reenqueue(self):
        job = enqueue(self.store, "task")
        self.assertEqual(job["status"], "queued")
        other = Store(self.home)
        try:
            with self.assertRaises(WorkflowError):
                enqueue(other, "task", action="release")
        finally:
            other.close()
        self.assertEqual(cancel(self.store, job["id"])["status"], "cancelled")
        with self.assertRaises(WorkflowError):
            cancel(self.store, job["id"])
        enqueue(self.store, "task", action="release")
        self.assertEqual(len(list_jobs(self.store)), 2)

    def test_invalid_inputs(self):
        for options in ({"action": "arbitrary"}, {"timeout": 0}, {"repairs": 4}, {"timeout": True}):
            with self.assertRaises(WorkflowError):
                enqueue(self.store, "task", **options)
        with self.assertRaises(WorkflowError):
            enqueue(self.store, "missing")

    def test_once_runs_process_records_log_and_failure(self):
        original = subprocess.Popen
        commands = []

        def fake(command, **kwargs):
            commands.append(command)
            return original([sys.executable, "-c", "print('fixture result'); raise SystemExit(7)"], **kwargs)

        enqueue(self.store, "task", timeout=12, repairs=2)
        with patch("agent_workflow.daemon.subprocess.Popen", side_effect=fake):
            self.assertEqual(run_daemon(self.home, threading.Event(), once=True), 1)
        job = list_jobs(self.store)[0]
        self.assertEqual(job["status"], "failed")
        self.assertEqual(job["returncode"], 7)
        self.assertIn("fixture result", Path(job["log_path"]).read_text())
        self.assertIn("--repairs", commands[0])
        self.assertIsNotNone(job["pid"])
        self.assertIsNotNone(job["started_at"])
        self.assertIsNotNone(job["finished_at"])

    def test_restart_interrupts_running_without_retry(self):
        job = enqueue(self.store, "task")
        with self.store.db:
            self.store.db.execute("UPDATE jobs SET status='running' WHERE id=?", (job["id"],))
        with patch("agent_workflow.daemon.subprocess.Popen") as popen:
            self.assertEqual(run_daemon(self.home, threading.Event(), once=True), 0)
            popen.assert_not_called()
        self.assertEqual(list_jobs(self.store)[0]["status"], "interrupted")

    def test_exclusive_lock_does_not_recover_active_job(self):
        job = enqueue(self.store, "task")
        with self.store.db:
            self.store.db.execute("UPDATE jobs SET status='running' WHERE id=?", (job["id"],))
        with (self.home / "daemon.lock").open("a+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaises(WorkflowError):
                run_daemon(self.home, threading.Event(), once=True)
        self.assertEqual(list_jobs(self.store)[0]["status"], "running")

    def test_stop_terminates_running_process(self):
        original = subprocess.Popen
        stop = threading.Event()
        processes = []

        def fake(command, **kwargs):
            process = original([sys.executable, "-c", "import time; time.sleep(60)"], **kwargs)
            processes.append(process)
            stop.set()
            return process

        enqueue(self.store, "task")
        with patch("agent_workflow.daemon.subprocess.Popen", side_effect=fake):
            run_daemon(self.home, stop, once=True)
        self.assertEqual(list_jobs(self.store)[0]["status"], "interrupted")
        self.assertIsNotNone(processes[0].poll())

    def test_release_success_and_empty_once(self):
        original = subprocess.Popen

        def fake(command, **kwargs):
            self.assertIn("release", command)
            self.assertNotIn("--repairs", command)
            return original([sys.executable, "-c", "pass"], **kwargs)

        enqueue(self.store, "task", action="release")
        with patch("agent_workflow.daemon.subprocess.Popen", side_effect=fake):
            run_daemon(self.home, threading.Event(), once=True)
        self.assertEqual(list_jobs(self.store)[0]["status"], "succeeded")
        self.assertEqual(run_daemon(self.home, threading.Event(), once=True), 0)


if __name__ == "__main__":
    unittest.main()
