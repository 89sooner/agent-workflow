import json
from pathlib import Path
import shlex
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from agent_workflow import native
from agent_workflow.core import WorkflowError


class NativeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.home = self.root / "observer"
        self.project = self.root / "project"
        self.project.mkdir()
        native.install(self.home, self.project)

    def event(self, kind, **extra):
        data = dict(session_id="native-session", hook_event_name=kind, cwd=str(self.project),
                    prompt="DO_NOT_STORE", tool_input={"secret": "DO_NOT_STORE"},
                    transcript_path="/DO_NOT_READ", last_assistant_message="DO_NOT_STORE")
        data.update(extra)
        self.assertTrue(native.ingest(self.home, self.project, data))
        return native.snapshot(self.home, None, refresh=False)

    def test_metadata_states_and_no_workflow_ledger(self):
        for event, expected in [("SessionStart","idle"),("UserPromptSubmit","running"),
                                ("PermissionRequest","approval"),("PostToolUse","running"),
                                ("Stop","input"),("SessionEnd","ended")]:
            result = self.event(event, tool_name="Bash")
            row = result["sessions"][0]
            self.assertEqual(row["state"], expected)
        db = sqlite3.connect(self.home / "native-observation.sqlite3")
        self.assertNotIn("DO_NOT_STORE", "\n".join(db.iterdump()))
        db.close()
        self.assertFalse((self.home/"state.sqlite3").exists())
        self.assertFalse((self.home/"tasks").exists())

    def test_subagent_and_unknown_after_timeout(self):
        self.event("SessionStart")
        result = self.event("SubagentStart", agent_id="child", agent_type="reviewer")
        self.assertEqual(len(result["sessions"]), 2)
        child = next(s for s in result["sessions"] if s["parent"])
        self.assertEqual(child["parent"], "native-session")
        with patch("agent_workflow.native.time.time", return_value=child["last_seen"]+301):
            stale = native.snapshot(self.home, None, refresh=False)
            self.assertTrue(all(s["state"]=="unknown" for s in stale["sessions"]))
        result = self.event("SubagentStop", agent_id="child")
        self.assertEqual(next(s for s in result["sessions"] if s["parent"])["state"],"ended")

    def test_unrelated_notifications_and_subagent_tool_identity(self):
        self.event("UserPromptSubmit")
        self.assertFalse(native.ingest(self.home,self.project,dict(
            session_id="native-session",hook_event_name="Notification",cwd=str(self.project),notification_type="auth_success")))
        self.assertEqual(native.snapshot(self.home,None,refresh=False)["sessions"][0]["state"],"running")
        result=self.event("PreToolUse",agent_id="child",tool_name="AskUserQuestion")
        child=next(s for s in result["sessions"] if s["parent"])
        self.assertEqual(child["state"],"input")
        self.assertEqual(next(s for s in result["sessions"] if not s["parent"])["state"],"running")

    def test_scope_validation_no_body_or_arbitrary_files(self):
        self.assertFalse(native.ingest(self.home,self.project,{"session_id":"a","hook_event_name":"Stop","cwd":str(self.root)}))
        self.assertFalse((self.home/"native-observation.sqlite3").exists())

    def test_install_idempotent_preserves_other_settings_and_uninstall(self):
        path=self.project/".claude/settings.local.json"
        data=json.loads(path.read_text())
        data["permissions"]={"allow":["Read"]}
        data["hooks"]["Stop"].append({"hooks":[{"type":"command","command":"echo existing"}]})
        path.write_text(json.dumps(data))
        native.install(str(self.home),self.project)
        native.install(self.home,self.project)
        installed=json.loads(path.read_text())
        self.assertEqual(len(installed["hooks"]["Stop"]),2)
        native.install(self.home,self.project,remove=True)
        result=json.loads(path.read_text())
        self.assertEqual(result["permissions"],data["permissions"])
        self.assertEqual(result["hooks"]["Stop"],[{"hooks":[{"type":"command","command":"echo existing"}]}])
        self.assertEqual(native.sources(self.home),[])
        self.assertTrue(list(path.parent.glob("*.observer-backup-*")))

    def test_upgrade_replaces_own_old_command(self):
        old=native.command(self.home,self.project)
        with patch("agent_workflow.native.command",return_value=old+" --upgraded"):
            native.install(self.home,self.project)
        text=(self.project/".claude/settings.local.json").read_text()
        data=json.loads(text)
        self.assertEqual(len(data["hooks"]["Stop"]),1)
        self.assertTrue(data["hooks"]["Stop"][0]["hooks"][0]["command"].endswith("--upgraded"))

    def test_legacy_registry_preserves_prompt_hooks(self):
        registry=native.sources(self.home)
        registry[0].pop("command",None)
        (self.home/"native-sources.json").write_text(json.dumps(registry))
        path=self.project/".claude/settings.local.json"
        config=json.loads(path.read_text())
        existing={"hooks":[{"type":"prompt","prompt":"Keep my existing validation"}]}
        config["hooks"]["Stop"].append(existing)
        path.write_text(json.dumps(config))
        native.install(self.home,self.project)
        self.assertIn(existing,json.loads(path.read_text())["hooks"]["Stop"])

    def test_hook_subprocess_zero_output_and_fail_open(self):
        argv=shlex.split(native.command(self.home,self.project))
        result=subprocess.run(argv,input=json.dumps(dict(session_id="real-subprocess",hook_event_name="Stop",cwd=str(self.project),prompt="PRIVATE")),text=True,capture_output=True)
        self.assertEqual((result.returncode,result.stdout,result.stderr),(0,"",""))
        self.assertEqual(native.snapshot(self.home,None,refresh=False)["sessions"][0]["state"],"input")
        bad=subprocess.run(argv,input="{malformed",text=True,capture_output=True)
        self.assertEqual((bad.returncode,bad.stdout,bad.stderr),(0,"",""))

    def test_opencode_archive_disconnect_and_missing_session(self):
        source={"id":"opencode-test","url":"http://127.0.0.1:4096","directory":"/project"}
        class Observer:
            def view(self, source_id):
                return {"tasks":[dict(id="session",project="/project",goal="native OpenCode",status="running")],"truncated":{"tasks":False}}
        with patch("agent_workflow.opencode.sources",return_value=[source]), patch("agent_workflow.opencode.Client.get",return_value=[]):
            result=native.snapshot(self.home,Observer())
        self.assertEqual(result["sessions"][0]["state"],"running")
        class Failed:
            def view(self, source_id):
                raise WorkflowError("offline")
        with patch("agent_workflow.opencode.sources",return_value=[source]), patch("agent_workflow.opencode.Client.get",return_value=[]):
            offline=native.snapshot(self.home,Failed(),result["sessions"][0]["id"])
        self.assertEqual(offline["sessions"][0]["state"],"unknown")
        self.assertTrue(offline["history"])
        self.assertEqual(len(offline["errors"]),1)
        class Empty:
            def view(self, source_id):
                return {"tasks":[]}
        with patch("agent_workflow.opencode.sources",return_value=[source]), patch("agent_workflow.opencode.Client.get",return_value=[]):
            missing=native.snapshot(self.home,Empty())
        self.assertEqual(missing["sessions"][0]["state"],"unknown")





    def test_waiting_since_survives_repeated_events_and_stale(self):
        with patch("agent_workflow.native.time.time", return_value=1000):
            row = self.event("PermissionRequest")["sessions"][0]
        self.assertEqual(row["waiting_since"], 1000)
        with patch("agent_workflow.native.time.time", return_value=1100):
            row = self.event("PermissionRequest")["sessions"][0]
        self.assertEqual(row["waiting_since"], 1000)
        with patch("agent_workflow.native.time.time", return_value=1400):
            row = native.snapshot(self.home, None, refresh=False)["sessions"][0]
        self.assertEqual(row["state"], "unknown")
        self.assertEqual(row["freshness_reason"], "event_expired")
        self.assertIsNone(row["waiting_since"])
        self.assertEqual(row["state_since"], 1000)
        with patch("agent_workflow.native.time.time", return_value=1500):
            row = self.event("PostToolUse")["sessions"][0]
        self.assertEqual(row["state_since"], 1500)
        self.assertIsNone(row["waiting_since"])

    def test_legacy_database_snapshot_does_not_migrate_on_read(self):
        self.event("PermissionRequest")
        path = self.home / "native-observation.sqlite3"
        with sqlite3.connect(path) as c:
            c.execute("DROP TABLE session_transitions")
        before = path.read_bytes()
        row = native.snapshot(self.home, None, refresh=False)["sessions"][0]
        self.assertIsNone(row["waiting_since"])
        self.assertEqual(path.read_bytes(), before)
        self.event("PermissionRequest")
        row = native.snapshot(self.home, None, refresh=False)["sessions"][0]
        self.assertIsNone(row["waiting_since"])


    def test_partial_opencode_wait_failure_preserves_start(self):
        from unittest.mock import Mock
        from agent_workflow import opencode
        observer = Mock()
        observer.view.return_value = {"tasks": [{"id": "ses_wait", "project": str(self.project),
            "goal": "test", "status": "idle"}]}
        source = {"id": "opencode-fixture"}
        def response(endpoint):
            return [{"sessionID": "ses_wait"}] if endpoint == "/permission" else []
        with patch.object(opencode, "sources", return_value=[source]), patch.object(opencode, "Client") as client:
            client.return_value.get.side_effect = response
            with patch("agent_workflow.native.time.time", return_value=1000):
                row = native.snapshot(self.home, observer)["sessions"][0]
                self.assertEqual(row["waiting_since"], 1000)
            client.return_value.get.side_effect = WorkflowError("temporary failure")
            with patch("agent_workflow.native.time.time", return_value=1100):
                failed = native.snapshot(self.home, observer)
                self.assertTrue(failed["errors"])
                self.assertEqual(failed["sessions"][0]["state"], "unknown")
                self.assertEqual(failed["sessions"][0]["observed_state"], "approval")
            client.return_value.get.side_effect = response
            with patch("agent_workflow.native.time.time", return_value=1200):
                row = native.snapshot(self.home, observer)["sessions"][0]
                self.assertEqual(row["waiting_since"], 1000)

    def test_child_name_survives_tool_and_stop_without_role(self):
        self.event("SubagentStart", agent_id="named", agent_type="reviewer")
        for event in ["PreToolUse", "SubagentStop"]:
            result = self.event(event, agent_id="named")
            child = next(s for s in result["sessions"] if s["parent"])
            self.assertEqual(child["name"], "Claude · reviewer")

    def test_explicit_activity_start_end_and_stale(self):
        for tool, expected in [("Read", "reading"), ("Edit", "writing"), ("Bash", "command"), ("unknown-tool", "working")]:
            row = self.event("PreToolUse", tool_name=tool)["sessions"][0]
            self.assertEqual(row["activity"], expected)
            row = self.event("PostToolUse", tool_name=tool)["sessions"][0]
            self.assertEqual(row["activity"], "working")
        with patch("agent_workflow.native.time.time", return_value=row["last_seen"]+301):
            self.assertEqual(native.snapshot(self.home, None, refresh=False)["sessions"][0]["activity"], "unknown")

    def test_opencode_same_tool_after_ends_activity(self):
        native.register(self.home, self.project, "opencode")
        payload = dict(session_id="oc-tool", tool_name="read")
        for event, expected in [("tool.execute.before", "reading"), ("tool.execute.after", "working")]:
            self.assertTrue(native.ingest_opencode(self.home, str(self.project), dict(payload, hook_event_name=event)))
            row = next(s for s in native.snapshot(self.home, None, refresh=False)["sessions"] if s["native_id"]=="oc-tool")
            self.assertEqual(row["activity"], expected)

    def test_project_identity_is_added_without_changing_original_path(self):
        row = self.event("SessionStart")["sessions"][0]
        self.assertEqual(row["project"], str(self.project))
        self.assertTrue(row["project_id"].startswith("project-"))

    def test_common_git_worktree_identity_preserves_both_paths(self):
        subprocess.run(["git", "init", "-q", str(self.project)], check=True)
        subprocess.run(["git", "-C", str(self.project), "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "--allow-empty", "-qm", "fixture"], check=True)
        tree = self.root / "linked"
        subprocess.run(["git", "-C", str(self.project), "worktree", "add", "-qb", "fixture-linked", str(tree)], check=True)
        native._project_identity.cache_clear()
        first = self.event("SessionStart")["sessions"][0]
        native.install(self.home, tree)
        self.assertTrue(native.ingest(self.home, tree, dict(session_id="linked", hook_event_name="SessionStart", cwd=str(tree))))
        rows = native.snapshot(self.home, None, refresh=False)["sessions"]
        self.assertEqual({s["project_id"] for s in rows}, {first["project_id"]})
        self.assertEqual({s["project"] for s in rows}, {str(self.project), str(tree)})

if __name__=="__main__":
    unittest.main()
