"""Durable task state and evidence. No agent output can directly mark a task done."""
from __future__ import annotations

import contextlib
import datetime as dt
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import uuid


class WorkflowError(Exception):
    pass


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def encode(value):
    return json.dumps(value, ensure_ascii=False, indent=2)


def atomic_write(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temp.open("x", encoding="utf-8") as f:
            os.chmod(temp, 0o600)
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def git(project, *args, binary=False):
    p = subprocess.run(["git", "-C", str(project), *args], capture_output=True, timeout=30)
    if p.returncode:
        raise WorkflowError("Git 확인 실패: " + p.stderr.decode(errors="replace").strip())
    return p.stdout if binary else p.stdout.decode("utf-8", errors="replace").strip()


def project_root(path):
    p = Path(path).expanduser().resolve(strict=True)
    root = Path(git(p, "rev-parse", "--show-toplevel")).resolve()
    # A single task belongs to a worktree, not a subdirectory with an ambiguous scope.
    return root


def snapshot(project):
    """Hash HEAD, index, and every nonignored worktree file, including untracked files.

    Ignored build outputs are not code identity. Submodule dirtiness participates too.
    Evidence is tied to content rather than modification times.
    """
    project = Path(project)
    head = git(project, "rev-parse", "HEAD")
    names = git(project, "ls-files", "-z", "--cached", "--others", "--exclude-standard", binary=True)
    h = hashlib.sha256(head.encode())
    h.update(git(project, "ls-files", "--stage", "-z", binary=True))
    for raw in sorted(set(names.split(b"\0")) - {b""}):
        p = project / os.fsdecode(raw)
        h.update(raw + b"\0")
        if p.exists() or p.is_symlink():
            st = p.lstat()
            # Directory sizes can change when ignored caches are generated.
            h.update(f"{st.st_mode}:{st.st_size if not p.is_dir() else 0}\0".encode())
        if p.is_symlink():
            h.update(b"symlink:" + os.fsencode(os.readlink(p)))
        elif p.is_file():
            with p.open("rb") as f:
                while chunk := f.read(1024 * 1024):
                    h.update(chunk)
        elif p.is_dir():
            # Gitlinks require separate handling; never silently ignore nested changes.
            h.update(snapshot(p)["fingerprint"].encode())
        else:
            h.update(b"missing")
    status = git(project, "status", "--porcelain=v1", "--untracked-files=all")
    h.update(status.encode())
    return {"head": head, "branch": git(project, "branch", "--show-current"),
            "fingerprint": h.hexdigest(), "dirty": bool(status), "status": status}


class Store:
    def __init__(self, home=None):
        self.home = Path(home or os.environ.get("AGENT_WORKFLOW_HOME") or
                         Path.home() / ".local/state/agent-workflow").expanduser().resolve()
        self.home.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.db = sqlite3.connect(self.home / "state.sqlite3", timeout=20)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript("""
          CREATE TABLE IF NOT EXISTS tasks (id TEXT PRIMARY KEY, data TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS events (
            seq INTEGER PRIMARY KEY, task_id TEXT NOT NULL, at TEXT NOT NULL,
            kind TEXT NOT NULL, data TEXT NOT NULL);
        """)
        self.db.commit()

    def close(self):
        self.db.close()

    def get(self, task_id):
        row = self.db.execute("SELECT data FROM tasks WHERE id=?", (task_id,)).fetchone()
        if row is None:
            raise WorkflowError(f"작업 없음: {task_id}")
        return json.loads(row[0])

    def all(self):
        return [json.loads(r[0]) for r in self.db.execute("SELECT data FROM tasks ORDER BY rowid DESC")]

    def save(self, task, kind, details=None, create=False):
        task["updated_at"] = now()
        with self.db:
            if create:
                try:
                    self.db.execute("INSERT INTO tasks VALUES (?,?)", (task["id"], encode(task)))
                except sqlite3.IntegrityError as exc:
                    raise WorkflowError("이미 존재하는 작업 ID: " + task["id"]) from exc
            else:
                self.db.execute("UPDATE tasks SET data=? WHERE id=?", (encode(task), task["id"]))
            self.db.execute("INSERT INTO events(task_id,at,kind,data) VALUES (?,?,?,?)",
                            (task["id"], now(), kind, encode(details or {})))

    def directory(self, task_id):
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}", task_id):
            raise WorkflowError("작업 ID는 영문·숫자·점·밑줄·하이픈 1~80자입니다.")
        p = self.home / "tasks" / task_id
        p.mkdir(parents=True, exist_ok=True, mode=0o700)
        return p

    @contextlib.contextmanager
    def lock(self, project):
        """One writer per physical worktree, shared by interactive and runner commands.

        The descriptor is passed into managed subprocesses so a killed parent does not
        allow a second runner while its immediate agent process remains alive.
        """
        # Git's per-worktree directory is shared even when runners use different
        # state homes. Linked worktrees have distinct git dirs and independent locks.
        git_dir = Path(git(project, "rev-parse", "--absolute-git-dir"))
        with (git_dir / "agent-workflow.lock").open("a+") as f:
            try:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise WorkflowError("이 워크트리는 다른 작업이 실행 중입니다.") from exc
            # Closing our descriptor (not LOCK_UN) retains child-held locks on crash.
            yield f.fileno()


