"""Release acceptance tests: local Git effects and mocked GitHub boundaries."""
import json
import subprocess
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from agent_workflow.core import Store, WorkflowError, complete, create_task, git
from agent_workflow.runner import run_checks
from agent_workflow.release import configure, release_task, github_merge, ci_gate, clear_configuration, gh_api


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="workflow-release-test-")
        self.root = Path(self.temp.name)
        self.project = self.root / "project with spaces"
        self.project.mkdir()
        self.remote = self.root / "remote.git"
        git(self.root, "init", "--bare", str(self.remote))
        git(self.project, "init", "-b", "main")
        git(self.project, "config", "user.name", "Release Fixture")
        git(self.project, "config", "user.email", "fixture@example.invalid")
        (self.project / "app.txt").write_text("old\n")
        git(self.project, "add", ".")
        git(self.project, "commit", "-m", "baseline")
        git(self.project, "remote", "add", "origin", str(self.remote))
        git(self.project, "push", "origin", "main")
        self.baseline = git(self.project, "rev-parse", "HEAD")
        self.store = Store(self.root / "state")
        self.task = create_task(self.store, "WP-release", self.project, "ship app", ["app is new"], [],
                               [[sys.executable, "-c", "from pathlib import Path; assert Path('app.txt').read_text() == 'new\\n'"]],
                               "claude", "none", "fixture release")

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def finish(self, extra=False):
        self.task = self.store.get(self.task["id"])
        (self.project / "app.txt").write_text("new\n")
        if extra:
            (self.project / "outside.txt").write_text("not authorized")
        with self.store.lock(self.project) as fd:
            self.assertTrue(run_checks(self.store, self.task, 5, fd))
            complete(self.store, self.task)

    def configure(self, deploy=False, health=None):
        command = [sys.executable, "-c",
                   "import os,sys; from pathlib import Path; "
                   "p=Path(sys.argv[1]); p.open('a').write(os.environ['WORKFLOW_REVISION']+'\\n')",
                   str(self.root / "deployments")]
        return configure(self.store, self.task["id"], "origin", "main", "work/release", ["app.txt"],
                         mode="local", deploy=command if deploy else None,
                         health=(health or [sys.executable, "-c", "pass"]) if deploy else None)

    def test_real_commit_push_merge_deploy_and_repeat_is_idempotent(self):
        self.finish()
        self.configure(deploy=True)
        result = release_task(self.store, self.task["id"], timeout=5)["release"]
        self.assertEqual(result["stage"], "deployed")
        candidate, merged = result["commit"], result["merge_commit"]
        self.assertEqual(git(self.remote, "rev-parse", "refs/heads/work/release"), candidate)
        self.assertEqual(git(self.remote, "rev-parse", "refs/heads/main"), merged)
        self.assertEqual(git(self.remote, "show", merged + ":app.txt"), "new")
        self.assertIn(candidate, git(self.remote, "rev-list", "--parents", "-n", "1", merged).split()[1:])
        self.assertEqual((self.root / "deployments").read_text().splitlines(), [merged])
        release_task(self.store, self.task["id"], timeout=5)
        self.assertEqual(git(self.remote, "rev-parse", "refs/heads/main"), merged)
        self.assertEqual((self.root / "deployments").read_text().splitlines(), [merged])

    def test_remote_change_cannot_redirect_release(self):
        self.finish()
        self.configure()
        other = self.root / "other.git"
        git(self.root, "init", "--bare", str(other))
        git(self.project, "remote", "set-url", "origin", str(other))
        with self.assertRaises(WorkflowError):
            release_task(self.store, self.task["id"], timeout=5)
        self.assertEqual(git(self.project, "rev-parse", "HEAD"), self.baseline)

    def test_failed_deploy_is_not_repeated_without_explicit_retry(self):
        self.finish()
        marker = self.root / "fail"
        marker.write_text("fail")
        count = self.root / "apply-count"
        deploy = [sys.executable, "-c", "from pathlib import Path; import sys; "
                  "p=Path(sys.argv[1]); p.open('a').write('apply\\n'); "
                  "sys.exit(1 if Path(sys.argv[2]).exists() else 0)", str(count), str(marker)]
        configure(self.store, self.task["id"], "origin", "main", "work/release", ["app.txt"],
                  mode="local", deploy=deploy, health=[sys.executable, "-c", "pass"])
        with self.assertRaises(WorkflowError):
            release_task(self.store, self.task["id"], timeout=5)
        self.assertEqual(count.read_text().splitlines(), ["apply"])
        marker.unlink()
        with self.assertRaises(WorkflowError):
            release_task(self.store, self.task["id"], timeout=5)
        self.assertEqual(count.read_text().splitlines(), ["apply"])
        released = release_task(self.store, self.task["id"], timeout=5, retry_deploy=True)
        self.assertEqual(released["release"]["stage"], "deployed")
        self.assertEqual(count.read_text().splitlines(), ["apply", "apply"])

    def test_failed_local_merge_push_can_resume_same_commit(self):
        import agent_workflow.release as module
        self.finish()
        self.configure()
        original_git = module.git
        def fail_base_push(project, *args, **kwargs):
            if args[0] == "push" and str(args[-1]).endswith(":refs/heads/main"):
                raise WorkflowError("simulated transport failure")
            return original_git(project, *args, **kwargs)
        with patch.object(module, "git", side_effect=fail_base_push):
            with self.assertRaises(WorkflowError):
                release_task(self.store, self.task["id"], timeout=5)
        candidate = self.store.get(self.task["id"])["release"]["local_merge_candidate"]
        result = release_task(self.store, self.task["id"], timeout=5)
        self.assertEqual(result["release"]["merge_commit"], candidate)
        self.assertEqual(git(self.remote, "rev-parse", "main"), candidate)

    def test_changes_outside_allowlist_are_not_committed(self):
        self.finish(extra=True)
        self.configure()
        with self.assertRaises(WorkflowError):
            release_task(self.store, self.task["id"], timeout=5)
        self.assertEqual(git(self.project, "rev-parse", "HEAD"), self.baseline)
        self.assertEqual(git(self.remote, "rev-parse", "main"), self.baseline)

    def test_staged_content_hidden_by_worktree_cannot_escape_allowlist(self):
        # The file is tracked in the clean task baseline; only its index version changes.
        other = self.project / "outside.txt"
        other.write_text("baseline")
        git(self.project, "add", "outside.txt")
        git(self.project, "commit", "-m", "fixture second file")
        git(self.project, "push", "origin", "main")
        self.baseline = git(self.project, "rev-parse", "HEAD")
        self.task = create_task(self.store, "WP-index", self.project, "ship app", ["app is new"], [],
                               self.task["checks"], "claude", "none", "fixture release")
        other.write_text("unapproved staged content")
        git(self.project, "add", "outside.txt")
        other.write_text("baseline")
        self.finish()
        self.configure()
        with self.assertRaises(WorkflowError):
            release_task(self.store, self.task["id"], timeout=5)
        self.assertEqual(git(self.project, "rev-parse", "HEAD"), self.baseline)
        self.assertEqual(git(self.remote, "rev-parse", "main"), self.baseline)

    def test_changed_code_after_completion_is_not_committed(self):
        self.finish()
        self.configure()
        (self.project / "app.txt").write_text("stale")
        with self.assertRaises(WorkflowError):
            release_task(self.store, self.task["id"], timeout=5)
        self.assertEqual(git(self.project, "rev-parse", "HEAD"), self.baseline)

    def test_unsafe_path_and_wrong_github_repo_rejected(self):
        for name in ("../escape", "/tmp/escape", ".", ".git/config", ":(glob)**"):
            with self.subTest(path=name), self.assertRaises(WorkflowError):
                configure(self.store, self.task["id"], "origin", "main", "work/release", [name], mode="local")
        with self.assertRaises(WorkflowError):
            configure(self.store, self.task["id"], "origin", "main", "work/release", ["app.txt"],
                      mode="github", repo="owner/repo")

    def test_health_retry_does_not_repeat_successful_deploy(self):
        self.finish()
        marker = self.root / "healthy"
        self.configure(deploy=True, health=[sys.executable, "-c", "import sys; from pathlib import Path; sys.exit(not Path(sys.argv[1]).exists())", str(marker)])
        with self.assertRaises(WorkflowError):
            release_task(self.store, self.task["id"], timeout=5)
        self.assertEqual(len((self.root / "deployments").read_text().splitlines()), 1)
        marker.touch()
        result = release_task(self.store, self.task["id"], timeout=5)
        self.assertEqual(result["release"]["stage"], "deployed")
        self.assertEqual(len((self.root / "deployments").read_text().splitlines()), 1)

    def github_task(self):
        self.task["release"] = {"stage": "pull_request", "pr": 7, "commit": "a" * 40,
                                "config": {"repo": "owner/repo", "base": "main", "branch": "work/release", "allow_no_ci": False}}
        self.store.save(self.task, "test_fixture")
        return self.task

    def pr(self, **extra):
        return {"head": {"sha": "a" * 40, "ref": "work/release"}, "base": {"ref": "main"},
                "state": "open", "draft": False, "mergeable": True, "mergeable_state": "clean", **extra}

    def test_github_head_mismatch_never_merges(self):
        task = self.github_task()
        with patch("agent_workflow.release.gh_api", return_value=self.pr(head={"sha": "b" * 40, "ref": "work/release"})) as api:
            with self.assertRaises(WorkflowError):
                github_merge(self.store, task, 1)
        self.assertFalse(any(len(c.args) > 1 and c.args[1] == "PUT" for c in api.call_args_list))

    def test_github_failed_ci_never_merges(self):
        task = self.github_task()
        def api(endpoint, method="GET", payload=None, **kwargs):
            self.assertEqual(method, "GET", "must not mutate GitHub after failed CI")
            if endpoint.endswith("/pulls/7"):
                return self.pr()
            if endpoint.endswith("/status"):
                return {"total_count": 1, "state": "failure"}
            return {"check_runs": []}
        with patch("agent_workflow.release.gh_api", side_effect=api), self.assertRaises(WorkflowError):
            github_merge(self.store, task, 1)

    def test_github_merge_is_pinned_to_reviewed_sha(self):
        task = self.github_task()
        writes = []
        def api(endpoint, method="GET", payload=None, **kwargs):
            if method == "PUT":
                writes.append((endpoint, payload))
                return {"merged": True, "sha": "c" * 40}
            if endpoint.endswith("/pulls/7"):
                return self.pr()
            if endpoint.endswith("/status"):
                return {"total_count": 1, "state": "success"}
            return {"check_runs": [{"name": "test", "status": "completed", "conclusion": "success"}]}
        with patch("agent_workflow.release.gh_api", side_effect=api):
            github_merge(self.store, task, 1)
        self.assertEqual(writes, [("repos/owner/repo/pulls/7/merge", {"sha": "a" * 40, "merge_method": "merge"})])
        self.assertEqual(task["release"]["merge_commit"], "c" * 40)

    def test_missing_ci_requires_explicit_exception(self):
        def api(endpoint, *args):
            return {"total_count": 0} if endpoint.endswith("/status") else {"check_runs": []}
        with patch("agent_workflow.release.gh_api", side_effect=api):
            self.assertFalse(ci_gate("owner/repo", "a" * 40, False))
            self.assertTrue(ci_gate("owner/repo", "a" * 40, True))

    def test_release_configuration_can_only_be_cleared_before_start(self):
        self.configure()
        self.assertIsNone(clear_configuration(self.store, self.task["id"])["release"])
        self.finish(); self.configure(); release_task(self.store, self.task["id"], timeout=5)
        with self.assertRaises(WorkflowError):
            clear_configuration(self.store, self.task["id"])

    def test_enterprise_hostname_is_pinned_for_remote_and_api(self):
        git(self.project, "remote", "set-url", "origin", "https://git.example.invalid/team/project.git")
        configured = configure(self.store, self.task["id"], "origin", "main", "work/release", ["app.txt"],
                               repo="team/project", hostname="git.example.invalid")
        self.assertEqual(configured["config"]["hostname"], "git.example.invalid")
        with self.assertRaises(WorkflowError):
            configure(self.store, self.task["id"], "origin", "main", "work/release", ["app.txt"], repo="team/project", hostname="other.invalid")
        with patch("agent_workflow.release.subprocess.run", return_value=subprocess.CompletedProcess([], 0, '{}', '')) as call:
            gh_api("repos/team/project", hostname="git.example.invalid")
        args = call.call_args.args[0]
        self.assertEqual(args[args.index("--hostname") + 1], "git.example.invalid")


if __name__ == "__main__":
    unittest.main()
