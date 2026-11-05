import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import {createRequire} from 'node:module';
import {homedir} from 'node:os';
import {join} from 'node:path';
const {chromium}=createRequire(import.meta.url)(process.env.WORKFLOW_PLAYWRIGHT_PATH||join(homedir(),'.cache/webprobe/runtime/node_modules/playwright'));
const f=JSON.parse(await fs.readFile(process.argv[2],'utf8'));
const browser=await chromium.launch({headless:true,args:['--enable-unsafe-swiftshader']});
try{
 const page=await browser.newPage({viewport:{width:1440,height:1000},reducedMotion:'reduce'}),errors=[],writes=[];
 page.on('pageerror',e=>errors.push(e.message));page.on('request',r=>{if(r.method()!=='GET')writes.push(r.url())});
 await page.goto(f.url+'/sessions');
 await page.locator('.native-session').filter({hasText:'Claude'}).click();
 // Decode screenshots in an isolated blank page; application CSP blocks data: images.
 const pixelPage=await browser.newPage();
 const countTint=async tint=>{
  const png=await page.locator('.room-canvas canvas').screenshot();
  return pixelPage.evaluate(async ({png,tint})=>{
   const image=new Image();image.src='data:image/png;base64,'+png;await image.decode();
   const canvas=document.createElement('canvas');canvas.width=image.width;canvas.height=image.height;
   const context=canvas.getContext('2d');context.drawImage(image,0,0);
   const pixels=context.getImageData(0,0,canvas.width,canvas.height).data;let count=0;
   for(let i=0;i<pixels.length;i+=4)if(tint.every((v,j)=>Math.abs(pixels[i+j]-v)<=4))count++;
   return count;
  },{png:png.toString('base64'),tint});
 };
 const send=async command=>{await fs.writeFile(f.control+'.next',command);await fs.rename(f.control+'.next',f.control)};
 const pixels=[];
 for(const motion of ['reduce','no-preference']){
  await page.emulateMedia({reducedMotion:motion});
  for(const [tool,label,tint] of [['Read','읽는 중',null],['Edit','수정 중',[115,214,165]],['Bash','명령 실행 중',[230,188,118]]]){
   const before=tint?await countTint(tint):0;
   await send(`activity:${tool}:start`);
   await page.getByText('관측 활동: '+label,{exact:true}).waitFor();
   await page.waitForFunction(label=>document.querySelector('.room-selection')?.textContent.includes(label),label);
   if(tool==='Read')await page.getByRole('button',{name:'선택한 작업자 가까이',exact:true}).click();
   if(tint){
    let visible=0;
    for(let i=0;i<20;i++){visible=await countTint(tint);if(visible>before+8)break;await page.waitForTimeout(100)}
    assert(visible>before+8,`${motion} ${tool} activity marker must produce visible canvas pixels: ${before} -> ${visible}`);
    pixels.push({motion,tool,before,visible});
   }
   await page.locator('.room-panel').screenshot({path:`/tmp/observatory-activity-${motion}-${tool}.png`});
   await send(`activity:${tool}:stop`);
   await page.getByText('관측 활동: 작업 중',{exact:true}).waitFor();
   if(tint){
    let after=Infinity;
    for(let i=0;i<20;i++){after=await countTint(tint);if(after<=before+4)break;await page.waitForTimeout(100)}
    assert(after<=before+4,`${motion} ${tool} marker tint must clear on completion: ${before} -> ${after}`);
   }
  }
 }
 await page.emulateMedia({reducedMotion:'reduce'});
 console.log(JSON.stringify({activityMarkerPixels:pixels}));
 await page.waitForFunction(element=>element.checked,await page.getByLabel('움직임 줄이기',{exact:true}).elementHandle());
 assert.deepEqual(errors,[]);assert.deepEqual(writes,[]);
 console.log(JSON.stringify({passed:true,checks:['explicit Read/Edit/Bash starts','completion returns generic work','3D selected activity','actual Edit/Bash canvas color pixels appear and clear','normal and reduced-motion','no page errors or writes']}));
}finally{await browser.close()}
