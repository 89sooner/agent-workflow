"""Conservative event-derived durations; absence of telemetry is never success."""
import math
import sqlite3
import time
from . import native
from .connections import state_home
from .core import WorkflowError


def snapshot(home, days='1', project=None, provider=None, now=None):
    try:days=int(days)
    except (TypeError,ValueError):raise WorkflowError('기간은 1·7·30일 중 선택하세요.') from None
    if days not in {1,7,30}:raise WorkflowError('기간은 1·7·30일 중 선택하세요.')
    if provider and provider not in native.PROVIDERS:raise WorkflowError('지원하지 않는 도구입니다.')
    end=time.time() if now is None else now;start=end-days*86400
    all_sessions=native.snapshot(home,None,refresh=False)['sessions']
    sessions=[s for s in all_sessions
              if (not project or s['project_id']==project) and (not provider or s['provider']==provider)]
    selected={s['id']:s for s in sessions};groups={};events=[]
    path=state_home(home)/'native-observation.sqlite3'
    if path.exists():
        c=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=1);c.row_factory=sqlite3.Row
        try:events=[dict(r) for r in c.execute('SELECT session_id,seq,at,event,state,tool FROM history ORDER BY seq')]
        finally:c.close()
    for e in events:
        if e['session_id'] in selected:groups.setdefault(e['session_id'],[]).append(e)
    durations={key:0.0 for key in ['working','human_waiting','attention','idle','retrying','unknown']}
    state_keys={'running':'working','input':'human_waiting','approval':'human_waiting','attention':'attention','idle':'idle','retrying':'retrying'}
    tool_stats={};missing=0;anomaly=0;known_results=0;errors=0;unknown_results=0
    for sid,s in selected.items():
        rows=groups.get(sid,[])
        if not rows:missing+=1;continue
        # Timestamp-valid results remain evidence even when interval order is invalid.
        for e in rows:
            if isinstance(e['at'],(int,float)) and math.isfinite(e['at']) and start<=e['at']<=end:
                if e['event'] in {'PostToolUse','PostToolUseFailure','tool.execute.after'}:
                    if s['provider']=='claude' and e['event'] in {'PostToolUse','PostToolUseFailure'}:
                        known_results+=1;failed=e['event']=='PostToolUseFailure';errors+=int(failed)
                        name=e['tool'] or '(도구 미기록)';stat=tool_stats.setdefault(name,{'results':0,'errors':0});stat['results']+=1;stat['errors']+=int(failed)
                    else:unknown_results+=1
        if (any(not isinstance(e['at'],(int,float)) or not math.isfinite(e['at']) for e in rows)
                or any(b['at']<a['at'] for a,b in zip(rows,rows[1:]))):
            anomaly+=1;continue
        for i,e in enumerate(rows):
            a=max(start,e['at']);b=min(end,rows[i+1]['at'] if i+1<len(rows) else end)
            if b<=a or e['state']=='ended':continue
            observed=max(0,min(b,e['at']+300)-a)
            durations[state_keys.get(e['state'],'unknown')]+=observed
            durations['unknown']+=b-a-observed
    return dict(schema_version=1,period_start=start,period_end=end,days=days,session_count=len(sessions),
                durations_seconds={k:round(v,3) for k,v in durations.items()},
                known_tool_results=known_results,observed_tool_errors=errors if known_results else None,
                tool_error_rate=errors/known_results if known_results else None,unknown_tool_results=unknown_results,
                tool_stats=tool_stats,sessions_without_history=missing,clock_anomaly_sessions=anomaly,
                retention_limit_reached=len(events)>=native.MAX_EVENTS or len(all_sessions)>=native.MAX_SESSIONS,
                retention=dict(max_sessions=native.MAX_SESSIONS,max_events=native.MAX_EVENTS,
                               retained_sessions=len(all_sessions),retained_events=len(events),
                               session_limit_reached=len(all_sessions)>=native.MAX_SESSIONS,
                               event_limit_reached=len(events)>=native.MAX_EVENTS),
                oldest_event_at=min((e['at'] for e in events if isinstance(e['at'],(int,float)) and math.isfinite(e['at'])),default=None),
                note='저장된 이벤트부터 최대 300초까지만 상태 시간을 인정합니다. 관측 공백은 미확인으로 분리하고 기록 없는 세션·시계 역전은 시간 합산에서 제외합니다. 기간 내 시각이 확인된 도구 결과는 시간 합산과 별도로 집계합니다. 동시 세션 시간은 각각 합산합니다. API 반복 조회 사이의 연속 활동은 보장하지 않습니다.')
