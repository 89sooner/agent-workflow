"""Durable Katakuri development goals on one managed worktree and one cumulative budget."""

import hashlib
from pathlib import Path

from . import profiles
from .core import (
    WaitingForInput,
    WorkflowError,
    atomic_write,
    complete,
    encode,
    now,
    reference_fingerprint,
    references_current,
    snapshot,
)
from .policy import ensure_not_held, preflight, reservation, scope_check
from .usage import ensure_call_budget, reconcile_pending, remaining
from .workspaces import create_from_profile, prepare

DIRECTIVE = "REFACTORING_SUPREME_DIRECTIVE.md"
HANDOFF = "agent-context/session-handoff.md"
SCOPE = (
    "Local Katakuri software development only. Preserve project instructions and track boundaries. "
    "No broker access, paper/live orders, credentials, trading permissions, operational DB/service changes, "
    "deployment, or automatic milestone promotion. Report external dependencies as waiting."
)
STEP_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["criterion", "outcome", "summary", "next_action"],
    "properties": {
        "criterion": {"type": "integer"},
        "outcome": {"type": "string", "enum": ["progress", "ready", "waiting"]},
        "summary": {"type": "string"},
        "next_action": {"type": "string"},
    },
}


PLAN_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["criteria"],
    "properties": {"criteria": {"type": "array", "items": {"type": "string"}}},
}


def new_item(number):
    return {"id": number, "state": "pending", "summary": "", "next_action": "", "seen": [], "inputs": []}


def validate_plan(answer):
    if not isinstance(answer, dict) or set(answer) != {"criteria"}:
        raise WorkflowError("검증 가능한 완료 조건 목록이 필요합니다.")
    criteria = [s.strip() for s in profiles.strings(answer["criteria"], "criteria")]
    if (
        not 1 <= len(criteria) <= 12
        or len(set(criteria)) != len(criteria)
        or any(len(s) > 1000 for s in criteria)
    ):
        raise WorkflowError("계획은 서로 다른 구체적인 완료 조건 1~12개여야 합니다.")
    return {"criteria": criteria}


def supported(config):
    root = Path(config["project"])
    return (
        bool(config.get("allowed_paths"))
        and not any(Path(p) == Path(".") for p in config["allowed_paths"])
        and (bool(config.get("checks")) or any(config.get("check_groups", {}).values()))
        and config.get("reviewer") != "none"
        and all((root / p).is_file() for p in ("AGENTS.md", DIRECTIVE, HANDOFF))
    )


def review_schema(base):
    row = {
        "type": "object",
        "additionalProperties": False,
        "required": ["criterion", "accepted", "evidence"],
        "properties": {
            "criterion": {"type": "integer"},
            "accepted": {"type": "boolean"},
            "evidence": {"type": "string"},
        },
    }
    return {
        **base,
        "required": [*base["required"], "coverage"],
        "properties": {**base["properties"], "coverage": {"type": "array", "items": row}},
    }


def read_coverage(answer, task):
    if not isinstance(answer, dict):
        raise WorkflowError("조건별 독립 검토 결과가 필요합니다.")
    rows = answer.get("coverage")
    if (
        not isinstance(rows, list)
        or len(rows) != len(task["criteria"])
        or any(
            not isinstance(r, dict)
            or set(r) != {"criterion", "accepted", "evidence"}
            or type(r["criterion"]) is not int
            or type(r["accepted"]) is not bool
            or not isinstance(r["evidence"], str)
            or not r["evidence"].strip()
            or len(r["evidence"]) > 8000
            for r in rows
        )
        or {r["criterion"] for r in rows} != set(range(1, len(rows) + 1))
        or (answer.get("outcome") == "pass") != all(r["accepted"] for r in rows)
    ):
        raise WorkflowError("독립 검토의 조건별 근거 또는 최종 판정이 일치하지 않습니다.")
    return rows


