"""Single-worker durable queue. A job records process outcome, not task completion.

Use a separate Store per thread. enqueue/list_jobs/cancel accept a caller-owned Store;
run_daemon opens its own connection. A crashed running job is interrupted, never retried.
"""
from __future__ import annotations

import fcntl
import os
from pathlib import Path
import signal
import subprocess
import sys
import uuid

from .core import Store, WorkflowError, now


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


def enqueue(store, task_id, action="pipeline", timeout=900, repairs=1):
    """Return a queued job dict; reject missing tasks, invalid options and active duplicates."""
    if action not in ("pipeline", "release"):
        raise WorkflowError("작업 유형은 pipeline 또는 release입니다.")
    if (type(timeout) is not int or not 1 <= timeout <= 86400
            or type(repairs) is not int or not 0 <= repairs <= 3):
        raise WorkflowError("repairs는 0~3, timeout은 1~86400초입니다.")
    store.get(task_id)
    init_queue(store)
    job_id = uuid.uuid4().hex
    import sqlite3
    try:
        with store.db:
            store.db.execute(
                "INSERT INTO jobs(id,task_id,action,status,timeout,repairs,created_at) "
                "VALUES(?,?,?,'queued',?,?,?)", (job_id, task_id, action, timeout, repairs, now()))
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


def _claim(store):
    # BEGIN IMMEDIATE serializes claiming against concurrent cancel/enqueue callers.
    try:
        store.db.execute("BEGIN IMMEDIATE")
        row = store.db.execute("SELECT * FROM jobs WHERE status='queued' ORDER BY rowid LIMIT 1").fetchone()
        if row is not None:
            store.db.execute("UPDATE jobs SET status='running',started_at=? WHERE id=?", (now(), row["id"]))
        store.db.commit()
    except BaseException:
        store.db.rollback()
        raise
    return dict(row) if row is not None else None


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
    if job["action"] == "pipeline":
        command += ["--repairs", str(job["repairs"])]
    process = None
    status, code, error = "failed", None, None
    try:
        with log_path.open("xb") as log:
            os.chmod(log_path, 0o600)
            process = subprocess.Popen(command, cwd=Path(__file__).resolve().parent.parent,
                                       stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                       start_new_session=True)
            with store.db:
                store.db.execute("UPDATE jobs SET pid=?,log_path=? WHERE id=?",
                                 (process.pid, str(log_path), job["id"]))
            while process.poll() is None:
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
            while not stop_event.is_set():
                job = _claim(store)
                if job is not None:
                    _run_job(store, job, stop_event)
                    processed += 1
                if once:
                    break
                if job is None:
                    stop_event.wait(0.25)
    finally:
        store.close()
    return processed
