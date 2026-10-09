import json
import sys
import unittest
from unittest.mock import patch

import test_workflow as fixtures
from agent_workflow.core import WorkflowError, create_task, snapshot
from agent_workflow.runner import command_for, execute, invoke, record_review, run_checks


class IndependentSessionTests(unittest.TestCase):
    setUp = fixtures.WorkflowTests.setUp
    tearDown = fixtures.WorkflowTests.tearDown
    result = staticmethod(fixtures.WorkflowTests.result)

    def task(self):
        return create_task(self.store, 'SAME-PROVIDER', self.project, 'value one', ['VALUE is 1'], [],
                           [[sys.executable,'-c','import app; assert app.VALUE == 1']],
                           'codex','codex-session','local only')

    def fake_review(self, agent, role, directory, model=None):
        self.assertEqual((agent,role),('codex-session','review'))
        code="from pathlib import Path; import sys; sys.stdin.read(); Path(sys.argv[1]).write_text(sys.argv[2])"
        return [sys.executable,'-c',code,str(directory/'answer.json'),json.dumps(self.result())]

    def test_manual_passing_json_cannot_replace_real_separate_run(self):
        task=self.task()
        with self.store.lock(task['project']) as fd: run_checks(self.store,task,5,fd)
        with self.assertRaisesRegex(WorkflowError,'실제 별도'):
            record_review(self.store,task,self.result(),snapshot(self.project)['fingerprint'],'codex-session')
        with patch('agent_workflow.runner.command_for',side_effect=self.fake_review):
            result=execute(self.store,task['id'],timeout=5,repairs=0)
        self.assertEqual(result['status'],'completed')
        self.assertEqual(result['review']['independent_run'],result['native_review_receipt']['directory'])

    def test_contract_change_invalidates_native_receipt(self):
        task=self.task()
        with self.store.lock(task['project']) as fd:
            with patch('agent_workflow.runner.command_for',side_effect=self.fake_review):
                answer=invoke(self.store,task,'codex-session','review',5,fd)
            task['checks'].append([sys.executable,'-c','print(1)'])
            with self.assertRaisesRegex(WorkflowError,'작업 계약'):
                record_review(self.store,task,answer,snapshot(self.project)['fingerprint'],'codex-session')

    def test_session_command_cannot_implement_or_resume_owner_history(self):
        with patch('agent_workflow.runner.shutil.which',return_value='/usr/bin/codex'):
            cmd=command_for('codex-session','review',self.root)
            self.assertIn('read-only',cmd)
            self.assertIn('exec',cmd)
            self.assertNotIn('resume',cmd)
            with self.assertRaises(WorkflowError): command_for('codex-session','implement',self.root)


if __name__ == '__main__': unittest.main()
