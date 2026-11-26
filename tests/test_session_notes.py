from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from agent_workflow import session_notes
from agent_workflow.core import Store, WorkflowError, create_task


class SessionNotesTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name)
        self.home = self.base / "state"
        self.project = self.base / "app"
        self.project.mkdir()
        self.git("init", "-b", "main")
        self.git("config", "user.email", "tester@example.invalid")
        self.git("config", "user.name", "Tester")
        (self.project / "app.py").write_text("original\n")
        self.git("add", "app.py")
        self.git("commit", "-qm", "first")

    def git(self, *args):
        subprocess.run(["git", "-C", str(self.project), *args], check=True, capture_output=True)

    def close(self, summary="작업 요약", next_action="다음 작업", worklog_dir=None):
        return session_notes.close_note(self.home, self.project, summary, next_action, worklog_dir)

    def pickup(self, path=None):
        return session_notes.pickup(self.home, path or self.project)

    def test_roundtrip_with_drift_and_no_source_contents(self):
        sensitive = "never-store-fixture-secret"
        sub = self.project / "dir"
        sub.mkdir()
        (sub / "a\n b.txt").write_text(sensitive)
        (self.project / "app.py").write_text(sensitive)
        self.git("add", "app.py")
        (self.project / "app.py").write_text(sensitive + "edited")
        self.git("config", "status.showUntrackedFiles", "no")
        closed = self.close()
        self.assertTrue(closed["saved"])
        self.assertEqual(closed["git"]["dirty_paths"], [
            {"status": "MM", "path": "app.py"}, {"status": "??", "path": "dir/a\n b.txt"}])
        self.assertEqual(closed["git"]["dirty_count"], 2)
        for key in ("worklog", "resume"):
            path = Path(closed[key])
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(path.parent.stat().st_mode & 0o777, 0o700)
            text = path.read_text()
            self.assertIn("작업 요약", text)
            self.assertIn("다음 작업", text)
            self.assertNotIn(sensitive, text)
        state = self.pickup(sub)
        self.assertFalse(state["dirty_paths_changed"])
        self.assertFalse(state["head_changed"])
        self.assertEqual(state["worklog"], closed["worklog"])
        (sub / "a\n b.txt").unlink()
        self.git("commit", "-qam", "change")
        state = self.pickup()
        self.assertTrue(state["dirty_paths_changed"])
        self.assertTrue(state["head_changed"])
        self.assertEqual(state["current"]["dirty_count"], 0)

    def test_empty_pickup_writes_nothing_and_works_before_first_commit(self):
        fresh = self.base / "new"
        subprocess.run(["git", "init", "-b", "first", str(fresh)], check=True, capture_output=True)
        result = self.pickup(fresh)
        self.assertFalse(result["found"])
        self.assertIsNone(result["current"]["head"])
        self.assertEqual(result["current"]["branch"], "first")
        self.assertFalse(self.home.exists())
        self.assertFalse((fresh / ".git/index").exists())

    def test_branch_worktree_and_detached_head_are_separate(self):
        first = self.close()
        self.git("switch", "-qc", "topic")
        self.assertFalse(self.pickup()["found"])
        other = self.close()
        linked = self.base / "linked"
        self.git("worktree", "add", "-q", "--detach", str(linked))
        state = session_notes.close_note(self.home, linked, "linked", "next")
        self.assertIsNone(state["git"]["branch"])
        self.git("switch", "--detach")
        self.assertFalse(self.pickup()["found"])
        self.git("switch", "main")
        self.assertEqual(self.pickup()["worklog"], first["worklog"])
        self.assertNotEqual(first["resume"], other["resume"])

    def test_context_documents_available_on_first_pickup_without_copying_contents(self):
        context = self.project / "agent-context"
        (context / "_handoff").mkdir(parents=True)
        names = ["_handoff/context-index.md", "session-summary.md", "todos.md"]
        for name in names:
            (context / name).write_text("sensitive fixture contents")
        for saved in (False, True):
            if saved:
                self.close()
            state = self.pickup(context)
            self.assertEqual(state["found"], saved)
            self.assertEqual(state["project_context"], [str(context / n) for n in names])
            self.assertNotIn("sensitive fixture", repr(state))

    def test_pickup_includes_current_branch_checkpoint_and_ledger_target(self):
        from agent_workflow.connections import connect
        source = self.base / "connected ledger"
        store = Store(source)
        try:
            task = create_task(store, "DOC", self.project, "feature", ["example"], [], [],
                               "codex", "none", "local")
            task["checkpoint"]["next"] = "verify example"
            task["verification"] = {"passed": False}
            task["review"] = {"outcome": "changes_requested"}
            store.save(task, "checkpoint")
        finally:
            store.close()
        connect(self.home, self.project, [source], include_processes=False,
                include_temporary=False, include_default=False)
        result = self.pickup()
        self.assertFalse(result["found"])
        with patch.dict(os.environ, {"GIT_DIR": str(self.base / "unrelated.git")}):
            self.assertEqual(self.pickup()["registered_tasks"], result["registered_tasks"])
        row, = result["registered_tasks"]
        self.assertEqual(row["id"], "DOC")
        self.assertFalse(row["recorded_checks_passed"])
        self.assertEqual(row["next_action"], row["resume_packet"]["next_action"]["text"])
        self.assertEqual(row["resume_packet"]["next_action"]["action"], "recheck")
        classic = session_notes.pickup(self.home, self.project, classic=True)["registered_tasks"][0]
        self.assertEqual(classic["next_action"], "verify example")
        self.assertNotIn("resume_packet", classic)
        self.assertEqual(row["recorded_review"], "changes_requested")
        self.assertIn("connected ledger", row["show"])
        self.git("switch", "-qc", "another")
        self.assertEqual(self.pickup()["registered_tasks"], [])

    def register(self, task_id, *, refs=(), reviewer="none"):
        store = Store(self.home)
        self.addCleanup(store.close)
        task = create_task(store, task_id, self.project, "feature", ["example"], refs,
                           [["git", "rev-parse", "HEAD"]], "codex", reviewer, "local")
        return store, task

    def test_pickup_compares_actual_evidence_once_for_visible_tasks(self):
        from agent_workflow.runner import run_checks
        store, task = self.register("A")
        self.register("B")
        with patch.object(session_notes, "_evidence_snapshot", side_effect=AssertionError):
            self.assertEqual(self.pickup()["registered_tasks"][0]["evidence"]["verification"], "missing")
        (self.project / "app.py").write_text("dirty")
        with store.lock(self.project) as fd:
            self.assertTrue(run_checks(store, task, 10, fd))
        with patch.object(session_notes, "_evidence_snapshot", wraps=session_notes._evidence_snapshot) as observe:
            rows = {r["id"]: r for r in self.pickup()["registered_tasks"]}
            self.assertEqual(observe.call_count, 1)
        self.assertEqual(rows["B"]["evidence"]["verification"], "missing")
        self.assertEqual(rows["A"]["evidence"]["verification"], "current")
        self.assertFalse(rows["A"]["evidence"]["rerun_checks"])
        self.assertEqual(rows["A"]["evidence"]["review"], "not_required")
        (self.project / "app.py").write_text("other")
        evidence = next(r["evidence"] for r in self.pickup()["registered_tasks"] if r["id"] == "A")
        self.assertEqual(evidence["verification"], "stale")
        self.assertTrue(evidence["rerun_checks"])

    def test_tracked_submodule_changes_invalidate_verification(self):
        from agent_workflow.core import snapshot
        library = self.base / "library"
        subprocess.run(["git", "clone", "-q", str(self.project), str(library)], check=True)
        self.git("-c", "protocol.file.allow=always", "submodule", "add", "-q", str(library), "lib")
        self.git("commit", "-qm", "register library")
        store, task = self.register("SUBMODULE")
        task["verification"] = {"passed": True, "fingerprint": snapshot(self.project)["fingerprint"]}
        store.save(task, "checks_finished")
        self.assertEqual(self.pickup()["registered_tasks"][0]["evidence"]["verification"], "current")
        (self.project / "lib" / "app.py").write_text("uncommitted submodule edit")
        self.assertEqual(self.pickup()["registered_tasks"][0]["evidence"]["verification"], "stale")

    def test_reference_review_and_failure_states(self):
        from agent_workflow.core import reference_fingerprint, snapshot
        ref = self.base / "external.md"
        ref.write_text("version one")
        store, task = self.register("A", refs=[str(ref)], reviewer="claude")
        receipt = {"fingerprint": snapshot(self.project)["fingerprint"], "refs_fingerprint": reference_fingerprint(task)}
        def evidence():
            return self.pickup()["registered_tasks"][0]["evidence"]
        task["verification"] = {**receipt, "passed": False}
        store.save(task, "checks_finished")
        self.assertEqual(evidence()["verification"], "failed")
        task["verification"]["passed"] = True
        task["review"] = {**receipt, "outcome": "changes_requested", "reviewer": "claude"}
        store.save(task, "review_finished")
        self.assertEqual(evidence()["review"], "failed")
        task["review"]["outcome"] = "pass"
        store.save(task, "review_finished")
        self.assertEqual(evidence()["review"], "current")
        ref.write_text("version two")
        changed = evidence()
        self.assertEqual((changed["verification"], changed["review"]), ("stale", "stale"))
        self.assertIn("참조", changed["reason"])
        ref.unlink()
        missing_ref = evidence()
        self.assertEqual(missing_ref["verification"], "unavailable")
        self.assertIsNone(missing_ref["rerun_checks"])

    def test_release_commit_receipt_on_release_branch(self):
        from agent_workflow.core import snapshot
        store, task = self.register("A")
        (self.project / "change").write_text("version one")
        original = snapshot(self.project)["fingerprint"]
        task.update(verification={"passed": True, "fingerprint": original},
                    completed_fingerprint=original, status="completed")
        self.git("switch", "-qc", "release-A")
        self.git("add", "change")
        self.git("commit", "-qm", "release")
        current = snapshot(self.project)
        task["release"] = {"stage": "committed", "commit": current["head"], "config": {"branch": "release-A"}}
        store.save(task, "release_committed")
        self.assertNotEqual(original, current["fingerprint"])
        row, = self.pickup()["registered_tasks"]
        self.assertEqual(row["evidence"]["completion"], "current")
        self.assertTrue(row["evidence"]["bound_via_release"])
        self.assertEqual(row["release_status"]["candidate_commit"], current["head"])
        (self.project / "change").write_text("version two")
        self.assertEqual(self.pickup()["registered_tasks"][0]["evidence"]["verification"], "stale")
        self.git("switch", "-qc", "unrelated")
        self.assertEqual(self.pickup()["registered_tasks"], [])

    def test_protected_paths_and_read_only_filter(self):
        from agent_workflow.core import snapshot
        from agent_workflow.policy import protected_snapshot
        store, task = self.register("A")
        (self.project / ".gitignore").write_text("private\n")
        private = self.project / "private"
        private.write_text("first")
        task["project_config"] = {"forbidden_paths": ["private"]}
        task["baseline"]["protected_paths"] = protected_snapshot(self.project, ["private"])
        task["verification"] = {"passed": True, "fingerprint": snapshot(self.project)["fingerprint"]}
        store.save(task, "checks_finished")
        self.assertEqual(self.pickup()["registered_tasks"][0]["evidence"]["verification"], "current")
        private.write_text("other")
        row, = self.pickup()["registered_tasks"]
        self.assertEqual(row["evidence"]["verification"], "stale")
        self.assertIn("보호", row["evidence"]["reason"])
        marker = self.base / "should-not-run"
        (self.project / ".gitattributes").write_text("app.py filter=custom\n")
        self.git("config", "filter.custom.clean", f"touch '{marker}'; cat")
        (self.project / "app.py").write_text("changed")
        self.git("config", "core.fsmonitor", "false")
        row, = self.pickup()["registered_tasks"]
        self.assertFalse(marker.exists())
        self.assertEqual(row["evidence"]["verification"], "unavailable")
        self.assertIn("Git 필터", row["evidence"]["reason"])

    def test_idempotence_atomic_failure_and_concurrent_calls(self):
        with ThreadPoolExecutor(max_workers=5) as pool:
            copies = list(pool.map(lambda _: self.close(), range(5)))
        self.assertEqual(sum(note["saved"] for note in copies), 1)
        original = Path(copies[0]["resume"])
        self.assertEqual(len({note["resume"] for note in copies}), 1)
        with patch("agent_workflow.session_notes._markdown", side_effect=OSError("disk error")):
            with self.assertRaisesRegex(OSError, "disk error"):
                self.close("second")
        self.assertEqual(self.pickup()["summary"], "작업 요약")
        self.assertEqual(list(original.parent.parent.glob(".pending-*")), [])
        with ThreadPoolExecutor(max_workers=5) as pool:
            updates = list(pool.map(lambda n: self.close(f"summary {n}"), range(5)))
        self.assertEqual(len({note["resume"] for note in updates}), 5)
        for note in updates:
            self.assertIn(note["summary"], Path(note["resume"]).read_text())
            self.assertIn(note["summary"], Path(note["worklog"]).read_text())
        self.assertIn("작업 요약", original.read_text())

    def test_read_only_git_disables_external_hooks_and_filters(self):
        marker = self.base / "executed"
        program = self.base / "external"
        program.write_text(f'#!/bin/sh\ntouch "{marker}"\ncat\n')
        program.chmod(0o700)
        (self.project / ".gitattributes").write_text("app.py filter=evil\n")
        (self.project / "app.py").write_text("changed and now needs filtering")
        self.git("config", "core.fsmonitor", str(program))
        self.git("config", "filter.evil.clean", str(program))
        self.git("config", "filter.evil.required", "true")
        with patch.dict(os.environ, {"GIT_DIR": str(self.base / "wrong-repo")}):
            state = self.pickup()
        self.assertEqual(state["current"]["dirty_count"], 2)
        self.assertFalse(marker.exists())

    def test_full_status_digest_covers_undisplayed_paths_and_ignores_own_notes(self):
        self.home = self.project / ".state"
        for i in range(session_notes.PATH_LIMIT + 1):
            (self.project / str(i).zfill(4)).touch()
        first = self.close()
        self.assertEqual(first["git"]["dirty_count"], session_notes.PATH_LIMIT + 1)
        self.assertEqual(len(first["git"]["dirty_paths"]), session_notes.PATH_LIMIT)
        self.assertFalse(self.pickup()["dirty_paths_changed"])
        (self.project / "zz").touch()
        state = self.pickup()
        self.assertTrue(state["dirty_paths_changed"])
        self.assertEqual(state["current"]["dirty_paths"], first["git"]["dirty_paths"])

    def test_invalid_request_does_not_create_notes(self):
        with self.assertRaises(WorkflowError):
            self.close("  ")
        with self.assertRaises(WorkflowError):
            self.pickup(self.base)
        self.assertFalse(self.home.exists())

    def test_export_reuses_note_and_preserves_user_edits_inside_project(self):
        vault = self.project / "개발 일지"
        vault.mkdir()
        first = self.close(worklog_dir=vault)
        dest = Path(first["exported_worklog"])
        self.assertEqual(dest.parent, vault)
        self.assertRegex(dest.name, r"^\d{4}-\d{2}-\d{2}_app_[0-9a-f]+\.md$")
        self.assertEqual(Path(first["worklog"]).read_bytes(), dest.read_bytes())
        second = self.close(worklog_dir=vault)
        self.assertFalse(second["saved"])
        self.assertEqual(first["exported_worklog"], second["exported_worklog"])
        dest.write_text("my edited journal")
        with self.assertRaisesRegex(WorkflowError, "로컬 작업일지"):
            self.close(worklog_dir=vault)
        self.assertEqual(list(vault.iterdir()), [dest])
        self.assertEqual(dest.read_text(), "my edited journal")
        (self.project / "other.py").touch()
        updated = self.close(worklog_dir=vault)
        self.assertTrue(updated["saved"])
        self.assertNotEqual(updated["exported_worklog"], str(dest))

    def test_export_failure_leaves_saved_note_and_can_retry(self):
        vault = self.base / "missing"
        with self.assertRaises(WorkflowError) as failure:
            self.close(worklog_dir=vault)
        local = self.pickup()
        self.assertIn(local["worklog"], str(failure.exception))
        self.assertFalse(vault.exists())
        vault.mkdir()
        with patch("agent_workflow.session_notes.os.link", side_effect=OSError("disk error")):
            with self.assertRaisesRegex(WorkflowError, "disk error"):
                self.close(worklog_dir=vault)
        self.assertEqual(list(vault.iterdir()), [])
        with ThreadPoolExecutor(max_workers=3) as pool:
            copies = list(pool.map(lambda _: self.close(worklog_dir=vault), range(3)))
        self.assertEqual([c["saved"] for c in copies], [False] * 3)
        self.assertEqual(len({c["exported_worklog"] for c in copies}), 1)
        self.assertEqual(len(list(vault.iterdir())), 1)
        self.assertEqual(local["worklog"], copies[0]["worklog"])

    def test_export_collision_preserves_existing_symlink_and_file(self):
        local = self.close()
        vault = self.base / "exports"
        vault.mkdir()
        dest = session_notes._export_path(vault, local)
        dest.symlink_to(self.project / "app.py")
        with self.assertRaisesRegex(WorkflowError, "내보내기 실패"):
            self.close(worklog_dir=vault)
        self.assertTrue(dest.is_symlink())
        self.assertEqual((self.project / "app.py").read_text(), "original\n")


if __name__ == "__main__":
    unittest.main()
