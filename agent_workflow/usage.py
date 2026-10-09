"""Durable invocation/time limits and provider-reported usage (unknown stays unknown)."""
from __future__ import annotations

import json
import math
from pathlib import Path
import time

from .core import WorkflowError, now
from .profiles import execution_config


def numeric(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def observation(agent, directory):
    path = Path(directory) / "stdout.log"
    records = []
    try:
        if agent == "claude" and path.stat().st_size <= 4_000_000:
            item = json.loads(path.read_text())
            if isinstance(item, dict):
                records.append(item)
        with path.open() as stream:
            for index, line in enumerate(stream):
                if agent == "claude" and records:
                    break
                if index > 100000 or len(line) > 4_000_000:
                    break
                try:
                    item = json.loads(line)
                except ValueError:
                    continue
                if isinstance(item, dict) and (agent == "claude" or item.get("type") == "turn.completed"):
                    records.append(item)
    except (OSError, ValueError):
        pass
    tokens = {}
    cost = None
    for item in records:
        usage = item.get("usage") or {}
        if isinstance(usage, dict):
            for key in ("input_tokens", "output_tokens", "cached_input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"):
                if type(usage.get(key)) is int and usage[key] >= 0:
                    tokens[key] = tokens.get(key, 0) + usage[key]
        if numeric(item.get("total_cost_usd")):
            cost = (cost or 0) + item["total_cost_usd"]
    return {"agent": agent, "tokens": tokens or None, "reported_cost_usd": cost,
            "source": str(path), "known": bool(tokens) or cost is not None}


def reconcile_pending(store, task):
    usage = task.setdefault("usage", {"agent_calls": 0, "seconds": 0, "uncertain_seconds": 0,
                                       "reported_cost_usd": None, "observations": []})
    if not any(item.get("reported_cost_usd") is not None for item in usage["observations"]):
        usage["reported_cost_usd"] = None
    pending = usage.pop("pending", None)
    if pending:
        # A crash can lose a result after an external model accepted the request.
        # Reserve the full unfinished interval; never invent a zero-cost success.
        usage["uncertain_seconds"] += pending["reserved_seconds"]
        store.save(task, "usage_reconciled", {"unknown_process": pending})
    return usage


def remaining(task):
    policy = execution_config(task.get("execution"))
    usage = task.get("usage") or {}
    left = policy["max_seconds"] - usage.get("seconds", 0) - usage.get("uncertain_seconds", 0)
    if left <= 0:
        raise WorkflowError("누적 실행 시간 한도에 도달했습니다. execution-config로 조건을 확인하세요.")
    return left


def ensure_call_budget(task):
    policy = execution_config(task.get("execution"))
    usage = task.get("usage") or {}
    if usage.get("agent_calls", 0) >= policy["max_agent_calls"]:
        raise WorkflowError("에이전트 호출 한도에 도달했습니다. execution-config로 조건을 확인하세요.")
    threshold = policy.get("stop_after_reported_usd")
    if threshold is not None and (usage.get("reported_cost_usd") or 0) >= threshold:
        raise WorkflowError("보고된 사용 금액의 중단 기준에 도달했습니다.")
    remaining(task)


def managed_process(store, task, command, cwd, directory, timeout, fd, input_text=None,
                    env_extra=None, agent=None, role=None, model=None):
    from .runner import process
    usage = reconcile_pending(store, task)
    policy = execution_config(task.get("execution"))
    timeout = min(timeout, remaining(task))
    if agent:
        ensure_call_budget(task)
        usage["agent_calls"] += 1
    usage["pending"] = {"directory": str(directory), "reserved_seconds": timeout, "agent": agent,
                        "role": role, "model": model, "at": now()}
    store.save(task, "process_budget_reserved", usage["pending"])
    started = time.monotonic()
    try:
        return process(command, cwd, directory, timeout, fd, input_text, env_extra)
    finally:
        elapsed = max(0, time.monotonic() - started)
        usage["seconds"] += elapsed
        usage.pop("pending", None)
        if agent:
            item = observation(agent, directory)
            item.update(role=role, model=model, seconds=elapsed)
            usage["observations"].append(item)
            if item["reported_cost_usd"] is not None:
                usage["reported_cost_usd"] = (usage["reported_cost_usd"] or 0) + item["reported_cost_usd"]
        store.save(task, "process_budget_recorded", {"seconds": elapsed, "agent": agent, "role": role})


def configure(store, task_id, options):
    task = store.get(task_id)
    validated = execution_config(options)
    with store.lock(task["project"]):
        task = store.get(task_id)
        if task["status"] == "completed":
            raise WorkflowError("완료한 작업의 실행 계약을 변경하지 않습니다.")
        task["execution"] = validated
        task.pop("model_routes", None)
        # Counters survive configuration changes and every resume.
        store.save(task, "execution_configured", validated)
        return task
