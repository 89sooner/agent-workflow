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