def create(
    store,
    ident,
    profile,
    objective,
    criteria,
    groups=None,
    token_limit=None,
    *,
    instrument=None,
    milestone=None,
    recommendation_id=None,
):
    cfg = profiles.get(store, profile)["config"]
    root = Path(cfg["project"])
    if not all((root / p).is_file() for p in ("AGENTS.md", DIRECTIVE, HANDOFF)):
        raise WorkflowError("Katakuri 지침과 현재 인계 문서가 있는 프로젝트 프로필이 필요합니다.")
    if not cfg.get("allowed_paths") or any(Path(p) == Path(".") for p in cfg["allowed_paths"]):
        raise WorkflowError("목표가 수정할 파일·디렉터리를 프로필 allowed_paths로 지정하세요.")
    if cfg["reviewer"] == "none" or not profiles.check_plan(cfg, groups)["checks"]:
        raise WorkflowError("목표의 완료에는 실제 검사와 독립 리뷰가 필요합니다.")
    profiles.strings(criteria, "criteria")
    needs_plan = not criteria
    criteria = criteria or [objective]
    if not isinstance(objective, str) or not objective.strip():
        raise WorkflowError("최종 목표를 입력하세요.")
    if len(criteria) > 64 or len(set(criteria)) != len(criteria):
        raise WorkflowError("서로 다른 완료 조건 1~64개를 지정하세요.")
    if instrument is not None:
        profiles.identifier(instrument)
    if milestone is not None and milestone not in tuple(f"M{i}" for i in range(1, 7)):
        raise WorkflowError("M1~M6 중 해당하는 단계만 지정하세요.")
    if token_limit is not None:
        profiles.integer(token_limit, "max_reported_tokens", 1, 10**12)
    contract = {
        "version": 1,
        "needs_plan": needs_plan,
        "instrument": instrument,
        "milestone": milestone,
        "phase": "planning" if needs_plan else "working",
        "max_reported_tokens": token_limit,
        "items": [new_item(i) for i in range(1, len(criteria) + 1)],
    }
    refs = [p for p in ("AGENTS.md", DIRECTIVE, HANDOFF) if p not in cfg["refs"]]
    return create_from_profile(
        store,
        ident,
        profile,
        objective,
        criteria,
        refs=refs,
        scope=SCOPE,
        isolate=True,
        check_groups=groups,
        katakuri_goal=contract,
        recommendation_id=recommendation_id,
    )


def contract(task):
    if not task.get("katakuri_goal"):
        raise WorkflowError("katakuri-goal로 등록된 작업이 필요합니다.")
    return task["katakuri_goal"]


def prompt(task):
    g = contract(task)
    if g["needs_plan"]:
        return (
            "\nRead-only planning turn. Inspect the relevant project instructions and current evidence. "
            "Decompose the original objective into 1 to 12 concrete independently verifiable criteria, in Korean. "
            "Keep the exact objective, instrument and milestone scope. Separate offline work from external observations. "
            "Preserve existing acceptance requirements. Do not edit files or perform operations. Return only criteria.\n"
            + encode({"instrument": g["instrument"], "milestone": g["milestone"]})
        )
    items = [
        {
            "criterion": i["id"],
            "requirement": task["criteria"][i["id"] - 1],
            "state": i["state"],
            "summary": i["summary"],
            "next": i["next_action"],
        }
        for i in g["items"]
    ]
    return (
        "\nKatakuri goal mode. Follow the repository directive and consult the relevant current handoff section. "
        "Select the highest-value feasible pending criterion. Perform one bounded step. Reuse valid historical "
        "evidence scoped to the instrument and version. Select ready only when the criterion is ready for verification. "
        "Use progress if further development is feasible. Use waiting for an external input, market observation, "
        "or operator action. State the exact missing input. Never synthesize acceptance or execute financial actions. "
        "Do not change the milestone requirements. Give a concise Korean summary. Return the step schema.\n"
        + encode({"instrument": g["instrument"], "milestone": g["milestone"], "criteria": items})
    )


def validate_step(value, task):
    if (
        not isinstance(value, dict)
        or set(value) != set(STEP_SCHEMA["required"])
        or type(value["criterion"]) is not int
        or value["criterion"] not in [i["id"] for i in contract(task)["items"] if i["state"] == "pending"]
        or value["outcome"] not in ("ready", "progress", "waiting")
        or not all(
            isinstance(value[k], str) and 0 < len(value[k].strip()) <= 8000
            for k in ("summary", "next_action")
        )
    ):
        raise WorkflowError("목표 단계 응답의 조건·상태·설명이 유효하지 않습니다.")
    return value


def token_usage(task):
    usage = task.get("usage") or {}
    observations = usage.get("observations", [])
    total = known = 0
    for row in observations:
        t = row.get("tokens") or {}
        if not all(type(t.get(k)) is int for k in ("input_tokens", "output_tokens")):
            continue
        known += 1
        total += t["input_tokens"] + t["output_tokens"]
        if row.get("agent") == "claude":
            total += t.get("cache_read_input_tokens", 0) + t.get("cache_creation_input_tokens", 0)
    return total, max(0, usage.get("agent_calls", 0) - known)


