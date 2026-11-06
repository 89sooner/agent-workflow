"""Local native-session observation, isolated from workflow tasks/jobs."""
from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path
import shlex
import sqlite3
import sys
import time

from .core import WorkflowError, atomic_write
from .connections import _registry_lock, state_home

EVENT_STATES = {
    "SessionStart": "idle", "UserPromptSubmit": "running", "PreToolUse": "running",
    "PostToolUse": "running", "PostToolUseFailure": "attention",
    "PermissionRequest": "approval", "Stop": "input", "StopFailure": "attention",
    "SessionEnd": "ended", "SubagentStart": "running", "SubagentStop": "ended",
    "Notification": "unknown",
}
CODEX_EVENTS = {k: EVENT_STATES[k] for k in (
    "SessionStart", "SessionEnd", "UserPromptSubmit", "PreToolUse", "PostToolUse",
    "PermissionRequest", "SubagentStart", "SubagentStop", "Stop")}
CODEX_EVENTS.update(Interrupt="input", PreModelRequest="running")
PROVIDERS = {"claude", "codex", "opencode"}
MAX_EVENTS = 10000
MAX_SESSIONS = 500


@lru_cache(maxsize=1024)
def _project_identity(path, bucket):
    from .connections import project_key
    return project_key(path)


def sources(home):
    path = state_home(home) / "native-sources.json"
    if not path.exists():
        return []
    data = json.loads(path.read_text())
    if not isinstance(data, list):
        raise WorkflowError("네이티브 연결 설정이 손상되었습니다.")
    return data


def command(home, project, provider="claude"):
    return shlex.join([sys.executable, str(Path(__file__).with_name("native_hook.py")),
                       "--home", str(state_home(home)), "--project", str(Path(project).resolve()), "--provider", provider])


def setup(home, project, provider="claude", require_exists=True):
    if provider not in PROVIDERS:
        raise WorkflowError("지원하지 않는 관측 도구입니다.")
    p = Path(project).expanduser()
    if not p.is_absolute() or (require_exists and not p.is_dir()):
        raise WorkflowError("프로젝트 절대 경로를 확인하세요.")
    return {"project": str(p.resolve()), "command": shlex.join([
        sys.executable, "-m", "agent_workflow", "--home", str(state_home(home)),
        "native", "install", "--provider", provider, "--project", str(p.resolve())]),
        "cwd": str(Path(__file__).resolve().parent.parent)}


def install(home, project, remove=False, provider="claude"):
    if provider not in {"claude", "codex"}:
        raise WorkflowError("OpenCode는 플러그인 설치 후 native register를 사용하세요.")
    home = state_home(home)
    project = setup(home, project, provider)["project"]
    root = Path(project) / (".claude" if provider == "claude" else ".codex")
    path = root / ("settings.local.json" if provider == "claude" else "hooks.json")
    if root.is_symlink() or path.is_symlink():
        raise WorkflowError("심볼릭 링크 설정 파일은 자동 변경하지 않습니다.")
    root.mkdir(exist_ok=True)
    cmd = command(home, project, provider)
    with _registry_lock(home), _registry_lock(root):
        if any(s["project"] == project and s.get("provider", "claude") == provider
               and s.get("mode") == "plugin" for s in sources(home)):
            raise WorkflowError("플러그인 등록은 native unregister로 해제한 뒤 직접 훅을 설치하세요.")
        previous_commands = {s.get("command") for s in sources(home) if s["project"] == project and s.get("provider", "claude") == provider}
        previous_commands.discard(None)
        previous_commands.add(cmd)
        original = path.read_text() if path.exists() else None
        settings = json.loads(original) if original else {}
        hooks = settings.setdefault("hooks", {})
        if not isinstance(hooks, dict):
            raise WorkflowError("기존 hooks 설정 형식을 확인하세요.")
        for event in (CODEX_EVENTS if provider == "codex" else EVENT_STATES):
            entries = hooks.get(event, [])
            if not isinstance(entries, list):
                raise WorkflowError("기존 hook 항목 형식을 확인하세요.")
            # Preserve all hooks except this exact observer command.
            kept = []
            for entry in entries:
                entry = dict(entry)
                entry["hooks"] = [h for h in entry.get("hooks", []) if h.get("command") not in previous_commands]
                if entry["hooks"]:
                    kept.append(entry)
            if not remove:
                kept.append({"matcher": "", "hooks": [{"type": "command", "command": cmd, "timeout": 3}]})
            hooks[event] = kept
        if original is not None:
            backup = root / (path.name + ".observer-backup-" + str(time.time_ns()))
            backup.write_text(original)
            backup.chmod(0o600)
        atomic_write(path, json.dumps(settings, ensure_ascii=False, indent=2))
        registered = [s for s in sources(home) if (s["project"], s.get("provider", "claude")) != (project, provider)]
        if not remove:
            registered.append({"project": project, "provider": provider, "mode": "direct", "installed_at": time.time(), "command": cmd})
        atomic_write(state_home(home) / "native-sources.json", json.dumps(registered))
    return {"project": project, "connected": not remove, "settings": str(path),
            "execution_started": False, "note": "새 세션/설정 재로딩 후 관측됩니다. Codex는 프로젝트 신뢰 및 /hooks 검토가 필요합니다. 모델 호출 없음."}


