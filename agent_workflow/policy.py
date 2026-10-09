"""Scope checks and cross-worktree resource leases, held by managed children too."""
from __future__ import annotations

import contextlib
import datetime as dt
import fcntl
import hashlib
import json
import os
from pathlib import Path
import stat
import uuid
from zoneinfo import ZoneInfo

from .core import WorkflowError, atomic_write, encode, git, snapshot


def changed_files(project):
    project = Path(project).resolve()
    names = git(project, "diff", "--no-renames", "--name-only", "-z", "HEAD", binary=True)
    names += git(project, "diff", "--cached", "--no-renames", "--name-only", "-z", "HEAD", binary=True)
    names += git(project, "ls-files", "--others", "--exclude-standard", "-z", binary=True)
    index_entries = {}
    for record in git(project, "ls-files", "--stage", "-z", binary=True).split(b"\0"):
        if record:
            metadata, name = record.split(b"\t", 1)
            index_entries.setdefault(name, []).append(metadata)
    result = {}
    for raw in set(names.split(b"\0")) - {b""}:
        name = os.fsdecode(raw)
        path = project / name
        digest = hashlib.sha256()
        for metadata in index_entries.get(raw, []):
            digest.update(metadata + b"\0")
        if path.is_symlink():
            digest.update(b"link:" + os.fsencode(os.readlink(path)))
        elif not path.resolve().is_relative_to(project):
            digest.update(b"outside-project")
        elif path.is_file():
            digest.update(str(stat.S_IMODE(path.stat().st_mode)).encode())
            with path.open("rb") as stream:
                while chunk := stream.read(1024 * 1024):
                    digest.update(chunk)
        elif path.exists():
            digest.update(b"directory:" + snapshot(path)["fingerprint"].encode())
        else:
            digest.update(b"missing")
        result[name] = digest.hexdigest()
    return result


def under(name, parent):
    p, base = Path(name), Path(parent)
    return base == Path(".") or p == base or base in p.parents


def scope_check(task):
    cfg = task.get("project_config") or {}
    if not cfg:
        return
    if git(task["project"], "rev-parse", "HEAD") != task["baseline"]["head"]:
        raise WorkflowError("관리 구현 중 기준 HEAD가 바뀌었습니다. 변경과 통합 상태를 확인하세요.")
    initial = task["baseline"].get("changed_files", {})
    changes = changed_files(task["project"])
    paths = [name for name, digest in changes.items() if initial.get(name) != digest]
    # Restoring a pre-existing change is also a mutation requiring permission.
    paths.extend(name for name in initial if name not in changes)
    allowed = cfg.get("allowed_paths") or ["."]
    forbidden = cfg.get("forbidden_paths") or []
    root = Path(task["project"]).resolve()
    violations = [name for name in paths if not any(under(name, x) for x in allowed)
                  or any(under(name, x) for x in forbidden)
                  or not (root / name).resolve().is_relative_to(root)]
    if violations:
        raise WorkflowError("작업 범위 위반: " + ", ".join(sorted(set(violations))))


def window_limit(config, at=None):
    """Seconds until the next blocked interval; a crossing-midnight day is its start day."""
    at = at or dt.datetime.now(dt.timezone.utc)
    limit = 86400
    for window in config.get("blocked_windows", []):
        zone = ZoneInfo(window["timezone"])
        local = at.astimezone(zone)
        start_time = dt.time.fromisoformat(window["start"])
        end_time = dt.time.fromisoformat(window["end"])
        for offset in range(-1, 8):
            day = local.date() + dt.timedelta(days=offset)
            if day.weekday() not in window["days"]:
                continue
            start = dt.datetime.combine(day, start_time, zone).astimezone(dt.timezone.utc)
            end_day = day + dt.timedelta(days=int(end_time <= start_time))
            end = dt.datetime.combine(end_day, end_time, zone).astimezone(dt.timezone.utc)
            if start <= at < end:
                raise WorkflowError(f"실행 제한 시간대입니다: {window['timezone']} {window['start']}~{window['end']}")
            if start > at:
                limit = min(limit, max(1, int((start - at).total_seconds())))
    return limit


def preflight(store, task):
    window_limit(task.get("project_config") or {})
    if task.get("dependencies") and not task.get("dependency_receipts"):
        from .scheduling import dependency_evidence
        dependency_evidence(store, task)
    scope_check(task)


@contextlib.contextmanager
def reservation(store, task):
    cfg = task.get("project_config") or {}
    if not cfg:
        yield None
        return
    common = Path(git(task["project"], "rev-parse", "--git-common-dir"))
    if not common.is_absolute():
        common = Path(task["project"]) / common
    root = common.resolve() / "agent-workflow-resources"
    root.mkdir(exist_ok=True)
    token = uuid.uuid4().hex
    lease = (root / token).open("a+")
    fcntl.flock(lease, fcntl.LOCK_EX)
    own = {"task": task["id"], "home": str(store.home), "paths": cfg.get("allowed_paths") or ["."],
           "ports": [s["port"] for s in cfg.get("services", [])]}

    def active_records():
        path = root / "registry.json"
        records = json.loads(path.read_text()) if path.exists() else {}
        active = {}
        for key, item in records.items():
            if len(key) != 32 or any(c not in "0123456789abcdef" for c in key):
                raise WorkflowError("자원 예약 원장이 손상됐습니다.")
            with (root / key).open("a+") as probe:
                try:
                    fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    active[key] = item
        return active

    registered = False
    try:
        with (root / "registry.lock").open("a+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            records = active_records()
            for item in records.values():
                overlap = any(under(a, b) or under(b, a) for a in own["paths"] for b in item["paths"])
                ports = set(own["ports"]) & set(item["ports"])
                if overlap or ports:
                    raise WorkflowError("다른 작업이 파일 범위 또는 포트를 사용 중입니다: " + item["task"])
            records[token] = own
            atomic_write(root / "registry.json", encode(records))
            registered = True
        yield lease.fileno()
    finally:
        if registered:
            with (root / "registry.lock").open("a+") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                records = active_records()
                records.pop(token, None)
                atomic_write(root / "registry.json", encode(records))
        lease.close()
        (root / token).unlink(missing_ok=True)