def model_budget(task):
    ensure_call_budget(task)
    total, unknown = token_usage(task)
    limit = contract(task)["max_reported_tokens"]
    if limit is not None and (unknown or total >= limit):
        raise WorkflowError("보고 토큰 한도 도달 또는 사용량 미확인. 새 모델 호출을 중단했습니다.")


def status(task):
    g = contract(task)
    total, unknown = token_usage(task)
    return {
        "id": task["id"],
        "goal": task["goal"],
        "project": task["project"],
        "status": task["status"],
        "instrument": g["instrument"],
        "milestone": g["milestone"],
        "phase": g["phase"],
        "blocker": task.get("blocker"),
        "checkpoint": task["checkpoint"],
        "criteria": [
            {
                "id": i["id"],
                "text": task["criteria"][i["id"] - 1],
                "state": "accepted" if task["status"] == "completed" else i["state"],
                "summary": i["summary"],
                "next_action": i["next_action"],
            }
            for i in g["items"]
        ],
        "agent_calls": (task.get("usage") or {}).get("agent_calls", 0),
        "max_agent_calls": task["execution"]["max_agent_calls"],
        "max_seconds": task["execution"]["max_seconds"],
        "seconds_used": (task.get("usage") or {}).get("seconds", 0),
        "uncertain_seconds": (task.get("usage") or {}).get("uncertain_seconds", 0),
        "last_audit": g.get("audit"),
        "reported_tokens": total,
        "unreported_calls": unknown,
        "max_reported_tokens": g["max_reported_tokens"],
    }


