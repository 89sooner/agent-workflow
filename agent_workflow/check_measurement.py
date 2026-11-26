"""Measure ordered check selections and the full suite on pinned Git trees."""
from __future__ import annotations

from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import re
import signal
from statistics import median
import subprocess
import sys
import tempfile
import time

from . import check_advisor, check_advisor_git, profiles
from .core import Store, WorkflowError, create_task, git, snapshot
from .local_recovery import check_context
from .policy import window_limit
from .runner import process
from .services import environment, running
from .workspaces import prepare


def _command_table(config):
    groups = config.get("check_groups", {})
    full = profiles.check_plan(config, list(groups))["checks"]
    return [
        {"id": f"C{index + 1}", "argv": argv, "base_check": index < len(config["checks"]),
         "groups": [name for name, commands in groups.items() if argv in commands],
         "baseline": [], "candidate": []}
        for index, argv in enumerate(full)
    ]


def _plan_ids(config, commands, groups):
    # Baseline argv may be repeated on purpose. Optional argv are distinct
    # and follow the provider's group order, just as in a registered task.
    slots = [row["id"] for row in commands if row["base_check"]]
    optional = {tuple(row["argv"]): row["id"] for row in commands if not row["base_check"]}
    plan = profiles.check_plan(config, groups)["checks"]
    return slots + [optional[tuple(argv)] for argv in plan[len(slots):]]


def _digest(path):
    with Path(path).open("rb") as stream:
        digest = hashlib.sha256()
        while chunk := stream.read(65536):
            digest.update(chunk)
    return digest.hexdigest()


def _checkout_paths(value, source, checkout):
    """Rebase absolute paths in a path or search path, preserving relative entries."""
    if str(source) not in value:
        return value
    mapped = []
    for part in value.split(os.pathsep):
        path = Path(os.path.normpath(part))
        if path.is_absolute() and path.is_relative_to(source):
            part = str(checkout / path.relative_to(source))
        mapped.append(part)
    return os.pathsep.join(mapped)


def _checkout_argv(argv, source, checkout):
    """Map standalone paths, --option=path and environment assignments to this checkout."""
    mapped = []
    embedded = re.compile(re.escape(str(source)) + r"(?=$|[/\s:'\"),;])")
    for arg in argv:
        key, sep, value = arg.partition("=")
        if not sep or not (key.startswith("--") or re.fullmatch(r"[A-Za-z_]\w*", key)):
            key, sep, value = "", "", arg
        value = _checkout_paths(value, source, checkout)
        if embedded.search(value):
            raise WorkflowError("저장소 경로를 명령 본문에 포함했습니다. 저장소 안의 파일은 별도 경로 인자로 지정하세요.")
        mapped.append(key + sep + value)
    return mapped


def _run_check(store, task, row, argv, timeout, fd):
    directory = store.directory(task["id"]) / row["id"]
    env = {**environment(task, directory), "AGENT_WORKFLOW_HOME": str(store.home)}
    try:
        result = process(argv, task["project"], directory, timeout, fd, env_extra=env)
    except OSError as exc:
        return {"status": "process_error", "error": type(exc).__name__}
    return {"status": "ran", "seconds": result["seconds"], "timed_out": result["timed_out"],
            "exit_code": result["exit_code"], "stdout_sha256": _digest(result["stdout"]),
            "stderr_sha256": _digest(result["stderr"])}


