"""Restore isolated state copies and roll back source. Never restore an input home."""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import signal
import socket
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent_workflow.core import Store
from agent_workflow.connections import connect, ledger_id


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def logical(path):
    connection = sqlite3.connect(Path(path).as_uri() + '?mode=ro', uri=True)
    try:
        assert connection.execute('PRAGMA quick_check').fetchone()[0] == 'ok'
        value = hashlib.sha256()
        for line in connection.iterdump():
            value.update(line.encode())
        return value.hexdigest()
    finally:
        connection.close()


def backup(source, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    reader = sqlite3.connect(source.as_uri() + '?mode=ro', uri=True)
    writer = sqlite3.connect(target)
    deadline = time.monotonic() + 30
    def progress(_status, _remaining, _total):
        if time.monotonic() > deadline:
            raise RuntimeError('Online backup exceeded its deadline. The input was not changed.')
    try:
        reader.backup(writer, pages=256, progress=progress, sleep=.05)
    finally:
        writer.close()
        reader.close()
    return logical(target)


def operating_snapshot(homes):
    result = {}
    for label, home in homes.items():
        connection = sqlite3.connect((home / 'state.sqlite3').as_uri() + '?mode=ro', uri=True)
        try:
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            result[label] = {}
            for table in ('jobs', 'batches'):
                rows = connection.execute('SELECT * FROM ' + table).fetchall() if table in tables else []
                result[label][table] = {'count': len(rows), 'sha256': hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()}
        finally:
            connection.close()
    return result


def main(args):
    os.umask(0o077)
    source = Path(args.source).resolve()
    root = Path(args.output).resolve()
    homes = {'base': Path(args.base_home).resolve(), 'H': Path(args.h_home).resolve(),
             'native': Path(args.native_home).resolve()}
    if root.exists() or not root.is_relative_to(Path(tempfile.gettempdir()).resolve()):
        raise ValueError('Use a new directory under the system temporary directory.')
    if any(root == p or root.is_relative_to(p) or p.is_relative_to(root) for p in [source, *homes.values()]):
        raise ValueError('The rehearsal directory must be disjoint from all inputs.')
    if not re.fullmatch(r'[a-fA-F0-9]{7,40}', args.rollback_ref):
        raise ValueError('Use an explicit rollback commit SHA.')
    rollback_sha = subprocess.check_output(['git', '-C', str(source), 'rev-parse', '--verify', args.rollback_ref + '^{commit}'], text=True).strip()
    root.mkdir(mode=0o700)
    report = {'complete': False, 'output': str(root), 'rollback_sha': rollback_sha,
              'operating_before': operating_snapshot({k: homes[k] for k in ('base', 'H')}),
              'input_homes_are_read_only': True, 'operating_restart': False,
              'rehearsal_worker_started': False, 'events': []}
    def event(name, **values):
        report['events'].append({'at': time.time(), 'event': name, **values})
        (root / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
        print(json.dumps({'event': name, **values}), flush=True)
    for label, home in homes.items():
        destination = root / 'backups' / label
        destination.mkdir(parents=True)
        for path in home.glob('*.sqlite3'):
            backup(path, destination / path.name)
        for name in ('connections.json', 'native-sources.json'):
            if (home / name).is_file():
                shutil.copyfile(home / name, destination / name)
    clones = {}
    for label in ('base', 'H', 'native'):
        clones[label] = root / 'homes' / label
        shutil.copytree(root / 'backups' / label, clones[label])
    observer = clones['native']
    # A private empty controller ledger permits strict read-only serve; no source DB is merged.
    Store(observer).close()
    connect(observer, Path(args.project).resolve(), ledgers=[clones['base'], clones['H']],
            registration_home=clones['base'], include_processes=False, include_temporary=False,
            include_default=False, include_home=False)
    candidate = root / 'candidate-code'
    candidate.mkdir()
    shutil.copytree(source / 'agent_workflow', candidate / 'agent_workflow',
                    ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    manifest = {str(p.relative_to(candidate)): digest(p) for p in candidate.rglob('*') if p.is_file()}
    (root / 'candidate-source.json').write_text(json.dumps(manifest, indent=2))
    state_before = {str(p.relative_to(root)): logical(p) for p in (root / 'homes').rglob('*.sqlite3')}
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    url = f'http://127.0.0.1:{port}'
    selected = json.loads(Path(args.native_proof).read_text())['providers'][0]
    task = args.task
    task_url = '/mission?ledger=' + ledger_id(clones['H']) + '&task=' + task
    browser_manifest = {'url': url, 'output': str(root), 'session': selected['observed_id'],
                        'nativeId': selected['native_id'], 'taskId': task, 'taskUrl': task_url}
    (root / 'browser-manifest.json').write_text(json.dumps(browser_manifest, indent=2))
    process = None
    handles = []
    def stop():
        nonlocal process
        if process is not None and process.poll() is None:
            process.send_signal(signal.SIGTERM)
            process.wait(timeout=15)
        process = None
    def start(code, label):
        nonlocal process
        env = {**os.environ, 'PYTHONPATH': str(code), 'AGENT_WORKFLOW_HOME': str(observer),
               'PYTHONDONTWRITEBYTECODE': '1'}
        log = (root / (label + '.log')).open('w')
        handles.append(log)
        process = subprocess.Popen([sys.executable, '-m', 'agent_workflow', '--home', str(observer),
                                    'serve', '--read-only', '--port', str(port)],
                                   cwd=code, env=env, stdout=log, stderr=log, start_new_session=True)
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError('The staging server exited. Inspect its log.')
            try:
                with urlopen(url + '/sessions', timeout=1) as response:
                    if response.status == 200:
                        metadata = json.loads((observer / 'server.json').read_text())
                        assert metadata['pid'] == process.pid and metadata['read_only']
                        assert not (observer / 'daemon.lock').exists()
                        event('server_started', label=label, pid=process.pid, url=url, read_only=True)
                        return
            except (OSError, ValueError):
                time.sleep(.1)
        raise RuntimeError('Staging server startup timed out; inspect its existing process.')
    def get(path):
        with urlopen(url + path, timeout=10) as response:
            return response.read()
    def healthy_native():
        try:
            data = json.loads(get('/api/native/sessions?session=' + selected['observed_id']))
            return any(row['native_id'] == selected['native_id'] for row in data['sessions'])
        except (HTTPError, ValueError, KeyError):
            return False
    def browser(phase):
        result = subprocess.run([args.node, str(source / 'tests/recovery_browser.mjs'),
                                 str(root / 'browser-manifest.json'), phase],
                                cwd=source, capture_output=True, text=True, timeout=90)
        (root / (phase + '-browser.log')).write_text(result.stdout + result.stderr)
        assert result.returncode == 0, 'Browser check failed: ' + phase
    try:
        start(candidate, 'candidate')
        assert healthy_native()
        ledgers = json.loads(get('/api/connections'))
        (root / 'staging-ledgers.json').write_text(json.dumps(ledgers))
        assert all(Path(row['home']).resolve().is_relative_to(root) for row in ledgers['ledgers'] if row.get('home'))
        browser('candidate-healthy')
        stop()
        broken = observer / 'native-observation.sqlite3'
        assert broken.is_relative_to(root)
        broken.write_bytes(b'ISOLATED RECOVERY REHEARSAL: intentionally invalid SQLite header')
        failed_at = time.monotonic()
        start(candidate, 'corrupt-database')
        assert not healthy_native(), 'The invalid state copy must fail health.'
        event('database_fault_detected')
        stop()
        for suffix in ('-wal', '-shm'):
            Path(str(broken) + suffix).unlink(missing_ok=True)
        restored = broken.with_suffix('.restore')
        backup(root / 'backups/native/native-observation.sqlite3', restored)
        os.replace(restored, broken)
        assert logical(broken) == state_before[str(broken.relative_to(root))]
        start(candidate, 'restored-database')
        assert healthy_native()
        browser('database-restored')
        event('database_restored', recovery_seconds=time.monotonic() - failed_at)
        # Inject only an owned copied asset; the development source remains unchanged.
        asset = candidate / 'agent_workflow/static/mission.js'
        asset.write_bytes(asset.read_bytes() + b'\n} INVALID_REHEARSAL_ASSET\n')
        assert hashlib.sha256(get('/static/mission.js')).hexdigest() != manifest['agent_workflow/static/mission.js']
        browser('asset-failure')
        event('asset_fault_detected')
        failed_at = time.monotonic()
        stop()
        rollback = root / 'rollback-code'
        archive = subprocess.check_output(['git', '-C', str(source), 'archive', rollback_sha, '--', 'agent_workflow'])
        with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
            tar.extractall(rollback, filter='data')
        start(rollback, 'rolled-back-source')
        assert healthy_native()
        assert hashlib.sha256(get('/static/mission.js')).hexdigest() == digest(rollback / 'agent_workflow/static/mission.js')
        browser('source-rolled-back')
        for path, value in state_before.items():
            assert logical(root / path) == value, 'State changed across recovery: ' + path
        event('source_rolled_back', recovery_seconds=time.monotonic() - failed_at, sha=rollback_sha)
        try:
            urlopen(Request(url + '/api/actions', data=b'{}', headers={'Content-Type': 'application/json'}), timeout=5)
            raise AssertionError('Read-only mutation unexpectedly succeeded.')
        except HTTPError as exc:
            assert exc.code == 403
        report['operating_after'] = operating_snapshot({k: homes[k] for k in ('base', 'H')})
        assert report['operating_after'] == report['operating_before']
        report['complete'] = True
        report['state_logical_digests'] = state_before
        report['limitations'] = ['All faults and restores were isolated copies.',
                                 'No operating service or worker was restarted.',
                                 'External HTTP source reconnect and active-writer production restore were not tested.']
        event('rehearsal_complete', operating_jobs_unchanged=True, operating_batches_unchanged=True)
    finally:
        stop()
        for handle in handles:
            handle.close()
        (root / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for flag in ('source', 'output', 'base-home', 'h-home', 'native-home', 'native-proof', 'project', 'rollback-ref', 'task'):
        parser.add_argument('--' + flag, required=True)
    parser.add_argument('--node', default='node')
    main(parser.parse_args())
