import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from agent_workflow import native
from agent_workflow.core import WorkflowError


class NativePluginTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name) / 'state'
        self.project = Path(self.temp.name) / 'project with spaces'
        self.project.mkdir()

    def payload(self, event, **extra):
        return dict(session_id='same-id', cwd=str(self.project), hook_event_name=event, **extra)

    def test_codex_install_coexists_and_preserves_settings(self):
        native.install(self.home, self.project)
        settings = self.project / '.codex/hooks.json'
        settings.parent.mkdir()
        existing = {'type': 'command', 'command': 'echo other'}
        settings.write_text(json.dumps({'description': 'keep', 'hooks': {'Stop': [{'hooks': [existing]}]}}))
        native.install(self.home, self.project, provider='codex')
        native.install(self.home, self.project, provider='codex')
        self.assertEqual(len(json.loads(settings.read_text())['hooks']['Stop']), 2)
        self.assertFalse((self.project / '.codex/config.toml').exists())
        for provider in ['claude', 'codex']:
            self.assertTrue(native.ingest(self.home, self.project, self.payload('SessionStart'), provider))
        self.assertEqual(len(native.snapshot(self.home, None, refresh=False)['sessions']), 2)
        native.install(self.home, self.project, remove=True, provider='codex')
        self.assertEqual(json.loads(settings.read_text())['hooks']['Stop'], [{'hooks': [existing]}])
        self.assertEqual(json.loads(settings.read_text())['description'], 'keep')
        self.assertTrue(native.ingest(self.home, self.project, self.payload('Stop')))
        self.assertFalse(native.ingest(self.home, self.project, self.payload('Stop'), 'codex'))

    def test_codex_states_subagent_privacy_and_stale(self):
        native.register(self.home, self.project, 'codex')
        for event, expected in [('SessionStart','idle'), ('UserPromptSubmit','running'),
                                ('PermissionRequest','approval'), ('Interrupt','input'),
                                ('PreModelRequest','running'), ('Stop','input')]:
            self.assertTrue(native.ingest(self.home, self.project, self.payload(event, prompt='PRIVATE',
                transcript_path='/do/not/read', model='test-model'), 'codex', 'plugin'))
            self.assertEqual(native.snapshot(self.home,None,refresh=False)['sessions'][0]['state'],expected)
        native.ingest(self.home,self.project,self.payload('PreToolUse',tool_name='request_user_input'),'codex','plugin')
        self.assertEqual(native.snapshot(self.home,None,refresh=False)['sessions'][0]['state'],'input')
        native.ingest(self.home,self.project,self.payload('SubagentStart',agent_id='child',agent_type='reviewer'),'codex','plugin')
        rows=native.snapshot(self.home,None,refresh=False)['sessions']
        self.assertEqual(rows[0]['parent'],'same-id')
        self.assertIn('reviewer', rows[0]['name'])
        self.assertEqual(len(rows),2)
        with patch('agent_workflow.native.time.time',return_value=rows[0]['last_seen']+301):
            self.assertEqual(native.snapshot(self.home,None,refresh=False)['sessions'][0]['state'],'unknown')
        c=native.database(self.home)
        dump='\n'.join(c.iterdump());c.close()
        self.assertNotIn('PRIVATE',dump);self.assertNotIn('/do/not/read',dump)
        self.assertFalse((self.home/'state.sqlite3').exists())

    def test_plugin_opt_in_scope_and_unregistration(self):
        payload=self.payload('SessionStart')
        self.assertFalse(native.ingest(self.home,self.project,payload,'codex','plugin'))
        native.register(self.home,self.project,'claude')
        self.assertFalse(native.ingest(self.home,self.project,payload,'codex','plugin'))
        native.register(self.home,self.project,'codex')
        self.assertFalse(native.ingest(self.home,self.project,payload,'codex','direct'))
        self.assertFalse(native.ingest(self.home,self.project,{**payload,'cwd':'/different'},'codex','plugin'))
        self.assertTrue(native.ingest(self.home,self.project,payload,'codex','plugin'))
        native.register(self.home,self.project,'codex',remove=True)
        self.assertFalse(native.ingest(self.home,self.project,payload,'codex','plugin'))
        self.assertEqual(native.status(self.home,self.project,'codex')['observed_sessions'],1)
        self.assertFalse((self.project/'.codex').exists())

    def test_direct_to_plugin_requires_explicit_uninstall(self):
        native.install(self.home,self.project,provider='codex')
        with self.assertRaises(WorkflowError):native.register(self.home,self.project,'codex')
        native.install(self.home,self.project,remove=True,provider='codex')
        native.register(self.home,self.project,'codex')

    def test_removed_project_can_unregister_and_inspect(self):
        native.register(self.home,self.project,'codex')
        self.project.rmdir()
        self.assertIsNotNone(native.status(self.home,self.project,'codex')['registration'])
        native.register(self.home,self.project,'codex',remove=True)
        self.assertIsNone(native.status(self.home,self.project,'codex')['registration'])
        self.project.mkdir()
        self.assertFalse(native.ingest(self.home,self.project,self.payload('SessionStart'),'codex','plugin'))

    def test_plugin_parent_survives_followup_events(self):
        native.register(self.home,self.project,'opencode')
        native.ingest_opencode(self.home,str(self.project),self.payload('session.created',parent='parent-session'))
        native.ingest_opencode(self.home,str(self.project),self.payload('tool.execute.before',tool_name='bash'))
        native.ingest_opencode(self.home,str(self.project),self.payload('session.status',status='busy'))
        self.assertEqual(native.snapshot(self.home,None,refresh=False)['sessions'][0]['parent'],'parent-session')
        self.assertFalse(native.ingest_opencode(self.home,str(self.project),self.payload('session.discovered')))
        self.assertEqual(native.snapshot(self.home,None,refresh=False)['sessions'][0]['state'],'running')

    def test_packaged_hook_empty_stdout_fail_open(self):
        root=Path(__file__).resolve().parents[1]
        env={**os.environ,'AGENT_WORKFLOW_HOME':str(self.home),'PYTHONPATH':str(root)}
        for provider in ['claude','codex']:
            native.register(self.home,self.project,provider)
            command=[sys.executable,str(root/'plugins'/provider/'hooks/observe.py')]
            for raw in [json.dumps(self.payload('Stop')), '{bad', 'x'*(1024*1024+1)]:
                p=subprocess.run(command,input=raw,text=True,capture_output=True,env=env,timeout=4)
                self.assertEqual((p.returncode,p.stdout,p.stderr),(0,'',''))
        self.assertEqual(len(native.snapshot(self.home,None,refresh=False)['sessions']),2)

    def test_opencode_plugin_history_and_disconnect(self):
        native.register(self.home,self.project,'opencode')
        for event, state in [('session.created','idle'),('permission.asked','approval'),
                             ('question.asked','input'),('session.deleted','ended')]:
            self.assertTrue(native.ingest_opencode(self.home,str(self.project),self.payload(event)))
            row=native.snapshot(self.home,None,refresh=False)['sessions'][0]
            self.assertEqual(row['state'],state)
        native.register(self.home,self.project,'opencode',remove=True)
        self.assertFalse(native.ingest_opencode(self.home,str(self.project),self.payload('session.created')))
        self.assertEqual(native.snapshot(self.home,None,refresh=False)['sessions'][0]['state'],'ended')

if __name__=='__main__':unittest.main()