def _trial(root, store, mirror, revision, side, repeat, config, commands, check_timeout, deadline):
    trial_id = f"{side}-{repeat}"
    checkout = root / trial_id
    git(mirror, "worktree", "add", "--quiet", "--detach", str(checkout), revision,
        timeout=min(30, max(.001, deadline - time.monotonic())))
    source = Path(config["project"])
    run_config = {**config, "project": str(checkout),
                  "setup": [_checkout_argv(argv, source, checkout) for argv in config["setup"]],
                  "services": [{**s, "argv": _checkout_argv(s["argv"], source, checkout)} for s in config["services"]]}
    task = create_task(store, trial_id, checkout, "등록 검사 측정", ["전체 검사 결과 기록"],
                       [], [c["argv"] for c in commands], config["owner"], config["reviewer"],
                       "격리된 커밋의 검사 측정", metadata={"project_config": run_config, "execution": dict(config["execution"])})
    if task["baseline"]["head"] != revision:
        raise WorkflowError("준비한 작업 사본이 측정할 커밋과 다릅니다.")
    context_task = {**task, "project_config": config}
    trial = {"side": side, "repeat": repeat, "revision": revision, "status": "complete"}
    samples = []
    try:
        with store.lock(checkout) as fd:
            remaining = deadline - time.monotonic()
            if remaining < 1:
                raise WorkflowError("전체 검사 측정의 실행 시간 한도에 도달했습니다.")
            task["execution"]["max_seconds"] = min(task["execution"]["max_seconds"], int(remaining))
            prepare(store, task, check_timeout, fd)
            if snapshot(checkout)["fingerprint"] != task["baseline"]["fingerprint"]:
                raise WorkflowError("준비 명령이 고정한 소스 입력을 변경했습니다.")
            context = check_context(context_task)
            trial["check_context"] = context
            with running(store, task, fd, deadline=deadline):
                for command in commands:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise WorkflowError("전체 검사 측정의 실행 시간 한도에 도달했습니다.")
                    timeout = min(check_timeout, window_limit(config), remaining)
                    argv = _checkout_argv(command["argv"], source, checkout)
                    sample = _run_check(store, task, command, argv, timeout, fd)
                    samples.append(sample)
                    if (snapshot(checkout)["fingerprint"] != task["baseline"]["fingerprint"]
                            or check_context(context_task) != context):
                        sample["status"] = "changed_input"
                        raise WorkflowError("검사가 고정한 소스 또는 등록 환경 입력을 변경했습니다.")
            if snapshot(checkout)["fingerprint"] != task["baseline"]["fingerprint"] or check_context(context_task) != context:
                raise WorkflowError("서비스 종료 시 소스 또는 등록 환경 입력이 달라졌습니다.")
    except (WorkflowError, OSError) as exc:
        trial.update(status="inconclusive", error=str(exc))
    for index, command in enumerate(commands):
        sample = samples[index] if index < len(samples) else {"status": "not_run"}
        if trial["status"] == "inconclusive" and sample["status"] == "ran":
            sample["status"] = "inconclusive_trial"
        command[side].append(sample)
    return trial


def _classify(row):
    baseline, candidate = row["baseline"], row["candidate"]
    if (len(baseline) < 2 or len(candidate) < 2
            or any(s["status"] != "ran" or s["timed_out"] or s["exit_code"] < 0 for s in baseline + candidate)):
        return "inconclusive"
    base_codes = {s["exit_code"] for s in baseline}
    candidate_codes = {s["exit_code"] for s in candidate}
    if len(base_codes) != 1 or len(candidate_codes) != 1:
        return "inconsistent"
    if candidate_codes != {0}:
        return "regression" if base_codes == {0} else "preexisting_failure"
    return "passing" if base_codes == {0} else "fixed"


def _arm(groups, selected, commands, model_calls, plan):
    detected = list(dict.fromkeys(c["check_id"] for c in selected if c["classification"] == "regression"))
    unknown = [c["id"] for c in selected if c["classification"] in ("inconsistent", "inconclusive")]
    change_detected = bool(detected)
    if not detected and unknown:
        change_detected = None
    times = [c["candidate_median_ms"] for c in selected]
    return {"groups": groups, "command_ids": [c["id"] for c in selected], "observation_plan": plan,
            "detected_regressions": detected,
            "change_regression_detected": change_detected,
            "missed_regressions": [c["id"] for c in commands if c["id"] == c["check_id"]
                                   and c["classification"] == "regression" and c["id"] not in detected],
            "selected_preexisting_failures": list(dict.fromkeys(c["check_id"] for c in selected
                                                               if c["classification"] == "preexisting_failure")),
            "inconclusive_selected": unknown,
            "selected_check_ms": sum(times) if None not in times else None,
            "model_calls": sum(call["calls"] for call in model_calls),
            "model_ms": sum(call["elapsed_ms"] for call in model_calls)}


