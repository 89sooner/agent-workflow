from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from agent_workflow.check_advisor import evaluate, recommend
from agent_workflow.core import Store, WorkflowError
from agent_workflow import profiles


class CheckAdvisorTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.project = self.root / "project"
        self.ledger = self.root / "ledger"
        self.git("init", "-q", "-b", "main", str(self.project), cwd=self.root)
        for path in ("web/page", "core/limit", "private/key"):
            f = self.project / path
            f.parent.mkdir()
            f.write_text("before\n")
        self.git("add", ".")
        self.git("-c", "user.name=Fixture", "-c", "user.email=test@example.invalid", "commit", "-qm", "initial")
        self.cfg = {"project": str(self.project), "checks": [["command-never-run", "sensitive-argument"]],
                    "check_groups": {"view": [["check-view"]], "limit": [["check-limit"]]},
                    "check_group_descriptions": {"view": "Screen rendering"},
                    "base_check_description": "Base check", "forbidden_paths": ["private"]}
        self.save()
        self.criteria = ["Render updated limit"]
        self.report = self.root / "report.json"

    def git(self, *args, cwd=None):
        subprocess.run(["git", *args], cwd=cwd or self.project, check=True, capture_output=True)

    def save(self):
        store = Store(self.ledger)
        try:
            profiles.save(store, "test", self.cfg)
        finally:
            store.close()

    def run_advisor(self, **options):
        return recommend(self.ledger, "test", self.criteria, **options)

    def change(self, path):
        (self.project / path).write_text("after\n")

    @patch("agent_workflow.check_advisor_models.ask")
    def test_blind_second_pass_and_human_labels(self, ask):
        self.change("web/page")
        self.change("core/limit")
        self.change("private/key")
        preview = self.run_advisor(preview=True)
        self.assertFalse(ask.called)
        self.assertEqual(preview["providers"], ["codex", "claude"])
        self.assertEqual(preview["input"]["change"]["omitted_sensitive"], 1)
        ask.side_effect = [{"groups":["view","view"], "reason":"first_answer"},
                           {"groups":["limit"], "reason":"second_answer"}]
        result = self.run_advisor()
        first, second = ask.call_args_list
        self.assertEqual(first.args[1], second.args[1])
        self.assertNotIn("first_answer", second.args[1])
        for secret in ("sensitive-argument", "command-never-run", "private/key"):
            self.assertNotIn(secret, first.args[1])
        self.assertEqual(preview["input_digest"], result["input_digest"])
        self.assertEqual(result["comparison"], {"primary_only":["view"], "reviewer_only":["limit"], "agreed":[]})
        self.assertEqual(result["checks"], [*self.cfg["checks"], ["check-view"], ["check-limit"]])
        self.report.write_text(json.dumps(result))
        scores = evaluate(self.report, ["limit"])
        self.assertEqual(scores["primary"]["missed"], ["limit"])
        self.assertEqual(scores["combined"]["missed"], [])
        self.assertEqual(scores["combined"]["extra"], ["view"])
        self.assertEqual(evaluate(self.report, [])["combined"]["extra_count"], 2)
        with self.assertRaises(WorkflowError):
            evaluate(self.report, ["no-such-group"])
        store = Store(self.ledger, read_only=True)
        try:
            self.assertEqual(store.all(), [])
        finally:
            store.close()

    @patch("agent_workflow.check_advisor_models.ask", return_value={"groups": ["view"], "reason": "display"})
    def test_rejects_inconsistent_or_malformed_reports(self, ask):
        original = self.run_advisor(reviewer="none")
        for diff in ({"version": True}, {"suggested_groups": []}, {"providers": []}, {"input_digest": "bad"}):
            with self.subTest(diff=diff), self.assertRaises(WorkflowError):
                self.report.write_text(json.dumps({**original, **diff}))
                evaluate(self.report, [])
        self.report.write_text("[" * 2000 + "]" * 2000)
        with self.assertRaises(WorkflowError):
            evaluate(self.report, [])

    @patch("agent_workflow.check_advisor_models.ask")
    def test_single_area_no_group_and_failure(self, ask):
        self.change("web/page")
        ask.return_value = {"groups": [], "reason": "baseline"}
        r = self.run_advisor()
        self.assertEqual((r["providers"], r["suggested_groups"], r["checks"]), (["codex"], [], self.cfg["checks"]))
        self.assertIsNone(r["comparison"])
        ask.side_effect = [{"groups":["view"], "reason":"view"}, WorkflowError("review unavailable")]
        r = self.run_advisor(provider="claude", reviewer="codex")
        self.assertEqual(r["status"], "review_incomplete")
        self.assertEqual(r["suggested_groups"], ["view"])
        self.report.write_text(json.dumps(r))
        with self.assertRaises(WorkflowError):
            evaluate(self.report, ["view"])
        ask.reset_mock(side_effect=True)
        del self.cfg["check_groups"]
        del self.cfg["check_group_descriptions"]
        self.save()
        r = self.run_advisor()
        self.assertEqual(r["providers"], [])
        self.assertEqual(r["checks"], self.cfg["checks"])
        self.assertFalse(ask.called)

    @patch("agent_workflow.check_advisor_models.ask")
    def test_label_validation_project_binding_and_worktrees(self, ask):
        ask.return_value = {"groups": ["run this arbitrary command"], "reason": "bad"}
        with self.assertRaises(WorkflowError):
            self.run_advisor(reviewer="none")
        ask.reset_mock()
        with self.assertRaises(WorkflowError):
            self.run_advisor(project=self.root)
        with self.assertRaises(WorkflowError):
            self.run_advisor(reviewer="codex")
        self.assertFalse(ask.called)
        linked = self.root / "linked"
        self.git("worktree", "add", "-q", "-b", "feature", str(linked))
        self.assertEqual(self.run_advisor(project=linked / "web", preview=True)["path_count"], 0)

    @patch("agent_workflow.check_advisor_models.ask")
    def test_cli_error_codes_and_partial_output(self, ask):
        from agent_workflow.cli import main
        argv = ["agent-workflow", "--home", str(self.ledger), "recommend-checks", "test",
                "--criterion", "render view", "--reviewer", "claude"]
        for answers, code in (([{"groups": ["view"], "reason": "view"}, WorkflowError("review failed")], 3),
                              ([WorkflowError("first failed")], 2)):
            ask.side_effect = answers
            stdout, stderr = StringIO(), StringIO()
            with (self.subTest(exit=code), patch("sys.argv", argv), patch("signal.signal"),
                  patch("os.umask"), redirect_stdout(stdout), redirect_stderr(stderr)):
                with self.assertRaises(SystemExit) as failure:
                    main()
                self.assertEqual(failure.exception.code, code)
                if code == 3:
                    self.assertEqual(json.loads(stdout.getvalue())["status"], "review_incomplete")
                else:
                    self.assertEqual(stdout.getvalue(), "")
                    self.assertIn("first failed", stderr.getvalue())

    def test_descriptions_validate_without_changing_legacy_profiles(self):
        for key, value in (("check_group_descriptions", {"unused": "unknown"}),
                           ("base_check_description", "x\ny"), ("base_check_description", 123)):
            with self.subTest(key=key), self.assertRaises(WorkflowError):
                profiles.validate({**self.cfg, key:value})
