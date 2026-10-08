#!/usr/bin/env python3
"""Bounded pilot preparation and checks. Does not implement or queue the WP."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import shutil
import uuid
import sys
import time

ROOT = Path(__file__).resolve().parent
REPO = Path('/home/roqkf/pr-search-wt/agent-workflow-wp061')
BASE = '2748b9c3831cc7701596bbcdac0ffb9f5915ab6a'
PILOT_ID = 'PRS-WP061'
BRANCH = 'pilot/agent-workflow-wp061'
EVIDENCE = ROOT / 'evidence'
sys.path.insert(0, str(ROOT.parents[1]))
from agent_workflow.core import snapshot
KINDS = ['requires', 'conflicts', 'oneOf', 'exactlyOne', 'atLeastOne', 'implies', 'repeatable', 'minItems', 'maxItems', 'enum', 'conditional_required', 'input_source', 'context']
SERVICES = {'pg': ('postgres:16-alpine', 5432), 'redis': ('redis:7-alpine', 6379)}
GATES = {
    'typecheck': ['pnpm', 'typecheck'],
    'lint': ['pnpm', 'lint'],
    'dependencies': ['pnpm', 'run', 'lint:deps'],
    'unit': ['pnpm', 'exec', 'vitest', 'run', 'packages/gh-cli/src', 'apps/web/lib/gh.test.ts', 'apps/search-api/src/gh', 'apps/gh-executor/src', '--maxWorkers=2'],
    'integration': ['pnpm', 'exec', 'vitest', 'run', '--config', 'vitest.integration.config.ts', 'apps/search-api/integration/gh/routes.test.ts', 'apps/gh-executor/integration/executor.test.ts', '--no-file-parallelism'],
    'a11y': ['pnpm', '--filter', '@prs/web', 'exec', 'vitest', 'run', '--config', 'vitest.a11y.config.ts', 'a11y/gh.test.tsx', '--maxWorkers=2'],
    'regression': ['pnpm', 'run', 'test:regression'],
}

def run(argv, cwd=REPO, **kwargs):
    return subprocess.run(argv, cwd=cwd, check=True, text=True, **kwargs)

def output(argv):
    return run(argv, capture_output=True).stdout.strip()

def guard():
    if output(['git', 'rev-parse', '--show-toplevel']) != str(REPO):
        raise RuntimeError('Wrong worktree')
    if output(['git', 'branch', '--show-current']) != BRANCH:
        raise RuntimeError('Unexpected branch; re-baseline explicitly before running')
    run(['git', 'merge-base', '--is-ancestor', BASE, 'HEAD'], capture_output=True)

def container(part):
    name = 'awp-prs-wp061-' + part
    p = subprocess.run(['docker', 'inspect', name], capture_output=True, text=True)
    if p.returncode:
        return name, None
    obj = json.loads(p.stdout)[0]
    if obj['Config'].get('Labels', {}).get('agent-workflow.pilot') != PILOT_ID:
        raise RuntimeError('Container name belongs to another workload: ' + name)
    return name, obj

def environment_up():
    guard()
    ports = {}
    for part, (image, port) in SERVICES.items():
        name, info = container(part)
        if info is None:
            cmd = ['docker', 'run', '-d', '--name', name, '--label', 'agent-workflow.pilot=' + PILOT_ID,
                   '--memory', '384m' if part == 'pg' else '128m', '--cpus', '1', '-p', f'127.0.0.1::{port}']
            if part == 'pg':
                cmd += ['-e', 'POSTGRES_USER=prs', '-e', 'POSTGRES_PASSWORD=prs', '-e', 'POSTGRES_DB=prs_test', '--tmpfs', '/var/lib/postgresql/data:rw']
            cmd += [image]
            run(cmd, capture_output=True)
        elif not info['State']['Running']:
            run(['docker', 'start', name], capture_output=True)
        _, info = container(part)
        bindings = info['NetworkSettings']['Ports'][str(port) + '/tcp']
        if len(bindings) != 1 or bindings[0]['HostIp'] != '127.0.0.1':
            raise RuntimeError('Unexpected test service binding')
        ports[part] = int(bindings[0]['HostPort'])
        healthy = ['docker', 'exec', name] + (['pg_isready', '-U', 'prs', '-d', 'prs_test'] if part == 'pg' else ['redis-cli', 'ping'])
        for attempt in range(30):
            result = subprocess.run(healthy, capture_output=True)
            if result.returncode == 0:
                break
            time.sleep(1)
        else:
            raise RuntimeError(name + ' did not become ready')
    (ROOT / 'runtime.json').write_text(json.dumps({'ports': ports, 'containers': ['awp-prs-wp061-' + p for p in SERVICES]}, indent=2))
    return ports

def environment_down():
    for part in SERVICES:
        name, info = container(part)
        if info and info['State']['Running']:
            run(['docker', 'stop', name], capture_output=True)

def gate(name, baseline=False):
    guard()
    env = dict(os.environ)
    if name in ('integration', 'regression'):
        ports = environment_up()
        env.update(DATABASE_URL=f"postgresql://prs:prs@127.0.0.1:{ports['pg']}/prs_test", POSTGRES_HOST='127.0.0.1',
                   POSTGRES_PORT=str(ports['pg']), POSTGRES_DB='prs_test', POSTGRES_TEST_DB='prs_test',
                   POSTGRES_USER='prs', POSTGRES_PASSWORD='prs', REDIS_URL=f"redis://127.0.0.1:{ports['redis']}")
    prefix = 'baseline-' if baseline else 'final-'
    path = EVIDENCE / (prefix + name)
    previous = EVIDENCE / 'history' / (prefix + name + '-' + uuid.uuid4().hex[:8])
    for suffix in ('.log', '.json', '.receipt.json'):
        old = path.with_suffix(suffix)
        if old.exists():
            previous.mkdir(parents=True, exist_ok=True)
            shutil.copy2(old, previous / old.name)
    command = list(GATES[name])
    if name in ('unit', 'integration', 'a11y'):
        command += ['--reporter=default', '--reporter=json', '--outputFile.json=' + str(path.with_suffix('.json'))]
    before = snapshot(REPO)['fingerprint']
    start = time.monotonic()
    with path.with_suffix('.log').open('w') as log:
        p = subprocess.run(command, cwd=REPO, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=900)
    after = snapshot(REPO)['fingerprint']
    exit_code = p.returncode if before == after else 2
    receipt = {'source_fingerprint': after, 'source_changed_during_check': before != after, 'gate': name, 'baseline': baseline, 'argv': command, 'exit_code': exit_code, 'elapsed_seconds': round(time.monotonic()-start,2),
               'head': output(['git','rev-parse','HEAD']), 'log': str(path.with_suffix('.log'))}
    path.with_suffix('.receipt.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2))
    print(json.dumps(receipt, ensure_ascii=False))
    return exit_code

def closure():
    guard()
    path = EVIDENCE / 'acceptance.json'
    if not path.exists():
        raise RuntimeError('WP acceptance evidence missing; baseline green is not WP completion')
    data = json.loads(path.read_text())
    sys.path.insert(0, str(ROOT.parents[1]))
    from agent_workflow.core import snapshot
    current_fingerprint = snapshot(REPO)['fingerprint']
    if data.get('source_fingerprint', current_fingerprint) != current_fingerprint:
        raise RuntimeError('Acceptance evidence does not match current source')
    passed = set()
    for name in ('unit', 'integration', 'a11y'):
        report = json.loads((EVIDENCE / f'final-{name}.json').read_text())
        if report.get('numFailedTests', 0) or not report.get('numPassedTests'):
            raise RuntimeError('Required suite not passed: ' + name)
        for suite in report.get('testResults', []):
            for case in suite.get('assertionResults', []):
                if case.get('status') == 'passed':
                    passed.add(case.get('fullName'))
    constraints = data.get('constraints', {})
    if set(constraints) != set(KINDS):
        raise RuntimeError('All 13 constraint families must be mapped')
    for kind, evidence in constraints.items():
        if not isinstance(evidence, dict) or set(evidence) != {'valid', 'invalid'}:
            raise RuntimeError('Both valid and invalid case names are required: ' + kind)
        if evidence['valid'] == evidence['invalid'] or not all(v in passed for v in evidence.values()):
            raise RuntimeError('Constraint evidence is not a passing test pair: ' + kind)
    obligations = data.get('wp_dod', {})
    if set(obligations) != {'D1','D2','D3','D4','D5'} or any(not names or not all(n in passed for n in names) for names in obligations.values()):
        raise RuntimeError('Each WP DoD needs passing test evidence')
    for name in GATES:
        r = json.loads((EVIDENCE / f'final-{name}.receipt.json').read_text())
        if r['exit_code'] != 0 or r.get('source_fingerprint') != current_fingerprint:
            raise RuntimeError('Required gate failed: ' + name)
    changes = output(['git','diff','--name-only',BASE]).splitlines()
    required_docs = ['docs/40_delivery/pr_search_work_packages.md','docs/40_delivery/pr_search_implementation_traceability.md']
    if not all(p in changes for p in required_docs):
        raise RuntimeError('WP and implementation ledger updates are required')
    (EVIDENCE / 'closure-receipt.json').write_text(json.dumps({'source_fingerprint':current_fingerprint,'acceptance_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'validated_constraint_families':13,'validated_wp_dod':5},indent=2))
    print('WP061 evidence mappings exist and match passing tests; independent review must assess semantic completeness.')
    return 0

def preflight():
    guard()
    summary = {'project':str(REPO),'branch':BRANCH,'baseline':BASE,'head':output(['git','rev-parse','HEAD']),
               'dirty':bool(output(['git','status','--porcelain'])),'node':output(['node','--version']), 'pnpm':output(['pnpm','--version']),
               'checks':list(GATES),'changes_since_baseline':output(['git','diff','--name-only',BASE]).splitlines()}
    print(json.dumps(summary,ensure_ascii=False,indent=2))
    return 0

if __name__ == '__main__':
    p=argparse.ArgumentParser()
    p.add_argument('action',choices=['preflight','up','down','gate','closure'])
    p.add_argument('name',nargs='?',choices=list(GATES));p.add_argument('--baseline',action='store_true')
    args=p.parse_args();EVIDENCE.mkdir(exist_ok=True)
    try:
        if args.action=='gate':
            if not args.name:p.error('gate requires a name')
            code=gate(args.name,args.baseline)
        elif args.action=='up': print(environment_up());code=0
        elif args.action=='down': environment_down();code=0
        elif args.action=='closure':code=closure()
        else:code=preflight()
    except (RuntimeError,OSError,ValueError,KeyError,subprocess.SubprocessError) as exc:
        print(str(exc),file=sys.stderr);code=2
    raise SystemExit(code)
