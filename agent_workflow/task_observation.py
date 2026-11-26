"""Read the inputs for a resume exactly once and project them for every consumer."""
from __future__ import annotations

import subprocess

from .core import WorkflowError, reference_fingerprint, references_current
from .recovery import diagnosis
from .resume_packet import build_packet


def freshness(task, current):
    fp = current["fingerprint"]
    release = task.get("release") or {}
    # The release receipt binds the verified contents across staging/commit metadata changes.
    bound = bool(release.get("commit") == current["head"] and not current["dirty"])
    refs = reference_fingerprint(task)
    from .policy import protected_current
    protected = protected_current(task)
    verified_refs = references_current(task, task.get("verification"), refs)
    reviewed_refs = task.get("reviewer") == "none" or references_current(task, task.get("review"), refs)
    receipt = task.get("verification") or {}
    context_ok = None
    if receipt.get("check_context"):
        from .local_recovery import check_context
        try:
            context_ok = receipt["check_context"] == check_context(task)
        except (WorkflowError, OSError):
            context_ok = False
    elif receipt and (task.get("project_config") or {}).get("check_inputs"):
        context_ok = False
    # Older records can still describe the observed code and references. Their
    # missing environment receipt requires a fresh check on execution.
    inputs_ok = protected and context_ok is not False
    return {"verification_is_current": inputs_ok and verified_refs and (receipt.get("fingerprint") == fp or (bound and bool(receipt))),
            "review_is_current": inputs_ok and reviewed_refs and ((task.get("review") or {}).get("fingerprint") == fp or (bound and bool(task.get("review")))),
            "completion_is_current": inputs_ok and verified_refs and reviewed_refs and (task.get("completed_fingerprint") == fp or (bound and task["status"] == "completed")),
            "evidence_bound_via_release": bound and inputs_ok and verified_refs and reviewed_refs,
            "check_context_is_current": context_ok,
            "protected_paths_are_current": protected,
            "refs_fingerprint": refs}


def observe(task, ledger, *, current=None):
    """Use a supplied snapshot when lookup or execution already observed this worktree."""
    from .session_notes import _evidence_snapshot, read_only_git

    try:
        workspace = task.get("workspace") or {}
        if workspace.get("removed"):
            current = {**(workspace.get("last_snapshot") or {}), "archived": True,
                       "error": "정리한 작업 폴더의 마지막 기록만 남아 있습니다."}
        elif current is None:
            if not task.get("project"):
                current = {"error": "원장에 작업 트리 경로가 없습니다."}
            else:
                current = _evidence_snapshot(task["project"])
        flags = freshness(task, current) if current.get("fingerprint") and not current.get("archived") and not current.get("error") else {}
        recovery = diagnosis(task, current, read_git=read_only_git(), flags=flags)
    except (WorkflowError, OSError, subprocess.TimeoutExpired, RecursionError) as exc:
        current = {"error": str(exc)}
        flags = {}
        recovery = diagnosis(task, current)
    return {"observed_git": current, "freshness": flags, "recovery": recovery,
            "resume_packet": build_packet(task, current, flags, recovery, ledger=ledger)}
