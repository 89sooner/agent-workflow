import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import test_workflow as fixtures
from agent_workflow.closure import finalize, sync_documents
from agent_workflow.core import WorkflowError, complete, snapshot
from agent_workflow.profiles import save
from agent_workflow.runner import execute, run_checks
from agent_workflow.workspaces import create_from_profile


class ClosureTests(unittest.TestCase):
    setUp = fixtures.WorkflowTests.setUp
    tearDown = fixtures.WorkflowTests.tearDown
    result = staticmethod(fixtures.WorkflowTests.result)
    fake_command = fixtures.WorkflowTests.fake_command

    def task(self, **options):
        cfg = {"project": str(self.project), "workspace": {"isolate": False},
               "checks": [[sys.executable, "-c", "import app; assert app.VALUE == 1"]],
               "allowed_paths": ["app.py", "notes.md"], "documents": ["notes.md"],
               "exports": {"directory": str(self.root / "dailywork"), "timezone": "Asia/Seoul"}}
        cfg.update(options)
        save(self.store, "example", cfg)
        return create_from_profile(self.store, "DOC", "example", "구현 결과 기록", ["기록과 코드 검증"])

    def test_repository_block_preserves_prose_and_enters_verified_fingerprint(self):
        (self.project / "notes.md").write_text("# 기존 문서\n\n사용자가 작성한 문장.\n")
        task = self.task()
        with patch("agent_workflow.runner.command_for", side_effect=self.fake_command):
            result = execute(self.store, task["id"], timeout=5, repairs=0)
        text = (self.project / "notes.md").read_text()
        self.assertIn("사용자가 작성한 문장.", text)
        self.assertEqual(text.count("<!-- agent-workflow:DOC:begin -->"), 1)
        self.assertEqual(result["review"]["fingerprint"], snapshot(self.project)["fingerprint"])
        with self.assertRaises(WorkflowError):
            sync_documents(self.store, result)

    def test_export_idempotence_and_user_edit_protection(self):
        task = self.task()
        with patch("agent_workflow.runner.command_for", side_effect=self.fake_command):
            task = execute(self.store, task["id"], timeout=5, repairs=0)
        first = finalize(self.store, task)
        path = Path(first["export"]["path"])
        second = finalize(self.store, task)
        self.assertEqual(first["export"], second["export"])
        self.assertEqual(len(list(path.parent.glob("*.md"))), 1)
        text = path.read_text()
        self.assertIn("status: done", text)
        self.assertIn("## 작업 흐름도", text)
        self.assertLess(text.index("## 작업 흐름도"), text.index("## 요약"))
        self.assertLessEqual(len(text.split("## 요약\n")[1].strip().splitlines()), 10)
        path.write_text(text + "사용자 추가 메모\n")
        with self.assertRaisesRegex(WorkflowError, "덮어쓰지"):
            finalize(self.store, task)
        self.assertIn("사용자 추가 메모", path.read_text())

    def test_stale_completion_export_is_not_done(self):
        task = self.task()
        with patch("agent_workflow.runner.command_for", side_effect=self.fake_command):
            task = execute(self.store, task["id"], timeout=5, repairs=0)
        (self.project / "app.py").write_text("VALUE = 5\n")
        result = finalize(self.store, task)
        self.assertIn("status: partial", Path(result["worklog"]).read_text())

    def test_malformed_marker_is_not_overwritten(self):
        (self.project / "notes.md").write_text("original\n<!-- agent-workflow:DOC:begin -->\nincomplete")
        task = self.task()
        with self.assertRaises(WorkflowError):
            sync_documents(self.store, task)
        self.assertIn("incomplete", (self.project / "notes.md").read_text())

    def test_document_symlink_cannot_cross_allowlist(self):
        (self.project / "notes.md").symlink_to("app.py")
        task = self.task(allowed_paths=["notes.md"])
        before = (self.project / "app.py").read_text()
        with self.assertRaisesRegex(WorkflowError, "허용 범위"):
            sync_documents(self.store, task)
        self.assertEqual((self.project / "app.py").read_text(), before)


if __name__ == "__main__":
    unittest.main()
