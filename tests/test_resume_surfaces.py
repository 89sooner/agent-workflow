import json
from pathlib import Path
import sqlite3
import unittest
from unittest.mock import patch

import test_workflow as fixtures
from agent_workflow import cli, resume_metrics, session_notes
from agent_workflow.closure import finalize
from agent_workflow.core import Store, WorkflowError, complete, create_task, encode, set_hold
from agent_workflow.connections import connect
from agent_workflow.runner import brief


class ResumeSurfacesTests(unittest.TestCase):
    setUp = fixtures.WorkflowTests.setUp
    tearDown = fixtures.WorkflowTests.tearDown
    task = fixtures.WorkflowTests.task
    checked = fixtures.WorkflowTests.checked

    def read(self, *args):
        return cli.dispatch(cli.parser().parse_args(["--home", str(self.store.home), *args]))

    def test_archived_task_never_uses_the_checkout_recreated_at_its_path(self):
        from agent_workflow.core import snapshot
        task = self.checked(self.task())
        complete(self.store, task)
        task["workspace"] = {"removed": True, "last_snapshot": snapshot(self.project)}
        self.store.save(task, "workspace_removed")
        record = self.read("show", task["id"])
        self.assertTrue(record["observed_git"]["archived"])
        self.assertFalse(record.get("completion_is_current"))
        row, = self.read("pickup", "--project", str(self.project))["registered_tasks"]
        self.assertEqual(row["resume_packet"], record["resume_packet"])
        self.assertEqual(row["evidence"]["completion"], "unavailable")
        self.assertFalse(row["resume_packet"]["valid_evidence"])

    def test_current_then_stale_packet_matches_files_cli_model_and_pickup(self):
        task = self.checked(self.task(reviewer="codex"))
        for expected in ("review", "recheck"):
            with self.subTest(expected=expected):
                packet = self.read("resume-view", task["id"])
                self.assertEqual(packet["next_action"]["action"], expected)
                self.assertEqual(packet, self.read("show", task["id"])["resume_packet"])
                row, = self.read("pickup", "--project", str(self.project))["registered_tasks"]
                self.assertEqual(row["resume_packet"], packet)
                self.assertIn(encode(packet), self.read("brief", task["id"]))
                self.assertIn(encode(packet), brief(task, "review", state_home=self.store.home))
                folder = Path(self.read("handoff", task["id"])["directory"])
                self.assertIn(encode(packet), (folder / "resume.txt").read_text())
                self.assertEqual(packet, json.loads((folder / "state.json").read_text())["resume_packet"])
                self.assertIn(packet["next_action"]["text"], (folder / "worklog.md").read_text())
                self.assertEqual(bool(packet["valid_evidence"]), expected == "review")
                (self.project / "app.py").write_text("VALUE = 2\n")

    def test_same_id_in_connected_ledger_uses_its_goal_and_separate_hold(self):
        primary = self.task()
        other = self.root / "secondary ledger"
        store = Store(other)
        try:
            create_task(store, primary["id"], self.project, "다른 목표", ["별도 조건"], [],
                        [["true"]], "codex", "none", "this checkout")
            set_hold(store, primary["id"], "운영자 확인")
        finally:
            store.close()
        connect(self.store.home, self.project, [other], include_processes=False,
                include_temporary=False, include_default=False)
        rows = self.read("pickup", "--project", str(self.project))["registered_tasks"]
        by_ledger = {r["resume_packet"]["ledger"]: r["resume_packet"] for r in rows}
        self.assertEqual(by_ledger[str(other)]["next_action"]["action"], "held")
        self.assertEqual(by_ledger[str(other)]["goal"], "다른 목표")
        self.assertIn("운영자 확인", by_ledger[str(other)]["uncertainties"])
        self.assertEqual(by_ledger[str(self.store.home)], self.read("resume-view", primary["id"]))

    def test_finalized_export_matches_committed_closure_revision(self):
        task = self.checked(self.task())
        complete(self.store, task)
        result = finalize(self.store, task)
        saved = json.loads((Path(result["handoff"]) / "state.json").read_text())
        current = self.read("resume-view", task["id"])
        self.assertEqual(saved["resume_packet"], current)
        self.assertEqual(saved["task"]["revision"], self.store.get(task["id"])["revision"])

    def test_explicit_timer_comparison_preserves_unknown_and_counts(self):
        self.task()
        for classic, start, kwargs in [(True, 1000, {}), (False, 2000, {"wrong_picks": 2, "stale_evidence_used": True})]:
            with patch.object(resume_metrics, "wall_ms", return_value=start):
                timer = session_notes.pickup(self.store.home, self.project, measure=True, classic=classic)["measurement"]
            with patch.object(resume_metrics, "wall_ms", return_value=start + 100):
                for _ in range(2):
                    resume_metrics.finish(self.store.home, timer["id"], self.project, reexplanations=1, **kwargs)
        variants = resume_metrics.summary(self.store.home, self.project)["pickup_variants"]
        self.assertIsNone(variants["classic"]["stale_evidence_used"])
        self.assertEqual(variants["structured"]["stale_evidence_reported"], 1)
        self.assertEqual(variants["structured"]["wrong_picks"], 2)
        self.assertEqual(variants["structured"]["stale_evidence_used"], 1)
        with self.assertRaises(WorkflowError):
            resume_metrics.finish(self.store.home, timer["id"], self.project, wrong_picks=2,
                                  reexplanations=1, stale_evidence_used=False)

    def test_legacy_timer_reads_without_migration_and_finishes_with_explicit_feedback(self):
        timer = session_notes.pickup(self.store.home, self.project, classic=True, measure=True)["measurement"]
        path = self.store.home / "session-notes" / "measurements.sqlite3"
        with sqlite3.connect(path) as db:
            db.execute("ALTER TABLE timers DROP COLUMN stale_evidence_used")
        before = path.read_bytes()
        self.assertEqual(resume_metrics.summary(self.store.home, self.project)["pickup_variants"]["classic"]["active"], 1)
        self.assertEqual(path.read_bytes(), before)
        self.read("resume-time", "finish", timer["id"], "--project", str(self.project),
                  "--wrong-picks", "2", "--reexplanations", "1", "--no-stale-evidence")
        row = resume_metrics.summary(self.store.home, self.project)["pickup_variants"]["classic"]
        self.assertEqual((row["wrong_picks"], row["stale_evidence_reported"], row["stale_evidence_used"]), (2, 1, 0))
