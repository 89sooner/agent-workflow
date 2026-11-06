// Reproducible, opt-in 60-second render benchmark. No operational state access.
import assert from 'node:assert/strict';
import {build} from 'esbuild';
import {createServer} from 'node:http';
import {createRequire} from 'node:module';
import {mkdir,writeFile} from 'node:fs/promises';
import {resolve,join} from 'node:path';
const out=resolve(process.argv[2]),durationMs=Number(process.argv[3]||60000);
assert(durationMs>=60000,'The acceptance sample must last at least 60 seconds.');
await mkdir(out,{recursive:true});
const {chromium}=createRequire(import.meta.url)(process.env.WORKFLOW_PLAYWRIGHT_PATH||'/home/roqkf/.cache/webprobe/runtime/node_modules/playwright');
const result=await build({stdin:{contents:`
import React from 'react';import {createRoot} from 'react-dom/client';import {Room} from './frontend/mission/Room';
const nodes=Array.from({length:24},(_,i)=>({id:'worker-'+i,actorId:'actor-'+i,taskId:'benchmark',label:'작업자 '+i,
role:i%2?'review':'implement',kind:'agent',status:'running',activity:['reading','writing','command'][i%3],x:0,y:0}));
createRoot(document.getElementById('root')).render(<Room activityKnown={true} nodes={nodes} selected={null} onSelect={()=>{}} motion={true} transfers={[]} visible={true}/>);
`,resolveDir:process.cwd(),loader:'tsx'},bundle:true,write:false,define:{'process.env.NODE_ENV':'"production"'}});
const html='<!doctype html><meta charset="utf-8"><style>body{margin:0;background:#111d2d;color:white}.room-canvas{height:500px;width:100%;position:relative}.panel-heading{display:flex;justify-content:space-between}.room-selection{overflow-wrap:anywhere}</style><div id="root"></div><script src="/bundle.js"></script>';
const server=createServer((req,res)=>{res.setHeader('Content-Type',req.url==='/bundle.js'?'text/javascript':'text/html');res.end(req.url==='/bundle.js'?result.outputFiles[0].contents:html)});
await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
let browser;
try{
 browser=await chromium.launch({headless:true,args:['--enable-unsafe-swiftshader']});
 const page=await browser.newPage({viewport:{width:1440,height:1000},deviceScaleFactor:1});
 const errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.goto('http://127.0.0.1:'+server.address().port);
 await page.waitForSelector('.room-nameplate');await page.waitForTimeout(1500);
 const sample=await page.evaluate(async durationMs=>{
  const canvas=document.querySelector('canvas'),host=canvas.parentElement;
  const firstFrame=Number(host.dataset.renderFrame),start=performance.now();let last=start;const intervals=[];
  await new Promise(resolve=>{function tick(now){intervals.push(now-last);last=now;if(now-start<durationMs)requestAnimationFrame(tick);else resolve()}requestAnimationFrame(tick)});
  intervals.shift();intervals.sort((a,b)=>a-b);
  const gl=canvas.getContext('webgl2'),ext=gl.getExtension('WEBGL_debug_renderer_info');
  return {inputWorkers:24,displayedWorkers:document.querySelectorAll('.room-nameplate').length,
   durationMs:performance.now()-start,sampleCount:intervals.length,renderedFrames:Number(host.dataset.renderFrame)-firstFrame,
   medianMs:intervals[Math.floor(intervals.length*.5)],p95Ms:intervals[Math.floor(intervals.length*.95)],
   drawCalls:Number(host.dataset.drawCalls),triangles:Number(host.dataset.triangles),
   canvasWidth:canvas.width,canvasHeight:canvas.height,dpr:devicePixelRatio,
   renderer:ext?gl.getParameter(ext.UNMASKED_RENDERER_WEBGL):'unknown',
   userAgent:navigator.userAgent,hardwareConcurrency:navigator.hardwareConcurrency};
 },durationMs);
 assert(sample.drawCalls>0&&sample.renderedFrames>=sample.sampleCount*.8,'The scene must continue rendering animated work.');
 assert.deepEqual(errors,[]);
 sample.targetMs=33.3;sample.meets24WorkerTarget=sample.displayedWorkers===24&&sample.p95Ms<=33.3+1e-6;
 await page.screenshot({path:join(out,'scene.png'),fullPage:true});
 await writeFile(join(out,'performance.json'),JSON.stringify({sample,errors,browser:browser.version(),note:'rAF frame intervals with continuous 3D rendering; not a hardware GPU frame-time measurement.'},null,2));
 console.log(JSON.stringify(sample));
 if(process.argv.includes("--require-target"))assert(sample.meets24WorkerTarget,"The 24-worker p95 target was not met.");
}finally{await browser?.close();await new Promise(resolve=>server.close(resolve))}
