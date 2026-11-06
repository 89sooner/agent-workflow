import assert from 'node:assert/strict';
import {build} from 'esbuild';
import {createServer} from 'node:http';
import {createRequire} from 'node:module';
const {chromium}=createRequire(import.meta.url)(process.env.WORKFLOW_PLAYWRIGHT_PATH||'/home/roqkf/.cache/webprobe/runtime/node_modules/playwright');
const bundle=await build({stdin:{loader:'tsx',resolveDir:process.cwd(),contents:`
import React,{useMemo,useRef,useLayoutEffect,useState} from 'react';
import {createRoot} from 'react-dom/client';import {Canvas,useFrame,useThree} from '@react-three/fiber';
import {Vector3} from 'three';import {InstancedWorkers} from './frontend/mission/InstancedWorkers';
function Source({id,x,actors,box,moving}){
 const ref=useRef(null);
 useLayoutEffect(()=>{actors.set(id,ref.current);ref.current.userData.node={id};return()=>actors.delete(id)},[id,actors]);
 useLayoutEffect(()=>{ref.current.userData.batchRevision=(ref.current.userData.batchRevision||0)+1},[box]);
 useFrame(({clock})=>{if(moving)ref.current.position.x=x+Math.sin(clock.elapsedTime*2)*.6},-2);
 return <group ref={ref} position={[x,0,0]}><mesh position={[0,1,0]}>
  {box?<boxGeometry args={[.8,.8,.8]}/>:<sphereGeometry args={[.4,12,8]}/>}
  <meshStandardMaterial color='#4d718e'/></mesh></group>;
}
function Probe({actors}){
 const {camera,gl}=useThree();
 window.actorPoint=id=>{camera.updateMatrixWorld();const group=actors.get(id);group.updateWorldMatrix(true,true);const p=group.localToWorld(new Vector3(0,1,0)).project(camera);const r=gl.domElement.getBoundingClientRect();return {x:r.left+(p.x+1)*r.width/2,y:r.top+(1-p.y)*r.height/2}};
 useFrame(()=>{window.rendered=gl.info.render.frame;window.drawCalls=gl.info.render.calls});return null;
}
function App(){const actors=useMemo(()=>new Map(),[]);const [moving,move]=useState(false),[box,shape]=useState(false),[first,keep]=useState(true);
 window.moveActors=move;window.changeShape=()=>shape(v=>!v);window.removeFirst=()=>keep(false);
 return <Canvas camera={{position:[0,2,7],fov:45}} gl={{antialias:false}}>
  <ambientLight intensity={2}/>{first&&<Source id='first' x={-1} actors={actors} box={false} moving={moving}/>}
  <Source id='second' x={1} actors={actors} box={box} moving={false}/>
  <InstancedWorkers actors={actors} onSelect={n=>{window.clicked=n.id}}/><Probe actors={actors}/>
 </Canvas>;
}createRoot(document.getElementById('root')).render(<App/>);
`},bundle:true,write:false,define:{'process.env.NODE_ENV':'"production"'}});
const server=createServer((req,res)=>{res.setHeader('Content-Type',req.url==='/app.js'?'text/javascript':'text/html');res.end(req.url==='/app.js'?bundle.outputFiles[0].contents:'<!doctype html><style>html,body,#root{height:100%;margin:0}</style><div id="root"></div><script src="/app.js"></script>')});
await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
let browser;
try{
 browser=await chromium.launch({headless:true,args:['--enable-unsafe-swiftshader']});
 const page=await browser.newPage({viewport:{width:900,height:600}}),errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.goto('http://127.0.0.1:'+server.address().port);
 await page.waitForFunction(()=>window.rendered>3&&window.drawCalls===1);
 async function pick(id){
  const point=await page.evaluate(id=>{window.clicked=null;return window.actorPoint(id)},id);
  await page.mouse.click(point.x,point.y);await page.waitForFunction(id=>window.clicked===id,id);
  return point;
 }
 const before=await pick('first');await pick('second');
 await page.evaluate(()=>window.moveActors(true));await page.waitForTimeout(600);
 await page.evaluate(()=>window.moveActors(false));await page.waitForTimeout(60);
 const after=await pick('first');assert(Math.abs(before.x-after.x)>4,'The source pose must update the instance matrix.');
 await page.evaluate(()=>window.changeShape());await page.waitForFunction(()=>window.drawCalls===2);
 await pick('second');
 await page.evaluate(()=>window.removeFirst());await page.waitForFunction(()=>window.drawCalls===1);
 await pick('second');assert.deepEqual(errors,[]);
 console.log('PASS actual instanced ray picking, moving matrices, geometry rebuild, actor removal, and shared draws');
}finally{await browser?.close();await new Promise(resolve=>server.close(resolve))}
