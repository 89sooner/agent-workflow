"""Identity and project summaries use isolated ledgers; no model or worker starts."""
import contextlib
import json
import os
import unittest
from unittest.mock import patch

import test_workflow as fixtures
from agent_workflow.core import Store, WorkflowError
from agent_workflow.daemon import enqueue
from agent_workflow.observability import process_span, project_view, run_context, view


class ProjectObservationTests(unittest.TestCase):
    setUp = fixtures.WorkflowTests.setUp
    tearDown = fixtures.WorkflowTests.tearDown
    task = fixtures.WorkflowTests.task

    def span(self, task, role="implement", model=None):
        return process_span(self.store, task, self.store.home / "tasks" / task["id"] / "runs" / "fixture",
                            ["fixture"], agent=task["owner"], role=role, model=model)

    def test_same_provider_and_name_are_distinct_live_invocations(self):
        first, second = self.task("FIRST"), self.task("SECOND")
        for task in (first, second):
            task["agent_names"] = {"implement": "Katakuri 구현 담당"}
            self.store.save(task, "assigned")
        with contextlib.ExitStack() as stack:
            one, two = stack.enter_context(self.span(first)), stack.enter_context(self.span(second))
            observed = project_view(self.store, self.project)
            agents = [actor for actor in observed["actors"] if actor["kind"] == "agent"]
            self.assertEqual(len(agents), 2)
            self.assertEqual(len({actor["id"] for actor in agents}), 2)
            self.assertEqual({actor["label"] for actor in agents}, {"Katakuri 구현 담당"})
            self.assertEqual({actor["name_source"] for actor in agents}, {"assigned"})
            self.assertEqual(observed["counts"]["running"], 2)
            self.assertEqual(observed["counts"]["agents_running"], 2)
            self.assertTrue(all(actor["native_session_id"] is None for actor in agents))
            self.assertEqual(len({actor["id"] for actor in observed["actors"]}), len(observed["actors"]))
            ids = {actor["id"] for actor in observed["actors"]}
            self.assertTrue(all(span["actor"] in ids for span in observed["spans"]))
            self.assertTrue(all(event["actor"] in ids for event in observed["events"]))
            one["outcome"] = two["outcome"] = {"exit_code": 0}

    def test_assignment_name_and_requested_model_are_historical_snapshots(self):
        task = self.task()
        task["agent_names"] = {"implement": "봉 데이터 담당"}
        with self.span(task, model="requested-model") as span:
            span["outcome"] = {"exit_code": 0}
        task["agent_names"] = {"implement": "새 담당자"}
        task["owner"] = "codex"
        self.store.save(task, "assigned")
        agent = next(actor for actor in view(self.store, task["id"])["actors"] if actor["kind"] == "agent")
        self.assertEqual(agent["label"], "봉 데이터 담당")
        self.assertEqual(agent["provider"], "claude")
        self.assertEqual(agent["model"], "requested-model")
        self.assertIsNone(agent["observed_model"])
        self.assertEqual(agent["name_source"], "assigned")

    def test_legacy_span_names_are_generated_not_current_assignments(self):
        task = self.task()
        with self.span(task) as span:
            span["outcome"] = {"exit_code": 0}
        row = self.store.db.execute("SELECT id,data FROM observation_spans").fetchone()
        data = json.loads(row["data"])
        data.pop("agent_identity")
        with self.store.db:
            self.store.db.execute("UPDATE observation_spans SET data=? WHERE id=?", (json.dumps(data), row["id"]))
        task["agent_names"] = {"implement": "지금만 적용할 이름"}
        self.store.save(task, "assigned")
        agent = next(actor for actor in view(self.store, task["id"])["actors"] if actor["kind"] == "agent")
        self.assertEqual(agent["name_source"], "generated")
        self.assertNotIn("지금만", agent["label"])

    def test_queue_job_and_linked_runs_count_once_beside_direct_run(self):
        task = self.task()
        job = enqueue(self.store, task["id"])
        with self.store.db:
            self.store.db.execute("UPDATE jobs SET status='running' WHERE id=?", (job["id"],))
        with patch.dict(os.environ, {"AGENT_WORKFLOW_JOB_ID": job["id"]}):
            with run_context(self.store, task, "pipeline"):
                with self.span(task) as span:
                    span["outcome"] = {"exit_code": 0}
            with run_context(self.store, task, "release"):
                pass
        with self.store.db:
            self.store.db.execute("UPDATE jobs SET status='succeeded' WHERE id=?", (job["id"],))
        with self.span(task) as span:
            span["outcome"] = {"exit_code": 0}
        observed = project_view(self.store)
        self.assertEqual(len(observed["runs"]), 3)
        self.assertEqual(len(observed["executions"]), 2)
        queued = next(item for item in observed["executions"] if item["source"] == "queue")
        self.assertEqual(queued["job_id"], job["id"])
        self.assertEqual(len(queued["run_ids"]), 2)
        self.assertEqual(observed["counts"]["running"], 0)

    def test_abandoned_runs_and_unverified_running_queue_are_unknown(self):
        task = self.task()
        with self.span(task) as span:
            span["outcome"] = {"exit_code": 0}
        with self.store.db:
            self.store.db.execute("UPDATE observation_runs SET status='running',owner_pid=99999999")
            self.store.db.execute("UPDATE observation_spans SET status='running',owner_pid=99999999")
        observed = project_view(self.store)
        self.assertEqual(observed["counts"]["running"], 0)
        self.assertEqual(observed["counts"]["unknown"], 1)
        self.assertEqual(next(actor for actor in observed["actors"] if actor["kind"] == "agent")["status"], "unknown")
        job = enqueue(self.store, task["id"])
        with self.store.db:
            self.store.db.execute("UPDATE jobs SET status='running',pid=? WHERE id=?", (os.getpid(), job["id"]))
        self.assertEqual(project_view(self.store)["counts"]["unknown"], 2)

    def test_read_only_empty_and_project_filter_never_initialize_jobs(self):
        task = self.task()
        before = list(self.store.db.execute("SELECT name FROM sqlite_master ORDER BY name"))
        reader = Store(self.store.home, read_only=True)
        try:
            empty = project_view(reader, self.root / "absent")
            self.assertEqual(empty["tasks"], [])
            self.assertEqual(empty["executions"], [])
            self.assertEqual(project_view(reader, self.project)["counts"]["tasks"], 1)
        finally:
            reader.close()
        self.assertEqual(before, list(self.store.db.execute("SELECT name FROM sqlite_master ORDER BY name")))
        self.assertEqual(self.store.get(task["id"])["revision"], task["revision"])

    def test_explicit_truncation_and_validated_bounds(self):
        self.task("FIRST")
        self.task("SECOND")
        observed = project_view(self.store, limit=1)
        self.assertTrue(observed["truncated"]["tasks"])
        self.assertEqual(observed["counts"]["tasks"], 2)
        self.assertEqual(len(observed["tasks"]), 1)
        for limit in (0, -1, 201, True, "1"):
            with self.assertRaises(WorkflowError):
                project_view(self.store, limit=limit)

    def test_agent_start_events_link_to_the_actual_invocation(self):
        task = self.task()
        with run_context(self.store, task, "pipeline"):
            for _ in range(2):
                self.store.save(task, "agent_started", {"agent": "claude", "role": "implement"})
                with self.span(task) as span:
                    span["outcome"] = {"exit_code": 0}
        observed = view(self.store, task["id"])
        starts = [event for event in observed["events"] if event["kind"] == "agent_started"]
        self.assertEqual([event["recipient"] for event in starts], [span["actor"] for span in observed["spans"]])
        self.assertEqual(len({actor["id"] for actor in observed["actors"] if actor["kind"] == "agent"}), 2)


if __name__ == "__main__":
    unittest.main()
