"""Read-only OpenCode HTTP observation. Never create tasks or call model APIs."""
from __future__ import annotations

import base64
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from .connections import _registry_lock, state_home
from .core import WorkflowError, atomic_write, now

MAX_SESSIONS = 200
MAX_MESSAGES = 100
MAX_BYTES = 4 * 1024 * 1024


def endpoint(value):
    try:
        url = urlsplit(value)
        if (url.scheme not in {"http", "https"} or url.hostname not in {"localhost", "127.0.0.1", "::1"}
                or url.username is not None or url.password is not None or url.path not in {"", "/"}
                or url.query or url.fragment or not url.port):
            raise ValueError()
        host = "[::1]" if url.hostname == "::1" else "127.0.0.1"
        return f"{url.scheme}://{host}:{url.port}"
    except (TypeError, ValueError):
        raise WorkflowError("OpenCode URL은 포트를 포함한 로컬 HTTP(S) 주소여야 합니다. 예: http://127.0.0.1:4096") from None


def sources(home):
    path = state_home(home) / "opencode.json"
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text())
        if data.get("version") != 1 or not isinstance(data.get("sources"), list):
            raise ValueError()
        for item in data["sources"]:
            if (not isinstance(item, dict) or item.get("url") != endpoint(item.get("url"))
                    or not isinstance(item.get("name"), str)
                    or not isinstance(item.get("directory"), str)
                    or item.get("id") != source_id(item["url"], item["directory"])):
                raise ValueError()
            if item.get("password_env") and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", item["password_env"]):
                raise ValueError()
        return data["sources"]
    except (ValueError, TypeError, AttributeError, KeyError):
        raise WorkflowError("OpenCode 연결 설정이 손상되었습니다. opencode connect로 연결을 다시 설정하세요.") from None


def source_id(url, directory):
    return "opencode-" + hashlib.sha256((url + "\0" + directory).encode()).hexdigest()[:20]


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Client:
    def __init__(self, source):
        self.source = source
        self.url = endpoint(source["url"])

    def get(self, path, **params):
        # All callers use fixed read endpoints; neither redirects nor environment proxies are followed.
        params = {"directory": self.source["directory"], **params}
        headers = {"Accept": "application/json"}
        env = self.source.get("password_env")
        if env:
            password = os.environ.get(env)
            if not password:
                raise WorkflowError(f"OpenCode 인증 환경변수가 없습니다: {env}")
            credential = (self.source.get("username", "opencode") + ":" + password).encode()
            headers["Authorization"] = "Basic " + base64.b64encode(credential).decode()
        request = Request(self.url + path + "?" + urlencode(params), headers=headers, method="GET")
        try:
            with build_opener(ProxyHandler({}), NoRedirect()).open(request, timeout=3) as response:
                raw = response.read(MAX_BYTES + 1)
            if len(raw) > MAX_BYTES:
                raise WorkflowError("OpenCode 응답이 조회 한도(4 MiB)를 넘었습니다.")
            return json.loads(raw)
        except HTTPError as exc:
            hint = "인증 환경변수를 확인하세요." if exc.code in {401, 403} else "서버 버전과 연결 주소를 확인하세요."
            raise WorkflowError(f"OpenCode HTTP {exc.code}. {hint}") from None
        except (URLError, OSError, ValueError):
            raise WorkflowError("OpenCode 응답을 읽지 못했습니다. 서버 연결과 JSON API 지원을 확인하세요.") from None


def connect(home, url, directory, name="OpenCode", password_env=None, username="opencode"):
    url = endpoint(url)
    directory = str(Path(directory).expanduser().resolve())
    if not name.strip() or len(name) > 120 or any(ord(c) < 32 for c in name + username):
        raise WorkflowError("OpenCode 연결 이름은 제어문자 없는 1~120자 문자열이어야 합니다.")
    if password_env and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", password_env):
        raise WorkflowError("password-env에는 비밀번호 대신 환경변수 이름을 지정하세요.")
    record = {"id": source_id(url, directory), "url": url, "directory": directory,
              "name": name, "password_env": password_env, "username": username}
    client = Client(record)
    health = client.get("/global/health")
    if not isinstance(health, dict) or health.get("healthy") is not True or not isinstance(health.get("version"), str):
        raise WorkflowError("OpenCode health/version 응답을 확인할 수 없습니다.")
    record["version"] = health["version"]
    if not record["version"].startswith("1."):
        raise WorkflowError("이 연결기는 OpenCode 1.x 세션 API용입니다. 서버 버전 호환성을 확인하세요.")
    _sessions(client)  # Validate the selected instance before persisting a connection.
    with _registry_lock(state_home(home)):
        records = [s for s in sources(home) if s["id"] != record["id"]] + [record]
        if len(records) > 8:
            raise WorkflowError("OpenCode 연결은 최대 8개까지 지원합니다.")
        atomic_write(state_home(home) / "opencode.json", json.dumps({"version": 1, "sources": records}, ensure_ascii=False, indent=2))
    return {"source": record, "execution_started": False, "next": "같은 home으로 watch를 실행하고 /mission에서 OpenCode 연결을 선택하세요."}


