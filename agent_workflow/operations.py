"""Local notification outbox and explicit user-service installation."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shlex
import stat
import subprocess
import sys

from .core import WorkflowError, atomic_write, encode, now


def init_notifications(store):
    store.db.execute("CREATE TABLE IF NOT EXISTS notifications(id TEXT PRIMARY KEY, task_id TEXT NOT NULL, data TEXT NOT NULL)")
    store.db.commit()


def notifications(store):
    init_notifications(store)
    return [json.loads(row[0]) for row in store.db.execute("SELECT data FROM notifications ORDER BY rowid DESC LIMIT 100")]


def publish_notification(store, task, job=None):
    init_notifications(store)
    payload = {"task_id": task["id"], "status": task.get("status"), "goal": task.get("goal"),
               "blocker": task.get("blocker"), "fingerprint": task.get("completed_fingerprint"),
               "release_stage": (task.get("release") or {}).get("stage"),
               "release_error": (task.get("release") or {}).get("error"), "job": job}
    key = hashlib.sha256(encode(payload).encode()).hexdigest()
    item = {"id": key, "at": now(), "payload": payload, "delivery": "local"}
    with store.db:
        created = store.db.execute("INSERT OR IGNORE INTO notifications VALUES (?,?,?)", (key, task["id"], encode(item))).rowcount
    if not created:
        return json.loads(store.db.execute("SELECT data FROM notifications WHERE id=?", (key,)).fetchone()[0])
    command = (task.get("project_config") or {}).get("notifications", {}).get("command")
    if command:
        # The explicit profile command is the only delivery adapter. No messaging
        # destination is discovered from a repository or inferred from task text.
        directory = store.directory(task["id"]) / "notifications" / key
        atomic_write(directory / "event.json", encode(item))
        item["delivery"] = "started"
        with store.db:
            store.db.execute("UPDATE notifications SET data=? WHERE id=?", (encode(item), key))
        try:
            from .runner import process
            result = process(command, directory, directory / "command", 30, (), env_extra={"WORKFLOW_NOTIFICATION_FILE": str(directory / "event.json")})
            item.update(result=result, delivery="delivered" if result["exit_code"] == 0 and not result["timed_out"] else "failed")
        except (WorkflowError, OSError) as exc:
            item.update(delivery="failed", error=str(exc))
        with store.db:
            store.db.execute("UPDATE notifications SET data=? WHERE id=?", (encode(item), key))
    return item


def unit_quote(value, command=False):
    value = str(value)
    if any(x in value for x in ("\n", "\r", "\0")):
        raise WorkflowError("서비스 경로·환경에 줄바꿈을 사용할 수 없습니다.")
    value = value.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%")
    if command:
        value = value.replace("$", "$$")
    return '"' + value + '"'


def service_unit(home, port, source=None):
    if type(port) is not int or not 1024 <= port <= 65535:
        raise WorkflowError("서비스 포트는 1024~65535입니다.")
    source = Path(source or Path(__file__).resolve().parent.parent).resolve()
    argv = [sys.executable, "-m", "agent_workflow", "--home", str(Path(home).resolve()), "serve", "--port", str(port)]
    return "\n".join(["# Managed by agent-workflow", "[Unit]", "Description=Local Agent Workflow",
                       "After=network.target", "", "[Service]", "Type=simple",
                       "WorkingDirectory=" + unit_quote(source),
                       "Environment=" + unit_quote("PATH=" + os.environ.get("PATH", "/usr/bin:/bin")),
                       "ExecStart=" + " ".join(unit_quote(x, command=True) for x in argv),
                       "Restart=on-failure", "RestartSec=5", "KillMode=control-group", "TimeoutStopSec=15",
                       "UMask=0077", "", "[Install]", "WantedBy=default.target", ""])


def install_login_start(store, port, enable=False, user_home=None):
    if type(port) is not int or not 1024 <= port <= 65535:
        raise WorkflowError("서비스 포트는 1024~65535입니다.")
    user_root = Path(user_home or Path.home()).resolve()
    launcher = user_root / ".config/agent-workflow/login-start.sh"
    signature = "#!/bin/sh\n# Managed by agent-workflow\n"
    if launcher.exists() and not launcher.read_text().startswith(signature):
        raise WorkflowError("기존 로그인 시작 스크립트를 덮어쓰지 않습니다.")
    source = Path(__file__).resolve().parent.parent
    command = [sys.executable, "-m", "agent_workflow", "--home", str(store.home), "daemon", "start", "--port", str(port)]
    script = signature + "PATH=" + shlex.quote(os.environ.get("PATH", "/usr/bin:/bin")) + "\nexport PATH\n"
    script += "cd " + shlex.quote(str(source)) + " || exit 1\nexec " + shlex.join(command) + "\n"
    atomic_write(launcher, script)
    os.chmod(launcher, 0o700)
    # Bash reads the first existing login profile. Preserve that selection and
    # never alter .bashrc or the user's unrelated profile contents.
    candidates = [user_root / name for name in (".bash_profile", ".bash_login", ".profile")]
    profile = next((p for p in candidates if p.exists()), candidates[-1])
    result = {"backend": "shell", "path": str(launcher), "profile": str(profile), "enabled": False,
              "trigger": "interactive-login-shell", "note": "WSL 부팅 자체가 아니라 대화형 Bash 로그인 셸 시작 시 실행합니다."}
    if enable:
        begin, end = "# >>> agent-workflow >>>", "# <<< agent-workflow <<<"
        target = profile.resolve()
        if not target.is_relative_to(user_root):
            raise WorkflowError("홈 밖의 공유 로그인 프로필은 자동 변경하지 않습니다.")
        original = profile.read_text() if profile.exists() else ""
        if original.count(begin) > 1 or original.count(end) > 1 or (begin in original) != (end in original):
            raise WorkflowError("로그인 프로필의 관리 구역이 손상됐습니다.")
        block = begin + "\ncase $- in\n  *i*) (" + shlex.quote(str(launcher)) + " >/dev/null 2>&1 || :) ;;\nesac\n" + end
        if begin in original:
            left, right = original.index(begin), original.index(end)
            if right < left:
                raise WorkflowError("로그인 프로필 관리 구역의 순서가 잘못됐습니다.")
            updated = original[:left] + block + original[right + len(end):]
        else:
            updated = original + ("\n" if original and not original.endswith("\n") else "") + "\n" + block + "\n"
        mode = stat.S_IMODE(profile.stat().st_mode) if profile.exists() else 0o644
        atomic_write(target, updated); os.chmod(target, mode)
        from .server import start_background
        result["daemon"] = start_background(store.home, port)
        result["enabled"] = True
    return result


def install_service(store, port=8776, enable=False, user_home=None, backend="systemd"):
    if backend == "auto":
        try:
            check = subprocess.run(["systemctl", "--user", "show-environment"], stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL, timeout=5)
            backend = "systemd" if check.returncode == 0 else "shell"
        except (OSError, subprocess.TimeoutExpired):
            backend = "shell"
    if backend == "shell":
        return install_login_start(store, port, enable, user_home)
    if backend != "systemd":
        raise WorkflowError("자동 시작 방식은 auto, systemd, shell입니다.")
    path = Path(user_home or Path.home()) / ".config/systemd/user/agent-workflow.service"
    if path.exists() and not path.read_text().startswith("# Managed by agent-workflow\n"):
        raise WorkflowError("기존 사용자 서비스 파일을 덮어쓰지 않습니다: " + str(path))
    atomic_write(path, service_unit(store.home, port))
    result = {"backend": "systemd", "path": str(path), "enabled": False, "note": "사용자 systemd와 WSL 기동이 필요합니다."}
    if enable:
        for args in (["daemon-reload"], ["enable", "--now", "agent-workflow.service"]):
            command = subprocess.run(["systemctl", "--user", *args], capture_output=True, text=True, timeout=30)
            if command.returncode:
                raise WorkflowError("서비스 파일은 생성했지만 systemd 적용 실패: " + command.stderr.strip())
        result["enabled"] = True
    return result
