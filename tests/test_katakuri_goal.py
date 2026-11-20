import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from agent_workflow import katakuri_goal as kg, profiles
from agent_workflow.cli import dispatch, parser
from agent_workflow.core import Store, WorkflowError, complete, git
from agent_workflow.release import configure


# Real subprocess boundary; deterministic CLI doubles expose schema, role and resume errors.
MODEL = r"""#!/usr/bin/env python3
from pathlib import Path
import sys, os, json
a=sys.argv
prompt=sys.stdin.read()
root=Path(os.environ['KG_CASE'])
is_codex='--output-last-message' in a
schema=json.loads(Path(a[a.index('--output-schema')+1]).read_text() if is_codex else a[a.index('--json-schema')+1])
calls=root/'calls'
with calls.open('a') as f:f.write(('review' if 'coverage' in schema['properties'] else 'implement')+'\n')
steps=json.loads((root/'answers').read_text())
row=steps.pop(0);(root/'answers').write_text(json.dumps(steps))
if 'edit' in row:Path('app.py').write_text(row.pop('edit'))
tokens={'input_tokens':7,'output_tokens':3}
if is_codex:
 Path(a[a.index('--output-last-message')+1]).write_text(json.dumps(row))
 print(json.dumps({'type':'turn.completed','usage':tokens}))
else: print(json.dumps({'subtype':'success','structured_output':row,'usage':tokens}))
"""


def step(n, outcome="ready", **extra):
    return dict(criterion=n, outcome=outcome, summary=f"step {n}", next_action="bound record", **extra)


def review(*accepted):
    return dict(
        outcome="pass" if all(accepted) else "changes_requested",
        summary="inspected acceptances",
        next_action="resolve open condition",
        findings=[],
        coverage=[
            dict(criterion=i, accepted=v, evidence=f"app.py and check for {i}")
            for i, v in enumerate(accepted, 1)
        ],
    )


class KatakuriGoalTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.repo = self.root / "katakuri-band"
        self.repo.mkdir()
        git(self.repo, "init", "-qb", "main")
        for f in ("AGENTS.md", kg.DIRECTIVE, kg.HANDOFF, "app.py"):
            p = self.repo / f
            p.parent.mkdir(exist_ok=True)
            p.write_text("base")
        git(self.repo, "add", ".")
        git(
            self.repo,
            "-c",
            "user.name=demo",
            "-c",
            "user.email=demo@example.invalid",
            "commit",
            "-qm",
            "base",
        )
        self.home = self.root / "state"
        self.store = Store(self.home)
        self.addCleanup(self.store.close)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        for model in ("codex", "claude"):
            p = self.bin / model
            p.write_text(MODEL)
            p.chmod(0o755)
        env = patch.dict(os.environ, PATH=str(self.bin) + ":" + os.environ["PATH"], KG_CASE=str(self.root))
        env.start()
        self.addCleanup(env.stop)
        self.cfg = {
            "project": str(self.repo),
            "owner": "claude",
            "reviewer": "codex-session",
            "allowed_paths": ["app.py"],
            "checks": [
                [sys.executable, "-c", "from pathlib import Path; assert Path('app.py').read_text()=='done'"]
            ],
        }
        profiles.save(self.store, "kb", self.cfg)

    def cli(self, *argv):
        return dispatch(parser().parse_args(["--home", str(self.home), "katakuri-goal", *argv]))

    def start(self, *extra):
        return self.cli(
            "create",
            "G",
            "--profile",
            "kb",
            "--goal",
            "fix test",
            "--criterion",
            "change",
            "--criterion",
            "prove",
            *extra,
        )

    def script(self, *answers):
        (self.root / "answers").write_text(json.dumps(answers))

    def test_multi_step_across_processes_and_fresh_review(self):
        data = self.start("--max-reported-tokens", "20")
        self.script(step(2, "progress", edit="draft"), step(1, edit="done"), step(2), review(True, True))
        with self.assertRaisesRegex(WorkflowError, "토큰"):
            self.cli("run", "G")
        self.assertEqual(self.cli("status", "G")["reported_tokens"], 20)
        self.cli("budget", "G", "--max-reported-tokens", "40")
        result = self.cli("run", "G")
        self.assertEqual(
            (result["phase"], result["status"], result["agent_calls"]), ("complete", "completed", 4)
        )
        self.assertEqual(result["reported_tokens"], 40)
        self.assertEqual(len(result["last_audit"]["rows"]), 2)
        self.assertEqual((Path(data["project"]) / "app.py").read_text(), "done")
        self.assertEqual((self.repo / "app.py").read_text(), "base")
        self.assertTrue(self.store.get("G")["native_review_receipt"])

    def test_wait_does_independent_work_then_sleeps_until_input(self):
        self.start()
        self.script(step(1, "waiting"), step(2, edit="done"))
        for _ in range(2):
            with self.assertRaisesRegex(WorkflowError, "외부 근거"):
                self.cli("run", "G")
        self.assertTrue(self.store.get("G")["verification"]["passed"])
        self.assertEqual(self.cli("status", "G")["agent_calls"], 2)
        p = self.root / "receipt.md"
        p.write_text("sanitized observation")
        self.cli("supply", "G", "--criterion", "1", "--ref", str(p))
        self.script(step(1, "waiting"))
        with self.assertRaises(WorkflowError):
            self.cli("run", "G")
        with self.assertRaisesRegex(WorkflowError, "이미 제출"):
            self.cli("supply", "G", "--criterion", "1", "--ref", str(p))
        p.write_text("new observation")
        self.cli("supply", "G", "--criterion", "1", "--ref", str(p))
        self.script(step(1), review(True, True))
        self.assertEqual(self.cli("run", "G")["status"], "completed")

    def test_checks_and_targeted_review_repair(self):
        self.start()
        self.script(
            step(1),
            step(2),
            step(2, edit="done"),
            step(1),
            review(True, False),
            step(2, edit="done"),
        )
        with self.assertRaisesRegex(WorkflowError, "거절된 리뷰"):
            self.cli("run", "G")
        self.assertEqual(self.cli("status", "G")["phase"], "waiting")
        self.assertEqual((self.root / "calls").read_text().count("review"), 1)
        with self.assertRaisesRegex(WorkflowError, "외부 근거"):
            self.cli("run", "G")

    def test_no_progress_and_release_fence(self):
        self.start()
        self.script(step(2, "progress"), step(1, "waiting"))
        with self.assertRaisesRegex(WorkflowError, "외부 근거"):
            self.cli("run", "G")
        self.assertEqual(self.cli("status", "G")["agent_calls"], 2)
        with self.assertRaisesRegex(WorkflowError, "릴리스"):
            configure(self.store, "G", "origin", "main", "work/test", ["app.py"])
        with self.assertRaises(WorkflowError):
            complete(self.store, self.store.get("G"))
        with self.assertRaises(WorkflowError):
            kg.read_coverage(review(True), self.store.get("G"))
        with self.assertRaises(WorkflowError):
            kg.validate_step(step(3), self.store.get("G"))

    def test_goal_only_plans_once_across_budget_stop(self):
        initial = self.cli(
            "create",
            "G",
            "--profile",
            "kb",
            "--goal",
            "ship a working change",
            "--instrument",
            "HSI",
            "--milestone",
            "M2",
            "--max-reported-tokens",
            "10",
        )
        self.assertEqual(initial["phase"], "planning")
        self.script({"criteria": ["working change", "prove the output"]})
        with self.assertRaisesRegex(WorkflowError, "토큰"):
            self.cli("run", "G")
        stored = self.store.get("G")
        self.assertEqual(stored["goal"], "ship a working change")
        self.assertFalse(stored["katakuri_goal"]["needs_plan"])
        self.assertEqual(stored["criteria"], ["working change", "prove the output"])
        self.cli("budget", "G", "--clear-token-limit")
        self.script(step(1, edit="done"), step(2), review(True, True))
        done = self.cli("run", "G")
        self.assertEqual(done["agent_calls"], 4)
        self.assertEqual((done["instrument"], done["milestone"]), ("HSI", "M2"))
        self.assertEqual({row["state"] for row in done["criteria"]}, {"accepted"})

    def test_input_binds_to_both_model_prompts_and_queues_resume(self):
        from agent_workflow.daemon import enqueue, list_jobs, run_daemon
        import threading

        self.start()
        self.script(step(1, "waiting"), step(2, edit="done"))
        enqueue(self.store, "G")
        run_daemon(self.home, threading.Event(), once=True)
        self.assertEqual(list_jobs(self.store)[0]["status"], "waiting_for_input")
        f = self.root / "report.txt"
        f.write_text("known synthetic observation")
        self.cli("supply", "G", "--criterion", "1", "--ref", str(f), "--queue")
        self.script(step(1), review(True, True))
        run_daemon(self.home, threading.Event(), once=True)
        self.assertEqual(self.cli("status", "G")["phase"], "complete")
        paths = list((self.store.directory("G") / "runs").glob("*/prompt.txt"))
        self.assertEqual(sum("known synthetic observation" in p.read_text() for p in paths), 2)

    def test_missing_usage_and_claude_cache_accounting(self):
        self.start("--max-reported-tokens", "200")
        task = self.store.get("G")
        task["usage"] = {
            "agent_calls": 2,
            "observations": [
                {
                    "agent": "claude",
                    "tokens": {"input_tokens": 1, "output_tokens": 2, "cache_read_input_tokens": 4},
                },
                {
                    "agent": "codex",
                    "tokens": {"input_tokens": 5, "output_tokens": 3, "cached_input_tokens": 2},
                },
            ],
        }
        self.assertEqual(kg.token_usage(task), (15, 0))
        task["usage"]["agent_calls"] = 3
        with self.assertRaisesRegex(WorkflowError, "미확인"):
            kg.model_budget(task)
