"""Compare the same scripted coding-agent trials by invoking the selected engine's CLI."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

HERE = Path(__file__).resolve().parent
REFERENCE_COMMIT = "9b0c443da3e72fc490ed487289b5ef62ae0242cf"
FIXTURE = "웹 fixture가 생성기 결과와 일치한다"
BUNDLE = "빌드된 웹 번들의 경계 검사가 통과한다"
EXTERNAL = "운영자가 제출한 관측 자료를 확인한다"
GEN = ["python3", "scripts/generate_web_fixtures.py", "--check"]
PYTEST = ["python3", "-m", "pytest", "-q", "tests/test_web_boundary.py",
          "--junitxml=$WORKFLOW_EVIDENCE_DIR/junit.xml"]
TESTCASE = "tests.test_web_boundary::test_16_bundle_scan_when_built"


def digest(paths, root):
    h = hashlib.sha256()
    for p in sorted(paths):
        h.update(p.relative_to(root).as_posix().encode() + b"\0" + p.read_bytes() + b"\0")
    return h.hexdigest()


def fixture_matches(work):
    data = json.loads((work / "web/fixtures/views.json").read_text())
    return set(data) == {f"view-{i}" for i in range(16)} and all(v == 29 for v in data.values())


def bundle_matches(work):
    p = work / "web/.next/view-version"
    return p.is_file() and p.read_text().strip() == "29"


def action(i, **kw):
    return {"id": i, **kw}


def observe_links(status, verification):
    refs = []
    results = verification.get("results", [])
    audit = {r["criterion"]: r for r in (status.get("last_audit") or {}).get("rows", [])}
    for row in status["criteria"]:
        proof = row.get("check_evidence")
        if not proof or not proof.get("result_id"):
            continue
        criterion = row["id"]
        expected = {FIXTURE: GEN, BUNDLE: PYTEST}[row["text"]]
        matches = [r for r in results if r.get("result_id") == proof["result_id"] and r["argv"] == expected]
        verified = len(matches) == 1 and proof["argv"] == expected
        if verified and expected == PYTEST:
            report = Path(matches[0]["stdout"]).parent / "junit.xml"
            verified = proof.get("report_sha256") == hashlib.sha256(report.read_bytes()).hexdigest()
        verified = verified and all(proof.get(k) == verification[k] for k in ("fingerprint", "refs_fingerprint"))
        if status["status"] == "completed":
            verified = verified and audit.get(criterion, {}).get("check_result_id") == proof["result_id"]
        refs.append({"criterion": criterion, "state": proof["state"], "result_id": proof["result_id"],
                     "matches_receipt_and_audit": verified})
    return refs


class Trial:
    def __init__(self, root, engine, arm, criteria, built):
        self.root = root
        repo = root / "fixture"
        shutil.copytree(HERE / "fixture", repo, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        (repo / "tests/test_web_boundary.py.in").rename(repo / "tests/test_web_boundary.py")
        files = {"AGENTS.md": "Use the offline fixture.", "REFACTORING_SUPREME_DIRECTIVE.md": "Stay in scope.",
                 "agent-context/session-handoff.md": "Start from the fixture comparison.",
                 ".gitignore": "__pycache__/\n.pytest_cache/\n",
                 "web/fixtures/views.json": json.dumps({f"view-{i}": 28 for i in range(16)})}
        if built:
            files["web/.next/view-version"] = "29\n"
        for name, data in files.items():
            p = repo / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(data)
        state = root / "script.json"
        bin_dir = root / "bin"
        bin_dir.mkdir()
        for name in ("claude", "codex"):
            exe = bin_dir / name
            shutil.copyfile(HERE / "model.py", exe)
            exe.chmod(0o755)
        clean_env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        self.env = {**clean_env, "PATH": os.pathsep.join([str(bin_dir), str(Path(sys.executable).parent), os.environ["PATH"]]),
                    "PYTHONPATH": str(engine), "PYTHONDONTWRITEBYTECODE": "1", "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
                    "PYTEST_ADDOPTS": "", "P0_SCRIPT": str(state), "P0_CRITERIA": str(len(criteria)),
                    "GIT_AUTHOR_DATE": "2026-01-01T00:00:00Z", "GIT_COMMITTER_DATE": "2026-01-01T00:00:00Z",
                    "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull}
        for args in [["init", "-qb", "main"], ["add", "."],
                     ["-c", "user.name=Fixture", "-c", "user.email=p0@example.invalid", "commit", "-qm", "fixture"]]:
            subprocess.run(["git", "-C", str(repo), *args], env=self.env, check=True)
        self.base = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], env=self.env, text=True).strip()
        self.state = state
        profile = {"project": str(repo), "allowed_paths": ["web/fixtures"], "forbidden_paths": ["scripts", "tests"],
                   "owner": "claude", "reviewer": "codex", "checks": [GEN, PYTEST]}
        if arm == "candidate":
            shipped = json.loads((engine / "profiles/katakuri-web-fixtures.json").read_text())
            if shipped["checks"] != profile["checks"]:
                raise ValueError("The shipped check commands differ from the fixed trial commands")
            profile["criterion_checks"] = shipped["criterion_checks"]
        config = root / "profile.json"
        config.write_text(json.dumps(profile))
        self.cli("profile", "save", "p0", "--file", str(config))
        flags = [part for c in criteria for part in ("--criterion", c)]
        created = self.cli("katakuri-goal", "create", "P0", "--profile", "p0", "--goal", "검증 가능한 오프라인 웹 변경", *flags)
        self.work = Path(created["project"])

    def cli(self, *args, codes=(0,)):
        cmd = [sys.executable, "-m", "agent_workflow", "--home", str(self.root / "ledger"), *args]
        result = subprocess.run(cmd, cwd=self.root, env=self.env, text=True, capture_output=True, timeout=60)
        if result.returncode not in codes:
            raise RuntimeError(f"{args}: exit {result.returncode}\n{result.stdout}\n{result.stderr}")
        if args[:2] == ("katakuri-goal", "run"):
            return result
        return json.loads(result.stdout)

    def run(self, actions, codes=(0,)):
        self.state.write_text(json.dumps(actions))
        return self.cli("katakuri-goal", "run", "P0", codes=codes)

    def status(self):
        return self.cli("katakuri-goal", "status", "P0")

    def observe(self):
        status = self.status()
        verification = self.cli("show", "P0")["task"].get("verification") or {}
        checks = []
        for result in verification.get("results", []):
            item = {"check": "fixtures" if result["argv"] == GEN else "web-boundary", "exit": result["exit_code"]}
            if result.get("result_id"):
                item["result_id"] = result["result_id"]
            if result["argv"] == PYTEST:
                report = Path(result["stdout"]).parent / "junit.xml"
                cases = ET.parse(report).findall(".//testcase")
                selected = [c for c in cases if c.get("classname") + "::" + c.get("name") == TESTCASE]
                if len(selected) != 1:
                    raise RuntimeError("The trial's real pytest report did not contain exactly one target")
                case = selected[0]
                item["bundle_test"] = next((tag for tag in ("skipped", "failure", "error")
                                            if case.find(tag) is not None), "passed")
            checks.append(item)
        return {"status": status["status"], "phase": status["phase"], "agent_calls": status["agent_calls"],
                "states": [c["state"] for c in status["criteria"]], "checks": checks,
                "criterion_links": observe_links(status, verification),
                "fixture_matches": fixture_matches(self.work), "bundle_matches": bundle_matches(self.work)}


def run_trial(root, engine, arm, case, variant):
    criteria = {"partial": [FIXTURE, BUNDLE], "interrupted": [FIXTURE],
                 "external": [FIXTURE, EXTERNAL], "regression": [FIXTURE, BUNDLE]}[case]
    built = case == "regression" or (case == "partial" and variant == "valid")
    trial = Trial(root, engine, arm, criteria, built)
    checkpoints = []
    if case == "partial":
        result = trial.run([action(1, version=29), action(2)], codes=(0, 4))
    elif case == "interrupted":
        stopped = trial.run([action(1, version=29, interrupt=True)], codes=(2,))
        checkpoint = trial.observe()
        if "exit=41" not in stopped.stderr or not checkpoint["fixture_matches"] or checkpoint["agent_calls"] != 1:
            raise RuntimeError("Did not exercise process interruption after the fixture write")
        checkpoints.append(checkpoint)
        result = trial.run([action(1)])
    elif case == "external":
        trial.run([action(2, outcome="waiting"), action(1, version=29)], codes=(4,))
        checkpoints.append(trial.observe())
        trial.run([], codes=(4,))
        checkpoints.append(trial.observe())
        evidence = root / "operator.txt"
        evidence.write_text("operator observed sample 17")
        trial.cli("katakuri-goal", "supply", "P0", "--criterion", "2", "--ref", str(evidence))
        result = trial.run([action(2, needs_observation=True)])
    else:
        if variant == "regressed":
            # Approve A, implement B while invalidating A, then propose the same tree for verification again.
            result = trial.run([action(1, version=29), action(2, version=28), action(1), action(2)], codes=(4,))
        else:
            result = trial.run([action(1, version=29), action(2, version=29, alternative=True)])
    final = trial.observe()
    writes = [json.loads(s)["fixture"] for s in (root / "writes.jsonl").read_text().splitlines()]
    completed = final["status"] == "completed"
    valid_files = final["fixture_matches"] and (BUNDLE not in criteria or final["bundle_matches"])
    expects_completion = variant not in {"missing_bundle", "regressed"}
    assertions = {"expected_outcome": completed == expects_completion, "correct_completion": completed == valid_files}
    if not valid_files:
        assertions["yielded_without_completion"] = result.returncode == 4 and final["phase"] == "waiting"
    if case == "interrupted":
        # P0 requested a second owner response. P1 can send the preserved change
        # straight to review. Both must charge every real model process exactly once.
        process_records = list((root / "ledger/tasks/P0/runs").glob("*/process.json"))
        assertions["one_write_and_durable_budget"] = (
            writes == [29] and final["agent_calls"] == len(process_records)
            and 2 <= len(process_records) <= 3
        )
    if case == "external":
        assertions["independent_work_then_sleep"] = all(c["phase"] == "waiting" and c["states"] == ["proposed", "waiting"]
                                                        and c["fixture_matches"] and c["checks"]
                                                        and c["agent_calls"] == 2 for c in checkpoints)
        assertions["resumed_with_supplied_input"] = final["agent_calls"] == 4 and completed
    if case == "regression" and variant == "regressed":
        assertions["final_tree_checked"] = writes == [29, 28] and any(c["exit"] != 0 for c in final["checks"])
    return {"case": case, "variant": variant, "fixture_commit": trial.base, "checkpoints": checkpoints,
            "final": final, "file_writes": writes, "assertions": assertions, "pass": all(assertions.values()),
            "false_completion": completed and not valid_files, "false_block": not completed and valid_files,
            "expected_final_phase": "complete" if expects_completion else "waiting"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", type=Path, required=True)
    parser.add_argument("--arm", choices=["baseline", "candidate"], required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    engine = args.engine.resolve()
    variants = {"partial": ["missing_bundle", "valid"], "interrupted": ["resume"],
                "external": ["supply"], "regression": ["regressed", "valid_alternative"]}
    rows = []
    with tempfile.TemporaryDirectory(prefix="aw-p0-") as tmp:
        for case, choices in variants.items():
            for variant in choices:
                rows.append(run_trial(Path(tmp) / f"{case}-{variant}", engine, args.arm, case, variant))
    bindings = [link for row in rows for link in row["final"]["criterion_links"]]
    inputs = [p for p in HERE.rglob("*") if p.name.endswith((".py", ".py.in"))]
    report = {"arm": args.arm, "reference_commit": REFERENCE_COMMIT,
              "engine_python_sha256": digest((engine / "agent_workflow").glob("*.py"), engine),
              "evaluator_sha256": digest(inputs, HERE), "final_criterion_links": len(bindings),
              "verified_final_links": sum(link["matches_receipt_and_audit"] for link in bindings),
              "python": sys.version.split()[0], "pytest": subprocess.check_output(
                  [sys.executable, "-m", "pytest", "--version"], text=True).strip(),
              "cases_passed": sum(all(r["pass"] for r in rows if r["case"] == c) for c in variants),
              "trials_passed": sum(r["pass"] for r in rows), "false_completions": sum(r["false_completion"] for r in rows),
              "false_blocks": sum(r["false_block"] for r in rows),
              "correct_terminal_waits": sum(r["expected_final_phase"] == r["final"]["phase"] == "waiting" for r in rows),
              "driver_resumes_after_interruption": sum(c["phase"] == "stopped" for r in rows for c in r["checkpoints"]),
              "trials": rows}
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(f"{args.arm}: {report['cases_passed']}/4 cases, {report['trials_passed']}/6 trials, "
          f"{report['false_completions']} false completions")
    if args.arm == "candidate" and report["cases_passed"] != 4:
        sys.exit(1)
    if args.arm == "candidate" and (len(bindings) != 10 or report["verified_final_links"] != 10):
        sys.exit("One or more final criterion receipts did not resolve")


if __name__ == "__main__":
    main()
