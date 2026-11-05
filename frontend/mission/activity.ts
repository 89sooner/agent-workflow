export const activityLabel=(value?:string)=>({reading:'읽는 중',writing:'수정 중',command:'명령 실행 중',working:'작업 중',input:'입력 대기',approval:'승인 대기',attention:'확인 필요',idle:'유휴',ended:'기록 종료',unknown:'관측 미확인',retrying:'재시도 대기'}[value||'']||'활동 종류 미기록');
export function activityPose(value?:string){
 return {marker:value==='writing'?'box':value==='command'?'octahedron':'sphere',
         headPitch:value==='reading'?Math.PI/12:0,
         armRest:value==='reading'?-0.035:0,
         handMotion:value!=='reading',
         tint:value==='reading'?'#8cbaf0':value==='writing'?'#73d6a5':value==='command'?'#e6bc76':'#a8bfce'};
}
