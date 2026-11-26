"""Regression contract for the self-hosted project goal."""
from pathlib import Path
import sys
import unittest

import test_workflow as fixtures
from agent_workflow.cli import dispatch, parser
from agent_workflow.core import create_task


class ResumeFingerprintNoticeTests(unittest.TestCase):
    setUp = fixtures.WorkflowTests.setUp
    tearDown = fixtures.WorkflowTests.tearDown
    checked = fixtures.WorkflowTests.checked

    def test_resume_distinguishes_code_drift_from_reference_drift(self):
        ref = self.root / "contract.txt"
        ref.write_text("contract v1")
        task = create_task(self.store, "NOTICE", self.project, "Keep the existing API", ["passes"],
                           [ref], [[sys.executable, "-c", "pass"]], "claude", "codex", "local software only")
        self.checked(task)
        recorded = self.store.get("NOTICE")["verification"]["fingerprint"]
        args = parser().parse_args(["--home", str(self.store.home), "resume-view", "NOTICE"])
        current = dispatch(args)
        self.assertNotIn(recorded[:12], " ".join(current["uncertainties"]))
        self.assertEqual(current["next_action"]["action"], "review")

        ref.write_text("contract v2")
        refs_only = dispatch(args)
        self.assertEqual(recorded, refs_only["fingerprint"])
        self.assertNotIn(recorded[:12], " ".join(refs_only["uncertainties"]))
        self.assertEqual(refs_only["next_action"]["action"], "recheck")
        ref.write_text("contract v1")

        (Path(task["project"]) / "app.py").write_text("VALUE = 2\n")
        stale = dispatch(args)
        live = stale["fingerprint"]
        self.assertNotEqual(recorded, live)
        self.assertFalse(stale["valid_evidence"])
        self.assertEqual(stale["next_action"]["action"], "recheck")
        self.assertTrue(any(recorded[:12] in note and live[:12] in note
                            and note.index(recorded[:12]) < note.index(live[:12])
                            for note in stale["uncertainties"]), "show the recorded and observed fingerprint prefixes in order")
