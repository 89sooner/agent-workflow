// Real local API/queue acceptance for the SaaS shell and Three.js room.
// Browser-only instrumentation measures rendering; API responses are not mocked.
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import { createRequire } from 'node:module';
import { homedir } from 'node:os';
import { join } from 'node:path';
import { inflateSync } from 'node:zlib';

const require = createRequire(import.meta.url);
const { chromium } = require(process.env.WORKFLOW_PLAYWRIGHT_PATH || join(homedir(), '.cache/webprobe/runtime/node_modules/playwright'));
const fixture = JSON.parse(await fs.readFile(process.argv[2], 'utf8'));
const origin = new URL(fixture.url).origin;
const screenshots = process.env.WORKFLOW_SCREENSHOT_DIR;
const browser = await chromium.launch({ headless: true, args: [
  '--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--disable-dev-shm-usage',
] });
const context = await browser.newContext({ viewport: { width: 1600, height: 1100 }, reducedMotion: 'no-preference' });
await context.addInitScript(() => {
  const requestFrame = window.requestAnimationFrame.bind(window);
  window.__qaFrames = 0;
  window.requestAnimationFrame = callback => requestFrame(time => { window.__qaFrames++; callback(time); });
});
const page = await context.newPage();
const errors = [], external = [], mutations = [], cspErrors = [];
function watch(target) {
  target.on('pageerror', error => errors.push(error.message));
  target.on('console', message => {
    if (/Content Security Policy|violates.*directive/i.test(message.text())) cspErrors.push(message.text());
  });
  target.on('request', request => {
    const url = new URL(request.url());
    if (/^https?:$/.test(url.protocol) && url.origin !== origin) external.push(url.href);
    if (request.method() === 'POST') mutations.push(url.pathname);
  });
}
watch(page);

const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
async function until(check, label, timeout = 20000) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    if (await check()) return;
    await delay(100);
  }
  throw new Error(`Timed out: ${label}`);
}
async function nav(view, target = page) {
  const opener = target.locator('#navigation-toggle');
  if (await opener.isVisible() && await opener.getAttribute('aria-expanded') !== 'true') await opener.click();
  await target.locator(`#nav-${view}`).click();
  await until(async () => await target.locator('body').getAttribute('data-view') === view, `${view} navigation`);
}
async function selectTask(id, target = page) {
  const opener = target.locator('#navigation-toggle');
  if (await opener.isVisible() && await opener.getAttribute('aria-expanded') !== 'true') await opener.click();
  await target.locator('#search').fill('');
  await target.locator('#filter').selectOption('all');
  await target.locator('.task-item').filter({ hasText: id }).click();
  await until(async () => await target.locator('.detail-header .task-id').textContent() === id, `${id} selected`);
  await nav('observatory', target);
  await until(async () => (await target.locator('.obs-task-line').textContent()).includes(id), `${id} observed`);
}
async function ready() {
  await until(async () => await page.locator('#observatory-scene').getAttribute('data-scene-status') === 'ready', 'Three.js ready');
  await page.locator('canvas[data-office-canvas]').waitFor({ state: 'visible' });
}
async function frameCount() { return page.evaluate(() => window.__qaFrames); }
async function assertNoContinuousFrames(label) {
  await delay(300);
  const before = await frameCount();
  await delay(900);
  const delta = await frameCount() - before;
  assert.ok(delta <= 4, `${label}: expected no continuous animation, observed ${delta} frames`);
}
async function assertAnimating(label) {
  const before = await frameCount();
  await until(async () => await frameCount() - before >= 5, label, 6000);
}
async function snapshot(name, target = page) {
  if (!screenshots) return;
  await fs.mkdir(screenshots, { recursive: true });
  await target.evaluate(() => { document.activeElement?.blur(); document.scrollingElement.scrollTop = 0; });
  await target.screenshot({ path: join(screenshots, `${name}.png`), animations: 'disabled' });
}

