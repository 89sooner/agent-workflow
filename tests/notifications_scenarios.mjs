import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import {createRequire} from 'node:module';
import {homedir} from 'node:os';
import {join} from 'node:path';
const {chromium}=createRequire(import.meta.url)(process.env.WORKFLOW_PLAYWRIGHT_PATH||join(homedir(),'.cache/webprobe/runtime/node_modules/playwright'));
const f=JSON.parse(await fs.readFile(process.argv[2],'utf8'));
const browser=await chromium.launch({headless:true,args:['--enable-unsafe-swiftshader']});
const context=await browser.newContext({reducedMotion:'reduce'}),calls=[];
try{
 await context.exposeBinding('__recordNotification',(source,value)=>calls.push({page:source.page,...value}));
 await context.addInitScript(()=>{
  window.__permission='granted';
  window.__lockAttempts=0;
  const original=navigator.locks.request.bind(navigator.locks);
  navigator.locks.request=(...args)=>{window.__lockAttempts++;return original(...args)};
  Object.defineProperty(document,'hidden',{configurable:true,get:()=>window.__testHidden??false});
  window.Notification=class {
   static get permission(){return window.__permission}
   static async requestPermission(){return window.__permission}
   constructor(title,opts){window.__lastNotification=this;window.__recordNotification({title,...opts})}
   close(){}
  };
 });
 const a=await context.newPage(),b=await context.newPage();
 for(const p of [a,b]){await p.goto(f.url+'/sessions');await p.waitForSelector('.native-session')}
 await a.evaluate(()=>window.__permission='denied');
 await a.getByRole('button',{name:'알림 켜기',exact:true}).click();
 await a.getByText('알림 권한이 허용되지 않았습니다.',{exact:false}).waitFor();
 await a.evaluate(()=>window.__permission='granted');
 await a.getByRole('button',{name:'알림 켜기',exact:true}).click();
 await b.getByRole('button',{name:'알림 끄기',exact:true}).waitFor();
 for(const p of [a,b])await p.evaluate(()=>{window.__testHidden=true;document.dispatchEvent(new Event('visibilitychange'))});
 assert.equal(calls.length,0,'initial history must not notify');
 // Reproduce the control-file create/write gap under contention.
 await fs.writeFile(f.control,'');
 await a.waitForTimeout(150);
 await fs.writeFile(f.control+'.next','PermissionRequest');
 await fs.rename(f.control+'.next',f.control);
 const until=Date.now()+20000;while(!calls.length&&Date.now()<until)await a.waitForTimeout(100);
 assert.equal(calls.length,1,'one notification across two tabs');
 await a.waitForTimeout(5000);assert.equal(calls.length,1,'polling cannot repeat notification');
 assert.equal(calls[0].title,'승인 요청');
 for(const p of [a,b])assert(await p.evaluate(()=>window.__lockAttempts>0),'both tabs attempted actual lock');
 await a.getByRole('button',{name:'알림 끄기',exact:true}).click();
 await b.getByRole('button',{name:'알림 켜기',exact:true}).waitFor();
 await fs.writeFile(f.control+'.next','Stop');
 await fs.rename(f.control+'.next',f.control);
 await a.waitForTimeout(6000);assert.equal(calls.length,1,'opt-out applies across tabs');
 await calls[0].page.evaluate(()=>window.__lastNotification.onclick());
 await calls[0].page.waitForURL('**/sessions?session=**');
 await calls[0].page.waitForSelector('.native-detail');
 assert((await calls[0].page.locator('.native-detail').innerText()).includes('claude-pilot'));
 console.log(JSON.stringify({passed:true,checks:['permission denied fallback','explicit opt-in','initial history suppressed','real Web Locks multi-tab dedupe','repeat polling suppressed','hidden-tab polling','cross-tab opt-out','notification click session navigation'],notificationAPI:'instrumented constructor; actual OS popup not tested'}));
}finally{await browser.close()}
