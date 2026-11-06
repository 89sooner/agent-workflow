"""Audit technical evidence. Human pilot completion requires separate evidence."""
import argparse
import importlib.util
import io
import tarfile
import json
from pathlib import Path
import sqlite3
import subprocess
import sys


def module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def audit(args):
    scripts = Path(__file__).resolve().parent
    native = module(scripts / 'verify-native-parallel.py', 'native_audit').verify(args.native_run)
    assert native['passed']
    performance = json.loads(Path(args.performance).read_text())['sample']
    assert performance['inputWorkers'] == performance['displayedWorkers'] == 24
    assert performance['durationMs'] >= 60000 and performance['p95Ms'] <= 33.3 + 1e-6
    assert performance['renderedFrames'] >= performance['sampleCount'] * .8
    assert performance['canvasWidth'] == 1440 and performance['canvasHeight'] == 500
    recovery = Path(args.recovery).resolve()
    report = json.loads((recovery / 'report.json').read_text())
    assert report['complete'] and not report['operating_restart'] and not report['rehearsal_worker_started']
    helper = module(scripts / 'rehearse-recovery.py', 'recovery_audit')
    for name, expected in report['state_logical_digests'].items():
        path = (recovery / name).resolve()
        assert path.is_relative_to(recovery)
        assert helper.logical(path) == expected
    for phase in ('candidate-healthy', 'database-restored', 'source-rolled-back'):
        browser = json.loads((recovery / phase / 'browser.json').read_text())
        assert browser['healthy'] and not browser['errors'] and not browser['writes']
    failed = json.loads((recovery / 'asset-failure/browser.json').read_text())
    assert not failed['healthy'] and failed['detected'] and failed['errors']
    # Git archive applies export filters, including this repository's CRLF policy.
    archive = subprocess.check_output(['git', '-C', str(scripts.parent), 'archive',
                                       report['rollback_sha'], '--', 'agent_workflow'])
    with tarfile.open(fileobj=io.BytesIO(archive)) as reference:
        for entry in reference:
            if entry.isdir():
                continue
            assert entry.isfile()
            assert reference.extractfile(entry).read() == (recovery / 'rollback-code' / entry.name).read_bytes()
    packed_root = Path(args.native_run)
    packed = json.loads((packed_root / 'opencode/packed-first-receipt-proof.json').read_text())
    assert packed['received'] and not packed['model_called']
    connection = sqlite3.connect((packed_root / 'observer/native-observation.sqlite3').as_uri() + '?mode=ro', uri=True)
    try:
        assert connection.execute('SELECT 1 FROM sessions WHERE native_id=?', (packed['session'],)).fetchone()
    finally:
        connection.close()
    moved = json.loads((packed_root / 'opencode/packed-move-proof.json').read_text())
    assert moved['passed'] and moved['first_receipt'] and moved['moved']
    assert moved['old_owner_state'] == 'unknown' and moved['server_stopped']
    assert not moved['model_called']
    return {'technical_checks_passed': True, 'overall_goal_complete': False,
            'human_pilot': 'Not assessed by this technical audit. Actual participant evidence is required.',
            'native_command_overlap_lower_bound_seconds': native['native_command_overlap_seconds_lower_bound'],
            'actual_3d_workers': 24, 'frame_p95_ms': performance['p95Ms'],
            'recovery_clone_state_verified': True, 'rollback_sha': report['rollback_sha'],
            'packed_plugin_first_receipt_verified': True,
            'packed_session_move_verified': True}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('native-run', 'performance', 'recovery', 'output'):
        parser.add_argument('--' + name, required=True)
    args = parser.parse_args()
    result = audit(args)
    Path(args.output).write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(result))
