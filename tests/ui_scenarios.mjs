// Production React bundle, real HTTP handlers, ephemeral database, synthetic agents only.
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import {createRequire} from 'node:module';
import {homedir} from 'node:os';
import {join} from 'node:path';
import {exerciseAdvisor} from './advisor_scenarios.mjs';

const require = createRequire(import.meta.url);
const {chromium} = require(process.env.WORKFLOW_PLAYWRIGHT_PATH || join(homedir(), '.cache/webprobe/runtime/node_modules/playwright'));
const f = JSON.parse(await fs.readFile(process.argv[2], 'utf8'));
const browser = await chromium.launch({args:['--enable-unsafe-swiftshader']});
const page = await browser.newPage({viewport:{width:1450,height:1000}, reducedMotion:'reduce'});
const requests = [], errors = [], external = [];
page.on('pageerror', e => errors.push(e.message));
page.on('request', req => {
  const url = new URL(req.url());
  if (url.origin !== f.url) external.push(url.origin);
  if (req.method() === 'POST') {
    assert.equal(req.headers()['x-workflow-token'], 'fixture-token');
    requests.push({path:url.pathname, data:req.postDataJSON()});
  }
});
const $ = id => page.locator('#' + id);
const button = name => page.getByRole('button',{name,exact:true});
const panel = () => $('task-management');
const until = async (fn, message) => {
  const end = Date.now()+25_000;
  while (Date.now()<end) {if (await fn()) return;await page.waitForTimeout(100);}
  throw Error('Timeout: '+message);
};
const state = async () => (await page.request.get(f.url+'/api/state?ledger='+f.primary)).json();
const task = async id => (await page.request.get(f.url+'/api/tasks/'+encodeURIComponent(id)+'?ledger='+f.primary)).json();
const jobs = async id => (await state()).jobs.filter(j=>j.task_id===id);
async function choose(id) {
  await $('task-search').fill(id);
  await page.locator('.task-item').filter({hasText:id}).click();
  await $('task-search').fill('');
}
async function openManagement() {
  if (!await panel().isVisible()) await button('작업·실행 관리').click();
  await panel().waitFor();
}
async function clickPost(name,path) {
  const got = page.waitForResponse(r=>new URL(r.url()).pathname===path && r.request().method()==='POST');
  await button(name).click();
  const response = await got;
  assert.ok(response.ok(), path+' failed: '+await response.text());
  return response.json();
}
async function screenshot(name) {
  if (!process.env.WORKFLOW_SCREENSHOT_DIR) return;
  await fs.mkdir(process.env.WORKFLOW_SCREENSHOT_DIR,{recursive:true});
  await page.screenshot({path:join(process.env.WORKFLOW_SCREENSHOT_DIR,name+'.png')});
}
async function ledgerDefault(path) {
  await page.goto(f.url+path);
  await button('새 작업 등록').waitFor();
  assert.equal(await $('new-task').isEnabled(),true);
  assert.equal(await $('ledger').inputValue(),f.primary,'new visits resolve to the writable primary');
  assert.equal(await page.locator('#root .mission-shell').count(),1);
  assert.equal(await page.locator('script[src="/static/app.js"]').count(),0);
}
try {
  const discovery = await (await page.request.get(f.url+'/api/connections')).json();
  assert.equal(discovery.ledgers[0].id,f.foreign,'fixture reproduces read-only discovery ordered ahead of primary');
  await ledgerDefault('/');
  await ledgerDefault('/mission');
  const prefs=page.locator('.console-preferences');
  const themeSwitch=page.getByRole('switch',{name:'다크 모드',exact:true});
  assert.equal(await themeSwitch.isChecked(),true,'dark default');
  await themeSwitch.focus(); await page.keyboard.press('Space');
  assert.equal(await themeSwitch.isChecked(),false);
  await prefs.locator('summary').click();
  await prefs.getByLabel('3D 공간 표시').uncheck();
  assert.ok(await prefs.getByLabel('움직임 줄이기').isDisabled());
  await page.reload();
  await until(async()=>await page.locator('html').getAttribute('data-theme')==='light','persistent theme');
  await prefs.locator('summary').click();
  assert.equal(await prefs.getByLabel('3D 공간 표시').isChecked(),false,'persistent list alternative');
  await prefs.locator('summary').click();
  await page.goto(f.url+'/sessions');
  assert.equal(await themeSwitch.isChecked(),false,'theme follows shared shell');
  await themeSwitch.click(); await page.goto(f.url+'/mission');
  assert.equal(await themeSwitch.isChecked(),true,'dark preference preserved');
  await page.locator('body').click({position:{x:1,y:1}});
  await page.keyboard.press('Control+k');
  await until(async()=>await $('task-search').evaluate(el=>el===document.activeElement),'keyboard search focus');
  await $('task-search').fill('not-a-task');
  await page.getByRole('button',{name:'검색 조건 초기화',exact:true}).click();

  await page.goto(f.url+'/mission?'+new URLSearchParams({ledger:f.foreign,task:'UI-FOREIGN'}));
  await until(async()=>await $('ledger').inputValue()===f.foreign,'explicit foreign ledger');
  await openManagement();
  assert.equal(await button('워크플로 실행').count(),0,'foreign ledger cannot execute');
  await choose('UI-RECOVERY');
  const resumePanel = panel().getByRole('region',{name:'작업 재개 정보',exact:true});
  const foreignDetail = await (await page.request.get(f.url+'/api/tasks/UI-RECOVERY?ledger='+f.foreign)).json();
  await resumePanel.getByRole('heading',{name:foreignDetail.resume_packet.goal,exact:true}).waitFor();
  assert.equal(foreignDetail.resume_packet.goal,'다른 원장의 같은 ID');
  assert.deepEqual(foreignDetail.resume_packet.valid_evidence,[]);
  await button('관리 원장에서 새 작업 등록').click();
  await $('create-profile').selectOption('fixture-project');
  assert.equal(await $('ledger').inputValue(),f.primary);
  assert.equal(requests.length,0,'switching the inspection source must not create a task');
  await page.keyboard.press('Escape');
  await $('create-dialog').waitFor({state:'hidden'});

  // Cover slow/delayed, malformed and partial model responses with test-only transport.
  await exerciseAdvisor(page);
  // Exercise both real server routes through the browser. Only model inference is synthetic.
  await $('new-task').click();
  await $('create-profile').selectOption('fixture-project');
  await $('create-criteria').fill('실습 미리보기');
  await page.locator('#advisor-options summary').click();
  await clickPost('보낼 내용 미리보기','/api/check-advisor/preview');
  const r = page.waitForResponse(res=>res.url().endsWith('/api/check-advisor/run'));
  await $('advisor-run').click();
  assert.equal((await r).status(),200);
  await $('advisor-apply').click();
  assert.deepEqual(await page.locator('#check-group-options input:checked').evaluateAll(xs=>xs.map(x=>x.value)),['docs']);
  assert.equal(await $('create-recommendation').inputValue(),'');
  await $('create-close').click();

  await $('new-task').click();
  await $('create-profile').selectOption('fixture-project');
  await $('recommendation-summary').click();
  await $('recent-recommendation').selectOption(f.receipt);
  assert.equal(await $('create-recommendation').inputValue(),f.receipt);
  assert.equal(await page.locator('#check-group-options input[value="ui"]').isChecked(),true);
  // An old ID may still be loaded directly, including after clearing the recent picker.
  await $('recent-recommendation').selectOption('');
  let startOld, finishOld, endOld;
  const began = new Promise(resolve => { startOld=resolve; });
  const pendingOld = new Promise(resolve => { finishOld=resolve; });
  const settled = new Promise(resolve => { endOld=resolve; });
  const oldPattern='**/api/recommendations/REC-expired*';
  await page.route(oldPattern, async route => {
    startOld();
    await pendingOld;
    await route.fulfill({status:400,json:{error:'stale ID response'}}).catch(()=>{});
    endOld();
  });
  await $('create-recommendation').fill('REC-expired');
  await Promise.all([began,$('load-recommendation').click()]);
  await $('create-recommendation').fill(f.receipt);
  await $('load-recommendation').click();
  await $('recommendation-hint').waitFor();
  finishOld();await settled;await page.unroute(oldPattern);
  assert.equal(await $('create-recommendation').inputValue(),f.receipt);
  assert.equal(await page.getByText('stale ID response',{exact:true}).count(),0);
  const literal = '<img src=x onerror="window.injected=true"> 표시 결과';
  await $('create-goal').fill(literal);
  await $('create-criteria').fill(f.criteria.join('\n'));
  await $('create-id').fill('UI-NEW');
  await $('create-agent-name').fill('검증 담당');
  await $('create-reviewer-name').fill('독립 리뷰');
  const created = (await clickPost('작업 등록','/api/tasks')).task;
  assert.equal(created.goal,literal);
  assert.ok(created.workspace.managed);
  assert.deepEqual(created.profile.check_groups,['ui']);
  assert.equal(created.profile.recommendation_id,f.receipt);
  assert.equal(created.agent_names.implement,'검증 담당');
  await $('create-dialog').waitFor({state:'hidden'});
  await openManagement();
  await panel().getByText('작업 계약과 검사 결과',{exact:true}).click();
  const originalCriterion = panel().locator('#resume-criterion-1');
  await originalCriterion.waitFor();
  assert.equal(await originalCriterion.textContent(), f.criteria[0]);
  assert.equal(await page.evaluate(()=>typeof window.injected),'undefined');
  const recommendation = panel().locator('details').filter({has:page.getByText('검사 추천과 실제 작업 결과',{exact:true})});
  await recommendation.locator('summary').first().click();
  const expected = recommendation.getByRole('group',{name:'실제로 필요했던 검사 묶음'}).getByRole('checkbox',{name:'ui',exact:true});
  await expected.check();
  await page.getByRole('button',{name:'기록 새로고침'}).click();
  assert.equal(await expected.isChecked(),true,'polling or refresh retains feedback draft');
  await clickPost('필요 묶음 저장','/api/recommendations/feedback');
  assert.deepEqual((await task('UI-NEW')).recommendation.expected_groups,['ui']);
  await screenshot('react-managed-task');
  console.log('PASS both entry points, primary default, foreign switch, CSRF, real saved and unrecorded recommendations, literal rendering and optional feedback');

  await button('작업 보류').click();
  await page.getByLabel('보류 이유').fill('운영 확인');
  await clickPost('보류 적용','/api/hold');
  assert.equal((await task('UI-NEW')).task.hold.reason,'운영 확인');
  await clickPost('보류 해제','/api/unhold');
  await panel().getByText('실행 옵션',{exact:true}).click();
  const timeout = page.getByLabel('프로세스 시간 제한(초)');
  await timeout.fill('0');
  const before = requests.length;
  await button('워크플로 실행').click();
  assert.equal(requests.length,before);
  assert.equal(await timeout.evaluate(i=>i.checkValidity()),false);
  await timeout.fill('60');await page.getByLabel('추가 수정 횟수').fill('0');await page.getByLabel('오류 복구 횟수').fill('0');
  const queued=(await clickPost('워크플로 실행','/api/queue')).job;
  assert.equal(queued.timeout,60);
  assert.equal(queued.repairs,0);
  await clickPost('UI-NEW 대기 취소','/api/cancel');
  const writesBeforeDiagnosis = requests.length;
  await choose('UI-RECOVERY'); await openManagement();
  const realDetail = await task('UI-RECOVERY');
  const recovery = realDetail.recovery;
  assert.equal(recovery.stage,'checks');
  assert.equal(recovery.action,'review');
  assert.ok(recovery.confirmed_effects.length);
  const recoveryPanel = panel().getByRole('region',{name:'중단 복구'});
  async function assertRecovery(expected, stageLabel, sharedNext = false) {
    const summary = recoveryPanel.getByText('중단 복구 · '+stageLabel,{exact:true});
    await summary.waitFor();
    if(!await summary.evaluate(node=>node.parentElement.open)) await summary.click();
    await recoveryPanel.getByText(expected.reason,{exact:true}).waitFor();
    await (sharedNext ? resumePanel : recoveryPanel).getByText(expected.next_action,{exact:true}).waitFor();
    if(sharedNext) assert.equal(await recoveryPanel.getByText(expected.next_action,{exact:true}).count(),0);
    await until(async()=>JSON.stringify(await recoveryPanel.getByRole('listitem').allTextContents())
      === JSON.stringify(expected.confirmed_effects),'all confirmed effects from the selected diagnosis');
    assert.equal(await recoveryPanel.getByRole('button').count(),0);
  }
  const packet = realDetail.resume_packet;
  assert.equal(packet.task_id,'UI-RECOVERY');
  assert.notEqual(packet.goal,foreignDetail.resume_packet.goal);
  await resumePanel.getByRole('heading',{name:packet.goal,exact:true}).waitFor();
  await resumePanel.getByRole('heading',{name:'남은 조건 · '+packet.remaining_criteria.length+'개'}).waitFor();
  for (const row of packet.remaining_criteria) assert.ok((await resumePanel.innerText()).includes(row.text));
  const firstCriterion = packet.remaining_criteria[0];
  await resumePanel.getByRole('link',{name:firstCriterion.text,exact:true}).click();
  await until(async()=>page.locator('#resume-criterion-'+firstCriterion.id).evaluate(node =>
    node === document.activeElement && node.checkVisibility()),'opens source acceptance criterion');
  await assertRecovery(recovery,'검사',true);
  const receipt = packet.valid_evidence.find(row=>row.kind==='verification');
  assert.ok(receipt);
  await resumePanel.getByText(receipt.description,{exact:true}).click();
  const record = receipt.source.substring(1).split('/').reduce((value,key)=>value[key],realDetail.task);
  await until(async()=>JSON.stringify(JSON.parse(await resumePanel.getByLabel('근거 원문').textContent()))===JSON.stringify(record),
    'opens the actual selected-ledger source receipt');
  for(const message of packet.uncertainties) await resumePanel.getByText(message,{exact:true}).waitFor();
  assert.equal(requests.length,writesBeforeDiagnosis);
  assert.deepEqual(await jobs('UI-RECOVERY'),[]);
  console.log('PASS recovery diagnosis shows recorded check effects and next review without enqueueing work');
  // Older recovery receipts can still be displayed when the source has no common resume packet.
  const pilot = JSON.parse(await fs.readFile(new URL('../pilots/p1-recovery/results.json',import.meta.url),'utf8'));
  const views = [
    [pilot.implementation.diagnosis,'구현'],
    [pilot.before_review.diagnosis,'검사'],
    [pilot.github.after_create_response_loss,'PR 생성'],
    [pilot.github.after_merge_response_loss,'병합'],
    [pilot.github.after_repeated_resume,'병합'],
    [pilot.deployment_with_version.after_acknowledgement_loss,'배포'],
    [pilot.deployment_with_version.different_observed_revision,'배포'],
    [pilot.deployment_with_version.after_repeated_resume,'배포'],
    [pilot.deployment_without_version.after_repeated_resume,'배포'],
  ];
  const taskRoute = url => url.pathname === '/api/tasks/UI-RECOVERY';
  let projection;
  await page.route(taskRoute, route=>route.fulfill({json:{...realDetail,resume_packet:null,recovery:projection}}));
  try {
    for (const [snapshot, stageLabel] of views) {
      projection = snapshot;
      await button('기록 새로고침').click();
      await assertRecovery(snapshot,stageLabel);
    }
  } finally {
    await page.unroute(taskRoute);
  }
  assert.equal(requests.length,writesBeforeDiagnosis);
  assert.deepEqual(await jobs('UI-RECOVERY'),[]);
  console.log('PASS read-only UI projection of nine measured local, GitHub, and deployment recovery states');
  await choose('UI-HEALTH'); await openManagement();
  await panel().getByText('원장에 기록된 릴리스 결과',{exact:true}).click();
  const phases=panel().locator('details').filter({has:page.getByText('원장에 기록된 릴리스 결과',{exact:true})});
  assert.equal(await phases.getByText('배포 적용',{exact:true}).locator('..').locator('dd').textContent(),'적용됨');
  assert.equal(await phases.getByText('배포 후 상태 검사',{exact:true}).locator('..').locator('dd').textContent(),'실패');
  await choose('OBS-TRACE'); await openManagement();
  const releaseJob=(await clickPost('통합 · 배포 실행','/api/queue')).job;
  assert.equal(releaseJob.action,'release');
  await clickPost('OBS-TRACE 대기 취소','/api/cancel');
  await clickPost('릴리스 설정 해제','/api/release-clear');
  assert.equal((await task('OBS-TRACE')).task.release,null);
  await choose('UI-NEW'); await openManagement();
  await clickPost('UI-NEW 실행 재개','/api/resume');
  await fs.writeFile(f.start_worker,'start fixture worker');
  await until(async()=>(await jobs('UI-NEW')).some(j=>j.status==='running' && j.log_path),'real worker starts');
  await clickPost('UI-NEW 실행 중단','/api/stop');
  await until(async()=>(await jobs('UI-NEW')).filter(j=>j.status==='cancelled').length>=2,'worker stopped');
  // Hold a live implementation at its actual subprocess boundary.
  await choose('OBS-LIVE'); await openManagement();
  await clickPost('워크플로 실행','/api/queue');
  const traceURL=f.url+'/api/observability?ledger='+f.primary+'&task_id=OBS-LIVE';
  await until(async()=>(await (await page.request.get(traceURL)).json()).spans.some(s=>s.role==='implement'&&s.status==='running'),'active implementation span');
  await button('작업 보류').click();
  await page.getByLabel('보류 이유').fill('다음 단계 중단 확인');
  await clickPost('보류 적용','/api/hold');
  await fs.writeFile(f.signal_agent,'release fixture model');
  await until(async()=>(await jobs('OBS-LIVE')).some(j=>j.status==='failed'),'hold stops the next stage');
  const heldTrace=await (await page.request.get(traceURL)).json();
  assert.equal(heldTrace.spans.some(span=>span.role==='check'||span.role==='review'),false);
  assert.equal(heldTrace.task.hold.reason,'다음 단계 중단 확인');
  await clickPost('보류 해제','/api/unhold');
  await clickPost('OBS-LIVE 실행 재개','/api/resume');
  await until(async()=>(await task('OBS-LIVE')).task.status==='completed','unheld job resumes');
  await choose('UI-NEW'); await openManagement();
  // Reissue from the cancelled row; saved options and the original task are retained.
  const resumedResponse=page.waitForResponse(r=>r.url().endsWith('/api/resume')&&r.request().method()==='POST');
  await button('UI-NEW 실행 재개').first().click();
  assert.ok((await resumedResponse).ok());
  await until(async()=>(await task('UI-NEW')).task.status==='completed','workflow completes');
  await until(async()=>(await jobs('UI-NEW')).some(j=>j.status==='succeeded'),'job succeeds');
  const detail=await task('UI-NEW');
  assert.equal(detail.freshness.completion_is_current,true);
  assert.equal(detail.recommendation.checks_by_group.ui.passed_count,1);
  // Wait for the completed row to refresh before opening its real job log.
  await page.getByRole('button',{name:'기록 새로고침'}).click();
  const finishedLog = panel().locator('.mg-jobs li').filter({hasText:/UI-NEW.*완료/s}).locator('.mg-job-log').first();
  await finishedLog.locator('summary').click();
  await finishedLog.locator('pre').filter({hasText:/check 1\/2|구현|review/i}).waitFor();
  console.log('PASS durable hold/unhold, invalid execution options, cancel, real stop/resume, applied checks, completed ledger and job log');

  await choose('OBS-TRACE');
  await openManagement();
  await fs.rename(f.project,f.project+'-moved');
  try {
    await page.getByRole('button',{name:'기록 새로고침'}).click();
    await panel().getByText(/작업 폴더 상태 확인 불가/).waitFor();
  } finally { await fs.rename(f.project+'-moved',f.project); }
  await page.getByRole('button',{name:'기록 새로고침'}).click();
  await panel().getByText(/작업 폴더 상태 확인 불가/).waitFor({state:'hidden'});

  await $('new-task').click();
  await $('create-profile').selectOption('project-ui-goal');
  await page.getByLabel('프로젝트 목표 연속 실행').check();
  assert.equal(await page.getByLabel('종목 (선택)').count(),0);
  assert.equal(await $('create-isolate').isDisabled(),true);
  await $('create-goal').fill('프로젝트 지침에 따른 로컬 값 검사');
  await $('create-id').fill('AW-GENERIC-UI');
  const projectGoal=(await clickPost('작업 등록','/api/goals')).task;
  assert.equal(projectGoal.goal_mode.phase,'planning');
  assert.equal(projectGoal.katakuri_goal,undefined);
  await openManagement();
  const projectPanel=page.getByRole('region',{name:'프로젝트 목표 진행'});
  await projectPanel.getByText('누적 예산 조정').click();
  await projectPanel.getByLabel('총 모델 호출 수').fill('18');
  await clickPost('예산 저장','/api/goals/budget');
  assert.equal((await task('AW-GENERIC-UI')).goal_mode.max_agent_calls,18);
  assert.equal((await jobs('AW-GENERIC-UI')).length,0);
  console.log('PASS project goals: registration without Katakuri documents, domain fields or implicit execution');

  await $('new-task').click();
  await $('create-profile').selectOption('kb-ui');
  await page.getByLabel('Katakuri 목표 연속 실행').check();
  assert.equal(await $('create-isolate').isDisabled(),true);
  await $('create-goal').fill('오프라인 CLI 회귀 검증');
  await $('create-id').fill('KG-UI');
  const goal = (await clickPost('작업 등록','/api/katakuri-goals')).task;
  assert.equal(goal.katakuri_goal.phase,'planning');
  await openManagement();
  const goalPanel = page.getByRole('region',{name:'Katakuri 목표 진행'});
  await goalPanel.getByText('누적 예산 조정').click();
  await goalPanel.getByLabel('총 모델 호출 수').fill('30');
  await clickPost('예산 저장','/api/katakuri-goals/budget');
  assert.equal((await task('KG-UI')).katakuri_goal.max_agent_calls,30);
  assert.equal((await jobs('KG-UI')).length,0);
  await choose('KG-INPUT');
  await openManagement();
  await page.getByLabel('제출할 근거').fill('fixture-only observation for this criterion');
  await clickPost('근거 저장','/api/katakuri-goals/input');
  const supplied = (await task('KG-INPUT')).katakuri_goal;
  assert.equal(supplied.criteria[0].state,'pending');
  assert.equal(supplied.agent_calls,0);
  console.log('PASS goal-only creation, progress, budget editing and supplying new evidence');
  await choose('KG-PROOF');
  await openManagement();
  const proof = await task('KG-PROOF');
  const proofRows = proof.katakuri_goal.criteria;
  assert.deepEqual(proofRows.map(row=>row.check_evidence.state), ['passed','skipped']);
  const passedReceipt = proof.resume_packet.valid_evidence.find(row=>row.result_id===proofRows[0].check_evidence.result_id && row.kind==='criterion_check');
  assert.ok(passedReceipt);
  await resumePanel.getByText(passedReceipt.description,{exact:true}).waitFor();
  for (const [i, statusText] of ['통과','건너뜀'].entries()) {
    const ev = proofRows[i].check_evidence;
    const summary = goalPanel.getByText('조건 검사 · '+statusText,{exact:true});
    await summary.click();
    const details = summary.locator('..');
    await details.getByText(ev.testcase,{exact:true}).waitFor();
    await details.getByText(ev.report_sha256,{exact:true}).waitFor();
    await details.getByText(ev.fingerprint,{exact:true}).waitFor();
    const original = details.getByRole('link',{name:'원본 검사 결과 보기'});
    await original.click();
    const receipt = page.locator('[id="goal-check-'+ev.result_id+'"]');
    await receipt.waitFor({state:'visible'});
    assert.equal(await receipt.evaluate(node=>node===document.activeElement), true);
    assert.ok((await receipt.innerText()).includes('종료 코드 0'));
    assert.ok((await receipt.innerText()).includes(ev.result_id));
  }
  await fs.appendFile(join(f.proof_project,'app.py'),'\n# changed after checks\n');
  await page.getByRole('button',{name:'기록 새로고침'}).click();
  await goalPanel.getByText('조건 검사 · 통과 · 재검증 필요',{exact:true}).waitFor();
  const stalePacket = (await task('KG-PROOF')).resume_packet;
  assert.equal(stalePacket.valid_evidence.some(item=>item.result_id===passedReceipt.result_id),false);
  assert.ok(stalePacket.uncertainties.length);
  for(const message of stalePacket.uncertainties) await resumePanel.getByText(message,{exact:true}).waitFor();
  assert.equal(await resumePanel.getByText(passedReceipt.description,{exact:true}).count(),0);
  console.log('PASS same-ID ledger identity, receipt source inspection and stale evidence removal in resume packet');
  console.log('PASS criterion-specific pass and skip, linked original result, stale receipt after worktree change');
  await choose('OBS-TRACE');
  for(const width of [375,768,1440]){
    await page.setViewportSize({width,height:heightFor(width)});
    if(width===375){
      await button('작업 탐색').click();
      const search=$('task-search');await search.fill('OBS-TRACE');
      await page.locator('.task-item').filter({hasText:'OBS-TRACE'}).click();
      assert.equal(await button('작업 탐색').getAttribute('aria-expanded'),'false');
    }
    await $('new-task').click();
    await $('create-profile').selectOption('fixture-project');
    const dialog=$('create-dialog');
    assert.equal(await dialog.evaluate(d=>d.scrollWidth<=d.clientWidth+1),true,'dialog overflows '+width);
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),true);
    await screenshot('react-create-'+width);
    await page.keyboard.press('Escape');
    await dialog.waitFor({state:'hidden'});
  }
  assert.equal(requests.some(r=>r.path==='/api/tasks' && r.data.id!=='UI-NEW'),false);
  assert.deepEqual(errors,[]);assert.deepEqual(external,[]);
  console.log('PASS release controls, responsive registration, no external assets and zero browser exceptions');
} catch(error){
  console.error('React management debug:',errors, (await page.locator('body').innerText()).slice(-3000));
  await screenshot('react-management-failure');
  throw error;
} finally{await browser.close()}
function heightFor(width){return width===768?390:860}