// Decode the browser's PNG screenshot (RGB/RGBA, filter 0..4) to distinguish a
// rendered 3D scene from an empty canvas with a uniform CSS background.
function canvasPalette(png) {
  assert.equal(png.subarray(1, 4).toString(), 'PNG');
  let width, height, channels;
  const compressed = [];
  for (let offset = 8; offset < png.length;) {
    const length = png.readUInt32BE(offset), kind = png.toString('ascii', offset + 4, offset + 8);
    const data = png.subarray(offset + 8, offset + 8 + length);
    if (kind === 'IHDR') {
      width = data.readUInt32BE(0); height = data.readUInt32BE(4);
      assert.equal(data[8], 8); assert.ok([2, 6].includes(data[9]));
      channels = data[9] === 6 ? 4 : 3;
      assert.equal(data[12], 0);
    }
    if (kind === 'IDAT') compressed.push(data);
    offset += 12 + length;
  }
  assert.ok(width > 200 && height > 200, 'room canvas has meaningful dimensions');
  const raw = inflateSync(Buffer.concat(compressed)), stride = width * channels;
  let previous = Buffer.alloc(stride), offset = 0;
  const palette = new Set();
  const paeth = (a, b, c) => {
    const p = a + b - c, pa = Math.abs(p - a), pb = Math.abs(p - b), pc = Math.abs(p - c);
    return pa <= pb && pa <= pc ? a : pb <= pc ? b : c;
  };
  for (let y = 0; y < height; y++) {
    const filter = raw[offset++], row = Buffer.from(raw.subarray(offset, offset + stride)); offset += stride;
    assert.ok(filter >= 0 && filter <= 4);
    for (let x = 0; x < stride; x++) {
      const a = x >= channels ? row[x - channels] : 0, b = previous[x], c = x >= channels ? previous[x - channels] : 0;
      row[x] = (row[x] + (filter === 1 ? a : filter === 2 ? b : filter === 3 ? Math.floor((a + b) / 2) : filter === 4 ? paeth(a, b, c) : 0)) & 255;
    }
    if (y % 4 === 0) for (let x = 0; x < stride; x += channels * 4) {
      palette.add(`${row[x] >> 4},${row[x + 1] >> 4},${row[x + 2] >> 4}`);
    }
    previous = row;
  }
  return palette.size;
}