def disconnect(home, identifier):
    with _registry_lock(state_home(home)):
        records = sources(home)
        if not any(s["id"] == identifier for s in records):
            raise WorkflowError("등록되지 않은 OpenCode 연결입니다.")
        atomic_write(state_home(home) / "opencode.json", json.dumps({"version": 1, "sources": [s for s in records if s["id"] != identifier]}, ensure_ascii=False, indent=2))
    return {"disconnected": identifier, "execution_started": False}


def _sessions(client):
    rows = client.get("/session", limit=MAX_SESSIONS + 1)
    statuses = client.get("/session/status")
    if (not isinstance(rows, list) or not isinstance(statuses, dict)
            or any(not isinstance(s, dict) or not isinstance(s.get("id"), str)
                   or not isinstance(s.get("directory"), str) or not isinstance(s.get("time"), dict) for s in rows)
            or any(not isinstance(v, dict) or not isinstance(v.get("type"), str) for v in statuses.values())):
        raise WorkflowError("지원되지 않는 OpenCode 세션 응답입니다.")
    # A single connection observes its explicit working directory, not every project in that server.
    truncated = len(rows) > MAX_SESSIONS
    rows = [s for s in rows if os.path.normpath(s["directory"]) == client.source["directory"]]
    rows.sort(key=lambda s: (s["time"].get("updated", 0), s["id"]), reverse=True)
    return rows[:MAX_SESSIONS], statuses, truncated


def _iso(value):
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    try:
        return datetime.fromtimestamp(value / 1000, timezone.utc).isoformat()
    except (ValueError, OverflowError, OSError):
        return None


def _status(sid, statuses):
    # OpenCode 1.3.17 SessionStatus removes idle entries; absent entry is idle on a successful snapshot.
    return {"busy": "running", "idle": "idle", "retry": "retrying"}.get(statuses.get(sid, {}).get("type", "idle"), "unknown")


def _task(session, statuses):
    return {"id": session["id"], "goal": str(session.get("title") or session["id"])[:500],
            "project": session["directory"], "status": _status(session["id"], statuses),
            "owner": "opencode", "reviewer": "none", "updated_at": _iso(session["time"].get("updated")),
            "native_parent_id": session.get("parentID")}


def _actor(source, task, name=None, model=None, span=None, status=None):
    return {"id": "opencode:" + task["id"] + ":" + (span or "session"), "kind": "agent",
            "label": str(name or ("OpenCode · " + task["goal"]))[:120], "role": "native_session",
            "name_source": "native" if name else "generated", "provider": "opencode",
            "model": None, "observed_model": model, "native_session_id": task["id"],
            "task_id": task["id"], "span_id": span, "status": status or task["status"]}


def _empty():
    return {"schema_version": 1, "generated_at": now(), "source": "opencode", "read_only": True,
            "tasks": [], "actors": [], "spans": [], "edges": [], "events": [], "runs": [], "executions": [],
            "warnings": ["OpenCode의 현재 세션·메시지 스냅샷입니다. idle은 응답 대기이며 작업 검증 완료가 아닙니다. 프롬프트·도구 입력/출력 본문과 이벤트 재생은 수집하지 않습니다."],
            "pagination": {"has_more": False, "next_before": None}}


