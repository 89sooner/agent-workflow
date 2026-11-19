"""Local handoffs for Git worktrees that have no registered workflow task."""
from __future__ import annotations

from datetime import datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import sqlite3
import stat
import subprocess
import tempfile

from .connections import ledger_home, load_registry, project_key, state_home
from .core import WorkflowError, atomic_write, now, snapshot


PATH_LIMIT = 100


def _git(project, *args, input_bytes=None):
    # Ignore inherited repository overrides so --project identifies the actual tree.
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_TERMINAL_PROMPT="0", GIT_NO_LAZY_FETCH="1", GIT_ALLOW_PROTOCOL="")
    command = ["git", "--no-optional-locks", "-C", str(project),
               "-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null",
               "-c", "core.untrackedCache=false", *args]
    result = subprocess.run(command, input=input_bytes, capture_output=True, env=env, timeout=60)
    if result.returncode:
        raise WorkflowError("Git 작업 트리와 상태를 확인할 수 없습니다.")
    return result.stdout


def _display(raw):
    return raw.decode("utf-8", errors="backslashreplace")


def _filter_drivers(root):
    # Git can invoke external clean filters while refreshing a changed path.
    raw = _git(root, "config", "--null", "--name-only", "--list")
    return {key.rsplit(b".", 1)[0].removeprefix(b"filter.") for key in raw.split(b"\0")
            if key.startswith(b"filter.") and key.endswith((b".clean", b".process"))}


def _filter_overrides(drivers):
    args = []
    for driver in sorted(drivers):
        for setting in ("clean=", "process=", "required=false"):
            args.extend(["-c", "filter." + os.fsdecode(driver) + "." + setting])
    return args


def _evidence_snapshot(project):
    """Use the evidence hash format with read-only Git, including in nested repositories."""
    filters = {}
    all_drivers = set()

    def read_git(root, *args, binary=False):
        if root not in filters:
            git_root = os.fsdecode(_git(root, "rev-parse", "--show-toplevel").removesuffix(b"\n"))
            if root != Path(git_root):
                raise WorkflowError("하위 저장소의 작업 트리를 확인할 수 없습니다.")
            filters[root] = _filter_drivers(root)
            all_drivers.update(filters[root])
        raw = _git(root, *_filter_overrides(all_drivers), *args)
        if filters[root] and "--others" in args and raw:
            # A clean filter can change Git status. Its fingerprint cannot be reproduced
            # faithfully without executing project code. Only decline when a path uses it.
            attrs = _git(root, "check-attr", "-z", "--stdin", "filter", input_bytes=raw)
            if filters[root].intersection(attrs.split(b"\0")[2::3]):
                raise WorkflowError("외부 Git 필터가 적용된 파일의 검증 지문을 조회할 수 없습니다.")
        return raw if binary else raw.decode("utf-8", errors="replace").strip()

    return snapshot(project, read_git=read_git)


def _snapshot(project, notes_root, exclude=None):
    directory = Path(project).expanduser().resolve(strict=True)
    root = Path(os.fsdecode(_git(directory, "rev-parse", "--show-toplevel").removesuffix(b"\n")))
    overrides = _filter_overrides(_filter_drivers(root))
    paths = ["--", "."]
    for path in (notes_root, exclude):
        if path is not None and path.is_relative_to(root):
            paths.append(":(top,exclude,literal)" + str(path.relative_to(root)))
    raw = _git(root, *overrides, "status", "--porcelain=v2", "-z", "--branch",
               "--no-ahead-behind", "--untracked-files=all", "--no-renames",
               "--ignore-submodules=dirty", *paths)
    rows = []
    head = branch = None
    for record in raw.split(b"\0"):
        if record.startswith(b"# branch.oid "):
            oid = record.removeprefix(b"# branch.oid ")
            head = None if oid == b"(initial)" else _display(oid)
        elif record.startswith(b"# branch.head "):
            ref = record.removeprefix(b"# branch.head ")
            branch = None if ref == b"(detached)" else _display(ref)
        elif record.startswith(b"? "):
            rows.append((record[2:], b"??"))
        elif record.startswith((b"1 ", b"u ")):
            fields = record.split(b" ", 8 if record.startswith(b"1 ") else 10)
            rows.append((fields[-1], fields[1]))

    rows.sort()
    digest = hashlib.sha256()
    for path, status in rows:
        digest.update(status + b"\0" + path + b"\0")
    return {
        "project": str(root), "branch": branch, "head": head,
        "dirty_count": len(rows), "dirty_digest": digest.hexdigest(),
        "submodule_contents_included": False,
        "dirty_paths": [{"status": _display(status), "path": _display(path)}
                        for path, status in rows[:PATH_LIMIT]],
    }


