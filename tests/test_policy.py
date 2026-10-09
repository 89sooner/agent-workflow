import datetime as dt
from pathlib import Path
import unittest

import test_workflow as fixtures
from agent_workflow.core import WorkflowError
from agent_workflow.policy import reservation, scope_check, window_limit
from agent_workflow.profiles import save
from agent_workflow.workspaces import create_from_profile


class PolicyTests(unittest.TestCase):
    setUp = fixtures.WorkflowTests.setUp
    tearDown = fixtures.WorkflowTests.tearDown

    def task(self, ident, allowed, **options):
        save(self.store, ident, {"project": str(self.project), "allowed_paths": allowed, **options})
        return create_from_profile(self.store, ident, ident, "scope", ["scope"])

    def test_conflicting_worktrees_block_but_disjoint_work_can_run(self):
        a = self.task("A", ["app.py"])
        b = self.task("B", ["app.py"])
        c = self.task("C", ["docs"])
        with reservation(self.store, a):
            with self.assertRaises(WorkflowError):
                with reservation(self.store, b):
                    self.fail("conflicting reservation")
            with reservation(self.store, c):
                pass
        with reservation(self.store, b):
            pass

    def test_scope_and_forbidden_paths_cannot_be_reported_as_done(self):
        task = self.task("SCOPE", ["."], forbidden_paths=["private"])
        root = Path(task["project"])
        (root / "app.py").write_text("VALUE = 2\n")
        scope_check(task)
        (root / "private").mkdir()
        (root / "private" / "key.txt").write_text("fixture")
        with self.assertRaisesRegex(WorkflowError, "범위 위반"):
            scope_check(task)

    def test_preexisting_outside_change_preserved_but_cannot_be_reverted(self):
        (self.project / "outside.txt").write_text("existing work")
        task = self.task("EXISTING", ["app.py"], workspace={"isolate": False})
        scope_check(task)
        (self.project / "outside.txt").unlink()
        with self.assertRaisesRegex(WorkflowError, "범위 위반"):
            scope_check(task)

    def test_allowed_parent_does_not_match_sibling_prefix(self):
        task = self.task("PREFIX", ["web"])
        (Path(task["project"]) / "web-secret.txt").write_text("outside")
        with self.assertRaises(WorkflowError):
            scope_check(task)

    def test_staged_outside_change_cannot_hide_behind_restored_file(self):
        from agent_workflow.core import git
        task = self.task("INDEX", ["allowed"])
        path = Path(task["project"]) / "app.py"
        original = path.read_text()
        path.write_text("VALUE = 7\n")
        git(task["project"], "add", "app.py")
        path.write_text(original)
        with self.assertRaisesRegex(WorkflowError, "범위 위반"):
            scope_check(task)

    def test_external_symlink_is_rejected_without_reading_target(self):
        task = self.task("LINK", ["."])
        (Path(task["project"]) / "external").symlink_to(self.root / "not-present")
        with self.assertRaisesRegex(WorkflowError, "범위 위반"):
            scope_check(task)

    def test_midnight_window_uses_start_day_and_caps_next_run(self):
        cfg = {"blocked_windows": [{"timezone": "Asia/Seoul", "days": [0], "start": "23:00", "end": "01:00"}]}
        # Monday 22:59:30 KST; Tuesday 00:30 KST belongs to Monday's window.
        before = dt.datetime(2026, 10, 12, 13, 59, 30, tzinfo=dt.timezone.utc)
        self.assertEqual(window_limit(cfg, before), 30)
        with self.assertRaisesRegex(WorkflowError, "제한 시간대"):
            window_limit(cfg, dt.datetime(2026, 10, 12, 15, 30, tzinfo=dt.timezone.utc))
        self.assertGreater(window_limit(cfg, dt.datetime(2026, 10, 12, 16, 1, tzinfo=dt.timezone.utc)), 0)


if __name__ == "__main__":
    unittest.main()
