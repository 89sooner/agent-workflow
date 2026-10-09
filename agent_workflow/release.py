"""Release verified files using a pinned remote, commit, merge and deployment receipt."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import time
from urllib.parse import urlencode
import uuid

from .core import WorkflowError, atomic_write, encode, git, now, ready_for_completion, snapshot
from .runner import process


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


def remote_head(project, remote, branch):
    rows = git(project, "ls-remote", "--heads", remote, "refs/heads/" + branch).splitlines()
    return rows[0].split()[0] if rows else None


def save_release(store, task, stage=None, **fields):
    task["release"].update(fields)
    if stage:
        task["release"]["stage"] = stage
    task["release"]["updated_at"] = now()
    store.save(task, "release_" + (stage or "updated"), fields)


def commit_verified(store, task):
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
        git(p, "switch", "-c", c["branch"])
    git(p, "add", "-A", "--", *r["changed_files"])
    if content_digest(p) != r["content_digest"]:
        raise WorkflowError("staging 중 코드가 바뀌었습니다.")
    tree = git(p, "write-tree")
    save_release(store, task, tree=tree)
    git(p, "commit", "-m", f"{task['id']}: {task['goal']}")
    if git(p, "rev-parse", "HEAD^{tree}") != tree or git(p, "status", "--porcelain"):
        raise WorkflowError("커밋 훅이 검증한 내용을 변경했습니다. 재검증이 필요합니다.")
    save_release(store, task, "committed", commit=git(p, "rev-parse", "HEAD"))


def github_api(hostname, *args, **kwargs):
    return gh_api(*args, **kwargs) if hostname == "github.com" else gh_api(*args, hostname=hostname, **kwargs)


def ci_gate(repo, sha, allow_no_ci, hostname="github.com"):
    statuses = github_api(hostname, f"repos/{repo}/commits/{sha}/status")
    checks = []
    page = 1
    while True:
        body = github_api(hostname, f"repos/{repo}/commits/{sha}/check-runs?per_page=100&page={page}")
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


def github_merge(store, task, timeout):
    r = task["release"]
    c = r["config"]
    repo, sha = c["repo"], r["commit"]
    hostname = c.get("hostname", "github.com")
    if not r.get("pr"):
        params = urlencode({"state": "all", "head": repo.split("/")[0] + ":" + c["branch"], "base": c["base"]})
        pulls = github_api(hostname, f"repos/{repo}/pulls?{params}")
        matching = [x for x in pulls if x.get("head", {}).get("sha") == sha]
        if matching:
            pr = matching[0]
        else:
            pr = github_api(hostname, f"repos/{repo}/pulls", "POST", {
                "title": f"{task['id']}: {task['goal']}", "head": c["branch"], "base": c["base"],
                "body": f"{task['goal']}\n\nValidation: registered local checks passed; reviewer {task['reviewer']}.\nTask: {task['id']}\nCandidate: {sha}"})
        save_release(store, task, "pull_request", pr=pr["number"], pr_url=pr["html_url"])
    deadline = time.monotonic() + timeout
    while True:
        pr = github_api(hostname, f"repos/{repo}/pulls/{r['pr']}")
        if pr["head"]["sha"] != sha or pr["base"]["ref"] != c["base"] or pr["head"]["ref"] != c["branch"]:
            raise WorkflowError("PR의 대상이나 HEAD가 등록된 릴리스와 다릅니다.")
        if pr.get("merged"):
            save_release(store, task, "merged", merge_commit=pr["merge_commit_sha"])
            return
        if pr.get("state") != "open" or pr.get("draft"):
            raise WorkflowError("PR이 열려 있지 않거나 draft입니다.")
        if ci_gate(repo, sha, c["allow_no_ci"], hostname) and pr.get("mergeable") is True:
            if pr.get("mergeable_state") not in ("clean", "unstable"):
                raise WorkflowError("PR 병합 조건 미충족: " + str(pr.get("mergeable_state")))
            result = github_api(hostname, f"repos/{repo}/pulls/{r['pr']}/merge", "PUT", {"sha": sha, "merge_method": "merge"})
            if not result.get("merged"):
                raise WorkflowError("GitHub가 병합을 완료하지 않았습니다.")
            save_release(store, task, "merged", merge_commit=result["sha"])
            return
        if time.monotonic() >= deadline:
            raise WorkflowError("CI/병합 가능 상태 대기 시간 초과. 다음 release 호출에서 재확인합니다.")
        time.sleep(2)


def checkout_release(store, task, sha, suffix):
    p = store.directory(task["id"]) / "release" / suffix
    if p.exists():
        if git(p, "rev-parse", "HEAD") != sha or git(p, "status", "--porcelain"):
            raise WorkflowError(f"릴리스 작업 사본이 변경됐습니다: {p}")
    else:
        p.parent.mkdir(parents=True, exist_ok=True)
        git(task["project"], "worktree", "add", "--detach", str(p), sha)
    return p


def local_merge(store, task):
    r, p = task["release"], task["project"]
    c = r["config"]
    git(p, "fetch", c["remote"], c["base"])
    base_sha = git(p, "rev-parse", "FETCH_HEAD")
    # If push succeeded before the receipt was saved, only recover our exact commit.
    if r.get("local_merge_candidate") and base_sha == r["local_merge_candidate"]:
        save_release(store, task, "merged", merge_commit=base_sha)
        return
    if r.get("local_merge_candidate"):
        parents = git(p, "rev-list", "--parents", "-n", "1", r["local_merge_candidate"]).split()[1:]
        if parents and parents[0] == base_sha and r["commit"] in parents:
            merge_sha = r["local_merge_candidate"]
            git(p, "push", c["remote"], merge_sha + ":refs/heads/" + c["base"])
            if remote_head(p, c["remote"], c["base"]) != merge_sha:
                raise WorkflowError("중단된 병합 push 결과를 확인할 수 없습니다.")
            save_release(store, task, "merged", merge_commit=merge_sha)
            return
    work = checkout_release(store, task, base_sha, "merge-" + base_sha[:12])
    git(work, "merge", "--no-ff", "-m", f"Merge {task['id']}", r["commit"])
    merge_sha = git(work, "rev-parse", "HEAD")
    save_release(store, task, local_merge_candidate=merge_sha)
    git(work, "push", c["remote"], merge_sha + ":refs/heads/" + c["base"])
    if remote_head(p, c["remote"], c["base"]) != merge_sha:
        raise WorkflowError("원격 병합 결과를 확인할 수 없습니다.")
    save_release(store, task, "merged", merge_commit=merge_sha)


def deploy_release(store, task, timeout, fd, retry=False):
    r, p = task["release"], task["project"]
    c = r["config"]
    if not c["deploy"] or r["stage"] == "deployed":
        return
    sha = r["merge_commit"]
    git(p, "fetch", c["remote"], sha)
    work = checkout_release(store, task, sha, "deploy-" + sha[:12])
    env = {"WORKFLOW_REVISION": sha, "WORKFLOW_TASK_ID": task["id"]}
    root = store.directory(task["id"]) / "release" / "commands"
    if r.get("deployment_state") in ("started", "failed") and not retry:
        raise WorkflowError("배포가 실패했거나 실행 결과가 불명확합니다. 상태 확인 후 --retry-deploy로만 재실행하세요.")
    if r.get("deployment_state") != "applied":
        from .services import running, environment
        with running(store, task, fd, cwd=work):
            for i, command in enumerate(task["checks"]):
                directory = root / ("check-" + uuid.uuid4().hex)
                result = process(command, work, directory, timeout, fd, env_extra={**env, **environment(task, directory)})
                if result["exit_code"] != 0 or result["timed_out"]:
                    raise WorkflowError("병합된 코드의 필수 검사 실패. 배포하지 않았습니다.")
        if git(work, "status", "--porcelain"):
            raise WorkflowError("병합 결과 검사 중 코드가 바뀌었습니다.")
        save_release(store, task, "deploying", deployment_state="started")
        result = process(c["deploy"], work, root / ("deploy-" + uuid.uuid4().hex), timeout, fd, env_extra=env)
        save_release(store, task, deployment_result=result,
                     deployment_state="applied" if result["exit_code"] == 0 and not result["timed_out"] else "failed")
        if r["deployment_state"] != "applied":
            raise WorkflowError("배포 명령 실패. 결과를 확인한 뒤 명시적으로 재시도하세요.")
    health = process(c["health"], work, root / ("health-" + uuid.uuid4().hex), timeout, fd, env_extra=env)
    save_release(store, task, health_result=health)
    if health["exit_code"] != 0 or health["timed_out"]:
        raise WorkflowError("배포 명령은 끝났지만 상태 확인이 실패했습니다. 다음 호출은 상태 확인부터 재개합니다.")
    save_release(store, task, "deployed", deployed_at=now(), deployed_revision=sha, error=None)


def release_task(store, task_id, timeout=300, retry_deploy=False):
    task = store.get(task_id)
    from .policy import reservation, window_limit
    with store.lock(task["project"]) as tree_fd, reservation(store, task) as resource_fd:
        fd = (tree_fd, resource_fd)
        timeout = min(timeout, window_limit(task.get("project_config") or {}))
        task = store.get(task_id)
        r = task.get("release")
        if not r:
            raise WorkflowError("release-config로 저장소·브랜치·파일·배포 명령을 먼저 지정하세요.")
        c = r["config"]
        if remote_url(task["project"], c["remote"]) != c["remote_url"]:
            raise WorkflowError("원격 URL이 릴리스 설정 이후 변경됐습니다.")
        try:
            if not r.get("commit"):
                commit_verified(store, task)
            sha = r["commit"]
            if not r.get("merge_commit"):
                current = remote_head(task["project"], c["remote"], c["branch"])
                if current not in (None, sha):
                    raise WorkflowError("원격 작업 브랜치에 다른 커밋이 있습니다. 덮어쓰지 않습니다.")
                git(task["project"], "push", c["remote"], sha + ":refs/heads/" + c["branch"])
                save_release(store, task, "pushed", error=None)
                if c["mode"] == "github":
                    github_merge(store, task, timeout)
                else:
                    local_merge(store, task)
            deploy_release(store, task, timeout, fd, retry_deploy)
            return task
        except (WorkflowError, OSError, subprocess.TimeoutExpired, KeyboardInterrupt) as exc:
            save_release(store, task, error=str(exc) or "릴리스 중단")
            raise WorkflowError(r["error"]) from exc
