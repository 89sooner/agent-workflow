// Read-only browser health for an isolated recovery rehearsal.
import assert from 'node:assert/strict';
import {readFile,writeFile,mkdir} from 'node:fs/promises';
import {join} from 'node:path';
import {createRequire} from 'node:module';
const manifest=JSON.parse(await readFile(process.argv[2],'utf8'));
const phase=process.argv[3];
assert.equal(new URL(manifest.url).hostname,'127.0.0.1');
const {chromium}=createRequire(import.meta.url)(process.env.WORKFLOW_PLAYWRIGHT_PATH||'/home/roqkf/.cache/webprobe/runtime/node_modules/playwright');
const browser=await chromium.launch({headless:true,args:['--enable-unsafe-swiftshader']});
const errors=[],writes=[];
const out=join(manifest.output,phase);await mkdir(out,{recursive:true});
try{
 const page=await browser.newPage({viewport:{width:1440,height:1000},reducedMotion:'reduce'});
 page.on('pageerror',e=>errors.push(e.message));
 page.on('request',r=>{if(r.method()!=='GET')writes.push(new URL(r.url()).pathname)});
 await page.goto(manifest.url+'/sessions?session='+manifest.session);
 if(phase==='asset-failure'){
  await page.waitForTimeout(500);
  assert(errors.length>0,'The injected invalid asset must fail browser health.');
  await writeFile(join(out,'browser.json'),JSON.stringify({healthy:false,detected:true,errors,writes},null,2));
  console.log(JSON.stringify({healthy:false,detected:true,errors}));
 }else{
  await page.getByRole('checkbox',{name:'종료 이력 포함'}).check();
  await page.waitForFunction(id=>document.querySelector('.native-detail')?.textContent.includes(id),manifest.nativeId);
  assert(await page.locator('.native-history li').count()>0);
  await page.screenshot({path:join(out,'session.png'),fullPage:true});
  const response=await page.goto(manifest.url+manifest.taskUrl);assert.equal(response.status(),200);
  await page.locator('.page-heading code').filter({hasText:manifest.taskId}).waitFor();
  await page.locator('.graph-node').first().waitFor();
  await page.screenshot({path:join(out,'task.png'),fullPage:true});
  assert.deepEqual(errors,[]);assert.deepEqual(writes,[]);
  await writeFile(join(out,'browser.json'),JSON.stringify({healthy:true,session:manifest.nativeId,task:manifest.taskId,errors,writes},null,2));
  console.log(JSON.stringify({healthy:true,session:manifest.nativeId,task:manifest.taskId,errors,writes}));
 }
}finally{await browser.close()}
