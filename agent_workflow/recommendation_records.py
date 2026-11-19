"""Opt-in, ledger-local recommendation receipts, task feedback, and observed checks."""
from __future__ import annotations

from collections import defaultdict
from contextlib import closing
import hashlib
import json
from pathlib import Path
from statistics import median
import uuid

from .connections import project_key, state_home
from .core import Store, WorkflowError, now, project_root
from .profiles import check_plan, identifier


def _init(db):
    db.execute("""CREATE TABLE IF NOT EXISTS check_recommendations (
        id TEXT PRIMARY KEY, profile TEXT NOT NULL, profile_digest TEXT NOT NULL,
        input_digest TEXT NOT NULL, data TEXT NOT NULL)""")
    db.execute("""CREATE INDEX IF NOT EXISTS check_recommendation_profile
                  ON check_recommendations(profile_digest, profile)""")
    db.execute("""CREATE TABLE IF NOT EXISTS check_recommendation_feedback (
        task_id TEXT PRIMARY KEY, recommendation_id TEXT NOT NULL, expected TEXT NOT NULL, updated_at TEXT NOT NULL)""")
    db.execute("CREATE INDEX IF NOT EXISTS check_feedback_receipt ON check_recommendation_feedback(recommendation_id)")


def _has(db):
    return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='check_recommendations'").fetchone() is not None


def save_recommendation(home, report, profile_record, criteria, root, base, areas, review_ms):
    """Store only the routing/check-selection evidence; argv, source and prompt stay out."""
    if report["status"] != "complete" or report["profile_digest"] != profile_record["digest"]:
        raise WorkflowError("현재 프로필에서 완료된 추천만 기록할 수 있습니다.")
    rid = "REC-" + uuid.uuid4().hex
    receipt = {
        "id": rid, "created_at": now(), "profile": report["profile"],
        "profile_digest": report["profile_digest"], "input_digest": report["input_digest"],
        "status": "complete", "providers": report["providers"],
        "primary": (report["primary"] or {}).get("groups", []),
        "reviewer": report["review"]["groups"] if report["review"] else None,
        "suggested_groups": report["suggested_groups"], "available_groups": report["available_groups"],
        "areas": sorted(set(areas)), "truncated": report["change_summary"]["truncated"],
        "review_ms": review_ms, "worktree": str(project_root(root)), "base": base,
    }
    decision = report.get("routing_decision") or {}
    if decision:
        receipt["routing"] = {"basis": decision.get("basis"), "reviewer": decision.get("reviewer"),
                              "labeled_inputs": (decision.get("evidence") or {}).get("labeled_inputs")}
    with closing(Store(home)) as store, store.db:
        store.db.execute("BEGIN IMMEDIATE")
        _init(store.db)
        store.db.execute("INSERT INTO check_recommendations VALUES (?,?,?,?,?)",
                         (rid, receipt["profile"], receipt["profile_digest"], receipt["input_digest"], json.dumps(receipt)))
    return rid


def _receipt(db, rid):
    identifier(rid)
    row = db.execute("SELECT data FROM check_recommendations WHERE id=?", (rid,)).fetchone() if _has(db) else None
    if row is None:
        raise WorkflowError("이 원장에 저장된 추천이 없습니다. 추천을 기록한 --home과 등록 원장을 확인하세요.")
    return json.loads(row[0])


def _public(receipt):
    return {k: v for k, v in receipt.items() if k not in ("worktree", "base")}


def show(home, recommendation_id):
    with closing(Store(home, read_only=True)) as store:
        return _public(_receipt(store.db, recommendation_id))


def resolve(store, rid, profile_record, expected_project):
    """Recover the recorded worktree and check its saved project/profile binding."""
    receipt = _receipt(store.db, rid)
    if (receipt["profile"] != profile_record["name"] or receipt["profile_digest"] != profile_record["digest"]):
        raise WorkflowError("추천 당시 프로필과 현재 프로필이 다릅니다. 추천을 다시 받으세요.")
    root = Path(receipt["worktree"])
    cfg = profile_record["config"]
    if project_key(root) != project_key(cfg["project"]) or (expected_project is not None and root != project_root(expected_project)):
        raise WorkflowError("추천에 기록된 작업 트리와 대상 프로젝트가 다릅니다.")
    return receipt, root


def verify_input(receipt, config, criteria, root):
    from .check_advisor import build_packet
    packet = build_packet(config, criteria, root, receipt["base"])
    if hashlib.sha256(json.dumps(packet, sort_keys=True).encode()).hexdigest() != receipt["input_digest"]:
        raise WorkflowError("추천 이후 변경 내용 또는 완료 조건이 바뀌었습니다. 최신 입력으로 다시 추천받으세요.")


