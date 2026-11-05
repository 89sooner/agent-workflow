import {useEffect,useRef,useState} from 'react';
import {configure,usePoll} from './api';
import type {Capabilities,Discovery} from './types';
type Link={id:string;session_id:string;kind:string;label:string;status:string;url:string|null;branch?:string|null;check_passed?:boolean|null};
export function SessionEvidence({session,capabilities}:{session?:string;capabilities:Capabilities|null}){
 const [limit,setLimit]=useState(20),[refresh,setRefresh]=useState(0),[kind,setKind]=useState('workflow'),[ledger,setLedger]=useState(''),[value,setValue]=useState(''),[error,setError]=useState(''),[busy,setBusy]=useState(false);
 const request=useRef<AbortController|null>(null);
 const result=usePoll<{links:Link[];ledgers?:Discovery["ledgers"];sources_error?:string}>('/api/native/evidence?'+new URLSearchParams(session?{session}:{}),5000,refresh);
 useEffect(()=>()=>request.current?.abort(),[]);
 async function change(data:Record<string,unknown>){
  if(!capabilities||busy)return;
  const controller=new AbortController();request.current=controller;setBusy(true);setError('');
  const timeout=setTimeout(()=>controller.abort(),12000);
  try{await configure('/api/native/evidence',{session,...data},capabilities.csrf_token,controller.signal);setRefresh(v=>v+1);setValue('')}
  catch(e){if(!controller.signal.aborted)setError(e instanceof Error?e.message:String(e));else setError('연결 요청이 중단됐습니다. 목록을 새로 확인하세요.')}
  finally{clearTimeout(timeout);setBusy(false)}
 }
 return <section aria-label={session?"연결된 결과 근거":"전체 결과 근거"}><h3>연결된 결과 근거</h3>
  <p>사용자가 명시적으로 연결한 기록입니다. 같은 폴더라는 이유로 세션의 결과라고 판단하지 않습니다.</p>
  {result.error&&<p role="alert">{result.error}</p>}
  {!result.loading&&!result.data?.links.length&&<p>연결된 근거가 없습니다. 검사 미실행을 성공으로 표시하지 않습니다.</p>}
  <ul>{result.data?.links.slice(0,limit).map(link=><li key={link.id} style={{overflowWrap:'anywhere'}}>
    {!session&&<a href={'/sessions?'+new URLSearchParams({session:link.session_id})}>원본 세션 {link.session_id.slice(0,8)} · </a>}
    {link.url?<a href={link.url} rel="noopener noreferrer">{link.label}</a>:link.label} · {link.status}
    {link.kind==='workflow'&&<p>브랜치: {link.branch||'기록 없음'} · 기록된 검사: {link.check_passed==null?'미확인·미실행':link.check_passed?'통과':'실패'} (현재 코드 재검사 아님)</p>}
    {capabilities?.can_configure_connections&&<button disabled={busy} onClick={()=>void change({action:'remove',id:link.id,session:link.session_id})}>근거 연결 해제</button>}
  </li>)}</ul>
  {(result.data?.links.length||0)>limit&&<button onClick={()=>setLimit(v=>v+20)}>근거 더 보기</button>}
  {capabilities?.can_configure_connections&&session?<details><summary>결과 근거 연결</summary>
    <label>근거 종류 <select aria-label="근거 종류" disabled={busy} value={kind} onChange={e=>{setKind(e.target.value);setValue('')}}><option value="workflow">workflow 작업</option><option value="pr">GitHub PR</option></select></label>
    {kind==='workflow'&&<label>원장 <select aria-label="근거 원장" value={ledger} disabled={busy} onChange={e=>setLedger(e.target.value)}><option value="">원장 선택</option>{result.data?.ledgers?.filter(s=>s.kind!=='opencode'&&s.status==='available').map(s=><option key={s.id} value={s.id}>{s.label||s.home}</option>)}</select></label>}
    {result.data?.sources_error&&<p role="alert">{result.data.sources_error}</p>}
    <label>{kind==='workflow'?'작업 ID':'PR URL'} <input aria-label="근거 값" value={value} disabled={busy} onChange={e=>setValue(e.target.value)}/></label>
    <button disabled={busy||!value.trim()||(kind==='workflow'&&!ledger)} onClick={()=>void change({action:'add',kind,...(kind==='workflow'?{ledger,task:value.trim()}:{url:value.trim()})})}>근거 연결 저장</button>
    {error&&<p role="alert">{error}</p>}
  </details>:<p>{capabilities?.can_configure_connections?"새 근거는 해당 세션 상세에서 연결하세요. 원본 세션이 정리된 연결도 여기서 해제할 수 있습니다.":"읽기 전용 모드에서는 근거 연결을 변경할 수 없습니다."}</p>}
 </section>
}
