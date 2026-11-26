"""Browser acceptance: native empty sessions plus explicitly synthetic execution fixtures."""
import json
import os
from pathlib import Path
import subprocess
import sys
import threading

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from test_opencode import OpenCodeTests
from agent_workflow.opencode import connect
from agent_workflow.server import handler


def main():
    fixture = OpenCodeTests()
    fixture.setUp()
    stop = threading.Event()
    root = Path(fixture.temp.name)
    control = root / 'control.json'
    source = fixture.connect(name='OpenCode 합성 실행 검증')
    native = None
    if os.environ.get('OPENCODE_TEST_URL'):
        native = connect(fixture.home, os.environ['OPENCODE_TEST_URL'], os.environ['OPENCODE_TEST_PROJECT'],
                         name='OpenCode 실제 서버 검증')['source']
    server = fixture.server(handler(fixture.home, 'fixture', read_only=True, observer=True))

    applied = []

    def controls():
        while not stop.wait(.05):
            if not control.exists():
                continue
            command = control.read_text()
            if command == 'fail':
                fixture.fail = True
            elif command == 'recover':
                fixture.fail = False
            elif command == 'history-limit':
                fixture.messages = [{'info': {'role': 'user'}, 'parts': []} for _ in range(99)] + fixture.messages[-1:]
            else:
                raise ValueError(f'Unexpected OpenCode fixture command: {command!r}')
            applied.append(command)
            control.unlink()

    thread = threading.Thread(target=controls, daemon=True)
    thread.start()
    manifest = root / 'browser.json'
    manifest.write_text(json.dumps({'url': f'http://127.0.0.1:{server.server_port}', 'source': source,
                                    'native': native, 'control': str(control)}))
    try:
        result = subprocess.run(['node', 'tests/opencode_scenarios.mjs', str(manifest)], timeout=180)
        if result.returncode == 0:
            assert applied == ['history-limit', 'fail', 'recover'], applied
        assert all(c[0] == 'GET' for c in fixture.calls)
        assert not (fixture.home / 'state.sqlite3').exists()
        return result.returncode
    finally:
        stop.set()
        thread.join()
        fixture.doCleanups()


if __name__ == '__main__':
    raise SystemExit(main())
