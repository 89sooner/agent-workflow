import assert from 'node:assert/strict';
import {build} from 'esbuild';
const out=await build({entryPoints:['frontend/mission/notifications.ts'],bundle:true,write:false,format:'esm',platform:'node'});
const {reconcile}=await import('data:text/javascript;base64,'+Buffer.from(out.outputFiles[0].text).toString('base64'));
const initial=[{id:'one',state:'approval',state_since:1,fresh:true,name:'one'}];
const first=reconcile(null,initial);assert.equal(first.changed.length,0);
assert.equal(reconcile(first.next,initial).changed.length,0);
const next=[{...initial[0],state:'input',state_since:2}];assert.equal(reconcile(first.next,next).changed.length,1);
assert.equal(reconcile(first.next,[{...next[0],fresh:false}]).changed.length,0);
assert.equal(reconcile(first.next,[{...next[0],state_since:null}]).changed.length,0);
assert.equal(reconcile(first.next,[{...next[0],state:'running'}]).changed.length,0);
console.log('Notifications: initial history suppressed, duplicate suppressed, actual fresh transitions only');

const stale=reconcile(first.next,[{...initial[0],state:'unknown',fresh:false}]);
assert.equal(reconcile(stale.next,initial).changed.length,0);

const loadedStale=reconcile(null,[{...initial[0],state:'unknown',fresh:false}]);
assert.equal(reconcile(loadedStale.next,initial).changed.length,0);

assert.equal(reconcile(new Map(),[{...initial[0],state_since:3000}],2000).changed.length,1);
assert.equal(reconcile(new Map(),[{...initial[0],state_since:1000}],2000).changed.length,0);
assert.equal(reconcile(loadedStale.next,[{...initial[0],state_since:3000}],2000).changed.length,1);
