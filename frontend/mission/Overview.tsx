import { SessionEvidence } from "./SessionEvidence";
import { SessionMetrics } from "./SessionMetrics";
import { SessionBookmarks } from "./SessionBookmarks";
import type {Capabilities} from "./types";
import { ConnectionDiagnostics } from "./ConnectionDiagnostics";
import { useState } from "react";
import { usePoll } from "./api";
type OverviewData = {
  projects: { id: string; path: string; managed: number; native: number }[];
  items: { id: string; kind: string; project_id: string; name: string; state: string; provider?: string; href: string }[];
  errors: { source: string; error: string }[];
};
export default function Overview() {
  const [project, setProject] = useState("");
  const result = usePoll<OverviewData>("/api/overview", 5000, 0);
  const data = result.data;
  const capabilities = usePoll<Capabilities>("/api/capabilities",60000,0);
  return <main className="native-page">
    <header className="panel"><h1>프로젝트 통합 관제실</h1><p>관리형 작업과 일상 세션을 프로젝트별로 확인합니다.</p>
      <nav><a href="/mission">관리형 작업 관제실</a> · <a href="/sessions">일상 세션·프로젝트 연결</a></nav>
    </header>
    <SessionMetrics project={project} />
    <SessionBookmarks capabilities={capabilities.data} />
    <SessionEvidence capabilities={capabilities.data} />
    <ConnectionDiagnostics />
    <section className="panel"><label>프로젝트 <select aria-label="통합 프로젝트" value={project} onChange={e=>setProject(e.target.value)}>
      <option value="">전체 프로젝트</option>{data?.projects.map(p=><option key={p.id} value={p.id}>{p.path} · 작업 {p.managed} / 세션 {p.native}</option>)}
    </select></label>
    {result.loading && <p role="status">관측 기록을 불러오는 중입니다.</p>}
    {result.error && <p role="alert">{result.error} · 마지막 조회 기록을 표시합니다.</p>}
    {data?.errors.map((e,i)=><p role="status" key={i}>{e.source}: {e.error}</p>)}
    {data && !data.items.length && <p>관측 기록이 없습니다. 일상 세션 화면에서 프로젝트를 연결하세요.</p>}
    {data && project && !data.projects.some(p=>p.id===project) && <p>선택했던 프로젝트를 현재 조회할 수 없습니다. 전체 프로젝트를 선택하세요.</p>}
    <ul>{data?.items.filter(i=>!project||i.project_id===project).map(i=><li key={i.id}>
      <a href={i.href}>{i.name}</a> · {i.kind==="managed"?"관리형 작업":i.provider+" 일상 세션"} · {i.state}
    </li>)}</ul></section>
  </main>;
}
