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


class StaleTaskError(WorkflowError):
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


def git(project, *args, binary=False, timeout=30):
    p = subprocess.run(["git", "-C", str(project), *args], capture_output=True, timeout=timeout)
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


def reference_fingerprint(task):
    """Bind reference inputs without copying their contents into controller records."""
    digest = hashlib.sha256()
    for name in task.get("refs", []):
        path = Path(name)
        digest.update(os.fsencode(str(path)) + b"\0")
        # Preserve the declared path and any ancestor links, not just its target.
        for part in reversed((path, *path.parents)):
            if part.is_symlink():
                digest.update(os.fsencode(str(part)) + b"=" + os.fsencode(os.readlink(part)) + b"\0")
        try:
            digest.update(os.fsencode(str(path.resolve())) + b"\0")
            if path.is_file():
                digest.update(str(path.stat().st_mode).encode() + b"\0")
                contents = hashlib.sha256()
                with path.open("rb") as stream:
                    while chunk := stream.read(1024 * 1024):
                        contents.update(chunk)
                digest.update(contents.digest())
            else:
                return None
        except (OSError, RuntimeError):
            return None
    return digest.hexdigest()


def references_current(task, evidence, current=None):
    # Old tasks without references need no migration; old reference evidence must
    # be rechecked because its previous input contents cannot be reconstructed.
    if not task.get("refs"):
        return True
    current = current if current is not None else reference_fingerprint(task)
    return current is not None and (evidence or {}).get("refs_fingerprint") == current


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
          CREATE TABLE IF NOT EXISTS task_controls (task_id TEXT PRIMARY KEY, data TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS events (
            seq INTEGER PRIMARY KEY, task_id TEXT NOT NULL, at TEXT NOT NULL,
            kind TEXT NOT NULL, data TEXT NOT NULL);
        """)
        self.db.commit()
        try:
            self.db.execute("BEGIN IMMEDIATE")
            if "revision" not in {row[1] for row in self.db.execute("PRAGMA table_info(tasks)")}:
                self.db.execute("ALTER TABLE tasks ADD COLUMN revision INTEGER NOT NULL DEFAULT 0")
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise
        from .observability import ensure_schema
        ensure_schema(self)

    def close(self):
        self.db.close()

    def get(self, task_id):
        row = self.db.execute("SELECT t.data,t.revision,c.data AS control FROM tasks t LEFT JOIN task_controls c ON c.task_id=t.id WHERE t.id=?", (task_id,)).fetchone()
        if row is None:
            raise WorkflowError(f"작업 없음: {task_id}")
        return {**json.loads(row[0]), "revision": row[1], "hold": json.loads(row[2]) if row[2] else None}

    def all(self):
        return [{**json.loads(r[0]), "revision": r[1], "hold": json.loads(r[2]) if r[2] else None}
                for r in self.db.execute("SELECT t.data,t.revision,c.data FROM tasks t LEFT JOIN task_controls c ON c.task_id=t.id ORDER BY t.rowid DESC")]

    def save(self, task, kind, details=None, create=False):
        expected = task.get("revision", 0)
        saved = {**task, "updated_at": now(), "revision": expected + 1}
        # Live controls have their own atomic record. An executing process must
        # never clear a newer human hold by saving its older task snapshot.
        saved.pop("hold", None)
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            if kind == "completed":
                ensure_not_held(self, task)
            if create:
                try:
                    self.db.execute("INSERT INTO tasks(id,data,revision) VALUES (?,?,?)", (task["id"], encode(saved), saved["revision"]))
                except sqlite3.IntegrityError as exc:
                    raise WorkflowError("이미 존재하는 작업 ID: " + task["id"]) from exc
            else:
                changed = self.db.execute("UPDATE tasks SET data=?,revision=? WHERE id=? AND revision=?",
                                          (encode(saved), saved["revision"], task["id"], expected)).rowcount
                if not changed:
                    raise StaleTaskError("작업 상태가 다른 실행에서 변경됐습니다. 다시 조회한 뒤 재시도하세요: " + task["id"])
            self.db.execute("INSERT INTO events(task_id,at,kind,data) VALUES (?,?,?,?)",
                            (task["id"], now(), kind, encode(details or {})))
            from .observability import record_event
            record_event(self, saved, kind, details)
        task.update(updated_at=saved["updated_at"], revision=saved["revision"])
        row = self.db.execute("SELECT data FROM task_controls WHERE task_id=?", (task["id"],)).fetchone()
        task["hold"] = json.loads(row[0]) if row else None

    def directory(self, task_id):
        if not isinstance(task_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}", task_id):
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


def ensure_not_held(store, task):
    row = store.db.execute("SELECT data FROM task_controls WHERE task_id=?", (task["id"],)).fetchone()
    task["hold"] = json.loads(row[0]) if row else None
    if task["hold"]:
        raise WorkflowError("작업이 수동 보류 중입니다. unhold 후 재개하세요: " + task["hold"]["reason"])


def set_hold(store, task_id, reason):
    if not isinstance(reason, str) or not reason.strip() or len(reason) > 2000:
        raise WorkflowError("보류 사유는 1~2000자 문자열이어야 합니다.")
    task = store.get(task_id)
    control = {"reason": reason.strip(), "at": now()}
    with store.db:
        store.db.execute("INSERT INTO task_controls VALUES (?,?) ON CONFLICT(task_id) DO UPDATE SET data=excluded.data", (task_id, encode(control)))
        store.db.execute("INSERT INTO events(task_id,at,kind,data) VALUES (?,?,?,?)", (task_id, now(), "task_held", encode(control)))
        from .observability import record_event
        record_event(store, task, "task_held", control)
    return store.get(task_id)


def clear_hold(store, task_id):
    task = store.get(task_id)
    with store.db:
        removed = store.db.execute("DELETE FROM task_controls WHERE task_id=?", (task_id,)).rowcount
        if removed:
            store.db.execute("INSERT INTO events(task_id,at,kind,data) VALUES (?,?,?,?)", (task_id, now(), "task_unheld", "{}"))
            from .observability import record_event
            record_event(store, task, "task_unheld")
    return store.get(task_id)


def create_task(store, task_id, project, goal, criteria, refs, checks, owner, reviewer, scope, metadata=None):
    store.directory(task_id)
    project = project_root(project)
    if store.home.is_relative_to(project):
        raise WorkflowError("상태 디렉터리는 대상 Git 프로젝트 밖에 두세요.")
    resolved_refs = []
    for ref in refs:
        p = Path(os.path.abspath(project / ref))
        if not p.is_file():
            raise WorkflowError(f"참조 문서 없음: {p}")
        resolved_refs.append(str(p))
    if not isinstance(goal, str) or not goal.strip() or not criteria or any(not isinstance(x, str) or not x.strip() for x in criteria):
        raise WorkflowError("목표와 최소 한 개의 완료 조건이 필요합니다.")
    if owner not in {"codex", "claude"} or reviewer not in {"codex", "claude", "codex-session", "none"}:
        raise WorkflowError("지원하는 구현·검토 담당을 지정하세요.")
    if metadata is not None and set(metadata) - {"profile", "project_config", "execution", "workspace"}:
        raise WorkflowError("작업 설정 메타데이터 항목이 잘못됐습니다.")
    if reviewer != "none" and reviewer == owner:
        raise WorkflowError("실행 담당과 독립 검토자는 달라야 합니다.")
    with store.lock(project):
        baseline = snapshot(project)
        from .policy import changed_files, protected_snapshot
        baseline["changed_files"] = changed_files(project)
        task = {"id": task_id, "project": str(project), "goal": goal,
                "criteria": criteria, "refs": resolved_refs, "checks": checks,
                "owner": owner, "reviewer": reviewer, "scope": scope,
                "status": "ready", "baseline": baseline, "created_at": now(),
                "checkpoint": {"summary": "등록됨", "next": "기준 확인 후 구현", "at": now()},
                "verification": None, "review": None, "implementation": None,
                "last_run": None, "blocker": None, "completed_fingerprint": None,
                "execution": {"recoveries": 1}}
        task.update(metadata or {})
        baseline["protected_paths"] = protected_snapshot(project, (task.get("project_config") or {}).get("forbidden_paths", []))
        store.save(task, "created", create=True)
        return task


def ready_for_completion(task, current):
    from .policy import protected_current
    if not protected_current(task):
        raise WorkflowError("금지 경로의 기준 증거가 없거나 내용이 변경됐습니다. 작업 범위를 확인하세요.")
    if task.get("dependencies") and not task.get("dependency_receipts"):
        raise WorkflowError("선행 조건 확인 기록이 필요합니다. check로 검사하세요.")
    if not task["checks"]:
        raise WorkflowError("실제 검사 명령이 없어 완료 처리할 수 없습니다. check-add로 등록하세요.")
    v = task.get("verification") or {}
    if not v.get("passed") or v.get("fingerprint") != current["fingerprint"] or not references_current(task, v):
        raise WorkflowError("검사가 실패·미실행 상태이거나 코드·참조 문서가 변경됐습니다. check를 실행하세요.")
    if task["reviewer"] != "none":
        r = task.get("review") or {}
        if (r.get("verdict") != "pass" or r.get("fingerprint") != current["fingerprint"]
                or r.get("reviewer") != task["reviewer"] or not references_current(task, r)):
            raise WorkflowError("현재 코드에 대한 독립 리뷰 통과 증거가 필요합니다.")


def complete(store, task):
    ensure_not_held(store, task)
    from .policy import scope_check
    scope_check(task)
    current = snapshot(task["project"])
    ready_for_completion(task, current)
    task.update(status="completed", blocker=None, completed_fingerprint=current["fingerprint"])
    summary = "필수 검사 완료 (이 작업은 독립 리뷰 미지정)" if task["reviewer"] == "none" else "필수 검사와 지정 리뷰 완료"
    task["checkpoint"] = {"summary": summary, "next": "다음 WP 선택 또는 승인된 통합 절차", "at": now()}
    store.save(task, "completed", current)
    return task
