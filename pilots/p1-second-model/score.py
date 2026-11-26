"""Aggregate individual and paired verdicts without conflating unavailable or disputed controls."""
import re


def _rate(hits, eligible):
    return {"count": hits if eligible else None, "eligible": eligible}


def _sum(values):
    return sum(values) if values and all(v is not None for v in values) else None


def _raw_block(checks, arm):
    if checks.get("version", 1) >= 2:
        return bool(arm["detected_regressions"] or arm["selected_preexisting_failures"])
    # Historical v1 receipts projected selected IDs onto the full suite.
    selected = set(arm["command_ids"])
    return any(cmd["id"] in selected and cmd["classification"] in {"regression", "preexisting_failure"}
               for cmd in checks["commands"])


def _check_pair(row):
    report = row.get("checks") or {}
    arms = report.get("arms", {})
    if report.get("status") != "complete" or not arms.get("primary") or not arms.get("combined"):
        return None
    return arms["primary"], arms["combined"]


def _mixed_hit(arm):
    if arm.get("outcome") != "changes_requested":
        return False
    for finding in arm["findings"]:
        if not finding["blocking"]:
            continue
        words = (finding["detail"] + " " + finding["evidence"]).casefold()
        if (re.search(r"(?<![\w./-])(?:\./)?pager\.py(?![\w/-]|\.\w)", words)
                and "valueerror" in words and "typeerror" in words
                and ("-1" in words or "negative" in words)
                and ("none" in words or "non-numeric" in words or "nonnumeric" in words)):
            return True
    return False


def _review_arm(rows, name, clean_ids, extra_defects):
    observed = [(r, r["review"]["arms"][name]) for r in rows if name in (r.get("review") or {}).get("arms", {})]
    scored = [(r, a) for r, a in observed if a.get("status") == "complete"]
    seeded = [a for r, a in scored if r["defect"] is not None]
    clean = [a for r, a in scored if r["case"] in clean_ids]
    # Cost includes attempted but ungraded reviews. Detection requires a completed verdict.
    joint = [(r, a, pair[1]) for r, a in observed if (pair := _check_pair(r))]
    green = [(r, a) for r, a, checks in joint if not _raw_block(r["checks"], checks)]
    green_scored = [(r, a) for r, a in green if a.get("status") == "complete"]
    added = [r["case"] for r, a in green_scored if
             (r["defect"] is not None and a["score"]["detected"])
             or (r["case"] in extra_defects and _mixed_hit(a))]
    return {
        "verdicts": len(scored),
        "inconclusive": len(observed) - len(scored),
        "known_defects_detected": _rate(sum(a["score"]["detected"] for a in seeded), len(seeded)),
        "clean_changes_falsely_blocked": _rate(sum(a["score"]["false_block"] for a in clean), len(clean)),
        "supplemental_detected": [r["case"] for r, a in scored if r["case"] in extra_defects and _mixed_hit(a)],
        "unmatched_supplemental_blocks": [r["case"] for r, a in scored if r["case"] in extra_defects
                                         and not r["defect"] and a["outcome"] == "changes_requested" and not _mixed_hit(a)],
        "model_ms": _sum([a["elapsed_ms"] for _, a in observed]), "model_calls": _sum([a["calls"] for _, a in observed]),
        "on_top_of_combined_checks": {
            "measured_changes": len(joint), "green_changes": len(green), "green_with_verdict": len(green_scored),
            "projected_review_calls_if_green_only": sum(a["calls"] for _, a in green) if joint else None,
            "projected_review_ms_if_green_only": sum(a["elapsed_ms"] for _, a in green) if joint else None,
            "additional_defective_changes_caught": len(added) if joint else None, "additional_defective_change_ids": added,
            "additional_clean_changes_falsely_blocked": sum(a["score"]["false_block"] for r, a in green_scored
                                                          if r["case"] in clean_ids) if joint else None,
        },
    }


