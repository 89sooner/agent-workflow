from contextlib import closing
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from agent_workflow import profiles, reviewer_policy
from agent_workflow.check_advisor import recommend
from agent_workflow.core import Store
from agent_workflow.recommendation_records import feedback
from agent_workflow.workspaces import create_from_profile


def sample(*, added=(), expected=(), primary=(), latency=None):
    return {"primary": list(primary), "combined": list(primary) + list(added),
            "expected": list(expected), "review_ms": latency}


class ReviewerPolicyTests(unittest.TestCase):
    def decide(self, rows, areas=("engine",), *, clipped=False, primary="codex"):
        with patch.object(reviewer_policy, "routing_samples", return_value=rows) as fetch:
            decision = reviewer_policy.decide("ledger", "digest", areas, primary, clipped, profile="profile")
            if clipped:
                fetch.assert_not_called()
            else:
                fetch.assert_called_once_with("ledger", "digest", sorted(set(areas)), primary,
                                              "claude" if primary == "codex" else "codex", profile="profile")
        return decision

    def test_cold_start_and_sparse_data_keep_area_rule(self):
        rescues = [sample(added=["ui"], expected=["ui"])] * 4
        self.assertIsNone(self.decide(rescues)["reviewer"])
        self.assertEqual(self.decide([])["basis"], "insufficient_samples")
        self.assertEqual(self.decide([], ("src", "tests"), primary="claude")["reviewer"], "codex")

    def test_repeated_rescues_promote_single_area_and_measure_marginal_work(self):
        rescue = sample(added=["ui", "style"], expected=["ui", "style"], latency=20)
        rows = [rescue] * 3 + [sample(added=["ui"], latency=100), sample()]
        d = self.decide(rows)
        self.assertEqual((d["basis"], d["reviewer"]), ("measured_rescue", "claude"))
        self.assertEqual(d["evidence"], {"labeled_inputs":5, "rescued_inputs":3,
                                        "rescued_groups":6, "extra_groups":1, "unresolved_groups":0,
                                        "latency_samples":4, "median_review_ms":20})

    def test_mixed_or_costly_rescues_do_not_override_area_rule(self):
        rows = [sample(added=["ui", "unused"], expected=["ui"])] * 5
        self.assertIsNone(self.decide(rows)["reviewer"])
        rows = [sample(added=["ui"], expected=["ui"])] * 2 + [sample()] * 3
        self.assertEqual(self.decide(rows)["basis"], "default_areas")
        self.assertEqual(self.decide(rows, ("src", "web"))["reviewer"], "claude")

    def test_demote_only_when_primary_missed_nothing_and_input_is_complete(self):
        areas = ("src", "tests")
        known = sample(primary=["syntax"], expected=["syntax"], added=["ui"])
        d = self.decide([known] * 5, areas)
        self.assertEqual((d["basis"], d["reviewer"]), ("measured_no_gain", None))
        self.assertEqual(d["evidence"]["extra_groups"], 5)
        self.assertIsNone(d["evidence"]["median_review_ms"])
        d = self.decide([known] * 4 + [sample(expected=["missing"])], areas)
        self.assertEqual(d["reviewer"], "claude")
        self.assertEqual(d["evidence"]["unresolved_groups"], 1)
        d = self.decide([known] * 10, areas, clipped=True)
        self.assertEqual((d["basis"], d["reviewer"]), ("truncated", "claude"))
        self.assertEqual(d["evidence"]["labeled_inputs"], 0)

    def test_recent_window_discards_old_decisions(self):
        outdated = sample(added=["ui"], expected=["ui"], latency=1000)
        d = self.decide([outdated] * 50 + [sample(latency=5)] * 10, ("src", "tests"))
        self.assertIsNone(d["reviewer"])
        self.assertEqual(d["evidence"]["labeled_inputs"], 10)
        self.assertEqual(d["evidence"]["median_review_ms"], 5)

class MeasuredRoutingIntegrationTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.repo = Path(temp.name) / "repo"
        self.home = Path(temp.name) / "ledger"
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True, capture_output=True)
        for area in ("src", "tests"):
            (self.repo / area).mkdir()
            (self.repo / area / "value").write_text("initial\n")
        subprocess.run(["git", "add", "."], cwd=self.repo, check=True, capture_output=True)
        subprocess.run(["git", "-c", "user.email=tests@example.invalid", "-c", "user.name=Tests",
                        "commit", "-qm", "fixture"], cwd=self.repo, check=True, capture_output=True)
        config = {"project": str(self.repo), "workspace": {"isolate": False},
                  "checks": [["true"]], "check_groups": {"coverage": [["true", "coverage"]]}}
        with closing(Store(self.home)) as store:
            profiles.save(store, "project", config)
            profiles.save(store, "other-name", config)
        self.criteria = ["Verify changed behavior"]

    def preview(self, profile="project", **kwargs):
        return recommend(self.home, profile, self.criteria, preview=True, **kwargs)["providers"]

    def record(self, task, primary, second, expected):
        responses = [{"groups": primary, "reason": "primary"}, {"groups": second, "reason": "review"}]
        with patch("agent_workflow.check_advisor_models.ask", side_effect=responses):
            report = recommend(self.home, "project", self.criteria, reviewer="claude", record_result=True)
        with closing(Store(self.home)) as store:
            create_from_profile(store, task, "project", "Implement", self.criteria,
                                recommendation_id=report["recommendation_id"])
        if expected is not None:
            feedback(self.home, task, expected)

    def test_single_area_observed_rescues_enable_dual_for_this_cohort_only(self):
        for index in range(5):
            self.assertEqual(self.preview(), ["codex"])
            (self.repo / "src/value").write_text(f"{index}\n")
            self.record(f"OBS{index}", [], ["coverage"], ["coverage"])
        self.assertEqual(self.preview(), ["codex", "claude"])
        self.assertEqual(self.preview("other-name"), ["codex"])
        self.assertEqual(self.preview(provider="claude"), ["claude"])
        self.assertEqual(self.preview(reviewer="none"), ["codex"])
        # Contradictory labels for the same input remove support rather than counting twice.
        self.record("REPEAT", [], ["coverage"], [])
        self.assertEqual(self.preview(), ["codex"])

    def test_multi_area_demotion_requires_human_label_and_can_be_overridden(self):
        for index in range(5):
            for area in ("src", "tests"):
                (self.repo / area / "value").write_text(f"{index}\n")
            self.record(f"OBS{index}", ["coverage"], [], ["coverage"] if index < 4 else None)
        self.assertEqual(self.preview(), ["codex", "claude"])
        feedback(self.home, "OBS4", ["coverage"])
        self.assertEqual(self.preview(), ["codex"])
        self.assertEqual(self.preview(reviewer="claude"), ["codex", "claude"])
        (self.repo / "tests/value").write_text("initial\n")
        self.assertEqual(self.preview(), ["codex"])
