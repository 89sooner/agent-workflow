"""Run paired CLI measurements on the local pagination fixture."""
from __future__ import annotations

import argparse
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

from unittest.mock import patch

from fixture import BASE, CASES, CHECKS, CRITERIA, IMPLEMENTATION_REPORT, PROFILE, ROOT, fixture_environment, prepare, profile_config
from agent_workflow import check_advisor, profiles
from agent_workflow.check_advisor_git import change_context
from agent_workflow.core import Store


HERE = Path(__file__).resolve().parent
# This program lives with the evaluator, outside all candidate worktrees/model packets.
ORACLE = """
import json, sys
namespace = {}
exec(compile(sys.stdin.read(), 'pager.py', 'exec'), namespace)
count = mismatches = 0
witness = None
for length in range(5):
    items = list(range(length))
    for start in range(-1, 6):
        for size in range(-1, 6):
            count += 1
            expected = 'ValueError' if start < 0 or size < 1 else [
                items[start:start+size], start+size if start+size < length else None]
            try:
                observed = list(namespace['page'](items, start, size))
            except ValueError:
                observed = 'ValueError'
            if observed != expected:
                mismatches += 1
                if witness is None:
                    witness = dict(length=length, start=start, size=size, expected=expected, observed=observed)
try:
    mixed = namespace['page']([], -1, None)
except (ValueError, TypeError) as exc:
    mixed = type(exc).__name__
print(json.dumps(dict(inputs=count, mismatches=mismatches, witness=witness, mixed_exception=mixed)))
"""


def verify_inputs(manifest, prepared):
    """Verify each measured tree and the held-out defect label before sending any prompts."""
    oracle = {}
    for case in [{"case": "base", "candidate": manifest["base"], "defect": None}, *manifest["cases"]]:
        revision = case["candidate"]
        git = ["git", "-C", str(prepared / "repository")]
        names = subprocess.check_output([*git, "ls-tree", "-r", "--name-only", revision], text=True).splitlines()
        if names != [".gitignore", "checks.py", "pager.py"]:
            raise ValueError("Unexpected files in the synthetic repository.")
        checks = subprocess.check_output([*git, "show", revision + ":checks.py"], text=True)
        if checks != CHECKS:
            raise ValueError("Fixture check implementation changed.")
        source = subprocess.check_output([*git, "show", revision + ":pager.py"], text=True)
        if case["case"] == "base":
            if source != BASE:
                raise ValueError("Fixture base differs from the calibration reference.")
        else:
            original = next(entry for entry in CASES if entry[0] == case["case"])
            if source != original[1] or case["defect"] != original[2]:
                raise ValueError("Fixture source or frozen defect label changed.")
        result = subprocess.run([sys.executable, "-I", "-B", "-c", ORACLE], input=source,
                                text=True, capture_output=True, check=True)
        observed = json.loads(result.stdout)
        if bool(observed["mismatches"]) != bool(case["defect"]):
            raise ValueError("The held-out oracle did not reproduce the registered label.")
        oracle[case["case"]] = observed
    return oracle


def invoke(prepared, argv, env):
    cmd = [sys.executable, "-B", "-m", "agent_workflow", "--home", str(prepared / "ledger"), *argv]
    completed = subprocess.run(cmd, env=env, cwd=ROOT, capture_output=True, text=True)
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError:
        # Raw stderr may include provider/host details; keep it off the shared artifact.
        print(completed.stderr[-800:], file=sys.stderr)
        return {"status": "unavailable", "cli_exit": completed.returncode}


def write(report, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, encoding="utf-8", delete=False) as temp:
        json.dump(report, temp, ensure_ascii=False, indent=2, allow_nan=False)
        temp.write("\n")
    os.replace(temp.name, path)


def versions(mode):
    result = {"python": sys.version.split()[0]}
    if mode == "live":
        for name in ("codex", "claude"):
            try:
                probe = subprocess.run([name, "--version"], text=True, capture_output=True, timeout=10)
                result[name] = probe.stdout.strip() if probe.returncode == 0 else None
            except (OSError, subprocess.TimeoutExpired):
                result[name] = None
    return result


def prior_check(path, prepared, manifest, case, provider, auxiliary):
    receipt = json.loads(path.read_text())
    verify_prior_check(receipt, prepared, manifest, case, provider, auxiliary)
    return receipt


