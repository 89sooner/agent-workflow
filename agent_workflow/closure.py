"""Evidence-backed closure and opt-in exports; repository notes are verified before finish."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import stat
from zoneinfo import ZoneInfo

from .core import WorkflowError, atomic_write, encode, ensure_not_held, now


def plain(value):
    text = str(value).replace("<!--", "&lt;!--").replace("-->", "--&gt;")
    text = re.sub(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{16,}", "[REDACTED]", text)
    return re.sub(r"(?i)(\b(?:api[_-]?key|access[_-]?token|password|authorization)\s*[:=]\s*)\S+", r"\1[REDACTED]", text)


def sync_documents(store, task):
    ensure_not_held(store, task)
    documents = (task.get("project_config") or {}).get("documents", [])
    if not documents:
        return False
    if task["status"] == "completed":
        raise WorkflowError("완료 후 저장소 문서를 변경하지 않습니다. 새 작업을 등록하세요.")
    root = Path(task["project"]).resolve()
    begin = f"<!-- agent-workflow:{task['id']}:begin -->"
    end = f"<!-- agent-workflow:{task['id']}:end -->"
    summary = (task.get("implementation") or {}).get("summary") or task["checkpoint"]["summary"]
    content = "\n".join([begin, f"### {task['id']}", "", "목표: " + plain(task["goal"]),
                          "구현 기록: " + plain(summary), "", "완료 조건:",
                          *["- " + plain(x) for x in task["criteria"]], "",
                          "최종 검사·리뷰·완료 상태는 생성된 작업 원장과 인계를 참조한다.",
                          "인계: `" + str(store.directory(task["id"]) / "handoff/state.json") + "`", end])
    changed = []
    for name in documents:
        path = (root / name).resolve()
        if not path.is_relative_to(root):
            raise WorkflowError("문서 경로가 저장소 밖을 가리킵니다.")
        from .policy import under
        cfg = task["project_config"]
        relative = path.relative_to(root).as_posix()
        if not any(under(relative, x) for x in (cfg.get("allowed_paths") or ["."])) or any(under(relative, x) for x in cfg.get("forbidden_paths", [])):
            raise WorkflowError("문서의 실제 대상이 허용 범위 밖입니다: " + name)
        old = path.read_text() if path.exists() else ""
        if old.count(begin) > 1 or old.count(end) > 1 or (begin in old) != (end in old):
            raise WorkflowError("문서의 관리 구역 표시가 손상됐습니다: " + name)
        if begin in old:
            left, right = old.index(begin), old.index(end)
            if right < left:
                raise WorkflowError("문서 관리 구역의 순서가 잘못됐습니다.")
            updated = old[:left] + content + old[right + len(end):]
        else:
            updated = old.rstrip() + ("\n\n" if old.strip() else "") + content + "\n"
        if old != updated:
            mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o644
            atomic_write(path, updated)
            os.chmod(path, mode)
            changed.append(name)
    if changed:
        task.update(verification=None, review=None)
        task["document_sync"] = {"paths": documents, "at": now()}
        store.save(task, "documents_updated", {"paths": changed})
    return bool(changed)


def worklog(store, task, state):
    from .workspaces import observed_snapshot
    current = observed_snapshot(task)
    cfg = (task.get("project_config") or {}).get("exports", {})
    date = dt.datetime.fromisoformat(task["created_at"]).astimezone(ZoneInfo(cfg.get("timezone", "UTC"))).date().isoformat()
    verified = bool(state["verification_is_current"] and (task.get("verification") or {}).get("passed"))
    reviewed = task["reviewer"] == "none" or bool(state["review_is_current"] and (task.get("review") or {}).get("verdict") == "pass")
    done = task["status"] == "completed" and state["completion_is_current"] and verified and reviewed
    status = "done" if done else "blocked" if task["status"] == "blocked" else "partial"
    title = plain(task["goal"])
    lines = ["---", "type: worklog", f"date: {date}", "title: " + json.dumps(title, ensure_ascii=False),
             "agent: " + ("claude-code" if task["owner"] == "claude" else "codex"), "status: " + status,
             "sensitivity: private", "tags: [agent-workflow, worklog]", "---", "", "# " + title, "", "## 작업 개요",
             f"- 작업 `{task['id']}`의 기록 상태는 `{task['status']}`이다.", "", "## 배경 / 요청",
             "> " + title.replace("\n", "\n> "), "", "## 변경 사항", "| 파일 | 변경 | 요지 |", "|---|---|---|"]
    from .policy import changed_files
    paths = changed_files(task["project"]) if not current.get("archived") else {}
    initial = task["baseline"].get("changed_files", {})
    touched = sorted({p for p, value in paths.items() if initial.get(p) != value} | {p for p in initial if p not in paths})
    # A committed release has a clean tree; list its actual committed diff instead.
    if not touched and (task.get("release") or {}).get("commit") and not current.get("archived"):
        from .core import git
        raw = git(task["project"], "diff", "--name-only", "-z", task["baseline"]["head"], task["release"]["commit"], binary=True)
        touched = [os.fsdecode(x) for x in raw.split(b"\0") if x]
    for path in touched:
        lines.append("| `" + plain(path).replace("|", "\\|") + "` | 변경 | Git 변경 목록에서 확인했다. |")
    if not touched:
        lines.append("| 없음 | 없음 | 새 파일 변경을 기록하지 않았다. |")
    lines += ["", "## 진행 과정", "1. 도구가 작업 목표와 완료 조건을 등록했다."]
    if task.get("implementation"):
        lines.append("2. 구현 결과를 기록했다.")
    if task.get("recoveries"):
        lines.append(f"- 도구가 복구 시도 {len(task['recoveries'])}회를 기록했다.")
    lines += ["", "## 검증"]
    results = (task.get("verification") or {}).get("results", [])
    for item in results:
        lines.append(f"- `{plain(shlex.join(item['argv']))}` → 결과: 종료 코드 `{item['exit_code']}`이다.")
    if not results:
        lines.append("- 검증 없음")
    lines += [f"- 현재 코드의 검사 증거는 {'유효하다' if verified else '유효하지 않다'}.",
              f"- 지정 리뷰 상태는 `{(task.get('review') or {}).get('verdict', '미지정' if task['reviewer'] == 'none' else '미실행')}`이다.",
              "", "## 이슈 / 미해결", "- " + plain(task.get("blocker") or (task.get("release") or {}).get("error") or "없음"),
              "", "## 후속 작업", "- [ ] " + plain(task["checkpoint"]["next"]), "", "## 참고 / 링크",
              "- 근거: `" + str(store.directory(task["id"]) / "handoff/state.json") + "`", "", "## 작업 흐름도", "```mermaid", "flowchart TD",
              '    A["작업 등록"] --> B["현재 변경 확인"]', '    B --> C["검사 기록 확인"]',
              '    C --> D["리뷰 기록 확인"]', f'    D --> E["결과: {status}"]', "```", "", "## 요약",
              f"1. 결과: 작업 상태는 `{status}`이다.", f"2. 작업 식별자는 `{task['id']}`이다.",
              f"3. 검사 명령 {len(results)}개의 결과를 기록했다.",
              f"4. 현재 검사 증거는 {'유효하다' if verified else '유효하지 않다'}.",
              f"5. 검토 담당은 `{task['reviewer']}`이다.",
              f"6. 릴리스 단계는 `{(task.get('release') or {}).get('stage', '미설정')}`이다.",
              "7. 다음 세션은 인계 파일로 작업을 확인한다."]
    return date, "\n".join(lines) + "\n"


def finalize(store, task, *, locked=False):
    """Export the latest state under the same worktree lock as configuration writes."""
    if not locked:
        with store.lock(task["project"]):
            return finalize(store, task, locked=True)
    latest = store.get(task["id"])
    task.clear()
    task.update(latest)
    from .cli import export_handoff, freshness
    from .workspaces import observed_snapshot
    directory = export_handoff(store, task)
    flags = freshness(task, observed_snapshot(task))
    date, text = worklog(store, task, flags)
    atomic_write(Path(directory) / "worklog.md", text)
    result = {"handoff": directory, "worklog": str(Path(directory) / "worklog.md"), "at": now()}
    target = (task.get("project_config") or {}).get("exports", {}).get("directory")
    if target:
        root = Path(target).expanduser().resolve()
        source = Path((task.get("workspace") or {}).get("source", task["project"])).resolve()
        if root.is_relative_to(Path(task["project"]).resolve()) or root.is_relative_to(source):
            raise WorkflowError("완료 후 내보내기는 검증한 저장소 밖의 폴더를 사용하세요.")
        path = root / f"{date}_{task['id']}.md"
        receipt = (task.get("closure") or {}).get("export") or {}
        if path.exists():
            existing = hashlib.sha256(path.read_bytes()).hexdigest()
            if receipt.get("path") != str(path) or receipt.get("sha256") != existing:
                raise WorkflowError("기존 작업일지에 별도 변경이 있습니다. 덮어쓰지 않습니다: " + str(path))
        digest = hashlib.sha256(text.encode()).hexdigest()
        if not path.exists() or receipt.get("sha256") != digest:
            atomic_write(path, text)
        result["export"] = {"path": str(path), "sha256": digest}
    task["closure"] = result
    store.save(task, "closure_finished", result)
    if not os.environ.get("AGENT_WORKFLOW_JOB_ID"):
        from .operations import publish_notification
        publish_notification(store, task)
    return result
