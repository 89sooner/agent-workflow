"""Release recovery at the boundary between an applied effect and its lost acknowledgement."""
from copy import deepcopy
from pathlib import Path
from subprocess import CompletedProcess
import sys
import unittest
from unittest.mock import patch

import test_release as fixtures
from agent_workflow.core import WorkflowError, git, set_hold
from agent_workflow import release


class GitHubFixture:
    """HTTP responses have GitHub's PR identity fields. Git effects use a private bare remote."""

    def __init__(self, case):
        self.case = case
        self.pr = None
        self.pending_view = None
        self.extra = []
        self.posts = self.puts = 0

    def __call__(self, endpoint, method="GET", payload=None, **kwargs):
        if method == "POST":
            self.posts += 1
            self.pr = {
                "number": 17, "state": "open", "mergeable": True, "mergeable_state": "clean",
                "head": {"ref": payload["head"], "sha": git(self.case.remote, "rev-parse", payload["head"]),
                         "repo": {"full_name": "team/project"}},
                "base": {"ref": payload["base"], "repo": {"full_name": "team/project"}},
                "html_url": "https://git.example.invalid/team/project/pull/17", "merged": False,
            }
            raise TimeoutError("lost PR create response")
        if method == "PUT":
            self.puts += 1
            project = self.case.root / "host-merge"
            git(self.case.root, "clone", "-qb", "main", str(self.case.remote), str(project))
            git(project, "-c", "user.name=GitHub", "-c", "user.email=host@example.invalid",
                "merge", "--no-ff", "-qm", "server merged #17", "origin/" + self.pr["head"]["ref"])
            revision = git(project, "rev-parse", "HEAD")
            git(project, "push", "origin", revision + ":refs/heads/main", ":refs/heads/" + self.pr["head"]["ref"])
            self.pending_view = deepcopy(self.pr)
            self.pr.update(state="closed", merged=True, merge_commit_sha=revision)
            raise TimeoutError("lost GitHub merge response")
        if "/pulls?" in endpoint:
            return ([deepcopy(self.pr)] if self.pr else []) + self.extra
        if endpoint.endswith("/pulls/17"):
            return deepcopy(self.pending_view or self.pr)
        if endpoint.endswith("/status"):
            return {"total_count": 1, "state": "success"}
        if "/check-runs?" in endpoint:
            return {"check_runs": []}
        raise AssertionError(endpoint)


