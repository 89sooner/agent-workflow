from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from agent_workflow import resume_metrics as metrics
from agent_workflow import session_notes
from agent_workflow.core import Store, WorkflowError, create_task


class ResumeMetricsTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.project, self.home = self.root / "repo", self.root / "ledger"
        subprocess.run(["git", "init", "-qb", "main", self.project], check=True)
        self.git("-c", "user.name=Fixture", "-c", "user.email=dev@example.invalid",
                 "commit", "--allow-empty", "-qm", "base")

    def git(self, *args):
        subprocess.run(["git", "-C", self.project, *args], check=True, capture_output=True)

    def at(self, instant):
        return patch.object(metrics, "wall_ms", return_value=instant)

    def test_finish_current_without_copying_timer_id(self):
        with self.assertRaises(WorkflowError):
            metrics.finish(self.home, None, self.project)
        self.assertFalse(self.home.exists())
        timer = metrics.start_manual(self.home, self.project)
        metrics.start_close(self.home, self.project)
        self.assertEqual(metrics.finish(self.home, None, self.project)["id"], timer["id"])
        self.assertEqual(metrics.summary(self.home, self.project)["close"]["active"], 1)

    def test_manual_and_pickup_confirm_count_and_medians(self):
        report = metrics.summary(self.home, self.project)
        self.assertIsNone(report["median_saved_ms"])
        self.assertFalse(self.home.exists())
        self.assertNotIn("measurement", session_notes.pickup(self.home, self.project))
        self.assertFalse(self.home.exists())

        with self.at(1000):
            manual = metrics.start_manual(self.home, self.project)
            self.assertIsNone(manual["evidence"])
        with self.at(1040):
            again = metrics.start_manual(self.home, self.project)
            self.assertEqual(again["id"], manual["id"])
            with self.assertRaises(WorkflowError):
                session_notes.pickup(self.home, self.project, measure=True)
        with self.at(1100):
            row = metrics.finish(self.home, manual["id"], self.project, wrong_pick=True, reexplanations=2)
            self.assertEqual(row["elapsed_ms"], 100)
        with self.at(1200):
            self.assertEqual(metrics.finish(self.home, row["id"], self.project, wrong_pick=True, reexplanations=2), row)
            with self.assertRaises(WorkflowError):
                metrics.finish(self.home, row["id"], self.project)
            timed = session_notes.pickup(self.home, self.project, measure=True)["measurement"]
            self.assertIsNone(timed["evidence"]["note"])
        with self.at(1250):
            self.assertEqual(session_notes.pickup(self.home, self.project, measure=True)["measurement"]["id"], timed["id"])
            self.assertEqual(metrics.finish(self.home, timed["id"], self.project)["elapsed_ms"], 50)
            another = metrics.start_manual(self.home, self.project)
        with self.at(1450):
            metrics.finish(self.home, another["id"], self.project)
        report = metrics.summary(self.home, self.project)
        self.assertEqual(report["manual"]["median_elapsed_ms"], 150)
        self.assertEqual(report["manual"]["wrong_picks"], 1)
        self.assertEqual(report["manual"]["reexplanations"], 2)
        self.assertEqual(report["median_saved_ms"], 100)
        self.assertEqual(report["active"], [])

    def test_close_authoring_tool_time_export_failure_and_deduplication(self):
        with self.at(1000):
            draft = metrics.start_close(self.home, self.project)
        with self.at(2500):
            with self.assertRaisesRegex(WorkflowError, "내보내기 실패"):
                session_notes.close_note(self.home, self.project, "done", "go", self.root / "missing")
        report = metrics.summary(self.home, self.project)
        self.assertEqual(report["close"]["median_elapsed_ms"], 1500)
        self.assertEqual(report["close"]["tool_count"], 1)
        self.assertIsNotNone(report["close"]["median_tool_ms"])
        with self.assertRaises(WorkflowError):
            metrics.abandon(self.home, draft["id"])
        with self.at(3000):
            retry = session_notes.close_note(self.home, self.project, "done", "go")
        self.assertFalse(retry["saved"])
        self.assertEqual(metrics.summary(self.home, self.project)["close"]["tool_count"], 1)
        with self.at(4000):
            second = session_notes.close_note(self.home, self.project, "changed", "go")
        self.assertTrue(second["saved"])
        self.assertEqual(metrics.summary(self.home, self.project)["close"]["tool_count"], 2)
        self.assertEqual(metrics.summary(self.home, self.project)["close"]["finished"], 1)

    def test_pickup_binds_snapshot_to_note_and_task_without_contents(self):
        secret = "fixture-prompt-should-not-be-retained-in-measurements"
        store = Store(self.home)
        try:
            create_task(store, "TASK", self.project, secret, ["criterion"], [], [], "codex", "none", "local")
        finally:
            store.close()
        session_notes.close_note(self.home, self.project, secret, "next")
        with self.at(1000):
            row = session_notes.pickup(self.home, self.project, measure=True)["measurement"]
        self.assertEqual(row["evidence"]["tasks"][0]["id"], "TASK")
        self.assertEqual(row["evidence"]["note"], "000000000001")
        self.assertNotIn(secret, repr(row))
        db = self.home / "session-notes" / "measurements.sqlite3"
        self.assertEqual(db.stat().st_mode & 0o777, 0o600)
        self.assertNotIn(secret.encode(), db.read_bytes())
        (self.project / "new-file").touch()
        with self.assertRaisesRegex(WorkflowError, "근거가 바뀌"):
            session_notes.pickup(self.home, self.project, measure=True)
        with self.at(1100):
            self.assertEqual(metrics.abandon(self.home, row["id"]), metrics.abandon(self.home, row["id"]))
        self.assertEqual(metrics.summary(self.home, self.project)["pickup"]["abandoned"], 1)
        with self.assertRaises(WorkflowError):
            metrics.finish(self.home, row["id"], self.project)

    def test_branch_scope_clock_rollback_and_concurrency(self):
        def start(_):
            return metrics.start_manual(self.home, self.project)["id"]
        with self.at(1000), ThreadPoolExecutor(max_workers=4) as pool:
            ids = list(pool.map(start, range(8)))
        self.assertEqual(len(set(ids)), 1)
        ident = ids[0]
        with self.at(900), self.assertRaises(WorkflowError):
            metrics.finish(self.home, ident, self.project)
        with self.assertRaises(WorkflowError):
            metrics.finish(self.home, ident, self.project, reexplanations=-1)
        self.git("switch", "-qc", "topic")
        self.assertEqual(metrics.summary(self.home, self.project)["active"], [])
        with self.assertRaises(WorkflowError):
            metrics.finish(self.home, ident, self.project)
        with self.at(1200):
            fixed = metrics.finish(self.home, ident, self.project, wrong_pick=True)
        self.assertEqual(fixed["ref"], "main")
        self.assertEqual(metrics.summary(self.home, self.project)["manual"]["finished"], 0)
        self.git("switch", "--detach")
        self.assertTrue(metrics.start_manual(self.home, self.project)["ref"].startswith("detached:"))

    def test_report_during_first_write_has_no_partial_samples(self):
        folder = self.home / "session-notes"
        folder.mkdir(parents=True)
        with sqlite3.connect(folder / "measurements.sqlite3") as writer:
            writer.execute("BEGIN IMMEDIATE")
            writer.execute("CREATE TABLE timers(id TEXT)")
            self.assertEqual(metrics.summary(self.home, self.project)["manual"]["finished"], 0)
            writer.rollback()

    def test_metrics_failure_keeps_note_and_recoverable_duration(self):
        with self.at(1000):
            metrics.start_close(self.home, self.project)
        with self.at(1500), patch.object(metrics, "record_close", side_effect=WorkflowError("offline")):
            note = session_notes.close_note(self.home, self.project, "done", "next")
        self.assertEqual(note["measurement_error"], "offline")
        self.assertEqual(session_notes.pickup(self.home, self.project)["summary"], "done")
        with self.at(2500):
            retried = session_notes.close_note(self.home, self.project, "done", "next")
        self.assertEqual(retried["authoring_measurement"]["elapsed_ms"], 500)
        self.assertEqual(metrics.summary(self.home, self.project)["close"]["tool_count"], 1)


if __name__ == "__main__":
    unittest.main()
