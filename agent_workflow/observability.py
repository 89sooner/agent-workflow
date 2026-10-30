"""Controller-native execution traces. Observing a run never changes its outcome.

Task evidence remains in core.Store. These append-only activity records and finite
process spans describe what the controller actually observed, not model thoughts
or an inferred conversation between independent CLIs.
"""
from __future__ import annotations

import contextlib
import contextvars
import functools
import json
import os
from pathlib import Path
import time
import uuid

from .core import WorkflowError, encode, now

_context = contextvars.ContextVar("workflow_trace", default=None)
SCHEMA_VERSION = 1
MAX_SPANS = 250
MAX_EVENTS = 500


def ensure_schema(store):
    store.db.executescript("""
      CREATE TABLE IF NOT EXISTS observation_runs (
        id TEXT PRIMARY KEY, task_id TEXT NOT NULL, action TEXT NOT NULL,
        started_at TEXT NOT NULL, ended_at TEXT, status TEXT NOT NULL,
        owner_pid INTEGER NOT NULL, owner_identity TEXT, error TEXT, job_id TEXT);
      CREATE INDEX IF NOT EXISTS observation_runs_task ON observation_runs(task_id,started_at);
      CREATE TABLE IF NOT EXISTS observation_spans (
        sequence INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE NOT NULL,
        run_id TEXT NOT NULL, task_id TEXT NOT NULL, parent_id TEXT,
        previous_span_id TEXT, actor TEXT NOT NULL, role TEXT NOT NULL,
        label TEXT NOT NULL, status TEXT NOT NULL, started_at TEXT NOT NULL,
        ended_at TEXT, duration_ms REAL, owner_pid INTEGER NOT NULL,
        owner_identity TEXT, data TEXT NOT NULL);
      CREATE INDEX IF NOT EXISTS observation_spans_run ON observation_spans(run_id,sequence);
      CREATE TABLE IF NOT EXISTS observation_events (
        seq INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL,
        run_id TEXT, span_id TEXT, at TEXT NOT NULL, kind TEXT NOT NULL,
        actor TEXT NOT NULL, recipient TEXT, summary TEXT NOT NULL,
        status TEXT, data TEXT NOT NULL);
      CREATE INDEX IF NOT EXISTS observation_events_task ON observation_events(task_id,seq);
      CREATE INDEX IF NOT EXISTS observation_events_run ON observation_events(run_id,seq);
    """)
    if "job_id" not in {row[1] for row in store.db.execute("PRAGMA table_info(observation_runs)")}:
        store.db.execute("ALTER TABLE observation_runs ADD COLUMN job_id TEXT")
    store.db.commit()


def _identity(pid):
    try:
        return Path(f"/proc/{pid}/stat").read_text().rsplit(") ", 1)[1].split()[19]
    except (OSError, IndexError):
        return None


def _effective_status(row):
    if row["status"] == "running" and (not row["owner_identity"] or
            _identity(row["owner_pid"]) != row["owner_identity"]):
        return "unknown"
    return row["status"]


def _text(value, limit=2000):
    return str(value)[:limit] if value is not None else ""


def _active(store, task_id):
    current = _context.get()
    return current if current and current["home"] == str(store.home) and current["task_id"] == task_id else None


def actor_for(task, role, agent=None):
    if role in {"implement", "review"}:
        provider = agent or task.get("reviewer" if role == "review" else "owner", "unknown")
        return f"{provider}:{role}"
    return "release" if role in {"integration_check", "deploy", "health"} else "checks" if role == "check" else "controller"


def _role_label(role):
    return {"implement": "구현", "review": "독립 리뷰", "check": "필수 검사", "setup": "환경 준비",
            "integration_check": "병합 결과 검사", "deploy": "배포", "health": "배포 상태 확인"}.get(role, "명령 실행")


