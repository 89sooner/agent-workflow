import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import {performance} from 'node:perf_hooks';
import {createRequire} from 'node:module';
import {homedir} from 'node:os';
import {join} from 'node:path';
const {chromium}=createRequire(import.meta.url)(process.env.WORKFLOW_PLAYWRIGHT_PATH||join(homedir(),'.cache/webprobe/runtime/node_modules/playwright'));
const f=JSON.parse(await fs.readFile(process.argv[2],'utf8'));
const browser=await chromium.launch({headless:true,args:['--enable-unsafe-swiftshader']});
try{
 const page=await browser.newPage({viewport:{width:1440,height:1000},reducedMotion:'reduce'});
 await page.goto(f.url+'/sessions');await page.locator('.native-session').filter({hasText:'Claude'}).waitFor();
 const durations=[];
 for(let i=0;i<100;i++){
  const [event,label]=i%2?['PermissionRequest','승인 대기']:['UserPromptSubmit','작업 중'];
  const start=performance.now();await fs.writeFile(f.control,event);
  await page.waitForFunction(label=>[...document.querySelectorAll('.native-session')].some(e=>e.textContent.includes('Claude')&&e.textContent.includes(label)),label,{timeout:10000});
  durations.push(performance.now()-start);if((i+1)%10===0)console.log('observed transitions',i+1);
 }
 const sorted=[...durations].sort((a,b)=>a-b);const report={passed:sorted[94]<=5000,transitions:100,p50_ms:sorted[49],p95_ms:sorted[94],max_ms:sorted.at(-1),target_ms:5000,scope:'actual local hook subprocess to foreground Chromium DOM; reduced motion; synthetic events; no models'};
 await fs.writeFile('/tmp/observatory-latency-100.json',JSON.stringify(report,null,2));console.log(JSON.stringify(report));assert(report.passed);
}finally{await browser.close()}