def create_task(store, task_id, project, goal, criteria, refs, checks, owner, reviewer, scope):
    store.directory(task_id)
    project = project_root(project)
    if store.home.is_relative_to(project):
        raise WorkflowError("상태 디렉터리는 대상 Git 프로젝트 밖에 두세요.")
    resolved_refs = []
    for ref in refs:
        p = (project / ref).resolve()
        if not p.is_file():
            raise WorkflowError(f"참조 문서 없음: {p}")
        resolved_refs.append(str(p))
    if not goal.strip() or not criteria:
        raise WorkflowError("목표와 최소 한 개의 완료 조건이 필요합니다.")
    if reviewer != "none" and reviewer == owner:
        raise WorkflowError("실행 담당과 독립 검토자는 달라야 합니다.")
    with store.lock(project):
        baseline = snapshot(project)
        task = {"id": task_id, "project": str(project), "goal": goal,
                "criteria": criteria, "refs": resolved_refs, "checks": checks,
                "owner": owner, "reviewer": reviewer, "scope": scope,
                "status": "ready", "baseline": baseline, "created_at": now(),
                "checkpoint": {"summary": "등록됨", "next": "기준 확인 후 구현", "at": now()},
                "verification": None, "review": None, "implementation": None,
                "last_run": None, "blocker": None, "completed_fingerprint": None}
        store.save(task, "created", create=True)
        return task


def ready_for_completion(task, current):
    if not task["checks"]:
        raise WorkflowError("실제 검사 명령이 없어 완료 처리할 수 없습니다. check-add로 등록하세요.")
    v = task.get("verification") or {}
    if not v.get("passed") or v.get("fingerprint") != current["fingerprint"]:
        raise WorkflowError("검사가 실패·미실행 상태이거나 코드가 변경됐습니다. check를 실행하세요.")
    if task["reviewer"] != "none":
        r = task.get("review") or {}
        if (r.get("verdict") != "pass" or r.get("fingerprint") != current["fingerprint"]
                or r.get("reviewer") != task["reviewer"]):
            raise WorkflowError("현재 코드에 대한 독립 리뷰 통과 증거가 필요합니다.")


def complete(store, task):
    current = snapshot(task["project"])
    ready_for_completion(task, current)
    task.update(status="completed", blocker=None, completed_fingerprint=current["fingerprint"])
    summary = "필수 검사 완료 (이 작업은 독립 리뷰 미지정)" if task["reviewer"] == "none" else "필수 검사와 지정 리뷰 완료"
    task["checkpoint"] = {"summary": summary, "next": "다음 WP 선택 또는 승인된 통합 절차", "at": now()}
    store.save(task, "completed", current)
    return task
