"""A read-only resume view of one ledger revision and one caller-supplied observation."""
from __future__ import annotations

from . import goal_policy
from . import criterion_checks
from .local_recovery import needs_check_context


def _evidence(kind, description, source, **identity):
    return {"kind": kind, "description": description, "source": source,
            **{key: value for key, value in identity.items() if value is not None}}


def _release_receipts(task):
    release = task.get("release") or {}
    config = release.get("config") or {}
    rows = []
    candidate = release.get("commit")
    if candidate:
        rows.append(_evidence("release", f"원장에 기록된 릴리스 후보 {candidate}", "/release/commit"))
    if release.get("pr") and config.get("repo"):
        rows.append(_evidence("release", f"원장에서 확인한 {config['repo']} PR #{release['pr']}",
                              "/release/pr", revision=candidate))
    merged = release.get("merge_commit")
    if merged:
        rows.append(_evidence("release", f"원장에 기록된 병합 리비전 {merged}", "/release/merge_commit"))
    observation = release.get("deployment_observation") or {}
    if observation.get("observed"):
        rows.append(_evidence("release", f"대상 조회 당시 적용 버전 {observation['observed']}",
                              "/release/deployment_observation", at=observation.get("at")))
    health = release.get("health_result") or {}
    if (merged and release.get("stage") == "deployed" and merged == release.get("deployed_revision")
            and release.get("deployment_state") == "applied" and health.get("exit_code") == 0
            and health.get("timed_out") is False):
        rows.append(_evidence("release", f"배포 당시 상태 검사 통과 · {merged}",
                              "/release/health_result", at=release.get("deployed_at")))
    return rows


def _goal_coverage(task, flags, check_evidence, review_current):
    """An accepted row applies only to a proposed item on this audited input."""
    goal = goal_policy.state(task) or {}
    audit = goal.get("audit") or {}
    review = task.get("review") or {}
    if (not review_current or not audit or audit.get("reviewer") != task.get("reviewer")
            or audit.get("fingerprint") != review.get("fingerprint")
            or (task.get("refs") and audit.get("refs_fingerprint") != flags.get("refs_fingerprint"))):
        return {}
    rows = audit.get("rows") or []
    criteria = task.get("criteria") or []
    if (len(rows) != len(criteria)
            or any(not isinstance(row, dict) or type(row.get("criterion")) is not int for row in rows)
            or {row["criterion"] for row in rows} != set(range(1, len(criteria) + 1))):
        return {}
    accepted = {}
    proposed = {i["id"] for i in goal.get("items", []) if i.get("state") == "proposed"}
    for index, row in enumerate(rows):
        number = row["criterion"]
        if number not in proposed or row.get("accepted") is not True or not row.get("evidence"):
            continue
        check = check_evidence.get(number)
        if check and (check["state"] != "passed" or check.get("result_id") != row.get("check_result_id")):
            continue
        accepted[number] = _evidence("criterion_review", f"조건 {number} 독립 검토 수락",
                                     f"/{goal_policy.state_key(task)}/audit/rows/{index}", criterion=number,
                                     result_id=row.get("check_result_id"))
    return accepted


def needs_fingerprint(task):
    return bool(task.get("verification") or task.get("review") or task.get("local_step")
                or goal_policy.state(task) or task.get("release") or task.get("status") == "completed"
                or task.get("refs") or (task.get("project_config") or {}).get("forbidden_paths"))


