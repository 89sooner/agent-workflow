"""Release verified files using a pinned remote, commit, merge and deployment receipt."""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import time
from urllib.parse import urlencode
import uuid

from .core import WorkflowError, encode, git, now, ready_for_completion, snapshot
from .observability import traced


class ReleaseBudget:
    """One invocation deadline, rechecked before every new command or remote effect."""
    def __init__(self, store, task, timeout):
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0:
            raise WorkflowError("릴리스 제한 시간은 양수여야 합니다.")
        self.store, self.task = store, task
        self.deadline = time.monotonic() + timeout

    def remaining(self):
        from .core import ensure_not_held, references_current
        from .policy import protected_current, window_limit
        from .usage import remaining
        ensure_not_held(self.store, self.task)
        if not protected_current(self.task):
            raise WorkflowError("금지 경로가 등록 기준과 달라 릴리스를 중단합니다.")
        if (not references_current(self.task, self.task.get("verification"))
                or (self.task.get("reviewer") != "none"
                    and not references_current(self.task, self.task.get("review")))):
            raise WorkflowError("참조 문서가 검증·리뷰 이후 변경됐습니다. 새 작업으로 확인하세요.")
        left = self.deadline - time.monotonic()
        if left <= 0:
            raise WorkflowError("릴리스 실행 시간 한도가 끝났습니다. 다음 호출에서 기록부터 재개합니다.")
        return min(left, window_limit(self.task.get("project_config") or {}), remaining(self.task))


def release_git(budget, project, *args, **kwargs):
    if budget is not None:
        kwargs["timeout"] = min(30, budget.remaining())
    return git(project, *args, **kwargs)


def release_api(budget, hostname, *args, **kwargs):
    if budget is not None:
        kwargs["timeout"] = min(30, budget.remaining())
    return github_api(hostname, *args, **kwargs)


