// Deterministic synthetic view models against the real Three.js renderer.
// Run against an isolated readonly preview URL; no ledger writes or model calls.
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { homedir } from 'node:os';
import { join } from 'node:path';
const require = createRequire(import.meta.url);
const { chromium } = require(process.env.WORKFLOW_PLAYWRIGHT_PATH || join(homedir(), '.cache/webprobe/runtime/node_modules/playwright'));
const url = process.argv[2];
if (!url) throw new Error('Usage: node tests/observatory_motion.mjs http://127.0.0.1:PORT');
const browser = await chromium.launch({ headless: true, args: ['--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--disable-dev-shm-usage'] });
const page = await browser.newPage({ viewport: { width: 1280, height: 900 }, reducedMotion: 'no-preference' });
const errors = [], writes = [];
page.on('pageerror', error => errors.push(error.message));
page.on('request', request => { if (request.method() !== 'GET') writes.push(request.method() + ' ' + request.url()); });
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
try {
  // Only load the isolated static module; app polling must not affect frame counts.
  await page.goto(new URL('/static/THREE_LICENSE.txt', url).href);
  await page.evaluate(async () => {
    document.body.replaceChildren();
    const host = document.createElement('div'); host.id = 'motion-test';
    Object.assign(host.style, { width:'1100px', height:'700px', position:'relative' }); document.body.append(host);
    window.motionRoom = (await import('/static/scene3d.js')).createOffice(host);
    window.motionActors = [
      {id:'controller',label:'컨트롤러',role:'controller',status:'idle',statusLabel:'대기'},
      {id:'agent:a',label:'지우 · 시장 데이터',role:'implement',status:'running',statusLabel:'실행 중',active:true,taskId:'T-A'},
    ];
    window.motionRoom.update({scope:'fixture',actors:window.motionActors,connected:true,visible:true,selectedActor:'agent:a'});
  });
  await page.waitForFunction(() => document.querySelector('#motion-test')?.dataset.sceneStatus === 'ready');
  const generation = await page.locator('[data-scene-actor="agent:a"]').getAttribute('data-station-generation');
  assert.match(await page.locator('[data-scene-actor="agent:a"]').textContent(), /지우 · 시장 데이터/);
  const beforeFrames = Number(await page.locator('#motion-test').getAttribute('data-render-count'));
  await sleep(400);
  assert.ok(Number(await page.locator('#motion-test').getAttribute('data-render-count')) > beforeFrames, 'running instance animates');
  await page.evaluate(() => {
    window.motionRoom.update({actors:window.motionActors});
    window.motionActors.push({id:'agent:b',label:'민수 · 독립 리뷰',role:'review',status:'running',statusLabel:'실행 중',active:true});
    window.motionRoom.update({actors:window.motionActors,transfers:[{id:'1',source:'controller',target:'agent:b',kind:'process_started'}]});
  });
  assert.equal(await page.locator('[data-scene-actor="agent:a"]').getAttribute('data-station-generation'), generation, 'unrelated assignment preserves existing station');
  assert.equal(await page.locator('#motion-test').getAttribute('data-transfer-count'), '1');
  await page.evaluate(() => window.motionRoom.update({transfers:[{id:'1',source:'controller',target:'agent:b',kind:'process_started'}]}));
  assert.equal(await page.locator('#motion-test').getAttribute('data-transfer-count'), '1', 'repeated polling does not replay transfer');
  await page.evaluate(() => window.motionRoom.update({reducedMotion:true}));
  await sleep(200);
  const reducedFrames = Number(await page.locator('#motion-test').getAttribute('data-render-count'));
  await sleep(400);
  assert.equal(Number(await page.locator('#motion-test').getAttribute('data-render-count')), reducedFrames, 'reduced motion stops continuous rendering');
  await page.evaluate(() => window.motionRoom.update({reducedMotion:false,replaying:true}));
  await sleep(200);
  const replayFrames = Number(await page.locator('#motion-test').getAttribute('data-render-count'));
  await sleep(400);
  assert.equal(Number(await page.locator('#motion-test').getAttribute('data-render-count')), replayFrames, 'historical state does not imply live execution');
  assert.match(await page.locator('[data-scene-actor="agent:a"]').textContent(), /당시 실행 중/);
  await page.evaluate(() => window.motionRoom.update({replaying:false,connected:false}));
  await sleep(200);
  const offlineFrames = Number(await page.locator('#motion-test').getAttribute('data-render-count'));
  await sleep(400);
  assert.equal(Number(await page.locator('#motion-test').getAttribute('data-render-count')), offlineFrames, 'disconnection stops live activity');
  await page.evaluate(() => { window.motionRoom.update({connected:true}); window.motionRoom.setVisible(false); });
  await sleep(200);
  const hiddenFrames = Number(await page.locator('#motion-test').getAttribute('data-render-count'));
  await sleep(400);
  assert.equal(Number(await page.locator('#motion-test').getAttribute('data-render-count')), hiddenFrames, 'hidden scene stops rendering');
  await page.evaluate(() => window.motionRoom.dispose());
  assert.equal(await page.locator('#motion-test canvas').count(), 0);
  assert.deepEqual(errors, []); assert.deepEqual(writes, []);
  console.log('PASS actual names, keyed stations, assignment transfer once, running motion, reduced motion, replay, disconnect, hidden pause, disposal; no writes');
} finally { await browser.close(); }
