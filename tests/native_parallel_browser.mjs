// Opt-in verification of real host evidence. No models, queue, or synthetic events.
import assert from 'node:assert/strict';
import {readFile, writeFile, mkdir} from 'node:fs/promises';
import {createRequire} from 'node:module';
import {join, resolve} from 'node:path';
const evidence=JSON.parse(await readFile(process.argv[2],'utf8'));
const base=process.argv[3],out=resolve(process.argv[4]);
assert(evidence.passed&&evidence.providers.length===3);
await mkdir(out,{recursive:true});
const {chromium}=createRequire(import.meta.url)(process.env.WORKFLOW_PLAYWRIGHT_PATH||'/home/roqkf/.cache/webprobe/runtime/node_modules/playwright');
const browser=await chromium.launch({headless:true,args:['--enable-unsafe-swiftshader']});
const errors=[],writes=[],results=[];
try{
 const page=await browser.newPage({viewport:{width:1440,height:1000},reducedMotion:'reduce'});
 page.on('pageerror',e=>errors.push(e.message));
 page.on('request',r=>{if(r.method()!=='GET')writes.push(new URL(r.url()).pathname)});
 for(const item of evidence.providers){
  for(const child of [false,true]){
   const id=child?item.child_observed_id:item.observed_id;
   const native=child?item.child_id:item.native_id;
   const response=await page.goto(base+'/sessions?session='+encodeURIComponent(id));
   assert.equal(response.status(),200);
   await page.getByRole('checkbox',{name:'종료 이력 포함'}).check();
   const detail=page.locator('.native-detail');await detail.waitFor();
   await page.waitForFunction(native=>document.querySelector('.native-detail')?.textContent.includes(native),native);
   if(child)assert((await detail.textContent()).includes('부모 세션: '+item.native_id));
   await page.locator('.native-history li').first().waitFor();
   const history=await page.locator('.native-history').innerText();
   assert(history.includes(item.provider==='opencode'?'session.execution.succeeded':'SessionEnd')||child&&history.includes('SubagentStop'));
   const toggle=page.getByRole('button',{name:'목록 보기',exact:true});
   if(await toggle.isVisible())await toggle.click();
   assert.equal(await page.locator('canvas').count(),0);
   await page.getByRole('button',{name:'3D 보기',exact:true}).click();
   await page.locator('canvas').waitFor();
   await page.screenshot({path:join(out,item.provider+(child?'-child':'-parent')+'.png'),fullPage:true});
   results.push({provider:item.provider,nativeId:native,child,selection:true,history:true,room:true,listFallback:true});
  }
 }
 assert.deepEqual(errors,[]);assert.deepEqual(writes,[]);
 await writeFile(join(out,'browser.json'),JSON.stringify({passed:true,results,errors,writes},null,2));
 console.log(JSON.stringify({passed:true,selections:results.length,errors,writes}));
}finally{await browser.close()}