class Observer:
    """Per HTTP handler cache: bounded metadata only, no persistent session transcript."""
    def __init__(self, home):
        self.home = home
        self.lock = threading.Lock()
        self.cache = {}

    def source(self, identifier):
        source = next((s for s in sources(self.home) if s["id"] == identifier), None)
        if source is None:
            raise WorkflowError("등록되지 않은 OpenCode 연결입니다. opencode connect로 연결하세요.")
        return source

    def snapshot(self, source):
        key = json.dumps(source, sort_keys=True)
        with self.lock:
            cached = self.cache.get(key)
            if cached and time.monotonic() - cached[0] < 2:
                return cached[1]
            value = _sessions(Client(source))
            self.cache = {k: v for k, v in self.cache.items() if time.monotonic() - v[0] < 2}
            self.cache[key] = (time.monotonic(), value)
            return value

    def ledgers(self):
        result = []
        for source in sources(self.home):
            item = {"id": source["id"], "home": source["url"], "label": source["name"], "kind": "opencode",
                    "task_count": 0, "matching_task_count": 0, "status": "available",
                    "projects": [{"id": source["id"], "path": source["directory"]}]}
            try:
                rows, _, truncated = self.snapshot(source)
                item.update(task_count=len(rows), matching_task_count=len(rows), truncated=truncated)
            except WorkflowError as exc:
                item.update(status="unavailable", error=str(exc))
            result.append(item)
        return result

    def view(self, identifier, task_id=None, run_id=None, project=None):
        source = self.source(identifier)
        rows, statuses, truncated = self.snapshot(source)
        data = _empty()
        if project and project != source["directory"]:
            rows = []
        if not task_id:
            data["tasks"] = [_task(s, statuses) for s in rows]
            data["actors"] = [_actor(source, task) for task in data["tasks"]]
            ids = {t["id"] for t in data["tasks"]}
            data["edges"] = [{"id": t["id"] + ":parent", "source": "opencode:" + t["native_parent_id"] + ":session",
                              "target": "opencode:" + t["id"] + ":session", "kind": "parent", "label": "OpenCode에 기록된 부모 세션"}
                             for t in data["tasks"] if t["native_parent_id"] in ids]
            data["counts"] = {"tasks": len(rows), "running": sum(t["status"] == "running" for t in data["tasks"]),
                              "unknown": sum(t["status"] == "unknown" for t in data["tasks"]), "queued": 0}
            data["truncated"] = {"tasks": truncated}
            return data
        session = next((s for s in rows if s["id"] == task_id), None)
        if session is None:
            raise WorkflowError("이 연결의 최근 세션 목록에 없는 ID입니다. 프로젝트와 세션 목록을 확인하세요.")
        task = _task(session, statuses)
        data["task"] = task
        messages = Client(source).get("/session/" + quote(task_id, safe="") + "/message", limit=MAX_MESSAGES)
        if not isinstance(messages, list):
            raise WorkflowError("지원되지 않는 OpenCode 메시지 응답입니다.")
        valid = []
        for message in messages:
            if not isinstance(message, dict) or not isinstance(message.get("info"), dict) or not isinstance(message.get("parts"), list):
                raise WorkflowError("지원되지 않는 OpenCode 메시지 응답입니다.")
            info = message["info"]
            if info.get("role") == "assistant":
                if (not isinstance(info.get("id"), str) or info.get("sessionID") != task_id
                        or not isinstance(info.get("time"), dict)):
                    raise WorkflowError("OpenCode 메시지 식별자가 올바르지 않습니다.")
                valid.append(message)
        valid.sort(key=lambda m: (m["info"]["time"].get("created", 0), m["info"]["id"]))
        valid = valid[-MAX_MESSAGES:]
        for message in reversed(valid):
            info = message["info"]
            status = ("failed" if info.get("error") else "succeeded" if info["time"].get("completed")
                      else "running" if task["status"] == "running" and message is valid[-1] else "unknown")
            data["runs"].append({"id": info["id"], "task_id": task_id, "action": "opencode_message", "status": status,
                                 "started_at": _iso(info["time"].get("created")), "ended_at": _iso(info["time"].get("completed"))})
        selected = run_id or (data["runs"][0]["id"] if data["runs"] else None)
        if run_id and not any(r["id"] == run_id for r in data["runs"]):
            raise WorkflowError("최근 조회 범위에 없는 OpenCode 응답 ID입니다.")
        data.update(selected_run_id=selected, runs_truncated=len(messages) >= MAX_MESSAGES)
        if not selected:
            data["actors"] = [_actor(source, task)]
            return data
        message = next(m for m in valid if m["info"]["id"] == selected)
        info = message["info"]
        run = next(r for r in data["runs"] if r["id"] == selected)
        model = "/".join(str(info.get(k) or "unknown") for k in ("providerID", "modelID"))
        actor = _actor(source, task, info.get("agent"), model, selected, run["status"])
        data["actors"] = [actor]
        data["spans"] = [{**run, "run_id": selected, "actor": actor["id"], "role": "native_session",
                          "label": "OpenCode 응답", "sequence": 0, "result": {"message_id": selected, "finish": info.get("finish")}}]
        for part in message["parts"]:
            if not isinstance(part, dict):
                raise WorkflowError("지원되지 않는 OpenCode 메시지 part입니다.")
            if part.get("type") != "tool":
                continue
            if (not isinstance(part.get("id"), str) or not isinstance(part.get("state"), dict)
                    or part.get("sessionID") != task_id or part.get("messageID") != selected):
                raise WorkflowError("OpenCode 도구 식별자가 올바르지 않습니다.")
            state = part["state"]
            native_status = state.get("status")
            status = {"completed": "succeeded", "error": "failed", "pending": "pending", "running": "running"}.get(native_status, "unknown")
            if status == "running" and run["status"] != "running":
                status = "unknown"
            times = state.get("time") or {}
            if not isinstance(times, dict):
                raise WorkflowError("OpenCode 도구 시간 형식이 올바르지 않습니다.")
            span_id = part["id"]
            tool = str(part.get("tool") or "도구")[:120]
            tool_actor = {**actor, "id": actor["id"] + ":" + span_id, "kind": "service", "label": tool,
                          "name_source": "native", "role": "tool", "span_id": span_id, "status": status}
            data["actors"].append(tool_actor)
            data["spans"].append({"id": span_id, "task_id": task_id, "run_id": selected, "actor": tool_actor["id"],
                                  "role": "tool", "label": tool, "status": status, "sequence": len(data["spans"]),
                                  "started_at": _iso(times.get("start")), "ended_at": _iso(times.get("end")),
                                  "result": {"tool": tool, "status": native_status}, "parent_id": selected})
            data["edges"].append({"id": selected + ":" + span_id, "source": selected, "target": span_id,
                                  "kind": "tool", "label": "메시지에 기록된 도구 호출"})
        return data
