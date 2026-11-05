import assert from 'node:assert/strict';
import {build} from 'esbuild';
const out=await build({entryPoints:['frontend/mission/attention.ts'],bundle:true,write:false,format:'esm',platform:'node'});
const {attentionFirst,isAttention}=await import('data:text/javascript;base64,'+Buffer.from(out.outputFiles[0].text).toString('base64'));
const rows=[{id:'idle',state:'idle'},{id:'b',state:'approval',waiting_since:100},{id:'a',state:'input',waiting_since:100},{id:'old',state:'input',waiting_since:1},{id:'unknown-time',state:'input'},{id:'stale',state:'unknown',waiting_since:0}];
assert.deepEqual(attentionFirst(rows).map(s=>s.id),['old','a','b','unknown-time','idle','stale']);
assert.equal(rows[0].id,'idle');assert(!isAttention(rows.at(-1)));
console.log('Attention: oldest first, tie by ID, unknown time last, idle/stale excluded, input immutable');
