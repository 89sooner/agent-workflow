"""Single-worker durable queue. A job records process outcome, not task completion.

Use a separate Store per thread. enqueue/list_jobs/cancel accept a caller-owned Store;
run_daemon opens its own connection. A crashed running job is interrupted, never retried.
"""
from __future__ import annotations

import fcntl
import json
import datetime as dt
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import uuid

from .core import Store, WorkflowError, ensure_not_held, now


def init_queue(store):
    """Idempotently initialize the queue on the same SQLite database as tasks."""
    store.db.executescript("""
        CREATE TABLE IF NOT EXISTS jobs (
          id TEXT PRIMARY KEY, task_id TEXT NOT NULL,
          action TEXT NOT NULL, status TEXT NOT NULL,
          timeout INTEGER NOT NULL, repairs INTEGER NOT NULL,
          created_at TEXT NOT NULL, started_at TEXT, finished_at TEXT,
          pid INTEGER, returncode INTEGER, log_path TEXT, error TEXT
        );
        CREATE UNIQUE INDEX IF NOT EXISTS jobs_one_active_task
          ON jobs(task_id) WHERE status IN ('queued', 'running');
    """)
    store.db.commit()
    try:
        store.db.execute("BEGIN IMMEDIATE")
        columns = {row[1] for row in store.db.execute("PRAGMA table_info(jobs)")}
        for name, declaration in (("options", "TEXT NOT NULL DEFAULT '{}'"), ("cancel_requested", "INTEGER NOT NULL DEFAULT 0"), ("batch_name", "TEXT")):
            if name not in columns:
                store.db.execute(f"ALTER TABLE jobs ADD COLUMN {name} {declaration}")
        store.db.commit()
    except BaseException:
        store.db.rollback()
        raise


def enqueue(store, task_id, action="pipeline", timeout=None, repairs=None, recoveries=None, deadline=None, batch_name=None, batch_resume=False):
    """Return a queued job dict; reject missing tasks, invalid options and active duplicates."""
    task = store.get(task_id)
    ensure_not_held(store, task)
    settings = task.get("execution") or {}
    timeout = settings.get("timeout", 900) if timeout is None else timeout
    repairs = settings.get("repairs", 1) if repairs is None else repairs
    if action not in ("pipeline", "release"):
        raise WorkflowError("작업 유형은 pipeline 또는 release입니다.")
    if (type(timeout) is not int or not 1 <= timeout <= 86400
            or type(repairs) is not int or not 0 <= repairs <= 3):
        raise WorkflowError("repairs는 0~3, timeout은 1~86400초입니다.")
    if recoveries is not None and (type(recoveries) is not int or not 0 <= recoveries <= 3):
        raise WorkflowError("recoveries는 0~3입니다.")
    if deadline is not None:
        try:
            moment = dt.datetime.fromisoformat(deadline)
            if moment.tzinfo is None:
                raise ValueError("timezone required")
        except (ValueError, TypeError) as exc:
            raise WorkflowError("실행 기한은 시간대가 포함된 ISO 날짜입니다.") from exc
    store.get(task_id)
    init_queue(store)
    job_id = uuid.uuid4().hex
    import sqlite3
    try:
        with store.db:
            store.db.execute("BEGIN IMMEDIATE")
            ensure_not_held(store, task)
            if batch_name:
                row = store.db.execute("SELECT data FROM batches WHERE name=?", (batch_name,)).fetchone()
                if row is None:
                    raise WorkflowError("연속 실행 묶음이 없습니다.")
                batch = json.loads(row[0])
                permitted = {"running", "waiting", "blocked", "paused"} if batch_resume else {"running", "waiting"}
                count = store.db.execute("SELECT COUNT(*) FROM jobs WHERE batch_name=?", (batch_name,)).fetchone()[0]
                if batch["status"] not in permitted or count >= batch["max_jobs"]:
                    raise WorkflowError("연속 실행 상태 또는 실행 횟수 한도로 새 시도를 등록할 수 없습니다.")
                if task_id not in batch["task_ids"] or deadline != batch.get("deadline") or not deadline or dt.datetime.now(dt.timezone.utc) >= dt.datetime.fromisoformat(deadline):
                    raise WorkflowError("연속 실행 범위 또는 기한이 유효하지 않습니다.")
            store.db.execute(
                "INSERT INTO jobs(id,task_id,action,status,timeout,repairs,created_at,options,batch_name) "
                "VALUES(?,?,?,'queued',?,?,?,?,?)", (job_id, task_id, action, timeout, repairs, now(), json.dumps({"recoveries": recoveries, "deadline": deadline}), batch_name))
    except sqlite3.IntegrityError as exc:
        raise WorkflowError("이 작업은 이미 큐에서 대기하거나 실행 중입니다.") from exc
    return dict(store.db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone())


