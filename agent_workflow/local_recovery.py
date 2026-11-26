"""Read-only reconciliation of recorded local steps against the current worktree."""
from __future__ import annotations

import hashlib
import json

from . import criterion_checks
from . import goal_policy
from .core import WorkflowError, git, references_current
from .policy import changed_files, protected_snapshot, scope_check


def check_context(task):
    config = task.get("project_config") or {}
    inputs = protected_snapshot(task["project"], config.get("check_inputs", []))
    value = {"commands": task["checks"], "services": config.get("services", []),
             "setup": config.get("setup", []), "criterion_checks": config.get("criterion_checks", {}),
             "inputs": inputs}
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def needs_check_context(task):
    """Honor completed receipts predating environment tracking only without declared inputs."""
    return bool((task.get("verification") or {}).get("check_context")
                or task.get("status") != "completed" or (task.get("project_config") or {}).get("check_inputs"))


def check_drift(task, current, refs=None, *, flags=None):
    """Return why a stored check receipt cannot be reused for execution."""
    receipt = task.get("verification") or {}
    if not receipt.get("passed"):
        return "현재 작업에 통과한 검사 기록이 없습니다."
    if receipt.get("fingerprint") != current.get("fingerprint"):
        return "검사 이후 코드 지문이 달라졌습니다."
    if flags is not None:
        refs_ok = not task.get("refs") or (flags.get("refs_fingerprint") is not None
                                           and receipt.get("refs_fingerprint") == flags["refs_fingerprint"])
    else:
        refs_ok = references_current(task, receipt, refs)
    if not refs_ok:
        return "검사 이후 참조 문서가 달라졌습니다."
    if not receipt.get("check_context"):
        return "이전 검사 기록에 검사 명령·환경 지문이 없습니다."
    context_ok = flags.get("check_context_is_current") if flags is not None else receipt["check_context"] == check_context(task)
    if not context_ok:
        return "검사 명령·서비스 설정 또는 등록한 환경 입력이 달라졌습니다."
    return None


def unbound_test_environment(task):
    """A pytest case may depend on ignored build outputs outside the source snapshot."""
    return (not (task.get("project_config") or {}).get("check_inputs")
            and any("testcase" in rule for _, rule in criterion_checks.bindings(task).values()))


def owner_delta(task, current):
    step = task.get("local_step") or {}
    return (step.get("stage") == "implementation" and step["head"] == current.get("head")
            and step["fingerprint"] != current.get("fingerprint"))


def check_owner_head(task, current):
    step = task.get("local_step") or {}
    if step.get("stage") == "implementation":
        if step["head"] != current.get("head") or ("branch" in step and step["branch"] != current.get("branch")):
            raise WorkflowError("구현 호출 이후 HEAD 또는 브랜치가 달라졌습니다. 대상 브랜치·변경을 확인한 뒤 현재 파일에서 check를 실행하세요.")


