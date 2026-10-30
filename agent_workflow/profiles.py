"""Explicitly registered project configuration; repo files never auto-authorize commands."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .core import WorkflowError, encode, now, project_root


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}", value):
        raise WorkflowError("이름은 영문·숫자·점·밑줄·하이픈 1~80자입니다.")
    return value


def integer(value, name, low, high):
    if type(value) is not int or not low <= value <= high:
        raise WorkflowError(f"{name}: {low}~{high} 정수가 필요합니다.")
    return value


def keys(value, allowed, name):
    if not isinstance(value, dict) or set(value) - set(allowed):
        raise WorkflowError(f"{name}: 객체가 아니거나 알 수 없는 항목이 있습니다.")


def strings(value, name):
    if not isinstance(value, list) or any(not isinstance(x, str) or not x.strip() or '\0' in x for x in value):
        raise WorkflowError(f"{name}: 비어 있지 않은 문자열 목록이 필요합니다.")
    return value


def commands(value, name):
    if not isinstance(value, list):
        raise WorkflowError(f"{name}: argv 목록의 목록이 필요합니다.")
    for argv in value:
        if not strings(argv, name) or any(x in {"&&", "||", ";", "|", ">", ">>", "<"} for x in argv):
            raise WorkflowError(f"{name}: shell 없는 명령 인자 목록이 필요합니다.")
    return value


def relative_paths(value, name):
    for item in strings(value, name):
        p = Path(item)
        if p.is_absolute() or ".." in p.parts or any(x in item for x in ("*", "?", "[", "\\")):
            raise WorkflowError(f"{name}: 저장소 안의 실제 상대 경로를 사용하세요: {item}")
        if ".git" in p.parts:
            raise WorkflowError("Git 관리 디렉터리는 작업 범위로 지정하지 않습니다.")
    return value


def execution_config(raw=None):
    raw = {} if raw is None else copy.deepcopy(raw)
    keys(raw, {"recoveries", "timeout", "repairs", "max_agent_calls", "max_seconds", "models", "fallback_models", "stop_after_reported_usd"}, "execution")
    if "stop_after_reported_usd" in raw:
        import math
        value = raw["stop_after_reported_usd"]
        if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
            raise WorkflowError("stop_after_reported_usd는 양수입니다. 공급자가 보고한 금액의 사후 중단 기준입니다.")
    for name, default, low, high in (("recoveries", 1, 0, 3), ("timeout", 900, 1, 86400),
                                   ("repairs", 1, 0, 3), ("max_agent_calls", 12, 1, 200),
                                   ("max_seconds", 7200, 1, 86400)):
        raw[name] = integer(raw.get(name, default), name, low, high)
    models = raw.setdefault("models", {})
    fallbacks = raw.setdefault("fallback_models", {})
    keys(models, {"implement", "review"}, "models")
    keys(fallbacks, {"implement", "review"}, "fallback_models")
    for name in models.values():
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,159}", name):
            raise WorkflowError("잘못된 모델 이름입니다.")
    for role, names in fallbacks.items():
        strings(names, "fallback_models")
        if len(names) > 3:
            raise WorkflowError("역할별 대체 모델은 최대 3개입니다.")
        for name in names:
            execution_config({"models": {role: name}})
    return raw


def validate(raw):
    cfg = copy.deepcopy(raw)
    keys(cfg, {"version", "project", "owner", "reviewer", "refs", "checks", "setup", "allowed_paths", "forbidden_paths",
               "execution", "workspace", "services", "blocked_windows", "exports", "notifications", "documents"}, "profile")
    if type(cfg.get("version", 1)) is not int or cfg.get("version", 1) != 1:
        raise WorkflowError("지원하는 프로필 형식은 version 1입니다.")
    cfg["version"] = 1
    if not isinstance(cfg.get("project"), str):
        raise WorkflowError("profile.project가 필요합니다.")
    cfg["project"] = str(project_root(cfg["project"]))
    cfg.setdefault("owner", "claude")
    cfg.setdefault("reviewer", "codex")
    if not isinstance(cfg["owner"], str):
        raise WorkflowError("profile.owner: 문자열이 필요합니다.")
    if not isinstance(cfg["reviewer"], str):
        raise WorkflowError("profile.reviewer: 문자열이 필요합니다.")
    if cfg["owner"] not in {"claude", "codex"} or cfg["reviewer"] not in {"claude", "codex", "codex-session", "none"} or cfg["owner"] == cfg["reviewer"]:
        raise WorkflowError("구현 담당과 다른 검토자를 지정하세요.")
    strings(cfg.setdefault("refs", []), "refs")
    for key in ("checks", "setup"):
        commands(cfg.setdefault(key, []), key)
    for key in ("allowed_paths", "forbidden_paths"):
        relative_paths(cfg.setdefault(key, []), key)
    from .policy import under
    for document in relative_paths(cfg.setdefault("documents", []), "documents"):
        if Path(document).suffix != ".md" or not any(under(document, x) for x in (cfg["allowed_paths"] or ["."])) or any(under(document, x) for x in cfg["forbidden_paths"]):
            raise WorkflowError("documents는 허용 범위 안의 Markdown 파일이어야 합니다.")
    cfg["execution"] = execution_config(cfg.get("execution"))
    workspace = cfg.setdefault("workspace", {})
    keys(workspace, {"isolate", "root", "branch_prefix"}, "workspace")
    if type(workspace.setdefault("isolate", True)) is not bool:
        raise WorkflowError("workspace.isolate는 boolean입니다.")
    prefix = workspace.setdefault("branch_prefix", "workflow/")
    if not isinstance(prefix, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9/_-]{0,59}/", prefix):
        raise WorkflowError("branch_prefix는 workflow/ 형태입니다.")
    if "root" in workspace:
        if not isinstance(workspace["root"], str) or not Path(workspace["root"]).expanduser().is_absolute():
            raise WorkflowError("workspace.root는 절대 경로입니다.")
        workspace["root"] = str(Path(workspace["root"]).expanduser().resolve())
    services = cfg.setdefault("services", [])
    if not isinstance(services, list):
        raise WorkflowError("services는 목록입니다.")
    seen = set()
    ports = set()
    for service in services:
        keys(service, {"name", "argv", "port", "ready_url", "startup_seconds"}, "service")
        name = identifier(service.get("name"))
        environment_key = name.upper().replace("-", "_").replace(".", "_")
        if environment_key in seen:
            raise WorkflowError("환경 변수 이름이 겹치는 서비스입니다.")
        seen.add(environment_key)
        commands([service.get("argv")], "service.argv")
        integer(service.get("port"), "service.port", 1024, 65535)
        if service["port"] in ports:
            raise WorkflowError("프로필 안의 서비스 포트가 중복됐습니다.")
        ports.add(service["port"])
        integer(service.setdefault("startup_seconds", 20), "startup_seconds", 1, 120)
        from urllib.parse import urlsplit
        if not isinstance(service.get("ready_url"), str):
            raise WorkflowError("service.ready_url: 문자열이 필요합니다.")
        try:
            address = urlsplit(service["ready_url"])
            valid = address.scheme == "http" and address.hostname == "127.0.0.1" and address.port == service["port"] and not address.username and not address.password
        except ValueError as exc:
            raise WorkflowError("service.ready_url: 올바른 URL이 필요합니다.") from exc
        if not valid:
            raise WorkflowError("ready_url은 지정 포트의 http://127.0.0.1 주소여야 합니다.")
    windows = cfg.setdefault("blocked_windows", [])
    if not isinstance(windows, list):
        raise WorkflowError("blocked_windows는 목록입니다.")
    for window in windows:
        keys(window, {"timezone", "days", "start", "end"}, "blocked_window")
        try:
            ZoneInfo(window.get("timezone", "UTC"))
        except (ZoneInfoNotFoundError, TypeError) as exc:
            raise WorkflowError("유효한 시간대를 지정하세요.") from exc
        window.setdefault("timezone", "UTC")
        days = window.setdefault("days", list(range(7)))
        if not isinstance(days, list) or not days or any(type(x) is not int or not 0 <= x <= 6 for x in days):
            raise WorkflowError("days는 월요일=0~일요일=6 목록입니다.")
        for key in ("start", "end"):
            if not isinstance(window.get(key), str) or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", window[key]):
                raise WorkflowError("시간은 HH:MM 형식입니다.")
        if window["start"] == window["end"]:
            raise WorkflowError("시간대의 시작과 끝이 같습니다.")
    exports = cfg.setdefault("exports", {})
    keys(exports, {"directory", "timezone"}, "exports")
    if "timezone" in exports:
        try:
            ZoneInfo(exports["timezone"])
        except (ZoneInfoNotFoundError, TypeError) as exc:
            raise WorkflowError("exports.timezone이 잘못됐습니다.") from exc
    if "directory" in exports:
        if not isinstance(exports["directory"], str) or not Path(exports["directory"]).expanduser().is_absolute():
            raise WorkflowError("exports.directory는 절대 경로입니다.")
        exports["directory"] = str(Path(exports["directory"]).expanduser().resolve())
    notifications = cfg.setdefault("notifications", {})
    keys(notifications, {"command"}, "notifications")
    if "command" in notifications:
        commands([notifications["command"]], "notifications.command")
    return cfg


def init(store):
    store.db.execute("CREATE TABLE IF NOT EXISTS profiles(name TEXT PRIMARY KEY, data TEXT NOT NULL)")
    store.db.commit()


def save(store, name, config):
    identifier(name)
    cfg = validate(config)
    init(store)
    record = {"name": name, "config": cfg, "updated_at": now(),
              "digest": hashlib.sha256(encode(cfg).encode()).hexdigest()}
    with store.db:
        store.db.execute("INSERT INTO profiles VALUES (?,?) ON CONFLICT(name) DO UPDATE SET data=excluded.data", (name, encode(record)))
    return record


def get(store, name):
    identifier(name)
    init(store)
    row = store.db.execute("SELECT data FROM profiles WHERE name=?", (name,)).fetchone()
    if row is None:
        raise WorkflowError("프로필 없음: " + name)
    return json.loads(row[0])


def list_profiles(store):
    init(store)
    return [json.loads(row[0]) for row in store.db.execute("SELECT data FROM profiles ORDER BY name")]
