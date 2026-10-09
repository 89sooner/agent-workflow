import copy
from pathlib import Path
import sys
import unittest

import test_workflow as fixtures
from agent_workflow import profiles
from agent_workflow.cli import dispatch, parser
from agent_workflow.core import WorkflowError, git
from agent_workflow.workspaces import create_from_profile, prepare, cleanup


class ProfileTests(unittest.TestCase):
    setUp = fixtures.WorkflowTests.setUp
    tearDown = fixtures.WorkflowTests.tearDown

    def config(self):
        return {"project": str(self.project), "checks": [[sys.executable, "-c", "import app; assert app.VALUE == 1"]],
                "allowed_paths": ["app.py"], "execution": {"recoveries": 2}}

    def test_saved_snapshot_and_isolated_worktree(self):
        cfg = self.config()
        profiles.save(self.store, "example", cfg)
        original_head = git(self.project, "rev-parse", "HEAD")
        (self.project / "app.py").write_text("VALUE = 77\n")
        task = create_from_profile(self.store, "ISOLATED", "example", "one", ["one"])
        self.assertNotEqual(Path(task["project"]), self.project)
        self.assertEqual((Path(task["project"]) / "app.py").read_text(), "VALUE = 1\n")
        self.assertEqual((self.project / "app.py").read_text(), "VALUE = 77\n")
        self.assertEqual(git(self.project, "rev-parse", "HEAD"), original_head)
        self.assertTrue(task["workspace"]["source_dirty"])
        cfg["execution"]["recoveries"] = 0
        profiles.save(self.store, "example", cfg)
        self.assertEqual(self.store.get("ISOLATED")["execution"]["recoveries"], 2)
        with self.assertRaises(WorkflowError):
            create_from_profile(self.store, "ISOLATED", "example", "two", ["two"])

    def test_prepare_once_and_dirty_cleanup_preserves_work(self):
        cfg = self.config()
        cfg["setup"] = [[sys.executable, "-c", "from pathlib import Path; p=Path('.prepared'); p.write_text(p.read_text()+'x' if p.exists() else 'x')"]]
        cfg["allowed_paths"].append(".prepared")
        profiles.save(self.store, "example", cfg)
        task = create_from_profile(self.store, "PREPARE", "example", "one", ["one"])
        with self.store.lock(task["project"]) as fd:
            prepare(self.store, task, 5, fd)
            prepare(self.store, task, 5, fd)
        self.assertEqual((Path(task["project"]) / ".prepared").read_text(), "x")
        result = cleanup(self.store, task, remove=True)
        self.assertFalse(result["removed"])
        self.assertTrue((Path(task["project"]) / ".prepared").exists())

    def test_failed_setup_is_not_silently_repeated(self):
        cfg = self.config()
        cfg["setup"] = [[sys.executable, "-c", "raise SystemExit(2)"]]
        profiles.save(self.store, "example", cfg)
        task = create_from_profile(self.store, "FAIL", "example", "one", ["one"])
        with self.store.lock(task["project"]) as fd:
            with self.assertRaises(WorkflowError):
                prepare(self.store, task, 5, fd)
            with self.assertRaisesRegex(WorkflowError, "retry-setup"):
                prepare(self.store, task, 5, fd)
        self.assertEqual(task["workspace"]["setup_state"], "failed")

    def test_bad_config_rejected_before_worktree_creation(self):
        bad = [{"unknown": 1}, {"allowed_paths": ["../escape"]}, {"execution": {"recoveries": True}},
               {"owner": "codex", "reviewer": "codex"}, {"setup": ["npm ci"]},
               {"services": [{"name": "web", "argv": ["python3"], "port": 3333, "ready_url": "http://example.com:3333"}]},
               {"execution": False}]
        for changes in bad:
            with self.subTest(changes=changes):
                cfg = self.config(); cfg.update(changes)
                with self.assertRaises(WorkflowError):
                    profiles.save(self.store, "bad", cfg)
        self.assertEqual(profiles.list_profiles(self.store), [])

    def test_cli_profile_registration_and_create(self):
        import json
        path = self.root / "config.json"
        cfg = self.config(); cfg["workspace"] = {"isolate": False}
        path.write_text(json.dumps(cfg))
        args = parser().parse_args(["--home", str(self.store.home), "profile", "save", "cli", "--file", str(path)])
        dispatch(args)
        task = dispatch(parser().parse_args(["--home", str(self.store.home), "create", "CLI", "--profile", "cli", "--goal", "one", "--criterion", "one"]))
        self.assertEqual(task["project"], str(self.project))
        self.assertEqual(task["checks"], cfg["checks"])
        self.assertFalse(task["workspace"]["managed"])


if __name__ == "__main__":
    unittest.main()
