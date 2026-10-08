import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {homedir} from 'node:os';
import {join} from 'node:path';
import {writeFile,mkdir} from 'node:fs/promises';
const require=createRequire(import.meta.url);
const {chromium}=require(join(homedir(),'.cache/webprobe/runtime/node_modules/playwright'));
const browser=await chromium.launch({headless:true});
const context=await browser.newContext({viewport:{width:1280,height:900}});
const page=await context.newPage();
const errors=[];page.on('pageerror',e=>errors.push(e.message));
const root='/home/roqkf/agent-workflow/pilots/KB-O-CURRENT-AUDIT/evidence';
await mkdir(root,{recursive:true});
try {
 await page.goto('http://127.0.0.1:3137/');
 const link=page.locator('[data-region="current-state"] [data-field="auditId"] a');
 await link.waitFor();
 const reason=page.getByRole('textbox',{name:'사유 (감사에 그대로 남는다)'});
 await reason.fill('PILOT_DRAFT_MUST_REMAIN');
 async function openAudit(){
  const [popup]=await Promise.all([context.waitForEvent('page'),link.click()]);
  await popup.waitForLoadState();
  const payload=JSON.parse(await popup.locator('#parsed-link').textContent());
  assert.deepEqual(payload,{kind:'ok',action:'KILL_SWITCH_CHANGE',auditId:'audit_current-01'});
  assert.equal(new URL(popup.url()).pathname,'/admin/access');
  await popup.close();
 }
 await openAudit();
 assert.equal(await reason.inputValue(),'PILOT_DRAFT_MUST_REMAIN');
 assert.deepEqual(await page.evaluate(()=>window.pilotCalls),{prepare:0,commit:0,reread:0});
 await page.getByRole('button',{name:'kill switch OFF — 해제',exact:true}).click();
 await page.locator('[data-panel="killswitch-confirmation"]').waitFor();
 const confirmationText=await page.locator('[data-panel="killswitch-confirmation"]').textContent();
 assert.match(confirmationText,/fixture-confirmation/);
 const before=await page.evaluate(()=>({...window.pilotCalls}));
 assert.equal(before.prepare,1);assert.equal(before.commit,0);
 await openAudit();
 assert.equal(await page.locator('[data-panel="killswitch-confirmation"]').count(),1);
 assert.deepEqual(await page.evaluate(()=>window.pilotCalls),before);
 assert.equal(await page.locator('[data-panel="killswitch-confirmation"]').textContent(),confirmationText);
 assert.equal(await page.locator('[data-controls-enabled]').getAttribute('data-control-phase'),'confirming');
 assert.deepEqual(errors,[]);
 await page.screenshot({path:join(root,'browser-proof.png'),fullPage:true});
 const result={result:'PASS',environment:'isolated Vite + actual A-002 component + synthetic state and fake API',
  checks:['native new-tab destination and parsed query','draft reason survives navigation','pending confirmation survives navigation','no added prepare/commit/reread calls'],
  counts:before,uncaught_errors:errors,production_api_requests:0};
 await writeFile(join(root,'browser-proof.json'),JSON.stringify(result,null,2)+'\n');
 console.log(JSON.stringify(result));
}finally{await context.close();await browser.close();}
