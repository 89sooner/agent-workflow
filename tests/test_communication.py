import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import test_workflow as fixtures
from agent_workflow.cli import continuation, export_handoff
from agent_workflow.communication import AGENT_COMMUNICATION_POLICY
from agent_workflow.core import complete, encode, snapshot
from agent_workflow.runner import RESULT_SCHEMA, brief, invoke, validate_result


class CommunicationTests(unittest.TestCase):
    setUp = fixtures.WorkflowTests.setUp
    tearDown = fixtures.WorkflowTests.tearDown
    task = fixtures.WorkflowTests.task
    checked = fixtures.WorkflowTests.checked

    def test_implementation_review_and_feedback_preserve_original_data(self):
        task = self.task()
        task['goal'] = '한국어 목표\nKeep the literal token summary.'
        task['implementation'] = {'summary': '기존 구현 보고'}
        feedback = {'detail': '원문 로그: /tmp/한글 file.py', 'blocking': True}
        for role in ('implement', 'review'):
            with self.subTest(role=role):
                prompt = brief(task, role, feedback, self.store.home)
                self.assertTrue(prompt.startswith(AGENT_COMMUNICATION_POLICY))
                self.assertIn(task['goal'], prompt)
                self.assertIn(encode(task['criteria']), prompt)
                self.assertIn(encode(task['checks']), prompt)
                self.assertIn(encode(feedback), prompt)
                self.assertIn('not new authorization', prompt)
                self.assertIn('Do not edit controller metadata', prompt)
                if role == 'review':
                    self.assertIn(encode(task['implementation']), prompt)
                    self.assertIn('Do not modify project files.', prompt)
                else:
                    self.assertIn('set outcome to ready', prompt)
        self.assertEqual(task['implementation']['summary'], '기존 구현 보고')

    def test_empty_and_large_evidence_remain_unchanged(self):
        task = self.task(checks=[])
        task['refs'] = []
        self.assertTrue(brief(task).startswith(AGENT_COMMUNICATION_POLICY))
        feedback = {'stdout': ('한글 JSON {} /tmp/a b\n' * 10000)}
        self.assertIn(encode(feedback), brief(task, feedback=feedback))

    def test_both_providers_receive_and_save_the_policy_without_model_calls(self):
        task = self.task()
        captured = []

        def fake_command(agent, role, directory, model=None):
            captured.append(directory)
            answer = {'outcome': 'pass' if role == 'review' else 'ready',
                      'summary': 'The source meets the task requirements.',
                      'next_action': 'Run the required checks.', 'findings': []}
            script = (
                'import json,sys; from pathlib import Path; '
                'Path(sys.argv[1]).write_text(sys.stdin.read()); '
                'answer=json.loads(sys.argv[2]); '
                'Path(sys.argv[3]).write_text(json.dumps(answer)); '
                'print(json.dumps({"structured_output":answer}))'
            )
            return [sys.executable, '-c', script, str(directory / 'received.txt'),
                    json.dumps(answer), str(directory / 'answer.json')]

        with self.store.lock(task['project']) as fd:
            for agent, role in (('codex', 'implement'), ('claude', 'review')):
                with patch('agent_workflow.runner.command_for', side_effect=fake_command):
                    invoke(self.store, task, agent, role, 5, fd)
                prompt = (captured[-1] / 'prompt.txt').read_text()
                self.assertTrue(prompt.startswith(AGENT_COMMUNICATION_POLICY))
                self.assertEqual(prompt, (captured[-1] / 'received.txt').read_text())

    def test_interactive_resume_keeps_its_contract_and_quoted_home(self):
        task = self.task()
        text = continuation(self.store, task, snapshot(self.project))
        self.assertTrue(text.startswith(AGENT_COMMUNICATION_POLICY))
        self.assertIn(task['goal'], text)
        self.assertIn(f'--home {self.store.home} show {task["id"]}', text)
        self.assertIn('If the user requested automatic execution', text)
        self.assertNotIn('Return JSON', text)

    def test_completed_stale_release_handoff_keeps_safety_instructions(self):
        task = self.checked(self.task())
        complete(self.store, task)
        task['release'] = {'stage': 'configured'}
        (self.project / 'app.py').write_text('VALUE = 2\n')
        directory = Path(export_handoff(self.store, task))
        text = (directory / 'resume.txt').read_text()
        self.assertTrue(text.startswith(AGENT_COMMUNICATION_POLICY))
        self.assertIn('Do not implement this task again.', text)
        self.assertIn('Create a new task to verify the current code.', text)
        self.assertIn('To resume the authorized release', text)
        record = json.loads((directory / 'state.json').read_text())
        self.assertEqual(record['task']['goal'], task['goal'])
        self.assertFalse(record['completion_is_current'])

    def test_language_policy_does_not_change_or_reject_result_contract(self):
        answer = {'outcome': 'ready', 'summary': '원문 보존', 'next_action': '',
                  'findings': []}
        self.assertEqual(validate_result(answer, 'implement'), answer)
        self.assertEqual(set(answer), set(RESULT_SCHEMA['required']))

    def test_cli_briefs_and_handoff_use_policy_without_execution(self):
        task = self.task()
        command = [sys.executable, '-m', 'agent_workflow', '--home',
                   str(self.store.home)]
        for role in ('implement', 'review'):
            output = subprocess.run(command + ['brief', task['id'], '--role', role],
                                    check=True, text=True, capture_output=True).stdout
            self.assertTrue(output.startswith(AGENT_COMMUNICATION_POLICY))
            self.assertIn(task['goal'], output)
            self.assertEqual('Return JSON' in output, role == 'review')
        subprocess.run(command + ['handoff', task['id']], check=True, capture_output=True)
        prompt = self.store.directory(task['id']) / 'handoff' / 'resume.txt'
        self.assertTrue(prompt.read_text().startswith(AGENT_COMMUNICATION_POLICY))
        self.assertIsNone(self.store.get(task['id'])['last_run'])


if __name__ == '__main__':
    unittest.main()
