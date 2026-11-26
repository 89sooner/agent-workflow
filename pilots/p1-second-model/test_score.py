from contextlib import closing
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from fixture import PROFILE, Store, fixture_environment, prepare, profiles
from score import _raw_block, summarize


def decision(outcome, elapsed, finding=""):
    missing = outcome in (None, "blocked")
    return {
        "outcome": outcome, "status": "incomplete" if missing else "complete",
        "error": "timeout" if outcome is None else None, "elapsed_ms": elapsed, "calls": 1,
        # The historical label was clean even for sample b.
        "score": {"detected": False, "false_block": None if missing else outcome == "changes_requested"},
        "findings": [{"blocking": True, "detail": finding, "evidence": "pager.py"}] if finding else [],
    }


def row(case, blind, informed):
    check = {"detected_regressions": [], "command_ids": ["C"], "model_ms": 2, "model_calls": 1,
             "selected_check_ms": 1}
    return {"case": case, "defect": None,
            "checks": {"status": "complete", "commands": [{"id": "C", "classification": "passing"}],
                       "arms": {"primary": check, "combined": check},
                       "model_calls": {"primary": {"calls": 1, "elapsed_ms": 1}, "review": {"calls": 1, "elapsed_ms": 1}},
                       "delta": {"additional_regressions": [], "additional_model_ms": 1,
                                 "additional_selected_check_ms": 0, "additional_model_calls": 1}},
            "review": {"arms": {"blind": blind, "with_report": informed},
                       "pair_delta": {"elapsed_ms": informed["elapsed_ms"] - blind["elapsed_ms"], "calls": 0}}}


class StudyScoringTests(unittest.TestCase):
    def test_fixture_registers_its_own_repo_when_invoked_from_a_git_hook(self):
        with tempfile.TemporaryDirectory() as temp:
            other = Path(temp) / "unrelated"
            pilot = Path(temp) / "pilot"
            subprocess.run(["git", "init", "-q", str(other)], env=fixture_environment(), check=True)
            with patch.dict(os.environ, GIT_DIR=str(other / ".git"), GIT_WORK_TREE=str(other)):
                prepare(pilot)
            with closing(Store(pilot / "ledger", read_only=True)) as store:
                self.assertEqual(profiles.get(store, PROFILE)["config"]["project"], str(pilot / "repository"))

    def test_review_eligibility_uses_actual_selected_plan_in_v2(self):
        checks = {"version": 2, "commands": [{"id": "C", "classification": "regression"}]}
        arm = {"command_ids": ["C"], "detected_regressions": [], "selected_preexisting_failures": []}
        self.assertFalse(_raw_block(checks, arm))
        arm["selected_preexisting_failures"] = ["C"]
        self.assertTrue(_raw_block(checks, arm))

    def test_partial_pair_retains_completed_verdict_and_spent_timeout(self):
        b = row("b", decision("changes_requested", 5, "page([], -1, None) raises TypeError instead of ValueError."),
                decision(None, 90))
        d = row("d", decision("changes_requested", 6, "impossible cursor"), decision("pass", 7))
        outcomes = {"base": "ValueError", "b": "TypeError", "d": "ValueError"}
        report = {"cases": [b, d], "oracle": {k: {"mixed_exception": v} for k, v in outcomes.items()}}
        result = summarize(report)
        self.assertEqual(result["adjudication"]["disputed_original_controls"], ["b"])
        reviews = result["review"]
        self.assertEqual(reviews["paired_case_ids"], ["d"])
        self.assertEqual(reviews["arms"]["blind"]["supplemental_detected"], ["b"])
        self.assertEqual(reviews["arms"]["blind"]["clean_changes_falsely_blocked"], {"count": 1, "eligible": 1})
        self.assertEqual(reviews["arms"]["blind"]["on_top_of_combined_checks"]["additional_defective_change_ids"], ["b"])
        self.assertEqual(reviews["arms"]["with_report"]["model_ms"], 97)
        self.assertEqual(result["observed_transport_totals"], {"ms": 112, "calls": 8})
        self.assertEqual(reviews["with_report_minus_blind_ms"], 1)
        b["review"]["arms"]["blind"]["findings"][0]["detail"] = "ordinary page loses its cursor"
        unr = summarize(report)["review"]["arms"]["blind"]
        self.assertEqual(unr["supplemental_detected"], [])
        self.assertEqual(unr["unmatched_supplemental_blocks"], ["b"])

    def test_clean_false_block_retained_when_other_arm_has_no_verdict(self):
        d = row("d", decision("changes_requested", 3, "ordinary page loses its cursor"), decision("blocked", 4))
        report = {"cases": [d], "oracle": {s: {"mixed_exception": "ValueError"} for s in ("base", "d")}}
        reviews = summarize(report)["review"]
        self.assertEqual(reviews["paired_case_ids"], [])
        self.assertEqual(reviews["arms"]["blind"]["clean_changes_falsely_blocked"], {"count": 1, "eligible": 1})
        self.assertEqual(reviews["inconclusive"][0]["reason"], "reviewer_blocked")
