"""Local, explicitly confirmed handoff timing. Store identifiers and durations only."""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import sqlite3
from statistics import median
import time
import uuid

from .connections import state_home
from .core import WorkflowError


def wall_ms():
    return time.time_ns() // 1_000_000


def _scope(project):
    from .session_notes import _display, _git
    root = Path(os.fsdecode(_git(Path(project).expanduser().resolve(strict=True),
                                 "rev-parse", "--show-toplevel").removesuffix(b"\n")))
    branch = _display(_git(root, "branch", "--show-current").removesuffix(b"\n"))
    ref = branch or "detached:" + _display(_git(root, "rev-parse", "HEAD").strip())
    return str(root), ref


def _saved_scope(git):
    return git["project"], git["branch"] or "detached:" + git["head"]


@contextmanager
def _database(home, *, write=False, create=False):
    path = state_home(home) / "session-notes" / "measurements.sqlite3"
    if not create and not path.is_file():
        yield None
        return
    db = None
    try:
        if create:
            path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            os.close(os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600))
        db = sqlite3.connect(path.as_uri() + ("?mode=rw" if write else "?mode=ro"),
                             uri=True, timeout=5, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
        if create:
            # Keep schema initialization and the first write in the same transaction.
            db.execute("""CREATE TABLE IF NOT EXISTS timers(
                id TEXT PRIMARY KEY, project TEXT NOT NULL, ref TEXT NOT NULL,
                mode TEXT NOT NULL, state TEXT NOT NULL, started_ms INTEGER NOT NULL,
                finished_ms INTEGER, wrong_pick INTEGER, reexplanations INTEGER, evidence TEXT)""")
            db.execute("""CREATE UNIQUE INDEX IF NOT EXISTS one_resume ON timers(project, ref)
                WHERE state = 'active' AND mode IN ('manual','pickup')""")
            db.execute("""CREATE UNIQUE INDEX IF NOT EXISTS one_draft ON timers(project, ref)
                WHERE state = 'active' AND mode = 'close'""")
            db.execute("""CREATE TABLE IF NOT EXISTS closes(
                note_key TEXT PRIMARY KEY, project TEXT NOT NULL, ref TEXT NOT NULL, tool_ms INTEGER NOT NULL)""")
        if write and db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='timers'").fetchone():
            if "stale_evidence_used" not in {row[1] for row in db.execute("PRAGMA table_info(timers)")}:
                db.execute("ALTER TABLE timers ADD COLUMN stale_evidence_used INTEGER")
        yield db
        db.commit()
    except (sqlite3.Error, OSError) as exc:
        raise WorkflowError(f"세션 측정 기록을 읽거나 쓸 수 없습니다. {path}: {exc}") from exc
    finally:
        if db is not None:
            db.close()


def _receipt(row):
    data = dict(row)
    data["evidence"] = json.loads(data["evidence"]) if data["evidence"] else None
    if data["wrong_pick"] is not None:
        data["wrong_pick_count"] = data["wrong_pick"]
        data["wrong_pick"] = bool(data["wrong_pick"])
    used = data.get("stale_evidence_used")
    data["stale_evidence_used"] = None if used is None else bool(used)
    data["elapsed_ms"] = data["finished_ms"] - data["started_ms"] if data["state"] == "finished" else None
    return data


def _record(db, ident):
    row = db and db.execute("SELECT * FROM timers WHERE id=?", (ident,)).fetchone()
    if row is None:
        raise WorkflowError("해당 원장에 측정 ID가 없습니다.")
    return row


def _start(home, scope, mode, started, evidence=None):
    with _database(home, write=True, create=True) as db:
        row = db.execute("""SELECT * FROM timers WHERE project=? AND ref=? AND state='active'
                            AND (mode='close')=?""", (*scope, mode == "close")).fetchone()
        if row is not None:
            if row["mode"] != mode:
                raise WorkflowError(f"다른 방식의 재개 측정이 진행 중입니다. {row['id']}")
            if mode == "pickup" and json.loads(row["evidence"]) != evidence:
                raise WorkflowError(f"진행 중인 측정의 인계 근거가 바뀌었습니다. 이전 측정을 종료하거나 abandon 하세요. {row['id']}")
            return _receipt(row)
        ident = "tm-" + uuid.uuid4().hex
        db.execute("""INSERT INTO timers(id,project,ref,mode,state,started_ms,evidence)
                      VALUES(?,?,?,?,'active',?,?)""",
                   (ident, *scope, mode, started, json.dumps(evidence, sort_keys=True) if evidence else None))
        return _receipt(_record(db, ident))


def start_manual(home, project):
    started = wall_ms()
    return _start(home, _scope(project), "manual", started)


def start_close(home, project):
    started = wall_ms()
    return _start(home, _scope(project), "close", started)


def start_pickup(home, result, started, *, classic=False):
    git = result["current"]
    evidence = {"head": git["head"], "dirty_digest": git["dirty_digest"],
                "note": Path(result["resume"]).parent.name if result["found"] else None,
                "tasks": [{"id": task["id"], "updated_at": task["updated_at"],
                           "source_key": hashlib.sha256(task["show"].encode()).hexdigest()[:16],
                           "verification": task["evidence"]["verification"]} for task in result["registered_tasks"]]}
    if not classic:
        evidence["variant"] = "structured"
        # Bind the displayed packets (including independent hold state) without
        # copying task text or file paths into the measurement database.
        for entry, task in zip(evidence["tasks"], result["registered_tasks"]):
            packet = json.dumps(task["resume_packet"], ensure_ascii=True, sort_keys=True)
            entry["packet_digest"] = hashlib.sha256(packet.encode()).hexdigest()
    return _start(home, _saved_scope(git), "pickup", started, evidence)


def finish(home, ident, project, *, wrong_pick=False, wrong_picks=None, reexplanations=0, stale_evidence_used=None):
    """Confirm that work on the correct action is starting; replay only matching feedback."""
    ended = wall_ms()
    if type(wrong_pick) is not bool or type(reexplanations) is not int or not 0 <= reexplanations <= 100000:
        raise WorkflowError("재선택 여부와 0~100000 범위의 재설명 횟수가 필요합니다.")
    if wrong_picks is None:
        wrong_picks = int(wrong_pick)
    elif wrong_pick or type(wrong_picks) is not int or not 0 <= wrong_picks <= 100000:
        raise WorkflowError("잘못 선택한 작업 횟수를 0~100000 범위로 지정하세요.")
    if stale_evidence_used is not None and type(stale_evidence_used) is not bool:
        raise WorkflowError("오래된 근거를 현재 근거로 사용했는지 지정하세요.")
    root, ref = _scope(project)
    with _database(home, write=True) as db:
        if ident is None:
            row = db.execute("""SELECT * FROM timers WHERE project=? AND ref=? AND state='active'
                                AND mode IN ('manual','pickup')""", (root, ref)).fetchone() if db else None
            if row is None:
                raise WorkflowError("현재 작업 트리·브랜치에 진행 중인 재개 측정이 없습니다. 이전 측정은 ID로 조회·종료하세요.")
            ident = row["id"]
        row = _record(db, ident)
        if row["mode"] == "close":
            raise WorkflowError("메모 작성 시계는 close-session으로 종료하세요.")
        if row["project"] != root or (row["ref"] != ref and not wrong_picks):
            raise WorkflowError("시작한 작업 트리·브랜치를 확인하세요. 같은 트리에서 브랜치를 바로잡았다면 --wrong-pick을 사용하세요.")
        if row["state"] == "finished":
            if (row["wrong_pick"] != wrong_picks or row["reexplanations"] != reexplanations
                    or row["stale_evidence_used"] != stale_evidence_used):
                raise WorkflowError("이미 다른 피드백으로 종료된 측정입니다.")
            return _receipt(row)
        if row["state"] != "active" or ended < row["started_ms"]:
            raise WorkflowError("종료할 수 없는 측정입니다. 상태와 시스템 시각을 확인하세요.")
        db.execute("UPDATE timers SET state='finished',finished_ms=?,wrong_pick=?,reexplanations=?,stale_evidence_used=? WHERE id=?",
                   (ended, wrong_picks, reexplanations, stale_evidence_used, ident))
        return _receipt(_record(db, ident))


def abandon(home, ident):
    with _database(home, write=True) as db:
        row = _record(db, ident)
        if row["state"] == "finished":
            raise WorkflowError("완료된 측정은 abandon할 수 없습니다.")
        if row["state"] == "active":
            db.execute("UPDATE timers SET state='abandoned', finished_ms=? WHERE id=?", (wall_ms(), ident))
        return _receipt(_record(db, ident))


def record_close(home, note, started_ms):
    """Record the unique note's generation time. Finish a draft before external export."""
    scope = _saved_scope(note["git"])
    folder = Path(note["worklog"]).parent
    key = folder.parent.name + "/" + folder.name
    with _database(home, write=True, create=True) as db:
        tool_ms = note.get("close_tool_ms")
        if tool_ms is not None:
            db.execute("INSERT OR IGNORE INTO closes VALUES(?,?,?,?)", (key, *scope, tool_ms))
        draft = db.execute("""SELECT * FROM timers WHERE project=? AND ref=? AND mode='close'
                              AND state='active'""", scope).fetchone()
        if draft is None or draft["started_ms"] > started_ms:
            return None
        initial = note.get("close_started_ms", started_ms)
        end = initial if draft["started_ms"] <= initial <= started_ms else started_ms
        db.execute("UPDATE timers SET state='finished', finished_ms=?, evidence=? WHERE id=?",
                   (end, json.dumps({"note": folder.name}), draft["id"]))
        return _receipt(_record(db, draft["id"]))


def _median(values):
    return median(values) if values else None


def summary(home, project):
    scope = _scope(project)
    rows, times = [], []
    with _database(home) as db:
        # A simultaneous first writer may have opened the file but not committed its schema yet.
        if db and db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='timers'").fetchone():
            rows = db.execute("SELECT * FROM timers WHERE project=? AND ref=? ORDER BY started_ms, id", scope).fetchall()
            times = [r[0] for r in db.execute("SELECT tool_ms FROM closes WHERE project=? AND ref=?", scope)]
    report = {"project": scope[0], "ref": scope[1],
              "active": [_receipt(r) for r in rows if r["state"] == "active"]}
    def statistics(mode, samples):
        completed = [r for r in samples if r["state"] == "finished"]
        stats = {"finished": len(completed),
                 "active": sum(r["state"] == "active" for r in samples),
                 "abandoned": sum(r["state"] == "abandoned" for r in samples),
                 "median_elapsed_ms": _median([r["finished_ms"] - r["started_ms"] for r in completed])}
        if mode == "close":
            stats.update(tool_count=len(times), median_tool_ms=_median(times))
        else:
            known = [r["stale_evidence_used"] for r in completed
                     if "stale_evidence_used" in r.keys() and r["stale_evidence_used"] is not None]
            stats.update(wrong_picks=sum(r["wrong_pick"] for r in completed),
                         reexplanations=sum(r["reexplanations"] for r in completed),
                         stale_evidence_used=sum(known) if known else None, stale_evidence_reported=len(known))
        return stats
    for mode in ("manual", "pickup", "close"):
        report[mode] = statistics(mode, [r for r in rows if r["mode"] == mode])
    pickups = [r for r in rows if r["mode"] == "pickup"]
    report["pickup_variants"] = {
        variant: statistics("pickup", [r for r in pickups if json.loads(r["evidence"] or "{}").get("variant", "classic") == variant])
        for variant in ("classic", "structured")
    }
    baseline, pickup = (report[m]["median_elapsed_ms"] for m in ("manual", "pickup"))
    report["median_saved_ms"] = baseline - pickup if None not in (baseline, pickup) else None
    return report
