import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from agent_workflow.overview import snapshot

class OverviewTests(unittest.TestCase):
    def test_collision_partial_failure_and_read_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);ledgers=[]
            for name in ['one','two']:
                home=root/name;home.mkdir()
                with sqlite3.connect(home/'state.sqlite3') as c:
                    c.execute('CREATE TABLE tasks(data TEXT)')
                    c.execute('INSERT INTO tasks VALUES(?)',(json.dumps(dict(id='SAME',project=str(root/'project'),goal=name,status='running')),))
                ledgers.append(dict(id=name,home=str(home),status='available'))
            before={p:p.read_bytes() for p in root.rglob('*.sqlite3')}
            ledgers.append(dict(id='missing',home=str(root/'missing'),status='available'))
            native=dict(sessions=[dict(id='native-id',source='plugin',native_id='SAME',project=str(root/'project'),name='Codex',state='approval',provider='codex',fresh=True)])
            result=snapshot(ledgers,native)
            self.assertEqual(len(result['items']),3)
            self.assertEqual(len({i['id'] for i in result['items']}),3)
            self.assertEqual(len(result['projects']),1)
            self.assertEqual(result['projects'][0]['managed'],2)
            self.assertEqual(len(result['errors']),1)
            self.assertFalse((root/'missing').exists())
            self.assertEqual(before,{p:p.read_bytes() for p in root.rglob('*.sqlite3')})
            self.assertFalse(result['execution_started'])

    def test_opencode_is_not_double_counted(self):
        result=snapshot([dict(id='api',kind='opencode')],dict(sessions=[]))
        self.assertEqual(result['items'],[])
