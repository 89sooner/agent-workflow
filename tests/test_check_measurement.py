from contextlib import closing
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from agent_workflow import check_measurement as cm, profiles
from agent_workflow.core import Store, WorkflowError, git


class CheckMeasurementTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.repo = root / "source"
        self.repo.mkdir()
        self.home = root / "ledger"
        git(self.repo, "init", "-q")
        git(self.repo, "config", "user.email", "fixture@example.invalid")
        git(self.repo, "config", "user.name", "Fixture")
        self.value = self.repo / "value"
        self.value.write_text("old")
        (self.repo / ".gitignore").write_text("context-seed\n")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-qm", "base")
        self.base = git(self.repo, "rev-parse", "HEAD")
        self.value.write_text("new")
        git(self.repo, "commit", "-qam", "candidate")
        self.candidate = git(self.repo, "rev-parse", "HEAD")
        self.value.write_text("private uncommitted sentinel")
        self.count = root / "counter"

    @staticmethod
    def check(body, *args):
        return [sys.executable, "-c", "from pathlib import Path; import sys; " + body, *args]

    def save(self, base, groups, **more):
        with closing(Store(self.home)) as store:
            return profiles.save(store, "paired", {"project": str(self.repo), "checks": base,
                                 "check_groups": groups, **more})

    def measure(self, first=("first",), second=("second",), **kwargs):
        packets = []

        def ask(provider, packet, timeout, *, call):
            packets.append(packet)
            call["calls"] += 1
            selection = first if provider == "codex" else second
            if selection is None:
                raise WorkflowError("provider unavailable")
            return {"groups": list(selection), "reason": "fixture", "provider": provider}

        with patch("agent_workflow.check_advisor._ask", side_effect=ask):
            report = cm.measure(self.home, "paired", self.base, self.candidate, ["public behavior"], **kwargs)
        for packet in packets:
            self.assertNotIn("private uncommitted sentinel", str(packet))
            self.assertEqual(packet, packets[0])
        self.assertEqual(self.value.read_text(), "private uncommitted sentinel")
        self.assertEqual(git(self.repo, "rev-parse", "HEAD"), self.candidate)
        self.assertEqual(len(git(self.repo, "worktree", "list").splitlines()), 1)
        with closing(Store(self.home, read_only=True)) as store:
            self.assertEqual(store.all(), [])
        return report

    def test_new_failures_compared_to_full_matrix_once_per_registered_slot(self):
        base = self.check("pass")
        first = self.check("print('first')")
        extra = self.check("assert Path('value').read_text() != 'new'")
        missed = self.check("assert Path('value').read_text() == 'old'")
        self.save([base, base], {"first": [base, first], "second": [first, extra],
                                 "overlap": [extra], "unselected": [missed]})
        r = self.measure()
        self.assertEqual(r["status"], "complete")
        rows = r["commands"]
        self.assertEqual([c["classification"] for c in rows], ["passing"] * 3 + ["regression"] * 2)
        self.assertEqual([len(c["candidate"]) + len(c["baseline"]) for c in rows], [4] * 5)
        self.assertEqual(r["arms"]["primary"]["command_ids"], ["C1", "C2", "C3"])
        self.assertEqual(r["arms"]["combined"]["detected_regressions"], ["C4"])
        self.assertEqual(r["arms"]["combined"]["missed_regressions"], ["C5"])
        self.assertEqual(r["delta"]["additional_regressions"], ["C4"])
        self.assertIs(r["delta"]["newly_detected_change"], True)
        combined_rows = r["selected_plan_observations"]["combined"]["commands"]
        self.assertEqual(r["delta"]["additional_selected_check_ms"], combined_rows[-1]["candidate_median_ms"])
        self.assertEqual([r["arms"][name]["model_calls"] for name in ("primary", "combined")], [1, 2])
        self.assertEqual(r["delta"]["additional_model_calls"], 1)
        self.assertEqual(r["delta"]["additional_model_ms"], r["model_calls"]["review"]["elapsed_ms"])

    def test_unselected_group_cannot_prime_the_selected_groups_cache(self):
        warm = self.check("Path('context-seed').touch()")
        check = self.check("sys.exit(Path('context-seed').exists() and Path('value').read_text() == 'new')")
        self.save([], {"first": [warm], "second": [check]})
        r = self.measure(first=(), second=("second",))
        self.assertEqual(r["status"], "complete")
        self.assertEqual(r["commands"][1]["classification"], "regression")
        self.assertEqual(r["arms"]["combined"]["command_ids"], ["C2"])
        self.assertEqual(r["arms"]["combined"]["missed_regressions"], ["C2"])
        self.assertEqual(r["delta"]["additional_regressions"], [])
        self.assertIs(r["delta"]["newly_detected_change"], False)
        self.assertEqual(r["selected_plan_observations"]["combined"]["commands"][0]["classification"], "passing")

    def test_group_order_is_preserved_and_identical_arms_share_receipts(self):
        first = self.check("sys.exit(Path('context-seed').exists() and Path('value').read_text() == 'new')")
        second = self.check("Path('context-seed').touch()")
        self.save([], {"first": [first], "second": [second]})
        r = self.measure(first=("second", "first"), second=("first",))
        self.assertEqual(r["commands"][0]["classification"], "passing")
        self.assertEqual(list(r["selected_plan_observations"]), ["primary"])
        for arm in r["arms"].values():
            self.assertEqual(arm["observation_plan"], "primary")
            self.assertEqual(arm["command_ids"], ["C2", "C1"])
            self.assertEqual(arm["detected_regressions"], ["C1"])
        self.assertEqual(len(r["selected_plan_observations"]["primary"]["trials"]), 4)

    def test_preexisting_unstable_fixed_or_unobserved_failures_cannot_earn_rescue(self):
        steady_failure = self.check("sys.exit(1)")
        fixed = self.check("assert Path('value').read_text() == 'new'")
        flaky = self.check("p=Path(sys.argv[1]); n=int(p.read_text()) if p.exists() else 0; "
                           "p.write_text(str(n+1)); sys.exit(n % 2)", str(self.count))
        absent = ["/no/p1-check-measurement/executable"]
        self.save([], {"first": [fixed], "second": [steady_failure, flaky, absent]})
        r = self.measure()
        self.assertEqual(r["status"], "checks_inconclusive")
        self.assertEqual([c["classification"] for c in r["commands"]],
                         ["fixed", "preexisting_failure", "inconsistent", "inconclusive"])
        self.assertEqual(r["arms"]["combined"]["inconclusive_selected"], ["C3", "C4"])
        self.assertEqual(r["delta"]["additional_regressions"], [])
        self.assertIsNone(r["delta"]["additional_selected_check_ms"])

    def test_setup_and_evidence_files_and_source_drift(self):
        setup = self.check("Path('ready-state').write_text('prepared')")
        # setup and check results use ignored artifacts only
        self.value.write_text("new")
        (self.repo / ".gitignore").write_text("ready-state\n")
        git(self.repo, "add", ".gitignore")
        git(self.repo, "commit", "-qm", "ignore fixture")
        self.base = git(self.repo, "rev-parse", "HEAD")
        self.value.write_text("another")
        git(self.repo, "commit", "-qam", "new candidate")
        self.candidate = git(self.repo, "rev-parse", "HEAD")
        self.value.write_text("private uncommitted sentinel")
        uses_setup = self.check("import os; p=Path(os.environ['WORKFLOW_EVIDENCE_DIR']); "
                                "(p/'proof').write_text('observed'); assert Path('ready-state').is_file()")
        dirties_checkout = self.check("Path('value').write_text('different')")
        self.save([uses_setup], {"first": [dirties_checkout], "second": [self.check("pass")]}, setup=[setup])
        r = self.measure()
        self.assertTrue(all(c["classification"] == "inconclusive" for c in r["commands"]))
        self.assertTrue(all(s["status"] == "changed_input" for s in r["commands"][1]["candidate"]))
        self.assertTrue(all(s["status"] == "not_run" for s in r["commands"][2]["candidate"]))

    def test_provider_failures_show_attempt_and_missing_arm(self):
        self.save([], {"first": [self.check("pass")], "second": [self.check("sys.exit(1)")]})
        r = self.measure(second=None)
        self.assertEqual(r["status"], "review_incomplete")
        self.assertEqual(r["arms"]["primary"]["model_calls"], 1)
        self.assertIsNone(r["arms"]["combined"])
        self.assertIsNone(r["delta"]["additional_regressions"])
        self.assertEqual(r["delta"]["additional_model_calls"], 1)
        r = self.measure(first=None)
        self.assertEqual(r["status"], "primary_incomplete")
        self.assertIsNone(r["arms"]["primary"])
        self.assertEqual(r["model_calls"]["review"]["attempts"], 0)
        self.assertEqual(len(r["oracle_trials"]), 4)

    def test_different_declared_inputs_across_repetitions_invalidate_oracle(self):
        self.save([], {"first": [self.check("pass")], "second": [self.check("sys.exit(1)")]},
                  setup=[self.check("Path('context-seed').write_text(Path.cwd().name)")],
                  check_inputs=["context-seed"])
        r = self.measure()
        self.assertEqual(r["status"], "checks_inconclusive")
        self.assertEqual(r["replicate_environment_drift"], ["baseline", "candidate"])
        self.assertEqual([c["classification"] for c in r["commands"]], ["inconclusive"] * 2)
        self.assertIsNone(r["delta"]["additional_selected_check_ms"])

    def test_timeout_is_an_incomplete_observation(self):
        ok = {"status": "ran", "timed_out": False, "seconds": 0.1, "exit_code": 0}
        timeout = {**ok, "timed_out": True, "exit_code": -15}
        row = {"baseline": [ok, ok], "candidate": [timeout, timeout]}
        self.assertEqual(cm._classify(row), "inconclusive")

    def test_unavailable_oracle_keeps_paid_models_and_marks_results_unknown(self):
        self.save([], {"first": [self.check("pass")], "second": [self.check("sys.exit(1)")]})
        with patch.object(cm, "_isolated_oracle", side_effect=WorkflowError("workspace unavailable")):
            r = self.measure()
        self.assertEqual(r["status"], "checks_inconclusive")
        self.assertEqual(r["oracle_error"], "workspace unavailable")
        self.assertEqual(r["arms"]["combined"]["model_calls"], 2)
        self.assertIsNone(r["arms"]["combined"]["change_regression_detected"])
        self.assertIsNone(r["delta"]["newly_detected_change"])

    def test_repeated_base_failure_has_one_signal_but_two_execution_costs(self):
        fails = self.check("assert Path('value').read_text() == 'old'")
        self.save([fails, fails], {"first": [fails], "second": [fails]})
        result = self.measure()
        for arm in result["arms"].values():
            self.assertEqual(arm["detected_regressions"], ["C1"])
            self.assertEqual(arm["command_ids"], ["C1", "C2"])
        self.assertEqual(result["delta"]["additional_regressions"], [])
        self.assertIs(result["delta"]["newly_detected_change"], False)

    def test_original_ledger_and_absolute_source_arguments_are_isolated(self):
        sentinel = self.home / "external"
        setup = self.check("import os; path=Path(os.environ['AGENT_WORKFLOW_HOME']); "
                           "(path/'setup-marker').touch()")
        checked = self.check("import os; assert (Path(os.environ['AGENT_WORKFLOW_HOME'])/'setup-marker').exists(); "
                             "assert Path(sys.argv[1]).read_text() != 'private uncommitted sentinel'",
                             str(self.value))
        self.save([checked], {"first": [checked], "second": [checked]}, setup=[setup])
        sentinel.mkdir()
        with patch.dict(os.environ, AGENT_WORKFLOW_HOME=str(sentinel), GIT_WORK_TREE=str(self.repo)):
            r = self.measure()
        self.assertEqual(r["status"], "complete")
        self.assertFalse((sentinel / "setup-marker").exists())

    def test_path_assignments_and_inherited_environment_resolve_to_the_measured_tree(self):
        script = ("import os; from pathlib import Path; root=Path.cwd(); "
                  "assert Path(os.environ['FIXTURE_ROOT']) == Path(os.environ['PWD']) == root; "
                  "assert os.environ['FIXTURE_PATH'].split(os.pathsep) == [str(root), str(root/'tools')]; "
                  "assert (root/'value').read_text() == 'old'")
        check = ["env", "FIXTURE_ROOT=" + str(self.repo), sys.executable, "-c", script]
        self.save([], {"first": [check], "second": [check]})
        with patch.dict(os.environ, FIXTURE_PATH=os.pathsep.join(map(str, (self.repo, self.repo / "tools")))):
            r = self.measure()
        self.assertEqual(r["status"], "complete")
        self.assertEqual(r["commands"][0]["classification"], "regression")
