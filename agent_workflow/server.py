"""Loopback-only dashboard and lifecycle management for the queue worker."""
from __future__ import annotations

import fcntl
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import signal
import subprocess
import sys
import threading
import time
from urllib.parse import unquote, urlparse

from . import __version__
from .core import Store, WorkflowError, atomic_write, encode, now, snapshot
from .daemon import cancel, enqueue, list_jobs, run_daemon, stop_job, resume_job


def identity(pid):
    try:
        # Field 22 survives PID reuse; comm may contain spaces/parentheses.
        return Path(f"/proc/{pid}/stat").read_text().rsplit(") ", 1)[1].split()[19]
    except (OSError, IndexError):
        return None


def status(home):
    path = Path(home) / "server.json"
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return {"running": False}
    data["running"] = bool(identity(data["pid"]) == data["process_identity"])
    return data


def start_background(home, port=8765):
    home = Path(home).resolve()
    home.mkdir(parents=True, exist_ok=True, mode=0o700)
    if status(home)["running"]:
        return status(home)
    command = [sys.executable, "-m", "agent_workflow", "--home", str(home), "serve", "--port", str(port)]
    with (home / "server.log").open("ab") as log:
        p = subprocess.Popen(command, cwd=Path(__file__).resolve().parent.parent,
                             stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                             start_new_session=True)
    for _ in range(100):
        if p.poll() is not None:
            raise WorkflowError(f"데몬 시작 실패. 로그: {home / 'server.log'}")
        data = status(home)
        if data["running"] and data["pid"] == p.pid:
            return data
        time.sleep(0.05)
    p.terminate()
    raise WorkflowError("데몬 시작 시간 초과")


def stop_background(home):
    data = status(home)
    if not data["running"]:
        return data
    os.kill(data["pid"], signal.SIGTERM)
    for _ in range(160):
        if not status(home)["running"]:
            return {"running": False, "stopped_pid": data["pid"]}
        time.sleep(0.05)
    raise WorkflowError("데몬 종료 처리 중입니다. 로그를 확인하세요.")


