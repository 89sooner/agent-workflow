import {useState} from 'react';
import {usePoll} from './api';
type Metrics={session_count:number;durations_seconds:Record<string,number>;known_tool_results:number;observed_tool_errors:number|null;tool_error_rate:number|null;unknown_tool_results:number;sessions_without_history:number;clock_anomaly_sessions:number;retention_limit_reached:boolean;retention:{max_sessions:number;max_events:number;retained_sessions:number;retained_events:number;session_limit_reached:boolean;event_limit_reached:boolean};oldest_event_at:number|null;note:string;tool_stats:Record<string,{results:number;errors:number}>};
const duration=(seconds:number)=>`${Math.floor(seconds/3600)}시간 ${Math.floor(seconds%3600/60)}분 ${Math.floor(seconds%60)}초`;
export function SessionMetrics({project='',provider=''}:{project?:string;provider?:string}){
 const [days,setDays]=useState('1'),[open,setOpen]=useState(false);
 const query=new URLSearchParams({days});if(project)query.set('project',project);if(provider)query.set('provider',provider);
 const result=usePoll<Metrics>(open?'/api/native/metrics?'+query:null,10000,0);
 const data=result.data;
 return <details className="panel" onToggle={e=>setOpen(e.currentTarget.open)} aria-label="관측 분석"><summary>관측 시간·도구 오류 요약</summary>
  <label>기간 <select aria-label="분석 기간" value={days} onChange={e=>setDays(e.target.value)}><option value="1">최근 24시간</option><option value="7">최근 7일</option><option value="30">최근 30일</option></select></label>
  {result.loading&&<p role="status">관측 구간을 분석하는 중입니다.</p>}
  {result.error&&<p role="alert">{result.error}</p>}
  {data&&<><p>조회 대상 세션 {data.session_count}개</p>
   {data.session_count===0?<p>분석할 관측 세션이 없습니다.</p>:<>
    <ul>{Object.entries({working:'실행 상태',human_waiting:'사람 대기',attention:'오류·확인 필요',idle:'유휴',retrying:'재시도',unknown:'미확인·관측 공백'}).map(([key,label])=><li key={key}>{label}: {duration(data.durations_seconds[key]||0)}</li>)}</ul>
    <p>결과가 기록된 도구 {data.known_tool_results}개 · 관측 오류 {data.observed_tool_errors==null?'미확인':data.observed_tool_errors+'개'} · 오류율 {data.tool_error_rate==null?'미확인':(data.tool_error_rate*100).toFixed(1)+'%'}</p>
    <p>결과를 판정할 수 없는 완료 메타데이터: {data.unknown_tool_results}개</p>
    <ul>{Object.entries(data.tool_stats).map(([tool,s])=><li key={tool}>{tool}: 관측 오류 {s.errors} / 결과 {s.results}</li>)}</ul>
    <p>시간 합산 제외: 기록 없음 {data.sessions_without_history}개 · 시계 이상 {data.clock_anomaly_sessions}개</p>
   </>}
   <p>{data.note}</p>
   {data.oldest_event_at!=null&&<p>저장소의 가장 오래된 보관 이벤트: {new Date(data.oldest_event_at*1000).toLocaleString()}</p>}
   <p>저장소 보관 범위: 세션 {data.retention.retained_sessions}/{data.retention.max_sessions}개 · 이벤트 {data.retention.retained_events}/{data.retention.max_events}개 (전체 프로젝트 기준)</p>
   {data.retention_limit_reached&&<p>세션 또는 이벤트 보관 한도에 도달했습니다. 선택 기간 전체가 남아 있음을 보장하지 않습니다.</p>}
  </>}
 </details>
}
