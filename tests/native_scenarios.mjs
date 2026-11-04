import assert from "node:assert/strict";
import fs from "node:fs/promises";
import {createRequire} from "node:module";
import {homedir} from "node:os";
import {join} from "node:path";
const require=createRequire(import.meta.url);
const {chromium}=require(process.env.WORKFLOW_PLAYWRIGHT_PATH||join(homedir(),".cache/webprobe/runtime/node_modules/playwright"));
const f=JSON.parse(await fs.readFile(process.argv[2],"utf8"));
const browser=await chromium.launch({headless:true,args:["--enable-unsafe-swiftshader"]});
const out=process.env.WORKFLOW_SCREENSHOT_DIR||"/tmp/native-pilot-browser";
await fs.mkdir(out,{recursive:true});
try{
 const page=await browser.newPage({viewport:{width:1440,height:1000},reducedMotion:"reduce"});
 const errors=[],writes=[];
 const waitFor=async(fn,arg)=>{
   const end=Date.now()+30000;
   while(Date.now()<end){if(await page.evaluate(fn,arg))return;await page.waitForTimeout(100)}
   throw Error("Expected browser state was not observed");
 };
 page.on("pageerror",e=>errors.push(e.message));
 page.on("request",r=>{if(r.method()!=="GET")writes.push(r.url())});
 await page.goto(f.url+"/sessions");
 await waitFor(()=>document.querySelectorAll(".native-session").length===2);
 const claude=page.locator(".native-session").filter({hasText:"Claude"});
 await claude.click();
 assert((await page.locator(".native-detail").innerText()).includes("claude-pilot"));
 const link=page.url();
 async function event(value,state){
   await fs.writeFile(f.control,value);
   await waitFor(state=>[...document.querySelectorAll(".native-session")].some(e=>e.textContent.includes("Claude")&&e.textContent.includes(state)),state);
 }
 await event("UserPromptSubmit","작업 중");
 await event("PermissionRequest","승인 대기");
 await page.getByLabel("확인 요청함",{exact:false}).check();
 assert.equal(await page.locator(".native-session").count(),1);
 await event("Stop","입력 대기");
 await page.getByLabel("확인 요청함",{exact:false}).uncheck();
 await page.goto(link);
 await page.waitForSelector(".native-history li");
 assert((await page.locator(".native-detail").innerText()).includes("PermissionRequest"));
 await page.getByLabel("도구 필터").selectOption("opencode");
 assert.equal(await page.locator(".native-session").count(),1);
 await fs.writeFile(f.control,"opencodeapproval");
 await waitFor(()=>document.querySelector(".native-session")?.textContent.includes("승인 대기"));
 await fs.writeFile(f.control,"opencodeinput");
 await waitFor(()=>document.querySelector(".native-session")?.textContent.includes("입력 대기"));
 await fs.writeFile(f.control,"offline");
 await waitFor(()=>document.querySelector(".notice")?.textContent.includes("OpenCode"));
 assert((await page.locator(".native-session").innerText()).includes("관측 미확인"));
 await page.getByLabel("도구 필터").selectOption("claude");
 await fs.writeFile(f.control,"SessionEnd");
 await waitFor(()=>document.querySelectorAll(".native-session").length===0);
 await page.getByLabel("종료 이력 포함").check();
 await page.waitForSelector(".native-session");
 assert((await page.locator(".native-session").innerText()).includes("종료"));
 await page.locator(".native-session").click();
 await page.getByText("Claude Code 처음 연결하기",{exact:true}).click();
 await page.getByLabel("Claude 프로젝트 경로").fill(f.project);
 await page.getByRole("button",{name:"설치 명령 준비"}).click();
 await page.waitForSelector(".native-setup pre");
 assert((await page.locator(".native-setup pre").innerText()).includes("native install"));
 await page.screenshot({path:join(out,"native-sessions.png"),fullPage:true});
 await page.setViewportSize({width:390,height:844});
 assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),"mobile overflow");
 await page.getByRole("link",{name:"관리형 작업 관제실"}).click();
 await page.waitForURL("**/mission");
 assert.deepEqual(errors,[]);assert.deepEqual(writes,[]);
 console.log(JSON.stringify({passed:true,checks:["Claude hook subprocess","OpenCode and Claude combined","working/approval/input/end","attention inbox","archive offline","deep link","setup command","mobile","existing mission link","zero writes"]}));
}finally{await browser.close()}
