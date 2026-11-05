"""Connection setup writes only local configuration, never a workflow or remote run."""
import json
from pathlib import Path
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import test_opencode
from agent_workflow import connections
from agent_workflow.core import Store, create_task, git
from agent_workflow.server import handler


class OnboardingTests(unittest.TestCase):
    setUp = test_opencode.OpenCodeTests.setUp
    server = test_opencode.OpenCodeTests.server

    def dashboard(self, **flags):
        Path(self.project).mkdir(exist_ok=True)
        return self.server(handler(self.home, "setup-token", **flags))

    def request(self, server, path, body=None, headers=None):
        request = Request(f'http://127.0.0.1:{server.server_port}' + path,
                          data=json.dumps(body).encode() if body is not None else None,
                          headers={"X-Workflow-Token": "setup-token", **(headers or {})})
        try:
            with urlopen(request, timeout=10) as response:
                return response.status, json.load(response)
        except HTTPError as response:
            return response.code, json.load(response)

    def payload(self, **kw):
        return {"kind": "opencode", "project": self.project, "url": self.url,
                "name": "한글 연결", **kw}

    def test_diagnostics_route_is_read_only_and_validates_query(self):
        from urllib.parse import urlencode
        for flags in ({}, {"read_only": True}, {"read_only": True, "observer": True}):
            server = self.dashboard(**flags)
            query = urlencode({"project": self.project, "provider": "codex"})
            code, value = self.request(server, '/api/connections/diagnostics?' + query)
            self.assertEqual(code, 200)
            self.assertFalse(value['execution_started'])
            self.assertEqual(value['provider'], 'codex')
            self.assertEqual(self.request(server, '/api/connections/diagnostics?project=relative')[0], 400)
        self.assertFalse((self.home / 'state.sqlite3').exists())
        self.assertFalse((self.home / 'native-observation.sqlite3').exists())

    def test_overview_native_failure_is_partial_not_http_failure(self):
        from agent_workflow.core import WorkflowError
        server = self.dashboard(read_only=True)
        with patch('agent_workflow.native.snapshot', side_effect=WorkflowError('private detail')), patch.object(
                connections, 'discover', return_value={"ledgers": [], "warnings": []}):
            code, value = self.request(server, '/api/overview')
        self.assertEqual(code, 200)
        self.assertTrue(value['errors'])
        self.assertNotIn('private detail', json.dumps(value))
        self.assertFalse(value['execution_started'])

    def test_session_evidence_permission_and_no_workflow_initialization(self):
        from agent_workflow import native
        Path(self.project).mkdir(exist_ok=True)
        native.install(self.home,self.project)
        native.ingest(self.home,self.project,dict(session_id="evidence",hook_event_name="SessionStart",cwd=self.project))
        sid=native.snapshot(self.home,None,refresh=False)["sessions"][0]["id"]
        payload=dict(action="add",session=sid,kind="pr",url="https://github.com/example/repo/pull/1")
        server=self.dashboard(read_only=True)
        self.assertEqual(self.request(server,'/api/native/evidence',payload)[0],403)
        observer=self.dashboard(read_only=True,observer=True)
        self.assertEqual(self.request(observer,'/api/native/evidence',payload,{"X-Workflow-Token":"wrong"})[0],403)
        self.assertEqual(self.request(observer,'/api/native/evidence',payload)[0],200)
        code,value=self.request(observer,'/api/native/evidence?session='+sid)
        self.assertEqual(code,200);self.assertEqual(len(value['links']),1)
        self.assertFalse((self.home/'state.sqlite3').exists())
        self.assertFalse((self.home/'daemon.lock').exists())

    def test_evidence_rejects_discovery_and_revokes_disconnected_ledger(self):
        from agent_workflow import native
        Path(self.project).mkdir(exist_ok=True)
        native.install(self.home,self.project)
        native.ingest(self.home,self.project,dict(session_id="evidence-registry",hook_event_name="SessionStart",cwd=self.project))
        sid=native.snapshot(self.home,None,refresh=False)["sessions"][0]["id"]
        other=Path(self.temp.name)/"discovered-only";store=Store(other)
        store.db.execute('INSERT INTO tasks(id,data,revision) VALUES(?,?,?)',('SAME',json.dumps(dict(id='SAME',status='completed',workspace={'branch':'only-explicit-branch'})),1))
        store.db.commit();store.close();before=(other/'state.sqlite3').read_bytes()
        identifier=connections.ledger_id(other)
        payload=dict(action='add',session=sid,kind='workflow',ledger=identifier,task='SAME')
        observer=self.dashboard(read_only=True,observer=True)
        registry=self.home/'connections.json'
        with patch.object(connections,'_process_homes',return_value=iter([other])):
            code,candidates=self.request(observer,'/api/connections')
            self.assertEqual(code,200);self.assertIn(identifier,[row['id'] for row in candidates['ledgers']])
            self.assertEqual(self.request(observer,'/api/native/evidence',payload)[0],400)
            registry.write_text(json.dumps(dict(version=1,projects=[dict(id='p',path=self.project,ledgers=[dict(home=str(other))])])))
            code,linked=self.request(observer,'/api/native/evidence',payload);self.assertEqual(code,200)
            code,value=self.request(observer,'/api/native/evidence?session='+sid)
            self.assertEqual(value['links'][0]['branch'],'only-explicit-branch')
            self.assertIn(identifier,[row['id'] for row in value['ledgers']])
            registry.write_text(json.dumps(dict(version=1,projects=[])))
            self.assertIn(identifier,[row['id'] for row in self.request(observer,'/api/connections')[1]['ledgers']])
            code,value=self.request(observer,'/api/native/evidence?session='+sid)
            self.assertEqual(value['links'][0]['status'],'근거 접근 불가')
            self.assertIsNone(value['links'][0]['url']);self.assertNotIn('branch',value['links'][0])
            self.assertNotIn(identifier,[row['id'] for row in value['ledgers']])
            self.assertEqual(self.request(observer,'/api/native/evidence',payload)[0],400)
            registry.write_text('invalid registry')
            code,value=self.request(observer,'/api/native/evidence?session='+sid)
            self.assertEqual(code,200);self.assertTrue(value['sources_error'])
            self.assertEqual(self.request(observer,'/api/native/evidence',dict(action='remove',session=sid,id=linked['id']))[0],200)
        self.assertEqual(before,(other/'state.sqlite3').read_bytes())
        self.assertFalse((self.home/'state.sqlite3').exists());self.assertFalse((self.home/'daemon.lock').exists())

    def test_bookmarks_permission_and_metadata_export(self):
        from agent_workflow import native
        Path(self.project).mkdir(exist_ok=True)
        native.install(self.home,self.project)
        native.ingest(self.home,self.project,dict(session_id="bookmark",hook_event_name="SessionStart",cwd=self.project))
        sid=native.snapshot(self.home,None,refresh=False)["sessions"][0]["id"]
        payload=dict(action="add",session=sid,label="HTTP bookmark",first_seq=1,last_seq=1)
        readonly=self.dashboard(read_only=True)
        self.assertEqual(self.request(readonly,'/api/native/bookmarks',payload)[0],403)
        observer=self.dashboard(read_only=True,observer=True)
        self.assertEqual(self.request(observer,'/api/native/bookmarks',payload,{"X-Workflow-Token":"wrong"})[0],403)
        code,value=self.request(observer,'/api/native/bookmarks',payload)
        self.assertEqual(code,200)
        code,export=self.request(observer,'/api/native/bookmarks/export?id='+value['id'])
        self.assertEqual(code,200);self.assertTrue(export['complete'])
        invalid={**payload,'first_seq':10**100,'last_seq':10**100}
        self.assertEqual(self.request(observer,'/api/native/bookmarks',invalid)[0],400)
        self.assertFalse((self.home/'state.sqlite3').exists())
        self.assertFalse((self.home/'daemon.lock').exists())

    def test_metrics_readonly_period_validation_and_no_state_initialization(self):
        server=self.dashboard(read_only=True)
        code,value=self.request(server,'/api/native/metrics?days=1')
        self.assertEqual(code,200);self.assertIsNone(value['tool_error_rate'])
        self.assertEqual(self.request(server,'/api/native/metrics?days=bad')[0],400)
        self.assertEqual(self.request(server,'/api/native/metrics?provider=unknown')[0],400)
        self.assertFalse((self.home/'native-observation.sqlite3').exists())
        self.assertFalse((self.home/'state.sqlite3').exists())

    def test_capabilities_do_not_initialize_state_and_describe_modes(self):
        for flags, mode, configure, manage in [({}, "manage", True, True),
                ({"read_only": True}, "read_only", False, False),
                ({"observer": True, "read_only": True}, "observer", True, False)]:
            with self.subTest(mode=mode):
                server = self.dashboard(**flags)
                code, value = self.request(server, '/api/capabilities')
                self.assertEqual(code, 200)
                self.assertEqual(value['mode'], mode)
                self.assertEqual(value['can_configure_connections'], configure)
                self.assertEqual(value['can_manage_tasks'], manage)
                self.assertIsNone(value['primary_ledger_id'])
                self.assertEqual(value['csrf_token'], 'setup-token')
                self.assertEqual([x['support'] for x in value['tools']],
                                 ['native_sessions', 'workflow_only', 'workflow_only'])
                self.assertFalse((self.home / 'state.sqlite3').exists())

    def test_observer_connect_refresh_and_disconnect_only_change_config(self):
        server = self.dashboard(read_only=True, observer=True)
        real = connections.discover
        with patch.object(connections, 'discover', side_effect=lambda *a, **kw:
                          real(homes=[], include_processes=False, include_default=False, include_temporary=False)):
            self.assertEqual(self.request(server, '/api/connections')[1]['ledgers'], [])
            code, value = self.request(server, '/api/connections/connect', self.payload())
            self.assertEqual(code, 200, value)
            self.assertFalse(value['execution_started'])
            sid = value['source_id']
            items = self.request(server, '/api/connections')[1]['ledgers']
            self.assertEqual([i['id'] for i in items], [sid])
            self.assertTrue(items[0]['last_success_at'])
            self.assertEqual(self.request(server, '/api/project-observability?ledger=' + sid)[1]['tasks'][0]['id'], 'ses_one')
            self.assertEqual(self.request(server, '/api/connections/disconnect', {'kind': 'opencode', 'source_id': sid})[0], 200)
            self.assertEqual(self.request(server, '/api/connections')[1]['ledgers'], [])
        self.assertFalse((self.home / 'state.sqlite3').exists())
        self.assertTrue(all(call[0] == 'GET' for call in self.calls))
        self.assertEqual(len(self.rows), 1)

    def test_read_only_and_observer_execution_boundaries(self):
        readonly = self.dashboard(read_only=True)
        for path in ('discover', 'connect', 'disconnect'):
            self.assertEqual(self.request(readonly, '/api/connections/' + path, self.payload())[0], 403)
        observer = self.dashboard(read_only=True, observer=True)
        for path in ('tasks', 'queue', 'resume', 'stop', 'hold', 'unhold', 'cancel', 'release-clear'):
            self.assertEqual(self.request(observer, '/api/' + path, {'task_id': 'test'})[0], 403)
        self.assertEqual(self.request(observer, '/api/connections/connect?x=1', self.payload())[0], 403)
        self.assertFalse(self.home.exists())
        self.assertEqual(self.calls, [])

    def test_setup_requires_token_and_same_origin(self):
        server = self.dashboard(observer=True)
        for headers, expected in [({'X-Workflow-Token': ''}, 403),
                                  ({'Origin': 'https://attacker.invalid'}, 400),
                                  ({'Host': 'attacker.invalid'}, 400)]:
            self.assertEqual(self.request(server, '/api/connections/connect', self.payload(), headers)[0], expected)
        self.assertFalse(self.home.exists())
        self.assertEqual(self.calls, [])

    def test_invalid_inputs_and_auth_failure_leave_no_configuration(self):
        server = self.dashboard(observer=True)
        for override in ({'project': ''}, {'project': '.'}, {'project': self.project + '/missing'},
                         {'name': []}, {'username': {}}, {'password_env': []}, {'url': []},
                         {'url': 'http://external.invalid:4096'}, {'kind': 'unknown'}):
            with self.subTest(override=override):
                self.assertEqual(self.request(server, '/api/connections/connect', self.payload(**override))[0], 400)
                self.assertFalse(self.home.exists())
        self.require_auth = True
        code, result = self.request(server, '/api/connections/connect', self.payload())
        self.assertEqual(code, 400)
        self.assertIn('401', result['error'])
        self.assertFalse(self.home.exists())

    def test_workflow_candidates_are_project_bound_and_explicit(self):
        server = self.dashboard(observer=True)
        project = Path(self.project)
        git(project, 'init', '-q')
        first = Store(Path(self.temp.name) / 'first')
        second = Store(Path(self.temp.name) / 'second')
        self.addCleanup(first.close)
        self.addCleanup(second.close)
        other = Path(self.temp.name) / 'other'
        other.mkdir()
        git(other, 'init', '-q')
        for repo in (project, other):
            git(repo, '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '--allow-empty', '-qm', 'fixture')
        create_task(first, 'MATCH', project, 'matching', ['criterion'], [], [], 'codex', 'none', 'local')
        create_task(second, 'OTHER', other, 'other', ['criterion'], [], [], 'codex', 'none', 'local')
        before = list(first.db.iterdump())
        real = connections.discover
        def isolated(project=None, homes=(), **kw):
            return real(project, [first.home, second.home], include_processes=False,
                        include_temporary=False, include_default=False)
        with patch.object(connections, 'discover', side_effect=isolated):
            code, found = self.request(server, '/api/connections/discover', {'project': self.project})
            self.assertEqual(code, 200, found)
            identifier = connections.ledger_id(first.home)
            self.assertEqual([item['id'] for item in found['ledgers']], [identifier])
            self.assertFalse(self.home.exists())
            for identifiers in ([], 'bad', [connections.ledger_id(second.home)], ['ledger-forged']):
                self.assertEqual(self.request(server, '/api/connections/connect',
                    {'kind': 'workflow', 'project': self.project, 'ledger_ids': identifiers})[0], 400)
            self.assertFalse(self.home.exists())
            code, result = self.request(server, '/api/connections/connect',
                {'kind': 'workflow', 'project': self.project, 'ledger_ids': [identifier]})
            self.assertEqual(code, 200, result)
            self.assertEqual(result, {'source_id': identifier, 'execution_started': False})
        self.assertEqual([item['id'] for item in connections.load_registry(self.home)['projects'][0]['ledgers']], [identifier])
        self.assertEqual(list(first.db.iterdump()), before)
        self.assertFalse((self.home / 'state.sqlite3').exists())

    def test_ui_connection_does_not_add_unselected_server_ledger(self):
        server = self.dashboard()
        project = Path(self.project)
        git(project, 'init', '-q')
        git(project, '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
            'commit', '--allow-empty', '-qm', 'fixture')
        own = Store(self.home)
        chosen = Store(Path(self.temp.name) / 'chosen')
        self.addCleanup(own.close)
        self.addCleanup(chosen.close)
        for store in (own, chosen):
            create_task(store, 'TASK', project, 'goal', ['criterion'], [], [], 'codex', 'none', 'local')
        real = connections.discover
        def isolated(project=None, homes=(), **kw):
            # Candidate discovery may see both; persistence must use only chosen IDs.
            candidates = [self.home, chosen.home] if kw.get('include_processes', True) else homes
            return real(project, candidates, include_processes=False, include_temporary=False, include_default=False)
        identifier = connections.ledger_id(chosen.home)
        with patch.object(connections, 'discover', side_effect=isolated):
            code, result = self.request(server, '/api/connections/connect',
                {'kind': 'workflow', 'project': self.project, 'ledger_ids': [identifier]})
        self.assertEqual(code, 200, result)
        self.assertEqual([x['id'] for x in connections.load_registry(self.home)['projects'][0]['ledgers']], [identifier])


if __name__ == '__main__':
    unittest.main()