class ReleaseResumeTests(unittest.TestCase):
    setUp = fixtures.ReleaseTests.setUp
    tearDown = fixtures.ReleaseTests.tearDown
    finish = fixtures.ReleaseTests.finish

    def run_release(self):
        return release.release_task(self.store, self.task["id"], timeout=10)

    def diagnosis(self):
        from agent_workflow.cli import dispatch, parser
        # Status must be a read of the ledger even when a release effect is indeterminate.
        with patch.object(release, "github_api", side_effect=AssertionError("unexpected remote read")), \
                patch.object(release, "release_command", side_effect=AssertionError("unexpected observer")):
            return dispatch(parser().parse_args(["--home", str(self.store.home),
                                                 "release-status", self.task["id"]]))["recovery"]

    def test_lost_pr_creation_and_merge_responses(self):
        self.finish()
        fixtures.ReleaseTests.configure(self)
        task = self.store.get(self.task["id"])
        # Keep the real local Git transport. Replace only the pull-request API with a stateful fixture.
        task["release"]["config"].update(mode="github", repo="team/project")
        self.store.save(task, "fixture_github_api")
        api = GitHubFixture(self)
        with patch.object(release, "github_api", side_effect=lambda host, *a, **kw: api(*a, **kw)):
            with self.assertRaisesRegex(WorkflowError, "lost PR create"):
                self.run_release()
            first = self.diagnosis()
            self.assertEqual(first["action"], "reconcile_pr")
            self.assertIn(api.pr["head"]["sha"], " ".join(first["confirmed_effects"]))
            self.assertIn("조회", first["next_action"])

            original = api.pr
            api.pr = deepcopy(original)
            api.pr["head"]["repo"]["full_name"] = "fork/project"
            with self.assertRaisesRegex(WorkflowError, "확인되지"):
                self.run_release()
            api.pr = original
            api.extra = [{**original, "number": 18}]
            with self.assertRaisesRegex(WorkflowError, "여러 개"):
                self.run_release()
            api.extra = []
            with self.assertRaisesRegex(WorkflowError, "lost GitHub merge"):
                self.run_release()
            second = self.diagnosis()
            self.assertEqual(second["action"], "reconcile_merge")
            self.assertIn("#17", " ".join(second["confirmed_effects"]))
            self.assertIn("병합", second["next_action"])
            with self.assertRaisesRegex(WorkflowError, "병합 요청의 결과가 확인되지"):
                self.run_release()  # GitHub read replica still reports the old open state.
            api.pending_view = None
            done = self.run_release()["release"]
            self.run_release()
        self.assertEqual((api.posts, api.puts), (1, 1))
        self.assertEqual(git(self.remote, "rev-parse", "main"), done["merge_commit"])
        self.assertEqual(git(self.remote, "branch", "--list", "work/release"), "")
        self.assertEqual(done["reconciled_effects"]["pull_request"]["pr"], 17)
        self.assertEqual(done["reconciled_effects"]["merge"]["merge_commit"], done["merge_commit"])
        self.assertTrue(done["integration_verification"]["passed"])
        self.assertEqual(self.diagnosis()["action"], "complete")
        self.replay_evidence = {"after_create_response_loss": first, "after_merge_response_loss": second,
                                "after_repeated_resume": self.diagnosis(), "create_requests": api.posts,
                                "merge_requests": api.puts, "remote_merge_commit": done["merge_commit"]}

    def configure_target(self, observer):
        self.finish()
        self.target = self.root / "deployed-sha"
        self.applies = self.root / "deploy-calls"
        self.lookups = self.root / "version-lookups"
        self.health = self.root / "health-calls"
        record = "from pathlib import Path; import sys,os; Path(sys.argv[1]).open('a').write('call\\n'); "
        deploy = [sys.executable, "-c", record + "Path(sys.argv[2]).write_text(os.environ['WORKFLOW_REVISION'])",
                  str(self.applies), str(self.target)]
        health = [sys.executable, "-c", record + "assert Path(sys.argv[2]).read_text() == os.environ['WORKFLOW_REVISION']",
                  str(self.health), str(self.target)]
        observe = [sys.executable, "-c", record + "print(Path(sys.argv[2]).read_text())",
                   str(self.lookups), str(self.target)]
        release.configure(self.store, self.task["id"], "origin", "main", "work/release", ["app.txt"], mode="local",
                          deploy=deploy, health=health, observe_revision=observe if observer else None)

    def lose_deploy_response(self):
        save = release.save_release

        def crash(store, task, stage=None, **fields):
            if "deployment_result" in fields:
                raise KeyboardInterrupt("lost deployment acknowledgement")
            return save(store, task, stage, **fields)

        with patch.object(release, "save_release", side_effect=crash), self.assertRaisesRegex(WorkflowError, "lost deployment"):
            self.run_release()

    def counts(self):
        return {key: len(p.read_text().splitlines()) if p.exists() else 0 for key, p in
                (("deploy", self.applies), ("observe_revision", self.lookups), ("health", self.health))}

    def test_unknown_deployment_reconciles_version_before_health(self):
        self.configure_target(observer=True)
        self.lose_deploy_response()
        expected = self.target.read_text()
        first = self.diagnosis()
        self.assertEqual(first["action"], "observe_deployment")
        self.assertIn(expected, " ".join(first["confirmed_effects"]))
        self.assertEqual(self.counts(), {"deploy": 1, "observe_revision": 0, "health": 0})
        self.target.write_text("0" * 40)
        with self.assertRaisesRegex(WorkflowError, "일치"):
            self.run_release()
        mismatch = self.diagnosis()
        self.assertIn("0" * 40, " ".join(mismatch["confirmed_effects"]))
        self.assertEqual(mismatch["action"], "confirm_deployment")
        self.assertEqual(self.counts(), {"deploy": 1, "observe_revision": 1, "health": 0})
        self.target.write_text(expected)
        done = self.run_release()["release"]
        self.run_release()
        self.assertEqual(done["deployed_revision"], expected)
        self.assertEqual(done["reconciled_effects"]["deployment"]["revision"], expected)
        self.assertEqual(self.counts(), {"deploy": 1, "observe_revision": 2, "health": 1})
        self.replay_evidence = {"after_acknowledgement_loss": first, "different_observed_revision": mismatch,
                                "after_repeated_resume": self.diagnosis(), "target_revision": expected,
                                "calls": self.counts()}

    def test_unknown_deployment_requires_confirmation_without_observer(self):
        self.configure_target(observer=False)
        self.lose_deploy_response()
        for _ in range(2):
            with self.assertRaisesRegex(WorkflowError, "retry-deploy"):
                self.run_release()
        diagnosis = self.diagnosis()
        self.assertEqual(diagnosis["action"], "confirm_deployment")
        self.assertIn("직접 확인", diagnosis["next_action"])
        self.assertEqual(self.counts(), {"deploy": 1, "observe_revision": 0, "health": 0})
        self.replay_evidence = {"after_repeated_resume": diagnosis, "calls": self.counts()}

    def test_changed_ignored_integration_inputs_require_new_check_before_effect(self):
        fixture = "checked-environment"
        with (self.project / ".git/info/exclude").open("a") as excludes:
            excludes.write("\n" + fixture + "\n")
        self.task["project_config"] = {"check_inputs": [fixture]}
        self.task["checks"].append([sys.executable, "-c", f"from pathlib import Path; assert not Path({fixture!r}).exists()"])
        self.store.save(self.task, "configure_input")
        self.finish()
        fixtures.ReleaseTests.configure(self, deploy=True)

        push = release.release_git
        def interrupt(budget, cwd, *args, **kwargs):
            if args[0] == "push" and args[-1].endswith(":refs/heads/main"):
                raise KeyboardInterrupt("before merge push")
            return push(budget, cwd, *args, **kwargs)

        with patch.object(release, "release_git", side_effect=interrupt), self.assertRaisesRegex(WorkflowError, "before merge push"):
            self.run_release()
        task = self.store.get(self.task["id"])
        candidate = task["release"]["local_merge_candidate"]
        receipt = task["release"]["integration_verification"]
        self.assertTrue(release.integration_verified(task, candidate))
        source_input = self.project / fixture
        source_input.touch()
        with self.assertRaisesRegex(WorkflowError, "로컬 검증"):
            self.run_release()
        self.assertEqual(git(self.remote, "rev-parse", "main"), self.baseline)
        source_input.unlink()
        changed = Path(receipt["input_worktree"]) / fixture
        changed.touch()
        self.assertFalse(release.integration_verified(task, candidate))
        with self.assertRaisesRegex(WorkflowError, "통합"):
            self.run_release()
        self.assertEqual(git(self.remote, "rev-parse", "main"), self.baseline)
        self.assertFalse((self.root / "deployments").exists())

        changed.unlink()
        checkout = release.checkout_release
        def provision(store, task, sha, suffix, budget=None):
            work = checkout(store, task, sha, suffix, budget)
            if suffix.startswith("deploy-"):
                (work / fixture).touch()
            return work

        with patch.object(release, "checkout_release", side_effect=provision), self.assertRaisesRegex(WorkflowError, "통합"):
            self.run_release()
        self.assertFalse((self.root / "deployments").exists())
        task = self.store.get(self.task["id"])
        self.assertFalse(task["release"]["integration_verification"]["passed"])
        (Path(task["release"]["integration_verification"]["input_worktree"]) / fixture).unlink()
        self.assertEqual(self.run_release()["release"]["stage"], "deployed")
        self.assertEqual(len((self.root / "deployments").read_text().splitlines()), 1)

    def test_unsent_or_definitely_rejected_write_can_be_retried(self):
        task = fixtures.ReleaseTests.github_task(self)
        intent = {"operation": "merge", "pr": 7, "target": {"hostname": "github.com"}}
        budget = release.ReleaseBudget(self.store, task, 30)
        with patch.object(budget, "remaining", side_effect=WorkflowError("hold")), \
                patch.object(release, "github_api") as http, self.assertRaisesRegex(WorkflowError, "hold"):
            release._github_write(self.store, task, budget, intent, "repos/owner/repo/pulls/7/merge", "PUT", {})
        http.assert_not_called()
        self.assertIsNone(self.store.get(task["id"])["release"].get("github_intent"))
        with patch.object(release.subprocess, "run", return_value=CompletedProcess([], 1, "", "gh: Forbidden (HTTP 403)")), \
                self.assertRaises(release.GitHubRejected):
            release._github_write(self.store, task, budget, intent, "repos/owner/repo/pulls/7/merge", "PUT", {})
        self.assertIsNone(self.store.get(task["id"])["release"].get("github_intent"))

    def test_release_status_reads_separate_hold_on_recorded_pr(self):
        task = fixtures.ReleaseTests.github_task(self)
        set_hold(self.store, task["id"], "inspect pending release")
        record = self.diagnosis()
        self.assertEqual(record["action"], "held")
        self.assertIn("inspect pending release", record["next_action"])
