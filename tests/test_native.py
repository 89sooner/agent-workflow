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


if __name__=="__main__":
    unittest.main()
