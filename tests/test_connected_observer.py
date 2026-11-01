"""Exercise HTTP ledger switching without touching observed databases or workers."""
import json
from pathlib import Path
import threading
import unittest
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from unittest.mock import patch

import test_workflow as fixtures
from agent_workflow.connections import connect, ledger_id, project_key
from agent_workflow.core import Store, create_task, git
from agent_workflow.profiles import save
from agent_workflow.server import handler


class ConnectedObserverTests(unittest.TestCase):
    setUp = fixtures.WorkflowTests.setUp
    tearDown = fixtures.WorkflowTests.tearDown
    task = fixtures.WorkflowTests.task

    def request(self, server, path, body=None):
        request = Request(f'http://127.0.0.1:{server.server_port}' + path,
                          data=json.dumps(body).encode() if body is not None else None,
                          headers={'X-Workflow-Token': 'fixture'})
        try:
            with urlopen(request, timeout=5) as response:
                return response.status, json.load(response)
        except HTTPError as response:
            return response.code, json.load(response)

    def serve(self, home, **kwargs):
        server = ThreadingHTTPServer(('127.0.0.1', 0), handler(home, 'fixture', **kwargs))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return server

    def test_connections_switch_same_task_ids_and_reject_paths_or_writes(self):
        self.task('SAME')
        second = Store(self.root / 'second')
        self.addCleanup(second.close)
        create_task(second, 'SAME', self.project, 'second goal', ['criterion'], [], [], 'claude', 'none', 'local')
        home = self.root / 'observer'
        Store(home).close()
        connect(home, self.project, ledgers=[self.store.home, second.home],
                include_processes=False, include_temporary=False, include_default=False)
        before = {s.home: (s.db.execute('SELECT count(*) FROM events').fetchone()[0],
                          list(s.db.execute('SELECT name FROM sqlite_master ORDER BY name'))) for s in (self.store, second)}
        server = self.serve(home, read_only=True)
        code, connections = self.request(server, '/api/connections')
        self.assertEqual(code, 200)
        self.assertEqual(len(connections['ledgers']), 3)
        results = []
        for s in (self.store, second):
            query = '?ledger=' + ledger_id(s.home)
            code, result = self.request(server, '/api/state' + query)
            self.assertEqual(code, 200)
            self.assertTrue(result['read_only'])
            results.append(result['tasks'][0]['task_key'])
            self.assertEqual(self.request(server, '/api/project-observability' + query)[0], 200)
            self.assertEqual(self.request(server, '/api/tasks/SAME' + query)[0], 200)
            self.assertEqual(self.request(server, '/api/observability' + query + '&task_id=SAME')[0], 200)
            self.assertEqual(self.request(server, '/api/queue' + query, {'task_id': 'SAME'})[0], 403)
            self.assertEqual(before[s.home], (s.db.execute('SELECT count(*) FROM events').fetchone()[0],
                                             list(s.db.execute('SELECT name FROM sqlite_master ORDER BY name'))))
        self.assertNotEqual(*results)
        self.assertEqual(self.request(server, '/api/state?ledger=/etc/passwd')[0], 400)
        self.assertFalse((home / 'daemon.lock').exists())

    def test_empty_observer_never_creates_database(self):
        home = self.root / 'empty-observer'
        with patch('agent_workflow.connections.discover', return_value={'ledgers': [], 'warnings': []}):
            server = self.serve(home, read_only=True, observer=True)
            code, result = self.request(server, '/api/state')
            self.assertEqual((code, result['tasks'], result['read_only']), (200, [], True))
            self.assertEqual(self.request(server, '/api/project-observability')[1]['actors'], [])
            self.assertFalse(home.exists())

    def test_auto_id_and_explicit_names_preserved_without_execution(self):
        save(self.store, 'fixture', {'project': str(self.project), 'workspace': {'isolate': False}})
        server = self.serve(self.store.home)
        body = {'profile': 'fixture', 'goal': 'new named assignment', 'criteria': ['criterion'],
                'agent_names': {'implement': '지우', 'review': '민수'}}
        code, result = self.request(server, '/api/tasks', body)
        self.assertEqual(code, 201)
        task = result['task']
        self.assertTrue(task['id'].startswith('AW-'))
        self.assertEqual(task['agent_names'], body['agent_names'])
        self.assertEqual(task['status'], 'ready')
        self.assertIsNone(task['last_run'])
        count = len(self.store.all())
        self.assertEqual(self.request(server, '/api/tasks', {**body, 'agent_names': {'implement': '\n'}})[0], 400)
        self.assertEqual(len(self.store.all()), count)

    def test_project_overview_groups_related_worktrees(self):
        task = self.task('MAIN')
        tree = self.root / 'related'
        git(self.project, 'worktree', 'add', '-qb', 'connected-related', str(tree))
        create_task(self.store, 'TREE', tree, 'worktree task', ['criterion'], [], [], 'claude', 'none', 'local')
        from agent_workflow.observability import project_view
        data = project_view(self.store, tree)
        self.assertEqual({t['id'] for t in data['tasks']}, {task['id'], 'TREE'})
        server = self.serve(self.store.home, read_only=True)
        tasks = self.request(server, '/api/state')[1]['tasks']
        self.assertEqual(len({t['project_source'] for t in tasks}), 1)
        self.assertEqual({t['project_key'] for t in tasks}, {project_key(tree)})


if __name__ == '__main__':
    unittest.main()
