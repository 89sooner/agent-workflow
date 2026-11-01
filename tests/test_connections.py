from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import test_workflow as fixtures
from agent_workflow import connections, profiles
from agent_workflow.cli import dispatch, parser
from agent_workflow.core import Store, WorkflowError, create_task, git


class ConnectionTests(unittest.TestCase):
    setUp = fixtures.WorkflowTests.setUp
    tearDown = fixtures.WorkflowTests.tearDown
    task = fixtures.WorkflowTests.task

    def discover(self, homes, project=None):
        return connections.discover(project, homes, include_processes=False,
                                    include_temporary=False, include_default=False)

    def connect(self, home=None, **kwargs):
        return connections.connect(home or self.root / 'observer', self.project,
                                   include_processes=False, include_temporary=False, include_default=False, **kwargs)

    def test_read_only_discovery_deduplicates_aliases_and_groups_worktrees(self):
        task = self.task('SAME-ID')
        tree = self.root / 'linked worktree'
        git(self.project, 'worktree', 'add', '-qb', 'test-connection', str(tree))
        second = Store(self.root / 'second')
        try:
            create_task(second, 'SAME-ID', tree, 'second session', ['criterion'], [], [], 'claude', 'none', 'local')
            alias = self.root / 'state-alias'
            alias.symlink_to(self.store.home, target_is_directory=True)
            before = self.store.db.execute('SELECT * FROM sqlite_master ORDER BY name').fetchall()
            found = self.discover([self.store.home, alias, second.home], tree)
            self.assertEqual(len(found['ledgers']), 2)
            self.assertEqual([r['matching_task_count'] for r in found['ledgers']], [1, 1])
            self.assertEqual(connections.project_key(self.project), connections.project_key(tree))
            self.assertNotEqual(connections.task_key(self.store.home, task['id']), connections.task_key(second.home, task['id']))
            self.assertEqual(self.store.db.execute('SELECT * FROM sqlite_master ORDER BY name').fetchall(), before)
            self.assertEqual(self.store.get(task['id'])['revision'], task['revision'])
            self.assertFalse((self.store.home / 'server.json').exists())
        finally:
            second.close()

    def test_missing_corrupt_and_empty_ledgers_are_visible_without_initializing(self):
        missing = self.root / 'missing'
        corrupt = self.root / 'corrupt'
        corrupt.mkdir()
        (corrupt / 'state.sqlite3').write_bytes(b'not sqlite')
        found = self.discover([missing, corrupt, self.store.home])
        self.assertEqual(len(found['warnings']), 2)
        self.assertEqual(len(found['ledgers']), 3)
        self.assertFalse(missing.exists())
        self.assertEqual((corrupt / 'state.sqlite3').read_bytes(), b'not sqlite')
        self.assertEqual(next(r for r in found['ledgers'] if r['status'] == 'available')['task_count'], 0)

    def test_connect_persists_only_registry_and_remembers_nonstandard_ledger(self):
        self.task()
        home = self.root / 'observer'
        before = self.store.db.execute('SELECT count(*) FROM events').fetchone()[0]
        result = self.connect(home, ledgers=[self.store.home], criteria=['실제 검사 통과'])
        self.assertFalse(result['execution_started'])
        self.assertFalse((home / 'state.sqlite3').exists())
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM events').fetchone()[0], before)
        refreshed = self.connect(home)
        self.assertEqual(refreshed['connection']['registration_home'], str(self.store.home))
        self.assertEqual(refreshed['connection']['criteria'], ['실제 검사 통과'])
        self.assertEqual(connections.resolve_ledger(home, connections.ledger_id(self.store.home)), self.store.home)
        with self.assertRaisesRegex(WorkflowError, '등록되지 않은'):
            connections.resolve_ledger(home, 'ledger-unknown')

    def test_connect_invalid_explicit_home_does_not_write_registry(self):
        home = self.root / 'observer'
        with self.assertRaisesRegex(WorkflowError, '지정한 원장'):
            self.connect(home, ledgers=[self.root / 'missing'])
        self.assertFalse(home.exists())

    def test_start_reuses_saved_profile_criteria_and_assigned_names_without_execution(self):
        profiles.save(self.store, 'trusted', {'project': str(self.project),
                     'checks': [[sys.executable, '-c', 'raise SystemExit(73)']],
                     'workspace': {'isolate': False}, 'owner': 'codex', 'reviewer': 'claude',
                     'allowed_paths': ['app.py']})
        home = self.root / 'observer'
        self.connect(home, ledgers=[self.store.home], profile='trusted', criteria=['완료 조건'])
        result = connections.start_registered(home, '이름 있는 새 작업', self.project,
                                              name='설계 담당 수민', reviewer_name='리뷰 담당 민수')
        task = result['task']
        self.assertFalse(result['execution_started'])
        self.assertEqual(task['status'], 'ready')
        self.assertEqual(task['criteria'], ['완료 조건'])
        self.assertEqual(task['owner'], 'codex')
        self.assertEqual(task['reviewer'], 'claude')
        self.assertEqual(task['agent_names'], {'implement': '설계 담당 수민', 'review': '리뷰 담당 민수'})
        self.assertEqual(task['checks'][0][-1], 'raise SystemExit(73)')
        self.assertIsNone(task['last_run'])
        self.assertIsNone(task['verification'])
        self.assertFalse(self.store.db.execute("SELECT 1 FROM sqlite_master WHERE name='jobs'").fetchone())
        self.assertEqual(result['state_home'], str(self.store.home))
        self.assertTrue(task['id'].startswith('AW-'))

    def test_multiple_ledgers_require_explicit_registration_target(self):
        self.task()
        second = Store(self.root / 'second')
        try:
            create_task(second, 'SECOND', self.project, 'goal', ['criterion'], [], [], 'claude', 'none', 'local')
            home = self.root / 'observer'
            self.connect(home, ledgers=[self.store.home, second.home], criteria=['criterion'])
            with self.assertRaisesRegex(WorkflowError, '여러 개'):
                connections.start_registered(home, 'goal', self.project)
            self.connect(home, ledgers=[self.store.home, second.home], registration_home=second.home)
            result = connections.start_registered(home, 'goal', self.project)
            self.assertEqual(result['state_home'], str(second.home))
        finally:
            second.close()

    def test_criteria_cannot_be_silently_invented_and_invalid_names_leave_no_task(self):
        home = self.root / 'observer'
        self.connect(home, ledgers=[self.store.home])
        with self.assertRaisesRegex(WorkflowError, '완료 조건'):
            connections.start_registered(home, 'natural language alone', self.project)
        with self.assertRaisesRegex(WorkflowError, '이름'):
            connections.start_registered(home, 'goal', self.project, ['criterion'], name='bad\nname')
        self.assertEqual(self.store.all(), [])

    def test_process_discovery_ignores_ops_server_and_shell_text(self):
        # A fake /proc tree tests recognition without inspecting unrelated session bodies.
        proc = self.root / 'proc'
        proc.mkdir()
        commands = [[b'/usr/bin/python3', b'-m', b'agent_workflow', b'--home', b'/tmp/valid', b'run', b'ID'],
                    [b'/usr/bin/python3', b'/app/run_ops_control_server.py', b'--home', b'/tmp/not-agent'],
                    [b'/bin/bash', b'-c', b'python -m agent_workflow --home /tmp/shell'],
                    [b'/bin/agent-workflow', b'--home=/tmp/cli', b'list']]
        for i, args in enumerate(commands):
            (proc / str(i)).mkdir()
            (proc / str(i) / 'cmdline').write_bytes(b'\0'.join(args) + b'\0')
        real_path = Path
        with patch.object(connections, 'Path', side_effect=lambda p: proc if p == '/proc' else real_path(p)):
            self.assertEqual(list(connections._process_homes()), [Path('/tmp/valid'), Path('/tmp/cli')])

    def test_refresh_retains_missing_ledger_and_never_recreates_existing_database(self):
        self.task()
        home = self.root / 'observer'
        self.connect(home, ledgers=[self.store.home], criteria=['criterion'])
        database = self.store.home / 'state.sqlite3'
        unavailable = database.with_suffix('.offline')
        database.rename(unavailable)
        try:
            with self.assertRaisesRegex(WorkflowError, '사라졌습니다'):
                connections.start_registered(home, 'goal', self.project)
            self.assertFalse(database.exists())
            refreshed = self.connect(home)
            self.assertIsNone(refreshed['connection']['registration_home'])
            self.assertIn(str(self.store.home), [r['home'] for r in refreshed['connection']['ledgers']])
            self.assertTrue(refreshed['discovery']['warnings'])
        finally:
            unavailable.rename(database)
        refreshed = self.connect(home)
        self.assertEqual(refreshed['connection']['registration_home'], str(self.store.home))

    def test_cli_watch_does_not_initialize_store_and_start_dispatches_registration(self):
        home = self.root / 'observer'
        with patch('agent_workflow.server.serve') as serve:
            result = dispatch(parser().parse_args(['--home', str(home), 'watch', '--port', '8878']))
            serve.assert_called_once_with(home, 8878, read_only=True, observer=True)
            self.assertTrue(result['stopped'])
        self.assertFalse(home.exists())
        self.connect(home, ledgers=[self.store.home], criteria=['criterion'])
        result = dispatch(parser().parse_args(['--home', str(home), 'start', 'new work', '--project', str(self.project), '--name', '실제 이름']))
        self.assertEqual(result['task']['agent_names']['implement'], '실제 이름')
        self.assertFalse(result['execution_started'])


if __name__ == '__main__':
    unittest.main()