def _location(notes_root, git):
    identity = [git["project"], git["branch"], None if git["branch"] else git["head"]]
    key = hashlib.sha256(json.dumps(identity).encode()).hexdigest()
    return notes_root / key


def _latest(directory):
    try:
        names = [p for p in directory.iterdir() if re.fullmatch(r"[0-9]{12}", p.name)]
    except FileNotFoundError:
        return None
    return max(names, default=None)


def _read(folder):
    note = json.loads((folder / "note.json").read_text(encoding="utf-8"))
    return {**note, "worklog": str(folder / "현황.md"), "resume": str(folder / "재개용.md")}


def _markdown(note, resume):
    git = note["git"]
    title = "세션 재개" if resume else "세션 작업일지"
    lines = ["# " + title, "", note["created_at"], ""]
    sections = [("작업 요약", note["summary"]), ("다음 작업", note["next_action"])]
    if resume:
        sections.reverse()
    for heading, body in sections:
        lines.extend(["## " + heading, "", body, ""])
    lines.extend(["## 저장 당시 Git 상태", "",
                  "    " + json.dumps(git["project"], ensure_ascii=False),
                  "    " + (git["branch"] or "(detached HEAD)"),
                  "    " + (git["head"] or "(첫 커밋 전)"), "",
                  f"변경 경로 {git['dirty_count']}개 (하위 모듈 내부 파일 제외)", ""])
    for row in git["dirty_paths"]:
        lines.append("    " + row["status"] + "  " + json.dumps(row["path"], ensure_ascii=False))
    remaining = git["dirty_count"] - len(git["dirty_paths"])
    if remaining:
        lines.extend(["", f"그 외 {remaining}개 경로가 있습니다."])
    return "\n".join(lines) + "\n"


def _export_path(worklog_dir, note):
    project = Path(note["git"]["project"]).name
    label = re.sub(r"[^\w.-]+", "-", project).strip(".-")[:40] or "project"
    key = hashlib.sha256(os.fsencode(note["worklog"])).hexdigest()[:16]
    day = note.get("local_date", note["created_at"][:10])
    filename = f"{day}_{label}_{key}.md"
    return Path(worklog_dir).expanduser().resolve() / filename


def _export(note, worklog_dir):
    if worklog_dir is None:
        return note
    try:
        path = _export_path(worklog_dir, note)
        contents = Path(note["worklog"]).read_bytes()
        # The link publishes a complete file and fails if another writer got there first.
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".worklog-") as stage:
            stage.write(contents)
            stage.flush()
            os.fsync(stage.fileno())
            try:
                os.link(stage.name, path)
            except FileExistsError:
                fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
                with os.fdopen(fd, "rb") as existing:
                    meta = os.fstat(fd)
                    if (not stat.S_ISREG(meta.st_mode) or meta.st_size != len(contents)
                            or existing.read(len(contents) + 1) != contents):
                        raise WorkflowError(f"대상에 다른 내용이 있습니다. {path}")
    except (OSError, RuntimeError, WorkflowError) as exc:
        raise WorkflowError(f"작업일지 내보내기 실패. 로컬 작업일지 {note['worklog']}\n{exc}") from exc
    return {**note, "exported_worklog": str(path)}


