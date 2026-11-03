import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import {createRequire} from 'node:module';
import {homedir} from 'node:os';
import {join} from 'node:path';
const require = createRequire(import.meta.url);
const {chromium} = require(process.env.WORKFLOW_PLAYWRIGHT_PATH || join(homedir(), '.cache/webprobe/runtime/node_modules/playwright'));
const f = JSON.parse(await fs.readFile(process.argv[2], 'utf8'));
const browser = await chromium.launch({headless:true, args:['--enable-unsafe-swiftshader']});
const page = await browser.newPage({viewport:{width:1440,height:1000}});
const errors=[], mutations=[];
page.on('pageerror', e=>errors.push(e.message));
page.on('request', r=>{if(r.method()!=='GET') mutations.push(r.url());});
const until = async (fn, timeout=20000)=>{
  const end=Date.now()+timeout;
  while(Date.now()<end){if(await fn())return; await page.waitForTimeout(100);}
  throw Error('OpenCode browser state not observed');
};
const screenshot=async name=>{
  if(!process.env.WORKFLOW_SCREENSHOT_DIR)return;
  await fs.mkdir(process.env.WORKFLOW_SCREENSHOT_DIR,{recursive:true});
  await page.screenshot({path:join(process.env.WORKFLOW_SCREENSHOT_DIR,name+'.png'),fullPage:true});
};
try{
  await page.goto(f.url+'/mission');
  await page.locator(`#ledger option[value="${f.source.id}"]`).waitFor({state:'attached'});
  await page.locator('#ledger').selectOption(f.source.id);
  await page.locator('.task-item').filter({hasText:'한글 세션'}).click();
  await until(async()=>!!await page.locator('.graph-node').filter({hasText:'실제 구현 담당'}).count());
  await page.locator('.graph-node').filter({hasText:'실제 구현 담당'}).click();
  await until(async()=>(await page.locator('.inspector').innerText()).includes('provider/model'));
  assert.match(await page.locator('.inspector').innerText(),/OpenCode API에 기록된 이름/);
  assert.match(await page.locator('.inspector').innerText(),/ses_one/);
  assert.match(await page.locator('.actor-list').innerText(),/OpenCode 기록 이름/);
  assert.match(await page.locator('.run-list').innerText(),/OpenCode 응답/);
  assert.match(await page.locator('.workspace-footer').innerText(),/OpenCode 상태 API/);
  assert.equal(await page.locator('.sidebar-bottom a').count(),0);
  assert.equal(await page.getByRole('button',{name:'▶ 기록 재생',exact:true}).isDisabled(),true);
  assert.ok(!(await page.locator('body').innerText()).includes('SECRET_'));
  await screenshot('opencode-fixture-desktop');
  await fs.writeFile(f.control,'fail');
  await until(async()=>!!await page.locator('.notice.error').count());
  assert.equal(await page.locator('.mission-shell').getAttribute('data-mode'),'paused');
  await fs.writeFile(f.control,'recover');
  await until(async()=>!await page.locator('.notice.error').count(),30000);
  await page.setViewportSize({width:390,height:844});
  await screenshot('opencode-fixture-mobile');
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
  if(f.native){
    await page.setViewportSize({width:1440,height:1000});
    await page.locator('#ledger').selectOption(f.native.id);
    await until(async()=>!!await page.locator('.task-item').count());
    // Explicit fixture creation on the isolated native test server, outside the observed browser.
    // No prompt/model APIs are called. The connector itself remains GET-only.
    const title='자동 발견 검증 '+Date.now();
    const response=await fetch(f.native.url+'/session?'+new URLSearchParams({directory:f.native.directory}),{
      method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({title})});
    assert.equal(response.ok,true);
    const session=await response.json();
    await until(async()=>!!await page.locator('.task-item').filter({hasText:title}).count());
    await page.locator('.task-item').filter({hasText:title}).click();
    await until(async()=>(await page.locator('.page-heading').innerText()).includes(session.id));
    await screenshot('opencode-native-discovered');
    console.log('Native OpenCode new-session discovery:',session.id);
  }
  assert.deepEqual(errors,[]);
  assert.deepEqual(mutations,[]);
  console.log('OpenCode browser: native discovery, identity/model, tool graph, failure/recovery, mobile, GET-only passed.');
}finally{await browser.close();}
