"""Exercise the operator/model resume view on current, stale and incomplete ledger evidence."""
from copy import deepcopy
import json
import sys
import unittest

import test_workflow as fixtures
from agent_workflow.cli import freshness
from agent_workflow.core import complete, git, snapshot
from agent_workflow.recovery import diagnosis
from agent_workflow.resume_packet import build_packet
from agent_workflow.runner import record_review, run_checks


class ResumePacketTests(unittest.TestCase):
    setUp = fixtures.WorkflowTests.setUp
    tearDown = fixtures.WorkflowTests.tearDown
    task = fixtures.WorkflowTests.task
    checked = fixtures.WorkflowTests.checked
    result = staticmethod(fixtures.WorkflowTests.result)

    def packet(self, task):
        current = snapshot(self.project)
        flags = freshness(task, current)
        return build_packet(task, current, flags, diagnosis(task, current), ledger=self.store.home)

    def test_shallow_pickup_matches_full_for_unstarted_task_and_preserves_scope(self):
        task = self.task()
        task["checkpoint"]["next"] = "Update the display in app.py."
        task["project_config"] = {"allowed_paths": ["app.py"]}
        full = self.packet(task)
        shallow = build_packet(task, {"head": full["head"]}, {}, None, ledger=self.store.home)
        self.assertEqual(full, shallow)
        self.assertEqual(full["remaining_criteria"][0]["text"], task["criteria"][0])
        self.assertEqual(full["next_action"], {"action": "checkpoint", "source": "/checkpoint/next",
                                               "text": task["checkpoint"]["next"]})
        self.assertEqual(full["limits"]["allowed_paths"], ["app.py"])
        self.assertEqual(full["ledger"], str(self.store.home))
        self.assertEqual(full["revision"], task["revision"])
        self.assertEqual(full["uncertainties"], [])

    def test_protected_baseline_cannot_be_overridden_by_a_checkpoint(self):
        from agent_workflow.policy import protected_snapshot
        from agent_workflow.resume_packet import needs_fingerprint
        task = self.task()
        task["project_config"] = {"forbidden_paths": ["private"]}
        task["baseline"]["protected_paths"] = protected_snapshot(self.project, ["private"])
        self.assertTrue(needs_fingerprint(task))
        (self.project / "private").write_text("new")
        packet = self.packet(task)
        self.assertEqual(packet["limits"]["forbidden_paths"], ["private"])
        self.assertEqual(packet["next_action"]["action"], "inspect")

    def test_local_checks_reference_original_receipt_then_disappear_on_drift(self):
        task = self.checked(self.task(reviewer="codex"))
        packet = self.packet(task)
        self.assertEqual(packet["next_action"]["action"], "review")
        self.assertEqual(packet["valid_evidence"][0]["source"], "/verification")
        recorded = packet["valid_evidence"][0]["fingerprint"]
        self.assertEqual(recorded, task["verification"]["fingerprint"])
        self.assertEqual(len(packet["remaining_criteria"]), 1)
        (self.project / "app.py").write_text("VALUE=9")
        stale = self.packet(task)
        self.assertNotEqual(recorded, stale["fingerprint"])
        self.assertEqual(stale["valid_evidence"], [])
        self.assertEqual(stale["next_action"]["action"], "recheck")
        self.assertTrue(stale["uncertainties"])

    def test_active_legacy_and_missing_worktree_never_offer_current_checks(self):
        task = self.checked(self.task(reviewer="codex"))
        del task["verification"]["check_context"]
        self.assertEqual(self.packet(task)["valid_evidence"], [])
        for observation in ({}, {"error": "missing"}, {"archived": True, **snapshot(self.project)}):
            with self.subTest(observation=observation):
                packet = build_packet(task, observation, {"verification_is_current": True},
                                      {"action": "review", "next_action": "review"})
                self.assertFalse(packet["valid_evidence"])
                self.assertEqual(packet["next_action"]["action"], "inspect")

    def test_missing_reference_is_not_an_actionable_manual_checkpoint(self):
        task = self.task()
        task["refs"] = [str(self.project / "unavailable-instructions.md")]
        task["checkpoint"]["next"] = "Proceed using the old instructions."
        packet = self.packet(task)
        self.assertEqual(packet["next_action"]["action"], "inspect")
        self.assertIn("참조 문서", packet["next_action"]["text"])

    def test_completed_legacy_receipt_agrees_with_pickup_and_declared_inputs(self):
        from agent_workflow.session_notes import _evidence
        t = self.checked(self.task())
        complete(self.store, t)
        del t["verification"]["check_context"]
        current = snapshot(self.project)
        for inputs, action, state in (([], "complete", "current"), (["fixtures"], "inspect_completion", "stale")):
            with self.subTest(inputs=inputs):
                t["project_config"] = {"check_inputs": inputs}
                flags = freshness(t, current)
                packet = self.packet(t)
                self.assertEqual(packet["next_action"]["action"], action)
                self.assertEqual(_evidence(t, current, None, flags=flags)["completion"], state)
                self.assertEqual("completion" in {e["kind"] for e in packet["valid_evidence"]}, state == "current")

    def test_ordinary_release_resumes_until_integration_and_health_are_recorded(self):
        t = self.checked(self.task())
        complete(self.store, t)
        merge = "b" * 40
        release = t["release"] = {"commit": "a" * 40, "stage": "pushed",
                                  "config": {"mode": "github", "repo": "org/project",
                                             "deploy": ["deploy"], "health": ["health"]}}
        stages = [
            ({}, "inspect_release"),
            ({"stage": "pull_request", "pr": 2}, "continue_pr"),
            ({"stage": "merged", "merge_commit": merge, "error": "integration failed",
              "integration_verification": {"revision": merge, "finished_at": "now", "passed": False}}, "verify_integration"),
            ({"integration_verification": {"revision": merge, "finished_at": "now", "passed": True}}, "deploy"),
            ({"stage": "deploying", "deployment_state": "applied"}, "health"),
            ({"stage": "deployed", "deployed_revision": merge}, "inspect_release"),
            ({"health_result": {"exit_code": 0, "timed_out": False}}, "complete"),
        ]
        for updates, action in stages:
            with self.subTest(action=action):
                release.update(updates)
                self.assertEqual(self.packet(t)["next_action"]["action"], action)

    def test_staging_during_release_preserves_preparation_as_the_next_action(self):
        (self.project / "note.txt").write_text("verified change")
        t = self.checked(self.task())
        complete(self.store, t)
        t["release"] = {"stage": "preparing", "content_digest": "recorded", "config": {"mode": "local"}}
        git(self.project, "add", "note.txt")
        packet = self.packet(t)
        self.assertNotIn("completion", {row["kind"] for row in packet["valid_evidence"]})
        self.assertEqual(packet["next_action"]["action"], "inspect_release")

    def test_stale_review_and_missing_identity_cannot_clear_remaining_criteria(self):
        t = self.checked(self.task(reviewer="codex"))
        record_review(self.store, t, self.result(), snapshot(self.project)["fingerprint"], "codex")
        complete(self.store, t)
        good = self.packet(t)
        self.assertFalse(good["remaining_criteria"])
        self.assertIn("completion", [e["kind"] for e in good["valid_evidence"]])
        t["review"]["reviewer"] = "some-other-reviewer"
        bad = self.packet(t)
        self.assertEqual(len(bad["remaining_criteria"]), 1)
        self.assertNotIn("review", [e["kind"] for e in bad["valid_evidence"]])
        self.assertEqual(bad["next_action"]["action"], "inspect_completion")

    def test_hold_wins_even_without_recovery_and_packet_does_not_change_row(self):
        t = self.task()
        t["hold"] = {"reason": "계정 범위 확인"}
        original = deepcopy(t)
        packet = self.packet(t)
        self.assertEqual(t, original)
        self.assertIn("계정 범위 확인", packet["uncertainties"])
        self.assertEqual(packet["next_action"]["action"], "held")

    def test_live_bound_katakuri_review_only_drops_accepted_current_rows(self):
        t = self.checked(self.task(reviewer="codex"))
        t["criteria"] = ["required behavior", "external observation"]
        fp = t["verification"]["fingerprint"]
        t["katakuri_goal"] = {"phase": "waiting", "items": [{"id": 1, "state": "proposed"},
                               {"id": 2, "state": "waiting", "next_action": "Operator sample"}],
                              "audit": {"rows": [{"criterion": 1, "accepted": True, "evidence": "accepted behavior"},
                                                 {"criterion": 2, "accepted": False, "evidence": "missing sample"}],
                                        "reviewer": "codex", "fingerprint": fp}}
        t["review"] = {"verdict": "changes_requested", "reviewer": "codex", "fingerprint": fp}
        packet = self.packet(t)
        self.assertEqual([r["id"] for r in packet["remaining_criteria"]], [2])
        self.assertEqual(packet["next_action"]["action"], "wait_for_input")
        self.assertTrue(any("Operator sample" in note for note in packet["uncertainties"]))
        t["katakuri_goal"]["audit"]["fingerprint"] = "old"
        self.assertEqual([r["id"] for r in self.packet(t)["remaining_criteria"]], [1, 2])

    def test_audit_of_superseded_condition_check_keeps_condition_open(self):
        t = self.task(reviewer="codex", checks=[[sys.executable, "-c", "pass"]])
        t["project_config"] = {"checks": t["checks"], "criterion_checks": {t["criteria"][0]: {"check": t["checks"][0]}}}
        t["katakuri_goal"] = {"phase": "reviewing", "items": [{"id": 1, "state": "proposed"}]}
        with self.store.lock(self.project) as fd:
            run_checks(self.store, t, 5, fd)
        v = t["verification"]
        record_review(self.store, t, self.result(), v["fingerprint"], "codex")
        t["katakuri_goal"]["audit"] = {"reviewer": "codex", "fingerprint": v["fingerprint"],
            "rows": [{"criterion": 1, "accepted": True, "evidence": "inspected", "check_result_id": "expired"}]}
        packet = self.packet(t)
        self.assertEqual([r["id"] for r in packet["remaining_criteria"]], [1])
        e, = [e for e in packet["valid_evidence"] if e["kind"] == "criterion_check"]
        self.assertEqual(e["result_id"], v["results"][0]["result_id"])
        self.assertNotIn("criterion_review", [e["kind"] for e in packet["valid_evidence"]])
        self.assertNotIn("review", [e["kind"] for e in packet["valid_evidence"]])
        self.assertEqual(packet["next_action"]["action"], "review")
        self.assertNotIn("expired", json.dumps(packet))

    def test_remote_receipt_and_unknown_deployment_keep_their_provenance(self):
        t = self.task()
        t["status"] = "completed"
        sha = "a" * 40
        t["release"] = {"commit": sha, "merge_commit": "b" * 40, "pr": 2, "deployment_state": "started",
                        "config": {"repo": "org/project", "observe_revision": ["observe"]}}
        current = {"error": "checkout unavailable"}
        packet = build_packet(t, current, {}, diagnosis(t, current))
        self.assertEqual(packet["next_action"]["action"], "observe_deployment")
        self.assertIn("/release/merge_commit", [e["source"] for e in packet["valid_evidence"]])
        self.assertNotIn("/release/health_result", [e["source"] for e in packet["valid_evidence"]])
        t["hold"] = {"reason": "owner paused"}
        self.assertEqual(build_packet(t, current, {}, diagnosis(t, current))["next_action"]["action"], "held")