def close_note(home, project, summary, next_action, worklog_dir=None):
    if not all(isinstance(value, str) and value.strip() for value in (summary, next_action)):
        raise WorkflowError("작업 요약과 다음 작업을 입력하세요.")
    notes_root = state_home(home) / "session-notes"
    git = _snapshot(project, notes_root)
    directory = _location(notes_root, git)
    notes_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    directory.mkdir(mode=0o700, exist_ok=True)
    with os.fdopen(os.open(directory / ".lock", os.O_RDWR | os.O_CREAT, 0o600), "rb") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        latest = _latest(directory)
        note = {"summary": summary.strip(), "next_action": next_action.strip(), "git": git}
        if latest is not None:
            previous = _read(latest)
            if previous["summary"] == note["summary"] and previous["next_action"] == note["next_action"]:
                comparable = git
                if worklog_dir is not None and previous["git"] != git:
                    # A previous export into this very worktree is itself a new Git path.
                    try:
                        candidate = _export_path(worklog_dir, previous)
                    except (OSError, RuntimeError):
                        # Commit the current note before reporting a target-path failure.
                        candidate = None
                    if candidate is not None and candidate.is_relative_to(Path(git["project"])):
                        comparable = _snapshot(project, notes_root, exclude=candidate)
                if previous["git"] == comparable:
                    return _export({**previous, "saved": False}, worklog_dir)
        note["created_at"] = now()
        note["local_date"] = datetime.fromisoformat(note["created_at"]).astimezone().date().isoformat()
        number = int(latest.name) + 1 if latest else 1
        folder = directory / f"{number:012d}"
        # Readers discover only complete, numbered directories.
        staging = Path(tempfile.mkdtemp(prefix=".pending-", dir=directory))
        try:
            atomic_write(staging / "note.json", json.dumps(note, ensure_ascii=True, indent=2))
            atomic_write(staging / "현황.md", _markdown(note, resume=False))
            atomic_write(staging / "재개용.md", _markdown(note, resume=True))
            staging.rename(folder)
        finally:
            if staging.exists():
                shutil.rmtree(staging)
        return _export({**_read(folder), "saved": True}, worklog_dir)


def _evidence(task, current, problem):
    from .cli import freshness

    verification = task.get("verification") or {}
    review = task.get("review") or {}
    review_required = task.get("reviewer") != "none"
    flags = {}
    if current is not None:
        try:
            flags = freshness(task, current)
            if task.get("refs") and flags["refs_fingerprint"] is None:
                problem = "등록된 참조 문서를 읽을 수 없습니다."
        except (OSError, WorkflowError, RuntimeError):
            problem = "등록된 검증 입력을 확인할 수 없습니다."

    def state(receipt, is_current, succeeded):
        if not receipt:
            return "missing"
        if problem:
            return "unavailable"
        if not is_current:
            return "stale"
        return "current" if succeeded else "failed"

    checks = state(verification, flags.get("verification_is_current"), verification.get("passed") is True)
    reviewed = state(review, flags.get("review_is_current"),
                     review.get("verdict", review.get("outcome")) == "pass"
                     and review.get("reviewer") == task.get("reviewer")) if review_required else "not_required"
    completed = state(task.get("status") == "completed", flags.get("completion_is_current"),
                      checks == "current" and reviewed in ("current", "not_required"))
    rerun = checks != "current" if task.get("checks") and checks != "unavailable" else None
    if problem:
        reason = problem
    elif checks == "stale" and not flags.get("protected_paths_are_current"):
        reason = "보호 경로 또는 보호 경로의 기준 기록이 변경됐습니다."
    elif checks == "stale" and task.get("refs") and verification.get("refs_fingerprint") != flags.get("refs_fingerprint"):
        reason = "검사 당시의 참조 문서 지문과 현재 지문이 다릅니다."
    elif checks == "stale":
        reason = "검사 당시의 작업 트리 지문과 현재 지문이 다르거나 기록에 지문이 없습니다."
    elif checks == "failed":
        reason = "현재 코드에서 마지막 검사가 실패했습니다."
    elif checks == "missing":
        reason = "검사 결과가 아직 등록되지 않았습니다."
    elif reviewed in ("failed", "stale", "missing"):
        reason = "현재 코드와 검토자에 해당하는 독립 리뷰 통과 기록이 필요합니다."
    else:
        reason = "현재 코드와 입력에 유효한 검사 결과가 있습니다."
    return {"verification": checks, "review": reviewed, "completion": completed,
            "rerun_checks": rerun, "reason": reason,
            "bound_via_release": bool(flags.get("evidence_bound_via_release") and not problem)}


