import json
import tempfile
import unittest
from pathlib import Path

from agent_workflow import diagnostics, native
from agent_workflow.core import WorkflowError


class DiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.home = self.root / 'state'
        self.project = self.root / 'project'
        self.project.mkdir()

    def diagnose(self, provider='claude'):
        result = diagnostics.connection(self.home, str(self.project), provider)
        self.assertFalse(result['execution_started'])
        return {s['id']: s['status'] for s in result['steps']}

    def test_empty_is_read_only(self):
        self.assertEqual(self.diagnose()['first_event'], 'unknown')
        self.assertFalse(self.home.exists())
        self.assertEqual(list(self.project.iterdir()), [])
        with self.assertRaises(WorkflowError):
            diagnostics.connection(self.home, 'relative', 'claude')

    def test_direct_hooks_and_host_unknown(self):
        for provider in ['claude', 'codex']:
            native.install(self.home, self.project, provider=provider)
            before = {p: p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
            result = self.diagnose(provider)
            self.assertEqual(result['installation'], 'pass')
            self.assertEqual(result['host_activation'], 'unknown')
            self.assertEqual(before, {p: p.read_bytes() for p in self.root.rglob('*') if p.is_file()})
        setting = self.project / '.codex/hooks.json'
        setting.write_text('{}')
        self.assertEqual(self.diagnose('codex')['installation'], 'fail')

    def test_plugin_evidence_and_provider_isolation(self):
        native.register(self.home, self.project, 'opencode')
        native.record(self.home, 'opencode', 'plugin:x', 'one', str(self.project), 'Agent', 'idle', 'session.created')
        self.assertEqual(self.diagnose('opencode')['first_event'], 'pass')
        self.assertEqual(self.diagnose('opencode')['installation'], 'unknown')
        self.assertEqual(self.diagnose('claude')['first_event'], 'unknown')
        self.assertFalse((self.home / 'state.sqlite3').exists())

    def test_corrupt_registration_and_deleted_project(self):
        self.home.mkdir()
        (self.home / 'native-sources.json').write_text('{')
        self.assertEqual(self.diagnose()['registration'], 'fail')
        self.project.rmdir()
        self.assertEqual(self.diagnose()['project'], 'fail')

    def test_malformed_registry_entries_fail_closed(self):
        self.home.mkdir()
        for value in [[None], ['broken'], [3]]:
            (self.home / 'native-sources.json').write_text(json.dumps(value))
            self.assertEqual(self.diagnose()['registration'], 'fail')

    def test_invalid_mode_does_not_echo_registry_payload(self):
        self.home.mkdir()
        for mode in [5, [], 'SECRET_BAD_MODE']:
            (self.home / 'native-sources.json').write_text(json.dumps([
                {'project': str(self.project), 'provider': 'claude', 'mode': mode}]))
            result = diagnostics.connection(self.home, str(self.project), 'claude')
            self.assertIsNone(result['mode'])
            self.assertIsNone(result['last_seen'])
            self.assertEqual(result['steps'][-1]['status'], 'fail')
            self.assertNotIn('SECRET_BAD_MODE', json.dumps(result))

    def test_malformed_hooks_and_unsupported_provider(self):
        native.install(self.home, self.project)
        path = self.project / '.claude/settings.local.json'
        for value in ['{', '{"hooks":[]}', '{"hooks":{"Stop":["bad"]}}']:
            path.write_text(value)
            self.assertEqual(self.diagnose()['installation'], 'fail')
        with self.assertRaises(WorkflowError):
            diagnostics.connection(self.home, str(self.project), 'unsupported')
