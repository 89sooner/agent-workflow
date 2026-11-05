import {useEffect,useRef,useState} from 'react';
import {configure,usePoll} from './api';
import type {Capabilities} from './types';
type Bookmark={id:string;session_id:string;label:string;first_seq:number;last_seq:number;expired:boolean|null;error:string|null;url:string;provider?:string|null;source?:string|null;native_id?:string|null};
type History={seq:number;at:number;event:string;state:string;tool:string};
type Export={bookmark:Bookmark;complete:boolean;events:History[];note:string};
export function SessionBookmarks({session,history=[],capabilities,initialBookmark=""}:{session?:string;history?:History[];capabilities:Capabilities|null;initialBookmark?:string}){
 const [refresh,setRefresh]=useState(0),[label,setLabel]=useState('기록 구간'),[first,setFirst]=useState(''),[last,setLast]=useState(''),[selected,setSelected]=useState(initialBookmark),[limit,setLimit]=useState(20),[busy,setBusy]=useState(false),[error,setError]=useState('');
 const request=useRef<AbortController|null>(null);useEffect(()=>()=>request.current?.abort(),[]);
 const records=usePoll<{bookmarks:Bookmark[]}>('/api/native/bookmarks',5000,refresh);
 const preview=usePoll<Export>(selected?'/api/native/bookmarks/export?'+new URLSearchParams({id:selected}):null,30000,refresh);
 const rows=(records.data?.bookmarks||[]).filter(b=>!session||b.session_id===session);
 const ordered=[...history].sort((a,b)=>a.seq-b.seq),start=first||String(ordered[0]?.seq||''),end=last||String(ordered.at(-1)?.seq||'');
 async function change(data:Record<string,unknown>){
  if(!capabilities||busy)return;const controller=new AbortController();request.current=controller;setBusy(true);setError('');const timeout=setTimeout(()=>controller.abort(),12000);
  try{await configure('/api/native/bookmarks',data,capabilities.csrf_token,controller.signal);setRefresh(v=>v+1);if(data.action==='remove'&&data.id===selected)setSelected('')}
  catch(e){setError(controller.signal.aborted?'요청이 중단됐습니다. 목록을 다시 확인하세요.':e instanceof Error?e.message:String(e))}
  finally{clearTimeout(timeout);setBusy(false)}
 }
 return <section className="panel" aria-label={session?'세션 북마크':'전체 북마크'}><h3>기록 북마크</h3>
  <p>관측 이벤트 구간을 저장합니다. 원본이 정리되면 만료로 표시하며 검사 성공·모델 대화 전문을 뜻하지 않습니다.</p>
  {records.error&&<p role="alert">{records.error}</p>}
  {!records.loading&&!rows.length&&<p>저장된 북마크가 없습니다.</p>}
  <ul>{rows.slice(0,limit).map(b=><li key={b.id} style={{overflowWrap:'anywhere'}}>
   {b.expired===false?<a href={b.url}>{b.label}</a>:<strong>{b.label}</strong>} · {b.expired===true?'근거 만료':b.expired===false?'근거 있음':'조회 미확인'} · {b.first_seq}~{b.last_seq}
   {b.error&&<p>{b.error}</p>}
   <button onClick={()=>setSelected(b.id)}>구간 보기</button> <a href={'/api/native/bookmarks/export?'+new URLSearchParams({id:b.id})}>메타데이터 내보내기</a>
   {capabilities?.can_configure_connections&&<button disabled={busy} onClick={()=>void change({action:'remove',id:b.id})}>북마크 삭제</button>}
  </li>)}</ul>
  {rows.length>limit&&<button onClick={()=>setLimit(v=>v+20)}>북마크 더 보기</button>}
  {preview.error&&<p role="alert">{preview.error}</p>}
  {preview.data&&(!session||preview.data.bookmark.session_id===session)&&<div aria-label="북마크 이벤트 구간"><h4>{preview.data.bookmark.label}</h4>
    <p style={{overflowWrap:"anywhere"}}>공급자: {preview.data.bookmark.provider||"미기록"} · 소스: {preview.data.bookmark.source||"미기록"} · 원래 세션: {preview.data.bookmark.native_id||"미기록"}</p>
    {!preview.data.complete&&<p>원본 근거 일부가 만료됐거나 구간을 완전히 확인할 수 없습니다.</p>}
    <ol>{preview.data.events.map(e=><li key={e.seq}>{e.seq} · {e.event} · {e.state} · {e.tool}</li>)}</ol>
  </div>}
  {session&&capabilities?.can_configure_connections&&<details><summary>현재 세션 구간 저장</summary>
   <label>이름 <input aria-label="북마크 이름" maxLength={120} value={label} onChange={e=>setLabel(e.target.value)}/></label>
   <label>처음 이벤트 <select aria-label="북마크 처음 이벤트" value={start} onChange={e=>setFirst(e.target.value)}>{ordered.map(e=><option key={e.seq} value={e.seq}>{e.seq} · {e.event}</option>)}</select></label>
   <label>마지막 이벤트 <select aria-label="북마크 마지막 이벤트" value={end} onChange={e=>setLast(e.target.value)}>{ordered.map(e=><option key={e.seq} value={e.seq}>{e.seq} · {e.event}</option>)}</select></label>
   <button disabled={busy||!label.trim()||!start||!end} onClick={()=>void change({action:'add',session,label:label.trim(),first_seq:Number(start),last_seq:Number(end)})}>북마크 저장</button>
  </details>}
  {error&&<p role="alert">{error}</p>}
 </section>
}
