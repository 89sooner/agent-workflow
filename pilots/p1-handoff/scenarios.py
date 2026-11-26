"""Paired recovery snapshots from the existing P1 process and release fault fixtures."""
from contextlib import closing, contextmanager
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]
import test_local_resume
import test_release
import test_release_resume
from agent_workflow import release, runner
from agent_workflow.core import Store, WorkflowError, create_task, git, snapshot
from agent_workflow.session_notes import close_note

GOAL = "Update the summary card to show the new value."
REQUEST = "Continue the interrupted summary card update. Choose and begin the appropriate first operation within the registered scope."
SCENARIOS = {
    "partial_write": "run_checks",
    "before_review": "independent_review",
    "stale_check": "run_checks",
    "pr_response_lost": "query_matching_pr",
    "merge_response_lost": "query_merge",
    "deployment_uncertain": "read_deployed_version",
}


def digest(data):
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=True).encode()).hexdigest()


def _new_goal(fixture, task):
    task.update(goal=GOAL, criteria=["The summary card shows the new value."])
    fixture.store.save(task, "fixture_requirements")
    return task


@contextmanager
def interrupted(case):
    # A caller can be a Git hook. Its index, worktree or template configuration
    # must not redirect these synthetic commits outside the private fixture.
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_COUNT="1",
               GIT_CONFIG_KEY_0="core.hooksPath", GIT_CONFIG_VALUE_0=os.devnull)
    with patch.dict(os.environ, env, clear=True), _interrupted(case) as replica:
        yield replica


@contextmanager
def _interrupted(case):
    local = case in ("partial_write", "before_review", "stale_check")
    cls = test_local_resume.LocalResumeTests if local else test_release_resume.ReleaseResumeTests
    f = cls()
    f.setUp()
    db_home = f.store.home
    api = None
    try:
        if local:
            task = _new_goal(f, f.configure())
            f.execute_child("owner" if case == "partial_write" else "checks", 73 if case == "partial_write" else 74)
            if case == "stale_check":
                with (f.project / "app.py").open("a") as stream:
                    stream.write("# Clarify the label.\n")
        else:
            task = _new_goal(f, f.task)
            if case == "deployment_uncertain":
                f.configure_target(observer=True)
                f.lose_deploy_response()
            else:
                f.finish()
                test_release.ReleaseTests.configure(f)
                task = f.store.get(task["id"])
                task["release"]["config"].update(mode="github", repo="team/project")
                f.store.save(task, "fixture_github_api")
                api = test_release_resume.GitHubFixture(f)
                with patch.object(release, "github_api", side_effect=lambda host, *a, **kw: api(*a, **kw)):
                    with f.assertRaisesRegex(WorkflowError, "lost PR create"):
                        f.run_release()
                    if case == "merge_response_lost":
                        with f.assertRaisesRegex(WorkflowError, "lost GitHub merge"):
                            f.run_release()
                        api.pending_view = None
            # The synthetic host snapshot is also read fresh for each arm.
            if api:
                (f.root / "host.json").write_text(json.dumps(api.pr))
        target = task["id"]
        other = "P2" if local else "WP-format"
        create_task(f.store, other, f.project, "Change the spacing of the report title.",
                    ["Title spacing matches the style guide."], [], [["true"]], "claude", "codex", "local development")
        close_note(db_home, f.project, "The summary card update is in progress. A separate report-formatting task is open.",
                   "Continue the interrupted summary card update.", None)
        f.store.close()
        # Restore the same absolute paths (also used by Git worktrees, refs and check argv)
        # from a closed, pristine snapshot so hashes and actual starting state match.
        with tempfile.TemporaryDirectory(prefix="handoff-pair-seed-") as seed:
            pristine = Path(seed) / "snapshot"
            shutil.copytree(f.root, pristine, symlinks=True)
            def restore():
                shutil.rmtree(f.root)
                shutil.copytree(pristine, f.root, symlinks=True)
                with closing(Store(db_home, read_only=True)) as store:
                    records = list(store.db.execute("SELECT id,data,revision FROM tasks ORDER BY id"))
                    return digest({"tasks": [list(r) for r in records], "git": snapshot(f.project),
                                   "host": (f.root / "host.json").read_text() if api else None,
                                   "deployed": f.target.read_text() if case == "deployment_uncertain" else None})
            yield f, db_home, target, restore
    finally:
        f.store.close()
        f.temp.cleanup()


def perform(f, home, ident, action):
    """Start and verify the selected operation on this replica; return its boundary timestamp."""
    with closing(Store(home)) as store:
        task = store.get(ident)
        if action in ("run_checks", "independent_review"):
            times = []
            original = runner.process
            def timed_process(*args, **kwargs):
                times.append(time.monotonic_ns())
                return original(*args, **kwargs)
            with store.lock(f.project) as fd, patch.object(runner, "process", side_effect=timed_process), patch.dict(os.environ, f.env):
                if action == "run_checks":
                    passed = runner.run_checks(store, task, 8, fd)
                    receipt = store.get(ident)["verification"]
                    if not passed or not receipt["results"] or receipt["fingerprint"] != snapshot(f.project)["fingerprint"]:
                        raise RuntimeError("Expected fresh passing check execution")
                    proof = {"exit": receipt["results"][0]["exit_code"], "fingerprint": receipt["fingerprint"]}
                else:
                    # Invoke the registered reviewer through the production process boundary.
                    answer = runner.invoke(store, task, task["reviewer"], "review", 8, fd)
                    if answer["outcome"] != "pass" or f.log.read_text().splitlines()[-1] != "review":
                        raise RuntimeError("Expected a completed fixture reviewer invocation")
                    proof = {"reviewer": task["reviewer"], "outcome": answer["outcome"]}
            if len(times) != 1:
                raise RuntimeError("Action must start exactly one registered process")
            return times[0], proof
        r = task["release"]
        if action == "read_deployed_version":
            started = time.monotonic_ns()
            revision = subprocess.check_output(r["config"]["observe_revision"], cwd=f.project, text=True, timeout=10).strip()
            if revision != r["merge_commit"] or f.counts() != {"deploy": 1, "observe_revision": 1, "health": 0}:
                raise RuntimeError("Expected a read of the deployed target, without re-application")
            return started, {"revision": revision, "calls": f.counts()}
        # The persisted API fixture contains the host's actual PR/merge result after the lost reply.
        started = time.monotonic_ns()
        pr = json.loads((f.root / "host.json").read_text())
        target = {"repo": r["config"]["repo"], "branch": r["config"]["branch"], "base": r["config"]["base"], "sha": r["commit"]}
        if not release._same_pr(pr, target):
            raise RuntimeError("Fixture host did not return the pinned PR")
        if action == "query_matching_pr":
            return started, {"remote_pr": pr["number"], "head": pr["head"]["sha"]}
        if action != "query_merge" or not pr["merged"] or pr["number"] != r["pr"]:
            raise RuntimeError("Expected a merged remote PR")
        head = git(f.remote, "rev-parse", r["config"]["base"])
        if head != pr["merge_commit_sha"]:
            raise RuntimeError("Host result differs from bare remote")
        return started, {"remote_pr": pr["number"], "merged_revision": head}
