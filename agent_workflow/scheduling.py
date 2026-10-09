"""Explicit task dependencies and bounded durable batches, not autonomous scope discovery."""
from __future__ import annotations

import datetime as dt
import json
import subprocess

from .core import WorkflowError, encode, git, now, snapshot
from .profiles import identifier, integer


class ControlChanged(WorkflowError):
    """A user start/pause superseded the worker's batch snapshot."""


def dependency_evidence(store, task):
    from .cli import freshness
    from .workspaces import observed_snapshot
    receipts = {}
    for task_id in task.get("dependencies", []):
        previous = store.get(task_id)
        current = observed_snapshot(previous)
        if previous["status"] != "completed" or not freshness(previous, current)["completion_is_current"]:
            raise WorkflowError("선행 작업의 유효한 완료를 기다립니다: " + task_id)
        release = previous.get("release") or {}
        if task.get("dependency_mode") == "merged" and not release.get("merge_commit"):
            raise WorkflowError("선행 작업의 병합을 기다립니다: " + task_id)
        receipts[task_id] = {"fingerprint": current["fingerprint"], "project": previous["project"],
                             "merge_commit": release.get("merge_commit"), "at": now()}
    return receipts


def set_dependencies(store, task_id, dependencies, mode="completed"):
    task = store.get(task_id)
    if mode not in {"completed", "merged"}:
        raise WorkflowError("선행 조건은 completed 또는 merged입니다.")
    dependencies = list(dict.fromkeys(identifier(x) for x in dependencies))
    for dependency in dependencies:
        store.get(dependency)
    graph = {item["id"]: item.get("dependencies", []) for item in store.all()}
    graph[task_id] = dependencies
    def visit(node, active):
        if node in active:
            raise WorkflowError("선행 작업 순환이 있습니다: " + node)
        for parent in graph.get(node, []):
            visit(parent, active | {node})
    visit(task_id, set())
    with store.lock(task["project"]):
        task = store.get(task_id)
        if task["status"] == "completed" or task.get("implementation") or task.get("verification") or (task.get("workspace") or {}).get("prepared"):
            raise WorkflowError("실행 전 작업에만 선행 조건을 지정할 수 있습니다.")
        task.update(dependencies=dependencies, dependency_mode=mode)
        task.pop("dependency_receipts", None)
        store.save(task, "dependencies_configured", {"dependencies": dependencies, "mode": mode})
        return task


def prepare_dependencies(store, task):
    if not task.get("dependencies") or task.get("dependency_receipts"):
        return
    from .policy import window_limit
    window_limit(task.get("project_config") or {})
    receipts = dependency_evidence(store, task)
    current = snapshot(task["project"])
    if task.get("dependency_mode") == "merged":
        for item in receipts.values():
            sha = item["merge_commit"]
            ancestor = subprocess.run(["git", "-C", task["project"], "merge-base", "--is-ancestor", sha, "HEAD"], capture_output=True, timeout=30)
            if ancestor.returncode == 0:
                continue
            if not (task.get("workspace") or {}).get("managed") or current["dirty"] or task.get("implementation"):
                raise WorkflowError("선행 병합을 포함한 깨끗한 관리 작업 폴더가 필요합니다.")
            # Advance only this task's unused owned branch, never the user's main.
            git(task["project"], "merge", "--ff-only", sha)
            current = snapshot(task["project"])
            task["baseline"] = {**current, "changed_files": {}}
            store.save(task, "dependency_base_advanced", {"commit": current["head"]})
    elif any(item["project"] == task["project"] for item in receipts.values()):
        # A same-worktree sequence accepts the already verified predecessor as its
        # baseline. Separate worktrees need merged mode to inherit source changes.
        from .policy import changed_files
        task["baseline"] = {**current, "changed_files": changed_files(task["project"])}
    task["dependency_receipts"] = receipts
    store.save(task, "dependencies_verified", receipts)


def init(store):
    store.db.execute("CREATE TABLE IF NOT EXISTS batches(name TEXT PRIMARY KEY, data TEXT NOT NULL)")
    store.db.commit()


def list_batches(store):
    init(store)
    return [json.loads(row[0]) for row in store.db.execute("SELECT data FROM batches ORDER BY rowid DESC")]


def get(store, name):
    init(store)
    row = store.db.execute("SELECT data FROM batches WHERE name=?", (name,)).fetchone()
    if row is None:
        raise WorkflowError("연속 실행 묶음 없음: " + name)
    return json.loads(row[0])


def save(store, batch):
    with store.db:
        store.db.execute("BEGIN IMMEDIATE")
        row = store.db.execute("SELECT data FROM batches WHERE name=?", (batch["name"],)).fetchone()
        if row is None or json.loads(row[0]).get("control_revision", 0) != batch.get("control_revision", 0):
            raise ControlChanged("연속 실행 제어 상태가 변경됐습니다.")
        batch["updated_at"] = now()
        store.db.execute("UPDATE batches SET data=? WHERE name=?", (encode(batch), batch["name"]))
    return batch


