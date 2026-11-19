"""Choose an automatic reviewer from paired, human-labelled observations in this ledger."""
from __future__ import annotations

from statistics import median

from .recommendation_records import routing_samples


MIN_LABELLED_INPUTS = 5
RECENT_INPUTS = 10


def decide(home, profile_digest, areas, provider, truncated, *, profile=None):
    """Apply the existing routing rule until a comparable cohort supports a change."""
    other = {"codex": "claude", "claude": "codex"}[provider]
    areas = sorted(set(areas))
    # Truncated inputs always get an independent review. Full-input observations cannot
    # tell us the effect of missing evidence, even if the visible areas are identical.
    samples = [] if truncated else routing_samples(
        home, profile_digest, areas, provider, other, profile=profile
    )[-RECENT_INPUTS:]
    rescued_inputs = rescued_groups = extra_groups = unresolved_groups = 0
    durations = []
    for sample in samples:
        primary, combined, expected = (set(sample[k]) for k in ("primary", "combined", "expected"))
        added = combined - primary
        rescued = len(added & expected)
        rescued_inputs += int(rescued > 0)
        rescued_groups += rescued
        extra_groups += len(added - expected)
        unresolved_groups += len(expected - combined)
        if sample["review_ms"] is not None:
            durations.append(sample["review_ms"])

    count = len(samples)
    reviewer = other if truncated or len(areas) >= 2 else None
    if truncated:
        basis, reason = "truncated", "변경 입력이 생략되어 두 번째 모델로 검토합니다."
    elif count < MIN_LABELLED_INPUTS:
        basis = "insufficient_samples"
        reason = f"비교 가능한 평가가 {MIN_LABELLED_INPUTS}건 미만이어서 기존 변경 영역 기준을 적용합니다."
    elif len(areas) == 1 and rescued_inputs * 2 >= count and rescued_groups > extra_groups:
        reviewer = other
        basis, reason = "measured_rescue", "유사 변경의 절반 이상에서 필요한 검사를 추가했고, 불필요하게 추가한 묶음보다 필요한 묶음이 많았습니다."
    elif len(areas) >= 2 and rescued_groups == 0 and unresolved_groups == 0:
        reviewer = None
        basis, reason = "measured_no_gain", "비교한 모든 입력에서 첫 모델이 필요한 묶음을 모두 선택했습니다."
    else:
        basis, reason = "default_areas", "실측 결과가 기본 호출 기준을 변경할 조건에 이르지 않았습니다."

    return {"reviewer": reviewer, "basis": basis, "reason": reason,
            "evidence": {"labeled_inputs": count, "rescued_inputs": rescued_inputs,
                         "rescued_groups": rescued_groups, "extra_groups": extra_groups,
                         "unresolved_groups": unresolved_groups,
                         "latency_samples": len(durations),
                         "median_review_ms": median(durations) if durations else None}}
