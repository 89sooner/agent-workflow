import {useMemo} from 'react';
import {sessionHierarchy,type HierarchySession} from './hierarchy';
export function SessionHierarchy({sessions,selected,onSelect}:{sessions:HierarchySession[];selected:string;onSelect:(id:string)=>void}){
 const rows=useMemo(()=>sessionHierarchy(sessions),[sessions]);
 return <details className="panel"><summary>부모·하위 에이전트 탐색 ({rows.length})</summary>
  <p>현재 필터의 기록된 부모 관계입니다. 이름은 관측 저장 이름이며 실제 배정 역할을 추정하지 않습니다.</p>
  <ol aria-label="세션 계층">{rows.map(({session:s,depth,issue})=><li key={s.id} style={{marginInlineStart:Math.min(depth,8)*12,overflowWrap:'anywhere'}}>
   <button aria-pressed={selected===s.id} onClick={()=>onSelect(s.id)}>{s.name}</button> · {s.provider} · {s.state}
   {depth>8&&<small> · 깊이 {depth}</small>}
   {issue==='parent_missing'&&<p>부모 기록이 없거나 현재 필터 범위 밖에 있습니다.</p>}
   {issue==='cycle'&&<p>순환 관계를 분리하여 표시합니다.</p>}
  </li>)}</ol>
 </details>
}
