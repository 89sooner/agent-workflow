import hashlib
from pathlib import Path
import shlex
import sys
import unittest

import test_katakuri_goal as fixtures
from agent_workflow import profiles
from agent_workflow.cli import dispatch, parser
from agent_workflow.core import WorkflowError, ready_for_completion, snapshot
from agent_workflow.runner import run_checks


COPY_REPORT = """
from pathlib import Path
import os, sys

source = Path(sys.argv[1])
report = Path(os.environ['WORKFLOW_EVIDENCE_DIR']) / 'junit.xml'
if source.is_symlink():
    report.symlink_to(source.resolve())
elif source.exists():
    report.write_bytes(source.read_bytes())
if len(sys.argv) > 3:
    source.write_text(sys.argv[3])
raise SystemExit(int(sys.argv[2]))
"""
REQUIREMENT = "번들 검사가 실행되어 통과한다"
TESTCASE = "web.bundle::test_built"
PASS = '<testcase classname="web.bundle" name="test_built"/>'
SKIP = '<testcase classname="web.bundle" name="test_built"><skipped/></testcase>'


def report(*cases):
    return "<testsuites><testsuite>" + "".join(cases) + "</testsuite></testsuites>"


class CriterionCheckTests(unittest.TestCase):
    setUp = fixtures.KatakuriGoalTests.setUp
    cli = fixtures.KatakuriGoalTests.cli
    script = fixtures.KatakuriGoalTests.script

    def command(self, name="report.xml", exit_code=0):
        return [sys.executable, "-c", COPY_REPORT, str(self.root / name), str(exit_code)]

    def create(self, *, target=None, extra=None, testcase=TESTCASE):
        target = target or self.command()
        rule = {"check": target}
        if testcase:
            rule["testcase"] = testcase
        profiles.save(self.store, "bound", {
            **self.cfg, "checks": [target, *(extra or [])],
            "criterion_checks": {REQUIREMENT: rule},
        })
        self.cli("create", "BOUND", "--profile", "bound", "--goal", REQUIREMENT,
                 "--criterion", REQUIREMENT, "--criterion", "리뷰로 확인")
        return self.store.get("BOUND")

    def verify(self):
        task = self.store.get("BOUND")
        with self.store.lock(task["project"]) as fd:
            run_checks(self.store, task, 5, fd)
        return self.cli("status", "BOUND")["criteria"]

    def test_invalid_profile_cannot_replace_registered_check_contract(self):
        self.create()
        original = profiles.get(self.store, "bound")
        for changes in (
            {"criterion_checks": {"": {"check": self.command()}}},
            {"criterion_checks": {REQUIREMENT: {"check": ["not-a-registered-command"]}}},
            {"criterion_checks": {REQUIREMENT: {"check": self.command(), "testcase": ""}}},
            {"checks": [self.command(), self.command()]},
        ):
            with self.subTest(changes=changes), self.assertRaises(WorkflowError):
                profiles.save(self.store, "bound", {**original["config"], **changes})
        self.assertEqual(profiles.get(self.store, "bound"), original)

    def test_outcome_comes_from_exact_case_in_bound_command(self):
        self.create(extra=[self.command("other.xml")])
        self.assertEqual(self.cli("status", "BOUND")["criteria"][0]["check_evidence"]["state"], "pending")
        (self.root / "other.xml").write_text(report(PASS))
        src = self.root / "report.xml"
        for text, expected in (
            (None, "missing"),
            (report('<testcase classname="unrelated" name="test_built"/>'), "missing"),
            (report("<system-out><testsuite>" + PASS + "</testsuite></system-out>"), "missing"),
            (report(PASS, PASS), "ambiguous"),
            ("<broken", "invalid"),
            (report(SKIP), "skipped"),
            (report('<testcase classname="web.bundle" name="test_built"><error/></testcase>'), "failed"),
            (report(PASS), "passed"),
        ):
            with self.subTest(expected=expected, text=text):
                if text is not None:
                    src.write_text(text)
                bound, unbound = self.verify()
                evidence = bound["check_evidence"]
                self.assertEqual(evidence["state"], expected)
                self.assertEqual(evidence["testcase"], TESTCASE)
                self.assertNotIn("check_evidence", unbound)
                result = self.store.get("BOUND")["verification"]["results"][0]
                self.assertEqual(evidence["result_id"], result["result_id"])
                if expected == "passed":
                    self.assertEqual(evidence["report_sha256"], hashlib.sha256(src.read_bytes()).hexdigest())

    def test_link_to_unowned_report_cannot_certify_a_testcase(self):
        self.create()
        target = self.root / "prior-run.xml"
        target.write_text(report(PASS))
        (self.root / "report.xml").symlink_to(target)
        self.assertEqual(self.verify()[0]["check_evidence"]["state"], "invalid")

    def test_appended_duplicate_cannot_replace_the_bound_execution(self):
        source = self.root / "report.xml"
        source.write_text(report(SKIP))
        # The two invocations of this argv return different reports, like a flaky test.
        producer = [*self.command(), report(PASS)]
        task = self.create(target=producer)
        dispatch(parser().parse_args(["--home", str(self.home), "check-add", task["id"], shlex.join(producer)]))
        evidence = self.verify()[0]["check_evidence"]
        first, second = self.store.get("BOUND")["verification"]["results"]
        self.assertEqual(evidence["state"], "skipped")
        self.assertEqual(evidence["result_id"], first["result_id"])
        other_report = Path(second["stdout"]).parent / "junit.xml"
        self.assertEqual(other_report.read_text(), report(PASS))

    def test_nonzero_command_cannot_certify_passing_testcase(self):
        self.create(target=self.command(exit_code=1))
        (self.root / "report.xml").write_text(report(PASS))
        self.assertEqual(self.verify()[0]["check_evidence"]["state"], "failed")
        (self.root / "report.xml").write_text(report(SKIP))
        self.assertEqual(self.verify()[0]["check_evidence"]["state"], "skipped")

    def test_planner_cannot_remove_an_original_objectives_registered_check(self):
        command = self.command()
        profiles.save(self.store, "planned", {**self.cfg, "checks": [command], "criterion_checks": {
            REQUIREMENT: {"check": command, "testcase": TESTCASE},
        }})
        self.cli("create", "AUTO", "--profile", "planned", "--goal", REQUIREMENT)
        (self.root / "report.xml").write_text(report(SKIP))
        self.script({"criteria": [REQUIREMENT + "."]}, fixtures.step(1), fixtures.step(2))
        with self.assertRaisesRegex(WorkflowError, "필수 검사 근거 대기"):
            self.cli("run", "AUTO")
        waiting = self.cli("status", "AUTO")
        self.assertEqual([r["text"] for r in waiting["criteria"]], [REQUIREMENT, REQUIREMENT + "."])
        self.assertEqual(waiting["criteria"][0]["check_evidence"]["state"], "skipped")

    def test_completed_goal_evidence_is_bound_to_verified_source(self):
        task = self.create()
        source = self.root / "report.xml"
        source.write_text(report(PASS))
        self.script(fixtures.step(1), fixtures.step(2), fixtures.review(True, True))
        done = self.cli("run", "BOUND")
        self.assertEqual(done["status"], "completed")
        evidence = done["criteria"][0]["check_evidence"]
        self.assertEqual(evidence["state"], "passed")
        current = snapshot(task["project"])
        self.assertEqual(evidence["fingerprint"], current["fingerprint"])
        task = self.store.get("BOUND")
        ready_for_completion(task, current)
        self.assertTrue(self.cli("status", "BOUND")["verification_is_current"])
        row = task["katakuri_goal"]["audit"]["rows"][0]
        self.assertEqual(row["check_result_id"], evidence["result_id"])
        row["check_result_id"] = "some-other-run"
        with self.assertRaises(WorkflowError):
            ready_for_completion(task, current)
        (Path(task["project"]) / "app.py").write_text("different implementation")
        self.assertFalse(self.cli("status", "BOUND")["verification_is_current"])
        task = self.store.get("BOUND")
        with self.assertRaises(WorkflowError):
            ready_for_completion(task, snapshot(task["project"]))

    def test_new_passing_receipt_can_reopen_a_parked_failed_check(self):
        command = [sys.executable, "-c", "from pathlib import Path; import sys; sys.exit(not Path(sys.argv[1]).exists())",
                   str(self.root / "build-ready")]
        self.create(target=command, testcase=None)
        self.script(fixtures.step(1), fixtures.step(2), fixtures.step(1), fixtures.step(2))
        with self.assertRaisesRegex(WorkflowError, "실패한 검사"):
            self.cli("run", "BOUND")
        task = self.store.get("BOUND")
        goal = task["katakuri_goal"]
        goal["failed_check_input"] = goal["failed_check_input"].rsplit(":", 1)[0]
        legacy = goal["items"][0]
        legacy.pop("parked_by")
        legacy["retry_after_change"] = goal["failed_check_input"]
        self.store.save(task, "legacy_check_wait_fixture")
        (self.root / "build-ready").touch()
        self.assertEqual(self.verify()[0]["check_evidence"]["state"], "passed")
        self.script(fixtures.review(True, True))
        self.assertEqual(self.cli("run", "BOUND")["status"], "completed")

    def test_new_bound_receipt_can_reopen_a_rejected_review(self):
        self.create()
        report_path = self.root / "report.xml"
        report_path.write_text(report(PASS))
        self.script(fixtures.step(1), fixtures.step(2), fixtures.review(False, True), fixtures.step(1))
        with self.assertRaisesRegex(WorkflowError, "거절된 리뷰"):
            self.cli("run", "BOUND")
        waiting = self.cli("status", "BOUND")
        with self.assertRaises(WorkflowError):
            self.cli("run", "BOUND")
        self.assertEqual(self.cli("status", "BOUND")["agent_calls"], waiting["agent_calls"])
        report_path.write_text(report(PASS.replace("/>", "><system-out>new evidence</system-out></testcase>")))
        new_check = self.verify()[0]["check_evidence"]
        self.assertNotEqual(new_check["result_id"], waiting["last_audit"]["rows"][0]["check_result_id"])
        self.script(fixtures.review(True, True))
        done = self.cli("run", "BOUND")
        self.assertEqual(done["status"], "completed")
        self.assertEqual(done["agent_calls"], waiting["agent_calls"] + 1)
        self.assertEqual(done["last_audit"]["rows"][0]["check_result_id"],
                         done["criteria"][0]["check_evidence"]["result_id"])

    def test_blocked_conditions_reopen_individually_as_their_checks_pass(self):
        command = self.command()
        other = "다른 검사도 통과한다"
        profiles.save(self.store, "two", {**self.cfg, "checks": [command], "criterion_checks": {
            REQUIREMENT: {"check": command, "testcase": TESTCASE},
            other: {"check": command, "testcase": "web.bundle::test_other"},
        }})
        self.cli("create", "BOUND", "--profile", "two", "--goal", "Both", "--criterion", REQUIREMENT, "--criterion", other)
        path = self.root / "report.xml"
        other_pass = PASS.replace("test_built", "test_other")
        path.write_text(report(PASS, other_pass))
        rejection = {**fixtures.review(False, False), "outcome": "blocked"}
        self.script(fixtures.step(1), fixtures.step(2), rejection)
        with self.assertRaises(WorkflowError):
            self.cli("run", "BOUND")
        path.write_text(report(PASS, SKIP.replace("test_built", "test_other")))
        self.verify()
        with self.assertRaises(WorkflowError):
            self.cli("run", "BOUND")
        self.assertEqual([r["state"] for r in self.cli("status", "BOUND")["criteria"]], ["proposed", "waiting"])
        path.write_text(report(PASS, other_pass))
        self.verify()
        self.script(fixtures.review(True, True))
        self.assertEqual(self.cli("run", "BOUND")["status"], "completed")

    def test_rerunning_same_input_cannot_reuse_an_earlier_success(self):
        self.create()
        source = self.root / "report.xml"
        source.write_text(report(PASS))
        first = self.verify()[0]["check_evidence"]
        source.write_text(report(SKIP))
        replacement = self.verify()[0]["check_evidence"]
        self.assertEqual(replacement["state"], "skipped")
        self.assertEqual(first["fingerprint"], replacement["fingerprint"])
        self.assertNotEqual(first["result_id"], replacement["result_id"])
        self.script(fixtures.step(1), fixtures.step(2), fixtures.review(True, True))
        with self.assertRaises(WorkflowError):
            self.cli("run", "BOUND")
        waiting = self.cli("status", "BOUND")
        self.assertEqual(waiting["phase"], "waiting")
        with self.assertRaises(WorkflowError):
            self.cli("run", "BOUND")
        self.assertEqual(self.cli("status", "BOUND")["agent_calls"], waiting["agent_calls"])
        source.write_text(report(PASS))
        done = self.cli("run", "BOUND")
        self.assertEqual(done["status"], "completed")
        self.assertEqual(done["agent_calls"], waiting["agent_calls"] + 1)

    def test_command_only_criterion_and_legacy_goal(self):
        self.create(testcase=None)
        bound, unbound = self.verify()
        self.assertEqual(bound["check_evidence"]["state"], "passed")
        self.assertNotIn("check_evidence", unbound)
        legacy = self.cli("create", "OLD", "--profile", "kb", "--goal", REQUIREMENT, "--criterion", REQUIREMENT)
        self.assertNotIn("check_evidence", legacy["criteria"][0])
