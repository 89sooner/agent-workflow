"""Owned worktrees and explicit setup. Never reset or discard user changes."""
from __future__ import annotations

from pathlib import Path
import copy

from .core import WorkflowError, create_task, git, now, snapshot
from . import profiles


def create_from_profile(store, task_id, name, goal, criteria, refs=(), checks=(), owner=None,
                        reviewer=None, scope="지정 목표의 로컬 구현·검증", isolate=None):
    store.directory(task_id)
    if store.db.execute("SELECT 1 FROM tasks WHERE id=?", (task_id,)).fetchone():
        raise WorkflowError("이미 존재하는 작업 ID: " + task_id)
    record = profiles.get(store, name)
    cfg = profiles.validate(record["config"])
    original = Path(cfg["project"])
    isolated = cfg["workspace"]["isolate"] if isolate is None else isolate
    owner, reviewer = owner or cfg["owner"], reviewer or cfg["reviewer"]
    if owner == reviewer:
        raise WorkflowError("실행 담당과 독립 검토자는 달라야 합니다.")
    if not goal.strip() or not criteria:
        raise WorkflowError("목표와 완료 조건이 필요합니다.")
    project = original
    workspace = {"managed": False, "source": str(original), "prepared": False}
    if isolated:
        root = Path(cfg["workspace"].get("root", store.home / "workspaces" / name)).resolve()
        if root.is_relative_to(original):
            raise WorkflowError("작업 폴더는 원본 저장소 밖에 두세요.")
        project = root / task_id
        if project.exists() or project.is_symlink():
            raise WorkflowError("작업 폴더가 이미 존재합니다: " + str(project))
        branch = cfg["workspace"]["branch_prefix"] + task_id
        root.mkdir(parents=True, exist_ok=True)
        with store.lock(original):
            before = snapshot(original)
            git(original, "worktree", "add", "-b", branch, str(project), before["head"])
        workspace = {"managed": True, "source": str(original), "path": str(project),
                     "branch": branch, "base_head": before["head"], "source_dirty": before["dirty"], "prepared": False}
    try:
        task = create_task(store, task_id, project, goal, criteria,
                           [*cfg["refs"], *refs], [*cfg["checks"], *checks], owner, reviewer, scope,
                           metadata={"profile": {"name": name, "digest": record["digest"]},
                                     "project_config": copy.deepcopy(cfg), "execution": cfg["execution"], "workspace": workspace})
    except BaseException:
        # This tree was created by this call and has not run setup or an agent.
        if workspace["managed"] and not snapshot(project)["dirty"]:
            git(original, "worktree", "remove", str(project))
            if git(original, "rev-parse", workspace["branch"]) == workspace["base_head"]:
                git(original, "branch", "-d", workspace["branch"])
        raise
    store.save(task, "profile_applied", {"profile": task["profile"], "workspace": workspace})
    return task


def prepare(store, task, timeout, fd, retry=False):
    from .policy import preflight, scope_check, window_limit
    from .scheduling import prepare_dependencies
    prepare_dependencies(store, task)
    preflight(store, task)
    cfg = task.get("project_config") or {}
    workspace = task.setdefault("workspace", {"managed": False, "source": task["project"], "prepared": False})
    if workspace.get("prepared"):
        return task
    if workspace.get("setup_state") in {"running", "failed"} and not retry:
        raise WorkflowError("이전 환경 준비 결과를 확인하고 prepare --retry-setup으로 명시적으로 재개하세요.")
    from .usage import managed_process
    workspace.setdefault("setup_results", [])
    start = len(workspace["setup_results"])
    for i, argv in enumerate(cfg.get("setup", [])):
        if i < start:
            continue
        workspace.update(setup_state="running", setup_index=i)
        store.save(task, "setup_started", {"index": i, "argv": argv})
        directory = store.directory(task["id"]) / "setup" / str(i)
        try:
            result = managed_process(store, task, argv, task["project"], directory, min(timeout, window_limit(cfg)), fd, role="setup")
            scope_check(task)
            if result["timed_out"] or result["exit_code"]:
                raise WorkflowError("환경 준비 명령 실패. 로그: " + str(directory))
        except BaseException:
            workspace["setup_state"] = "failed"
            store.save(task, "setup_failed", {"index": i, "directory": str(directory)})
            raise
        workspace["setup_results"].append({"argv": argv, **result})
        store.save(task, "setup_finished", {"index": i})
    workspace.update(prepared=True, setup_state="passed", prepared_at=now())
    store.save(task, "workspace_prepared", workspace)
    return task


def cleanup(store, task, remove=False):
    workspace = task.get("workspace") or {}
    result = {"removed": False, "project": task["project"]}
    if not workspace.get("managed"):
        return {**result, "reason": "도구가 만든 작업 폴더가 아닙니다."}
    if workspace.get("removed"):
        return {**result, "removed": True, "reason": "이미 정리한 작업 폴더입니다."}
    with store.lock(task["project"]):
        current = snapshot(task["project"])
        if not remove or current["dirty"] or task["status"] != "completed":
            return {**result, "reason": "작업 폴더와 미반영 변경을 보존합니다.", "dirty": current["dirty"]}
        source = workspace["source"]
        # Only an already integrated, clean tree may be removed. Never use --force.
        import subprocess
        ancestor = subprocess.run(["git", "-C", source, "merge-base", "--is-ancestor", current["head"], "HEAD"], capture_output=True)
        if ancestor.returncode:
            raise WorkflowError("원본 HEAD에 통합되지 않은 작업 폴더는 보존합니다.")
        workspace["last_snapshot"] = current
        git(source, "worktree", "remove", task["project"])
        workspace.update(removed=True, removed_at=now())
        store.save(task, "workspace_removed", workspace)
        return {**result, "removed": True}


def observed_snapshot(task):
    workspace = task.get("workspace") or {}
    if workspace.get("removed"):
        return {**workspace["last_snapshot"], "archived": True}
    return snapshot(task["project"])