def build_packet(task, current, flags, recovery, *, ledger=None):
    """Project existing receipts. No files, Git, providers or remote services are read here."""
    current = current or {}
    flags = flags or {}
    goal = goal_policy.state(task) or {}
    cfg = task.get("project_config") or {}
    verify = task.get("verification") or {}
    review = task.get("review") or {}
    criteria = task.get("criteria") or []
    items = {i["id"]: i for i in goal.get("items", []) if isinstance(i, dict) and "id" in i}
    needs_full = needs_fingerprint(task)
    observed = bool((current.get("fingerprint") or (not needs_full and current.get("head")))
                    and not current.get("archived") and not current.get("error"))
    ver_current = bool(observed and type(verify.get("passed")) is bool
                       and verify.get("fingerprint") and flags.get("verification_is_current")
                       and (flags.get("check_context_is_current") is True or not needs_check_context(task))
                       and not any(verify.get(key) for key in ("code_changed_during_checks",
                                   "references_changed_during_checks", "check_context_changed_during_checks")))
    verified = ver_current and verify.get("passed") is True
    reviewer = task.get("reviewer")
    review_current = bool(verified and review.get("fingerprint") and flags.get("review_is_current")
                          and review.get("verdict", review.get("outcome")) in {"pass", "blocked", "changes_requested"}
                          and reviewer and reviewer != "none" and reviewer == review.get("reviewer")
                          and reviewer != task.get("owner"))
    verdict = review.get("verdict", review.get("outcome")) if review_current else None
    evidence = []
    unknown = []
    def note(text, source=None):
        if text and source and len(text) > 320:
            text = f"{text[:320]}… (원문 {source})"
        if text and text not in unknown:
            unknown.append(text)

    if not observed:
        note("현재 작업 트리의 코드·참조 지문을 확인할 수 없습니다.")
    if flags.get("protected_paths_are_current") is False:
        note("보호 경로의 현재 상태가 등록된 기준과 다릅니다.")
    if observed and task.get("refs") and not flags.get("refs_fingerprint"):
        note("등록된 참조 문서의 현재 내용을 확인할 수 없습니다.")
    if verify and not ver_current:
        recorded_fp, live_fp = verify.get("fingerprint"), current.get("fingerprint")
        drift = (f" (검사 당시 코드 {recorded_fp[:12]} → 현재 코드 {live_fp[:12]})"
                 if observed and isinstance(recorded_fp, str) and isinstance(live_fp, str)
                 and recorded_fp and live_fp and recorded_fp != live_fp else "")
        note("이전 검사 영수증의 코드·참조·검사 환경을 현재 입력과 대조해 재검사해야 합니다." + drift)
    if review and not review_current:
        note("현재 검사 입력과 지정된 검토자에 해당하는 독립 리뷰 근거를 확인해야 합니다.")
    if ver_current:
        evidence.append(_evidence("verification", "등록 검사 명령 통과" if verified else "마지막 등록 검사 실패",
                                  "/verification", at=verify.get("at"), fingerprint=verify["fingerprint"],
                                  refs_fingerprint=verify.get("refs_fingerprint"),
                                  check_context=verify.get("check_context")))
    # Unbound JUnit inputs (e.g. an ignored bundle) must be observed again on
    # re-entry. A passing process receipt alone cannot carry that behavior forward.
    try:
        observations = criterion_checks.observations(task)
    except (KeyError, IndexError, ValueError, TypeError):
        # Older/partial receipts cannot prove the declared mapping. Preserve the
        # criterion and route it back to verification without inventing a receipt.
        rules = cfg.get("criterion_checks") or {}
        observations = {n: {"state": "missing"} for n, text in enumerate(criteria, 1) if text in rules}
    unbound_environment = (not cfg.get("check_inputs") and any("testcase" in e for e in observations.values()))
    slots = {row.get("result_id"): index for index, row in enumerate(verify.get("results") or [])}
    unmet = []
    for number, check in observations.items():
        if unbound_environment and check.get("testcase"):
            unmet.append(number)
            note(f"조건 {number}의 시험 환경 입력 지문이 없어 재개 시 해당 검사를 다시 확인해야 합니다.")
        elif not ver_current or check["state"] != "passed":
            unmet.append(number)
            note(f"조건 {number}의 필수 검사 근거를 확인해야 합니다 ({check['state'] if ver_current else '미확인'}).")
        elif check.get("result_id") in slots:
            evidence.append(_evidence("criterion_check", f"조건 {number}에 연결된 검사 통과",
                                      f"/verification/results/{slots[check['result_id']]}", criterion=number,
                                      result_id=check["result_id"], at=check.get("at")))
    accepted = _goal_coverage(task, flags, observations, review_current)
    for number in unmet:
        accepted.pop(number, None)
    if goal and verdict == "pass" and len(accepted) != len(criteria):
        note("조건별 검토 수락 기록과 현재 검사 영수증을 다시 대조해야 합니다.")
        verdict = None
    if review_current and verdict and not unmet:
        evidence.append(_evidence("review", f"{reviewer} 독립 검토 · {verdict}",
                                  "/review", at=review.get("at"), fingerprint=review["fingerprint"]))
    evidence.extend(accepted.values())
    completed = (criteria and task.get("status") == "completed" and task.get("completed_fingerprint")
                 and verified and flags.get("completion_is_current") and (reviewer == "none" or verdict == "pass")
                 and (not task.get("dependencies") or task.get("dependency_receipts"))
                 and (not goal or (goal.get("phase") == "complete" and len(accepted) == len(criteria))))
    if completed:
        evidence.append(_evidence("completion", "현재 입력과 대조된 로컬 완료 영수증", "/completed_fingerprint",
                                  fingerprint=task["completed_fingerprint"]))
    evidence.extend(_release_receipts(task))
    if observed and recovery and recovery.get("stage") == "implementation":
        for effect in recovery.get("confirmed_effects", []):
            evidence.append(_evidence("worktree", effect, "/local_step", fingerprint=current["fingerprint"]))

    remaining = []
    for n, text in enumerate(criteria, 1):
        if completed or n in accepted:
            continue
        item = items.get(n, {})
        state = item.get("state", "pending")
        if state not in {"proposed", "waiting", "pending"}:
            state = "pending"
        if item.get("inputs"):
            note(f"조건 {n}에 제출된 자료 {len(item['inputs'])}건의 충족 여부를 검토하세요.")
        if state == "waiting":
            note(f"조건 {n} 대기 · " + (item.get("next_action") or "재개에 필요한 근거를 확인하세요."),
                 f"/{goal_policy.state_key(task)}/items")
        remaining.append({"id": n, "text": text, "state": state, "source": f"/criteria/{n - 1}"})

    if task.get("blocker"):
        note(task["blocker"], "/blocker")
    if recovery and recovery.get("reason") and recovery.get("action") not in {"complete", "finish", "review"}:
        note(recovery["reason"])
    action, text = _next(task, observed, flags, recovery, verified, verdict, bool(completed), unmet, remaining)
    if task.get("hold"):
        note(task["hold"].get("reason"))
    return {"version": 1, "task_id": task["id"], "revision": task.get("revision"),
            **({"ledger": str(ledger)} if ledger is not None else {}),
            "status": task.get("status"), "goal": task.get("goal"), "scope": task.get("scope"),
            "project": task.get("project"), "owner": task.get("owner"), "reviewer": reviewer,
            "limits": {"allowed_paths": cfg.get("allowed_paths", []), "forbidden_paths": cfg.get("forbidden_paths", [])},
            "refs": task.get("refs") or [], "head": current.get("head") if observed else None,
            "fingerprint": current.get("fingerprint") if observed and needs_full else None,
            "remaining_criteria": remaining, "valid_evidence": evidence, "uncertainties": unknown,
            "next_action": {"action": action, "text": text,
                            **({"source": "/checkpoint/next"} if action == "checkpoint" else {})}}


