"""Bookmarks reference bounded metadata history; pruned evidence stays expired."""
from bisect import bisect_left,bisect_right
import hashlib
import json
import re
import sqlite3
import time
from urllib.parse import urlencode
from . import session_evidence as evidence
from .connections import state_home
from .core import WorkflowError


def _key(value):
    if not isinstance(value,str) or not re.fullmatch(r'[a-f0-9]{32}',value):
        raise WorkflowError('올바른 북마크 ID가 필요합니다.')
    return value


def _open(home, write=False):
    c=evidence._open(home,write)
    if c is not None and write:
        c.execute('CREATE TABLE IF NOT EXISTS bookmarks(id TEXT PRIMARY KEY,session_id TEXT,label TEXT,first_seq INTEGER,last_seq INTEGER,created_at REAL,event_count INTEGER)')
    if c is not None and write and 'event_count' not in {row[1] for row in c.execute('PRAGMA table_info(bookmarks)')}:
        c.execute('ALTER TABLE bookmarks ADD COLUMN event_count INTEGER')
    if c is not None and write:
        columns={row[1] for row in c.execute('PRAGMA table_info(bookmarks)')}
        for field in ('provider','source','native_id'):
            if field not in columns:c.execute('ALTER TABLE bookmarks ADD COLUMN '+field+' TEXT')
    return c


def _identity(home,sid):
    path=state_home(home)/'native-observation.sqlite3'
    c=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=1)
    try:
        row=c.execute('SELECT provider,source,native_id FROM sessions WHERE id=?',(sid,)).fetchone()
        if row is None:raise WorkflowError('관측 세션이 없거나 보관 기간이 지났습니다.')
        return row
    finally:c.close()


def _events(home, sid, first, last):
    path=state_home(home)/'native-observation.sqlite3'
    if not path.exists():return []
    c=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=1);c.row_factory=sqlite3.Row
    try:
        return [dict(r) for r in c.execute('SELECT h.seq,h.at,h.event,h.state,h.tool FROM history h JOIN sessions s ON s.id=h.session_id WHERE h.session_id=? AND h.seq BETWEEN ? AND ? ORDER BY h.seq LIMIT 201',(sid,first,last))]
    finally:c.close()


def change(home,data):
    if data.get('action')=='remove':
        key=_key(data.get('id'));c=_open(home)
        if c is None:return {'execution_started':False}
        try:exists=c.execute("SELECT 1 FROM sqlite_master WHERE name='bookmarks'").fetchone()
        finally:c.close()
        if exists:
            c=_open(home,True)
            try:
                with c:c.execute('DELETE FROM bookmarks WHERE id=?',(key,))
            finally:c.close()
        return {'execution_started':False}
    if data.get('action')!='add':raise WorkflowError('지원하지 않는 북마크 동작입니다.')
    sid=evidence.session(home,data.get('session'))
    label=data.get('label');first=data.get('first_seq');last=data.get('last_seq')
    if not isinstance(label,str) or not label.strip() or len(label)>120 or any(ord(ch)<32 for ch in label):
        raise WorkflowError('북마크 이름을 1~120자로 입력하세요.')
    if type(first)!=int or type(last)!=int or not 0<first<=last<=2**63-1:
        raise WorkflowError('이벤트 구간을 확인하세요.')
    events=_events(home,sid,first,last)
    if not events or events[0]['seq']!=first or events[-1]['seq']!=last or len(events)>200:
        raise WorkflowError('현재 남아 있는 같은 세션의 이벤트 200개 이내 구간을 선택하세요.')
    label=label.strip();key=hashlib.sha256(json.dumps([sid,label,first,last]).encode()).hexdigest()[:32]
    identity=_identity(home,sid)
    c=_open(home,True)
    try:
        with c:
            c.execute('BEGIN IMMEDIATE')
            if (not c.execute('SELECT 1 FROM bookmarks WHERE id=?',(key,)).fetchone()
                    and c.execute('SELECT COUNT(*) FROM bookmarks').fetchone()[0]>=500):
                raise WorkflowError('북마크 500개 한도입니다. 기존 북마크를 정리하세요.')
            c.execute('INSERT OR IGNORE INTO bookmarks(id,session_id,label,first_seq,last_seq,created_at,event_count,provider,source,native_id) VALUES(?,?,?,?,?,?,?,?,?,?)',(key,sid,label,first,last,time.time(),len(events),*identity))
    finally:c.close()
    return {'id':key,'execution_started':False}


def _rows(home, key=None):
    c=_open(home)
    if c is None:return []
    try:
        if not c.execute("SELECT 1 FROM sqlite_master WHERE name='bookmarks'").fetchone():return []
        return [dict(r) for r in (c.execute('SELECT * FROM bookmarks WHERE id=?',(_key(key),)) if key else c.execute('SELECT * FROM bookmarks ORDER BY created_at DESC,id LIMIT 500'))]
    finally:c.close()


def view(home):
    rows=_rows(home);groups={};error=None
    path=state_home(home)/'native-observation.sqlite3'
    try:
        if rows and path.exists():
            c=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=1)
            try:
                for sid,seq in c.execute('SELECT h.session_id,h.seq FROM history h JOIN sessions s ON s.id=h.session_id ORDER BY h.seq'):
                    groups.setdefault(sid,[]).append(seq)
            finally:c.close()
    except (OSError,sqlite3.Error):error='원본 근거 조회 미확인'
    for row in rows:
        seqs=groups.get(row['session_id'],[])
        left=bisect_left(seqs,row['first_seq']);right=bisect_right(seqs,row['last_seq'])
        row['error']=error or ('원본 구간 크기 미기록' if row.get('event_count') is None else None)
        row['expired']=None if row['error'] else not (right>left and seqs[left]==row['first_seq'] and seqs[right-1]==row['last_seq'] and right-left==row['event_count'])
        row['url']='/sessions?'+urlencode({'session':row['session_id'],'bookmark':row['id']})
    return {'bookmarks':rows}


def export(home,key):
    rows=_rows(home,key)
    if not rows:raise WorkflowError('북마크가 없습니다.')
    row=rows[0];events=_events(home,row['session_id'],row['first_seq'],row['last_seq'])
    complete=bool(events) and events[0]['seq']==row['first_seq'] and events[-1]['seq']==row['last_seq'] and len(events)==row.get('event_count')
    return {'schema_version':1,'bookmark':row,'complete':complete,'events':events,
            'note':'관측 메타데이터 구간입니다. 모델 대화 전문·도구 입력·검증 완료 증명이 아닙니다.'}
