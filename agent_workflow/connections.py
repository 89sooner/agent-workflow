"""Explicit project connections; discovery never initializes or changes a ledger."""
from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import uuid

from .core import Store, WorkflowError, atomic_write, create_task, now, project_root


def state_home(home=None):
    return Path(home or os.environ.get("AGENT_WORKFLOW_HOME") or
                Path.home() / ".local/state/agent-workflow").expanduser().resolve()


def ledger_home(home):
    path = Path(home).expanduser().resolve()
    return path.parent if path.name == "state.sqlite3" else path


def ledger_id(home):
    path = (ledger_home(home) / "state.sqlite3").resolve()
    return "ledger-" + hashlib.sha256(os.fsencode(path)).hexdigest()[:20]


def task_key(home, task_id):
    return ledger_id(home) + ":" + task_id


def project_key(path):
    """Group worktrees by common Git directory, retaining missing historical paths."""
    path = Path(path).expanduser().resolve()
    try:
        result = subprocess.run(["git", "-C", str(path), "rev-parse", "--git-common-dir"],
                                capture_output=True, text=True, timeout=2)
        common = result.stdout.strip()
        if result.returncode == 0 and common:
            path = (path / common).resolve()
    except (OSError, subprocess.TimeoutExpired):
        pass
    return "project-" + hashlib.sha256(os.fsencode(path)).hexdigest()[:20]


def task_project(task):
    return ((task.get("workspace") or {}).get("source") or
            (task.get("project_config") or {}).get("project") or task.get("project"))


def _process_homes():
    """Only recognize argv for this CLI, never arbitrary process text or cwd."""
    try:
        processes = sorted((p for p in Path("/proc").iterdir() if p.name.isdigit()),
                           key=lambda p: int(p.name))[:8192]
    except OSError:
        return
    for path in processes:
        try:
            args = (path / "cmdline").read_bytes().split(b"\0")
            args = [os.fsdecode(x) for x in args if x]
            if not args:
                continue
            program = Path(args[0]).name
            start = 1 if program == "agent-workflow" else None
            if program.startswith("python") and len(args) >= 3 and args[1:3] == ["-m", "agent_workflow"]:
                start = 3
            if start is None:
                continue
            # --home is a global argument; stop at the command itself.
            options = args[start:]
            if options and options[0] == "--home" and len(options) > 1:
                home = Path(options[1]).expanduser()
                yield (path / "cwd" / home).resolve() if not home.is_absolute() else home
            elif options and options[0].startswith("--home="):
                home = Path(options[0].partition("=")[2]).expanduser()
                yield (path / "cwd" / home).resolve() if not home.is_absolute() else home
        except (OSError, ValueError, RuntimeError):
            continue


def _inspect(home, wanted, keys):
    home = ledger_home(home)
    database = home / "state.sqlite3"
    result = {"id": ledger_id(home), "home": str(home), "task_count": 0,
              "matching_task_count": 0, "projects": [], "profile_names": [],
              "last_updated": None, "status": "available"}
    connection = None
    try:
        connection = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=.25)
        connection.execute("PRAGMA query_only=ON")
        rows = connection.execute("SELECT id,data FROM tasks ORDER BY id LIMIT 10001").fetchall()
        if len(rows) > 10000:
            result["truncated"] = True
        result["task_count"] = connection.execute("SELECT count(*) FROM tasks").fetchone()[0]
        projects = {}
        for task_id, raw in rows[:10000]:
            task = json.loads(raw)
            if not isinstance(task, dict):
                raise ValueError("작업 데이터가 객체가 아닙니다.")
            project = task_project(task)
            if project:
                key = keys.setdefault(project, None)
                if key is None:
                    key = keys[project] = project_key(project)
                projects.setdefault(key, {"id": key, "path": project})
                if wanted is None or key == wanted:
                    result["matching_task_count"] += 1
                    at = task.get("updated_at") or task.get("created_at")
                    if isinstance(at, str) and at > (result["last_updated"] or ""):
                        result["last_updated"] = at
        result["projects"] = list(projects.values())
        if connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='profiles'").fetchone():
            for name, raw in connection.execute("SELECT name,data FROM profiles ORDER BY name"):
                profile = json.loads(raw)
                project = profile.get("config", {}).get("project")
                if project and project not in keys:
                    keys[project] = project_key(project)
                if project and (wanted is None or keys[project] == wanted):
                    result["profile_names"].append(name)
    except (OSError, sqlite3.Error, ValueError, TypeError, AttributeError) as exc:
        result.update(status="unavailable", error=str(exc))
    finally:
        if connection is not None:
            connection.close()
    return result