def integration_contract(task):
    return hashlib.sha256(json.dumps({"checks": task["checks"],
                                     "services": (task.get("project_config") or {}).get("services", [])},
                                    ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def integration_verified(task, revision=None):
    from .core import references_current
    release = task.get("release") or {}
    receipt = release.get("integration_verification") or {}
    revision = revision or release.get("merge_commit")
    forbidden = (task.get("project_config") or {}).get("forbidden_paths") or []
    return bool(revision and task["checks"] and receipt.get("passed")
                and receipt.get("revision") == revision and receipt.get("fingerprint")
                and receipt.get("contract") == integration_contract(task)
                and references_current(task, receipt)
                and set(receipt.get("protected_paths", {})) == set(forbidden))


def assert_release_identity(work, revision, fingerprint=None, protected=None):
    current = snapshot(work)
    if (current["head"] != revision or current["dirty"]
            or (fingerprint is not None and current["fingerprint"] != fingerprint)):
        raise WorkflowError(f"릴리스 작업 사본의 HEAD·인덱스·내용이 변경됐습니다: {work}")
    if protected is not None:
        from .policy import protected_snapshot
        if protected_snapshot(work, protected) != protected:
            raise WorkflowError(f"릴리스 검사·배포 중 금지 경로가 변경됐습니다: {work}")
    return current


def release_command(store, task, command, work, directory, budget, fd, env, role):
    from .usage import managed_process
    return managed_process(store, task, command, work, directory, budget.remaining(), fd,
                           env_extra=env, role=role)


def content_digest(project):
    """Commit/staging independent content identity, including nonignored new files."""
    h = hashlib.sha256()
    paths = git(project, "ls-files", "-z", "--cached", "--others", "--exclude-standard", binary=True)
    for raw in sorted(set(paths.split(b"\0")) - {b""}):
        p = Path(project) / os.fsdecode(raw)
        if not p.exists() and not p.is_symlink():
            continue
        h.update(raw + b"\0")
        h.update(str(p.lstat().st_mode).encode() + b"\0")
        if p.is_symlink():
            h.update(os.fsencode(os.readlink(p)))
        elif p.is_dir():
            h.update(content_digest(p).encode())
        else:
            file_hash = hashlib.sha256()
            with p.open("rb") as source:
                while chunk := source.read(1024 * 1024):
                    file_hash.update(chunk)
            h.update(file_hash.digest())
    return h.hexdigest()


def remote_url(project, remote):
    fetch = git(project, "remote", "get-url", remote)
    push = git(project, "remote", "get-url", "--push", remote)
    if fetch != push:
        raise WorkflowError("fetch/push URL이 다른 원격은 지원하지 않습니다.")
    return push


def configure(store, task_id, remote, base, branch, paths, mode="github", repo=None,
              deploy=None, health=None, allow_no_ci=False, hostname="github.com"):
    t = store.get(task_id)
    with store.lock(t["project"]):
        t = store.get(task_id)
        if (t.get("release") or {}).get("stage") not in (None, "configured"):
            raise WorkflowError("릴리스가 시작된 후 대상을 변경할 수 없습니다.")
        if t["baseline"]["dirty"]:
            raise WorkflowError("자동 커밋 작업은 깨끗한 기준선에서 등록해야 합니다.")
        for ref in (base, branch):
            git(t["project"], "check-ref-format", "--branch", ref)
        if base == branch or remote.startswith("-"):
            raise WorkflowError("작업 브랜치와 병합 대상은 달라야 합니다.")
        url = remote_url(t["project"], remote)
        if mode == "github":
            if not isinstance(hostname, str) or not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?", hostname):
                raise WorkflowError("GitHub 호스트 이름이 잘못됐습니다.")
            hostname = hostname.lower()
            if not repo or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
                raise WorkflowError("GitHub owner/repo를 지정하세요.")
            expected = {f"https://{hostname}/{repo}", f"https://{hostname}/{repo}.git",
                        f"git@{hostname}:{repo}", f"git@{hostname}:{repo}.git"}
            if url not in expected:
                raise WorkflowError("원격 URL과 지정한 GitHub 저장소가 일치하지 않습니다.")
        elif mode == "local":
            if not Path(url).is_absolute() or not Path(url).is_dir():
                raise WorkflowError("local 모드는 절대 경로의 로컬 Git 원격만 허용합니다.")
        else:
            raise WorkflowError("릴리스 mode는 github 또는 local입니다.")
        if bool(deploy) != bool(health):
            raise WorkflowError("배포 명령과 상태 확인 명령을 함께 지정하세요.")
        if not paths:
            raise WorkflowError("자동 커밋할 파일/디렉터리 --path가 필요합니다.")
        clean_paths = []
        root = Path(t["project"])
        for name in paths:
            p = Path(name)
            if p.is_absolute() or ".." in p.parts or ".git" in p.parts or name in ("", ".") or name.startswith(":"):
                raise WorkflowError("--path에는 프로젝트 안의 명시적 상대 경로를 지정하세요.")
            if not (root / p).resolve().is_relative_to(root):
                raise WorkflowError("프로젝트 밖을 가리키는 경로입니다.")
            clean_paths.append(p.as_posix().rstrip("/"))
        t["release"] = {"stage": "configured", "error": None, "configured_at": now(),
                        "config": {"remote": remote, "remote_url": url, "base": base,
                                   "branch": branch, "paths": clean_paths, "mode": mode,
                                   "repo": repo, "hostname": hostname, "deploy": deploy, "health": health,
                                   "allow_no_ci": allow_no_ci}}
        store.save(t, "release_configured", t["release"]["config"])
        return t["release"]


def clear_configuration(store, task_id):
    task = store.get(task_id)
    with store.lock(task["project"]):
        task = store.get(task_id)
        release = task.get("release")
        if release and (release.get("stage") != "configured" or release.get("commit")):
            raise WorkflowError("이미 시작한 릴리스는 해제하지 않습니다. 실제 원격 상태를 먼저 확인하세요.")
        task["release"] = None
        store.save(task, "release_cleared")
        return task


def gh_api(endpoint, method="GET", payload=None, timeout=30, hostname="github.com"):
    args = ["gh", "api", "--method", method, "-H", "Accept: application/vnd.github+json", endpoint]
    args += ["--hostname", hostname]
    if payload is not None:
        args.extend(["--input", "-"])
    p = subprocess.run(args, input=encode(payload) if payload is not None else None,
                       capture_output=True, text=True, timeout=timeout)
    if p.returncode:
        raise WorkflowError("GitHub 요청 실패: " + p.stderr.strip()[:1200])
    try:
        return json.loads(p.stdout)
    except ValueError as exc:
        raise WorkflowError("GitHub 응답 JSON 오류") from exc


def changed_files(project):
    data = git(project, "diff", "HEAD", "--name-only", "-z", binary=True)
    data += git(project, "diff", "--cached", "--name-only", "-z", binary=True)
    data += git(project, "diff", "--name-only", "-z", binary=True)
    data += git(project, "ls-files", "--others", "--exclude-standard", "-z", binary=True)
    return sorted({os.fsdecode(p) for p in data.split(b"\0") if p})


def remote_head(project, remote, branch, budget=None):
    rows = release_git(budget, project, "ls-remote", "--heads", remote, "refs/heads/" + branch).splitlines()
    return rows[0].split()[0] if rows else None


def save_release(store, task, stage=None, **fields):
    task["release"].update(fields)
    if stage:
        task["release"]["stage"] = stage
    task["release"]["updated_at"] = now()
    store.save(task, "release_" + (stage or "updated"), fields)


def commit_verified(store, task, budget=None):
    p, r = task["project"], task["release"]
    c = r["config"]
    if "content_digest" not in r:
        ready_for_completion(task, snapshot(p))
        if task["status"] != "completed":
            raise WorkflowError("먼저 로컬 검증과 마감을 완료하세요.")
        changed = changed_files(p)
        if not changed:
            raise WorkflowError("커밋할 변경이 없습니다.")
        if any(not any(name == path or name.startswith(path + "/") for path in c["paths"]) for name in changed):
            raise WorkflowError("허용한 --path 밖의 변경이 있습니다. 자동 커밋을 중단합니다.")
        # Persist intent before changing index/branch, so an interrupted commit can be reconciled.
        save_release(store, task, "preparing", content_digest=content_digest(p),
                     original_head=git(p, "rev-parse", "HEAD"), changed_files=changed)
    if content_digest(p) != r["content_digest"]:
        raise WorkflowError("검증된 내용 이후 파일이 변경됐습니다.")
    head = git(p, "rev-parse", "HEAD")
    if head != r["original_head"]:
        if (not r.get("tree") or git(p, "rev-parse", "HEAD^{tree}") != r["tree"]
                or git(p, "rev-parse", "HEAD^") != r["original_head"]
                or git(p, "status", "--porcelain")):
            raise WorkflowError("중단된 커밋과 현재 HEAD를 일치시킬 수 없습니다.")
        save_release(store, task, "committed", commit=head)
        return
    current_branch = git(p, "branch", "--show-current")
    if current_branch != c["branch"]:
        existing = git(p, "branch", "--list", c["branch"])
        if existing:
            raise WorkflowError("대상 작업 브랜치가 이미 존재합니다. 해당 worktree를 사용하세요.")
        release_git(budget, p, "switch", "-c", c["branch"])
    release_git(budget, p, "add", "-A", "--", *r["changed_files"])
    if content_digest(p) != r["content_digest"]:
        raise WorkflowError("staging 중 코드가 바뀌었습니다.")
    tree = git(p, "write-tree")
    save_release(store, task, tree=tree)
    release_git(budget, p, "commit", "-m", f"{task['id']}: {task['goal']}")
    if git(p, "rev-parse", "HEAD^{tree}") != tree or git(p, "status", "--porcelain"):
        raise WorkflowError("커밋 훅이 검증한 내용을 변경했습니다. 재검증이 필요합니다.")
    save_release(store, task, "committed", commit=git(p, "rev-parse", "HEAD"))


def github_api(hostname, *args, **kwargs):
    return gh_api(*args, **kwargs) if hostname == "github.com" else gh_api(*args, hostname=hostname, **kwargs)


def ci_gate(repo, sha, allow_no_ci, hostname="github.com", budget=None):
    statuses = release_api(budget, hostname, f"repos/{repo}/commits/{sha}/status")
    checks = []
    page = 1
    while True:
        body = release_api(budget, hostname, f"repos/{repo}/commits/{sha}/check-runs?per_page=100&page={page}")
        rows = body.get("check_runs", [])
        checks.extend(rows)
        if len(rows) < 100:
            break
        page += 1
        if page > 50:
            raise WorkflowError("CI 검사 수가 지원 범위를 초과했습니다.")
    has_status = statuses.get("total_count", 0) > 0
    if not has_status and not checks:
        if allow_no_ci:
            return True
        return False
    if has_status and statuses.get("state") in ("error", "failure"):
        raise WorkflowError("원격 commit status 실패")
    bad = [x for x in checks if x.get("status") == "completed" and x.get("conclusion") not in ("success", "neutral", "skipped")]
    if bad:
        raise WorkflowError("원격 CI 실패: " + ", ".join(x.get("name", "check") for x in bad))
    return (not has_status or statuses.get("state") == "success") and all(x.get("status") == "completed" for x in checks)


def github_merge(store, task, timeout, budget=None):
    budget = budget or ReleaseBudget(store, task, timeout)
    r = task["release"]
    c = r["config"]
    repo, sha = c["repo"], r["commit"]
    hostname = c.get("hostname", "github.com")
    if not r.get("pr"):
        params = urlencode({"state": "all", "head": repo.split("/")[0] + ":" + c["branch"], "base": c["base"]})
        pulls = release_api(budget, hostname, f"repos/{repo}/pulls?{params}")
        matching = [x for x in pulls if x.get("head", {}).get("sha") == sha]
        if matching:
            pr = matching[0]
        else:
            pr = release_api(budget, hostname, f"repos/{repo}/pulls", "POST", {
                "title": f"{task['id']}: {task['goal']}", "head": c["branch"], "base": c["base"],
                "body": f"{task['goal']}\n\nValidation: registered local checks passed; reviewer {task['reviewer']}.\nTask: {task['id']}\nCandidate: {sha}"})
        save_release(store, task, "pull_request", pr=pr["number"], pr_url=pr["html_url"])
    while True:
        pr = release_api(budget, hostname, f"repos/{repo}/pulls/{r['pr']}")
        if pr["head"]["sha"] != sha or pr["base"]["ref"] != c["base"] or pr["head"]["ref"] != c["branch"]:
            raise WorkflowError("PR의 대상이나 HEAD가 등록된 릴리스와 다릅니다.")
        if pr.get("merged"):
            save_release(store, task, "merged", merge_commit=pr["merge_commit_sha"])
            return
        if pr.get("state") != "open" or pr.get("draft"):
            raise WorkflowError("PR이 열려 있지 않거나 draft입니다.")
        if ci_gate(repo, sha, c["allow_no_ci"], hostname, budget) and pr.get("mergeable") is True:
            if pr.get("mergeable_state") not in ("clean", "unstable"):
                raise WorkflowError("PR 병합 조건 미충족: " + str(pr.get("mergeable_state")))
            result = release_api(budget, hostname, f"repos/{repo}/pulls/{r['pr']}/merge", "PUT", {"sha": sha, "merge_method": "merge"})
            if not result.get("merged"):
                raise WorkflowError("GitHub가 병합을 완료하지 않았습니다.")
            save_release(store, task, "merged", merge_commit=result["sha"])
            return
        time.sleep(min(2, budget.remaining()))


def checkout_release(store, task, sha, suffix, budget=None):
    p = store.directory(task["id"]) / "release" / suffix
    if p.exists():
        assert_release_identity(p, sha)
    else:
        p.parent.mkdir(parents=True, exist_ok=True)
        release_git(budget, task["project"], "worktree", "add", "--detach", str(p), sha)
    return p


def verify_integration(store, task, sha, budget, fd, work=None):
    """Check the actual merged revision even when this release has no deployment."""
    from .services import environment, running, stop_recorded
    from .core import reference_fingerprint, references_current
    from .policy import protected_snapshot
    r, c = task["release"], task["release"]["config"]
    budget.remaining()
    if work is None:
        release_git(budget, task["project"], "fetch", c["remote"], sha)
        work = checkout_release(store, task, sha, "integration-" + sha[:12], budget)
    before = assert_release_identity(work, sha)
    if integration_verified(task, sha):
        receipt = r["integration_verification"]
        assert_release_identity(work, sha, receipt["fingerprint"], receipt.get("protected_paths", {}))
        return work
    if not task["checks"]:
        raise WorkflowError("병합 결과를 검증할 필수 검사 명령이 없습니다.")
    results = []
    protected = protected_snapshot(work, (task.get("project_config") or {}).get("forbidden_paths") or [])
    receipt = {"passed": False, "revision": sha, "fingerprint": before["fingerprint"],
               "contract": integration_contract(task), "refs_fingerprint": reference_fingerprint(task),
               "protected_paths": protected, "started_at": now(), "results": results}
    save_release(store, task, integration_verification=receipt)
    root = store.directory(task["id"]) / "release" / "commands"
    env = {"WORKFLOW_REVISION": sha, "WORKFLOW_TASK_ID": task["id"]}
    try:
        stop_recorded(store, task)
        with running(store, task, fd, cwd=work, deadline=budget.deadline):
            for command in task["checks"]:
                budget.remaining()
                assert_release_identity(work, sha, before["fingerprint"], protected)
                directory = root / ("integration-" + uuid.uuid4().hex)
                result = release_command(store, task, command, work, directory, budget, fd,
                                         {**env, **environment(task, directory)}, "integration_check")
                results.append({"argv": command, **result})
                assert_release_identity(work, sha, before["fingerprint"], protected)
                if result["exit_code"] != 0 or result["timed_out"]:
                    raise WorkflowError("병합된 코드의 필수 검사 실패. 통합 성공으로 처리하지 않았습니다.")
        assert_release_identity(work, sha, before["fingerprint"], protected)
        if not references_current(task, receipt):
            raise WorkflowError("통합 검사 중 참조 문서가 변경됐습니다.")
    except (WorkflowError, OSError, subprocess.TimeoutExpired, KeyboardInterrupt) as exc:
        receipt.update(finished_at=now(), error=str(exc) or "통합 검사 중단")
        save_release(store, task, integration_verification=receipt)
        raise
    receipt.update(passed=True, finished_at=now(), error=None)
    save_release(store, task, integration_verification=receipt)
    return work


def local_merge(store, task, budget=None, fd=None):
    budget = budget or ReleaseBudget(store, task, 300)
    r, p = task["release"], task["project"]
    c = r["config"]
    release_git(budget, p, "fetch", c["remote"], c["base"])
    base_sha = release_git(budget, p, "rev-parse", "FETCH_HEAD")
    # If push succeeded before the receipt, recover only our exact committed effect.
    if r.get("local_merge_candidate") and base_sha == r["local_merge_candidate"]:
        save_release(store, task, "merged", merge_commit=base_sha)
        verify_integration(store, task, base_sha, budget, fd)
        return
    work = store.directory(task["id"]) / "release" / ("merge-" + base_sha[:12])
    intent = r.get("local_merge_intent") or {}
    matching_intent = (intent.get("base") == base_sha and intent.get("candidate") == r["commit"]
                       and intent.get("worktree") == str(work))
    if not matching_intent and r.get("local_merge_candidate") and work.exists():
        candidate = r["local_merge_candidate"]
        parents = release_git(budget, p, "rev-list", "--parents", "-n", "1", candidate).split()[1:]
        if parents == [base_sha, r["commit"]] and release_git(budget, work, "rev-parse", "HEAD") == candidate:
            # v0.3 receipts had the candidate after merge, but no pre-merge intent.
            assert_release_identity(work, candidate)
            intent = {"base": base_sha, "candidate": r["commit"], "worktree": str(work), "at": now()}
            save_release(store, task, local_merge_intent=intent)
            matching_intent = True
    if not matching_intent:
        work = checkout_release(store, task, base_sha, work.name, budget)
        intent = {"base": base_sha, "candidate": r["commit"], "worktree": str(work), "at": now()}
        # A crash after git merge must not make this owned checkout unrecognizable.
        save_release(store, task, local_merge_intent=intent)
    head = release_git(budget, work, "rev-parse", "HEAD")
    assert_release_identity(work, head)
    if head == base_sha:
        release_git(budget, work, "merge", "--no-ff", "-m", f"Merge {task['id']}", r["commit"])
        merge_sha = release_git(budget, work, "rev-parse", "HEAD")
    else:
        parents = release_git(budget, work, "rev-list", "--parents", "-n", "1", head).split()[1:]
        message = release_git(budget, work, "log", "-1", "--format=%B")
        if parents != [base_sha, r["commit"]] or message != f"Merge {task['id']}":
            raise WorkflowError("중단된 병합의 부모·작업 기록이 현재 사본과 다릅니다. 변경을 보존합니다.")
        merge_sha = head
    save_release(store, task, local_merge_candidate=merge_sha)
    verify_integration(store, task, merge_sha, budget, fd, work)
    if remote_head(p, c["remote"], c["base"], budget) != base_sha:
        raise WorkflowError("통합 검사 중 원격 기준 브랜치가 바뀌었습니다. 다음 호출에서 새 기준을 검증하세요.")
    assert_release_identity(work, merge_sha, r["integration_verification"]["fingerprint"],
                            r["integration_verification"].get("protected_paths", {}))
    release_git(budget, work, "push", c["remote"], merge_sha + ":refs/heads/" + c["base"])
    if remote_head(p, c["remote"], c["base"], budget) != merge_sha:
        raise WorkflowError("원격 병합 결과를 확인할 수 없습니다.")
    save_release(store, task, "merged", merge_commit=merge_sha)


def deploy_release(store, task, timeout, fd, retry=False, budget=None):
    budget = budget or ReleaseBudget(store, task, timeout)
    r, p = task["release"], task["project"]
    c = r["config"]
    if not c["deploy"] or r["stage"] == "deployed":
        return
    if not integration_verified(task):
        raise WorkflowError("현재 병합 커밋의 통합 검사 통과 기록이 필요합니다.")
    if r.get("deployment_state") in ("started", "failed") and not retry:
        raise WorkflowError("배포가 실패했거나 실행 결과가 불명확합니다. 상태 확인 후 --retry-deploy로만 재실행하세요.")
    sha = r["merge_commit"]
    release_git(budget, p, "fetch", c["remote"], sha)
    work = checkout_release(store, task, sha, "deploy-" + sha[:12], budget)
    fingerprint = r["integration_verification"]["fingerprint"]
    protected = r["integration_verification"].get("protected_paths", {})
    assert_release_identity(work, sha, fingerprint, protected)
    env = {"WORKFLOW_REVISION": sha, "WORKFLOW_TASK_ID": task["id"]}
    root = store.directory(task["id"]) / "release" / "commands"
    if r.get("deployment_state") != "applied":
        budget.remaining()
        assert_release_identity(work, sha, fingerprint, protected)
        save_release(store, task, "deploying", deployment_state="started")
        result = release_command(store, task, c["deploy"], work, root / ("deploy-" + uuid.uuid4().hex),
                                 budget, fd, env, "deploy")
        save_release(store, task, deployment_result=result,
                     deployment_state="applied" if result["exit_code"] == 0 and not result["timed_out"] else "failed")
        if r["deployment_state"] != "applied":
            raise WorkflowError("배포 명령 실패. 결과를 확인한 뒤 명시적으로 재시도하세요.")
    assert_release_identity(work, sha, fingerprint, protected)
    health = release_command(store, task, c["health"], work, root / ("health-" + uuid.uuid4().hex),
                             budget, fd, env, "health")
    save_release(store, task, health_result=health)
    assert_release_identity(work, sha, fingerprint, protected)
    if health["exit_code"] != 0 or health["timed_out"]:
        raise WorkflowError("배포 명령은 끝났지만 상태 확인이 실패했습니다. 다음 호출은 상태 확인부터 재개합니다.")
    save_release(store, task, "deployed", deployed_at=now(), deployed_revision=sha, error=None)


@traced("release")
def release_task(store, task_id, timeout=300, retry_deploy=False):
    task = store.get(task_id)
    from .policy import reservation
    with store.lock(task["project"]) as tree_fd, reservation(store, task) as resource_fd:
        fd = (tree_fd, resource_fd)
        task = store.get(task_id)
        budget = ReleaseBudget(store, task, timeout)
        budget.remaining()
        r = task.get("release")
        if not r:
            raise WorkflowError("release-config로 저장소·브랜치·파일·배포 명령을 먼저 지정하세요.")
        c = r["config"]
        if remote_url(task["project"], c["remote"]) != c["remote_url"]:
            raise WorkflowError("원격 URL이 릴리스 설정 이후 변경됐습니다.")
        try:
            if not r.get("commit"):
                commit_verified(store, task, budget)
            sha = r["commit"]
            if not r.get("merge_commit"):
                current = remote_head(task["project"], c["remote"], c["branch"], budget)
                if current not in (None, sha):
                    raise WorkflowError("원격 작업 브랜치에 다른 커밋이 있습니다. 덮어쓰지 않습니다.")
                release_git(budget, task["project"], "push", c["remote"], sha + ":refs/heads/" + c["branch"])
                save_release(store, task, "pushed", error=None)
                if c["mode"] == "github":
                    github_merge(store, task, timeout, budget)
                else:
                    local_merge(store, task, budget, fd)
            if not integration_verified(task):
                verify_integration(store, task, r["merge_commit"], budget, fd)
            deploy_release(store, task, timeout, fd, retry_deploy, budget)
            if r.get("error"):
                save_release(store, task, error=None)
            return task
        except (WorkflowError, OSError, subprocess.TimeoutExpired, KeyboardInterrupt) as exc:
            save_release(store, task, error=str(exc) or "릴리스 중단")
            raise WorkflowError(r["error"]) from exc