def _oracle(root, config, base, candidate, check_timeout, plans):
    table = {row["id"]: row for row in _command_table(config)}
    deadline = time.monotonic() + config["execution"]["max_seconds"]
    mirror = root / "mirror.git"
    git(root, "clone", "--quiet", "--local", "--bare", "--no-hardlinks", "--", config["project"], str(mirror),
        timeout=config["execution"]["max_seconds"])
    git(mirror, "config", "core.hooksPath", "/dev/null")
    git(mirror, "config", "core.autocrlf", "false")
    git(mirror, "remote", "remove", "origin")
    source = Path(config["project"])
    inherited_paths = {k: v for k, v in os.environ.items() if str(source) in v}
    observations = {}
    for name, identifiers in plans.items():
        directory = root / name
        directory.mkdir()
        commands = [{**table[key], "baseline": [], "candidate": []} for key in identifiers]
        trials = []
        observations[name] = {"commands": commands, "trials": trials}
        if not commands:
            continue
        for repeat, order in enumerate((("baseline", "candidate"), ("candidate", "baseline")), 1):
            for side in order:
                revision = base if side == "baseline" else candidate
                try:
                    if time.monotonic() >= deadline:
                        raise WorkflowError("검사 측정의 전체 실행 시간 한도에 도달했습니다.")
                    # Setup and services also see a fresh ledger on each trial.
                    ledger = directory / f"ledger-{side}-{repeat}"
                    checkout = directory / f"{side}-{repeat}"
                    os.environ.update({k: _checkout_paths(v, source, checkout) for k, v in inherited_paths.items()})
                    os.environ["PWD"] = str(checkout)
                    os.environ["AGENT_WORKFLOW_HOME"] = str(ledger)
                    with closing(Store(ledger)) as store:
                        trial = _trial(directory, store, mirror, revision, side, repeat, config, commands, check_timeout, deadline)
                except (WorkflowError, OSError, subprocess.TimeoutExpired) as exc:
                    trial = {"side": side, "repeat": repeat, "revision": revision, "status": "inconclusive", "error": str(exc)}
                    for command in commands:
                        command[side].append({"status": "not_run"})
                trials.append(trial)
    return observations


def _isolated_oracle(config, base, candidate, check_timeout, plans):
    # Setup and service launches also inherit these settings. Do not change the
    # process environment of another concurrent measurement or the operator's CLI.
    with tempfile.TemporaryDirectory(prefix="aw-check-measure-", dir="/tmp") as temp:
        root = Path(temp)
        if root.is_relative_to(config["project"]):
            raise WorkflowError("측정 원장은 대상 저장소 밖에 있어야 합니다.")
        env = {k: v for k, v in os.environ.items() if not k.startswith(("GIT_", "AGENT_WORKFLOW_"))}
        env.update(AGENT_WORKFLOW_HOME=str(root / "ledger"), GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)
        argv = [sys.executable, "-B", "-m", __spec__.name, str(root)]
        request = json.dumps({"config": config, "base": base, "candidate": candidate,
                              "check_timeout": check_timeout, "plans": plans})
        with subprocess.Popen(argv, cwd=Path(__file__).resolve().parents[1], env=env, stdin=subprocess.PIPE,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True) as child:
            try:
                out, err = child.communicate(request)
            except BaseException:
                # Let the worker's process/service context managers stop every group they own.
                if child.poll() is None:
                    child.send_signal(signal.SIGINT)
                child.communicate()
                raise
        if child.returncode:
            raise WorkflowError("격리 검사 측정 실패: " + err.strip()[-1200:])
        return json.loads(out)


def _classify_plan(commands, trials):
    drift = [side for side in ("baseline", "candidate")
             if len({t["check_context"] for t in trials if t["side"] == side and t["status"] == "complete"}) > 1]
    same_argv = {}
    for command in commands:
        same_argv.setdefault(tuple(command["argv"]), []).append(command)
    for occurrences in same_argv.values():
        samples = {side: [sample for row in occurrences for sample in row[side]] for side in ("baseline", "candidate")}
        classification = "inconclusive" if drift else _classify(samples)
        for command in occurrences:
            command["check_id"] = occurrences[0]["id"]
            command["classification"] = classification
            times = [s["seconds"] * 1000 for s in command["candidate"] if s["status"] == "ran" and not s["timed_out"]]
            command["candidate_median_ms"] = round(median(times), 2) if len(times) == 2 and not drift else None
    return drift


