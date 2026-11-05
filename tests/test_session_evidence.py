import tempfile,json
from pathlib import Path
import unittest
from agent_workflow import native,session_evidence as evidence
from agent_workflow.core import WorkflowError

class EvidenceTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
  self.root=Path(self.tmp.name);self.home=self.root/'home';self.project=self.root/'project';self.project.mkdir()
  native.install(self.home,self.project)
  native.ingest(self.home,self.project,dict(session_id='test',hook_event_name='SessionStart',cwd=str(self.project)))
  self.sid=native.snapshot(self.home,None,refresh=False)['sessions'][0]['id']
 def test_read_no_create_and_explicit_pr_idempotent(self):
  self.assertEqual(evidence.view(self.home,self.sid)['links'],[])
  self.assertFalse((self.home/'observer-metadata.sqlite3').exists())
  payload=dict(action='add',session=self.sid,kind='pr',url='https://github.com/org/repo/pull/12')
  a=evidence.change(self.home,payload);b=evidence.change(self.home,payload)
  self.assertEqual(a['id'],b['id']);self.assertEqual(len(evidence.view(self.home,self.sid)['links']),1)
  self.assertFalse((self.home/'state.sqlite3').exists())
  evidence.change(self.home,dict(action='remove',session=self.sid,id=a['id']))
  self.assertEqual(evidence.view(self.home,self.sid)['links'],[])
 def test_reject_unregistered_ledger_urls_and_session(self):
  for url in ['javascript:alert(1)','http://github.com/o/r/pull/1','https://github.com@evil.example/o/r/pull/1','https://github.com/o/r/pull/1?token=secret']:
   with self.assertRaises(WorkflowError):evidence.change(self.home,dict(action='add',session=self.sid,kind='pr',url=url))
  with self.assertRaises(WorkflowError):evidence.change(self.home,dict(action='add',session=self.sid,kind='workflow',ledger='/tmp',task='x'))
  self.assertEqual(evidence.view(self.home,'0'*32)['links'],[])

 def test_workflow_link_reads_only_explicit_ledger(self):
  from agent_workflow.core import Store
  home=self.root/'ledger';store=Store(home)
  task=dict(id='T-1',status='completed',verification=None,workspace={'branch':'fixture-branch'})
  store.db.execute('INSERT INTO tasks(id,data,revision) VALUES(?,?,?)',('T-1',json.dumps(task),1));store.db.commit();store.close()
  from agent_workflow.connections import ledger_id
  identifier=ledger_id(home)
  (self.home/'connections.json').write_text(json.dumps(dict(version=1,projects=[dict(id='p',path=str(self.project),ledgers=[dict(home=str(home))])])))
  before=(home/'state.sqlite3').read_bytes()
  evidence.change(self.home,dict(action='add',session=self.sid,kind='workflow',ledger=identifier,task='T-1'))
  row=evidence.view(self.home,self.sid)['links'][0]
  self.assertIsNone(row['check_passed']);self.assertEqual(row['branch'],'fixture-branch')
  self.assertIn(identifier,row['url']);self.assertEqual(row['attribution'],'user_selected')
  self.assertEqual(before,(home/'state.sqlite3').read_bytes())
  (self.home/'connections.json').write_text(json.dumps(dict(version=1,projects=[])))
  self.assertEqual(evidence.view(self.home,self.sid)['links'][0]['status'],'근거 접근 불가')

 def test_orphan_evidence_is_discoverable_removable_and_frees_capacity(self):
  import sqlite3
  payload=dict(action='add',session=self.sid,kind='pr',url='https://github.com/org/repo/pull/12')
  key=evidence.change(self.home,payload)['id']
  with sqlite3.connect(self.home/'native-observation.sqlite3') as c:c.execute('DELETE FROM sessions WHERE id=?',(self.sid,))
  self.assertEqual(evidence.view(self.home,'')['links'][0]['session_id'],self.sid)
  self.assertEqual(evidence.view(self.home,self.sid)['links'][0]['id'],key)
  with sqlite3.connect(self.home/'observer-metadata.sqlite3') as c:
   row=c.execute('SELECT * FROM evidence').fetchone()
   c.executemany('INSERT INTO evidence VALUES(?,?,?,?,?)',[(f'{i:032x}',*row[1:]) for i in range(999)])
  native.ingest(self.home,self.project,dict(session_id='new',hook_event_name='SessionStart',cwd=str(self.project)))
  sid=native.snapshot(self.home,None,refresh=False)['sessions'][0]['id']
  payload['session']=sid
  with self.assertRaises(WorkflowError):evidence.change(self.home,payload)
  evidence.change(self.home,dict(action='remove',session=self.sid,id=key))
  evidence.change(self.home,payload)