def _next(task, observed, flags, recovery, verified, verdict, completed, unmet, remaining):
    goal = goal_policy.state(task) or {}
    status = task.get("status")
    if task.get("hold"):
        return "held", "원장에 기록된 수동 보류 사유를 확인하세요."
    # Remote observations are scoped to a specific target. Looking up a lost
    # acknowledgement remains appropriate even if local code cannot be reused.
    if recovery and recovery.get("action") in {"reconcile_pr", "reconcile_merge", "observe_deployment", "confirm_deployment"}:
        return recovery["action"], recovery["next_action"]
    if not observed:
        return "inspect", "작업 트리와 브랜치를 확인한 뒤 원장에서 재개 정보를 다시 읽으세요."
    if flags.get("protected_paths_are_current") is False:
        return "inspect", "보호 경로의 변경과 허용 범위를 먼저 대조하세요."
    if task.get("refs") and not flags.get("refs_fingerprint"):
        return "inspect", "등록한 참조 문서의 위치와 접근 가능 여부를 확인하세요."
    if ((task.get("release") or {}).get("stage") == "preparing" and recovery
            and recovery.get("action") == "inspect_release"):
        # Staging the verified changes itself changes the local fingerprint.
        return recovery["action"], recovery["next_action"]
    if status == "completed" and not completed:
        return "inspect_completion", "완료 당시 근거와 현재 입력을 대조하세요. 추가 검증은 새 작업으로 등록하세요."
    if goal.get("phase") == "waiting":
        if any(i["state"] == "waiting" for i in remaining):
            return "wait_for_input", "대기 조건의 재개 요건과 제출할 근거를 확인하세요."
        if unmet:
            return "recheck", "필수 시험의 환경과 보고서를 준비한 뒤 해당 목표를 다시 검사하세요."
    if recovery:
        action = recovery.get("action")
        if action in {"review", "finish"} and unmet:
            return "recheck", "현재 입력에서 조건별 필수 검사 근거를 다시 확인하세요."
        if action in {"review", "finish"} and not verified:
            return "recheck", "현재 입력에서 등록 검사를 다시 실행하세요."
        if action == "finish" and task.get("reviewer") != "none" and verdict != "pass":
            return "review", "지정된 독립 검토자에게 현재 변경과 검사 근거를 전달하세요."
        if action and recovery.get("next_action"):
            return action, recovery["next_action"]
    if completed:
        if task.get("release") and task["release"].get("stage") == "configured":
            return "inspect_release", "등록된 릴리스 대상과 실행 범위를 확인하세요."
        return "complete", "완료 영수증을 확인하고 다음 작업을 선택하세요."
    if goal.get("needs_plan"):
        return "plan", "원래 목표와 등록된 참조에서 검증 가능한 완료 조건을 작성하세요."
    if verdict == "changes_requested" and task.get("blocker") in (None, (task.get("review") or {}).get("summary")):
        return "repair", "현재 독립 리뷰의 차단 지적을 확인하고 등록된 범위에서 수정하세요."
    if status == "blocked" and task.get("blocker"):
        return "inspect_blocker", "원장에 기록된 차단 사유와 재개 요건을 확인하세요."
    if status in {"implementing", "reviewing", "checking"}:
        return "inspect", "진행 중으로 기록된 실행의 상태와 최신 영수증을 확인하세요."
    if task.get("dependencies") and not task.get("dependency_receipts"):
        return "inspect_dependencies", "등록한 선행 작업의 완료·통합 영수증을 확인하세요."
    if goal and remaining:
        if any(c["state"] == "pending" for c in remaining):
            return "continue_goal", "목표 실행기에서 진행 가능한 조건을 이어가세요."
        if any(c["state"] == "waiting" for c in remaining):
            return "wait_for_input", "대기 조건의 재개 요건과 제출할 근거를 확인하세요."
        if unmet or not verified:
            return "recheck", "검증 요청된 조건을 등록 검사와 대조하세요."
        return "review", "현재 입력에서 남은 조건을 독립 검토하세요."
    if task.get("verification") and not verified:
        return "recheck", "기존 검사 결과를 확인하고 현재 입력에서 등록 검사를 수행하세요."
    if verified and not unmet and task.get("reviewer") and task["reviewer"] != "none" and verdict != "pass":
        return "review", "현재 변경과 검사 근거를 지정된 독립 검토자에게 전달하세요."
    if status == "ready_to_finish" and verified and (not goal or not remaining) and (task.get("reviewer") == "none" or verdict == "pass"):
        return "finish", "현재 입력에서 완료 판정을 실행하세요."
    checkpoint = (task.get("checkpoint") or {}).get("next")
    if checkpoint:
        return "checkpoint", checkpoint
    return "inspect", "원래 목표·허용 범위와 남은 조건을 원본 작업에서 확인하고 다음 단계를 선택하세요."
