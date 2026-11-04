"""UI onboarding against isolated HTTP sources; never starts a queue worker."""
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_opencode import OpenCodeTests
from agent_workflow import connections
from agent_workflow.core import Store, create_task, git
from agent_workflow.server import handler


def main():
    fixture = OpenCodeTests()
    fixture.setUp()
    root = Path(fixture.temp.name)
    project = Path(fixture.project)
    project.mkdir()
    git(project, 'init', '-q')
    git(project, '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '--allow-empty', '-qm', 'fixture')
    workflow_home = root / 'workflow-source'
    store = Store(workflow_home)
    create_task(store, 'UI-WORKFLOW', project, '화면에서 연결한 workflow 작업', ['observed'], [], [], 'codex', 'none', 'fixture')
    before = list(store.db.iterdump())
    store.close()
    control = root / 'control'
    stop = threading.Event()

    def controls():
        while not stop.wait(.05):
            if not control.exists():
                continue
            command = control.read_text()
            if command == 'auth':
                fixture.require_auth = True
            elif command == 'fail':
                fixture.fail = True
            elif command == 'recover':
                fixture.require_auth = fixture.fail = False
            elif command == 'empty':
                fixture.rows = []
            elif command == 'new':
                fixture.rows = [{'id': 'ses_new', 'directory': fixture.project, 'title': '자동 발견된 새 세션',
                                 'time': {'created': 1700000000000, 'updated': 1700000002000}}]
                fixture.statuses = {}
            control.unlink()

    real = connections.discover
    def isolated(project=None, homes=(), **_):
        return real(project, [*homes, *([workflow_home] if project else [])],
                    include_default=False, include_processes=False, include_temporary=False)

    thread = threading.Thread(target=controls, daemon=True)
    thread.start()
    try:
        with patch.object(connections, 'discover', side_effect=isolated):
            observer = fixture.server(handler(fixture.home, 'fixture', read_only=True, observer=True))
            readonly = fixture.server(handler(workflow_home, 'fixture', read_only=True))
            manager = fixture.server(handler(workflow_home, 'fixture'))
            manifest = root / 'onboarding.json'
            manifest.write_text(json.dumps({'url': f'http://127.0.0.1:{observer.server_port}',
                'readonly': f'http://127.0.0.1:{readonly.server_port}',
                'manager': f'http://127.0.0.1:{manager.server_port}',
                'project': fixture.project, 'opencode': fixture.url, 'control': str(control)}))
            if os.environ.get('ONBOARDING_PREVIEW_MANIFEST'):
                Path(os.environ['ONBOARDING_PREVIEW_MANIFEST']).write_text(manifest.read_text())
                print('Isolated onboarding preview ready: ' + str(manifest), flush=True)
                stop.wait(1800)
                result = 0
            else:
                result = subprocess.run(['node', 'tests/onboarding_scenarios.mjs', str(manifest)], timeout=180).returncode
            assert all(call[0] == 'GET' for call in fixture.calls), fixture.calls
            assert not (fixture.home / 'state.sqlite3').exists()
            check = Store(workflow_home, read_only=True)
            try:
                assert list(check.db.iterdump()) == before
            finally:
                check.close()
            assert not (workflow_home / 'daemon.lock').exists()
            return result
    finally:
        stop.set()
        thread.join()
        fixture.doCleanups()


if __name__ == '__main__':
    raise SystemExit(main())
