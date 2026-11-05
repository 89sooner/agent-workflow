"""User-selected evidence, stored outside workflow execution ledgers."""
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import time
from urllib.parse import urlencode, urlsplit

from .connections import state_home, registry_ledgers, resolve_ledger, discover
from .core import Store, WorkflowError


def _identifier(identifier):
    if not isinstance(identifier, str) or not re.fullmatch(r'[a-f0-9]{32}', identifier):
        raise WorkflowError('올바른 관측 세션 ID가 필요합니다.')
    return identifier


def session(home, identifier):
    _identifier(identifier)
    db = state_home(home) / 'native-observation.sqlite3'
    if not db.is_file():
        raise WorkflowError('관측 세션이 없습니다.')
    c = sqlite3.connect(db.as_uri()+'?mode=ro', uri=True, timeout=1)
    try:
        row = c.execute('SELECT id FROM sessions WHERE id=?',(identifier,)).fetchone()
        if not row:
            raise WorkflowError('관측 세션이 없거나 보관 기간이 지났습니다.')
    finally:
        c.close()
    return identifier


def _open(home, write=False):
    path = state_home(home) / 'observer-metadata.sqlite3'
    if not write and not path.exists():
        return None
    c = sqlite3.connect(path if write else path.as_uri()+'?mode=ro', uri=not write, timeout=1)
    c.row_factory = sqlite3.Row
    version=c.execute('PRAGMA user_version').fetchone()[0]
    if version>1:
        c.close()
        raise WorkflowError('지원하지 않는 관측 메타데이터 버전입니다.')
    if write and version==0:c.execute('PRAGMA user_version=1')
    if write:
        path.chmod(0o600)
        c.execute('CREATE TABLE IF NOT EXISTS evidence(id TEXT PRIMARY KEY,session_id TEXT,kind TEXT,data TEXT,created_at REAL)')
    return c


def sources(home):
    registered = registry_ledgers(home)
    return discover(homes=[row['home'] for row in registered],
                    include_processes=False, include_temporary=False, include_default=False)['ledgers']


def _workflow(home, ledger_id, task_id):
    # Resolve against the current explicit registry, never the discovery cache.
    source = resolve_ledger(home, ledger_id)
    if not isinstance(ledger_id,str) or not ledger_id:
        raise WorkflowError('연결된 workflow 원장을 선택하세요.')
    if not isinstance(task_id,str) or not 0 < len(task_id) <= 160:
        raise WorkflowError('작업 ID를 확인하세요.')
    store = Store(source, read_only=True)
    try:
        return store.get(task_id)
    finally:
        store.close()


def change(home, data):
    identifier = _identifier(data.get('session'))
    action = data.get('action')
    if action == 'remove':
        key = data.get('id')
        if not isinstance(key,str) or not re.fullmatch(r'[a-f0-9]{32}',key):
            raise WorkflowError('연결 ID가 필요합니다.')
        c = _open(home)
        if c is not None:
            c.close()
            c = _open(home,True)
            try:
                with c:c.execute('DELETE FROM evidence WHERE id=? AND session_id=?',(key,identifier))
            finally:c.close()
        return {'execution_started':False}
    if action != 'add':
        raise WorkflowError('지원하지 않는 연결 동작입니다.')
    session(home, identifier)
    kind = data.get('kind')
    if kind == 'workflow':
        _workflow(home,data.get('ledger'),data.get('task'))
        value = {'ledger':data['ledger'],'task':data['task']}
    elif kind == 'pr':
        raw = data.get('url')
        if not isinstance(raw,str) or len(raw)>500:
            raise WorkflowError('GitHub PR 주소를 확인하세요.')
        try:
            url = urlsplit(raw)
        except ValueError:
            raise WorkflowError("GitHub PR 주소를 확인하세요.") from None
        if (url.scheme!='https' or url.netloc!='github.com' or url.query or url.fragment
                or not re.fullmatch(r'/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/pull/[1-9][0-9]*/?',url.path)):
            raise WorkflowError('https://github.com/소유자/저장소/pull/번호 주소만 지원합니다.')
        value = {'url':raw.rstrip('/')}
    else:
        raise WorkflowError('지원하지 않는 근거 종류입니다.')
    encoded=json.dumps(value,sort_keys=True)
    key=hashlib.sha256((identifier+kind+encoded).encode()).hexdigest()[:32]
    c=_open(home,True)
    try:
        with c:
            c.execute('BEGIN IMMEDIATE')
            existing=c.execute('SELECT 1 FROM evidence WHERE id=?',(key,)).fetchone()
            if not existing and c.execute('SELECT COUNT(*) FROM evidence').fetchone()[0]>=1000:
                raise WorkflowError('근거 연결 1000개 한도에 도달했습니다. 기존 연결을 정리하세요.')
            c.execute('INSERT OR IGNORE INTO evidence VALUES(?,?,?,?,?)',(key,identifier,kind,encoded,time.time()))
    finally:c.close()
    return {'id':key,'execution_started':False}


def view(home, identifier):
    if identifier: _identifier(identifier)
    c=_open(home)
    if c is None:return {'links':[]}
    try:
        rows=[dict(r) for r in (c.execute('SELECT * FROM evidence WHERE session_id=? ORDER BY created_at,id',(identifier,)) if identifier else c.execute('SELECT * FROM evidence ORDER BY created_at,id LIMIT 1000'))]
    finally:c.close()
    links=[]
    for row in rows:
        value=json.loads(row.pop('data'))
        row['attribution']='user_selected'
        if row['kind']=='pr':
            row.update(url=value['url'],label='사용자가 연결한 PR',status='미조회')
        else:
            try:
                task=_workflow(home,value['ledger'],value['task'])
                verification=task.get('verification') if isinstance(task.get('verification'),dict) else {}
                passed=verification.get('passed')
                row.update(label=task['id'],status=task.get('status','unknown'),
                           branch=task.get('workspace',{}).get('branch') if isinstance(task.get('workspace'),dict) else None,
                           check_passed=passed if isinstance(passed,bool) else None,
                           url='/mission?'+urlencode({'ledger':value['ledger'],'task':value['task']}))
            except (WorkflowError,OSError,sqlite3.Error,ValueError,TypeError):
                row.update(label=value['task'],status='근거 접근 불가',url=None,check_passed=None)
        links.append(row)
    return {'links':links}
