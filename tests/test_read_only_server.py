import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from agent_workflow.core import Store, WorkflowError


class ReadOnlyServerTests(unittest.TestCase):
    def test_missing_database_is_not_created(self):
        with tempfile.TemporaryDirectory() as root:
            home = Path(root) / 'missing'
            with self.assertRaises(WorkflowError):
                Store(home, read_only=True)
            self.assertFalse(home.exists())
            result = subprocess.run(
                [sys.executable, '-m', 'agent_workflow', '--home', str(home),
                 'serve', '--read-only', '--port', '0'], capture_output=True, timeout=10)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(home.exists())

    def test_live_reads_preserve_queue_batch_schema_and_events(self):
        with tempfile.TemporaryDirectory() as root:
            home = Path(root)
            writer = Store(home)
            task = {'id': 'READ-1', 'status': 'completed', 'goal': 'recent H task'}
            with writer.db:
                writer.db.execute('INSERT INTO tasks VALUES (?,?,?)', ('READ-1', json.dumps(task), 1))
                writer.db.execute('CREATE TABLE jobs(id TEXT, task_id TEXT, status TEXT)')
                writer.db.execute("INSERT INTO jobs VALUES ('queued-1','READ-1','queued')")
                writer.db.execute("INSERT INTO jobs VALUES ('running-1','READ-1','running')")
                writer.db.execute('CREATE TABLE batches(name TEXT, data TEXT)')
                writer.db.execute('INSERT INTO batches VALUES (?,?)', ('active', '{"status":"running"}'))
            before_schema = writer.db.execute('SELECT * FROM sqlite_master ORDER BY name').fetchall()
            before_jobs = writer.db.execute('SELECT * FROM jobs').fetchall()
            before_batches = writer.db.execute('SELECT * FROM batches').fetchall()
            process = subprocess.Popen(
                [sys.executable, '-m', 'agent_workflow', '--home', str(home),
                 'serve', '--read-only', '--port', '0'],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            try:
                deadline = time.monotonic() + 10
                meta = home / 'server.json'
                while not meta.exists():
                    if process.poll() is not None or time.monotonic() > deadline:
                        self.fail('read-only server failed to start: ' + process.communicate(timeout=2)[1])
                    time.sleep(.05)
                info = json.loads(meta.read_text())
                self.assertTrue(info['read_only'])
                url = info['url']

                def get(path):
                    with urlopen(url + path, timeout=5) as response:
                        return json.load(response)

                state = get('/api/state')
                self.assertTrue(state['read_only'])
                self.assertEqual(state['tasks'][0]['id'], 'READ-1')
                self.assertEqual(len(state['jobs']), 2)
                self.assertEqual(state['profiles'], [])
                self.assertEqual(state['notifications'], [])
                self.assertTrue(get('/api/observability?task_id=READ-1')['legacy'])
                with writer.db:
                    task['goal'] = 'live update from existing writer'
                    writer.db.execute('UPDATE tasks SET data=?', (json.dumps(task),))
                self.assertEqual(get('/api/state')['tasks'][0]['goal'], task['goal'])
                for path in ['/api/queue', '/api/stop', '/api/hold', '/api/tasks', '/api/resume']:
                    request = Request(url + path, data=b'{}', headers={'X-Workflow-Token': state['csrf_token']})
                    with self.assertRaises(HTTPError) as error:
                        urlopen(request, timeout=5)
                    self.assertEqual(error.exception.code, 403)
                self.assertFalse((home / 'daemon.lock').exists())
                self.assertEqual(writer.db.execute('SELECT * FROM jobs').fetchall(), before_jobs)
                self.assertEqual(writer.db.execute('SELECT * FROM batches').fetchall(), before_batches)
                self.assertEqual(writer.db.execute('SELECT * FROM sqlite_master ORDER BY name').fetchall(), before_schema)
                self.assertEqual(writer.db.execute('SELECT count(*) FROM events').fetchone()[0], 0)
                reader = Store(home, read_only=True)
                try:
                    with self.assertRaises(sqlite3.OperationalError):
                        reader.db.execute('DELETE FROM tasks')
                finally:
                    reader.close()
            finally:
                process.terminate()
                process.communicate(timeout=10)
                writer.close()
            self.assertEqual(process.returncode, 0)
            self.assertFalse(meta.exists())


if __name__ == '__main__':
    unittest.main()