def supply(store, ident, criterion, path):
    file = Path(path).expanduser()
    if not file.is_file() or file.suffix.lower() not in {".md", ".json", ".txt", ".csv"}:
        raise WorkflowError("정제된 UTF-8 근거 파일(.md, .json, .txt, .csv)이 필요합니다.")
    with file.open("rb") as source:
        content = source.read(MAX_INPUT_BYTES + 1)
    try:
        return supply_text(store, ident, criterion, content.decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise WorkflowError("근거는 UTF-8 텍스트여야 합니다.") from exc


MAX_INPUT_BYTES = 16 * 1024
MAX_GOAL_INPUT_BYTES = 96 * 1024


def evidence_packet(task):
    """Supply the same content to both isolated models without granting ledger access."""
    refs = {Path(ref).stem: Path(ref) for ref in task["refs"]}
    packet = []
    for item in contract(task)["items"]:
        for digest in item["inputs"]:
            path = refs.get(digest)
            if path is None or not path.is_file():
                raise WorkflowError("등록한 근거 파일이 없습니다.")
            with path.open("rb") as stream:
                data = stream.read(MAX_INPUT_BYTES + 1)
            if hashlib.sha256(data).hexdigest() != digest:
                raise WorkflowError("제출 당시와 근거 내용이 다릅니다.")
            packet.append({"criterion": item["id"], "sha256": digest, "evidence": data.decode("utf-8")})
    return packet


def supply_text(store, ident, criterion, content):
    if not isinstance(content, str) or not content.strip() or "\0" in content:
        raise WorkflowError("정제한 텍스트 근거를 입력하세요.")
    raw = content.encode("utf-8")
    if len(raw) > MAX_INPUT_BYTES:
        raise WorkflowError("근거 한 건은 UTF-8 16 KiB 이하여야 합니다.")
    task = store.get(ident)
    with store.lock(task["project"]):
        task = store.get(ident)
        items = contract(task)["items"]
        if (
            type(criterion) is not int
            or not 1 <= criterion <= len(items)
            or items[criterion - 1]["state"] != "waiting"
        ):
            raise WorkflowError("대기 중인 조건 번호가 필요합니다.")
        item = items[criterion - 1]
        digest = hashlib.sha256(raw).hexdigest()
        if digest in item["inputs"]:
            raise WorkflowError("이 조건에 이미 제출한 근거입니다.")
        if (
            len(raw) + sum(len(row["evidence"].encode("utf-8")) for row in evidence_packet(task))
            > MAX_GOAL_INPUT_BYTES
        ):
            raise WorkflowError("목표 전체 근거 한도는 UTF-8 96 KiB입니다.")
        saved = store.directory(ident) / "goal-inputs" / (digest + ".txt")
        atomic_write(saved, content)
        item["inputs"].append(digest)
        if str(saved) not in task["refs"]:
            task["refs"].append(str(saved))
        item.update(state="pending", seen=[], next_action="새 근거를 실제 조건에 대조한다.")
        task["katakuri_goal"]["phase"] = "working"
        task.update(status="ready", blocker=None, verification=None, review=None)
        task["checkpoint"] = {
            "at": now(),
            "summary": f"조건 {criterion} 근거 추가",
            "next": item["next_action"],
        }
        store.save(task, "katakuri_input_received", {"criterion": criterion, "sha256": digest})
        return status(task)


def park(items, reason, fingerprint, ids=None):
    for item in items:
        if item["state"] == "proposed" and (ids is None or item["id"] in ids):
            item.update(state="waiting", next_action=reason, retry_after_change=fingerprint)


def plan_goal(store, task, timeout, fd, current, refs):
    from .runner import invoke

    g = contract(task)
    g["phase"] = "planning"
    model_budget(task)
    plan = invoke(
        store,
        task,
        task["owner"],
        "implement",
        timeout,
        fd,
        model=task["execution"]["models"].get("implement"),
    )
    if (
        snapshot(task["project"])["fingerprint"] != current["fingerprint"]
        or reference_fingerprint(task) != refs
    ):
        raise WorkflowError("계획을 작성하는 동안 작업 파일이 변경됐습니다.")
    task["criteria"] = plan["criteria"]
    g.update(needs_plan=False, items=[new_item(n) for n in range(1, len(task["criteria"]) + 1)])
    store.save(task, "katakuri_plan_created", plan)


def advance_criterion(store, task, timeout, fd, key):
    from .runner import invoke

    g = contract(task)
    items = g["items"]
    g["phase"] = "working"
    model_budget(task)
    step = invoke(
        store,
        task,
        task["owner"],
        "implement",
        timeout,
        fd,
        feedback=g.get("feedback"),
        model=task["execution"]["models"].get("implement"),
    )
    scope_check(task)
    item = items[step["criterion"] - 1]
    after = snapshot(task["project"])["fingerprint"] + ":" + (reference_fingerprint(task) or "missing")
    item["summary"], item["next_action"] = step["summary"], step["next_action"]
    if step["outcome"] == "progress" and (after == key or after in item["seen"]):
        item.update(
            state="waiting",
            retry_after_change=after,
            next_action="동일 입력에서 진척 없음. " + step["next_action"],
        )
    else:
        item.pop("retry_after_change", None)
        item["state"] = {"progress": "pending", "ready": "proposed", "waiting": "waiting"}[step["outcome"]]
    item["seen"].append(key)
    task["implementation"] = step
    task["checkpoint"] = {
        "at": now(),
        "summary": item["summary"],
        "next": item["next_action"],
    }
    store.save(task, "katakuri_step", step)


def run(store, ident, timeout):
    from .runner import invoke, record_review, run_checks
    from .recovery import AgentFailure, record_failure
    from .scheduling import prepare_dependencies

    task = store.get(ident)
    contract(task)
    with store.lock(task["project"]) as tree, reservation(store, task) as lease:
        fd = (tree, lease)
        task = store.get(ident)
        g = contract(task)
        if task["status"] == "completed":
            raise WorkflowError("이미 완료한 목표입니다. status에서 근거를 확인하세요.")
        if task.get("release") or task["reviewer"] == "none":
            raise WorkflowError("목표 모드는 독립 리뷰가 있는 로컬 개발 작업만 실행합니다.")
        try:
            ensure_not_held(store, task)
            reconcile_pending(store, task)
            prepare_dependencies(store, task)
            preflight(store, task)
            prepare(store, task, timeout, fd)
            while True:
                ensure_not_held(store, task)
                items = g["items"]
                current = snapshot(task["project"])
                refs = reference_fingerprint(task)
                if refs is None:
                    raise WorkflowError("목표의 참조 자료를 확인할 수 없습니다.")
                key = current["fingerprint"] + ":" + refs
                for item in items:
                    stalled_at = item.get("retry_after_change")
                    if item["state"] == "waiting" and stalled_at and stalled_at != key:
                        item.update(state="pending", retry_after_change=None)
                pending = [i for i in items if i["state"] == "pending"]
                parked = [i for i in items if i["state"] == "waiting"]
                if not pending and len(parked) == len(items):
                    g["phase"] = "waiting"
                    raise WaitingForInput("외부 근거 대기. status에서 조건별 재개 요건을 확인하세요.")
                if g["needs_plan"]:
                    plan_goal(store, task, timeout, fd, current, refs)
                    continue
                if pending:
                    advance_criterion(store, task, timeout, fd, key)
                    continue
                if (task.get("project_config") or {}).get("documents"):
                    from .closure import sync_documents

                    if sync_documents(store, task):
                        continue
                verification = task.get("verification") or {}
                if not (
                    verification.get("passed")
                    and verification.get("fingerprint") == current["fingerprint"]
                    and references_current(task, verification, refs)
                ):
                    g["phase"] = "checking"
                    if g.get("failed_check_input") == key:
                        park(items, "같은 코드와 근거에서 실패한 검사를 반복하지 않습니다.", key)
                        g["phase"] = "waiting"
                        raise WaitingForInput("같은 코드와 근거에서 실패한 검사를 반복하지 않습니다.")
                    remaining(task)
                    if not run_checks(store, task, timeout, fd):
                        g["failed_check_input"] = key
                        g["feedback"] = task["verification"]
                        for item in items:
                            if item["state"] == "proposed":
                                item["state"] = "pending"
                        store.save(task, "katakuri_repair_needed", {"source": "checks"})
                        continue
                if parked:
                    g["phase"] = "waiting"
                    raise WaitingForInput(
                        "진행 가능한 조건을 검사했습니다. 남은 조건의 외부 근거를 기다립니다."
                    )
                g["phase"] = "reviewing"
                current = snapshot(task["project"])
                refs = reference_fingerprint(task)
                if g.get("rejected_review_input") == current["fingerprint"] + ":" + str(refs):
                    park(
                        items,
                        "같은 입력에서 거절된 리뷰를 반복하지 않습니다.",
                        key,
                        {r["criterion"] for r in g["audit"]["rows"] if not r["accepted"]},
                    )
                    g["phase"] = "waiting"
                    raise WaitingForInput("같은 입력에서 거절된 리뷰를 반복하지 않습니다.")
                prior = task.get("review") or {}
                audit = g.get("audit") or {}
                if not (
                    audit.get("reviewer") == task["reviewer"]
                    and audit.get("fingerprint") == current["fingerprint"]
                    and references_current(task, audit, refs)
                    and all(r["accepted"] for r in audit.get("rows", []))
                    and prior.get("verdict") == "pass"
                    and prior.get("fingerprint") == current["fingerprint"]
                    and prior.get("reviewer") == task["reviewer"]
                    and references_current(task, prior, refs)
                ):
                    model_budget(task)
                    answer = invoke(
                        store,
                        task,
                        task["reviewer"],
                        "review",
                        timeout,
                        fd,
                        feedback={"goal_criteria": status(task)["criteria"]},
                        model=task["execution"]["models"].get("review"),
                    )
                    record_review(store, task, answer, current["fingerprint"], task["reviewer"], refs)
                    if answer["outcome"] != "pass":
                        g["rejected_review_input"] = current["fingerprint"] + ":" + str(refs)
                        g["feedback"] = answer
                        failed = {
                            r["criterion"]: r["evidence"] for r in g["audit"]["rows"] if not r["accepted"]
                        }
                        for item in items:
                            if item["id"] in failed:
                                item["state"] = "waiting" if answer["outcome"] == "blocked" else "pending"
                                item["next_action"] = failed[item["id"]]
                        store.save(task, "katakuri_review_returned", {"outcome": answer["outcome"]})
                        continue
                g["phase"] = "complete"
                return complete(store, task)
        except (WorkflowError, OSError, KeyboardInterrupt) as exc:
            if isinstance(exc, AgentFailure):
                record_failure(store, task, exc, "review" if g["phase"] == "reviewing" else "implement")
            task.update(status="blocked", blocker=str(exc) or "사용자 중단")
            if g["phase"] != "waiting":
                g["phase"] = "stopped"
            store.save(task, "katakuri_stopped", {"reason": task["blocker"]})
            raise


def budget(store, ident, calls, seconds, tokens, clear_token_limit=False):
    if type(clear_token_limit) is not bool or (clear_token_limit and tokens is not None):
        raise WorkflowError("토큰 한도 변경 또는 해제 중 하나를 선택하세요.")
    task = store.get(ident)
    with store.lock(task["project"]):
        task = store.get(ident)
        g = contract(task)
        if task["status"] == "completed" or (
            not clear_token_limit and all(v is None for v in (calls, seconds, tokens))
        ):
            raise WorkflowError("진행 중인 목표와 변경할 예산이 필요합니다.")
        cfg = dict(task["execution"])
        if calls is not None:
            cfg["max_agent_calls"] = calls
        if seconds is not None:
            cfg["max_seconds"] = seconds
        task["execution"] = profiles.execution_config(cfg)
        if clear_token_limit:
            g["max_reported_tokens"] = None
        if tokens is not None:
            g["max_reported_tokens"] = profiles.integer(tokens, "max_reported_tokens", 1, 10**12)
        store.save(task, "katakuri_budget_changed", {"calls": calls, "seconds": seconds, "tokens": tokens})
        return status(task)
