import copy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from agent_workflow.core import WorkflowError
from agent_workflow.opencode import Client, Observer, connect, disconnect, endpoint, sources
from agent_workflow.server import handler


class OpenCodeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name) / 'observer'
        self.project = str(Path(self.temp.name) / 'project')
        self.rows = [{'id': 'ses_one', 'directory': self.project, 'title': '한글 세션',
                      'time': {'created': 1700000000000, 'updated': 1700000001000}}]
        self.statuses = {'ses_one': {'type': 'busy'}}
        self.messages = [{
            'info': {'id': 'msg_one', 'sessionID': 'ses_one', 'role': 'assistant',
                     'agent': '실제 구현 담당', 'providerID': 'provider', 'modelID': 'model',
                     'time': {'created': 1700000000000}},
            'parts': [{'id': 'prt_one', 'sessionID': 'ses_one', 'messageID': 'msg_one', 'type': 'tool',
                       'tool': 'bash', 'state': {'status': 'running', 'time': {'start': 1700000000001},
                                                'input': {'command': 'SECRET_INPUT'}}},
                      {'type': 'text', 'text': 'SECRET_RESPONSE'},
                      {'type': 'reasoning', 'text': 'SECRET_REASONING'}]}]
        self.calls = []
        self.fail = False
        self.redirect = False
        self.require_auth = False
        self.version = '1.3.17'
        test = self

        class API(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_GET(self):
                test.calls.append(('GET', self.path, self.headers.get('Authorization')))
                if test.redirect:
                    self.send_response(302)
                    self.send_header('Location', test.url + '/redirect-target')
                    self.end_headers()
                    return
                if test.fail or (test.require_auth and not self.headers.get('Authorization')):
                    self.send_response(401 if test.require_auth else 503)
                    self.end_headers()
                    return
                path = urlsplit(self.path).path
                value = ({'healthy': True, 'version': test.version} if path == '/global/health' else
                         test.rows if path == '/session' else test.statuses if path == '/session/status' else
                         getattr(test, 'permissions', []) if path == '/permission' else
                         getattr(test, 'questions', []) if path == '/question' else
                         test.messages if path.endswith('/message') else {'error': 'unknown'})
                body = json.dumps(value).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                test.calls.append(('POST', self.path, None))
                self.send_response(405)
                self.end_headers()

        self.api = self.server(API)
        self.url = 'http://127.0.0.1:' + str(self.api.server_port)

    def server(self, request_handler):
        server = ThreadingHTTPServer(('127.0.0.1', 0), request_handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return server

    def connect(self, **kw):
        return connect(self.home, self.url, self.project, **kw)['source']

    def request(self, server, path, body=None):
        request = Request(f'http://127.0.0.1:{server.server_port}' + path,
                          data=json.dumps(body).encode() if body is not None else None)
        try:
            with urlopen(request) as r:
                return r.status, json.load(r)
        except HTTPError as r:
            return r.code, json.load(r)

    def test_connection_never_creates_workflow_db_or_calls_mutations(self):
        source = self.connect(name='개발 세션')
        self.assertEqual(sources(self.home), [source])
        self.assertFalse((self.home / 'state.sqlite3').exists())
        self.assertTrue(all(c[0] == 'GET' for c in self.calls))
        disconnect(self.home, source['id'])
        self.assertEqual(sources(self.home), [])
        self.assertEqual(len(self.rows), 1)

    def test_new_sessions_and_idle_are_observed_without_registration(self):
        source = self.connect()
        obs = Observer(self.home)
        self.assertEqual(obs.view(source['id'])['counts']['running'], 1)
        self.rows.append({**self.rows[0], 'id': 'ses_child', 'parentID': 'ses_one'})
        self.statuses = {}
        obs.cache.clear()
        data = obs.view(source['id'])
        self.assertEqual(len(data['tasks']), 2)
        self.assertEqual({t['status'] for t in data['tasks']}, {'idle'})
        self.assertEqual(data['edges'][0]['kind'], 'parent')
        self.assertFalse((self.home / 'state.sqlite3').exists())

    def test_native_identity_tools_and_model_without_sensitive_bodies(self):
        source = self.connect()
        data = Observer(self.home).view(source['id'], 'ses_one')
        self.assertEqual(data['actors'][0]['label'], '실제 구현 담당')
        self.assertEqual(data['actors'][0]['name_source'], 'native')
        self.assertEqual(data['actors'][0]['native_session_id'], 'ses_one')
        self.assertEqual(data['actors'][0]['observed_model'], 'provider/model')
        self.assertEqual(data['spans'][1]['status'], 'running')
        self.assertEqual(data['edges'][0]['source'], 'msg_one')
        self.assertNotIn('SECRET', json.dumps(data))
        self.assertEqual(data['events'], [])

    def test_idle_does_not_imply_incomplete_message_completed(self):
        source = self.connect()
        self.statuses = {}
        data = Observer(self.home).view(source['id'], 'ses_one')
        self.assertEqual(data['task']['status'], 'idle')
        self.assertEqual(data['runs'][0]['status'], 'unknown')
        self.assertEqual(data['spans'][1]['status'], 'unknown')

    def test_previous_response_and_error_are_not_task_completion(self):
        source = self.connect()
        previous = copy.deepcopy(self.messages[0])
        previous['info'].update(id='msg_previous', time={'created': 1699999999999, 'completed': 1700000000000})
        previous['parts'] = []
        self.messages.insert(0, previous)
        self.messages[1]['info']['error'] = {'name': 'ProviderAuthError', 'data': {'message': 'SECRET_AUTH'}}
        obs = Observer(self.home)
        data = obs.view(source['id'], 'ses_one')
        self.assertEqual(data['runs'][0]['status'], 'failed')
        self.assertNotIn('SECRET_AUTH', json.dumps(data))
        data = obs.view(source['id'], 'ses_one', 'msg_previous')
        self.assertEqual(data['selected_run_id'], 'msg_previous')
        self.assertEqual(data['spans'][0]['status'], 'succeeded')
        self.assertNotEqual(data['task']['status'], 'completed')
        with self.assertRaises(WorkflowError):
            obs.view(source['id'], 'ses_one', 'invented')

    def test_project_isolation_and_session_membership(self):
        source = self.connect()
        self.rows.append({**self.rows[0], 'id': 'ses_other', 'directory': '/other-project'})
        obs = Observer(self.home)
        self.assertEqual(len(obs.view(source['id'])['tasks']), 1)
        self.assertEqual(obs.view(source['id'], project='/other-project')['tasks'], [])
        with self.assertRaises(WorkflowError):
            obs.view(source['id'], 'ses_other')
        with self.assertRaises(WorkflowError):
            obs.view('opencode-invented')

    def test_auth_stores_env_name_only_and_redacts_errors(self):
        self.require_auth = True
        with patch.dict(os.environ, {'TEST_OPENCODE_PASSWORD': 'SECRET_PASSWORD'}):
            source = self.connect(password_env='TEST_OPENCODE_PASSWORD')
            self.assertNotIn('SECRET_PASSWORD', (self.home / 'opencode.json').read_text())
            self.assertTrue(self.calls[-1][2].startswith('Basic '))
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(WorkflowError, '인증 환경변수'):
                Client(source).get('/session')

    def test_connection_loss_never_reuses_running_snapshot(self):
        source = self.connect()
        obs = Observer(self.home)
        obs.view(source['id'])
        obs.cache.clear()
        self.fail = True
        self.assertEqual(obs.ledgers()[0]['status'], 'unavailable')
        with self.assertRaises(WorkflowError):
            obs.view(source['id'])

    def test_redirects_and_nonlocal_urls_are_rejected(self):
        for url in ['http://example.com:4096', 'http://user:password@localhost:4096',
                    'http://localhost:4096/path', 'http://localhost:4096?x=1', 'file:///tmp/a', 'http://localhost']:
            with self.subTest(url=url), self.assertRaises(WorkflowError):
                endpoint(url)
        self.redirect = True
        with self.assertRaisesRegex(WorkflowError, 'HTTP 302'):
            self.connect()
        self.assertFalse(any('/redirect-target' in c[1] for c in self.calls))

    def test_unsupported_response_and_versions_do_not_save_connection(self):
        self.version = '2.0.0'
        with self.assertRaises(WorkflowError):
            self.connect()
        self.version = '1.3.17'
        self.rows = {'bad': 'shape'}
        with self.assertRaises(WorkflowError):
            self.connect()
        self.assertFalse((self.home / 'opencode.json').exists())

    def test_session_limit_and_empty_response(self):
        source = self.connect()
        obs = Observer(self.home)
        self.rows = [{**self.rows[0], 'id': f'ses_{i}'} for i in range(201)]
        data = obs.view(source['id'])
        self.assertEqual(len(data['tasks']), 200)
        self.assertTrue(data['truncated']['tasks'])
        self.rows = []
        obs.cache.clear()
        self.assertEqual(obs.view(source['id'])['counts']['tasks'], 0)

    def test_http_integration_works_without_any_sqlite_ledger(self):
        source = self.connect()
        with patch('agent_workflow.connections.discover', return_value={'ledgers': [], 'warnings': []}):
            server = self.server(handler(self.home, 'test', read_only=True, observer=True))
            code, data = self.request(server, '/api/connections')
            self.assertEqual(code, 200)
            self.assertEqual(data['ledgers'][0]['kind'], 'opencode')
            query = '?ledger=' + source['id']
            code, data = self.request(server, '/api/project-observability' + query)
            self.assertEqual(code, 200)
            self.assertEqual(data['counts']['running'], 1)
            code, data = self.request(server, '/api/observability' + query + '&task_id=ses_one')
            self.assertEqual(code, 200)
            self.assertEqual(data['source'], 'opencode')
            self.assertEqual(self.request(server, '/api/queue' + query, {'task_id': 'ses_one'})[0], 403)
        self.assertFalse((self.home / 'state.sqlite3').exists())
        self.assertTrue(all(c[0] == 'GET' for c in self.calls))


if __name__ == '__main__':
    unittest.main()
