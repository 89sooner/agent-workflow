export interface AttentionSession {
  id: string;
  state: string;
  waiting_since?: number | null;
}
export function isAttention(s: AttentionSession) {
  return ["input", "approval", "attention"].includes(s.state);
}
export function attentionFirst<T extends AttentionSession>(sessions: T[]): T[] {
  return [...sessions].sort((a,b)=>{
    const aa=isAttention(a), bb=isAttention(b);
    if(aa!==bb)return aa?-1:1;
    if(!aa)return 0;
    const at=a.waiting_since??Infinity, bt=b.waiting_since??Infinity;
    if(at!==bt)return at<bt?-1:1;
    return a.id<b.id?-1:a.id>b.id?1:0;
  });
}