def discover(project=None, homes=(), *, include_processes=True, include_temporary=True, include_default=True):
    """Bounded known-location discovery, without schema changes or model calls."""
    wanted = project_key(project) if project else None
    candidates = [ledger_home(h) for h in homes]
    default = state_home()
    if include_default and (default / "state.sqlite3").is_file():
        candidates.append(default)
    if include_processes:
        candidates.extend(_process_homes())
    if include_temporary:
        try:
            candidates.extend(sorted(Path("/tmp").glob("*workflow*/state.sqlite3"))[:128])
        except OSError:
            pass
    records, warnings, seen, keys = [], [], set(), {}
    for candidate in candidates:
        home = ledger_home(candidate)
        key = ledger_id(home)
        if key in seen:
            continue
        seen.add(key)
        record = _inspect(home, wanted, keys)
        if record["status"] == "unavailable":
            warnings.append({"home": str(home), "error": record["error"]})
        records.append(record)
    records.sort(key=lambda r: (r["matching_task_count"] > 0, r["last_updated"] or ""), reverse=True)
    return {"ledgers": records, "warnings": warnings, "project_id": wanted}


def load_registry(home=None):
    path = state_home(home) / "connections.json"
    if not path.exists():
        return {"version": 1, "projects": []}
    try:
        data = json.loads(path.read_text())
        if not isinstance(data, dict) or data.get("version") != 1 or not isinstance(data.get("projects"), list):
            raise ValueError("지원하지 않는 연결 형식")
        for item in data["projects"]:
            if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not isinstance(item.get("path"), str):
                raise ValueError("잘못된 프로젝트 연결")
            if not isinstance(item.get("ledgers"), list) or any(not isinstance(x, dict) or not isinstance(x.get("home"), str) for x in item["ledgers"]):
                raise ValueError("잘못된 원장 연결")
        return data
    except (OSError, ValueError) as exc:
        raise WorkflowError(f"연결 설정을 읽을 수 없습니다: {path}: {exc}") from exc


def registry_ledgers(home=None):
    homes = [state_home(home)]
    for project in load_registry(home)["projects"]:
        homes.extend(ledger_home(item["home"]) for item in project["ledgers"])
    result = {}
    for item in homes:
        result.setdefault(ledger_id(item), {"id": ledger_id(item), "home": str(item)})
    return list(result.values())


def resolve_ledger(home, identifier=None):
    if identifier is None:
        return state_home(home)
    for item in registry_ledgers(home):
        if item["id"] == identifier:
            return Path(item["home"])
    raise WorkflowError("등록되지 않은 원장입니다. connect --ledger로 연결하세요.")


