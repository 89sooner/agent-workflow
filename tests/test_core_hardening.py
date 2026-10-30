"""Behavioral regressions for contracts, state races, live holds and optional delivery."""
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import threading
import unittest
from unittest.mock import patch

import test_workflow as fixtures
from agent_workflow.cli import dispatch, freshness, parser
from agent_workflow.closure import finalize
from agent_workflow.core import (Store, StaleTaskError, WorkflowError, clear_hold,
                                 complete, create_task, encode, git,
                                 reference_fingerprint, set_hold, snapshot)
from agent_workflow.daemon import _claim, enqueue, list_jobs, run_daemon
from agent_workflow.operations import notifications, publish_notification
from agent_workflow.policy import protected_snapshot, scope_check
from agent_workflow.profiles import save, validate
from agent_workflow.runner import execute, record_review, run_checks
from agent_workflow.workspaces import create_from_profile


class CoreHardeningTests(unittest.TestCase):
    setUp = fixtures.WorkflowTests.setUp
    tearDown = fixtures.WorkflowTests.tearDown
    task = fixtures.WorkflowTests.task
    checked = fixtures.WorkflowTests.checked
    fake_command = fixtures.WorkflowTests.fake_command
    result = staticmethod(fixtures.WorkflowTests.result)

    def protected_task(self, forbidden):
        save(self.store, "protected", {"project": str(self.project),
             "workspace": {"isolate": False}, "allowed_paths": ["app.py"],
             "forbidden_paths": forbidden, "reviewer": "none",
             "checks": [[sys.executable, "-c", "import app; assert app.VALUE == 1"]]})
        return create_from_profile(self.store, "PROTECTED", "protected", "preserve secrets", ["preserved"])

    def reference_task(self, path, reviewer="none", checks=None):
        return create_task(self.store, "REF", self.project, "reference contract", ["actual inputs"],
                           [str(path)], checks or [[sys.executable, "-c", "import app; assert app.VALUE == 1"]],
                           "claude", reviewer, "local fixture")

    def test_stale_save_cannot_overwrite_newer_contract_or_append_event(self):
        stale = self.task()
        second = Store(self.store.home)
        try:
            current = second.get(stale["id"])
            current["goal"] = "newer goal"
            second.save(current, "fixture_new_contract")
            before = self.store.db.execute("SELECT COUNT(*) FROM events").fetchone()[0]
            stale["goal"] = "stale goal"
            with self.assertRaises(StaleTaskError):
                self.store.save(stale, "fixture_stale_write")
            self.assertEqual(self.store.get(stale["id"])["goal"], "newer goal")
            self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM events").fetchone()[0], before)
            self.assertLess(stale["revision"], current["revision"])
        finally:
            second.close()

    def test_old_database_gets_revision_without_losing_task(self):
        home = self.root / "legacy"
        home.mkdir()
        db = sqlite3.connect(home / "state.sqlite3")
        db.execute("CREATE TABLE tasks(id TEXT PRIMARY KEY,data TEXT NOT NULL)")
        db.execute("INSERT INTO tasks VALUES (?,?)", ("OLD", json.dumps({"id": "OLD", "goal": "preserved"})))
        db.commit(); db.close()
        legacy = Store(home)
        try:
            task = legacy.get("OLD")
            self.assertEqual(task["revision"], 0)
            legacy.save(task, "migration_write")
            self.assertEqual(legacy.get("OLD")["revision"], 1)
            self.assertEqual(legacy.get("OLD")["goal"], "preserved")
        finally:
            legacy.close()

    def test_finalize_reloads_release_configuration_saved_after_completion(self):
        from agent_workflow.release import configure
        task = complete(self.store, self.checked(self.task()))
        remote = self.root / "remote.git"
        git(self.project, "init", "--bare", "-q", str(remote))
        git(self.project, "remote", "add", "origin", str(remote))
        git(self.project, "push", "-q", "origin", "HEAD:refs/heads/main")
        second = Store(self.store.home)
        try:
            configure(second, task["id"], "origin", "main", "workflow/finalize", ["app.py"], "local")
            configured = second.get(task["id"])
            self.assertGreater(configured["revision"], task["revision"])
            finalize(self.store, task)
            latest = self.store.get(task["id"])
            self.assertEqual(latest["release"]["stage"], "configured")
            self.assertEqual(latest["release"]["config"], configured["release"]["config"])
            self.assertIn("closure", latest)
            self.assertEqual(task["revision"], latest["revision"])
        finally:
            second.close()

    def test_ignored_forbidden_file_change_prevents_completion_without_content_log(self):
        with (self.project / ".gitignore").open("a") as stream:
            stream.write(".env\n")
        secret = self.project / ".env"
        secret.write_text("FIXTURE_VALUE=original-secret\n")
        task = self.checked(self.protected_task([".env"]))
        before = snapshot(self.project)["fingerprint"]
        secret.write_text("FIXTURE_VALUE=changed-secret\n")
        self.assertEqual(snapshot(self.project)["fingerprint"], before)
        self.assertFalse(freshness(task, snapshot(self.project))["verification_is_current"])
        with self.assertRaisesRegex(WorkflowError, "범위 위반"):
            complete(self.store, task)
        self.assertNotIn("original-secret", encode(task))
        self.assertNotIn("changed-secret", encode(task))

    def test_new_ignored_file_under_missing_forbidden_directory_is_detected(self):
        with (self.project / ".gitignore").open("a") as stream:
            stream.write("private/\n")
        task = self.protected_task(["private"])
        scope_check(task)
        (self.project / "private").mkdir()
        (self.project / "private/token").write_text("fixture")
        with self.assertRaisesRegex(WorkflowError, "private"):
            scope_check(task)

    def test_forbidden_mode_deletion_and_symlink_changes_are_detected(self):
        path = self.project / ".env"
        path.write_text("fixture")
        task = self.protected_task([".env"])
        mode = path.stat().st_mode & 0o777
        path.chmod(mode ^ 0o100)
        with self.assertRaises(WorkflowError):
            scope_check(task)
        path.chmod(mode)
        scope_check(task)
        path.unlink()
        with self.assertRaises(WorkflowError):
            scope_check(task)
        path.symlink_to(self.root / "outside-target")
        with self.assertRaises(WorkflowError):
            scope_check(task)

    def test_external_forbidden_symlink_is_rejected_without_reading_target(self):
        outside = self.root / "outside"
        outside.write_text("initial")
        (self.project / "private").symlink_to(outside)
        with patch.object(Path, "open", side_effect=AssertionError("must not read external contents")) as opened:
            with self.assertRaisesRegex(WorkflowError, "저장소 밖"):
                protected_snapshot(self.project, ["private"])
            opened.assert_not_called()

    def test_external_forbidden_alias_cannot_register_a_task(self):
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "settings").write_text("fixture only")
        (self.project / "private").symlink_to(outside)
        for path in ("private", "private/settings"):
            with self.subTest(path=path), self.assertRaisesRegex(WorkflowError, "저장소 밖"):
                self.protected_task([path])
        self.assertEqual(self.store.all(), [])

    def test_ignored_internal_forbidden_alias_binds_target_content_and_mode(self):
        with (self.project / ".gitignore").open("a") as stream:
            stream.write(".env\ninternal.env\n")
        target = self.project / "internal.env"
        target.write_text("FIXTURE=before")
        (self.project / ".env").symlink_to(target.name)
        task = self.checked(self.protected_task([".env"]))
        before = snapshot(self.project)["fingerprint"]
        mode = target.stat().st_mode & 0o777
        target.chmod(mode ^ 0o100)
        with self.assertRaisesRegex(WorkflowError, "범위 위반"):
            scope_check(task)
        target.chmod(mode)
        scope_check(task)
        (self.project / ".env").write_text("FIXTURE=after")
        self.assertEqual(snapshot(self.project)["fingerprint"], before)
        self.assertFalse(freshness(task, snapshot(self.project))["verification_is_current"])
        with self.assertRaisesRegex(WorkflowError, "범위 위반"):
            complete(self.store, task)

    def test_internal_forbidden_directory_alias_binds_children(self):
        target = self.project / "config"
        target.mkdir()
        (target / "settings").write_text("before")
        (self.project / "private").symlink_to(target.name)
        before = protected_snapshot(self.project, ["private"])
        (target / "settings").write_text("after")
        self.assertNotEqual(protected_snapshot(self.project, ["private"]), before)
        (target / "settings").write_text("before")
        self.assertEqual(protected_snapshot(self.project, ["private"]), before)
        (target / "new").write_text("new forbidden child")
        self.assertNotEqual(protected_snapshot(self.project, ["private"]), before)

    def test_missing_internal_alias_target_is_recorded_and_creation_detected(self):
        with (self.project / ".gitignore").open("a") as stream:
            stream.write(".env\ninternal.env\n")
        (self.project / ".env").symlink_to("internal.env")
        task = self.protected_task([".env"])
        scope_check(task)
        (self.project / "internal.env").write_text("now present")
        with self.assertRaisesRegex(WorkflowError, "범위 위반"):
            scope_check(task)

    def test_forbidden_link_cycles_are_rejected(self):
        (self.project / "first").symlink_to("second")
        (self.project / "second").symlink_to("first")
        with self.assertRaisesRegex(WorkflowError, "금지 경로"):
            self.protected_task(["first"])
        folder = self.project / "config"
        folder.mkdir()
        (folder / "parent").symlink_to("..")
        with self.assertRaisesRegex(WorkflowError, "순환"):
            protected_snapshot(self.project, ["config"])

    def test_unrelated_ignored_cache_does_not_change_protected_identity(self):
        task = self.protected_task([".env"])
        before = snapshot(self.project)["fingerprint"]
        (self.project / "__pycache__").mkdir()
        (self.project / "__pycache__/cache.pyc").write_bytes(b"fixture")
        self.assertEqual(snapshot(self.project)["fingerprint"], before)
        scope_check(task)

    def test_legacy_forbidden_path_without_baseline_is_not_silently_trusted(self):
        task = self.protected_task([".env"])
        task["baseline"].pop("protected_paths")
        with self.assertRaisesRegex(WorkflowError, "기준 증거"):
            scope_check(task)

    def test_hold_blocks_existing_queue_until_explicit_unhold(self):
        task = self.checked(self.task(reviewer="codex"))
        record_review(self.store, task, self.result(), snapshot(self.project)["fingerprint"], "codex")
        job = enqueue(self.store, task["id"])
        set_hold(self.store, task["id"], "사용자 확인 대기")
        self.assertIsNone(_claim(self.store))
        self.assertEqual(list_jobs(self.store)[0]["status"], "queued")
        restarted = Store(self.store.home)
        try:
            self.assertEqual(restarted.get(task["id"])["hold"]["reason"], "사용자 확인 대기")
        finally:
            restarted.close()
        clear_hold(self.store, task["id"])
        self.assertEqual(_claim(self.store)["id"], job["id"])
        with patch("agent_workflow.runner.command_for") as command:
            self.assertEqual(execute(self.store, task["id"])["status"], "completed")
            command.assert_not_called()

    def test_hold_is_available_while_running_and_survives_older_task_save(self):
        task = self.task()
        with self.store.lock(task["project"]):
            second = Store(self.store.home)
            try:
                set_hold(second, task["id"], "pause after current process")
                task["usage"] = {"agent_calls": 1}
                self.store.save(task, "fixture_inflight_result")
                self.assertEqual(second.get(task["id"])["hold"]["reason"], "pause after current process")
                self.assertEqual(second.get(task["id"])["usage"]["agent_calls"], 1)
            finally:
                second.close()

    def test_hold_after_implementation_stops_before_checks_or_review(self):
        import agent_workflow.runner as runner
        task = self.task(reviewer="codex")
        original = runner.process
        calls = []

        def finish_then_hold(*args, **kwargs):
            calls.append(args[0])
            result = original(*args, **kwargs)
            second = Store(self.store.home)
            try:
                set_hold(second, task["id"], "operator hold")
            finally:
                second.close()
            return result

        with patch("agent_workflow.runner.command_for", side_effect=self.fake_command), patch("agent_workflow.runner.process", side_effect=finish_then_hold):
            with self.assertRaisesRegex(WorkflowError, "보류"):
                execute(self.store, task["id"], timeout=5)
        latest = self.store.get(task["id"])
        self.assertEqual(len(calls), 1)
        self.assertIsNone(latest["verification"])
        self.assertEqual(latest["hold"]["reason"], "operator hold")
        self.assertEqual(latest["usage"]["agent_calls"], 1)

    def test_hold_blocks_finish_and_new_queue(self):
        task = self.checked(self.task())
        set_hold(self.store, task["id"], "hold")
        with self.assertRaisesRegex(WorkflowError, "보류"):
            complete(self.store, task)
        with self.assertRaisesRegex(WorkflowError, "보류"):
            enqueue(self.store, task["id"])

    def test_hold_arriving_between_preflight_and_claim_is_not_lost(self):
        task = self.task()
        enqueue(self.store, task["id"])

        def hold_during_preflight(_config):
            set_hold(self.store, task["id"], "claim race")
            return 100

        with patch("agent_workflow.policy.window_limit", side_effect=hold_during_preflight):
            self.assertIsNone(_claim(self.store))
        self.assertEqual(list_jobs(self.store)[0]["status"], "queued")

    def test_cli_hold_and_unhold_preserve_retryable_blocked_status(self):
        task = self.task()
        task.update(status="blocked", blocker="retryable failure")
        self.store.save(task, "fixture_blocked")
        args = ["--home", str(self.store.home)]
        held = dispatch(parser().parse_args([*args, "hold", task["id"], "--reason", "pending scope"]))
        self.assertEqual(held["status"], "blocked")
        unheld = dispatch(parser().parse_args([*args, "unhold", task["id"]]))
        self.assertIsNone(unheld["hold"])
        self.assertEqual(unheld["blocker"], "retryable failure")
        self.assertEqual(enqueue(self.store, task["id"])["status"], "queued")

    def test_external_reference_change_invalidates_proofs_and_reruns_agents(self):
        ref = self.root / "requirements.md"
        ref.write_text("first reference")
        task = self.checked(self.reference_task(ref, reviewer="codex"))
        fp = snapshot(self.project)["fingerprint"]
        record_review(self.store, task, self.result(), fp, "codex", reference_fingerprint(task))
        ref.write_text("changed reference")
        self.assertEqual(snapshot(self.project)["fingerprint"], fp)
        self.assertFalse(freshness(task, snapshot(self.project))["verification_is_current"])
        with self.assertRaises(WorkflowError):
            complete(self.store, task)
        with patch("agent_workflow.runner.command_for", side_effect=self.fake_command) as command:
            result = execute(self.store, task["id"], timeout=5, repairs=0)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(command.call_count, 2)

    def test_reference_symlink_retarget_and_deletion_invalidate_proofs(self):
        first, second = self.root / "first.md", self.root / "second.md"
        first.write_text("same text"); second.write_text("same text")
        link = self.root / "current.md"
        link.symlink_to(first)
        task = self.checked(self.reference_task(link))
        self.assertEqual(task["refs"], [str(link)])
        link.unlink(); link.symlink_to(second)
        self.assertFalse(freshness(task, snapshot(self.project))["verification_is_current"])
        link.unlink()
        self.assertFalse(freshness(task, snapshot(self.project))["verification_is_current"])
        with self.assertRaisesRegex(WorkflowError, "참조 문서"):
            run_checks(self.store, task, 5, ())

    def test_reference_changed_by_check_does_not_pass(self):
        ref = self.root / "requirements.md"
        ref.write_text("first")
        command = [sys.executable, "-c", "from pathlib import Path; Path(" + repr(str(ref)) + ").write_text('changed')"]
        task = self.reference_task(ref, checks=[command])
        self.assertFalse(run_checks(self.store, task, 5, ()))
        self.assertTrue(task["verification"]["references_changed_during_checks"])
        self.assertFalse(task["verification"]["code_changed_during_checks"])

    def test_manual_review_requires_reference_digest_and_legacy_proof_is_stale(self):
        ref = self.root / "requirements.md"
        ref.write_text("fixture")
        task = self.checked(self.reference_task(ref, reviewer="codex"))
        fp = snapshot(self.project)["fingerprint"]
        with self.assertRaisesRegex(WorkflowError, "문서 지문"):
            record_review(self.store, task, self.result(), fp, "codex")
        record_review(self.store, task, self.result(), fp, "codex", reference_fingerprint(task))
        task["verification"].pop("refs_fingerprint")
        self.assertFalse(freshness(task, snapshot(self.project))["verification_is_current"])
        with self.assertRaises(WorkflowError):
            complete(self.store, task)

    def test_malformed_profile_types_report_field_errors(self):
        for key in ("owner", "reviewer"):
            for value in (None, [], {}, 123, True):
                with self.subTest(key=key, value=value), self.assertRaisesRegex(WorkflowError, key):
                    validate({"project": str(self.project), key: value})
        for value in (None, [], {}, 123, True, "http://127.0.0.1:wrong"):
            with self.subTest(ready_url=value), self.assertRaisesRegex(WorkflowError, "ready_url"):
                validate({"project": str(self.project), "services": [{"name": "web", "argv": ["python3"], "port": 3333, "ready_url": value}]})

    def test_profile_cli_reports_type_errors_without_traceback(self):
        for invalid in ({"owner": []}, {"reviewer": {}}, {"services": [{"name": "web", "argv": ["python3"], "port": 3333, "ready_url": 123}]}):
            with self.subTest(invalid=invalid):
                path = self.root / "profile.json"
                path.write_text(json.dumps({"project": str(self.project), **invalid}))
                result = subprocess.run([sys.executable, "-m", "agent_workflow", "--home", str(self.store.home), "profile", "save", "invalid", "--file", str(path)],
                                        cwd=Path(__file__).resolve().parent.parent, capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, 2)
                self.assertIn("agent-workflow:", result.stderr)
                self.assertNotIn("Traceback", result.stderr)

    def test_notification_preparation_failure_does_not_stop_queue(self):
        first = self.task("FIRST")
        second = self.task("SECOND")
        first["project_config"] = {"notifications": {"command": [sys.executable, "-c", "pass"]}}
        self.store.save(first, "fixture_notifications")
        (self.store.directory("FIRST") / "notifications").write_text("path collision")
        enqueue(self.store, first["id"]); enqueue(self.store, second["id"])
        stop = threading.Event()
        original_process = subprocess.Popen
        original_publish = publish_notification

        def fake_process(command, **kwargs):
            return original_process([sys.executable, "-c", "pass"], **kwargs)

        def publish_then_stop(store, task, job=None):
            result = original_publish(store, task, job)
            if task["id"] == "SECOND":
                stop.set()
            return result

        with patch("agent_workflow.daemon.subprocess.Popen", side_effect=fake_process), patch("agent_workflow.operations.publish_notification", side_effect=publish_then_stop):
            self.assertEqual(run_daemon(self.store.home, stop), 2)
        self.assertEqual({j["status"] for j in list_jobs(self.store)}, {"succeeded"})
        item = next(n for n in notifications(self.store) if n["payload"]["task_id"] == "FIRST")
        self.assertEqual(item["delivery"], "failed")
        self.assertIn("error", item)

    def test_notification_database_failures_are_not_swallowed(self):
        task = self.task()
        with patch("agent_workflow.operations.init_notifications", side_effect=sqlite3.OperationalError("disk full")):
            with self.assertRaises(sqlite3.OperationalError):
                publish_notification(self.store, task)


if __name__ == "__main__":
    unittest.main()
