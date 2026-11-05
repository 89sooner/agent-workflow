import {useState} from 'react';
export function SessionReturn({project,nativeId,provider}:{project:string;nativeId:string;provider:string}){
 const [message,setMessage]=useState('');
 async function copy(value:string,label:string){
  try{await navigator.clipboard.writeText(value);setMessage(label+'를 복사했습니다.')}
  catch{setMessage('클립보드에 접근할 수 없습니다. 아래 값을 선택해 복사하세요.')}
 }
 return <section aria-label="원래 도구로 복귀">
  <h3>원래 도구로 복귀</h3>
  <p>{provider}의 해당 프로젝트·세션으로 돌아가 입력·승인을 처리하세요. 이 연결에서 검증된 직접 이동 링크는 제공되지 않습니다.</p>
  <button onClick={()=>void copy(project,'프로젝트 경로')}>프로젝트 경로 복사</button>
  <button onClick={()=>void copy(nativeId,'세션 ID')}>세션 ID 복사</button>
  <p style={{overflowWrap:'anywhere'}}>프로젝트: <code>{project}</code><br/>세션: <code>{nativeId}</code></p>
  {message&&<p role="status">{message}</p>}
 </section>
}
