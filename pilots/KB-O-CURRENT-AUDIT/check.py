#!/usr/bin/env python3
import argparse,json,subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parent
REPO=Path('/home/roqkf/katakuri-band-o-audit-current-link')
BASE='f5e77a8bd81fab309bd61fa792019c98f76fb6b5'
ALLOWED={
 'web/components/risk/KillSwitchPanel.tsx',
 'web/tests/risk.current-audit-link.test.tsx',
 'web/tests/risk.render.test.tsx','web/tests/risk.control.test.tsx','web/tests/audit.link.test.tsx',
 'agent-context/todos.md','agent-context/session-handoff.md',
 'agent-context/sessions/2026-10-08-current-audit-link.md',
}
def git(*args):return subprocess.check_output(['git','-c','core.autocrlf=false','-C',str(REPO),*args],text=True).strip()
def gate(name):
 if git('branch','--show-current')!='feat/top-current-audit-link':raise RuntimeError('wrong branch')
 if name=='scope':
  changes=set(git('diff','--name-only',BASE).splitlines())|set(git('ls-files','--others','--exclude-standard').splitlines())
  outside=changes-ALLOWED
  if outside:raise RuntimeError('Outside scope: '+', '.join(sorted(outside)))
  if 'web/components/risk/KillSwitchPanel.tsx' not in changes:raise RuntimeError('Implementation has not changed')
  if not any(p.startswith('web/tests/') for p in changes):raise RuntimeError('Behavioral regression evidence missing')
  print(json.dumps({'changed_files':sorted(changes),'scope':'PASS'}));return 0
 if name=='contract':command=['node',str(ROOT/'verify-link.cjs')]
 elif name=='unit':command=['npm','test','--','tests/risk.render.test.tsx','tests/risk.control.test.tsx','tests/audit.link.test.tsx','tests/risk.current-audit-link.test.tsx','--maxWorkers=2']
 elif name=='typecheck':command=['npm','run','typecheck']
 else:
  paths=[p.removeprefix('web/') for p in sorted(ALLOWED) if p.startswith('web/') and (REPO/p).exists()]
  command=['./node_modules/.bin/eslint',*paths]
 print(json.dumps({'gate':name,'command':command}),flush=True)
 return subprocess.run(command,cwd=REPO/'web',timeout=600).returncode
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('gate',choices=['contract','unit','typecheck','lint','scope']);args=p.parse_args()
 try:raise SystemExit(gate(args.gate))
 except (RuntimeError,OSError,subprocess.SubprocessError) as e:print(str(e),file=sys.stderr);raise SystemExit(2)