def list_jobs(store):
    """Return job dicts newest first, including historical terminal jobs."""
    init_queue(store)
    return [dict(row) for row in store.db.execute("SELECT * FROM jobs ORDER BY rowid DESC")]


def cancel(store, job_id):
    """Cancel only queued jobs and return the updated dict; otherwise raise WorkflowError."""
    init_queue(store)
    with store.db:
        changed = store.db.execute(
            "UPDATE jobs SET status='cancelled',finished_at=? WHERE id=? AND status='queued'",
            (now(), job_id)).rowcount
        if not changed:
            raise WorkflowError("대기 중인 작업만 취소할 수 있습니다.")
    return dict(store.db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone())


def stop_job(store, job_id):
    init_queue(store)
    row = store.db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    if row is None:
        raise WorkflowError("실행 기록이 없습니다.")
    if row["status"] == "queued":
        return cancel(store, job_id)
    with store.db:
        changed = store.db.execute("UPDATE jobs SET cancel_requested=1 WHERE id=? AND status='running'", (job_id,)).rowcount
    if not changed:
        raise WorkflowError("현재 실행 중인 작업에만 중단을 요청할 수 있습니다.")
    return dict(store.db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone())


def resume_job(store, job_id):
    init_queue(store)
    row = store.db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    if row is None or row["status"] not in {"failed", "interrupted", "cancelled"}:
        raise WorkflowError("실패·중단·취소된 실행만 재개할 수 있습니다.")
    options = json.loads(row["options"] or "{}")
    deadline = options.get("deadline")
    if deadline and dt.datetime.now(dt.timezone.utc) >= dt.datetime.fromisoformat(deadline):
        raise WorkflowError("원래 실행 기한이 끝났습니다. 새 범위·한도로 실행을 등록하세요.")
    return enqueue(store, row["task_id"], row["action"], row["timeout"], row["repairs"], options.get("recoveries"),
                   deadline=deadline, batch_name=row["batch_name"], batch_resume=True)


def _claim(store):
    # Git/dependency reads can be slow. Do them without holding a DB write lock,
    # then atomically claim only a row which is still queued.
    rows = store.db.execute("SELECT * FROM jobs WHERE status='queued' ORDER BY rowid").fetchall()
    for candidate in rows:
        deadline = json.loads(candidate["options"] or "{}").get("deadline")
        if deadline and dt.datetime.now(dt.timezone.utc) >= dt.datetime.fromisoformat(deadline):
            with store.db:
                store.db.execute("UPDATE jobs SET status='cancelled',finished_at=?,error=? WHERE id=? AND status='queued'", (now(), "실행 기한 만료", candidate["id"]))
            continue
        task = store.get(candidate["task_id"])
        try:
            ensure_not_held(store, task)
            from .policy import window_limit
            window_limit(task.get("project_config") or {})
            if task.get("dependencies") and not task.get("dependency_receipts"):
                from .scheduling import dependency_evidence
                dependency_evidence(store, task)
        except WorkflowError as exc:
            if candidate["error"] != str(exc):
                with store.db:
                    store.db.execute("UPDATE jobs SET error=? WHERE id=? AND status='queued'", (str(exc), candidate["id"]))
            continue
        with store.db:
            claimed = store.db.execute("UPDATE jobs SET status='running',started_at=?,error=NULL WHERE id=? AND status='queued' AND NOT EXISTS (SELECT 1 FROM task_controls WHERE task_id=jobs.task_id)", (now(), candidate["id"])).rowcount
        if claimed:
            return dict(store.db.execute("SELECT * FROM jobs WHERE id=?", (candidate["id"],)).fetchone())
    return None