def _event(store, task_id, kind, actor="controller", recipient=None, summary="", status=None,
           data=None, run_id=None, span_id=None, at=None):
    current = _active(store, task_id)
    store.db.execute("""INSERT INTO observation_events
        (task_id,run_id,span_id,at,kind,actor,recipient,summary,status,data)
        VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (task_id, run_id or (current or {}).get("run_id"), span_id or (current or {}).get("span_id"),
         at or now(), kind, actor, recipient, _text(summary), status, encode(data or {})))


_EVENT_LABELS = {
    "created": "작업 등록", "agent_started": "에이전트에 작업 지시", "implementation_returned": "구현 결과 수신",
    "review_finished": "독립 리뷰 결과 수신", "checks_started": "필수 검사 시작", "checks_finished": "검사 결과 수신",
    "checks_interrupted": "검사 중단", "agent_failed": "에이전트 실행 오류", "recovery_started": "복구 시작",
    "recovery_checked": "중단된 구현 검증", "model_rerouted": "설정된 대체 모델 선택", "completed": "로컬 완료 증거 저장",
    "blocked": "작업 중단", "checkpoint": "체크포인트 저장", "held": "작업 보류", "unheld": "보류 해제",
    "hold_set": "작업 보류", "hold_cleared": "보류 해제", "task_held": "작업 보류", "task_unheld": "보류 해제",
    "release_configured": "릴리스 설정", "release_preparing": "통합 준비", "release_committed": "검증한 변경 커밋",
    "release_pushed": "브랜치 푸시", "release_pull_request": "PR 생성", "release_merged": "병합 완료",
    "release_deploying": "배포 시작", "release_deployed": "배포와 상태 확인 완료", "release_failed": "릴리스 중단",
    "release_updated": "릴리스 기록 갱신", "closure_finished": "결과와 인계 저장", "workspace_prepared": "작업 환경 준비",
    "setup_started": "환경 준비 시작", "setup_failed": "환경 준비 실패", "documents_updated": "관리 문서 갱신",
    "service_started": "검사 서비스 시작", "service_stopped": "검사 서비스 종료", "usage_reconciled": "중단된 실행 시간 확인",
    "dependencies_ready": "선행 조건 확인", "dependency_prepared": "선행 결과 반영",
}


def _public_details(kind, details):
    source = details if isinstance(details, dict) else {}
    result = {}
    for key in ("outcome", "verdict", "summary", "next_action", "reason", "error", "role", "agent", "model",
                "fingerprint", "commit", "merge_commit", "deployed_revision", "stage", "strategy", "kind"):
        if isinstance(source.get(key), (str, int, float, bool)):
            result[key] = _text(source[key])
    for key in ("passed", "code_changed_during_checks", "references_changed_during_checks"):
        if type(source.get(key)) is bool:
            result[key] = source[key]
    if isinstance(source.get("findings"), list):
        result["findings"] = [{"blocking": item.get("blocking") is True,
                               "detail": _text(item.get("detail")), "evidence": _text(item.get("evidence"))}
                              for item in source["findings"][:50] if isinstance(item, dict)]
    if kind == "checks_finished":
        checks = source.get("results") or []
        result["checks"] = [{key: item.get(key) for key in ("exit_code", "timed_out", "seconds")}
                            for item in checks[:200] if isinstance(item, dict)]
    return result


def record_event(store, task, kind, details=None):
    """Called inside Store.save's transaction; do not save the task recursively."""
    if kind not in _EVENT_LABELS and not kind.startswith("release_"):
        return
    data = _public_details(kind, details)
    role = data.get("role")
    actor, recipient = "controller", None
    if kind == "agent_started":
        recipient = actor_for(task, role, data.get("agent"))
    elif kind == "implementation_returned":
        actor, recipient, role = actor_for(task, "implement"), "controller", "implement"
    elif kind == "review_finished":
        actor, recipient, role = actor_for(task, "review"), "controller", "review"
    elif kind == "checks_started":
        recipient, role = "checks", "check"
    elif kind.startswith("checks_"):
        actor, recipient, role = "checks", "controller", "check"
    elif kind.startswith("release_"):
        actor, recipient = "release", "controller"
    elif kind == "agent_failed":
        actor, recipient = actor_for(task, role or "implement"), "controller"
    status = data.get("outcome") or data.get("verdict")
    if "passed" in data:
        status = "pass" if data["passed"] else "failed"
    if kind in {"blocked", "agent_failed", "checks_interrupted", "release_failed"}:
        status = "blocked" if kind == "blocked" else "failed"
    summary = data.get("summary") or data.get("reason") or data.get("error") or _EVENT_LABELS.get(kind, "릴리스 기록 갱신")
    current = _active(store, task["id"])
    run_id = None
    if not current and kind in {"task_held", "task_unheld", "held", "unheld", "hold_set", "hold_cleared"}:
        active_run = store.db.execute("SELECT * FROM observation_runs WHERE task_id=? AND status='running' ORDER BY rowid DESC LIMIT 1", (task["id"],)).fetchone()
        if active_run and _effective_status(active_run) == "running":
            run_id = active_run["id"]
    span_id = (current or {}).get("span_id")
    if current and not span_id and role and kind in {"implementation_returned", "review_finished", "agent_failed", "checks_finished"}:
        row = store.db.execute("SELECT id,data FROM observation_spans WHERE run_id=? AND role=? ORDER BY sequence DESC LIMIT 1",
                               (current["run_id"], role)).fetchone()
        if row:
            span_id = row["id"]
            span_data = json.loads(row["data"])
            span_data["result"] = data
            store.db.execute("UPDATE observation_spans SET data=? WHERE id=?", (encode(span_data), span_id))
    _event(store, task["id"], kind, actor, recipient, summary, status, data, run_id=run_id, span_id=span_id)


@contextlib.contextmanager
def run_context(store, task, action):
    if _active(store, task["id"]):
        yield _context.get()["run_id"]
        return
    run_id = uuid.uuid4().hex
    job_id = os.environ.get("AGENT_WORKFLOW_JOB_ID")
    if job_id:
        has_jobs = store.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='jobs'").fetchone()
        job = store.db.execute("SELECT 1 FROM jobs WHERE id=? AND task_id=? AND status='running'", (job_id, task["id"])).fetchone() if has_jobs else None
        if not job:
            job_id = None
    with store.db:
        store.db.execute("INSERT INTO observation_runs (id,task_id,action,started_at,ended_at,status,owner_pid,owner_identity,error,job_id) VALUES (?,?,?,?,?,?,?,?,?,?)",
                         (run_id, task["id"], action, now(), None, "running", os.getpid(), _identity(os.getpid()), None, job_id))
    token = _context.set({"home": str(store.home), "task_id": task["id"], "run_id": run_id, "span_id": None})
    with store.db:
        _event(store, task["id"], "run_started", summary="실행 시작", status="running", data={"action": action})
    outcome, error = "succeeded", None
    try:
        yield run_id
        latest = store.db.execute("SELECT status FROM observation_spans WHERE run_id=? ORDER BY sequence DESC LIMIT 1", (run_id,)).fetchone()
        if store.get(task["id"]).get("status") == "blocked" or (latest and latest[0] in {"failed", "interrupted"}):
            outcome = "blocked"
    except BaseException as exc:
        outcome = "interrupted" if isinstance(exc, (KeyboardInterrupt, SystemExit)) else "blocked"
        error = _text(str(exc) or type(exc).__name__)
        raise
    finally:
        try:
            with store.db:
                store.db.execute("UPDATE observation_runs SET ended_at=?,status=?,error=? WHERE id=?", (now(), outcome, error, run_id))
                _event(store, task["id"], "run_finished", summary=error or "실행 종료", status=outcome)
        finally:
            _context.reset(token)


def traced(action):
    """Wrap a public controller operation; nested operations share its trace."""
    def decorator(function):
        @functools.wraps(function)
        def call(store, task_or_id, *args, **kwargs):
            task = task_or_id if isinstance(task_or_id, dict) else store.get(task_or_id)
            with run_context(store, task, action):
                return function(store, task_or_id, *args, **kwargs)
        return call
    return decorator


def _directory(store, task_id, directory):
    directory = Path(directory).resolve()
    task_root = (store.home / "tasks" / task_id).resolve()
    if not directory.is_relative_to(task_root):
        return None
    return str(directory.relative_to(store.home))


@contextlib.contextmanager
def process_span(store, task, directory, command, agent=None, role=None, model=None):
    if role is None:
        role = "process"
    actor = actor_for(task, role, agent)
    with run_context(store, task, role):
        current = _active(store, task["id"])
        span_id = uuid.uuid4().hex
        previous = store.db.execute("SELECT id FROM observation_spans WHERE run_id=? ORDER BY sequence DESC LIMIT 1", (current["run_id"],)).fetchone()
        data = {"model": model, "directory": _directory(store, task["id"], directory),
                "input": {"goal": _text(task.get("goal")), "criteria": [_text(x) for x in task.get("criteria", [])[:50]]},
                "executable": Path(str(command[0])).name if command else None, "usage": None, "result": None}
        with store.db:
            store.db.execute("""INSERT INTO observation_spans
                (id,run_id,task_id,parent_id,previous_span_id,actor,role,label,status,started_at,owner_pid,owner_identity,data)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (span_id, current["run_id"], task["id"], current.get("span_id"), previous[0] if previous else None,
                 actor, role, _role_label(role), "running", now(), os.getpid(), _identity(os.getpid()), encode(data)))
            _event(store, task["id"], "process_started", "controller", actor, _role_label(role) + " 실행 지시", "running", span_id=span_id)
        token = _context.set({**current, "span_id": span_id})
        started = time.monotonic()
        result = {"outcome": None, "usage": None}
        error = None
        try:
            yield result
        except BaseException as exc:
            error = "interrupted" if isinstance(exc, (KeyboardInterrupt, SystemExit)) else "failed"
            result["error"] = _text(str(exc) or type(exc).__name__)
            raise
        finally:
            process_result = result.get("outcome") or {}
            status = error or ("succeeded" if process_result.get("exit_code") == 0 and not process_result.get("timed_out") else "failed")
            data["usage"] = result.get("usage")
            data["process"] = {key: process_result.get(key) for key in ("exit_code", "timed_out", "seconds")}
            if result.get("error"):
                data["error"] = result["error"]
            try:
                with store.db:
                    store.db.execute("UPDATE observation_spans SET ended_at=?,duration_ms=?,status=?,data=? WHERE id=?",
                                     (now(), round(max(0, time.monotonic() - started) * 1000, 3), status, encode(data), span_id))
                    _event(store, task["id"], "process_finished", actor, "controller", _role_label(role) + " 프로세스 종료", status, data["process"], span_id=span_id)
            finally:
                _context.reset(token)


def _span(row):
    item = dict(row)
    data = json.loads(item.pop("data"))
    item["status"] = _effective_status(row)
    for key in ("owner_pid", "owner_identity"):
        item.pop(key, None)
    item.update({key: data.get(key) for key in ("model", "usage", "input", "result", "process", "error")})
    item["artifacts"] = ["stdout", "stderr"] if data.get("directory") else []
    return item


def _activity(row):
    item = dict(row)
    item.pop("task_id", None)
    item["data"] = json.loads(item["data"])
    return item


def _runs(store, task_id):
    rows = store.db.execute("""SELECT r.*,(SELECT count(*) FROM observation_spans s WHERE s.run_id=r.id) span_count
        FROM observation_runs r WHERE task_id=? ORDER BY rowid DESC LIMIT 31""", (task_id,)).fetchall()
    result = []
    for row in rows[:30]:
        item = dict(row)
        item["status"] = _effective_status(row)
        item.pop("owner_pid", None); item.pop("owner_identity", None)
        result.append(item)
    return result, len(rows) > 30


def _edges(spans):
    by_id = {item["id"]: item for item in spans}
    result = []
    for span in spans:
        previous = by_id.get(span["previous_span_id"])
        if not previous:
            continue
        labels = {("implement", "check"): "구현 결과 → 검사", ("check", "review"): "검사 결과 → 독립 리뷰",
                  ("review", "implement"): "리뷰 피드백 → 재구현", ("check", "implement"): "검사 결과 → 재구현",
                  ("integration_check", "deploy"): "통합 검증 → 배포", ("deploy", "health"): "배포 결과 → 상태 확인"}
        result.append({"id": previous["id"] + ":" + span["id"], "source": previous["id"], "target": span["id"],
                       "kind": "handoff" if previous["actor"] != span["actor"] else "sequence",
                       "label": labels.get((previous["role"], span["role"]), "컨트롤러의 다음 실행"), "event_seq": None})
    return result


def _actors(task, spans, run_status=None):
    roles = [("controller", "컨트롤러", "controller")]
    if spans:
        for span in spans:
            if span["role"] in {"implement", "review"} and not any(item[0] == span["actor"] for item in roles):
                roles.append((span["actor"], span["actor"].split(":", 1)[0], span["role"]))
    else:
        roles.append((actor_for(task, "implement"), task.get("owner", "미지정"), "implement"))
        if task.get("reviewer") != "none":
            roles.append((actor_for(task, "review"), task.get("reviewer", "미지정"), "review"))
    roles += [("checks", "검사 실행기", "check"), ("release", "통합·배포", "release")]
    actors = []
    for actor_id, label, role in roles:
        related = [span for span in spans if span["actor"] == actor_id]
        latest = related[-1] if related else None
        status = (latest or {}).get("status", "idle")
        if actor_id == "controller" and run_status == "running":
            status = "running"
        if task.get("hold") and not spans:
            status = "held"
        actors.append({"id": actor_id, "label": label, "role": role, "status": status,
                       "span_id": latest["id"] if latest else None, "task_id": task["id"],
                       "task_goal": task.get("goal"), "last_seen": (latest or {}).get("ended_at") or (latest or {}).get("started_at")})
    return actors


def view(store, task_id, run_id=None, before=None, limit=200, exporting=False):
    if type(limit) is not int or not 1 <= limit <= MAX_EVENTS:
        raise WorkflowError(f"limit는 1~{MAX_EVENTS} 정수입니다.")
    if before is not None and (type(before) is not int or before < 1):
        raise WorkflowError("before는 양의 이벤트 순번입니다.")
    task = store.get(task_id)
    runs, more_runs = _runs(store, task_id)
    if run_id:
        selected = store.db.execute("SELECT * FROM observation_runs WHERE id=? AND task_id=?", (run_id, task_id)).fetchone()
        if not selected:
            raise WorkflowError("이 작업에 속한 실행 기록이 없습니다.")
    else:
        # A rejected duplicate request must not hide the already running worker.
        active = next((row for row in store.db.execute("SELECT * FROM observation_runs WHERE task_id=? AND status='running' ORDER BY rowid DESC", (task_id,))
                       if _effective_status(row) == "running"), None)
        run_id = active["id"] if active else runs[0]["id"] if runs else None
    if run_id and not any(item["id"] == run_id for item in runs):
        selected = store.db.execute("SELECT r.*,(SELECT count(*) FROM observation_spans s WHERE s.run_id=r.id) span_count FROM observation_runs r WHERE r.id=?", (run_id,)).fetchone()
        item = dict(selected); item["status"] = _effective_status(selected)
        item.pop("owner_pid", None); item.pop("owner_identity", None)
        runs.insert(0, item)
    run_status = next((item["status"] for item in runs if item["id"] == run_id), None)
    cap = 5000 if exporting else MAX_SPANS
    rows = store.db.execute("SELECT * FROM observation_spans WHERE task_id=? AND run_id=? ORDER BY sequence DESC LIMIT ?", (task_id, run_id, cap + 1)).fetchall() if run_id else []
    spans = [_span(row) for row in reversed(rows[:cap])]
    params = [task_id]
    where = "task_id=?"
    if run_id:
        where += " AND run_id=?"; params.append(run_id)
    if before is not None:
        where += " AND seq<?"; params.append(before)
    event_limit = 10000 if exporting else limit
    activities = store.db.execute(f"SELECT * FROM observation_events WHERE {where} ORDER BY seq DESC LIMIT ?", (*params, event_limit + 1)).fetchall()
    legacy = not runs
    if legacy:
        legacy_where, legacy_params = "task_id=?", [task_id]
        if before is not None:
            legacy_where += " AND seq<?"; legacy_params.append(before)
        old = store.db.execute(f"SELECT seq,at,kind,data FROM events WHERE {legacy_where} ORDER BY seq DESC LIMIT ?", (*legacy_params, event_limit + 1)).fetchall()
        activities = [{"seq": row["seq"], "run_id": None, "span_id": None, "at": row["at"], "kind": row["kind"],
                       "actor": "controller", "recipient": None, "summary": _EVENT_LABELS.get(row["kind"], row["kind"]),
                       "status": None, "data": encode(_public_details(row["kind"], json.loads(row["data"])))} for row in old]
    events = [_activity(row) for row in reversed(activities[:event_limit])]
    warnings = []
    if legacy:
        warnings.append("이 작업에는 단계별 관측 기록이 없습니다. 기존 이벤트만 표시하며 실행 시간과 대화를 추정하지 않습니다.")
    if any(span["status"] == "unknown" for span in spans):
        warnings.append("종료 기록 없이 실행 프로세스가 사라진 단계가 있습니다. 성공으로 간주하지 않습니다.")
    if len(rows) > cap:
        warnings.append(f"최근 {cap}개 단계만 표시합니다. JSON 내보내기로 더 많은 실행 기록을 확인하세요.")
    return {"schema_version": SCHEMA_VERSION, "generated_at": now(),
            "task": {key: task.get(key) for key in ("id", "goal", "status", "hold", "owner", "reviewer")},
            "runs": runs, "runs_truncated": more_runs, "selected_run_id": run_id, "spans": spans,
            "events": events, "edges": _edges(spans), "actors": _actors(task, spans, run_status), "legacy": legacy, "warnings": warnings,
            "coverage": {"source": "controller", "cli_internal_tools": False, "raw_prompts": False,
                         "handoffs": "controller-mediated", "usage": "provider-reported-or-unknown"},
            "pagination": {"has_more": len(activities) > event_limit, "next_before": events[0]["seq"] if events else None,
                           "span_limit": cap, "spans_truncated": len(rows) > cap}}


def artifact(store, span_id, kind):
    if kind not in {"stdout", "stderr"}:
        raise WorkflowError("허용된 실행 로그가 아닙니다.")
    row = store.db.execute("SELECT * FROM observation_spans WHERE id=?", (span_id,)).fetchone()
    if not row:
        raise WorkflowError("실행 단계가 없습니다.")
    directory = json.loads(row["data"]).get("directory")
    if not directory:
        raise WorkflowError("이 단계에는 등록된 로그가 없습니다.")
    root = (store.home / "tasks" / row["task_id"]).resolve()
    path = (store.home / directory / f"{kind}.log").resolve()
    if not path.is_relative_to(root) or not root.is_relative_to(store.home / "tasks"):
        raise WorkflowError("등록된 작업 밖의 로그는 읽을 수 없습니다.")
    try:
        with path.open("rb") as stream:
            stream.seek(0, 2)
            size = stream.tell()
            stream.seek(max(0, size - 65536))
            text = stream.read(65536).decode(errors="replace")
    except OSError as exc:
        raise WorkflowError("실행 로그를 아직 읽을 수 없습니다.") from exc
    return {"span_id": span_id, "kind": kind, "text": text, "truncated": size > 65536}
