import assert from 'node:assert/strict';
import {mkdtemp, mkdir, readFile, rm, writeFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join, resolve} from 'node:path';
import {spawnSync} from 'node:child_process';
import plugin from '../plugins/opencode/v2.js';

const root=await mkdtemp(join(tmpdir(),'workflow-plugin-v2-'));
const cwd=process.cwd();
try {
  const project=join(root,'project');await mkdir(project);
  const python=process.env.AGENT_WORKFLOW_PYTHON||'python3';
  process.env.AGENT_WORKFLOW_HOME=join(root,'state');
  process.env.PYTHONPATH=resolve('.');
  const cli=(...args)=>{
    const p=spawnSync(python,['-P','-m','agent_workflow',...args],{encoding:'utf8'});
    assert.equal(p.status,0,p.stderr);return JSON.parse(p.stdout);
  };
  cli('native','register','--provider','opencode','--project',project);
  const hooks={},events=[];
  let wake,signal;
  const context={location:{directory:project},
    tool:{hook:async(name,callback)=>{hooks[name]=callback;}},
    session:{get:async()=>({data:{location:{directory:'/other'}}}),list:async()=>({data:[{id:'bootstrap',parentID:'parent',location:{directory:project}},
      {id:'wrong-bootstrap',location:{directory:'/other'}}]})},
    event:{subscribe:async function*(options){
      assert(options.types.includes('session.moved'));signal=options.signal;
      while(!signal.aborted){
        if(events.length)yield events.shift();
        else await new Promise(resolve=>{wake=resolve;signal.addEventListener('abort',resolve,{once:true});});
      }
    }},
  };
  const dispose=await plugin.setup(context);
  const send=(type,data,directory=project)=>{
    events.push({type,...(directory?{location:{directory}}:{}),data});wake?.();
  };
  send('session.created',{sessionID:'child',parentID:'actual-parent',title:'PRIVATE'});
  send('session.created',{sessionID:'wrong',parentID:'private'},'/other');
  send('session.execution.started',{sessionID:'child',prompt:'PRIVATE'});
  send('session.execution.succeeded',{sessionID:'child',output:'PRIVATE'},null);
  send('session.execution.failed',{sessionID:'failed',error:{message:'PRIVATE'}});
  send('session.execution.interrupted',{sessionID:'interrupted'});
  send('form.created',{sessionID:'question',question:'PRIVATE'});
  send('session.created',{sessionID:'moved'});
  send('session.moved',{sessionID:'moved'},'/other');
  send('session.execution.started',{sessionID:'moved'},null);
  await hooks['execute.before']({sessionID:'tool',tool:'shell',input:{command:'PRIVATE'}});
  await hooks['execute.after']({sessionID:'tool',tool:'shell',result:{content:'PRIVATE'}});
  let data;
  for(let i=0;i<100;i++){
    const p=spawnSync(python,['-P','-c',"import os,json;from agent_workflow.native import snapshot;print(json.dumps(snapshot(os.environ['AGENT_WORKFLOW_HOME'],None,refresh=False)))"],{encoding:'utf8'});
    assert.equal(p.status,0,p.stderr);data=JSON.parse(p.stdout);
    if(data.sessions.length>=7&&data.sessions.find(x=>x.native_id==='child')?.state==='idle'&&data.sessions.find(x=>x.native_id==='moved')?.state==='unknown')break;
    await new Promise(r=>setTimeout(r,50));
  }
  const byId=Object.fromEntries(data.sessions.map(s=>[s.native_id,s]));
  assert.equal(byId.child.parent,'actual-parent');
  assert.equal(byId.child.state,'idle');
  assert.equal(byId.failed.state,'attention');
  assert.equal(byId.interrupted.state,'input');
  assert.equal(byId.question.state,'input');
  assert.equal(byId.bootstrap.state,'unknown');
  assert.equal(byId.bootstrap.parent,'parent');
  assert.equal(byId.tool.tool,'shell');
  assert.equal(byId.moved.state,'unknown','A moved session must not resume in the old project.');
  assert(!byId.wrong&&!byId['wrong-bootstrap']);
  assert(!(await readFile(join(root,'state/native-observation.sqlite3'))).includes(Buffer.from('PRIVATE')));
  dispose();assert.equal(signal.aborted,true);
  console.log('PASS OpenCode V2 execution lifecycle, actual parent IDs, location isolation, metadata-only transport, and cleanup');
} finally {process.chdir(cwd);await rm(root,{recursive:true,force:true});}
