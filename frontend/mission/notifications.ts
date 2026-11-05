export type ObservedSession = {id:string; state:string; state_since?:number|null; fresh:boolean; name:string};
export const notificationStates = new Set(['input','approval','attention','ended']);
export function transitionKey(s:ObservedSession) {
  return s.state_since == null ? null : `${s.id}:${s.state}:${s.state_since}`;
}
export function reconcile(previous:Map<string,string|null>|null, sessions:ObservedSession[], observedSince=Infinity) {
  const next=new Map(sessions.map(s=>[s.id,s.fresh ? transitionKey(s) : previous?.get(s.id) ?? null]));
  const changed=previous===null?[]:sessions.filter(s=>s.fresh && notificationStates.has(s.state) && (previous.get(s.id)!=null || (s.state_since??-Infinity)>=observedSince) && transitionKey(s)!==null && previous.get(s.id)!==transitionKey(s));
  return {next,changed};
}