def verify_prior_check(receipt, prepared, manifest, case, provider, auxiliary):
    preview = check_advisor.recommend(prepared / "ledger", PROFILE, CRITERIA, base=manifest["base"],
                                      candidate=case["candidate"], provider=provider, reviewer=auxiliary, preview=True)
    if (receipt["kind"] != "agent-workflow.check-measurement" or receipt["status"] != "complete"
            or receipt["profile_digest"] != preview["profile_digest"] or receipt["input_digest"] != preview["input_digest"]
            or (receipt["base"], receipt["candidate"]) != (manifest["base"], case["candidate"])
            or [receipt["model_calls"][role]["provider"] for role in ("primary", "review")] != preview["providers"]
            or any(c["calls"] != 1 or c["status"] != "complete" for c in receipt["model_calls"].values())
            or [(t["side"], t["repeat"]) for t in receipt["oracle_trials"]] != [
                ("baseline", 1), ("candidate", 1), ("candidate", 2), ("baseline", 2)]):
        raise ValueError("Prior check receipt does not match the pinned pair.")


def prior_four(path, prepared, manifest, mode, provider, auxiliary, reviewer):
    original = json.loads(path.read_text())
    if (original.get("mode") != mode or original.get("base") != manifest["base"]
            or original.get("criteria") != CRITERIA or original.get("implementation_report") != IMPLEMENTATION_REPORT
            or (original.get("primary"), original.get("auxiliary"), original.get("independent_reviewer"))
            != (provider, auxiliary, reviewer) or len(original["cases"]) != 4):
        raise ValueError("The saved study has a different mode, setup or case count.")
    for old, case in zip(original["cases"], manifest["cases"][:4]):
        if any(old[k] != v for k, v in case.items()):
            raise ValueError("The earlier case commit, rubric or order changed.")
        verify_prior_check(old["checks"], prepared, manifest, case, provider, auxiliary)
        review = old["review"]
        change = change_context(prepared / "repository", manifest["base"], candidate=case["candidate"])
        packet = {"criteria": CRITERIA, "change": change}
        for name in ("blind", "with_report"):
            payload = packet if name == "blind" else {**packet, "implementation_report": IMPLEMENTATION_REPORT}
            expected = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=True).encode()).hexdigest()
            if review["arms"][name]["payload_digest"] != expected:
                raise ValueError("The earlier review payload changed.")
        expected_order = ["with_report", "blind"] if case["report_first"] else ["blind", "with_report"]
        if (review["base"] != manifest["base"] or review["candidate"] != case["candidate"]
                or review["reviewer"] != reviewer or review["order"] != expected_order
                or review["ground_truth"] != case["defect"] or review["profile_digest"] != old["checks"]["profile_digest"]):
            raise ValueError("The earlier review configuration changed.")
    return original


