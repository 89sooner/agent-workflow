import shlex
import sys
import unittest
from unittest.mock import patch

import test_workflow as fixtures
from agent_workflow import connections, profiles, recommendation_records as records
from agent_workflow.check_advisor import recommend
from agent_workflow.cli import dispatch, parser
from agent_workflow.core import WorkflowError, git
from agent_workflow.runner import run_checks
from agent_workflow.workspaces import create_from_profile


class RecordTests(unittest.TestCase):
    def setUp(self):
        fixtures.WorkflowTests.setUp(self)
        self.base = [sys.executable, "-c", "print('base')"]
        self.extra = [sys.executable, "-c", "raise SystemExit(17)"]
        self.cfg = {"project": str(self.project), "checks": [self.base], "workspace": {"isolate": False},
                    "check_groups": {"data": [self.base], "ui": [self.base, self.extra]}, "reviewer": "none"}
        self.criteria = [" private criterion "]
        self.profile = profiles.save(self.store, "demo", self.cfg)

    tearDown = fixtures.WorkflowTests.tearDown

    def record(self, project=None):
        with patch("agent_workflow.check_advisor_models.ask",
                   side_effect=[{"groups": ["data"], "reason": "private reason"},
                                {"groups": ["ui"], "reason": "private reason"}]):
            report = recommend(self.store.home, "demo", self.criteria, project=project,
                               reviewer="claude", record_result=True)
        return report["recommendation_id"]

    def create(self, tid, rid, **kwargs):
        return create_from_profile(self.store, tid, "demo", "goal", self.criteria, recommendation_id=rid, **kwargs)

    def test_recent_lists_current_profile_receipts_in_reverse_insertion_order(self):
        ids = [self.record() for _ in range(14)]
        profiles.save(self.store, "other", self.cfg)
        recent = records.recent(self.store, "demo")
        rows = recent["recommendations"]
        self.assertEqual([row["id"] for row in rows], ids[-12:][::-1])
        self.assertEqual(set(rows[0]), {"id", "created_at", "profile_digest", "suggested_groups"})
        self.assertEqual(rows[0]["suggested_groups"], ["data", "ui"])
        self.assertEqual(rows[0]["profile_digest"], self.profile["digest"])
        self.assertEqual(records.recent(self.store, "other")["recommendations"], [])
        profiles.save(self.store, "demo", {**self.cfg, "checks":[]})
        self.assertEqual(records.recent(self.store, "demo")["recommendations"], [])

    def test_adoption_feedback_and_latest_executed_costs(self):
        rid = self.record()
        task = self.create("TASK", rid)
        self.assertEqual(task["checks"], [self.base, self.extra])
        self.assertEqual(task["profile"]["check_groups"], ["data", "ui"])
        self.assertEqual(task["profile"]["recommendation_id"], rid)
        self.assertIsNone(records.task_summary(self.store, task)["expected_groups"])
        self.assertEqual(records.task_summary(self.store, task)["checks_by_group"]["ui"]["seconds"], None)
        with self.store.lock(task["project"]) as fd:
            self.assertFalse(run_checks(self.store, task, 5, fd))
        records.feedback(self.store.home, "TASK", ["data"])
        stats = records.summary(self.store.home, "demo")
        row, = stats["tasks"]
        self.assertEqual(row["checks_by_group"]["ui"]["failed_count"], 1)
        self.assertEqual(row["checks_by_group"]["ui"]["passed_count"], 1)
        self.assertEqual(row["unnecessary_review_check_seconds"], task["verification"]["results"][1]["seconds"])
        self.assertEqual(row["expected_groups"], ["data"])
        self.assertEqual(stats["labeled_tasks"], 1)
        self.assertEqual(stats["quality"]["extra_groups"], 1)
        self.assertEqual(stats["quality"]["rescued_groups"], 0)
        self.assertEqual(stats["quality"]["observed_timing_tasks"], 1)
        self.assertEqual(stats["quality"]["unnecessary_review_check_seconds"], row["unnecessary_review_check_seconds"])
        self.assertIsNotNone(stats["quality"]["median_review_ms"])
        self.assertEqual(records.summary(self.store.home, "other")["recommendations"], 0)
        task["verification"] = None
        self.store.save(task, "checks_started")
        historical = records.task_summary(self.store, task)
        self.assertEqual(historical["checks_by_group"], row["checks_by_group"])
        self.assertEqual(historical["verification_at"], row["verification_at"])
        raw = self.store.db.execute("SELECT data FROM check_recommendations").fetchone()[0]
        for secret in ("private reason", "private criterion", "SystemExit", "diff", "VALUE = 1"):
            self.assertNotIn(secret, raw)
        self.assertNotIn("worktree", records.show(self.store.home, rid))
        with self.assertRaises(WorkflowError):
            records.feedback(self.store.home, "TASK", ["bogus"])

    def test_late_check_add_duplicate_and_repeated_baseline_have_separate_occurrences(self):
        self.cfg["checks"].append(self.base)
        self.profile = profiles.save(self.store, "demo", self.cfg)
        rid = self.record()
        self.create("TASK", rid)
        dispatch(parser().parse_args(["--home", str(self.store.home), "check-add", "TASK", shlex.join(self.extra)]))
        task = self.store.get("TASK")
        self.assertEqual(task["checks"], [self.base, self.base, self.extra, self.extra])
        with self.store.lock(task["project"]) as fd:
            self.assertFalse(run_checks(self.store, task, 5, fd))
        records.feedback(self.store.home, "TASK", ["data"])
        actual = task["verification"]["results"]
        detail = records.task_summary(self.store, task)
        self.assertEqual(detail["checks_by_group"]["data"]["executed_count"], 1)
        self.assertEqual(detail["checks_by_group"]["data"]["seconds"], actual[0]["seconds"])
        self.assertEqual(detail["checks_by_group"]["ui"]["executed_count"], 2)
        self.assertEqual(detail["checks_by_group"]["ui"]["failed_count"], 1)
        self.assertEqual(detail["unnecessary_review_check_seconds"], actual[2]["seconds"])

    def test_reuse_overrides_conflicting_labels_and_deduplication(self):
        rid = self.record()
        self.create("A", rid, check_groups=[])
        self.create("B", rid, check_groups=["ui"])
        self.assertEqual(self.store.get("A")["checks"], [self.base])
        records.feedback(self.store.home, "A", ["ui"])
        records.feedback(self.store.home, "B", ["ui"])
        def samples():
            return records.routing_samples(self.store.home, self.profile["digest"], [], "codex", "claude", profile="demo")
        self.assertEqual(len(samples()), 1)
        self.assertEqual(samples()[0]["expected"], ["ui"])
        stats = records.summary(self.store.home)
        self.assertEqual(stats["linked_recommendations"], 1)
        self.assertEqual(stats["quality"]["paired_labeled_tasks"], 2)
        self.assertEqual(stats["quality"]["distinct_paired_inputs"], 1)
        self.assertEqual(stats["quality"]["rescued_groups"], 1)
        self.assertEqual(stats["quality"]["unnecessary_review_check_seconds"], None)
        records.feedback(self.store.home, "B", [])
        self.assertEqual(samples(), [])
        self.assertEqual(records.summary(self.store.home)["quality"]["conflicting_inputs"], 1)
        self.assertEqual(records.summary(self.store.home)["quality"]["distinct_paired_inputs"], 0)
        self.assertEqual(records.task_summary(self.store, self.store.get("A"))["expected_groups"], ["ui"])

    def test_input_profile_and_isolation_guards(self):
        path = self.project / "app.py"
        path.write_text("VALUE = 2\n")
        rid = self.record()
        path.write_text("VALUE = 3\n")
        with self.assertRaisesRegex(WorkflowError, "변경 내용"):
            self.create("STALE", rid)
        self.assertFalse((self.store.home / "tasks" / "STALE").exists())
        path.write_text("VALUE = 2\n")
        with self.assertRaisesRegex(WorkflowError, "미커밋"):
            self.create("COPY", rid, isolate=True)
        self.assertNotIn("workflow/COPY", git(self.project, "branch", "--list"))
        self.assertFalse((self.store.home / "tasks" / "COPY").exists())
        self.assertFalse((self.store.home / "workspaces").exists())
        self.criteria = ["new criterion"]
        with self.assertRaisesRegex(WorkflowError, "완료 조건"):
            self.create("OTHER", rid)
        profiles.save(self.store, "demo", {**self.cfg, "checks": []})
        with self.assertRaisesRegex(WorkflowError, "프로필"):
            self.create("CHANGED", rid)

    def test_start_uses_target_ledger_and_correct_worktree(self):
        other = self.root / "linked"
        git(self.project, "worktree", "add", "-q", "-b", "linked", str(other))
        rid = self.record(other)
        observer = self.root / "observer"
        connections.connect(observer, self.project, [self.store.home], profile="demo", criteria=self.criteria,
                            include_temporary=False, include_processes=False, include_default=False)
        with self.assertRaisesRegex(WorkflowError, "작업 트리"):
            connections.start_registered(observer, "goal", self.project, recommendation_id=rid)
        result = connections.start_registered(observer, "goal", other, recommendation_id=rid, isolate=False)
        self.assertEqual(result["task"]["project"], str(other))
        self.assertEqual(result["task"]["profile"]["recommendation_id"], rid)
        self.assertEqual(records.summary(observer)["recommendations"], 0)

    def test_read_only_legacy_ledger_and_unknown_receipt(self):
        self.assertFalse(records._has(self.store.db))
        self.assertEqual(records.summary(self.store.home)["tasks"], [])
        self.assertEqual(records.routing_samples(self.store.home, self.profile["digest"], [], "codex", "claude"), [])
        with self.assertRaisesRegex(WorkflowError, "원장"):
            self.create("MISSING", "REC-not-found")
        self.assertFalse(records._has(self.store.db))
