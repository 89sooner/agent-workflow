import datetime as dt
from pathlib import Path
import subprocess
import sys
import threading
import unittest
from unittest.mock import patch

import test_workflow as fixtures
from agent_workflow.core import Store, WorkflowError, complete, git
from agent_workflow.daemon import _claim, cancel, enqueue, list_jobs, run_daemon, resume_job
from agent_workflow.profiles import save as save_profile
from agent_workflow.runner import run_checks
from agent_workflow.scheduling import create, control, get, prepare_dependencies, set_dependencies, tick
from agent_workflow.workspaces import create_from_profile


class SchedulingTests(unittest.TestCase):
    setUp = fixtures.WorkflowTests.setUp
    tearDown = fixtures.WorkflowTests.tearDown
    task = fixtures.WorkflowTests.task

    def finish(self, task):
        with self.store.lock(task["project"]) as fd:
            self.assertTrue(run_checks(self.store, task, 5, fd))
            complete(self.store, task)

    def test_dependencies_block_claim_and_cycles_are_rejected(self):
        a = self.task("A"); b = self.task("B")
        set_dependencies(self.store, "B", ["A"])
        with self.assertRaisesRegex(WorkflowError, "순환"):
            set_dependencies(self.store, "A", ["B"])
        enqueue(self.store, "B")
        self.assertIsNone(_claim(self.store))
        self.finish(a)
        self.assertEqual(_claim(self.store)["task_id"], "B")

    def test_batch_picks_ready_work_then_next_and_completes(self):
        a = self.task("A"); b = self.task("B")
        set_dependencies(self.store, "B", ["A"])
        create(self.store, "batch", ["B", "A"], max_jobs=2)
        control(self.store, "batch", "start")
        tick(self.store)
        first = list_jobs(self.store)[0]
        self.assertEqual(first["task_id"], "A")
        self.finish(a)
        with self.store.db:
            self.store.db.execute("UPDATE jobs SET status='succeeded' WHERE id=?", (first["id"],))
        tick(self.store)
        second = list_jobs(self.store)[0]
        self.assertEqual(second["task_id"], "B")
        b = self.store.get("B"); prepare_dependencies(self.store, b); self.finish(b)
        with self.store.db:
            self.store.db.execute("UPDATE jobs SET status='succeeded' WHERE id=?", (second["id"],))
        tick(self.store)
        batch = get(self.store, "batch")
        self.assertEqual(batch["status"], "completed")
        self.assertEqual(batch["done"], ["A", "B"])

    def test_batch_failure_does_not_advance(self):
        self.task("A"); self.task("B")
        create(self.store, "batch", ["A", "B"]); control(self.store, "batch", "start")
        tick(self.store)
        with self.store.db:
            self.store.db.execute("UPDATE jobs SET status='failed'")
        tick(self.store)
        self.assertEqual(get(self.store, "batch")["status"], "blocked")
        self.assertEqual(len(list_jobs(self.store)), 1)

    def test_user_pause_wins_over_inflight_scheduler(self):
        self.task('A'); create(self.store,'batch',['A']); control(self.store,'batch','start')
        def pause_during_probe(task):
            control(self.store,'batch','pause')
            return False
        with patch('agent_workflow.scheduling.task_done', side_effect=pause_during_probe):
            tick(self.store)
        self.assertEqual(get(self.store,'batch')['status'],'paused')
        self.assertEqual(list_jobs(self.store),[])

    def test_pause_after_enqueue_survives_worker_receipt(self):
        self.task('A'); create(self.store,'batch',['A']); control(self.store,'batch','start')
        original=enqueue
        def pause_after_enqueue(*args,**kwargs):
            job=original(*args,**kwargs); control(self.store,'batch','pause'); return job
        with patch('agent_workflow.daemon.enqueue', side_effect=pause_after_enqueue):
            tick(self.store)
        self.assertEqual(get(self.store,'batch')['status'],'paused')
        self.assertEqual(len(list_jobs(self.store)),1)

    def test_cancel_is_not_blocked_by_dependency_probe(self):
        a=self.task('A'); self.finish(a); self.task('B')
        set_dependencies(self.store,'B',['A']); job=enqueue(self.store,'B')
        other=Store(self.store.home)
        other.db.execute('PRAGMA busy_timeout=100')
        try:
            def concurrent_cancel(*args):
                cancel(other,job['id']); return {}
            with patch('agent_workflow.scheduling.dependency_evidence',side_effect=concurrent_cancel):
                self.assertIsNone(_claim(self.store))
            self.assertEqual(list_jobs(self.store)[0]['status'],'cancelled')
        finally:
            other.close()

    def test_batch_limit_and_expiration_do_not_enqueue_more(self):
        a = self.task("A"); self.task("B")
        create(self.store, "batch", ["A", "B"], max_jobs=1); control(self.store, "batch", "start")
        tick(self.store); self.finish(a)
        with self.store.db:
            self.store.db.execute("UPDATE jobs SET status='succeeded'")
        tick(self.store)
        self.assertEqual(get(self.store, "batch")["status"], "budget_limited")
        with self.assertRaises(WorkflowError):
            control(self.store, "batch", "start")

    def test_deadline_terminates_only_the_owned_job(self):
        self.task("A")
        deadline = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=.7)).isoformat()
        enqueue(self.store, "A", deadline=deadline)
        original = subprocess.Popen
        def fake(command, **kwargs):
            return original([sys.executable, "-c", "import time; time.sleep(30)"], **kwargs)
        with patch("agent_workflow.daemon.subprocess.Popen", side_effect=fake):
            run_daemon(self.store.home, threading.Event(), once=True)
        job = list_jobs(self.store)[0]
        self.assertEqual(job["status"], "interrupted")
        self.assertIn("시간 한도", job["error"])
        with self.assertRaisesRegex(WorkflowError, "기한"):
            resume_job(self.store, job["id"])

    def test_crash_between_queue_and_receipt_keeps_batch_budget(self):
        self.task("A"); self.task("B")
        create(self.store, "batch", ["A", "B"], max_jobs=1)
        batch = control(self.store, "batch", "start")
        orphan = enqueue(self.store, "A", deadline=batch["deadline"], batch_name="batch")
        # The process ended before saving batch.active_job/history.
        tick(self.store)
        recovered = get(self.store, "batch")
        self.assertEqual(recovered["history"], [orphan["id"]])
        self.assertEqual(recovered["active_job"], orphan["id"])
        with self.store.db:
            self.store.db.execute("UPDATE jobs SET status='failed' WHERE id=?", (orphan["id"],))
        tick(self.store)
        with self.assertRaises(WorkflowError):
            resume_job(self.store, orphan["id"])
        self.assertEqual(len(list_jobs(self.store)), 1)

    def test_merged_dependency_advances_only_unused_owned_branch(self):
        save_profile(self.store, "project", {"project": str(self.project), "reviewer": "none", "checks": [[sys.executable, "-c", "import app; assert app.VALUE == 1"]]})
        parent = create_from_profile(self.store, "PARENT", "project", "parent", ["parent"])
        child = create_from_profile(self.store, "CHILD", "project", "child", ["child"])
        set_dependencies(self.store, "CHILD", ["PARENT"], "merged")
        original_head = git(self.project, "rev-parse", "HEAD")
        (Path(parent["project"]) / "parent.txt").write_text("parent change")
        git(parent["project"], "add", "parent.txt")
        git(parent["project"], "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-qm", "parent")
        # Verify and record this committed fixture as the prerequisite.
        parent["baseline"]["head"] = git(parent["project"], "rev-parse", "HEAD")
        parent["baseline"]["changed_files"] = {}
        self.finish(parent)
        parent["release"] = {"commit": parent["baseline"]["head"], "merge_commit": parent["baseline"]["head"], "stage": "merged", "config": {"deploy": None}}
        from agent_workflow.release import integration_contract
        parent["release"]["integration_verification"] = {"passed": True,
            "revision": parent["baseline"]["head"], "fingerprint": parent["verification"]["fingerprint"],
            "contract": integration_contract(parent)}
        self.store.save(parent, "fixture_merge")
        child = self.store.get("CHILD")
        prepare_dependencies(self.store, child)
        self.assertTrue((Path(child["project"]) / "parent.txt").is_file())
        self.assertEqual(git(self.project, "rev-parse", "HEAD"), original_head)
        self.assertEqual(child["baseline"]["head"], parent["release"]["merge_commit"])

    def test_already_merged_dependency_preserves_dirty_baseline(self):
        from agent_workflow.policy import scope_check
        save_profile(self.store,'isolated',{'project':str(self.project),'reviewer':'none','checks':[[sys.executable,'-c','import app; assert app.VALUE == 1']]})
        parent=create_from_profile(self.store,'PARENT','isolated','parent',['parent'])
        self.finish(parent)
        sha=git(parent['project'],'rev-parse','HEAD')
        parent['release']={'commit':sha,'merge_commit':sha,'stage':'merged','config':{'deploy':None}}
        from agent_workflow.release import integration_contract
        parent['release']['integration_verification'] = {'passed': True, 'revision': sha,
            'fingerprint': parent['verification']['fingerprint'], 'contract': integration_contract(parent)}
        self.store.save(parent,'fixture_merge')
        (self.project/'outside.txt').write_text('user edit')
        save_profile(self.store,'current',{'project':str(self.project),'workspace':{'isolate':False},'allowed_paths':['app.py']})
        child=create_from_profile(self.store,'CHILD','current','child',['child'])
        initial=child['baseline']['changed_files'].copy()
        set_dependencies(self.store,'CHILD',['PARENT'],'merged')
        child=self.store.get('CHILD'); prepare_dependencies(self.store,child)
        self.assertEqual(child['baseline']['changed_files'],initial)
        scope_check(child)
        (self.project/'outside.txt').unlink()
        with self.assertRaises(WorkflowError): scope_check(child)


if __name__ == "__main__":
    unittest.main()