def summarize(report):
    rows = report["cases"]
    oracle = report["oracle"]
    base_exception = oracle["base"]["mixed_exception"]
    supplemental = {r["case"] for r in rows if oracle[r["case"]]["mixed_exception"] != base_exception}
    clean_ids = {r["case"] for r in rows if r["defect"] is None and r["case"] not in supplemental}
    check_rows = [(r, p) for r in rows if (p := _check_pair(r))]
    check_arms = {}
    for index, name in enumerate(("primary", "combined")):
        defects = [(r, arms[index]) for r, arms in check_rows if r["defect"] is not None]
        clean = [(r, arms[index]) for r, arms in check_rows if r["case"] in clean_ids]
        check_arms[name] = {
            "defective_changes_detected": _rate(sum(bool(a["detected_regressions"]) for _, a in defects), len(defects)),
            "clean_changes_blocked_by_any_selected_failure": _rate(sum(_raw_block(r["checks"], a) for r, a in clean), len(clean)),
            "model_ms": _sum([arms[index]["model_ms"] for _, arms in check_rows]),
            "model_calls": _sum([arms[index]["model_calls"] for _, arms in check_rows]),
            "estimated_selected_check_ms": _sum([arms[index]["selected_check_ms"] for _, arms in check_rows]),
        }
    checks = {
        "comparable_changes": len(check_rows), "arms": check_arms,
        "additional_defective_changes": _sum([int(r["defect"] is not None and not p["detected_regressions"]
                                                 and bool(u["detected_regressions"])) for r, (p, u) in check_rows]),
        "additional_failing_commands": _sum([len(r["checks"]["delta"]["additional_regressions"]) for r, _ in check_rows]),
        "additional_clean_changes_blocked": _sum([int(r["case"] in clean_ids and not _raw_block(r["checks"], p)
                                                    and _raw_block(r["checks"], u)) for r, (p, u) in check_rows]),
        "additional_model_ms": _sum([r["checks"]["delta"]["additional_model_ms"] for r, _ in check_rows]),
        "additional_model_calls": _sum([r["checks"]["delta"]["additional_model_calls"] for r, _ in check_rows]),
        "additional_estimated_check_ms": _sum([r["checks"]["delta"]["additional_selected_check_ms"] for r, _ in check_rows]),
    }
    names = ("blind", "with_report")
    paired = [r for r in rows if all((r.get("review") or {}).get("arms", {}).get(n, {}).get("status") == "complete" for n in names)]
    review = {
        "comparable_changes": len(paired), "paired_case_ids": [r["case"] for r in paired],
        "arms": {n: _review_arm(rows, n, clean_ids, supplemental) for n in names},
        "paired_arms": {n: _review_arm(paired, n, clean_ids, supplemental) for n in names},
        "with_report_minus_blind_ms": _sum([r["review"]["pair_delta"]["elapsed_ms"] for r in paired]),
        "with_report_minus_blind_calls": _sum([r["review"]["pair_delta"]["calls"] for r in paired]),
        "inconclusive": [{"case": r["case"], "arm": n, "reason": "reviewer_blocked" if a["outcome"] == "blocked"
                          else "incomplete_input_or_transport", "error": a.get("error")}
                         for r in rows for n, a in (r.get("review") or {}).get("arms",{}).items()
                         if a.get("status") != "complete"],
    }
    attempts = []
    for r in rows:
        attempts.extend(call for call in ((r.get("checks") or {}).get("model_calls") or {}).values() if call)
        attempts.extend((r.get("review") or {}).get("arms", {}).values())
    return {"cases": len(rows), "recommendation": checks, "review": review,
            "adjudication": {"probe": "page([], -1, None)", "reference_exception": base_exception,
                             "supplemental_defect_cases": sorted(supplemental), "clean_control_cases": sorted(clean_ids),
                             "disputed_original_controls": sorted(r["case"] for r in rows if r["defect"] is None and r["case"] in supplemental)},
            "observed_transport_totals": {"calls": _sum([c["calls"] for c in attempts]),
                                          "ms": _sum([c["elapsed_ms"] for c in attempts])}}
