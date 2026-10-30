"""Release fault injection with real local Git effects and command processes."""
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import test_release as fixtures
from agent_workflow.core import WorkflowError, create_task, git, set_hold, clear_hold
from agent_workflow.release import configure, release_task
from agent_workflow.scheduling import dependency_evidence, set_dependencies, task_done


class ReleaseHardeningTests(unittest.TestCase):
    setUp = fixtures.ReleaseTests.setUp
    tearDown = fixtures.ReleaseTests.tearDown
    finish = fixtures.ReleaseTests.finish
    configure = fixtures.ReleaseTests.configure

    def incompatible_base(self):
        (self.project / "mode.txt").write_text("compatible\n")
        git(self.project, "add", "mode.txt")
        git(self.project, "commit", "-m", "integration contract")
        git(self.project, "push", "origin", "main")
        check = [sys.executable, "-c", "from pathlib import Path; "
                 "assert Path('app.txt').read_text() == 'new\\n'; "
                 "assert Path('mode.txt').read_text() == 'compatible\\n'"]
        self.task = create_task(self.store, "WP-integration", self.project, "ship", ["compatible"],
                                [], [check], "claude", "none", "app.txt")
        self.finish()
        self.configure()
        other = self.root / "parallel"
        git(self.root, "clone", "--branch", "main", str(self.remote), str(other))
        git(other, "config", "user.name", "Fixture")
        git(other, "config", "user.email", "fixture@example.invalid")
        (other / "mode.txt").write_text("incompatible\n")
        git(other, "add", "mode.txt")
        git(other, "commit", "-m", "independent base change")
        git(other, "push", "origin", "main")
        return git(self.remote, "rev-parse", "main")

    def test_failed_integrated_code_never_reaches_local_base(self):
        base = self.incompatible_base()
        with self.assertRaisesRegex(WorkflowError, "검사 실패"):
            release_task(self.store, self.task["id"], timeout=5)
        task = self.store.get(self.task["id"])
        self.assertEqual(git(self.remote, "rev-parse", "main"), base)
        self.assertFalse(task["release"].get("merge_commit"))
        self.assertFalse(task_done(task))
        self.assertFalse(task["release"]["integration_verification"]["passed"])

    def test_check_cannot_commit_different_head_and_deploy_it(self):
        self.task["checks"] = [[sys.executable, "-c", """
import os, subprocess
from pathlib import Path
assert Path('app.txt').read_text() == 'new\\n'
if os.environ.get('WORKFLOW_REVISION'):
    Path('app.txt').write_text('unreviewed\\n')
    subprocess.run(['git', 'add', 'app.txt'], check=True)
    subprocess.run(['git', '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
                    'commit', '-qm', 'unreviewed check output'], check=True)
"""]]
        self.store.save(self.task, "fixture_checks")
        self.finish()
        self.configure(deploy=True)
        with self.assertRaisesRegex(WorkflowError, "변경"):
            release_task(self.store, self.task["id"], timeout=5)
        self.assertFalse((self.root / "deployments").exists())
        self.assertEqual(git(self.remote, "rev-parse", "main"), self.baseline)
        self.assertNotEqual(self.store.get(self.task["id"])["release"]["stage"], "deployed")

    def test_merge_receipt_crash_reconciles_owned_clean_merge(self):
        import agent_workflow.release as module
        self.finish()
        self.configure()
        original = module.save_release
        killed = False
        def crash(store, task, stage=None, **fields):
            nonlocal killed
            if "local_merge_candidate" in fields and not killed:
                killed = True
                raise KeyboardInterrupt("kill between merge and receipt")
            return original(store, task, stage, **fields)
        with patch.object(module, "save_release", side_effect=crash):
            with self.assertRaises(WorkflowError):
                release_task(self.store, self.task["id"], timeout=5)
        first = self.store.get(self.task["id"])["release"]
        self.assertIsNone(first.get("local_merge_candidate"))
        self.assertEqual(git(self.remote, "rev-parse", "main"), self.baseline)
        result = release_task(self.store, self.task["id"], timeout=5)
        self.assertEqual(result["release"]["stage"], "merged")
        self.assertTrue(task_done(result))
        self.assertEqual(result["release"]["integration_verification"]["revision"],
                         git(self.remote, "rev-parse", "main"))

    def test_window_closing_after_integration_prevents_push_and_deploy(self):
        from agent_workflow.usage import managed_process
        self.finish()
        self.configure(deploy=True)
        closed = False
        commands_after_close = []
        def window(config):
            if closed:
                raise WorkflowError("test window closed")
            return 300
        def command(*args, **kwargs):
            nonlocal closed
            if closed:
                commands_after_close.append(kwargs.get("role"))
            result = managed_process(*args, **kwargs)
            if kwargs.get("role") == "integration_check":
                closed = True
            return result
        with patch("agent_workflow.policy.window_limit", side_effect=window), \
                patch("agent_workflow.usage.managed_process", side_effect=command):
            with self.assertRaisesRegex(WorkflowError, "window closed"):
                release_task(self.store, self.task["id"], timeout=5)
        self.assertEqual(commands_after_close, [])
        self.assertEqual(git(self.remote, "rev-parse", "main"), self.baseline)
        self.assertFalse((self.root / "deployments").exists())

    def test_window_closing_after_deploy_preserves_apply_and_resumes_health_only(self):
        from agent_workflow.usage import managed_process
        self.finish()
        self.configure(deploy=True)
        closed = False
        def window(config):
            if closed:
                raise WorkflowError("test window closed")
            return 300
        def command(*args, **kwargs):
            nonlocal closed
            result = managed_process(*args, **kwargs)
            if kwargs.get("role") == "deploy":
                closed = True
            return result
        with patch("agent_workflow.policy.window_limit", side_effect=window), \
                patch("agent_workflow.usage.managed_process", side_effect=command):
            with self.assertRaisesRegex(WorkflowError, "window closed"):
                release_task(self.store, self.task["id"], timeout=5)
        state = self.store.get(self.task["id"])["release"]
        self.assertEqual(state["deployment_state"], "applied")
        self.assertNotEqual(state["stage"], "deployed")
        result = release_task(self.store, self.task["id"], timeout=5)
        self.assertEqual(result["release"]["stage"], "deployed")
        self.assertEqual(len((self.root / "deployments").read_text().splitlines()), 1)

    def test_unknown_deployment_receipt_is_not_automatically_repeated(self):
        import agent_workflow.release as module
        self.finish()
        self.configure(deploy=True)
        original = module.save_release
        def crash(store, task, stage=None, **fields):
            if "deployment_result" in fields:
                raise KeyboardInterrupt("kill after external apply")
            return original(store, task, stage, **fields)
        with patch.object(module, "save_release", side_effect=crash):
            with self.assertRaises(WorkflowError):
                release_task(self.store, self.task["id"], timeout=5)
        self.assertEqual(self.store.get(self.task["id"])["release"]["deployment_state"], "started")
        with self.assertRaisesRegex(WorkflowError, "retry-deploy"):
            release_task(self.store, self.task["id"], timeout=5)
        self.assertEqual(len((self.root / "deployments").read_text().splitlines()), 1)

    def test_merge_only_success_has_revision_bound_integration_evidence(self):
        self.finish()
        self.configure()
        task = release_task(self.store, self.task["id"], timeout=5)
        receipt = task["release"].get("integration_verification") or {}
        self.assertTrue(receipt.get("passed"))
        self.assertEqual(receipt.get("revision"), task["release"]["merge_commit"])
        self.assertTrue(task_done(task))
        receipt["revision"] = "0" * 40
        self.assertFalse(task_done(task))

    def test_merged_dependency_requires_integration_evidence(self):
        self.finish()
        self.configure()
        parent = release_task(self.store, self.task["id"], timeout=5)
        child = create_task(self.store, "CHILD", self.project, "child", ["child"], [],
                            [[sys.executable, "-c", "pass"]], "claude", "none", "fixture")
        set_dependencies(self.store, child["id"], [parent["id"]], mode="merged")
        child = self.store.get(child["id"])
        self.assertTrue(dependency_evidence(self.store, child))
        parent["release"].pop("integration_verification", None)
        self.store.save(parent, "fixture_missing_evidence")
        with self.assertRaisesRegex(WorkflowError, "통합"):
            dependency_evidence(self.store, child)

    def test_github_merge_effect_survives_failed_post_merge_verification(self):
        import agent_workflow.release as module
        base = self.incompatible_base()
        task = self.store.get(self.task["id"])
        task["release"]["config"]["mode"] = "github"
        self.store.save(task, "fixture_github_boundary")
        def merged_remotely(store, task, timeout, budget=None):
            # This local remote stands in only for the irreversible GitHub merge effect.
            work = self.root / "github-merged"
            git(self.project, "fetch", "origin", "main")
            git(self.project, "worktree", "add", "--detach", str(work), base)
            git(work, "merge", "--no-ff", "-m", "remote merge", task["release"]["commit"])
            sha = git(work, "rev-parse", "HEAD")
            git(work, "push", "origin", sha + ":refs/heads/main")
            module.save_release(store, task, "merged", merge_commit=sha)
        with patch.object(module, "github_merge", side_effect=merged_remotely):
            with self.assertRaisesRegex(WorkflowError, "검사 실패"):
                release_task(self.store, task["id"], timeout=5)
        after = self.store.get(task["id"])
        self.assertEqual(after["release"]["stage"], "merged")
        self.assertEqual(after["release"]["merge_commit"], git(self.remote, "rev-parse", "main"))
        self.assertNotEqual(after["release"]["merge_commit"], base)
        self.assertFalse(after["release"]["integration_verification"]["passed"])
        self.assertFalse(task_done(after))

    def test_hold_after_integration_stops_base_push_until_explicit_unhold(self):
        from agent_workflow.usage import managed_process
        self.finish()
        self.configure(deploy=True)
        def command(*args, **kwargs):
            result = managed_process(*args, **kwargs)
            if kwargs.get("role") == "integration_check":
                set_hold(self.store, self.task["id"], "inspect integration")
            return result
        with patch("agent_workflow.usage.managed_process", side_effect=command):
            with self.assertRaisesRegex(WorkflowError, "보류"):
                release_task(self.store, self.task["id"], timeout=5)
        self.assertEqual(git(self.remote, "rev-parse", "main"), self.baseline)
        self.assertFalse((self.root / "deployments").exists())
        clear_hold(self.store, self.task["id"])
        result = release_task(self.store, self.task["id"], timeout=5)
        self.assertEqual(result["release"]["stage"], "deployed")

    def test_release_timeout_is_shared_across_commands(self):
        import agent_workflow.release as module
        from agent_workflow.usage import managed_process
        self.task["checks"] *= 2
        self.store.save(self.task, "fixture_two_checks")
        self.finish()
        self.configure()
        elapsed = 0
        limits = []
        def command(*args, **kwargs):
            nonlocal elapsed
            limits.append(args[5])
            result = managed_process(*args, **kwargs)
            elapsed += 3
            return result
        with patch.object(module, "time", SimpleNamespace(monotonic=lambda: elapsed)), \
                patch("agent_workflow.usage.managed_process", side_effect=command):
            with self.assertRaisesRegex(WorkflowError, "시간 한도"):
                release_task(self.store, self.task["id"], timeout=5)
        self.assertEqual(limits, [5, 2])
        self.assertEqual(git(self.remote, "rev-parse", "main"), self.baseline)

    def test_deploy_changing_checkout_cannot_record_successful_revision(self):
        self.finish()
        deploy = [sys.executable, "-c", """
from pathlib import Path
import subprocess
Path('app.txt').write_text('unexpected deploy edit\\n')
subprocess.run(['git', 'add', 'app.txt'], check=True)
subprocess.run(['git', '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
                'commit', '-qm', 'deploy changed HEAD'], check=True)
"""]
        configure(self.store, self.task["id"], "origin", "main", "work/release", ["app.txt"],
                  mode="local", deploy=deploy, health=[sys.executable, "-c", "pass"])
        with self.assertRaisesRegex(WorkflowError, "변경"):
            release_task(self.store, self.task["id"], timeout=5)
        after = self.store.get(self.task["id"])["release"]
        self.assertEqual(after["deployment_state"], "applied")
        self.assertNotEqual(after["stage"], "deployed")
        self.assertIsNone(after.get("deployed_revision"))

    def test_crash_recovery_preserves_manual_changes_in_merge_worktree(self):
        import agent_workflow.release as module
        self.finish()
        self.configure()
        original = module.save_release
        def crash(store, task, stage=None, **fields):
            if "local_merge_candidate" in fields:
                raise KeyboardInterrupt("kill before receipt")
            return original(store, task, stage, **fields)
        with patch.object(module, "save_release", side_effect=crash):
            with self.assertRaises(WorkflowError):
                release_task(self.store, self.task["id"], timeout=5)
        state = self.store.get(self.task["id"])["release"]
        work = Path(state["local_merge_intent"]["worktree"])
        (work / "app.txt").write_text("user edit\n")
        with self.assertRaisesRegex(WorkflowError, "변경"):
            release_task(self.store, self.task["id"], timeout=5)
        self.assertEqual((work / "app.txt").read_text(), "user edit\n")
        self.assertEqual(git(self.remote, "rev-parse", "main"), self.baseline)

    def test_reference_change_blocks_resuming_an_already_committed_release(self):
        import agent_workflow.release as module
        reference = self.root / "requirements.md"
        reference.write_text("original requirement")
        self.task["refs"] = [str(reference)]
        self.store.save(self.task, "fixture_reference")
        self.finish()
        self.configure()
        original_git = module.git
        def fail_base_push(project, *args, **kwargs):
            if args[0] == "push" and str(args[-1]).endswith(":refs/heads/main"):
                raise WorkflowError("temporary transport failure")
            return original_git(project, *args, **kwargs)
        with patch.object(module, "git", side_effect=fail_base_push):
            with self.assertRaises(WorkflowError):
                release_task(self.store, self.task["id"], timeout=5)
        self.assertTrue(self.store.get(self.task["id"])["release"]["commit"])
        reference.write_text("changed requirement")
        with self.assertRaisesRegex(WorkflowError, "참조 문서"):
            release_task(self.store, self.task["id"], timeout=5)
        self.assertEqual(git(self.remote, "rev-parse", "main"), self.baseline)

    def test_integrated_checks_cannot_modify_ignored_forbidden_paths(self):
        from agent_workflow.policy import protected_snapshot
        (self.project / ".git" / "info" / "exclude").write_text(".env\n")
        self.task["project_config"] = {"forbidden_paths": [".env"]}
        self.task["baseline"]["protected_paths"] = protected_snapshot(self.project, [".env"])
        self.task["checks"] = [[sys.executable, "-c", "import os; from pathlib import Path; "
                               "assert Path('app.txt').read_text() == 'new\\n'; "
                               "Path('.env').write_text('dummy fixture value') if os.environ.get('WORKFLOW_REVISION') else None"]]
        self.store.save(self.task, "fixture_forbidden_path")
        self.finish()
        self.configure()
        with self.assertRaisesRegex(WorkflowError, "금지 경로"):
            release_task(self.store, self.task["id"], timeout=5)
        self.assertEqual(git(self.remote, "rev-parse", "main"), self.baseline)
        self.assertFalse(self.store.get(self.task["id"])["release"]["integration_verification"]["passed"])

    def test_legacy_saved_merge_candidate_is_rechecked_and_resumed(self):
        import agent_workflow.release as module
        self.finish()
        self.configure()
        original_git = module.git
        def fail_base_push(project, *args, **kwargs):
            if args[0] == "push" and str(args[-1]).endswith(":refs/heads/main"):
                raise WorkflowError("temporary transport failure")
            return original_git(project, *args, **kwargs)
        with patch.object(module, "git", side_effect=fail_base_push):
            with self.assertRaises(WorkflowError):
                release_task(self.store, self.task["id"], timeout=5)
        old = self.store.get(self.task["id"])
        candidate = old["release"]["local_merge_candidate"]
        old["release"].pop("local_merge_intent", None)
        old["release"].pop("integration_verification", None)
        self.store.save(old, "fixture_v03_receipt")
        result = release_task(self.store, old["id"], timeout=5)
        self.assertEqual(result["release"]["merge_commit"], candidate)
        self.assertTrue(result["release"]["integration_verification"]["passed"])
        self.assertTrue(task_done(result))


if __name__ == "__main__":
    unittest.main()
