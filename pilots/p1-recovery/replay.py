"""Run the recovery acceptance cases and export their observed effects and call counts."""
import argparse
from contextlib import redirect_stdout
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
CASES = {
    "implementation": "test_local_resume.LocalResumeTests.test_owner_crash_hands_preserved_change_to_checks",
    "before_review": "test_local_resume.LocalResumeTests.test_checks_crash_resumes_at_review",
    "github": "test_release_resume.ReleaseResumeTests.test_lost_pr_creation_and_merge_responses",
    "deployment_with_version": "test_release_resume.ReleaseResumeTests.test_unknown_deployment_reconciles_version_before_health",
    "deployment_without_version": "test_release_resume.ReleaseResumeTests.test_unknown_deployment_requires_confirmation_without_observer",
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="Write JSON after all checks pass; defaults to stdout.")
    args = parser.parse_args()
    sys.path[:0] = [str(ROOT), str(ROOT / "tests")]
    report = {}
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
    for name, method in CASES.items():
        suite = unittest.defaultTestLoader.loadTestsFromName(method)
        case, = suite
        with patch.dict(os.environ, env, clear=True), redirect_stdout(sys.stderr):
            result = unittest.TextTestRunner(stream=sys.stderr, verbosity=1).run(suite)
        if not result.wasSuccessful() or result.skipped:
            raise SystemExit(f"Recovery case did not pass: {method}")
        report[name] = {"test": method, **case.replay_evidence}
    output = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(output, encoding="utf-8")
    else:
        sys.stdout.write(output)


if __name__ == "__main__":
    main()