let fallback;
try {
  const response = await page.goto(fixture.url);
  const policy = response.headers()['content-security-policy'];
  assert.match(policy, /script-src 'self'/);
  assert.doesNotMatch(policy, /unsafe-eval|unsafe-inline|https:/);
  await page.locator('#loading').waitFor({ state: 'hidden' });
  assert.equal(await page.locator('html').getAttribute('data-theme'), 'light');
  await selectTask('OBS-TRACE');
  await ready();
  await until(async () => await page.locator('#observatory-graph .obs-node').count() === 6, 'actual six-step trace');
  const palette = canvasPalette(await page.locator('canvas[data-office-canvas]').screenshot());
  assert.ok(palette > 25, `3D canvas should contain geometry and shading, found ${palette} coarse colors`);
  await assertNoContinuousFrames('completed run');

  const trace = await (await page.request.get(`${fixture.url}/api/observability?task_id=OBS-TRACE`)).json();
  const implementation = trace.spans.filter(span => span.actor === 'claude:implement').at(-1);
  const marker = page.locator('.scene3d-label[data-scene-actor="claude:implement"]');
  await until(async () => Number(await marker.getAttribute('data-anchor-x')) > 0, 'projected actor anchor');
  const host = await page.locator('#observatory-scene').boundingBox();
  const x = Number(await marker.getAttribute('data-anchor-x')), y = Number(await marker.getAttribute('data-anchor-y'));
  await page.mouse.click(host.x + x, host.y + y);
  await until(async () => await page.locator('#observatory-graph .obs-node.is-selected').getAttribute('data-span-id') === implementation.id, 'mesh selection reaches graph');
  assert.match(await page.locator('#observatory-inspector').textContent(), /구현/);
  await page.locator('#observatory-zoom-in').click();
  await until(async () => Number(await page.locator('#observatory-scene').getAttribute('data-scene-zoom')) > 1, 'camera zoom');
  await page.locator('#observatory-reset-camera').click();
  await until(async () => Number(await page.locator('#observatory-scene').getAttribute('data-scene-zoom')) === 1, 'camera reset');
  await page.locator('.obs-station[data-actor="codex:review"]').click();
  assert.match(await page.locator('#observatory-inspector').textContent(), /독립 리뷰/);
  console.log(`PASS actual Three.js pixels (${palette} colors), raycast selection, graph/inspector synchronization, zoom/reset, idle render loop`);

  const projects = await page.locator('#workspace-project option').evaluateAll(items => items.map(item => item.value));
  assert.deepEqual(new Set(projects), new Set(['', fixture.project, fixture.other_project]));
  assert.equal(await page.locator('#metric-running').textContent(), '0');
  assert.equal(await page.locator('#metric-queued').textContent(), '0');
  assert.equal(await page.locator('#metric-completed').textContent(), '1');
  assert.ok(!projects.includes(fixture.isolated_project), 'worktree is grouped under its source repository');
  await page.locator('#workspace-project').selectOption(fixture.other_project);
  await until(async () => await page.locator('.task-item').count() === 1, 'secondary project filter');
  assert.match(await page.locator('#task-list').textContent(), /SAAS-OTHER/);
  assert.equal(await page.locator('#metric-completed').textContent(), '0');
  await page.locator('#workspace-project').selectOption(fixture.project);
  await until(async () => await page.locator('.task-item').count() === 3, 'source repository includes isolated worktree');
  assert.match(await page.locator('#task-list').textContent(), /SAAS-WORKTREE/);
  await page.keyboard.press('Control+k');
  assert.equal(await page.evaluate(() => document.activeElement.id), 'search');
  await page.locator('#search').fill('별도 작업 폴더');
  await until(async () => await page.locator('.task-item').count() === 1, 'keyboard search filters real tasks');
  await page.locator('#search').fill('');
  await selectTask('OBS-TRACE');
  await nav('tasks');
  assert.equal(await page.locator('#task-panels').isVisible(), true);
  await nav('runs');
  assert.equal(await page.locator('#execution-panel').isVisible(), true);
  await assertNoContinuousFrames('hidden observatory');
  await nav('settings');
  assert.equal(await page.locator('#settings-panel').isVisible(), true);
  assert.match(await page.locator('#settings-panel').textContent(), /primary-demo|프로젝트|프로필/);
  await page.locator('#preferences-three-d').uncheck();
  await nav('observatory');
  await until(async () => await page.locator('#observatory-view-list').getAttribute('aria-pressed') === 'true', '3D preference off');
  await page.locator('.obs-station[data-actor="claude:implement"]').click();
  assert.equal(await page.locator('#observatory-graph .obs-node.is-selected').getAttribute('data-span-id'), implementation.id);
  await assertNoContinuousFrames('list view');
  await page.getByRole('button', { name: '작업 보류', exact: true }).click();
  await page.locator('#hold-reason').fill('역할 목록에서도 보류 제어 확인');
  await page.locator('#hold-submit').click();
  await page.getByRole('button', { name: '보류 해제', exact: true }).waitFor();
  const listHold = await (await page.request.get(`${fixture.url}/api/observability?task_id=OBS-TRACE`)).json();
  assert.match(listHold.task.hold.reason, /역할 목록/);
  await page.getByRole('button', { name: '보류 해제', exact: true }).click();
  await page.getByRole('button', { name: '작업 보류', exact: true }).waitFor();
  await page.reload();
  await page.locator('#loading').waitFor({ state: 'hidden' });
  await selectTask('OBS-TRACE');
  await until(async () => await page.locator('#observatory-view-list').getAttribute('aria-pressed') === 'true', '3D preference survives reload');
  await nav('settings');
  assert.equal(await page.locator('#preferences-three-d').isChecked(), false);
  await page.locator('#preferences-theme').selectOption('dark');
  assert.equal(await page.locator('html').getAttribute('data-theme'), 'dark');
  await page.locator('#preferences-theme').selectOption('light');
  await page.locator('#preferences-three-d').check();
  await nav('observatory');
  await ready();
  console.log('PASS source-project grouping, real search and navigation, persisted 3D preference, accessible list alternative');

  const selected = await page.locator('#observatory-graph .obs-node.is-selected').getAttribute('data-span-id');
  for (const width of [390, 768, 1600]) {
    await page.setViewportSize({ width, height: width === 390 ? 844 : 1100 });
    await until(async () => await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), `no body overflow at ${width}`);
    assert.equal(await page.locator('#observatory-graph .obs-node.is-selected').getAttribute('data-span-id'), selected);
    const rect = await page.locator('canvas[data-office-canvas]').boundingBox();
    assert.ok(rect.width > 150 && rect.width <= width, `canvas fits ${width}px viewport`);
    if (width === 390) await snapshot('saas-mobile');
  }
  await snapshot('saas-desktop');
  await nav('settings');
  await page.locator('#preferences-theme').selectOption('dark');
  await nav('observatory');
  await ready();
  await snapshot('saas-dark');
  await nav('settings');
  await page.locator('#preferences-theme').selectOption('light');
  await nav('observatory');
  console.log('PASS 390/768/1600 responsive layout, real canvas resizing and selection retention');

  // A short landscape viewport must expose lower navigation content through
  // actual wheel scrolling; locator auto-scrolling would hide clipping bugs.
  await page.setViewportSize({ width: 768, height: 390 });
  await page.locator('#navigation-toggle').click();
  const navigation = page.locator('#navigation');
  await until(async () => (await navigation.boundingBox()).x >= -.5, 'landscape drawer opens');
  assert.equal(await navigation.evaluate(element => element.inert), false);
  const initialScroll = await navigation.evaluate(element => element.scrollTop);
  async function wheelToVisible(selector) {
    const target = page.locator(selector);
    for (let attempt = 0; attempt < 10; attempt++) {
      const rect = await target.boundingBox();
      if (rect && rect.y >= 2 && rect.y + rect.height <= 388) {
        assert.ok(rect.x >= 0 && rect.x + rect.width <= 768, `${selector} fits landscape width`);
        const point = { x: rect.x + rect.width / 2, y: rect.y + rect.height / 2 };
        assert.equal(await target.evaluate((element, point) => element.contains(document.elementFromPoint(point.x, point.y)), point), true,
          `${selector} can receive an actual pointer click without clipping`);
        return point;
      }
      await page.mouse.move(150, 195);
      await page.mouse.wheel(0, rect ? rect.y + rect.height / 2 - 195 : 150);
      await delay(150);
    }
    throw new Error(`Landscape drawer wheel scrolling did not reveal ${selector}`);
  }
  const searchPoint = await wheelToVisible('#search');
  assert.ok(await navigation.evaluate(element => element.scrollTop) > initialScroll, 'actual wheel scroll reveals search');
  await page.mouse.click(searchPoint.x, searchPoint.y);
  assert.equal(await page.evaluate(() => document.activeElement.id), 'search');
  await page.keyboard.type('OBS-LIVE');
  await until(async () => await page.locator('.task-item').count() === 1, 'landscape search filters tasks');
  const taskPoint = await wheelToVisible('.task-item');
  await page.mouse.click(taskPoint.x, taskPoint.y);
  await until(async () => await page.locator('.detail-header .task-id').textContent() === 'OBS-LIVE', 'landscape task selected');
  assert.equal(await page.locator('#navigation-toggle').getAttribute('aria-expanded'), 'false');
  assert.equal(await navigation.evaluate(element => element.inert), true, 'closed mobile drawer is inert');
  assert.equal(await page.locator('.workspace-main').evaluate(element => element.inert), false);
  assert.equal(await page.evaluate(() => document.activeElement.id), 'page-title', 'task selection restores focus to page heading');
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
  await page.setViewportSize({ width: 1600, height: 1100 });
  console.log('PASS 768×390 landscape wheel access to search/tasks, drawer inert state and heading focus after selection');

  await selectTask('OBS-LIVE');
  await page.getByRole('button', { name: '워크플로 실행', exact: true }).click();
  await page.locator('.obs-station[data-actor="claude:implement"].is-active').waitFor({ timeout: 20000 });
  await until(async () => await page.locator('#metric-running').textContent() === '1', 'real running job metric');
  await assertAnimating('live managed process animates');
  await snapshot('saas-live');
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await assertNoContinuousFrames('system reduced motion');
  await page.emulateMedia({ reducedMotion: 'no-preference' });
  await assertAnimating('system motion restored');
  const mutationCount = mutations.length;
  await page.locator('#observatory-position').focus();
  await page.locator('#observatory-position').press('Home');
  assert.equal(await page.locator('.obs-station.is-active').count(), 0);
  await assertNoContinuousFrames('historical replay');
  await page.locator('#observatory-live').click();
  assert.equal(mutations.length, mutationCount, 'replay remains read-only');
  await assertAnimating('live view after replay');
  await nav('runs');
  await assertNoContinuousFrames('running task hidden by navigation');
  await nav('settings');
  await page.locator('#preferences-motion').selectOption('reduce');
  await nav('observatory');
  await assertNoContinuousFrames('user reduced motion');
  await page.getByRole('button', { name: '작업 보류', exact: true }).click();
  await page.locator('#hold-reason').fill('3D 수용 검사: 다음 단계 전에 확인');
  await page.locator('#hold-submit').click();
  await page.getByRole('button', { name: '보류 해제', exact: true }).waitFor();
  await fs.writeFile(fixture.release_file, 'continue controlled process\n');
  await until(async () => {
    const state = await (await page.request.get(`${fixture.url}/api/state`)).json();
    return state.jobs.some(job => job.task_id === 'OBS-LIVE' && job.status === 'failed');
  }, 'held workflow stops at next phase');
  const held = await (await page.request.get(`${fixture.url}/api/observability?task_id=OBS-LIVE`)).json();
  assert.deepEqual(held.spans.map(span => span.role), ['implement']);
  assert.match(held.task.hold.reason, /3D 수용 검사/);
  console.log('PASS real queued activity, system/user motion, replay and hidden-view pause, durable hold at actual process boundary');

  fallback = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  await fallback.addInitScript(() => {
    const getContext = HTMLCanvasElement.prototype.getContext;
    HTMLCanvasElement.prototype.getContext = function(type, ...args) {
      if (['webgl', 'webgl2', 'experimental-webgl'].includes(type)) return null;
      return getContext.call(this, type, ...args);
    };
  });
  const unsupported = await fallback.newPage(); watch(unsupported);
  await unsupported.goto(fixture.url);
  await unsupported.locator('#loading').waitFor({ state: 'hidden' });
  await selectTask('OBS-LIVE', unsupported);
  await until(async () => await unsupported.locator('#observatory-scene').getAttribute('data-scene-status') === 'unavailable', 'unsupported WebGL fallback');
  assert.equal(await unsupported.locator('#observatory-scene-fallback').isVisible(), true);
  await unsupported.locator('.obs-station[data-actor="claude:implement"]').click();
  assert.match(await unsupported.locator('#observatory-inspector').textContent(), /구현/);
  await unsupported.getByRole('button', { name: '보류 해제', exact: true }).click();
  await unsupported.getByRole('button', { name: '워크플로 실행', exact: true }).waitFor();
  await unsupported.getByRole('button', { name: '작업 보류', exact: true }).click();
  await unsupported.locator('#hold-reason').fill('WebGL 대체 화면에서도 작업 제어 확인');
  await unsupported.locator('#hold-submit').click();
  await unsupported.getByRole('button', { name: '보류 해제', exact: true }).waitFor();
  assert.equal(await unsupported.locator('#observatory-graph .obs-node').count(), 1);
  await snapshot('saas-fallback', unsupported);
  await unsupported.getByRole('button', { name: '보류 해제', exact: true }).click();
  console.log('PASS unavailable WebGL preserves actor selection, execution graph, hold and unhold');

  await nav('observatory');
  await ready();
  const lossSupported = await page.locator('canvas[data-office-canvas]').evaluate(canvas => {
    const extension = canvas.getContext('webgl2').getExtension('WEBGL_lose_context');
    if (!extension) return false;
    extension.loseContext(); return true;
  });
  assert.equal(lossSupported, true);
  await until(async () => await page.locator('#observatory-scene').getAttribute('data-scene-status') === 'unavailable', 'lost context fallback');
  assert.equal(await page.locator('#observatory-scene-fallback').isVisible(), true);
  assert.equal(await page.locator('.obs-station[data-actor="claude:implement"]').isEnabled(), true);
  assert.deepEqual(external, []);
  assert.deepEqual(cspErrors, []);
  assert.deepEqual(errors, []);
  console.log('PASS real WebGL context loss fallback, same-origin assets, strict CSP and zero uncaught exceptions');
} catch (error) {
  console.error('SaaS browser diagnostics:', JSON.stringify({ errors, external, cspErrors }));
  await snapshot('saas-failure').catch(() => {});
  throw error;
} finally {
  await fs.writeFile(fixture.release_file, 'cleanup controlled process\n').catch(() => {});
  await fallback?.close();
  await context.close();
  await browser.close();
}
