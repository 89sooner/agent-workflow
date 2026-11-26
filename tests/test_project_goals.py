import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import test_workflow as fixtures
from test_katakuri_goal import MODEL, review, step

from agent_workflow import goal_policy, goals, profiles
from agent_workflow.cli import dispatch, parser
from agent_workflow.core import WorkflowError, git


class ProjectGoalTests(unittest.TestCase):
    tearDown = fixtures.WorkflowTests.tearDown

    def setUp(self):
        fixtures.WorkflowTests.setUp(self)
        guide = self.project / "GUIDE.md"
        guide.write_text("Local development with the registered checks.")
        git(self.project, "add", "GUIDE.md")
        git(self.project, "-c", "user.name=Fixture", "-c", "user.email=p@example.invalid",
            "commit", "-qm", "project instructions")
        self.cfg = {"project": str(self.project), "owner": "claude", "reviewer": "codex-session",
                    "allowed_paths": ["app.py"], "checks": [[sys.executable, "-c",
                        "from pathlib import Path; assert Path('app.py').read_text() == 'done'"]],
                    "goal_mode": {"version": 1, "scope": "Change the local Python application.",
                                  "instructions": ["GUIDE.md"], "input_extensions": [".json"]}}
        profiles.save(self.store, "project", self.cfg)
        bin_dir = self.root / "bin"
        bin_dir.mkdir()
        for name in ("claude", "codex"):
            tool = bin_dir / name
            tool.write_text(MODEL)
            tool.chmod(0o700)
        env = patch.dict(os.environ, KG_CASE=str(self.root), PATH=str(bin_dir) + os.pathsep + os.environ["PATH"])
        env.start()
        self.addCleanup(env.stop)

    def cli(self, *args):
        return dispatch(parser().parse_args(["--home", str(self.store.home), *args]))

    def create(self, *args):
        return self.cli("goal", "create", "GEN", "--profile", "project", "--goal", "Update app.py", *args)

    def test_plan_then_resume_using_frozen_policy_and_same_review_gate(self):
        result = self.create()
        work = Path(result["project"])
        self.assertEqual((result["kind"], result["phase"]), ("project", "planning"))
        self.assertFalse(any((work / name).exists() for name in goal_policy.KATAKURI_INSTRUCTIONS))
        self.assertEqual(self.store.get("GEN")["refs"], [str(work / "GUIDE.md")])
        (self.root / "answers").write_text(json.dumps([{"criteria": ["Application updated", "Observation received"]},
                                                       step(1, edit="done"), step(2, "waiting")]))
        with self.assertRaises(WorkflowError):
            self.cli("goal", "run", "GEN")
        waiting = self.cli("goal", "status", "GEN")
        self.assertEqual(waiting["phase"], "waiting")
        self.cfg["goal_mode"]["input_extensions"] = [".txt"]
        profiles.save(self.store, "project", self.cfg)
        self.assertEqual(self.cli("goal", "status", "GEN")["input_extensions"], [".json"])
        with self.assertRaises(WorkflowError):
            self.cli("katakuri-goal", "budget", "GEN", "--max-agent-calls", "30")
        with self.assertRaises(WorkflowError):
            goals.supply_text(self.store, "GEN", 2, "plain text", extension=".txt")
        doc = self.root / "input.json"
        doc.write_text('{"observation":"OK"}')
        self.cli("goal", "supply", "GEN", "--criterion", "2", "--ref", str(doc))
        (self.root / "answers").write_text(json.dumps([step(2), review(True, True)]))
        done = self.cli("goal", "run", "GEN")
        self.assertEqual((done["phase"], done["status"], done["agent_calls"]), ("complete", "completed", 5))
        self.assertEqual(done["policy_digest"], result["policy_digest"])
        packet = self.cli("resume-view", "GEN")
        self.assertEqual(packet["remaining_criteria"], [])
        cites = {row["source"] for row in packet["valid_evidence"]}
        self.assertIn("/goal_mode/audit/rows/0", cites)
        self.assertIn("/goal_mode/audit/rows/1", cites)
        self.assertEqual((work / "app.py").read_text(), "done")
        self.assertNotEqual((self.project / "app.py").read_text(), "done")
        with self.assertRaises(WorkflowError):
            self.cli("release-config", "GEN", "--branch", "work/demo", "--path", "app.py")
        with self.assertRaisesRegex(WorkflowError, "로컬 개발 목표"):
            self.cli("release", "GEN")

    def test_missing_worktree_instruction_cannot_create_a_task(self):
        (self.project / "NEW.md").write_text("uncommitted instructions")
        self.cfg["goal_mode"]["instructions"] = ["NEW.md"]
        profiles.save(self.store, "project", self.cfg)
        with self.assertRaisesRegex(WorkflowError, "파일"):
            self.create("--criterion", "One")
        self.assertEqual(self.store.all(), [])
        self.assertFalse((self.store.home / "workspaces/project/GEN").exists())

    def test_generic_goal_worktree_respects_repository_checkout_line_endings(self):
        git(self.project, "config", "core.autocrlf", "true")
        with patch.dict(os.environ, {"GIT_CONFIG_COUNT": "0"}):
            work = Path(self.create("--criterion", "One")["project"])
        self.assertEqual((work / "app.py").read_bytes(), b"VALUE = 1\r\n")

    def test_goal_requires_explicit_scope_checks_and_independent_reviewer(self):
        for overrides in ({"allowed_paths": ["."]}, {"reviewer": "none"}, {"checks": []}):
            with self.subTest(overrides=overrides):
                profiles.save(self.store, "project", {**self.cfg, **overrides})
                with self.assertRaises(WorkflowError):
                    self.create("--criterion", "One")
        with self.assertRaises(WorkflowError):
            profiles.save(self.store, "project", {**self.cfg, "goal_mode": {**self.cfg["goal_mode"], "version": 2}})
        profiles.save(self.store, "project", self.cfg)
        self.create("--criterion", "One")
        task = self.store.get("GEN")
        task["project_config"]["goal_mode"]["scope"] = "different"
        with self.assertRaises(WorkflowError):
            goals.contract(task)
        with self.assertRaises(WorkflowError):
            goal_policy.state({**task, "katakuri_goal": {}})
