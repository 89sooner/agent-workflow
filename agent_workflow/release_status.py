"""Summarize the last recorded release receipts without contacting the project or its remote."""
from __future__ import annotations

import json
import sqlite3

from .core import Store, WorkflowError


def _check_result(receipt):
    if not receipt:
        return "not_recorded"
    if receipt.get("passed") is True:
        return "succeeded"
    if receipt.get("passed") is False:
        return "failed"
    return "uncertain"


def _merge(release):
    if not release:
        return "not_configured"
    if release.get("merge_commit"):
        return "succeeded"
    # The process may have changed the remote before it could record the receipt.
    if release.get("stage") in {"configured", "preparing", "committed"}:
        return "not_attempted"
    return "uncertain"


def _integration(release):
    receipt = release.get("integration_verification")
    if not receipt:
        return "not_recorded" if release else "not_configured"
    revision = release.get("merge_commit") or release.get("local_merge_candidate")
    if not revision or receipt.get("revision") != revision or not receipt.get("finished_at"):
        return "uncertain"
    return _check_result(receipt)


def _deployment(release):
    config = release.get("config") or {}
    if not config.get("deploy"):
        return "not_configured"
    state = release.get("deployment_state")
    if state in {"applied", "failed"}:
        return state
    if state or release.get("stage") in {"deploying", "deployed"}:
        # A retry records "started" before replacing its previous command result.
        return "uncertain"
    return "not_attempted"


def _health(release, deployment):
    if not (release.get("config") or {}).get("health"):
        return "not_configured"
    result = release.get("health_result")
    if not result:
        return "not_recorded" if deployment == "applied" else "not_attempted"
    if deployment != "applied":
        return "uncertain"
    if result.get("timed_out") or (isinstance(result.get("exit_code"), int) and result["exit_code"] != 0):
        return "failed"
    # The final receipt is written after checking the identity of the released checkout.
    if (result.get("exit_code") == 0 and result.get("timed_out") is False
            and release.get("stage") == "deployed" and release.get("deployed_revision")
            and release["deployed_revision"] == release.get("merge_commit")):
        return "succeeded"
    return "uncertain"


def summarize_release(task):
    """Return recorded outcomes. This projection does not attest to current checkout or service health."""
    release = task.get("release") or {}
    local_completion = "not_completed"
    if task.get("status") == "completed":
        local_completion = "succeeded" if task.get("completed_fingerprint") else "uncertain"
    deployment = _deployment(release)
    config = release.get("config") or {}
    return {
        "task_id": task.get("id"),
        "source": "local_ledger",
        "stage": release.get("stage"),
        "target": {key: config[key] for key in ("mode", "repo", "base", "branch") if config.get(key)},
        "pull_request": release.get("pr"),
        "candidate_commit": release.get("commit"),
        "local_verification": _check_result(task.get("verification")),
        "local_completion": local_completion,
        "merge": _merge(release),
        "merge_commit": release.get("merge_commit"),
        "integration_check": _integration(release),
        "integration_revision": (release.get("integration_verification") or {}).get("revision"),
        "deployment": deployment,
        "deployment_revision": release.get("deployed_revision"),
        "health": _health(release, deployment),
        "deployed_at": release.get("deployed_at"),
        "updated_at": release.get("updated_at") or release.get("configured_at"),
        "error": release.get("error"),
    }


def status(home, task_id):
    """Read only the release-bearing task row. Legacy ledgers may lack task_controls and revision."""
    try:
        store = Store(home, read_only=True)
        try:
            row = store.db.execute("SELECT data FROM tasks WHERE id = ?", (task_id,)).fetchone()
            if row is None:
                raise WorkflowError(f"작업 없음: {task_id}")
            return summarize_release(json.loads(row[0]))
        finally:
            store.close()
    except sqlite3.Error as exc:
        raise WorkflowError(f"릴리스 상태 원장을 읽을 수 없습니다: {exc}") from exc