def feedback(home, task_id, expected_groups):
    if not isinstance(expected_groups, (list, tuple)) or any(not isinstance(x, str) for x in expected_groups):
        raise WorkflowError("사람이 확인한 묶음 이름 목록이 필요합니다.")
    if not (state_home(home) / "state.sqlite3").is_file():
        raise WorkflowError("피드백을 기록할 작업 원장이 없습니다.")
    with closing(Store(home)) as store, store.db:
        task = store.get(task_id)
        rid = (task.get("profile") or {}).get("recommendation_id")
        if not rid:
            raise WorkflowError("추천 기록이 연결된 작업이 필요합니다.")
        receipt = _receipt(store.db, rid)
        if set(expected_groups) - set(receipt["available_groups"]):
            raise WorkflowError("추천 당시 등록된 검사 묶음에서 선택하세요.")
        expected = list(dict.fromkeys(expected_groups))
        store.db.execute("""INSERT INTO check_recommendation_feedback VALUES (?,?,?,?)
                            ON CONFLICT(task_id) DO UPDATE SET expected=excluded.expected,updated_at=excluded.updated_at""",
                         (task_id, rid, json.dumps(expected), now()))
        return {"task_id": task_id, "recommendation_id": rid, "expected_groups": expected}


def _profile_results(config, adopted, results):
    # Every group reuses the first matching slot in the frozen profile plan. Later
    # check-add entries can repeat the argv, but those executions are independent.
    planned = check_plan(config, adopted)["checks"]
    observed = {}
    for argv, result in zip(planned, results):
        if argv == result.get("argv"):
            observed.setdefault(tuple(argv), result)
    return observed


def _observed_groups(config, adopted, observed):
    """Per-group coverage of latest verification. Shared commands appear in both group totals."""
    output = {}
    for group in adopted:
        commands = {tuple(argv) for argv in config.get("check_groups", {}).get(group, [])}
        matching = [observed[cmd] for cmd in commands if cmd in observed]
        passed = sum(r.get("exit_code") == 0 and not r.get("timed_out") for r in matching)
        durations = [r.get("seconds") for r in matching]
        output[group] = {"planned_count": len(commands), "executed_count": len(matching), "passed_count": passed,
                         "failed_count": len(matching) - passed,
                         "seconds": round(sum(durations), 3) if matching and all(isinstance(n, (int, float)) for n in durations) else None}
    return output


def task_summary(store, task):
    profile = task.get("profile") or {}
    rid = profile.get("recommendation_id")
    if not rid:
        return None
    receipt = _receipt(store.db, rid)
    label = store.db.execute("SELECT expected FROM check_recommendation_feedback WHERE task_id=? AND recommendation_id=?",
                             (task["id"], rid)).fetchone()
    expected = json.loads(label[0]) if label is not None else None
    adopted = profile.get("check_groups", [])
    verification = task.get("verification")
    if not isinstance(verification, dict) or not isinstance(verification.get("results"), list):
        # check-add and a newly started attempt can invalidate current evidence.
        # A prior completed receipt still measures work performed for this task.
        row = store.db.execute("""SELECT data FROM events WHERE task_id=? AND kind='checks_finished'
                                ORDER BY seq DESC LIMIT 1""", (task["id"],)).fetchone()
        verification = json.loads(row[0]) if row else {}
    results = verification.get("results") or []
    cfg = task["project_config"]
    observed = _profile_results(cfg, adopted, results)
    # Marginal work measured in the latest verification, excluding shared/base checks.
    unnecessary_seconds = None
    if expected is not None and receipt["reviewer"] is not None and results:
        extra = set(adopted).intersection(receipt["reviewer"]) - set(receipt["primary"]) - set(expected)
        groups = cfg.get("check_groups", {})
        other_commands = {tuple(argv) for name in set(adopted) - extra for argv in groups[name]}
        other_commands.update(tuple(argv) for argv in cfg.get("checks", []))
        other_commands.update(tuple(task["checks"][i]) for i in profile.get("individual_check_indexes", []))
        extra_commands = {tuple(argv) for name in extra for argv in groups[name]} - other_commands
        durations = [observed[cmd].get("seconds") for cmd in extra_commands if cmd in observed]
        if extra_commands <= observed.keys() and all(type(n) in (float, int) for n in durations):
            unnecessary_seconds = round(sum(durations), 3)
    return {"task_id": task["id"], "recommendation_id": rid, "profile": receipt["profile"],
            "primary": receipt["primary"], "reviewer": receipt["reviewer"], "suggested_groups": receipt["suggested_groups"],
            "available_groups": receipt["available_groups"], "adopted_groups": adopted, "expected_groups": expected,
            "checks_by_group": _observed_groups(cfg, adopted, observed), "review_ms": receipt["review_ms"],
            "verification_at": verification.get("at"),
            "unnecessary_review_check_seconds": unnecessary_seconds}


