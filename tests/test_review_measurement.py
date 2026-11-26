import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from agent_workflow import profiles
from agent_workflow.check_advisor_git import change_context, resolve_commit
from agent_workflow.core import Store, WorkflowError, git
from agent_workflow.review_measurement import measure, _score


LABEL = {"path": "pager.py", "cues": [["end of list", "past-end"], ["returns 0", "returns zero"]]}
REPORT = "Reorganized the index logic and kept the original behavior."
FINDING = {"blocking": True, "detail": "At the end of list this returns zero", "evidence": "`pager.py:2`"}


def reply(outcome, findings=()):
    return {"outcome": outcome, "findings": list(findings), "summary": "Reviewed the change.", "next_action": "Inspect result."}


class ReviewMeasurementTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        git(self.repo, "init", "-qb", "main")
        self.file = self.repo / "pager.py"
        self.file.write_text("def cursor():\n    return None\n")
        self.base = self.commit()
        self.file.write_text("def cursor():\n    return 0\n")
        self.candidate = self.commit()
        self.file.write_text("DIRTY_NOT_MODEL_INPUT")
        (self.repo / "untracked").write_text("UNTRACKED_NOT_MODEL_INPUT")
        self.home = self.root / "ledger"
        store = Store(self.home)
        self.addCleanup(store.close)
        profiles.save(store, "pager", {"project": str(self.repo), "checks": []})
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.calls = self.root / "calls"
        env = patch.dict(os.environ, {"PATH": str(self.bin) + os.pathsep + os.environ["PATH"]})
        env.start()
        self.addCleanup(env.stop)

    def commit(self):
        git(self.repo, "add", ".")
        git(self.repo, "-c", "user.name=Test", "-c", "user.email=a@example.invalid", "commit", "-qm", "fixture")
        return git(self.repo, "rev-parse", "HEAD")

    def double(self, blind, informed):
        cli = self.bin / "claude"
        cli.write_text(f"""#!{sys.executable}
import json, os, sys
args = sys.argv
assert '--no-session-persistence' in args and args[args.index('--tools')+1] == ''
assert 'findings' in json.loads(args[args.index('--json-schema')+1])['properties']
packet = json.loads(sys.stdin.read())
with open({str(self.calls)!r}, 'a') as f:
    print(json.dumps([os.getcwd(), packet]), file=f)
answer = {informed!r} if 'implementation_report' in packet else {blind!r}
print(json.dumps({{'structured_output': answer, 'subtype': 'success', 'modelUsage': {{'claude-test': {{}}}}}}))
""")
        cli.chmod(0o700)

    def run_case(self, label=LABEL, **options):
        return measure(self.home, "pager", self.base, self.candidate, ["Preserve cursor behavior."], reviewer="claude",
                       expected_defect=label, implementation_report=REPORT, timeout=5, **options)

    def test_immutable_diff_and_blinded_same_reviewer_pair(self):
        self.double(reply("changes_requested", [FINDING]), reply("pass"))
        rule = {**LABEL, "cues": [*LABEL["cues"][:1], [*LABEL["cues"][1], "secret_grader_alternative"]]}
        result = self.run_case(rule, blind_first=False)
        self.assertEqual(result["status"], "complete")
        calls = [json.loads(line) for line in self.calls.read_text().splitlines()]
        self.assertEqual(len(calls), 2)
        self.assertNotEqual(calls[0][0], calls[1][0])
        self.assertFalse(Path(calls[0][0]).exists())
        informed, blind = calls[0][1], calls[1][1]
        self.assertEqual(informed.pop("implementation_report"), REPORT)
        self.assertEqual(informed, blind)
        self.assertNotIn("secret_grader_alternative", self.calls.read_text())
        self.assertNotIn("NOT_MODEL_INPUT", self.calls.read_text())
        self.assertIn("+    return 0", blind["change"]["diff"])
        self.assertEqual(blind["change"]["candidate"], self.candidate)
        self.assertEqual(result["no_review"]["calls"], 0)
        self.assertTrue(result["no_review"]["score"]["missed"])
        self.assertEqual(result["pair_delta"]["detected"], -1)
        self.assertTrue(result["arms"]["blind"]["score"]["detected"])
        for arm in result["arms"].values():
            self.assertEqual(arm["calls"], 1)
            self.assertEqual(arm["models_reported"], ["claude-test"])
            self.assertGreater(arm["elapsed_ms"], 0)

    def test_clean_control_and_indeterminate_reply(self):
        self.double(reply("blocked"), reply("changes_requested", [FINDING]))
        result = self.run_case(label=None)
        self.assertTrue(result["arms"]["with_report"]["score"]["false_block"])
        self.assertIsNone(result["arms"]["blind"]["score"]["false_block"])
        self.assertTrue(result["arms"]["blind"]["score"]["inconclusive"])
        self.assertIsNone(result["pair_delta"]["false_block"])
        self.assertEqual(result["status"], "incomplete")

    def test_partial_or_wrong_block_never_counts_as_known_defect(self):
        for findings in ([], [{**FINDING, "evidence": "`my_pager.py`"}], [{**FINDING, "blocking": False}],
                         [{**FINDING, "detail": "End of list"}, {**FINDING, "detail": "Returns zero"}]):
            with self.subTest(findings=findings):
                score = _score(reply("changes_requested", findings), LABEL)
                self.assertTrue(score["unmatched_block"])
                self.assertFalse(score["detected"])
                self.assertTrue(score["missed"])

    def test_no_credit_for_transport_failure_or_zero_launch(self):
        self.double(reply("pass"), {})
        result = self.run_case()
        failed = result["arms"]["with_report"]
        self.assertEqual(failed["calls"], 1)
        self.assertTrue(failed["score"]["inconclusive"])
        self.assertIsNone(result["pair_delta"]["detected"])
        with patch("agent_workflow.check_advisor_models.shutil.which", return_value=None):
            failed = self.run_case()["arms"]["blind"]
        self.assertEqual(failed["calls"], 0)
        self.assertIsNone(failed["score"]["missed"])

    def test_redacted_or_truncated_diff_is_not_scored(self):
        self.file.write_text("def cursor():\n    password = 'test-secret'\n")
        self.candidate = self.commit()
        result = self.run_case()
        self.assertEqual(result["status"], "incomplete")
        self.assertEqual(result["arms"]["blind"]["calls"], 0)
        self.assertNotIn("test-secret", str(result))
        self.file.write_text("#" * 10_000)
        self.candidate = self.commit()
        self.assertTrue(change_context(self.repo, self.base, candidate=self.candidate)["truncated"])
        self.assertIsNone(self.run_case()["arms"]["blind"]["score"]["detected"])
        self.assertFalse(self.calls.exists())

    def test_requires_local_commits_and_explicit_ground_truth(self):
        for label in ({}, {"path": "../pager.py", "cues": [["x"], ["y"]]}, {"path": "pager.py", "cues": [["x"]]}):
            with self.subTest(label=label), self.assertRaises(WorkflowError):
                self.run_case(label)
        with self.assertRaises(WorkflowError):
            resolve_commit(self.repo, "--invalid")
        with self.assertRaises(WorkflowError):
            change_context(self.repo, candidate=self.candidate)
        self.assertEqual(resolve_commit(self.repo, "HEAD~"), self.base)

    def test_redacted_report_or_criterion_cannot_produce_a_complete_comparison(self):
        self.double(reply("pass"), reply("pass"))
        def run(criteria, report):
            return measure(self.home, "pager", self.base, self.candidate, criteria, reviewer="claude",
                           implementation_report=report, expected_defect=LABEL, timeout=5)
        redacted_report = run(["Preserve cursor behavior."], "password=synthetic")
        self.assertEqual(redacted_report["status"], "incomplete")
        self.assertEqual(redacted_report["arms"]["blind"]["calls"], 1)
        self.assertEqual(redacted_report["arms"]["with_report"]["calls"], 0)
        redacted_criteria = run(["password=synthetic"], REPORT)
        self.assertTrue(all(arm["calls"] == 0 for arm in redacted_criteria["arms"].values()))
        self.assertNotIn("synthetic", self.calls.read_text())