def _registered_tasks(home, current):
    """List recorded checkpoints in this worktree and branch. Keep lookup independent of note storage."""
    primary = state_home(home)
    homes = {primary}
    unavailable = []
    try:
        projects = load_registry(primary)["projects"]
    except WorkflowError:
        projects = []
        unavailable.append(str(primary / "connections.json"))
    if projects:
        key = project_key(current["project"])
        for item in projects:
            if item["id"] == key:
                homes.update(ledger_home(ledger["home"]) for ledger in item["ledgers"])
    matches = []
    for source in sorted(homes):
        db = source / "state.sqlite3"
        if not db.is_file():
            if source != primary:
                unavailable.append(str(db))
            continue
        query = """
            SELECT id, data
              FROM tasks
             WHERE json_extract(data, '$.project') = :project
               AND (json_extract(data, '$.baseline.branch') = :branch
                    OR (json_extract(data, '$.release.commit') IS NOT NULL
                        AND json_extract(data, '$.release.config.branch') = :branch))
               AND (:branch <> '' OR json_extract(data, '$.baseline.head') = :head)
             ORDER BY json_extract(data, '$.updated_at') DESC, id LIMIT 6
        """
        branch = current["branch"]
        try:
            conn = sqlite3.connect(db.as_uri() + "?mode=ro", uri=True, timeout=1)
            try:
                rows = conn.execute(query, {"project": current["project"], "branch": branch or "",
                                            "head": current["head"]}).fetchall()
            finally:
                conn.close()
        except sqlite3.Error:
            unavailable.append(str(db))
            continue
        for task_id, data in rows:
            task = json.loads(data)
            matches.append((source, task_id, task))
    matches.sort(key=lambda item: item[2].get("updated_at") or "", reverse=True)
    visible = matches[:5]
    observed, problem = None, None
    if any(task.get("verification") or task.get("review") or task.get("status") == "completed"
           for _, _, task in visible):
        try:
            observed = _evidence_snapshot(current["project"])
            if observed["head"] != current["head"] or (observed["branch"] or None) != current["branch"]:
                problem = "조회 중 HEAD 또는 브랜치가 변경됐습니다. pickup을 다시 실행하세요."
        except (OSError, WorkflowError, subprocess.TimeoutExpired, RecursionError) as exc:
            problem = str(exc) if isinstance(exc, WorkflowError) else "현재 작업 트리의 지문을 읽을 수 없습니다."

    from .release_status import summarize_release
    tasks = []
    for source, task_id, task in visible:
        review = task.get("review") or {}
        tasks.append({"id": task_id, "recorded_status": task.get("status"),
                      "updated_at": task.get("updated_at"), "next_action": (task.get("checkpoint") or {}).get("next"),
                      "recorded_checks_passed": (task.get("verification") or {}).get("passed"),
                      "recorded_review": review.get("outcome", review.get("verdict")),
                      "evidence": _evidence(task, observed, problem),
                      "release_status": summarize_release(task),
                      "show": shlex.join(["agent-workflow", "--home", str(source), "show", task_id])})
    return {"registered_tasks": tasks, "more_registered_tasks": len(matches) > 5,
            "unavailable_ledgers": unavailable}


def pickup(home, project):
    notes_root = state_home(home) / "session-notes"
    current = _snapshot(project, notes_root)
    context_folder = Path(current["project"]) / "agent-context"
    project_context = [str(context_folder / name) for name in
                       ("_handoff/context-index.md", "session-summary.md", "todos.md")
                       if (context_folder / name).is_file()]
    result = {"current": current, "project_context": project_context, **_registered_tasks(home, current)}
    latest = _latest(_location(notes_root, current))
    if latest is None:
        return {**result, "found": False, "head_changed": None, "dirty_paths_changed": None}
    note = _read(latest)
    return {
        **note, **result, "found": True,
        "head_changed": note["git"]["head"] != current["head"],
        "dirty_paths_changed": note["git"]["dirty_digest"] != current["dirty_digest"],
    }
