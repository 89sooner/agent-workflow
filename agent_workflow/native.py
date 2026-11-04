"""Local native-session observation, isolated from workflow tasks/jobs."""
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
MAX_EVENTS = 10000
MAX_SESSIONS = 500


def sources(home):
    path = state_home(home) / "native-sources.json"
    if not path.exists():
        return []
    data = json.loads(path.read_text())
    if not isinstance(data, list):
        raise WorkflowError("네이티브 연결 설정이 손상되었습니다.")
    return data


def command(home, project):
    return shlex.join([sys.executable, str(Path(__file__).with_name("native_hook.py")),
                       "--home", str(state_home(home)), "--project", str(Path(project).resolve())])


def setup(home, project):
    p = Path(project).expanduser()
    if not p.is_absolute() or not p.is_dir():
        raise WorkflowError("프로젝트 절대 경로를 확인하세요.")
    return {"project": str(p.resolve()), "command": shlex.join([
        sys.executable, "-m", "agent_workflow", "--home", str(state_home(home)),
        "native", "install", "--project", str(p.resolve())]),
        "cwd": str(Path(__file__).resolve().parent.parent)}


def install(home, project, remove=False):
    home = state_home(home)
    project = setup(home, project)["project"]
    root = Path(project) / ".claude"
    path = root / "settings.local.json"
    if root.is_symlink() or path.is_symlink():
        raise WorkflowError("심볼릭 링크 설정 파일은 자동 변경하지 않습니다.")
    root.mkdir(exist_ok=True)
    cmd = command(home, project)
    with _registry_lock(home), _registry_lock(root):
        previous_commands = {s.get("command") for s in sources(home) if s["project"] == project}
        previous_commands.discard(None)
        previous_commands.add(cmd)
        original = path.read_text() if path.exists() else None
        settings = json.loads(original) if original else {}
        hooks = settings.setdefault("hooks", {})
        if not isinstance(hooks, dict):
            raise WorkflowError("기존 hooks 설정 형식을 확인하세요.")
        for event in EVENT_STATES:
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
            backup = root / ("settings.local.json.observer-backup-" + str(time.time_ns()))
            backup.write_text(original)
            backup.chmod(0o600)
        atomic_write(path, json.dumps(settings, ensure_ascii=False, indent=2))
        registered = [s for s in sources(home) if s["project"] != project]
        if not remove:
            registered.append({"project": project, "provider": "claude", "installed_at": time.time(), "command": cmd})
        atomic_write(state_home(home) / "native-sources.json", json.dumps(registered))
    return {"project": project, "connected": not remove, "settings": str(path),
            "execution_started": False, "note": "기존 Claude 세션은 설정 재로딩/재시작 후 관측됩니다. 모델 호출은 하지 않았습니다."}


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
    CREATE TABLE IF NOT EXISTS history (
      seq INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, at REAL,
      event TEXT, state TEXT, tool TEXT);
    """)
    return c


def clean(value, limit=160):
    return "".join(ch for ch in str(value or "") if ord(ch) >= 32)[:limit]


def record(home, provider, source, native_id, project, name, state, event,
           tool="", parent="", model=""):
    identifier = hashlib.sha256((provider + "\0" + source + "\0" + native_id).encode()).hexdigest()[:32]
    timestamp = time.time()
    c = database(home)
    try:
        with c:
            old = c.execute("SELECT * FROM sessions WHERE id=?", (identifier,)).fetchone()
            if not old or old["state"] != state or old["tool"] != tool or provider == "claude":
                c.execute("INSERT INTO history(session_id,at,event,state,tool) VALUES(?,?,?,?,?)",
                          (identifier, timestamp, clean(event), state, clean(tool)))
            c.execute("""INSERT OR REPLACE INTO sessions VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                      (identifier, provider, source, clean(native_id), project, clean(name), state,
                       timestamp, clean(tool), clean(parent), clean(model) or (old["model"] if old else "")))
            c.execute("DELETE FROM history WHERE seq NOT IN (SELECT seq FROM history ORDER BY seq DESC LIMIT ?)", (MAX_EVENTS,))
            c.execute("DELETE FROM sessions WHERE id NOT IN (SELECT id FROM sessions ORDER BY last_seen DESC LIMIT ?)", (MAX_SESSIONS,))
            c.execute("DELETE FROM history WHERE session_id NOT IN (SELECT id FROM sessions)")
    finally:
        c.close()
    return identifier


def ingest(home, project, payload):
    project = str(Path(project).resolve())
    if project not in {s["project"] for s in sources(home)}:
        return False
    if not isinstance(payload, dict) or str(Path(payload.get("cwd", "")).resolve()) != project:
        return False
    sid, event = payload.get("session_id"), payload.get("hook_event_name")
    if not isinstance(sid, str) or not 0 < len(sid) <= 160 or event not in EVENT_STATES:
        return False
    state = EVENT_STATES[event]
    tool = clean(payload.get("tool_name"))
    if event == "PreToolUse" and tool == "AskUserQuestion":
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
    record(home, "claude", project, sid, project,
           "Claude · " + (clean(payload.get("agent_type")) or sid[:12]),
           state, event, tool, parent, clean(payload.get("model")))
    return True


def snapshot(home, observer, session_id=None, refresh=True):
    from . import opencode
    errors = []
    available = set()
    if refresh:
        for source in opencode.sources(home):
            try:
                data = observer.view(source["id"])
                waiting = {}
                for endpoint, waiting_state in (("/question", "input"), ("/permission", "approval")):
                    try:
                        requests = opencode.Client(source).get(endpoint)
                        if not isinstance(requests, list) or any(not isinstance(r, dict) or not isinstance(r.get("sessionID"), str) for r in requests):
                            raise WorkflowError("승인·질문 목록 형식을 확인할 수 없습니다.")
                        waiting.update({r["sessionID"]: waiting_state for r in requests})
                    except (WorkflowError, ValueError, OSError) as exc:
                        errors.append({"source": source["id"], "error": endpoint + " 대기 요청 관측 미확인: " + str(exc)})

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
        return {"sessions": [], "history": [], "errors": errors, "sources": sources(home)}
    c = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=1)
    c.row_factory = sqlite3.Row
    try:
        rows = [dict(r) for r in c.execute("SELECT * FROM sessions ORDER BY last_seen DESC")]
        registered = {s["project"] for s in sources(home)}
        now = time.time()
        for row in rows:
            fresh = (row["source"] + "\0" + row["native_id"]) in available if row["provider"] == "opencode" else row["project"] in registered and now - row["last_seen"] < 300
            row["observed_state"] = row["state"]
            row["fresh"] = fresh
            if not fresh and row["state"] != "ended":
                row["state"] = "unknown"
        history = [dict(r) for r in c.execute(
            "SELECT * FROM history WHERE session_id=? ORDER BY seq DESC LIMIT 200", (session_id,))] if session_id else []
        return {"sessions": rows, "history": history, "errors": errors, "sources": sources(home),
                "limits": {"sessions": MAX_SESSIONS, "events": MAX_EVENTS, "history": 200}}
    finally:
        c.close()