def local_diagnosis(task, current, *, read_git=git, flags=None):
    """Describe persisted evidence; calling this function never executes a check or model."""
    if task.get("status") == "completed" or not current or not current.get("fingerprint"):
        return None
    step = task.get("local_step") or {}
    verification = task.get("verification") or {}
    goal = goal_policy.state(task) or {}
    try:
        check_owner_head(task, current)
    except WorkflowError as exc:
        return {"stage": "implementation", "action": "inspect", "reason": str(exc),
                "confirmed_effects": [f"호출 당시 {step.get('branch', '')} {step['head'][:12]} · 관측 {current.get('branch', '')} {str(current.get('head'))[:12]}"],
                "next_action": "대상 브랜치·변경을 먼저 대조하세요."}
    if owner_delta(task, current):
        if task["status"] == "implementing" and step.get("pid"):
            from .server import identity
            if step.get("identity") and identity(step["pid"]) == step["identity"]:
                return None
        effect = f"구현 호출 직전과 같은 HEAD에서 작업 파일·인덱스 지문이 달라졌습니다 ({current['fingerprint'][:12]})."
        effects = [effect]
        before = step.get("changed_files")
        if isinstance(before, dict):
            after = changed_files(task["project"], read_git=read_git)
            paths = sorted(name for name in before.keys() | after.keys() if before.get(name) != after.get(name))
            if paths:
                effects.append(f"호출 직전과 다른 경로 {len(paths)}개 · " + ", ".join(paths[:8]))
        diagnose = {"stage": "implementation", "reason": "파일 변경 후 구현 단계 결과가 기록되지 않았습니다.",
                    "confirmed_effects": effects, "action": "verify_partial",
                    "next_action": "남은 변경을 등록 검사와 독립 리뷰에 제출합니다."}
        try:
            scope_check(task, read_git=read_git)
        except WorkflowError as exc:
            return {**diagnose, "action": "inspect", "reason": str(exc), "next_action": "등록 범위와 보존된 변경을 대조하세요."}
        if task["reviewer"] == "none":
            diagnose.update(action="assign_reviewer", next_action="독립 검토자를 지정한 뒤 남은 변경을 검증하세요.")
        elif goal and sum(i["state"] == "pending" for i in goal["items"]) > 1:
            diagnose["next_action"] = "읽기 전용으로 어느 조건에 변경이 반영됐는지 확인한 뒤 그 조건의 검사를 진행합니다."
        failure = task.get("failure") or {}
        if failure.get("directory") == (task.get("last_run") or {}).get("directory") and failure.get("kind") in {"authentication", "permission", "quota"}:
            diagnose["reason"] = failure.get("message") or diagnose["reason"]
            diagnose["next_action"] = "공급자 인증·권한·사용 한도 상태를 먼저 확인하세요. 명시적으로 재개하면 " + diagnose["next_action"]
        return diagnose
    if goal and (goal.get("needs_plan") or any(i["state"] != "proposed" for i in goal["items"])):
        return None
    if (task.get("review") or {}).get("verdict") == "changes_requested":
        return None
    if not verification.get("passed"):
        if step.get("stage") != "checks" or verification:
            return None
        return {"stage": "checks", "reason": "검사 대상은 준비됐지만 사용할 검사 결과가 없습니다.",
                "confirmed_effects": ["구현 또는 이전 검사 시도가 원장에 기록돼 있습니다."],
                "action": "recheck", "next_action": "보존된 작업 파일에서 등록 검사를 실행합니다."}
    effects = ["등록 검사 통과 기록이 원장에 남아 있습니다."]
    if verification.get("fingerprint") and "results" in verification:
        effects = [f"코드 지문 {verification['fingerprint'][:12]}에서 등록 검사 {len(verification['results'])}개가 통과했습니다."]
    try:
        drift = check_drift(task, current, flags=flags)
    except WorkflowError as exc:
        drift = str(exc)
    if not drift and unbound_test_environment(task):
        drift = "필수 시험이 사용하는 빌드 환경의 입력 지문이 등록되지 않았습니다."
    if drift:
        if not goal and (task.get("review") or {}).get("verdict") == "pass":
            return {"stage": "checks", "reason": drift, "confirmed_effects": effects,
                    "action": "reassess", "next_action": "이전 승인 후 변경된 입력으로 구현 담당이 요구사항을 재검토하고 검사·리뷰를 진행합니다."}
        return {"stage": "checks", "reason": drift, "confirmed_effects": effects,
                "action": "recheck", "next_action": "구현을 보존하고 현재 코드·검사 입력으로 다시 검사합니다."}
    if (task.get("review") or {}).get("verdict") == "pass":
        return None
    needs_review = task["reviewer"] != "none"
    return {"stage": "checks", "reason": "현재 입력의 검사 통과 후 독립 리뷰 결과를 기다립니다." if needs_review else "필수 검사가 완료됐습니다.",
            "confirmed_effects": effects, "action": "review" if needs_review else "finish",
            "next_action": "기존 검사 기록을 사용해 독립 리뷰를 진행합니다." if needs_review else "현재 검사 근거로 작업을 마감합니다."}