@contextlib.contextmanager
def _registry_lock(home):
    home.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (home / "connections.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def _read_profile(home, name, project):
    connection = None
    try:
        connection = sqlite3.connect((home / "state.sqlite3").as_uri() + "?mode=ro", uri=True, timeout=.25)
        row = connection.execute("SELECT data FROM profiles WHERE name=?", (name,)).fetchone()
        if not row:
            raise WorkflowError("프로필 없음: " + name)
        record = json.loads(row[0])
        if project_key(record["config"]["project"]) != project_key(project):
            raise WorkflowError("프로필이 다른 프로젝트를 가리킵니다.")
        return record
    except (sqlite3.Error, ValueError, KeyError, TypeError) as exc:
        raise WorkflowError("저장된 프로필을 읽을 수 없습니다: " + name) from exc
    finally:
        if connection is not None:
            connection.close()


def connect(home, project, ledgers=(), profile=None, criteria=None, scope=None, registration_home=None,
            *, include_processes=True, include_temporary=True, include_default=True):
    home = state_home(home)
    project = project_root(project)
    key = project_key(project)
    previous = next((p for p in load_registry(home)["projects"] if p["id"] == key), {})
    candidates = [*ledgers, *(p["home"] for p in previous.get("ledgers", []))]
    if (home / "state.sqlite3").is_file():
        candidates.append(home)
    discovery = discover(project, candidates,
                         include_processes=include_processes, include_temporary=include_temporary,
                         include_default=include_default)
    explicit = {ledger_id(path) for path in ledgers}
    selected = [r for r in discovery["ledgers"] if r["status"] == "available" and
                (r["id"] in explicit or r["matching_task_count"] or r["profile_names"])]
    invalid = [r for r in discovery["ledgers"] if r["id"] in explicit and r["status"] != "available"]
    if invalid:
        raise WorkflowError("지정한 원장을 읽을 수 없습니다: " + "; ".join(r["home"] + ": " + r["error"] for r in invalid))
    eligible = [r for r in selected if not profile or profile in r["profile_names"]]
    if registration_home:
        registration = ledger_home(registration_home)
        if ledger_id(registration) not in {r["id"] for r in eligible}:
            raise WorkflowError("등록 대상 원장은 프로젝트 연결 및 지정 프로필과 일치해야 합니다.")
    elif len(explicit) == 1 and any(r["id"] in explicit for r in eligible):
        registration = Path(next(r["home"] for r in eligible if r["id"] in explicit))
    elif previous.get("registration_home") and ledger_id(previous["registration_home"]) in {r["id"] for r in eligible}:
        registration = Path(previous["registration_home"])
    elif len(eligible) == 1:
        registration = Path(eligible[0]["home"])
    elif not selected and not profile and not previous.get("ledgers"):
        registration = home
    else:
        registration = None
    if profile:
        if registration is None:
            raise WorkflowError("프로필의 원장을 하나로 지정하세요: --ledger 또는 --registration-home")
        _read_profile(registration, profile, project)
    if criteria is not None and (not criteria or any(not isinstance(c, str) or not c.strip() for c in criteria)):
        raise WorkflowError("완료 조건은 비어 있지 않은 문자열 목록입니다.")
    with _registry_lock(home):
        registry = load_registry(home)
        previous = next((p for p in registry["projects"] if p["id"] == key), {})
        remembered = {ledger_id(r["home"]): {"id": ledger_id(r["home"]), "home": str(ledger_home(r["home"]))}
                      for r in [*previous.get("ledgers", []), *selected]}
        record = {"id": key, "path": str(project), "ledgers": list(remembered.values()),
                  "profile": profile or previous.get("profile"),
                  "criteria": criteria if criteria is not None else previous.get("criteria", []),
                  "scope": scope if scope is not None else previous.get("scope", "지정 목표의 로컬 구현·검증. 커밋·푸시·병합·배포 제외."),
                  "registration_home": str(registration) if registration else None,
                  "registration_existing": bool(registration and (registration / "state.sqlite3").is_file()),
                  "updated_at": now()}
        # An existing default must remain valid when refreshing discovery.
        if record["profile"] and not profile:
            previous_home = previous.get("registration_home")
            if previous_home and str(registration) == previous_home:
                _read_profile(Path(previous_home), record["profile"], project)
                record["registration_home"] = previous_home
            else:
                record["profile"] = None
        registry["projects"] = [p for p in registry["projects"] if p["id"] != key] + [record]
        atomic_write(home / "connections.json", json.dumps(registry, ensure_ascii=False, indent=2))
    return {"connection": record, "discovery": discovery, "execution_started": False}


def start_registered(home, goal, project=".", criteria=None, *, checks=(), owner=None, reviewer=None,
                     name=None, reviewer_name=None):
    home = state_home(home)
    project = project_root(project)
    key = project_key(project)
    record = next((p for p in load_registry(home)["projects"] if p["id"] == key), None)
    if record is None:
        raise WorkflowError("프로젝트를 먼저 연결하세요: connect " + str(project))
    target = record.get("registration_home")
    if not target:
        raise WorkflowError("등록 원장이 여러 개입니다. connect --registration-home으로 지정하세요.")
    if record.get("registration_existing", True) and not (Path(target) / "state.sqlite3").is_file():
        raise WorkflowError("연결한 기존 원장이 사라졌습니다. 빈 원장으로 대체하지 않습니다: " + target)
    criteria = list(criteria if criteria is not None else record.get("criteria", []))
    if not criteria:
        raise WorkflowError("완료 조건이 필요합니다. start --criterion 또는 connect --criterion으로 저장하세요.")
    names = {role: value for role, value in (("implement", name), ("review", reviewer_name)) if value is not None}
    if any(not isinstance(value, str) or not value.strip() or len(value) > 120 or any(ord(c) < 32 for c in value) for value in names.values()):
        raise WorkflowError("에이전트 이름은 제어문자 없는 1~120자 문자열입니다.")
    task_id = "AW-" + now()[:10].replace("-", "") + "-" + uuid.uuid4().hex[:12]
    profile = record.get("profile")
    if profile:
        _read_profile(Path(target), profile, project)
    store = Store(target)
    try:
        if profile:
            from .workspaces import create_from_profile
            task = create_from_profile(store, task_id, profile, goal, criteria, checks=checks, owner=owner,
                                       reviewer=reviewer, scope=record["scope"])
        else:
            task = create_task(store, task_id, project, goal, criteria, [], list(checks), owner or "claude",
                               reviewer or "codex", record["scope"])
        if names:
            task["agent_names"] = names
            store.save(task, "agent_names_assigned", {"names": names})
        return {"task": task, "ledger_id": ledger_id(target), "state_home": str(store.home),
                "task_key": task_key(target, task_id), "execution_started": False,
                "next": "등록 완료. brief로 인계하거나 run/queue로 명시적으로 실행하세요."}
    finally:
        store.close()
