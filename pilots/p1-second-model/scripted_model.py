#!/usr/bin/env python3
"""Calibration transport for the fixture. Outcomes are deliberately predetermined."""
import json
import os
from pathlib import Path
import sys

args = sys.argv[1:]
provider = Path(sys.argv[0]).name
case = os.environ["PAGER_CALIBRATION_CASE"]
prompt = sys.stdin.read()
if provider == "codex":
    schema = json.loads(Path(args[args.index("--output-schema") + 1]).read_text())
else:
    schema = json.loads(args[args.index("--json-schema") + 1])

if "groups" in schema["properties"]:
    groups = {
        "a": {"codex": ["inputs"], "claude": ["bounds"]},
        "b": {"codex": ["bounds"], "claude": ["bounds"]},
        "c": {"codex": ["bounds"], "claude": ["bounds"]},
        "d": {"codex": ["bounds", "inputs"], "claude": ["inputs"]},
        "e": {"codex": ["bounds"], "claude": ["archive"]},
    }[case][provider]
    answer = {"groups": groups, "reason": "고정 응답으로 선택기 채점을 검증합니다."}
else:
    with_report = "I refactored page bounds and believe output" in prompt
    detail = ""
    if case == "a":
        detail = "At an exact final page the function incorrectly returns another cursor."
    elif case == "c" and not with_report:
        detail = "A zero page size is accepted and returns a repeating cursor instead of raising ValueError."
    elif case in ("b", "d") and not with_report:
        detail = "The ordinary page loses its next cursor."  # Does not identify B's mixed-type defect.
    findings = ([{"blocking": True, "detail": detail, "evidence": "pager.py: " + detail}]
                if detail else [])
    answer = {"outcome": "changes_requested" if findings else "pass", "summary": "Calibration review.",
              "next_action": "Read the finding." if findings else "Complete.", "findings": findings}
if provider == "codex":
    Path(args[args.index("--output-last-message") + 1]).write_text(json.dumps(answer))
else:
    print(json.dumps({"subtype": "success", "structured_output": answer}))
