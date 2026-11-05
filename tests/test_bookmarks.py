import tempfile,sqlite3,json
from pathlib import Path
import unittest
from agent_workflow import native,bookmarks
from agent_workflow.core import WorkflowError
class BookmarkTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
  self.home=Path(self.tmp.name)/'home';self.project=Path(self.tmp.name)/'project';self.project.mkdir()
  native.install(self.home,self.project)
  for event in ['SessionStart','PermissionRequest','Stop']:
   native.ingest(self.home,self.project,dict(session_id='test',hook_event_name=event,cwd=str(self.project),prompt='PRIVATE_PROMPT'))
  row=native.snapshot(self.home,None,refresh=False)['sessions'][0];self.sid=row['id']
 def payload(self,**kw):return dict(action='add',session=self.sid,label='sample',first_seq=1,last_seq=3,**kw)
 def test_reference_export_expiry_and_remove(self):
  key=bookmarks.change(self.home,self.payload())['id']
  value=bookmarks.export(self.home,key);self.assertTrue(value['complete']);self.assertEqual(len(value['events']),3)
  self.assertNotIn('PRIVATE_PROMPT',json.dumps(value))
  with sqlite3.connect(self.home/'native-observation.sqlite3') as c:c.execute('DELETE FROM history WHERE seq=1')
  self.assertTrue(bookmarks.view(self.home)['bookmarks'][0]['expired'])
  self.assertFalse(bookmarks.export(self.home,key)['complete'])
  bookmarks.change(self.home,dict(action='remove',id=key));self.assertEqual(bookmarks.view(self.home)['bookmarks'],[])
  self.assertFalse((self.home/'state.sqlite3').exists())
 def test_invalid_ranges_and_read_without_write(self):
  self.assertEqual(bookmarks.view(self.home)['bookmarks'],[])
  self.assertFalse((self.home/'observer-metadata.sqlite3').exists())
  for a,b in [(3,1),(0,1),(1,100),(True,2),(1,2**63),(10**100,10**100)]:
   payload=self.payload();payload.update(first_seq=a,last_seq=b)
   with self.assertRaises(WorkflowError):bookmarks.change(self.home,payload)

 def test_interior_pruning_marks_incomplete(self):
  key=bookmarks.change(self.home,self.payload())['id']
  with sqlite3.connect(self.home/'native-observation.sqlite3') as c:c.execute('DELETE FROM history WHERE seq=2')
  self.assertTrue(bookmarks.view(self.home)['bookmarks'][0]['expired'])
  self.assertFalse(bookmarks.export(self.home,key)['complete'])

 def test_deleted_session_expires_even_if_orphan_events_remain(self):
  key=bookmarks.change(self.home,self.payload())['id']
  with sqlite3.connect(self.home/'native-observation.sqlite3') as c:c.execute('DELETE FROM sessions WHERE id=?',(self.sid,))
  self.assertTrue(bookmarks.view(self.home)['bookmarks'][0]['expired'])
  self.assertFalse(bookmarks.export(self.home,key)['complete'])

 def test_provenance_survives_source_pruning(self):
  key=bookmarks.change(self.home,self.payload())['id']
  with sqlite3.connect(self.home/'native-observation.sqlite3') as c:
   identity=c.execute('SELECT provider,source,native_id FROM sessions WHERE id=?',(self.sid,)).fetchone()
   c.execute('DELETE FROM sessions WHERE id=?',(self.sid,))
  row=bookmarks.export(self.home,key)['bookmark']
  self.assertEqual(tuple(row[k] for k in ('provider','source','native_id')),identity)
  self.assertTrue(bookmarks.view(self.home)['bookmarks'][0]['expired'])

 def test_legacy_metadata_migrates_with_unknown_provenance(self):
  key=bookmarks.change(self.home,self.payload())['id']
  with sqlite3.connect(self.home/'observer-metadata.sqlite3') as c:
   for field in ('provider','source','native_id'):c.execute('ALTER TABLE bookmarks DROP COLUMN '+field)
  self.assertNotIn('provider',bookmarks.export(self.home,key)['bookmark'])
  data=self.payload();data['label']='new bookmark'
  new=bookmarks.change(self.home,data)['id']
  self.assertIsNone(bookmarks.export(self.home,key)['bookmark']['provider'])
  self.assertEqual(bookmarks.export(self.home,new)['bookmark']['native_id'],'test')