def create(store, name, task_ids, max_jobs=3, max_seconds=3600):
    identifier(name)
    integer(max_jobs, "max_jobs", 1, 100)
    integer(max_seconds, "max_seconds", 1, 86400)
    task_ids = list(dict.fromkeys(task_ids))
    if not task_ids:
        raise WorkflowError("연속 실행할 기존 작업 ID가 필요합니다.")
    for task_id in task_ids:
        store.get(identifier(task_id))
    init(store)
    batch = {"name": name, "task_ids": task_ids, "max_jobs": max_jobs, "max_seconds": max_seconds,
             "status": "draft", "history": [], "active_job": None, "done": [], "created_at": now(), "error": None, "control_revision": 0}
    import sqlite3
    try:
        with store.db:
            store.db.execute("INSERT INTO batches VALUES (?,?)", (name, encode(batch)))
    except sqlite3.IntegrityError as exc:
        raise WorkflowError("이미 존재하는 연속 실행 이름입니다.") from exc
    return batch


def control(store, name, action):
    init(store)
    with store.db:
        store.db.execute("BEGIN IMMEDIATE")
        row = store.db.execute("SELECT data FROM batches WHERE name=?", (name,)).fetchone()
        if row is None:
            raise WorkflowError("연속 실행 묶음 없음: " + name)
        batch = json.loads(row[0])
        if batch["status"] in {"completed", "budget_limited"}:
            raise WorkflowError("종료된 묶음입니다. 새 실행 범위와 한도로 새 묶음을 만드세요.")
        if action == "pause":
            batch["status"] = "paused"
        elif action == "start":
            batch["status"] = "running"
            batch["error"] = None
            if not batch.get("deadline"):
                batch["deadline"] = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=batch["max_seconds"])).isoformat()
        else:
            raise WorkflowError("연속 실행 동작은 start 또는 pause입니다.")
        batch["control_revision"] = batch.get("control_revision", 0) + 1
        batch["updated_at"] = now()
        store.db.execute("UPDATE batches SET data=? WHERE name=?", (encode(batch), name))
    return batch


def task_done(task):
    from .cli import freshness
    from .workspaces import observed_snapshot
    if task["status"] != "completed" or not freshness(task, observed_snapshot(task))["completion_is_current"]:
        return False
    release = task.get("release")
    return not release or release.get("stage") == "deployed" or (release.get("stage") == "merged" and not release["config"].get("deploy"))


def _tick(store, batches):
    """Called by the single daemon. At most one owned job per batch is queued."""
    from .daemon import enqueue, init_queue, cancel
    from .policy import window_limit
    init_queue(store)
    for batch in batches:
        if batch["status"] not in {"running", "waiting"}:
            continue
        owned = store.db.execute("SELECT id FROM jobs WHERE batch_name=? ORDER BY rowid", (batch["name"],)).fetchall()
        new_ids = [row[0] for row in owned if row[0] not in batch["history"]]
        if new_ids:
            # Queue insertion survives a crash before this batch receipt is saved.
            batch["history"].extend(new_ids)
            if not batch["active_job"]:
                batch["active_job"] = new_ids[0]
            save(store, batch)
        active = store.db.execute("SELECT * FROM jobs WHERE id=?", (batch["active_job"],)).fetchone() if batch["active_job"] else None
        expired = dt.datetime.now(dt.timezone.utc) >= dt.datetime.fromisoformat(batch["deadline"])
        if expired:
            if active and active["status"] == "queued":
                cancel(store, active["id"])
            batch.update(status="budget_limited", error="연속 실행 시간 한도가 끝났습니다.")
            save(store, batch)
            continue
        if active:
            if active["status"] in {"queued", "running"}:
                continue
            batch["active_job"] = None
            if active["status"] != "succeeded" or not task_done(store.get(active["task_id"])):
                batch.update(status="blocked", error="실행 실패 또는 완료 증거 부족: " + active["task_id"])
                save(store, batch)
                continue
            batch["done"].append(active["task_id"])
        pending = [task_id for task_id in batch["task_ids"] if task_id not in batch["done"]]
        for task_id in pending[:]:
            if task_done(store.get(task_id)):
                batch["done"].append(task_id)
                pending.remove(task_id)
        if not pending:
            batch.update(status="completed", error=None)
            save(store, batch)
            continue
        if len(batch["history"]) >= batch["max_jobs"]:
            batch.update(status="budget_limited", error="연속 실행 횟수 한도에 도달했습니다.")
            save(store, batch)
            continue
        reason = None
        for task_id in pending:
            task = store.get(task_id)
            try:
                if not task.get("dependency_receipts"):
                    dependency_evidence(store, task)
                window_limit(task.get("project_config") or {})
                job = enqueue(store, task_id, deadline=batch["deadline"], batch_name=batch["name"])
            except WorkflowError as exc:
                reason = str(exc)
                continue
            batch.update(status="running", error=None, active_job=job["id"])
            batch["history"].append(job["id"])
            save(store, batch)
            break
        else:
            if batch["status"] != "waiting" or batch.get("error") != reason:
                batch.update(status="waiting", error=reason)
                save(store, batch)


def tick(store):
    for batch in list_batches(store):
        try:
            _tick(store, [batch])
        except ControlChanged:
            continue
        except (WorkflowError, OSError, ValueError) as exc:
            batch.update(status="blocked", error=str(exc))
            try:
                save(store, batch)
            except ControlChanged:
                pass
