import unittest
from unittest.mock import patch

from evaluate import ROOT, operation_trial, score_choice, summarize
from scenarios import interrupted


class OutcomeTests(unittest.TestCase):
    def marks(self, task, op, use=False):
        return score_choice({"operation": op, "task_id": task, "uses_pass_as_current": use},
                            "A", "run_checks", {"A": True, "B": False}, {"A","B"})

    def test_history_is_not_reliance_but_implicit_or_explicit_use_is(self):
        self.assertTrue(self.marks("A", "run_checks")["eligible_action"])
        self.assertFalse(self.marks("A", "inspect_task")["stale_use"])
        self.assertTrue(self.marks("A", "independent_review")["stale_use"])
        self.assertFalse(self.marks("A", "run_checks", True)["eligible_action"])

    def test_lookup_and_reexplanation_are_not_wrong_task_actions(self):
        self.assertTrue(self.marks("B", "run_checks")["wrong_task"])
        self.assertFalse(self.marks("B", "inspect_task")["wrong_task"])
        self.assertTrue(self.marks("", "ask_operator")["reexplanation"])

    def test_unavailable_observations_are_not_zero_time_or_zero_mistakes(self):
        a = {"arm":"classic", "outcome":"started", "decisions":[{"choice":{}, "elapsed_ms":15, "calls":1}],
             "wrong_task_choices":1, "stale_evidence_uses":1, "reexplanations":1, "lookups":0,
             "first_correct_ms":43}
        b = {"arm":"structured", "outcome":"unavailable", "decisions":[{"error":"timeout", "elapsed_ms":300, "calls":1}],
             "first_correct_ms":None}
        out = summarize([{"trials":[a,b]}])
        self.assertEqual(out["paired_starts"], 0)
        self.assertEqual(out["arms"]["classic"]["observed_starts_median_ms"], 43)
        self.assertEqual(out["arms"]["classic"]["wrong_task_choices"], 1)
        self.assertEqual(out["arms"]["structured"]["model_elapsed_ms"], 300)
        self.assertIsNone(out["arms"]["structured"]["reexplanations"])
        self.assertIsNone(out["paired_median_change_ms"])

    def test_bad_choices_are_measured_before_the_real_check_starts(self):
        def choice(task, operation, use=False):
            return {"task_id": task, "operation": operation, "uses_pass_as_current": use, "reason": "calibration"}

        with interrupted("stale_check") as (fixture, home, target, restore):
            for sequence, expected in [
                ([choice("P2","run_checks"), choice(target,"ask_operator"), choice(target,"run_checks")], (1,1,0)),
                ([choice(target,"independent_review"), choice(target,"run_checks")], (0,0,1)),
            ]:
                restore()
                previous = fixture.log.read_text().splitlines()
                with patch("evaluate.ask_json", side_effect=sequence):
                    trial = operation_trial("stale_check", "classic", fixture, home, target, "live",
                                            10, ROOT, ROOT, {target: True, "P2": False})
                self.assertEqual(trial["outcome"], "started")
                self.assertEqual(trial["effect"]["exit"], 0)
                self.assertEqual((trial["wrong_task_choices"], trial["reexplanations"], trial["stale_evidence_uses"]), expected)
                self.assertGreaterEqual(trial["first_correct_ms"], trial["pickup_ms"])
                self.assertEqual(fixture.log.read_text().splitlines(), previous + ["check"])
            restore()
            invalid = choice(target, ["run_checks"])
            with patch("evaluate.ask_json", return_value=invalid):
                trial = operation_trial("stale_check", "classic", fixture, home, target, "live",
                                        10, ROOT, ROOT, {target: True, "P2": False})
            self.assertEqual(trial["outcome"], "unavailable")
            self.assertIsNone(trial["first_correct_ms"])