def handler(home, token):
    assets = Path(__file__).with_name("static")

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            # Do not log CSRF tokens or arbitrary request bodies.
            pass

        def origin(self):
            host = self.headers.get("Host", "")
            port = self.server.server_address[1]
            if host not in (f"127.0.0.1:{port}", f"localhost:{port}"):
                raise WorkflowError("허용되지 않은 Host")
            origin = self.headers.get("Origin")
            if origin and origin != "http://" + host:
                raise WorkflowError("다른 출처의 요청은 허용하지 않습니다.")

        def response(self, data, status_code=200, content_type="application/json; charset=utf-8"):
            body = data if isinstance(data, bytes) else encode(data).encode()
            self.send_response(status_code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            store = None
            try:
                self.origin()
                path = unquote(urlparse(self.path).path)
                if path in ("/", "/static/app.js", "/static/styles.css"):
                    name = "index.html" if path == "/" else path.rsplit("/", 1)[1]
                    mime = {"index.html": "text/html", "app.js": "text/javascript", "styles.css": "text/css"}[name]
                    return self.response((assets / name).read_bytes(), content_type=mime + "; charset=utf-8")
                if path == "/demo":
                    p = Path(home) / "demo/site/index.html"
                    if not p.exists():
                        return self.response({"error": "시범 배포가 아직 없습니다."}, 404)
                    return self.response(p.read_bytes(), content_type="text/html; charset=utf-8")
                store = Store(home)
                if path == "/api/state":
                    from .profiles import list_profiles
                    from .operations import notifications
                    return self.response({"tasks": store.all(), "jobs": list_jobs(store),
                                          "profiles": list_profiles(store), "notifications": notifications(store),
                                          "daemon": status(home), "version": __version__, "csrf_token": token})
                if path.startswith("/api/job-log/"):
                    job_id = path.removeprefix("/api/job-log/")
                    jobs = list_jobs(store)
                    job = next((item for item in jobs if item["id"] == job_id), None)
                    if not job or not job.get("log_path"):
                        raise WorkflowError("실행 로그가 아직 없습니다.")
                    log = Path(job["log_path"]).resolve()
                    if log != (store.home / "jobs" / (job_id + ".log")).resolve() or not log.is_relative_to(store.home / "jobs"):
                        raise WorkflowError("등록된 실행 로그 경로가 잘못됐습니다.")
                    with log.open("rb") as stream:
                        stream.seek(0, 2)
                        size = stream.tell()
                        stream.seek(max(0, size - 65536))
                        text = stream.read().decode(errors="replace")
                    return self.response({"job_id": job_id, "text": text, "truncated": size > 65536})
                if path.startswith("/api/tasks/"):
                    task = store.get(path.removeprefix("/api/tasks/"))
                    from .cli import freshness
                    try:
                        from .workspaces import observed_snapshot
                        current = observed_snapshot(task)
                        flags = freshness(task, current)
                    except (WorkflowError, OSError) as exc:
                        current, flags = {"error": str(exc)}, {}
                    rows = store.db.execute("SELECT at,kind,data FROM events WHERE task_id=? ORDER BY seq DESC LIMIT 60", (task["id"],)).fetchall()
                    events = [{"at": r[0], "kind": r[1], "data": json.loads(r[2])} for r in rows]
                    return self.response({"task": task, "observed_git": current, "freshness": flags,
                                          "events": events, "jobs": [j for j in list_jobs(store) if j["task_id"] == task["id"]]})
                return self.response({"error": "경로 없음"}, 404)
            except WorkflowError as exc:
                self.response({"error": str(exc)}, 400)
            except (OSError, ValueError) as exc:
                self.response({"error": str(exc)}, 500)
            finally:
                if store:
                    store.close()

        def do_POST(self):
            store = None
            try:
                self.origin()
                if not secrets.compare_digest(self.headers.get("X-Workflow-Token", ""), token):
                    return self.response({"error": "요청 토큰 오류"}, 403)
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 8192:
                    raise WorkflowError("요청 크기 오류")
                data = json.loads(self.rfile.read(length))
                if not isinstance(data, dict):
                    raise WorkflowError("JSON object가 필요합니다.")
                store = Store(home)
                if self.path == "/api/tasks":
                    from .workspaces import create_from_profile
                    from .profiles import identifier, strings
                    task_id = identifier(data.get("id"))
                    profile = identifier(data.get("profile"))
                    goal = data.get("goal")
                    criteria = strings(data.get("criteria"), "criteria")
                    isolate = data.get("isolate")
                    if not isinstance(goal, str) or not goal.strip() or not criteria:
                        raise WorkflowError("목표와 완료 조건이 필요합니다.")
                    if isolate is not None and type(isolate) is not bool:
                        raise WorkflowError("isolate는 boolean입니다.")
                    task = create_from_profile(store, task_id, profile, goal, criteria, isolate=isolate)
                    return self.response({"task": task}, 201)
                if self.path == "/api/queue":
                    task_id = data.get("task_id")
                    if not isinstance(task_id, str):
                        raise WorkflowError("task_id가 필요합니다.")
                    task = store.get(task_id)
                    action = data.get("action", "pipeline")
                    if action == "release" and not task.get("release"):
                        raise WorkflowError("릴리스 대상을 먼저 설정하세요.")
                    return self.response({"job": enqueue(store, task_id, action, data.get("timeout"), data.get("repairs"), data.get("recoveries"))})
                if self.path == "/api/cancel":
                    if not isinstance(data.get("job_id"), str):
                        raise WorkflowError("job_id가 필요합니다.")
                    return self.response({"job": cancel(store, data["job_id"])})
                if self.path in {"/api/stop", "/api/resume"}:
                    if not isinstance(data.get("job_id"), str):
                        raise WorkflowError("job_id가 필요합니다.")
                    operation = stop_job if self.path == "/api/stop" else resume_job
                    return self.response({"job": operation(store, data["job_id"])})
                if self.path == "/api/release-clear":
                    from .release import clear_configuration
                    task_id = data.get("task_id")
                    if not isinstance(task_id, str):
                        raise WorkflowError("task_id가 필요합니다.")
                    return self.response({"task": clear_configuration(store, task_id)})
                return self.response({"error": "경로 없음"}, 404)
            except (WorkflowError, ValueError) as exc:
                self.response({"error": str(exc)}, 400)
            except OSError as exc:
                self.response({"error": str(exc)}, 500)
            finally:
                if store:
                    store.close()
    return Handler


def serve(home, port=8765):
    home = Path(home).resolve()
    home.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (home / "server.lock").open("a+") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise WorkflowError("대시보드 서버가 이미 실행 중입니다.") from exc
        stop = threading.Event()
        token = secrets.token_urlsafe(32)
        http = ThreadingHTTPServer(("127.0.0.1", port), handler(home, token))
        http.daemon_threads = True
        http.timeout = 0.25
        worker_errors = []
        def worker_main():
            try:
                run_daemon(home, stop)
            except BaseException as exc:
                worker_errors.append(str(exc))
                stop.set()
        worker = threading.Thread(target=worker_main, name="workflow-queue")
        worker.start()
        meta = {"pid": os.getpid(), "process_identity": identity(os.getpid()),
                "started_at": now(), "url": f"http://127.0.0.1:{http.server_port}"}
        atomic_write(home / "server.json", encode(meta))
        previous = {}
        for sig in (signal.SIGTERM, signal.SIGINT):
            previous[sig] = signal.signal(sig, lambda *_: stop.set())
        print(meta["url"], flush=True)
        try:
            while not stop.is_set():
                http.handle_request()
        finally:
            stop.set()
            worker.join(timeout=10)
            http.server_close()
            (home / "server.json").unlink(missing_ok=True)
            for sig, callback in previous.items():
                signal.signal(sig, callback)
        if worker_errors:
            raise WorkflowError("큐 데몬 종료: " + worker_errors[0])