def _terminate(process):
    """Let the CLI signal handler terminate its own separately-sessioned agent children."""
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()


def _run_job(store, job, stop_event):
    log_dir = store.home / "jobs"
    log_dir.mkdir(mode=0o700, exist_ok=True)
    log_path = log_dir / (job["id"] + ".log")
    command = [sys.executable, "-m", "agent_workflow", "--home", str(store.home),
               job["action"], job["task_id"], "--timeout", str(job["timeout"])]
    options = json.loads(job.get("options") or "{}")
    if job["action"] == "pipeline":
        command += ["--repairs", str(job["repairs"])]
        options = json.loads(job.get("options") or "{}")
        if options.get("recoveries") is not None:
            command += ["--recoveries", str(options["recoveries"])]
    process = None
    status, code, error = "failed", None, None
    try:
        with log_path.open("xb") as log:
            os.chmod(log_path, 0o600)
            process = subprocess.Popen(command, cwd=Path(__file__).resolve().parent.parent,
                                       stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                       start_new_session=True,
                                       env={**os.environ, "AGENT_WORKFLOW_JOB_ID": job["id"]})
            with store.db:
                store.db.execute("UPDATE jobs SET pid=?,log_path=? WHERE id=?",
                                 (process.pid, str(log_path), job["id"]))
            while process.poll() is None:
                if options.get("deadline") and dt.datetime.now(dt.timezone.utc) >= dt.datetime.fromisoformat(options["deadline"]):
                    _terminate(process)
                    status, error = "interrupted", "연속 실행 시간 한도가 끝났습니다."
                    break
                requested = store.db.execute("SELECT cancel_requested FROM jobs WHERE id=?", (job["id"],)).fetchone()[0]
                if requested:
                    _terminate(process)
                    status, error = "cancelled", "사용자가 이 실행의 중단을 요청했습니다."
                    break
                if stop_event.wait(0.1):
                    _terminate(process)
                    status = "interrupted"
                    error = "데몬 종료 요청으로 실행을 중단했습니다."
                    break
            else:
                status = "succeeded" if process.returncode == 0 else "failed"
            code = process.returncode
    except BaseException as exc:
        if process is not None and process.poll() is None:
            _terminate(process)
            code = process.returncode
        error = str(exc)
        if not isinstance(exc, Exception):
            status = "interrupted"
            raise
    finally:
        with store.db:
            store.db.execute(
                "UPDATE jobs SET status=?,finished_at=?,returncode=?,log_path=?,error=? WHERE id=?",
                (status, now(), code, str(log_path), error, job["id"]))
        from .operations import publish_notification
        publish_notification(store, store.get(job["task_id"]), {"id": job["id"], "status": status, "error": error})


def run_daemon(home, stop_event, once=False):
    """Run until stop_event; once processes at most one job. Return processed job count.

    Exactly one daemon may own a state home. On startup, previously running jobs are
    marked interrupted without being executed again. Queued jobs survive restarts.
    Raises WorkflowError if another daemon holds the lock. Never starts a daemon itself.
    """
    store = Store(home)
    processed = 0
    try:
        with (store.home / "daemon.lock").open("a+") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise WorkflowError("이 상태 디렉터리의 데몬이 이미 실행 중입니다.") from exc
            init_queue(store)
            with store.db:
                store.db.execute(
                    "UPDATE jobs SET status='interrupted',finished_at=?,error=? WHERE status='running'",
                    (now(), "이전 데몬이 실행 도중 종료되었습니다. 자동 재시도하지 않습니다."))
            scheduled_at = 0
            while not stop_event.is_set():
                from .scheduling import tick
                if once or time.monotonic() - scheduled_at >= 5:
                    tick(store)
                    scheduled_at = time.monotonic()
                job = _claim(store)
                if job is not None:
                    _run_job(store, job, stop_event)
                    processed += 1
                    scheduled_at = 0
                if once:
                    break
                if job is None:
                    stop_event.wait(0.25)
    finally:
        store.close()
    return processed
