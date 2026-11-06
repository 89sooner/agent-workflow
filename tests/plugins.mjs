import assert from 'node:assert/strict';
import {mkdtemp, mkdir, readFile, rm, writeFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join, resolve} from 'node:path';
import {spawnSync} from 'node:child_process';
import {pathToFileURL} from 'node:url';
const originalCwd=process.cwd();
const root=await mkdtemp(join(tmpdir(),'workflow-plugin-'));
try {
 const project=join(root,'project');await mkdir(project);
 const plugins=join(project,'.opencode/plugins');await mkdir(plugins,{recursive:true});
 await writeFile(join(project,'package.json'),JSON.stringify({type:'module'}));
 const installed=join(plugins,'agent-workflow.js');
 await writeFile(installed,await readFile(resolve('plugins/opencode/index.js')));
 const module=await import(pathToFileURL(installed).href);
 assert.deepEqual(Object.keys(module),['AgentWorkflowObserver']);
 const {AgentWorkflowObserver}=module;
 const python=process.env.AGENT_WORKFLOW_PYTHON||'python3';
 process.env.AGENT_WORKFLOW_HOME=join(root,'state');
 process.env.PYTHONPATH=resolve('.');
 await writeFile(join(project,'json.py'), "raise RuntimeError('PROJECT MODULE MUST NOT EXECUTE')\n");
 await mkdir(join(project,'agent_workflow'));
 await writeFile(join(project,'agent_workflow/__init__.py'), "raise RuntimeError('PROJECT PACKAGE MUST NOT EXECUTE')\n");
 process.chdir(project);
 const cli=(...args)=>{const r=spawnSync(python,['-P','-m','agent_workflow','--home',process.env.AGENT_WORKFLOW_HOME,...args],{encoding:'utf8'});assert.equal(r.status,0,r.stderr);return JSON.parse(r.stdout)};
 cli('native','register','--provider','opencode','--project',project);
 const hooks=await AgentWorkflowObserver({directory:project});
 await hooks.event({event:{type:'session.created',properties:{info:{id:'ses_plugin',parentID:'parent',title:'PRIVATE'}}}});
 await hooks['tool.execute.before']({sessionID:'ses_plugin',tool:'bash',args:{command:'PRIVATE'}});
 await hooks.event({event:{type:'permission.asked',properties:{sessionID:'ses_plugin',patterns:['PRIVATE']}}});
 await hooks.event({event:{type:'question.asked',properties:{sessionID:'ses_plugin',questions:['PRIVATE']}}});
 await hooks.event({event:{type:'session.deleted',properties:{info:{id:'ses_plugin'}}}});
 let data;
 for(let i=0;i<100;i++){
  const r=spawnSync(python,['-P','-c','import os,json;from agent_workflow.native import snapshot;print(json.dumps(snapshot(os.environ["AGENT_WORKFLOW_HOME"],None,refresh=False)))'],{encoding:'utf8'});
  assert.equal(r.status,0,r.stderr);data=JSON.parse(r.stdout);
  if(data.sessions[0]?.state==='ended')break;
  await new Promise(r=>setTimeout(r,50));
 }
 assert.equal(data.sessions[0].state,'ended');
 assert.equal(data.sessions[0].provider,'opencode');
 const id=data.sessions[0].id;
 const history=spawnSync(python,['-P','-c',`import os,json;from agent_workflow.native import snapshot;print(json.dumps(snapshot(os.environ['AGENT_WORKFLOW_HOME'],None,'${id}',refresh=False)['history']))`],{encoding:'utf8'});
 assert.deepEqual(JSON.parse(history.stdout).reverse().map(x=>x.state),['idle','running','approval','input','ended']);
 assert(!(await readFile(join(root,'state/native-observation.sqlite3'))).includes(Buffer.from('PRIVATE')));
 await AgentWorkflowObserver({directory:project,client:{session:{list:async()=>({data:[
  {id:'bootstrap',directory:project,title:'PRIVATE'},
  {id:'ses_plugin',directory:project},
  {id:'other-project',directory:'/different'}]})}}});
 await new Promise(r=>setTimeout(r,1000));
 assert.equal(cli('native','status','--provider','opencode','--project',project).observed_sessions,2);
 const snapshot=spawnSync(python,['-P','-c',"import os,json;from agent_workflow.native import snapshot;print(json.dumps(snapshot(os.environ['AGENT_WORKFLOW_HOME'],None,refresh=False)['sessions']))"],{encoding:'utf8'});
 const boot=JSON.parse(snapshot.stdout);
 assert.equal(boot.find(s=>s.native_id==='bootstrap').state,'unknown');
 assert.equal(boot.find(s=>s.native_id==='ses_plugin').state,'ended');
 const before=cli('native','status','--provider','opencode','--project',project).last_seen;
 cli('native','unregister','--provider','opencode','--project',project);
 await hooks.event({event:{type:'session.created',properties:{info:{id:'another'}}}});
 await new Promise(r=>setTimeout(r,300));
 assert.equal(cli('native','status','--provider','opencode','--project',project).last_seen,before);
 process.env.AGENT_WORKFLOW_PYTHON='/does/not/exist';
 await hooks.event({event:{type:'session.idle',properties:{sessionID:'error'}}});
 await new Promise(r=>setTimeout(r,100));
 console.log('PASS OpenCode standalone file installation, plugin FIFO, states, metadata privacy, unregister, missing runtime fail-open, hostile project module isolation');
} finally {process.chdir(originalCwd);await rm(root,{recursive:true,force:true});}
