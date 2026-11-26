"""Prepare local synthetic Git commits, immutable case labels and a registered check profile."""
from __future__ import annotations

import argparse
from contextlib import closing
import json
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from agent_workflow import profiles
from agent_workflow.core import Store

PROFILE = "pager"
CRITERIA = [
    "Refactor page() for readability. Preserve its tuple of (items, next cursor), including exact final pages.",
    "Continue to reject negative starts and nonpositive page sizes with ValueError.",
]
IMPLEMENTATION_REPORT = "I refactored page bounds and believe output and validation behavior are preserved."
BASE = '''def page(items, start, size):
    """Return a page and a continuation cursor, or None at the end."""
    if start < 0 or size <= 0:
        raise ValueError("invalid page bounds")
    chunk = items[start:start + size]
    if start + size < len(items):
        return chunk, start + size
    return chunk, None
'''
BOUNDARY = '''def page(items, start, size):
    """Return a page and a continuation cursor, or None at the end."""
    if start < 0 or size <= 0:
        raise ValueError("invalid page bounds")
    stop = start + size
    next_start = stop if stop < len(items) else None
    return items[start:stop], next_start
'''
VALIDATION = '''def page(items, start, size):
    """Return a page and a continuation cursor, or None at the end."""
    invalid_start = start < 0
    invalid_size = size <= 0
    if invalid_start or invalid_size:
        raise ValueError("invalid page bounds")
    stop = start + size
    return items[start:stop], stop if stop < len(items) else None
'''
SHORT_CIRCUIT = '''def page(items, start, size):
    """Return a page and a continuation cursor, or None at the end."""
    if start < 0 or size <= 0:
        raise ValueError("invalid page bounds")
    stop = start + size
    chunk = items[start:stop]
    if stop < len(items):
        return chunk, stop
    return chunk, None
'''
# Only these independent controls and commits are visible in scratch project history.
# The scoring rubric remains outside that history.
CHECKS = '''import sys
from pager import page

def check(name):
    if name == "shape":
        assert page([10, 20, 30], 0, 2) == ([10, 20], 2)
    elif name == "end":
        assert page([10, 20, 30, 40], 2, 2) == ([30, 40], None)
        assert page([10], 1, 1) == ([], None)
    elif name == "invalid":
        for start, size in [(0, 0), (0, -1), (-1, 1)]:
            try:
                page([10], start, size)
            except ValueError:
                continue
            raise AssertionError((start, size))
    elif name == "archive":
        raise AssertionError("archived format fixture unavailable")
    else:
        raise ValueError(name)

if __name__ == "__main__":
    check(sys.argv[1])
'''
END_RUBRIC = {"path": "pager.py", "cues": [
    ["exact", "equal", "last page", "final page", "at the end", "<= len", "== len"],
    ["cursor", "next_start", "continuation", "another page", "extra page"],
]}
ZERO_RUBRIC = {"path": "pager.py", "cues": [
    ["size=0", "size = 0", "size == 0", "size of 0", "size of zero", "zero", "0, 0"],
    ["valueerror", "raise", "reject", "invalid", "accept", "infinite", "cursor", "loop"],
]}
CASES = (
    ("a", BOUNDARY.replace("stop < len", "stop <= len"), END_RUBRIC, False),
    ("b", VALIDATION, None, True),
    ("c", VALIDATION.replace("size <= 0", "size < 0"), ZERO_RUBRIC, True),
    ("d", BOUNDARY, None, False),
    ("e", SHORT_CIRCUIT, None, True),
)


def profile_config(repo):
    def argv(probe):
        return ["python3", "-B", "checks.py", probe]

    return {"project": str(repo), "checks": [argv("shape")], "check_groups": {
        "bounds": [argv("shape"), argv("end")], "inputs": [argv("invalid")], "archive": [argv("archive")]},
        "check_group_descriptions": {"bounds": "Slicing and continuation at end of input",
                                     "inputs": "Input validation for start and size",
                                     "archive": "Historical archive compatibility"},
        "base_check_description": "Ordinary nonterminal page with a continuation"}


def fixture_environment():
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
    return env


def prepare(root):
    """Create a fresh fixture; refuse to change any existing nonempty directory."""
    root = Path(root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    if any(root.iterdir()):
        raise ValueError("Use an empty pilot directory.")
    repo = root / "repository"
    repo.mkdir()
    env = fixture_environment()
    env.update(GIT_AUTHOR_DATE="2026-01-01T00:00:00Z", GIT_COMMITTER_DATE="2026-01-01T00:00:00Z")

    def git(*args):
        return subprocess.check_output(
            ["git", "-C", str(repo), "-c", "core.hooksPath=/dev/null", "-c", "user.name=Fixture",
             "-c", "user.email=pager@example.invalid", *args], env=env, text=True).strip()

    git("init", "-q", "-b", "main")
    (repo / "checks.py").write_text(CHECKS)
    (repo / "pager.py").write_text(BASE)
    (repo / ".gitignore").write_text("__pycache__/\n")
    git("add", ".")
    git("commit", "-qm", "Pager reference")
    base = git("rev-parse", "HEAD")
    cases = []
    for name, source, rubric, report_first in CASES:
        git("checkout", "-qb", f"sample-{name}", base)
        (repo / "pager.py").write_text(source)
        git("add", "pager.py")
        git("commit", "-qm", "Refactor pager")
        item = {"case": name, "candidate": git("rev-parse", "HEAD"), "report_first": report_first,
                "defect": rubric}
        if rubric:
            (root / f"defect-{name}.json").write_text(json.dumps(rubric, indent=2) + "\n")
        cases.append(item)
    git("checkout", "-q", "main")
    with patch.dict(os.environ, env, clear=True), closing(Store(root / "ledger")) as store:
        profiles.save(store, PROFILE, profile_config(repo))
    manifest = {"base": base, "cases": cases, "profile": PROFILE, "criteria": CRITERIA,
                "implementation_report": IMPLEMENTATION_REPORT}
    (root / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


def extend(prepared):
    """Append the follow-up commit to an existing disposable four-case pilot."""
    prepared = Path(prepared).resolve()
    manifest = json.loads((prepared / "manifest.json").read_text())
    if [c["case"] for c in manifest["cases"]] == [x[0] for x in CASES]:
        return manifest
    if [c["case"] for c in manifest["cases"]] != ["a", "b", "c", "d"]:
        raise ValueError("Expected the four-case synthetic pilot.")
    # Fetch only the new, reproducible synthetic commit. Preserve the active checkout.
    import tempfile
    with tempfile.TemporaryDirectory(prefix="pager-followup-") as temp:
        full = prepare(temp)
        if manifest["base"] != full["base"] or manifest["cases"] != full["cases"][:4]:
            raise ValueError("The previous four fixture commits or labels differ.")
        source = Path(temp) / "repository"
        repo = prepared / "repository"
        subprocess.run(["git", "-c", "core.hooksPath=/dev/null", "-C", str(repo), "fetch",
                        "--quiet", "--no-tags", str(source), "refs/heads/sample-e:refs/heads/sample-e"],
                       env=fixture_environment(), check=True)
    (prepared / "manifest.json").write_text(json.dumps(full, indent=2) + "\n")
    return full


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--prepare", type=Path)
    action.add_argument("--extend", type=Path)
    arg = parser.parse_args()
    print(json.dumps(prepare(arg.prepare) if arg.prepare else extend(arg.extend), indent=2))
