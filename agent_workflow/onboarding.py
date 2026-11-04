"""Explicit observation configuration, separate from task execution permissions."""
from pathlib import Path

from . import connections, opencode
from .core import WorkflowError


def capabilities(home, token, *, read_only=False, observer=False):
    return {
        "can_configure_connections": observer or not read_only,
        "can_manage_tasks": not read_only and not observer,
        "primary_ledger_id": connections.ledger_id(home) if (Path(home) / "state.sqlite3").is_file() else None,
        "csrf_token": token,
        "mode": "observer" if observer else "read_only" if read_only else "manage",
        "tools": [
            {"id": "opencode", "label": "OpenCode", "support": "native_sessions",
             "description": "로컬 OpenCode 1.x 서버를 한 번 연결하면 새 세션을 자동으로 관측합니다."},
            {"id": "codex", "label": "Codex", "support": "workflow_only",
             "description": "agent-workflow에 기록된 작업만 관측합니다. 일반 Codex 대화의 자동 발견은 지원하지 않습니다."},
            {"id": "claude", "label": "Claude", "support": "workflow_only",
             "description": "agent-workflow에 기록된 작업만 관측합니다. 일상 세션 화면에서 프로젝트 관측 훅을 설치하면 WP 없이 Claude Code 세션도 관측할 수 있습니다."},
        ],
    }


def _text(data, name, default=None, limit=4096):
    value = data.get(name, default)
    if not isinstance(value, str) or not value.strip() or len(value) > limit or any(ord(c) < 32 for c in value):
        raise WorkflowError(f"{name}: 비어 있지 않은 올바른 문자열을 입력하세요.")
    return value.strip()


def _project(data):
    path = Path(_text(data, "project")).expanduser()
    if not path.is_absolute() or not path.is_dir():
        raise WorkflowError("프로젝트 폴더의 실제 절대 경로를 입력하세요. 관제실 서버가 실행되는 컴퓨터의 경로여야 합니다.")
    return path.resolve()


def discover(home, data):
    project = _project(data)
    registry = connections.load_registry(home)
    homes = [item["home"] for p in registry["projects"] for item in p["ledgers"]]
    if (Path(home) / "state.sqlite3").is_file():
        homes.append(home)
    result = connections.discover(project, homes)
    result["ledgers"] = [item for item in result["ledgers"] if item["status"] == "available"
                         and (item["matching_task_count"] or item["profile_names"])]
    result["project"] = str(project)
    result["execution_started"] = False
    return result


def configure(home, action, data):
    if action == "discover":
        return discover(home, data)
    kind = _text(data, "kind", limit=32)
    if action == "disconnect":
        if kind != "opencode":
            raise WorkflowError("이 화면에서는 OpenCode 연결만 해제할 수 있습니다.")
        return opencode.disconnect(home, _text(data, "source_id", limit=120))
    project = _project(data)
    if kind == "opencode":
        env = data.get("password_env")
        if env is not None:
            env = _text(data, "password_env", limit=120)
        result = opencode.connect(home, _text(data, "url"), str(project),
                                  _text(data, "name", "OpenCode", 120), env,
                                  _text(data, "username", "opencode", 120))
        return {"source_id": result["source"]["id"], "execution_started": False}
    if kind != "workflow":
        raise WorkflowError("지원하지 않는 연결 도구입니다.")
    identifiers = data.get("ledger_ids")
    if (not isinstance(identifiers, list) or not 1 <= len(identifiers) <= 32
            or any(not isinstance(item, str) or len(item) > 120 for item in identifiers)):
        raise WorkflowError("발견된 작업 기록을 하나 이상 선택하세요.")
    candidates = {item["id"]: item for item in discover(home, data)["ledgers"]}
    if any(identifier not in candidates for identifier in identifiers):
        raise WorkflowError("이 프로젝트의 연결 후보가 바뀌었습니다. 기록을 다시 찾아 선택하세요.")
    selected = [candidates[identifier]["home"] for identifier in dict.fromkeys(identifiers)]
    connections.connect(home, project, selected, include_processes=False,
                        include_temporary=False, include_default=False, include_home=False)
    return {"source_id": identifiers[0], "execution_started": False}
