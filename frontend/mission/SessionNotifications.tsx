import {useEffect,useRef,useState} from 'react';
import {reconcile,transitionKey,type ObservedSession} from './notifications';
const preference='workflow-session-notifications';
const labels:Record<string,string>={input:'입력 요청',approval:'승인 요청',attention:'오류·확인 필요',ended:'세션 종료'};
export function SessionNotifications({sessions,fresh,observedAt,onEnabledChange}:{sessions:ObservedSession[];fresh:boolean;observedAt?:number;onEnabledChange:(value:boolean)=>void}) {
  const [enabled,setEnabled]=useState(false),[notice,setNotice]=useState('');
  const previous=useRef<Map<string,string|null>|null>(null);
  const observedSince=useRef<number>(Infinity);
  const mounted=useRef(true);
  const latest=useRef(sessions);latest.current=sessions;
  useEffect(()=>{
    mounted.current=true;
    const sync=()=>{try{setEnabled(localStorage.getItem(preference)==='on')}catch{}};
    sync();window.addEventListener('storage',sync);
    return()=>{mounted.current=false;window.removeEventListener('storage',sync)};
  },[]);
  useEffect(()=>onEnabledChange(enabled),[enabled,onEnabledChange]);
  useEffect(()=>{
    if(!fresh)return;
    if(previous.current===null)observedSince.current=observedAt??Date.now()/1000;
    const {next,changed}=reconcile(previous.current,sessions,observedSince.current);previous.current=next;
    if(!enabled)return;

    for(const s of changed){
      setNotice(`${labels[s.state]}: ${s.name}`);
      if(typeof Notification==='undefined'||Notification.permission!=='granted'||!navigator.locks)continue;
      void navigator.locks.request('workflow-session-alert',async()=>{
        if(!mounted.current)return;
        const current=latest.current.find(row=>row.id===s.id);
        if(!current?.fresh || transitionKey(current)!==transitionKey(s))return;
        try{
          if(localStorage.getItem(preference)!=='on')return;
          const key='workflow-alert:'+transitionKey(s);
          if(localStorage.getItem(key))return;
          // Bound persisted dedupe receipts to the most recent 1000 entries.
          const keys=Object.keys(localStorage).filter(k=>k.startsWith('workflow-alert:'));
          if(keys.length>=1000)keys.sort((a,b)=>Number(localStorage.getItem(a))-Number(localStorage.getItem(b))).slice(0,keys.length-999).forEach(k=>localStorage.removeItem(k));
          localStorage.setItem(key,String(Date.now()));
          const alert=new Notification(labels[s.state],{body:s.name,tag:key});
          alert.onclick=()=>{alert.close();window.focus();location.assign('/sessions?'+new URLSearchParams({session:s.id}))};
        }catch{ /* Permission, storage or platform failures never stop observation. */ }
      });
    }

  },[sessions,fresh,enabled,observedAt]);
  async function toggle(){
    if(enabled){setEnabled(false);setNotice('알림을 껐습니다.');try{localStorage.setItem(preference,'off')}catch{};return}
    if(typeof Notification==='undefined'){setNotice('이 브라우저는 데스크톱 알림을 지원하지 않습니다. 확인 요청함을 사용하세요.');return}
    try{
      const permission=await Notification.requestPermission();
      if(permission!=='granted'){setNotice('알림 권한이 허용되지 않았습니다. 브라우저 설정에서 변경하거나 확인 요청함을 사용하세요.');return}
      setEnabled(true);try{localStorage.setItem(preference,'on')}catch{}
      setNotice(navigator.locks?'새 상태 변화부터 알립니다.':'탭 간 중복 방지를 지원하지 않아 화면 안 알림만 제공합니다.');
    }catch{setNotice('알림 권한을 요청하지 못했습니다. 확인 요청함을 사용하세요.')}
  }
  return <section className="panel"><button onClick={()=>void toggle()}>{enabled?'알림 끄기':'알림 켜기'}</button>
    <p>입력·승인·오류·세션 종료의 새 관측 전이를 알립니다. 브라우저가 열린 동안 동작합니다.</p>
    {notice&&<p role="status">{notice}</p>}
  </section>
}