def run_cli(mode, prepared, output, timeout, reviewer, provider, auxiliary, first_check=None, previous=None):
    manifest = json.loads((prepared / "manifest.json").read_text())
    with closing(Store(prepared / "ledger", read_only=True)) as ledger:
        cfg = profiles.get(ledger, PROFILE)["config"]
    if cfg != profiles.validate(profile_config(prepared / "repository")):
        raise ValueError("The registered check profile differs from the synthetic pilot.")
    oracle = verify_inputs(manifest, prepared)
    if (manifest["criteria"] != CRITERIA or manifest["implementation_report"] != IMPLEMENTATION_REPORT
            or manifest["profile"] != PROFILE or [c["case"] for c in manifest["cases"]] != [c[0] for c in CASES]):
        raise ValueError("Expected the synthetic pager manifest produced by fixture.py.")
    report = {"mode": mode, "versions": versions(mode), "base": manifest["base"], "criteria": CRITERIA,
              "implementation_report": IMPLEMENTATION_REPORT, "primary": provider,
              "auxiliary": auxiliary, "independent_reviewer": reviewer, "oracle": oracle, "cases": []}
    if previous:
        prior = prior_four(previous, prepared, manifest, mode, provider, auxiliary, reviewer)
        report.update(cases=prior["cases"], initial_summary_as_labelled=prior.get("summary"),
                      initial_report_sha256=hashlib.sha256(previous.read_bytes()).hexdigest())
    prior_count = len(report["cases"])
    with tempfile.TemporaryDirectory(prefix="pager-models-") as bindir:
        env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
        if mode == "scripted":
            for name in ("codex", "claude"):
                target = Path(bindir) / name
                shutil.copyfile(HERE / "scripted_model.py", target)
                target.chmod(0o700)
            env["PATH"] = bindir + os.pathsep + env["PATH"]
        for case in manifest["cases"][prior_count:]:
            name = case["case"]
            if mode == "scripted":
                env["PAGER_CALIBRATION_CASE"] = name
            common = [PROFILE, "--base", manifest["base"], "--candidate", case["candidate"],
                      "--timeout", str(timeout)]
            for statement in CRITERIA:
                common += ["--criterion", statement]
            row = {**case, "checks": None, "review": None}
            report["cases"].append(row)
            if first_check and name == "a":
                row["checks"] = prior_check(first_check, prepared, manifest, case, provider, auxiliary)
                row["supplied_check_report_sha256"] = hashlib.sha256(first_check.read_bytes()).hexdigest()
            else:
                row["checks"] = invoke(prepared, ["measure-checks", *common, "--provider", provider,
                                                 "--reviewer", auxiliary, "--check-timeout", "15"], env)
            write(report, output)
            args = ["measure-review", *common, "--reviewer", reviewer,
                    "--implementation-report", IMPLEMENTATION_REPORT]
            if case["defect"]:
                args += ["--expected-defect", str(prepared / f"defect-{name}.json")]
            elif oracle[name]["mixed_exception"] != oracle["base"]["mixed_exception"]:
                # Fresh replays know the supplemental defect. Imported historical
                # receipts retain the clean label used when their sessions ran.
                supplemental = Path(bindir) / "mixed-defect.json"
                supplemental.write_text(json.dumps({"path": "pager.py", "cues": [
                    ["negative", "-1"], ["none", "nonnumeric", "non-numeric"], ["typeerror"], ["valueerror"]]}))
                args += ["--expected-defect", str(supplemental)]
            else:
                args += ["--clean"]
            if case["report_first"]:
                args.append("--report-first")
            row["review"] = invoke(prepared, args, env)
            write(report, output)
            print(f"{mode} {name}: checks={row['checks'].get('status')}, review={row['review'].get('status')}",
                  file=sys.stderr, flush=True)
    from score import summarize
    report["summary"] = summarize(report)
    write(report, output)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("live", "scripted"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--prepared", type=Path, help="Existing directory generated by fixture.py.")
    reuse = parser.add_mutually_exclusive_group()
    reuse.add_argument("--first-check-report", type=Path, help="Reuse an explicitly supplied live CLI receipt for case a.")
    reuse.add_argument("--prior-four-report", type=Path, help="Preserve A–D, including incomplete arms, and run only E.")
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--reviewer", choices=("codex", "claude"), default="codex")
    parser.add_argument("--provider", choices=("codex", "claude"), default="codex")
    parser.add_argument("--auxiliary", choices=("codex", "claude"), default="claude")
    args = parser.parse_args()
    if args.first_check_report and (args.mode != "live" or not args.prepared):
        parser.error("--first-check-report requires --mode live and the original --prepared directory")
    if args.prior_four_report and not args.prepared:
        parser.error("--prior-four-report requires the original --prepared directory with case e")
    if args.mode == "scripted" and (args.provider, args.auxiliary) != ("codex", "claude"):
        parser.error("The calibration configuration uses codex as primary and claude as auxiliary.")
    with tempfile.TemporaryDirectory(prefix="pager-pairstudy-") as scratch:
        prepared = args.prepared.resolve() if args.prepared else Path(scratch)
        if args.prepared is None:
            prepare(prepared)
        result = run_cli(args.mode, prepared, args.output.resolve(), args.timeout,
                         args.reviewer, args.provider, args.auxiliary, args.first_check_report, args.prior_four_report)
    print(json.dumps(result["summary"], ensure_ascii=False, indent=2))
    checks, review = result["summary"]["recommendation"], result["summary"]["review"]
    if args.mode == "scripted" and (checks["comparable_changes"] != len(CASES) or review["comparable_changes"] != len(CASES)):
        raise SystemExit("Incomplete pairs: consult the saved per-case report.")
    if args.mode == "scripted":
        blind, informed = review["arms"]["blind"], review["arms"]["with_report"]
        observed = (checks["additional_defective_changes"], checks["additional_clean_changes_blocked"],
                    blind["known_defects_detected"]["count"], blind["clean_changes_falsely_blocked"]["count"],
                    informed["known_defects_detected"]["count"], informed["clean_changes_falsely_blocked"]["count"],
                    blind["on_top_of_combined_checks"]["additional_defective_changes_caught"],
                    result["summary"]["observed_transport_totals"]["calls"])
        if observed != (1, 1, 2, 1, 1, 0, 1, 20):
            raise SystemExit(f"Calibration mismatch: {observed}")


if __name__ == "__main__":
    with patch.dict(os.environ, fixture_environment(), clear=True):
        main()
