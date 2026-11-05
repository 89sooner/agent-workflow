import { useEffect, useRef, useState } from "react";
import { get } from "./api";
type Result = { steps: { id: string; status: string; reason: string; action: string }[]; last_seen?: number | null };
const labels: Record<string,string> = {project:"프로젝트 경로",registration:"프로젝트 등록",installation:"관측 설치",host_activation:"호스트 활성화",first_event:"첫 이벤트 수신"};
const statuses: Record<string,string> = {pass:"확인",fail:"조치 필요",unknown:"미확인","not-applicable":"해당 없음"};
export function ConnectionDiagnostics() {
  const [project,setProject]=useState("");
  const [provider,setProvider]=useState("claude");
  const [data,setData]=useState<Result|null>(null);
  const [error,setError]=useState("");
  const [busy,setBusy]=useState(false);
  const request=useRef<AbortController|null>(null);
  const generation=useRef(0);
  function reset(){generation.current++;request.current?.abort();setData(null);setError("");setBusy(false)}
  useEffect(()=>()=>{generation.current++;request.current?.abort()},[]);
  async function diagnose(){
    reset();const ticket=generation.current;const controller=new AbortController();request.current=controller;setBusy(true);
    const timeout=setTimeout(()=>controller.abort(),12000);
    try{
      const value=await get<Result>("/api/connections/diagnostics?"+new URLSearchParams({project,provider}),controller.signal);
      if(ticket===generation.current)setData(value);
    }catch(e){if(ticket===generation.current)setError(controller.signal.aborted?"진단 시간이 초과되었습니다. 다시 시도하세요.":e instanceof Error?e.message:String(e))}
    finally{clearTimeout(timeout);if(ticket===generation.current)setBusy(false)}
  }
  return <details className="panel native-setup"><summary>프로젝트 관측 연결 진단</summary>
    <p>설정이나 작업을 실행하지 않고 등록·설치·수신 근거를 확인합니다.</p>
    <label>도구 <select aria-label="진단 도구" value={provider} onChange={e=>{reset();setProvider(e.target.value)}}>
      <option value="claude">Claude Code</option><option value="codex">Codex</option><option value="opencode">OpenCode</option>
    </select></label>
    <label>프로젝트 절대 경로 <input aria-label="진단 프로젝트 경로" value={project} onChange={e=>{reset();setProject(e.target.value)}} /></label>
    <button disabled={busy||!project.trim()} onClick={()=>void diagnose()}>{busy?"진단 중…":"연결 진단 실행"}</button>
    {error&&<p role="alert">{error}</p>}
    {data&&<ol aria-label="연결 진단 결과">{data.steps.map(s=><li key={s.id}>
      <strong>{labels[s.id]||s.id}: {statuses[s.status]||s.status}</strong><p>{s.reason}</p><p>{s.action}</p>
    </li>)}</ol>}
    {data?.last_seen!=null&&<p>마지막 수신: {new Date(data.last_seen*1000).toLocaleString()}</p>}
  </details>
}
