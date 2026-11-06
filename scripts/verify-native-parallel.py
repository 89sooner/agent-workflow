"""Audit an opt-in native host test from its real logs, outputs, and observer DB."""
import argparse
import hashlib
import re
import json
from pathlib import Path
import sqlite3
import subprocess
import sys


def verify(root):
    root = Path(root).resolve()
    records = json.loads((root / 'parallel-processes.json').read_text())
    projects = json.loads((root / 'projects.json').read_text())
    barriers = {name: json.loads((root / 'gate' / (name + '.json')).read_text())['started']
                for name in ('codex', 'claude', 'opencode')}
    observed_overlap = min(row['ended_at'] for row in records.values()) - max(barriers.values())
    holds = []
    gate_digests = {}
    for name in ('codex', 'claude', 'opencode'):
        gate = Path(projects[name]) / 'readiness-gate.py'
        waits = re.findall(r'time\.sleep\((\d+)\)', gate.read_text())
        assert len(waits) == 1 and gate.stat().st_mtime <= records[name]['started_at']
        holds.append(int(waits[0]))
        gate_digests[name] = hashlib.sha256(gate.read_bytes()).hexdigest()
        assert 'All three native tools reached the barrier.' in (root / (name + '-run.jsonl')).read_text()
    overlap = min(holds)
    assert overlap >= 8 and observed_overlap >= overlap, 'The real command barrier did not complete.'
    parents = {}
    for name in ('codex', 'claude'):
        for line in (root / (name + '-run.jsonl')).read_text().splitlines():
            event = json.loads(line)
            identifier = event.get('thread_id') if name == 'codex' else event.get('session_id')
            if identifier:
                parents[name] = identifier
                break
    parents['opencode'] = json.loads((root / 'opencode/attempt2-session.json').read_text())['id']
    db = sqlite3.connect((root / 'observer/native-observation.sqlite3').as_uri() + '?mode=ro', uri=True)
    db.row_factory = sqlite3.Row
    verified = []
    try:
        for name in ('codex', 'claude', 'opencode'):
            run = records[name]
            assert run['status'] == 'exited' and run['exit_code'] == 0
            assert run['started_at'] <= barriers[name] < run['ended_at']
            project = Path(projects[name])
            data = json.loads((project / 'readiness-input.json').read_text())
            output = json.loads((project / 'readiness-result.json').read_text())
            assert output['count'] == len(data['numbers'])
            assert output['total'] == sum(data['numbers'])
            check = subprocess.run([sys.executable, 'readiness-verify.py'], cwd=project,
                                   capture_output=True, text=True)
            assert check.returncode == 0, check.stderr
            parent = db.execute('SELECT * FROM sessions WHERE provider=? AND native_id=? AND project=?',
                                (name, parents[name], str(project))).fetchone()
            assert parent is not None
            expected = 'idle' if name == 'opencode' else 'ended'
            assert parent['state'] == expected, (name, parent['state'])
            children = db.execute('SELECT * FROM sessions WHERE provider=? AND parent=? AND project=? '
                                  'AND last_seen>=? ORDER BY last_seen DESC',
                                  (name, parents[name], str(project), run['started_at'])).fetchall()
            children = [c for c in children if c['state'] == expected]
            assert children, 'No actual completed child is linked to ' + name
            child = children[0]
            parent_events = [dict(x) for x in db.execute(
                'SELECT event,state,tool FROM history WHERE session_id=? ORDER BY seq', (parent['id'],))]
            child_events = [dict(x) for x in db.execute(
                'SELECT event,state,tool FROM history WHERE session_id=? ORDER BY seq', (child['id'],))]
            assert any(x['tool'] for x in parent_events), 'No observed parent tool: ' + name
            assert any(x['tool'] for x in child_events), 'No observed child tool: ' + name
            assert any(x['state'] == 'running' for x in parent_events)
            assert any(x['state'] == expected for x in parent_events)
            assert any(x['state'] == expected for x in child_events)
            verified.append({'provider': name, 'native_id': parents[name], 'observed_id': parent['id'],
                             'child_id': child['native_id'], 'child_observed_id': child['id'],
                             'project': str(project), 'final_observed_state': expected,
                             'parent_events': parent_events, 'child_events': child_events,
                             'output': output, 'independent_check': check.stdout.strip()})
    finally:
        db.close()
    return {'passed': True, 'native_command_overlap_seconds_lower_bound': overlap,
            'observed_parent_process_overlap_seconds': min(row['ended_at'] for row in records.values()) - max(row['started_at'] for row in records.values()),
            'gate_script_sha256': gate_digests, 'providers': verified,
            'limitations': ['The first attempt used an incorrect OpenCode location.',
                            'Earlier failed attempts are preserved; this audit uses the latest complete parallel run.',
                            'This is developer host validation, not a new-user pilot.']}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-dir', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    result = verify(args.run_dir)
    Path(args.output).write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'passed': result['passed'], 'overlap_seconds': result['native_command_overlap_seconds_lower_bound'],
                      'providers': [x['provider'] for x in result['providers']]}))