def measure(home, profile, base, candidate, criteria, *, provider="codex", reviewer="claude", timeout=90, check_timeout=30):
    """Ask both providers once; execute each distinct ordered plan on fresh checkouts."""
    if provider == reviewer or {provider, reviewer} != {"codex", "claude"}:
        raise WorkflowError("비교할 서로 다른 추천 공급자 둘을 지정하세요.")
    if type(check_timeout) is not int or not 1 <= check_timeout <= 86400:
        raise WorkflowError("검사 제한 시간은 1~86400초입니다.")
    with closing(Store(home, read_only=True)) as reader:
        record = profiles.get(reader, profile)
    config = record["config"]
    source = Path(config["project"])
    base = check_advisor_git.resolve_commit(source, base)
    candidate = check_advisor_git.resolve_commit(source, candidate)
    if base == candidate:
        raise WorkflowError("변경을 비교할 서로 다른 기준·후보 커밋이 필요합니다.")
    commands = _command_table(config)
    if not config.get("check_groups") or not commands:
        raise WorkflowError("보조 모델 효과를 측정할 등록 검사 묶음이 필요합니다.")
    window_limit(config)
    recommendation = check_advisor.recommend(home, profile, criteria, base=base, candidate=candidate,
                                             provider=provider, reviewer=reviewer, timeout=timeout, measure_calls=True)
    if recommendation["profile_digest"] != record["digest"]:
        raise WorkflowError("측정 도중 프로필이 변경됐습니다. 새 설정으로 다시 실행하세요.")

    calls = recommendation["model_calls"]
    selections = {}
    if calls["primary"]["status"] == "complete":
        selections["primary"] = recommendation["primary"]["groups"]
    if recommendation["status"] == "complete":
        selections["combined"] = recommendation["suggested_groups"]
    plans = {"full": [c["id"] for c in commands]}
    arm_plans = {}
    for name, groups in selections.items():
        ids = _plan_ids(config, commands, groups)
        arm_plans[name] = next((plan for plan, existing in plans.items() if existing == ids), name)
        plans.setdefault(arm_plans[name], ids)

    check_timeout = min(check_timeout, config["execution"]["timeout"])
    started = time.monotonic()
    oracle_error = None
    try:
        observations = _isolated_oracle(config, base, candidate, check_timeout, plans)
    except (WorkflowError, OSError) as exc:
        oracle_error = str(exc)
        table = {row["id"]: row for row in commands}
        observations = {name: {"commands": [dict(table[key]) for key in ids], "trials": []}
                        for name, ids in plans.items()}

    oracle_ms = round((time.monotonic() - started) * 1000)
    for observation in observations.values():
        observation["replicate_environment_drift"] = _classify_plan(observation["commands"], observation["trials"])
    full = observations["full"]
    commands = full["commands"]
    primary = combined = None
    delta = {"additional_regressions": None, "newly_detected_change": None, "additional_selected_check_ms": None,
             "additional_model_calls": calls["review"]["calls"], "additional_model_ms": calls["review"]["elapsed_ms"]}
    if "primary" in selections:
        plan = arm_plans["primary"]
        primary = _arm(selections["primary"], observations[plan]["commands"], commands, [calls["primary"]], plan)
    if "combined" in selections:
        plan = arm_plans["combined"]
        selected = observations[plan]["commands"]
        combined = _arm(selections["combined"], selected, commands, [calls["primary"], calls["review"]], plan)
        extra = [c for c in selected if c["id"] not in primary["command_ids"]]
        costs = [c["candidate_median_ms"] for c in extra]
        delta.update(additional_regressions=[key for key in combined["detected_regressions"]
                                             if key not in primary["detected_regressions"] and key not in primary["inconclusive_selected"]],
                     additional_selected_check_ms=sum(costs) if None not in costs else None)
        if primary["change_regression_detected"] is True or combined["change_regression_detected"] is False:
            delta["newly_detected_change"] = False
        elif primary["change_regression_detected"] is False:
            delta["newly_detected_change"] = combined["change_regression_detected"]
    status = recommendation["status"]
    if status == "complete" and any(c["classification"] in ("inconsistent", "inconclusive")
                                    for obs in observations.values() for c in obs["commands"]):
        status = "checks_inconclusive"
    return {"kind": "agent-workflow.check-measurement", "version": 2, "status": status, "profile": profile,
            "profile_digest": record["digest"], "base": base, "candidate": candidate,
            "input_digest": recommendation["input_digest"], "change_summary": recommendation["change_summary"],
            "model_calls": calls, "model_errors": {role: recommendation[role]["error"] for role in ("primary", "review")
                                                 if (recommendation.get(role) or {}).get("error")},
            "selections": {role: (recommendation.get(role) or {}).get("groups") for role in ("primary", "review")},
            "oracle_elapsed_ms": oracle_ms, "oracle_error": oracle_error, "replicate_environment_drift": full["replicate_environment_drift"],
            "oracle_trials": full["trials"], "commands": commands,
            "selected_plan_observations": {name: obs for name, obs in observations.items() if name != "full"},
            "arms": {"primary": primary, "combined": combined}, "delta": delta}


if __name__ == "__main__":
    try:
        print(json.dumps(_oracle(Path(sys.argv[1]), **json.load(sys.stdin))))
    except (WorkflowError, OSError, subprocess.TimeoutExpired) as exc:
        raise SystemExit(str(exc))
