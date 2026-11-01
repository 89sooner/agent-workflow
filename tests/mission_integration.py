"""Actual SQLite/HTTP/Chromium acceptance; synthetic subprocess agents, isolated state only."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
from http.server import ThreadingHTTPServer

SOURCE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE))
from agent_workflow.core import Store, create_task
from agent_workflow.connections import connect, ledger_id
from agent_workflow.observability import run_context, view
from agent_workflow.server import handler
from agent_workflow.daemon import init_queue
from agent_workflow.usage import managed_process
from saas_integration import prepare_saas


def main():
    with tempfile.TemporaryDirectory(prefix='workflow-react-mission-') as directory:
        root = Path(directory)
        fixture = prepare_saas(root)
        store = Store(fixture['home'])
        try:
            init_queue(store)
            task = create_task(store, 'MISSION-LONG', fixture['project'], 'SYNTHETIC: many real check stages', ['observed'], [], [], 'claude', 'none', 'fixture only')
            with store.lock(task['project']) as fd, run_context(store, task, 'check'):
                for i in range(10):
                    managed_process(store, task, [sys.executable, '-c', 'print("stage evidence")'], task['project'], store.directory(task['id']) / 'checks' / str(i), 5, fd, role='check')
                    store.save(task, 'checks_finished', {'passed':True, 'summary':'stage evidence'})
                for i in range(520):
                    store.save(task, 'checkpoint', {'summary':f'MANY-CHECKPOINT-{i}'})
            long_trace = view(store, task['id'])
            fixture['old_span'] = long_trace['spans'][0]['id']
            fixture['long_run'] = long_trace['selected_run_id']
            fixture['ledger'] = ledger_id(store.home)
            before_jobs = list(store.db.execute('SELECT * FROM jobs'))
        finally:
            store.close()
        second = Store(root / 'second')
        try:
            create_task(second, 'SECOND-ONLY', fixture['other_project'], 'Second independent ledger', ['observed'], [], [], 'claude', 'none', 'fixture only')
        finally:
            second.close()
        connect(Path(fixture['home']), Path(fixture['other_project']), ledgers=[root / 'second'], include_processes=False, include_temporary=False, include_default=False)
        fixture.update(second_ledger=ledger_id(root / 'second'), python=sys.executable, source=str(SOURCE))
        server = ThreadingHTTPServer(('127.0.0.1', 0), handler(Path(fixture['home']), 'fixture', read_only=True))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        fixture['url'] = f'http://127.0.0.1:{server.server_port}'
        manifest = root / 'fixture.json'
        manifest.write_text(json.dumps(fixture))
        try:
            result = subprocess.run(['node', 'tests/mission_scenarios.mjs', str(manifest)], cwd=SOURCE, timeout=240)
            store = Store(fixture['home'], read_only=True)
            try:
                assert list(store.db.execute('SELECT * FROM jobs')) == before_jobs
                assert not (Path(fixture['home']) / 'daemon.lock').exists()
            finally:
                store.close()
            return result.returncode
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == '__main__':
    raise SystemExit(main())
