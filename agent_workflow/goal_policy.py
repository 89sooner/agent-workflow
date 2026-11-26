"""Project policies and storage keys for the shared, local-only goal executor."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .core import WorkflowError


DIRECTIVE = "REFACTORING_SUPREME_DIRECTIVE.md"
HANDOFF = "agent-context/session-handoff.md"
KATAKURI_INSTRUCTIONS = ("AGENTS.md", DIRECTIVE, HANDOFF)
TEXT_EXTENSIONS = (".md", ".json", ".txt", ".csv")
KATAKURI_SCOPE = (
    "Local Katakuri software development only. Preserve project instructions and track boundaries. "
    "No broker access, paper/live orders, credentials, trading permissions, operational DB/service changes, "
    "deployment, or automatic milestone promotion. Report external dependencies as waiting."
)
PROJECT_SCOPE = (
    "Local software development only. Work within the registered scope and paths. "
    "Do not commit, push, merge, deploy, change operational services or access credentials."
)


def state_key(task):
    keys = [key for key in ("katakuri_goal", "goal_mode") if task.get(key) is not None]
    if len(keys) > 1 or (keys and (not isinstance(task[keys[0]], dict) or not task[keys[0]])):
        raise WorkflowError("작업에 서로 충돌하거나 유효하지 않은 목표 계약이 있습니다.")
    return keys[0] if keys else None


def state(task):
    key = state_key(task)
    return task[key] if key else None


def kind(task):
    return {"katakuri_goal": "katakuri", "goal_mode": "project"}.get(state_key(task))


def digest(policy):
    return hashlib.sha256(json.dumps(policy, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def policy(task):
    if kind(task) == "katakuri":
        # Original v1 records predate explicit project policies. The storage key
        # selects their original contract even after the profile has been edited.
        if state(task).get("version", 1) != 1:
            raise WorkflowError("지원하지 않는 Katakuri 목표 계약 버전입니다.")
        return {"version": 1, "scope": KATAKURI_SCOPE, "instructions": list(KATAKURI_INSTRUCTIONS),
                "input_extensions": list(TEXT_EXTENSIONS)}
    goal = state(task)
    frozen = (task.get("project_config") or {}).get("goal_mode")
    if (not goal or not isinstance(frozen, dict) or frozen.get("version") != 1
            or goal.get("version") != 1 or digest(frozen) != goal.get("policy_digest")):
        raise WorkflowError("목표에 고정된 프로젝트 정책 버전·지문을 확인할 수 없습니다.")
    return frozen


def is_katakuri(root, config):
    marker = root / DIRECTIVE
    names = [*config.get("refs", []), *(config.get("goal_mode") or {}).get("instructions", [])]
    return Path(config["project"]).name == "katakuri-band" or marker.exists() or marker.is_symlink() or DIRECTIVE in names


def checked_files(root, names):
    """Resolve policy sources on the checkout that will actually be executed."""
    root = root.resolve()
    for name in names:
        path = root / name
        try:
            inside = path.resolve(strict=True).is_relative_to(root) and path.is_file()
        except (OSError, RuntimeError):
            inside = False
        if not inside:
            raise WorkflowError(f"목표의 참조 문서는 작업 저장소 안의 실제 파일이어야 합니다: {name}")


def check_checkout(root, config, task_kind):
    if task_kind == "project":
        if is_katakuri(root, config):
            raise WorkflowError("Katakuri 프로젝트 목표는 katakuri-goal로 등록하세요.")
        checked_files(root, [*config.get("refs", []), *config["goal_mode"]["instructions"]])
    else:
        checked_files(root, KATAKURI_INSTRUCTIONS)