def _quality(records, tasks):
    """Quality counts inputs; executed time counts real labeled task verifications."""
    by_receipt = defaultdict(list)
    labels = defaultdict(set)
    for task in tasks:
        if task["expected_groups"] is not None:
            by_receipt[task["recommendation_id"]].append(task)
    for receipt in records:
        identity = (receipt["profile"], receipt["profile_digest"], receipt["input_digest"])
        labels[identity].update(tuple(sorted(t["expected_groups"])) for t in by_receipt[receipt["id"]])

    seen = set()
    conflicts = set()
    rescued = extra = paired_tasks = 0
    latencies, observed_seconds = [], []
    for receipt in reversed(records):  # records are newest first; earliest labeled pair represents an input
        linked = by_receipt[receipt["id"]]
        if not linked or receipt["reviewer"] is None:
            continue
        paired_tasks += len(linked)
        identity = (receipt["profile"], receipt["profile_digest"], receipt["input_digest"])
        if len(labels[identity]) != 1:
            conflicts.add(identity)
            continue
        observed_seconds.extend(t["unnecessary_review_check_seconds"] for t in linked
                                if t["unnecessary_review_check_seconds"] is not None)
        pair = (*identity, tuple(receipt["providers"]))
        if pair in seen:
            continue
        seen.add(pair)
        expected = set(linked[0]["expected_groups"])
        added = set(receipt["suggested_groups"]) - set(receipt["primary"])
        rescued += len(added & expected)
        extra += len(added - expected)
        if receipt["review_ms"] is not None:
            latencies.append(receipt["review_ms"])
    return {"paired_labeled_tasks": paired_tasks, "distinct_paired_inputs": len(seen),
            "conflicting_inputs": len(conflicts), "rescued_groups": rescued, "extra_groups": extra,
            "review_latency_samples": len(latencies), "median_review_ms": median(latencies) if latencies else None,
            "observed_timing_tasks": len(observed_seconds),
            "unnecessary_review_check_seconds": round(sum(observed_seconds), 3) if observed_seconds else None}


def summary(home, profile=None):
    result = {"profile": profile, "recommendations": 0, "linked_recommendations": 0, "linked_tasks": 0,
              "labeled_tasks": 0, "tasks": [], "records": [], "quality": _quality([], [])}
    if not (state_home(home) / "state.sqlite3").is_file():
        return result
    with closing(Store(home, read_only=True)) as store:
        if not _has(store.db):
            return result
        store.db.execute("BEGIN")
        query = "SELECT data FROM check_recommendations"
        if profile is not None:
            query += " WHERE profile=?"
        records = [_public(json.loads(row[0])) for row in store.db.execute(query + " ORDER BY rowid DESC", (profile,) if profile is not None else ())]
        result["records"] = records
        ids = {r["id"] for r in records}
        query = """SELECT data FROM tasks WHERE json_extract(data, '$.profile.recommendation_id') IS NOT NULL"""
        if profile is not None:
            query += " AND json_extract(data, '$.profile.name')=?"
        for row in store.db.execute(query + " ORDER BY rowid DESC", (profile,) if profile is not None else ()):
            task = json.loads(row[0])
            if task["profile"]["recommendation_id"] in ids:
                result["tasks"].append(task_summary(store, task))
    tasks = result["tasks"]
    result.update(recommendations=len(ids), linked_recommendations=len({t["recommendation_id"] for t in tasks}),
                  linked_tasks=len(tasks), labeled_tasks=sum(t["expected_groups"] is not None for t in tasks),
                  quality=_quality(records, tasks))
    return result


def routing_samples(home, profile_digest, areas, provider, reviewer, *, profile=None):
    """One paired outcome per distinct labeled input. Conflicting human labels are inconclusive."""
    if not (state_home(home) / "state.sqlite3").is_file():
        return []
    with closing(Store(home, read_only=True)) as store:
        if not _has(store.db):
            return []
        query = """SELECT r.data, f.expected FROM check_recommendations r
                   JOIN check_recommendation_feedback f ON r.id=f.recommendation_id
                   WHERE r.profile_digest=?"""
        args = [profile_digest]
        if profile is not None:
            query += " AND r.profile=?"
            args.append(profile)
        rows = store.db.execute(query + " ORDER BY r.rowid, f.task_id", args)
        observed = {}
        labels = defaultdict(set)
        area_key = sorted(set(areas))
        for row in rows:
            r = json.loads(row[0])
            if r["truncated"] or r["areas"] != area_key:
                continue
            expected = json.loads(row[1])
            key = r["input_digest"]
            labels[key].add(tuple(sorted(expected)))
            if r["status"] != "complete" or r["providers"] != [provider, reviewer]:
                continue
            # First paired observation represents this input even after reusing or repeating it.
            observed.setdefault(key, {"input_digest": key, "primary": r["primary"], "combined": r["suggested_groups"],
                                      "expected": expected, "review_ms": r["review_ms"]})
    return [sample for key, sample in observed.items() if len(labels[key]) == 1]
