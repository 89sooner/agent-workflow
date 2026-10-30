"""Finite local test services. Only processes created for this task are terminated."""
from __future__ import annotations

import contextlib
import http.client
import os
from pathlib import Path
import signal
import socket
import subprocess
import time
import uuid
from urllib.parse import urlsplit

from .core import WorkflowError, now
from .policy import window_limit


def environment(task, directory):
    # Bytecode caches would otherwise appear as untracked code in repositories without an ignore rule.
    result = {"PYTHONDONTWRITEBYTECODE": os.environ.get("PYTHONDONTWRITEBYTECODE", "1"),
              "WORKFLOW_TASK_ID": task["id"], "WORKFLOW_EVIDENCE_DIR": str(directory)}
    for item in (task.get("project_config") or {}).get("services", []):
        result["WORKFLOW_SERVICE_" + item["name"].upper().replace("-", "_").replace(".", "_") + "_URL"] = item["ready_url"]
    return result


def stop_recorded(store, task):
    """Recover owned helpers after a hard controller crash without trusting a reused PID."""
    from .server import identity
    records = task.get("active_services") or []
    for record in records:
        if record.get("state") != "running":
            continue
        pid = record["pid"]
        if identity(pid) == record["process_identity"]:
            try:
                if os.getpgid(pid) != pid:
                    raise WorkflowError("서비스 프로세스 그룹이 변경됐습니다.")
                os.killpg(pid, signal.SIGTERM)
                deadline = time.monotonic() + 2
                while identity(pid) == record["process_identity"] and time.monotonic() < deadline:
                    time.sleep(.05)
                if identity(pid) == record["process_identity"]:
                    os.killpg(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        record.update(state="stopped", stopped_at=now())
    if records:
        store.save(task, "services_reconciled", {"services": records})


def ready(service):
    address = urlsplit(service["ready_url"])
    connection = http.client.HTTPConnection("127.0.0.1", service["port"], timeout=.5)
    try:
        connection.request("GET", (address.path or "/") + ("?" + address.query if address.query else ""))
        response = connection.getresponse()
        return 200 <= response.status < 400
    except (OSError, http.client.HTTPException):
        return False
    finally:
        connection.close()


@contextlib.contextmanager
def running(store, task, lock_fd, cwd=None, deadline=None):
    from .core import ensure_not_held
    from .runner import stop_group
    from .server import identity
    services = (task.get("project_config") or {}).get("services", [])
    children = []
    try:
        for item in services:
            ensure_not_held(store, task)
            left = deadline - time.monotonic() if deadline is not None else float("inf")
            if left <= 0:
                raise WorkflowError("검사 서버 시작 전 실행 시간 한도가 끝났습니다.")
            ready_deadline = time.monotonic() + min(item["startup_seconds"],
                window_limit(task.get("project_config") or {}), left)
            with socket.socket() as probe:
                # Ignore closed connections in TIME_WAIT, while a live listener
                # still prevents this bind. SO_REUSEPORT is deliberately not used.
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                try:
                    probe.bind(("127.0.0.1", item["port"]))
                except OSError as exc:
                    raise WorkflowError(f"검사 포트가 이미 사용 중이거나 사용할 수 없습니다: {item['port']}") from exc
            directory = store.directory(task["id"]) / "services" / (item["name"] + "-" + uuid.uuid4().hex[:8])
            directory.mkdir(parents=True)
            with (directory / "stdout.log").open("w") as out, (directory / "stderr.log").open("w") as err:
                proc = subprocess.Popen(item["argv"], cwd=cwd or task["project"], stdin=subprocess.DEVNULL,
                                        stdout=out, stderr=err, start_new_session=True,
                                        pass_fds=tuple(x for x in (lock_fd if isinstance(lock_fd, tuple) else (lock_fd,)) if x is not None),
                                        env={**os.environ, **environment(task, directory)})
            record = {"name": item["name"], "pid": proc.pid, "process_identity": identity(proc.pid),
                      "state": "running", "directory": str(directory), "port": item["port"], "started_at": now()}
            children.append((proc, record))
            task.setdefault("active_services", []).append(record)
            store.save(task, "service_started", record)
            while True:
                ensure_not_held(store, task)
                window_limit(task.get("project_config") or {})
                if proc.poll() is not None:
                    raise WorkflowError("검사 서버 시작 실패. 로그: " + str(directory))
                if time.monotonic() >= ready_deadline:
                    raise WorkflowError("검사 서버 준비 시간 초과. 로그: " + str(directory))
                if ready(item):
                    break
                time.sleep(.1)
        yield
    finally:
        for proc, record in reversed(children):
            stop_group(proc)
            record.update(state="stopped", stopped_at=now(), exit_code=proc.returncode)
            store.save(task, "service_stopped", record)
