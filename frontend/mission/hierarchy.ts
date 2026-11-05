export interface HierarchySession {
  id:string; native_id:string; source:string; provider:string; parent:string;
  name:string; state:string;
}
export interface HierarchyRow<T> {session:T; depth:number; issue:""|"parent_missing"|"cycle"}
export function sessionHierarchy<T extends HierarchySession>(sessions:T[]):HierarchyRow<T>[] {
  const key=(s:HierarchySession,id=s.native_id)=>JSON.stringify([s.provider,s.source,id]);
  const byKey=new Map(sessions.map(s=>[key(s),s]));
  const parents=new Map<string,string>();
  const issues=new Map<string,"parent_missing"|"cycle">();
  for(const s of sessions){
    if(!s.parent)continue;
    const p=byKey.get(key(s,s.parent));
    if(p)parents.set(s.id,p.id);else issues.set(s.id,'parent_missing');
  }
  // Break one edge in every cycle; all sessions remain reachable from a root.
  const checked=new Set<string>();
  for(const s of sessions){
    const chain=new Set<string>();let id:string|undefined=s.id;
    while(id!==undefined&&!checked.has(id)){
      if(chain.has(id)){parents.delete(id);issues.set(id,'cycle');break}
      chain.add(id);id=parents.get(id);
    }
    for(const visited of chain)checked.add(visited);
  }
  const children=new Map<string, T[]>();
  for(const s of sessions){const p=parents.get(s.id);if(p){const list=children.get(p)||[];list.push(s);children.set(p,list)}}
  const stack=sessions.filter(s=>!parents.has(s.id)).reverse().map(s=>({session:s,depth:0}));
  const rows:HierarchyRow<T>[]=[],seen=new Set<string>();
  while(stack.length){
    const row=stack.pop()!;if(seen.has(row.session.id))continue;seen.add(row.session.id);
    rows.push({...row,issue:issues.get(row.session.id)||''});
    for(const child of [...(children.get(row.session.id)||[])].reverse())stack.push({session:child,depth:row.depth+1});
  }
  return rows;
}
