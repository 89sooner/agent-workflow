import sys
import unittest

import test_workflow as fixtures
from agent_workflow import connections, profiles
from agent_workflow.cli import dispatch, parser
from agent_workflow.core import Store, WorkflowError, git
from agent_workflow.runner import run_checks
from agent_workflow.workspaces import create_from_profile


class ProfileCheckGroupsTests(unittest.TestCase):
    setUp = fixtures.WorkflowTests.setUp
    tearDown = fixtures.WorkflowTests.tearDown

    def config(self):
        return {"project": str(self.project),
                "checks": [[sys.executable, "-c", "import app; assert app.VALUE == 1"]],
                "check_groups": {"negative": [[sys.executable, "-c", "raise SystemExit(17)"]]},
                "workspace": {"isolate": False}}

    def test_profiled_task_resolves_only_selected_groups_and_runs_them(self):
        cfg = self.config()
        base = cfg["checks"][0]
        negative = cfg["check_groups"]["negative"][0]
        extra = [sys.executable, "-c", "print('extra')"]
        cfg["check_groups"]["ui"] = [base, extra]
        cfg["check_groups"]["unused"] = [["wrong-command-never-selected"]]
        profiles.save(self.store, "team", cfg)

        task = create_from_profile(self.store, "GROUPED", "team", "verify", ["evidence"],
                                   checks=[extra], check_groups=["ui", "ui", "negative"])
        self.assertEqual(task["checks"], [base, extra, negative])
        self.assertEqual(task["profile"]["check_groups"], ["ui", "negative"])
        cfg["check_groups"]["negative"] = [extra]
        profiles.save(self.store, "team", cfg)
        task = self.store.get("GROUPED")
        self.assertEqual(task["checks"][-1], negative)
        with self.store.lock(task["project"]) as fd:
            self.assertFalse(run_checks(self.store, task, 5, fd))
        self.assertEqual([r["exit_code"] for r in task["verification"]["results"]], [0, 0, 17])

    def test_legacy_default_preserves_repeated_checks(self):
        cfg = self.config()
        del cfg["check_groups"]
        cfg["checks"] *= 2
        saved = profiles.save(self.store, "legacy", cfg)
        self.assertNotIn("check_groups", saved["config"])
        task = create_from_profile(self.store, "OLD", "legacy", "verify", ["evidence"],
                                   checks=cfg["checks"][:1])
        self.assertEqual(task["checks"], [cfg["checks"][0]] * 3)
        self.assertNotIn("check_groups", task["profile"])
        cfg["check_groups"] = {"repeat": cfg["checks"][:1]}
        self.assertEqual(profiles.check_plan(cfg, ["repeat"])["checks"], cfg["checks"])

    def test_invalid_definitions_rejected_before_profile_replacement(self):
        cfg = self.config()
        saved = profiles.save(self.store, "team", cfg)
        for invalid in (None, [], {"bad name": [["true"]]}, {"ui": []},
                        {"ui": ["npm test"]}, {"ui": [["cmd", "&&", "cmd"]]}):
            with self.subTest(invalid=invalid), self.assertRaises(WorkflowError):
                profiles.save(self.store, "team", {**cfg, "check_groups": invalid})
        self.assertEqual(profiles.get(self.store, "team"), saved)

    def test_invalid_selection_creates_no_worktree_or_task_directory(self):
        cfg = self.config()
        cfg["workspace"]["isolate"] = True
        profiles.save(self.store, "team", cfg)
        for names in (["unknown"], "negative", [["negative"]]):
            with self.subTest(names=names), self.assertRaises(WorkflowError):
                create_from_profile(self.store, "INVALID", "team", "verify", ["x"], check_groups=names)
        self.assertFalse((self.store.home / "tasks" / "INVALID").exists())
        self.assertFalse((self.store.home / "workspaces").exists())
        self.assertNotIn("workflow/INVALID", git(self.project, "branch", "--list"))
        self.assertEqual(self.store.all(), [])

    def test_connected_cli_start_uses_registered_profile_and_rejects_unbound_groups(self):
        home = self.root / "observer"
        config = self.config()
        profiles.save(self.store, "team", config)
        connections.connect(home, self.project, [self.store.home], profile="team", criteria=["verified"],
                            include_temporary=False, include_processes=False, include_default=False)
        common = ["--home", str(home), "start", "grouped work", "--project", str(self.project),
                  "--check-group"]
        args = parser().parse_args([*common, "unknown"])
        with self.assertRaises(WorkflowError):
            dispatch(args)
        self.assertEqual(self.store.all(), [])
        task = dispatch(parser().parse_args([*common, "negative"]))["task"]
        self.assertEqual(task["checks"], config["checks"] + config["check_groups"]["negative"])
        self.assertIsNone(task["verification"])

        blank = self.root / "unbound"
        connections.connect(blank, self.project, criteria=["verified"], include_temporary=False,
                            include_processes=False, include_default=False)
        with self.assertRaisesRegex(WorkflowError, "프로필"):
            connections.start_registered(blank, "goal", self.project, check_groups=["negative"])
        self.assertFalse((blank / "state.sqlite3").exists())

    def test_readonly_lookup_of_legacy_and_grouped_profiles(self):
        read_only = Store(self.store.home, read_only=True)
        try:
            with self.assertRaisesRegex(WorkflowError, "프로필 없음"):
                profiles.get(read_only, "missing")
            cfg = self.config()
            profiles.save(self.store, "team", cfg)
            config = profiles.get(read_only, "team")["config"]
            self.assertEqual(profiles.check_plan(config, ["negative"])["checks"],
                             cfg["checks"] + cfg["check_groups"]["negative"])
        finally:
            read_only.close()
