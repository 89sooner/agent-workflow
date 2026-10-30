"""Execution-backed observation tests; no external model or remote release calls."""
import json
import os
from pathlib import Path
import sys
import threading
import time
import unittest
from unittest.mock import patch

import test_workflow as fixtures
from agent_workflow.core import Store, WorkflowError, set_hold
from agent_workflow.observability import artifact, process_span, run_context, view
from agent_workflow.runner import execute
from agent_workflow.usage import managed_process


class ObservationTests(unittest.TestCase):
    setUp = fixtures.WorkflowTests.setUp
    tearDown = fixtures.WorkflowTests.tearDown
    task = fixtures.WorkflowTests.task
    result = staticmethod(fixtures.WorkflowTests.result)
    fake_command = fixtures.WorkflowTests.fake_command

    def test_actual_pipeline_is_one_ordered_trace_with_explicit_handoffs(self):
        task = self.task(reviewer="codex")
        with patch("agent_workflow.runner.command_for", side_effect=self.fake_command):
            execute(self.store, task["id"], timeout=5, repairs=0)
        result = view(self.store, task["id"])
        self.assertEqual(len(result["runs"]), 1)
        self.assertEqual(result["runs"][0]["status"], "succeeded")
        spans = result["spans"]
        self.assertEqual([span["role"] for span in spans], ["implement", "check", "review"])
        self.assertEqual([span["actor"] for span in spans], ["claude:implement", "checks", "codex:review"])
        self.assertEqual([span["status"] for span in spans], ["succeeded"] * 3)
        self.assertEqual(spans[2]["result"]["outcome"], "pass")
        self.assertEqual(spans[0]["result"]["outcome"], "ready")
        self.assertIsNone(spans[0]["usage"]["reported_cost_usd"])
        self.assertTrue(all(span["duration_ms"] >= 0 for span in spans))
        self.assertEqual([(edge["source"], edge["target"]) for edge in result["edges"]],
                         [(spans[0]["id"], spans[1]["id"]), (spans[1]["id"], spans[2]["id"])])
        self.assertTrue(all(edge["kind"] == "handoff" for edge in result["edges"]))
        starts = [event for event in result["events"] if event["kind"] == "process_started"]
        self.assertEqual([event["recipient"] for event in starts], [span["actor"] for span in spans])
        self.assertTrue(all(event["actor"] == "controller" for event in starts))
        seq = [event["seq"] for event in result["events"]]
        self.assertEqual(seq, sorted(set(seq)))
        self.store.close(); self.store = Store(self.root / "state")
        self.assertEqual(view(self.store, task["id"])["selected_run_id"], result["selected_run_id"])

    def test_review_rejection_and_repair_preserve_each_attempt(self):
        task = self.task(reviewer="codex")
        reviews = []
        def command(agent, role, directory, model=None):
            if role != "review":
                return self.fake_command(agent, role, directory, model)
            reviews.append(1)
            answer = self.result("changes_requested" if len(reviews) == 1 else "pass")
            return [sys.executable, "-c", "from pathlib import Path; import sys; sys.stdin.read(); Path(sys.argv[1]).write_text(sys.argv[2])",
                    str(directory / "answer.json"), json.dumps(answer)]
        with patch("agent_workflow.runner.command_for", side_effect=command):
            execute(self.store, task["id"], timeout=5, repairs=1)
        result = view(self.store, task["id"])
        self.assertEqual([span["role"] for span in result["spans"]], ["implement", "check", "review"] * 2)
        self.assertEqual(result["spans"][2]["result"]["outcome"], "changes_requested")
        self.assertEqual(result["spans"][5]["result"]["outcome"], "pass")
        self.assertIn("리뷰 피드백 → 재구현", [edge["label"] for edge in result["edges"]])
        self.assertEqual(len(result["runs"]), 1)

    def test_live_process_is_visible_and_hold_event_is_linked_without_losing_state(self):
        task = self.task()
        marker = self.root / "started"
        release = self.root / "continue"
        errors = []
        def worker():
            store = Store(self.store.home)
            try:
                current = store.get(task["id"])
                directory = store.directory(task["id"]) / "checks/live"
                code = "from pathlib import Path; import sys,time; Path(sys.argv[1]).touch(); end=time.monotonic()+5\nwhile not Path(sys.argv[2]).exists() and time.monotonic()<end: time.sleep(.02)"
                managed_process(store, current, [sys.executable, "-c", code, str(marker), str(release)],
                                self.project, directory, 6, None, role="check")
            except BaseException as exc:
                errors.append(exc)
            finally:
                store.close()
        thread = threading.Thread(target=worker)
        thread.start()
        try:
            deadline = time.monotonic() + 4
            while not marker.exists() and time.monotonic() < deadline:
                time.sleep(.02)
            self.assertTrue(marker.exists())
            live = view(self.store, task["id"])
            self.assertEqual(live["spans"][0]["status"], "running")
            self.assertIsNone(live["spans"][0]["ended_at"])
            set_hold(self.store, task["id"], "사람이 확인 중")
            held = view(self.store, task["id"])
            self.assertEqual(held["task"]["hold"]["reason"], "사람이 확인 중")
            self.assertIn("task_held", [event["kind"] for event in held["events"]])
            self.assertEqual(held["spans"][0]["status"], "running")
        finally:
            release.touch(); thread.join(timeout=8)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(view(self.store, task["id"])["spans"][0]["status"], "succeeded")

    def test_timeout_is_failed_and_abandoned_process_is_unknown(self):
        task = self.task()
        directory = self.store.directory(task["id"]) / "checks/timeout"
        result = managed_process(self.store, task, [sys.executable, "-c", "import time; time.sleep(10)"],
                                 self.project, directory, .1, None, role="check")
        self.assertTrue(result["timed_out"])
        observed = view(self.store, task["id"])
        self.assertEqual(observed["spans"][0]["status"], "failed")
        with self.store.db:
            self.store.db.execute("UPDATE observation_spans SET status='running',ended_at=NULL,owner_pid=99999999")
            self.store.db.execute("UPDATE observation_runs SET status='running',ended_at=NULL,owner_pid=99999999")
        interrupted = view(self.store, task["id"])
        self.assertEqual(interrupted["spans"][0]["status"], "unknown")
        self.assertEqual(interrupted["runs"][0]["status"], "unknown")
        self.assertTrue(any("성공으로 간주하지" in item for item in interrupted["warnings"]))

    def test_raw_prompt_and_stdout_are_not_exported_and_log_is_explicit_bounded(self):
        task = self.task()
        directory = self.store.directory(task["id"]) / "checks/private"
        managed_process(self.store, task, [sys.executable, "-c", "print('PRIVATE_LOG_' + 'x'*70000)"],
                        self.project, directory, 5, None, input_text="PRIVATE_PROMPT", role="check")
        observed = view(self.store, task["id"], exporting=True)
        exported = json.dumps(observed)
        self.assertNotIn("PRIVATE_LOG", exported)
        self.assertNotIn("PRIVATE_PROMPT", exported)
        span = observed["spans"][0]
        log = artifact(self.store, span["id"], "stdout")
        self.assertTrue(log["truncated"])
        self.assertLessEqual(len(log["text"]), 65536)
        with self.assertRaises(WorkflowError):
            artifact(self.store, span["id"], "../../project/app.py")
        (directory / "stdout.log").unlink()
        (directory / "stdout.log").symlink_to(self.project / "app.py")
        with self.assertRaisesRegex(WorkflowError, "작업 밖"):
            artifact(self.store, span["id"], "stdout")

    def test_events_page_without_loss_or_duplicates_even_at_same_timestamp(self):
        task = self.task()
        with run_context(self.store, task, "fixture"):
            for index in range(37):
                self.store.save(task, "checkpoint", {"summary": f"사건 {index}"})
        latest = view(self.store, task["id"], limit=7)
        ids, page = [], latest
        while True:
            ids.extend(event["seq"] for event in page["events"])
            if not page["pagination"]["has_more"]:
                break
            page = view(self.store, task["id"], run_id=latest["selected_run_id"], before=page["pagination"]["next_before"], limit=7)
        self.assertEqual(len(ids), 39)
        self.assertEqual(len(set(ids)), 39)
        self.assertEqual(len(view(self.store, task["id"], exporting=True)["events"]), 39)

    def test_large_graph_is_bounded_and_keeps_recent_activity(self):
        task = self.task()
        directory = self.store.directory(task["id"]) / "checks/many"
        with run_context(self.store, task, "fixture"):
            for _ in range(255):
                with process_span(self.store, task, directory, ["fixture"], role="check") as span:
                    span["outcome"] = {"exit_code": 0, "timed_out": False}
        result = view(self.store, task["id"])
        self.assertEqual(len(result["spans"]), 250)
        self.assertTrue(result["pagination"]["spans_truncated"])
        self.assertEqual(result["spans"][-1]["sequence"], 255)
        self.assertEqual(len(view(self.store, task["id"], exporting=True)["spans"]), 255)

    def test_legacy_events_are_not_reinvented_as_spans(self):
        task = self.task()
        for index in range(4):
            self.store.save(task, "checkpoint", {"summary": str(index)})
        with self.store.db:
            self.store.db.execute("DELETE FROM observation_events")
        set_hold(self.store, task["id"], "새 버전의 보류")
        data = view(self.store, task["id"])
        self.assertTrue(data["legacy"])
        self.assertEqual(data["spans"], [])
        self.assertEqual(len(data["events"]), 6)
        self.assertTrue(all(event["span_id"] is None for event in data["events"]))

    def test_trace_query_rejects_cross_task_run_and_unbounded_arguments(self):
        first = self.task()
        second = self.task("SECOND")
        with run_context(self.store, first, "fixture"):
            pass
        run_id = view(self.store, first["id"])["selected_run_id"]
        with self.assertRaisesRegex(WorkflowError, "이 작업"):
            view(self.store, second["id"], run_id)
        for invalid in (0, 501, True, -1):
            with self.assertRaises(WorkflowError):
                view(self.store, first["id"], limit=invalid)
        with self.assertRaises(WorkflowError):
            view(self.store, first["id"], before=0)


if __name__ == "__main__":
    unittest.main()
