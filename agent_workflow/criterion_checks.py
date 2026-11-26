"""Connect goal criteria to observations from a specific registered check attempt."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import stat
from xml.etree import ElementTree

from . import goal_policy
from .core import WorkflowError, now


def validate(config):
    rules = config["criterion_checks"]
    if not isinstance(rules, dict):
        raise WorkflowError("criterion_checks에는 완료 조건과 검사 설정의 객체가 필요합니다.")
    for text, rule in rules.items():
        if not isinstance(text, str) or not text.strip() or text != text.strip():
            raise WorkflowError("criterion_checks에는 비어 있지 않은 완료 조건 문장이 필요합니다.")
        if not isinstance(rule, dict) or set(rule) - {"check", "testcase"}:
            raise WorkflowError("조건 검사에는 check와 선택적인 testcase만 지정하세요.")
        if config["checks"].count(rule.get("check")) != 1:
            raise WorkflowError("조건 검사의 check는 공통 checks에 정확히 한 번 등록한 argv여야 합니다.")
        if "testcase" in rule:
            selector = rule["testcase"]
            if not isinstance(selector, str) or not selector.isprintable():
                raise WorkflowError("testcase는 JUnit classname::name 문자열이어야 합니다.")
            classname, sep, name = selector.partition("::")
            if not (sep and classname.strip() and name.strip()):
                raise WorkflowError("testcase에 JUnit classname과 name을 ::로 구분해 입력하세요.")


def bindings(task):
    """Resolve against the frozen base slots, never a later check-add with the same argv."""
    config = task.get("project_config") or {}
    rules = config.get("criterion_checks", {})
    if not rules or not goal_policy.state(task):
        return {}
    return {number: (config["checks"].index(rule["check"]), rule)
            for number, text in enumerate(task["criteria"], 1) if (rule := rules.get(text))}


def _read_report(path):
    # Only ingest the regular file in this attempt's private directory. Never follow
    # a link, block on a FIFO, or load an unbounded XML document into the ledger.
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError("not a regular JUnit report")
        limit = 8 * 1024 * 1024
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError("oversized JUnit report")
    return data


def _junit_states(directory, selectors):
    try:
        data = _read_report(directory / "junit.xml")
    except FileNotFoundError:
        return dict.fromkeys(selectors, "missing"), None
    except (ValueError, OSError):
        return dict.fromkeys(selectors, "invalid"), None
    digest = hashlib.sha256(data).hexdigest()
    try:
        xml = data.decode("utf-8-sig")
        if "<!DOCTYPE" in xml or "<!ENTITY" in xml or "\x00" in xml:
            raise ValueError("unsupported XML")
        root = ElementTree.fromstring(xml)
        if root.tag not in {"testsuite", "testsuites"}:
            raise ValueError("expected JUnit suite")
    except (ValueError, ElementTree.ParseError):
        return dict.fromkeys(selectors, "invalid"), digest

    states = dict.fromkeys(selectors, "missing")
    suites = [root] if root.tag == "testsuite" else root.findall("testsuite")
    for suite in suites:
        for case in suite.findall("testcase"):
            selector = f"{case.get('classname')}::{case.get('name')}"
            if selector not in states:
                continue
            if states[selector] != "missing":
                states[selector] = "ambiguous"
                continue
            tags = {child.tag for child in case}
            if tags & {"error", "failure"}:
                states[selector] = "failed"
            elif "skipped" in tags:
                states[selector] = "skipped"
            elif tags - {"system-out", "system-err", "properties"} or case.get("status") not in (None, "run"):
                states[selector] = "invalid"
            else:
                states[selector] = "passed"
    return states, digest


def capture(task, index, directory, result):
    """Save observations before another check runs; raw XML stays in the attempt directory."""
    rules = [rule for slot, rule in bindings(task).values() if slot == index and rule["check"] == result["argv"]]
    if not rules:
        return
    result["result_id"] = f"{task['id']}/checks/{directory.name}"
    result["recorded_at"] = now()
    selectors = {rule["testcase"] for rule in rules if "testcase" in rule}
    if selectors:
        states, digest = _junit_states(directory, selectors)
        result["testcases"] = states
        if digest is not None:
            result["report_sha256"] = digest


def observations(task):
    """Project the latest verification only. Historical audit rows retain their own IDs."""
    verification = task.get("verification") or {}
    results = verification.get("results", [])
    observations_by_id = {}
    for number, (slot, rule) in bindings(task).items():
        evidence = {"argv": rule["check"], "state": "pending"}
        testcase = rule.get("testcase")
        if testcase:
            evidence["testcase"] = testcase
        observations_by_id[number] = evidence
        if slot >= len(results):
            continue
        result = results[slot]
        result_id = result.get("result_id")
        expected_id = "/".join(Path(result.get("stdout", "")).parent.parts[-3:])
        if (result.get("argv") != rule["check"] or result_id != expected_id
                or not expected_id.startswith(f"{task['id']}/checks/")):
            evidence["state"] = "missing"
            continue
        state = "passed" if result.get("exit_code") == 0 and result.get("timed_out") is False else "failed"
        if (verification.get("code_changed_during_checks") or verification.get("references_changed_during_checks")
                or verification.get("check_context_changed_during_checks")):
            state = "invalid"
        elif testcase:
            observed = result.get("testcases", {}).get(testcase, "missing")
            # A different failing test can make pytest exit nonzero even when this
            # case was skipped. Its own outcome still explains the missing proof.
            if observed != "passed":
                state = observed
        evidence.update(state=state, result_id=result_id, at=result["recorded_at"],
                        fingerprint=verification["fingerprint"], refs_fingerprint=verification["refs_fingerprint"])
        if testcase and result.get("report_sha256"):
            evidence["report_sha256"] = result["report_sha256"]
    return observations_by_id


def audit_matches(task, rows):
    current = observations(task)
    cited = {row["criterion"]: row.get("check_result_id") for row in rows}
    return all(evidence["state"] == "passed" and cited.get(number) == evidence["result_id"]
               for number, evidence in current.items())
