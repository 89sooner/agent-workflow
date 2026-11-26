from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from agent_workflow import cli
from agent_workflow.core import WorkflowError


class ModelMeasurementCliTests(unittest.TestCase):
    def test_invalid_label_is_never_treated_as_a_clean_control(self):
        with tempfile.TemporaryDirectory() as root:
            label = Path(root) / "label.json"
            args = cli.parser().parse_args([
                "measure-review", "p", "--base", "main", "--candidate", "topic", "--criterion", "contract",
                "--implementation-report", "author summary", "--expected-defect", str(label),
            ])
            for invalid in ("null", "[]", "[" * 2000 + "]" * 2000, " " * 16385):
                label.write_text(invalid)
                with self.subTest(value=invalid[:10]), self.assertRaises(WorkflowError):
                    cli.dispatch(args)

    def test_incomplete_measurement_prints_its_receipt_and_signals_partial_result(self):
        argv = ["agent-workflow", "measure-checks", "p", "--base", "main", "--candidate", "topic",
                "--criterion", "contract"]
        report = {"status": "review_incomplete", "arms": {"combined": None}}
        with (patch.object(sys, "argv", argv), patch("signal.signal"), patch("os.umask"),
              patch("agent_workflow.check_measurement.measure", return_value=report),
              redirect_stdout(io.StringIO()) as stdout, self.assertRaises(SystemExit) as caught):
            cli.main()
        self.assertEqual(caught.exception.code, 3)
        self.assertEqual(json.loads(stdout.getvalue()), report)