def database(home):
    home = state_home(home)
    home.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = home / "native-observation.sqlite3"
    c = sqlite3.connect(path, timeout=1)
    path.chmod(0o600)
    c.row_factory = sqlite3.Row
    c.executescript("""
    CREATE TABLE IF NOT EXISTS sessions (
      id TEXT PRIMARY KEY, provider TEXT, source TEXT, native_id TEXT, project TEXT,
      name TEXT, state TEXT, last_seen REAL, tool TEXT, parent TEXT, model TEXT);
    CREATE TABLE IF NOT EXISTS session_transitions (
      session_id TEXT PRIMARY KEY, state TEXT, since REAL);
    CREATE TABLE IF NOT EXISTS history (
      seq INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, at REAL,
      event TEXT, state TEXT, tool TEXT);
    """)
    return c


def clean(value, limit=160):
    return "".join(ch for ch in str(value or "") if ord(ch) >= 32)[:limit]


def record(home, provider, source, native_id, project, name, state, event,
           tool="", parent="", model="", only_if_new=False):
    identifier = hashlib.sha256((provider + "\0" + source + "\0" + native_id).encode()).hexdigest()[:32]
    timestamp = time.time()
    c = database(home)
    try:
        with c:
            c.execute("BEGIN IMMEDIATE")
            old = c.execute("SELECT * FROM sessions WHERE id=?", (identifier,)).fetchone()
            if old and only_if_new:
                return None
            if not old or old["state"] != state:
                c.execute("INSERT OR REPLACE INTO session_transitions VALUES(?,?,?)",
                          (identifier, state, timestamp))
            if not old or old["state"] != state or old["tool"] != tool or provider in {"claude", "codex"} or event in {"tool.execute.before", "tool.execute.after"}:
                c.execute("INSERT INTO history(session_id,at,event,state,tool) VALUES(?,?,?,?,?)",
                          (identifier, timestamp, clean(event), state, clean(tool)))
            c.execute("""INSERT OR REPLACE INTO sessions VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                      (identifier, provider, source, clean(native_id), project, clean(name) or (old["name"] if old else provider.title() + " · " + clean(native_id)[:12]), state,
                       timestamp, clean(tool), clean(parent) or (old["parent"] if old else ""),
                       clean(model) or (old["model"] if old else "")))
            c.execute("DELETE FROM history WHERE seq NOT IN (SELECT seq FROM history ORDER BY seq DESC LIMIT ?)", (MAX_EVENTS,))
            c.execute("DELETE FROM sessions WHERE id NOT IN (SELECT id FROM sessions ORDER BY last_seen DESC LIMIT ?)", (MAX_SESSIONS,))
            c.execute("DELETE FROM history WHERE session_id NOT IN (SELECT id FROM sessions)")
            c.execute("DELETE FROM session_transitions WHERE session_id NOT IN (SELECT id FROM sessions)")
    finally:
        c.close()
    return identifier


def ingest(home, project, payload, provider="claude", mode="direct"):
    if provider not in {"claude", "codex"}:
        return False
    project = str(Path(project).resolve())
    if not any(s["project"] == project and s.get("provider", "claude") == provider
               and s.get("mode", "direct") == mode for s in sources(home)):
        return False
    if (not isinstance(payload, dict) or not isinstance(payload.get("cwd"), str)
            or not Path(payload["cwd"]).is_absolute() or str(Path(payload["cwd"]).resolve()) != project):
        return False
    sid, event = payload.get("session_id"), payload.get("hook_event_name")
    if not isinstance(sid, str) or not 0 < len(sid) <= 160 or event not in (CODEX_EVENTS if provider == "codex" else EVENT_STATES):
        return False
    state = (CODEX_EVENTS if provider == "codex" else EVENT_STATES)[event]
    tool = clean(payload.get("tool_name"))
    if event == "PreToolUse" and tool in {"AskUserQuestion", "request_user_input"}:
        state = "input"
    if event == "Notification":
        state = {"permission_prompt": "approval", "idle_prompt": "input",
                 "agent_needs_input": "input", "elicitation_dialog": "input",
                 "elicitation_url_dialog": "input"}.get(payload.get("notification_type"))
        if state is None:
            return False
    parent = ""
    if event in {"SubagentStart", "SubagentStop"} or payload.get("agent_id"):
        agent = payload.get("agent_id")
        if not isinstance(agent, str) or not 0 < len(agent) <= 120:
            return False
        parent = sid
        sid += ":" + agent
    record(home, provider, project, sid, project,
           "" if payload.get("agent_id") and not clean(payload.get("agent_type")) else ("Codex · " if provider == "codex" else "Claude · ") + (clean(payload.get("agent_type")) or sid[:12]),
           state, event, tool, parent, clean(payload.get("model")))
    return True


def snapshot(home, observer, session_id=None, refresh=True):
    from . import opencode
    observed_at = time.time()
    errors = []
    available = set()
    if refresh:
        for source in opencode.sources(home):
            try:
                data = observer.view(source["id"])
                waiting = {}
                waiting_complete = True
                for endpoint, waiting_state in (("/question", "input"), ("/permission", "approval")):
                    try:
                        requests = opencode.Client(source).get(endpoint)
                        if not isinstance(requests, list) or any(not isinstance(r, dict) or not isinstance(r.get("sessionID"), str) for r in requests):
                            raise WorkflowError("승인·질문 목록 형식을 확인할 수 없습니다.")
                        waiting.update({r["sessionID"]: waiting_state for r in requests})
                    except (WorkflowError, ValueError, OSError) as exc:
                        waiting_complete = False
                        errors.append({"source": source["id"], "error": endpoint + " 대기 요청 관측 미확인: " + str(exc)})

                if not waiting_complete:
                    continue  # Preserve earlier waits; partial data cannot prove resolution.
                for task in data["tasks"]:
                    available.add(source["id"] + "\0" + task["id"])
                    state = {"running": "running", "idle": "idle", "retrying": "retrying"}.get(task["status"], "unknown")
                    state = waiting.get(task["id"], state)
                    record(home, "opencode", source["id"], task["id"], task["project"],
                           task["goal"], state, "snapshot", parent=task.get("native_parent_id") or "")
                if data.get("truncated"):
                    if any(data["truncated"].values()):
                        errors.append({"source": source["id"], "error": "최근 세션 조회 한도에 도달했습니다."})
            except (WorkflowError, OSError, ValueError) as exc:
                errors.append({"source": source["id"], "error": str(exc)})
    path = state_home(home) / "native-observation.sqlite3"
    if not path.exists():
        return {"sessions": [], "history": [], "errors": errors, "sources": sources(home), "observed_at": observed_at}
    c = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=1)
    c.row_factory = sqlite3.Row
    try:
        c.execute("BEGIN")
        rows = [dict(r) for r in c.execute("SELECT * FROM sessions ORDER BY last_seen DESC")]
        transitions = {r["session_id"]: dict(r) for r in c.execute("SELECT * FROM session_transitions")} if c.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='session_transitions'").fetchone() else {}
        from .activity import activity
        latest_events = {r[0]: r[1] for r in c.execute(
            "SELECT h.session_id,h.event FROM history h JOIN (SELECT session_id,MAX(seq) seq FROM history GROUP BY session_id) last ON last.seq=h.seq")}
        registered = {(s.get("provider", "claude"), s["project"]) for s in sources(home)}
        now = time.time()
        identity_bucket = int(time.monotonic() // 30)
        for row in rows:
            row["project_id"] = _project_identity(row["project"], identity_bucket)
            fresh = (row["source"] + "\0" + row["native_id"]) in available if row["provider"] == "opencode" and not row["source"].startswith("plugin:") else (row["provider"], row["project"]) in registered and 0 <= now - row["last_seen"] < 300
            row["observed_state"] = row["state"]
            row["fresh"] = fresh
            row["activity"] = activity(row["state"], fresh, latest_events.get(row["id"]), row["tool"])
            transition = transitions.get(row["id"], {})
            row["state_since"] = transition.get("since") if transition.get("state") == row["state"] else None
            row["waiting_since"] = row["state_since"] if fresh and row["state"] in {"input", "approval", "attention"} else None
            row["freshness_reason"] = "observed" if fresh else "source_unavailable" if row["provider"] == "opencode" and not row["source"].startswith("plugin:") else "not_registered" if (row["provider"], row["project"]) not in registered else "event_expired"
            if not fresh and row["state"] != "ended":
                row["state"] = "unknown"
        history = [dict(r) for r in c.execute(
            "SELECT * FROM history WHERE session_id=? ORDER BY seq DESC LIMIT 200", (session_id,))] if session_id else []
        return {"sessions": rows, "history": history, "errors": errors, "sources": sources(home),
                "limits": {"sessions": MAX_SESSIONS, "events": MAX_EVENTS, "history": 200}, "observed_at": observed_at}
    finally:
        c.close()


def register(home, project, provider, remove=False):
    """Opt a project into a plugin without editing any agent settings."""
    project = setup(home, project, provider, require_exists=not remove)["project"]
    with _registry_lock(state_home(home)):
        rows = sources(home)
        current = [s for s in rows if (s["project"], s.get("provider", "claude")) == (project, provider)]
        if any(s.get("mode", "direct") != "plugin" for s in current):
            raise WorkflowError("직접 설치한 훅은 native uninstall로 해제한 뒤 플러그인을 등록하세요.")
        rows = [s for s in rows if s not in current]
        if not remove:
            rows.append({"project": project, "provider": provider, "mode": "plugin", "installed_at": time.time()})
        atomic_write(state_home(home) / "native-sources.json", json.dumps(rows))
    return {"project": project, "provider": provider, "registered": not remove,
            "execution_started": False, "note": "플러그인 로딩·훅 신뢰는 원래 도구에서 확인하세요."}


def status(home, project, provider):
    project = setup(home, project, provider, require_exists=False)["project"]
    registration = next((s for s in sources(home) if
                         (s["project"], s.get("provider", "claude")) == (project, provider)), None)
    rows = snapshot(home, None, refresh=False)["sessions"]
    rows = [s for s in rows if s["project"] == project and s["provider"] == provider]
    return {"provider": provider, "project": project, "home": str(state_home(home)),
            "registration": registration, "observed_sessions": len(rows),
            "last_seen": max((s["last_seen"] for s in rows), default=None),
            "note": "등록은 플러그인 로딩/훅 신뢰 완료를 보장하지 않습니다. 새 세션 후 last_seen을 확인하세요."}


def ingest_opencode(home, project, payload):
    """Only the plugin's allowlisted metadata protocol, never message bodies."""
    if not any(s["project"] == project and s.get("provider") == "opencode"
               and s.get("mode") == "plugin" for s in sources(home)):
        return False
    sid, event = payload.get("session_id"), payload.get("hook_event_name")
    states = {"session.created": "idle", "session.deleted": "ended", "session.idle": "idle",
              "session.error": "attention", "permission.asked": "approval", "question.asked": "input",
              "permission.replied": "unknown", "question.replied": "unknown", "question.rejected": "unknown",
              "tool.execute.before": "running", "tool.execute.after": "running",
              "session.discovered": "unknown", "session.moved": "unknown",
              "session.execution.started": "running", "session.execution.succeeded": "idle",
              "session.execution.failed": "attention", "session.execution.interrupted": "input",
              "form.created": "input", "form.replied": "unknown", "form.cancelled": "unknown"}
    state = states.get(event)
    if event == "session.status":
        state = {"busy": "running", "idle": "idle", "retry": "retrying"}.get(payload.get("status"))
    if not isinstance(sid, str) or not 0 < len(sid) <= 160 or state is None:
        return False
    return record(home, "opencode", "plugin:" + project, sid, project, "OpenCode · " + sid[:12],
                  state, event, clean(payload.get("tool_name")), clean(payload.get("parent")),
                  only_if_new=event == "session.discovered") is not None
