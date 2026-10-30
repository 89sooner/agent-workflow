// Browser acceptance against the real local HTTP API and SQLite trace store.
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import { createRequire } from 'node:module';
import { homedir } from 'node:os';
import { join } from 'node:path';
const require = createRequire(import.meta.url);
const playwright = require(process.env.WORKFLOW_PLAYWRIGHT_PATH || join(homedir(), '.cache/webprobe/runtime/node_modules/playwright'));
const fixture = JSON.parse(await fs.readFile(process.argv[2], 'utf8'));
const browser = await playwright.chromium.launch({ headless: true });
const context = await browser.newContext({ viewport: { width: 1512, height: 1100 }, reducedMotion: 'reduce' });
const page = await context.newPage();
const errors = [], mutations = [], logs = [];
page.on('pageerror', error => errors.push(error.message));
page.on('request', request => {
  if (request.method() === 'POST') mutations.push(new URL(request.url()).pathname);
  if (request.url().includes('/api/observability/artifacts/')) logs.push(request.url());
});
const screenshots = process.env.WORKFLOW_SCREENSHOT_DIR;
async function snapshot(name) {
  if (!screenshots) return;
  await fs.mkdir(screenshots, { recursive: true });
  // Capture this component without the separate sticky top bar overlaying it.
  const previous = await page.locator('.topbar').evaluate(element => { const old = element.style.visibility; element.style.visibility = 'hidden'; return old; });
  try { await page.locator('#observatory').screenshot({ path: join(screenshots, name + '.png'), animations: 'disabled' }); }
  finally { await page.locator('.topbar').evaluate((element, value) => { element.style.visibility = value; }, previous); }
}
async function until(check, timeout = 15000) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    if (await check()) return;
    await new Promise(resolve => setTimeout(resolve, 100));
  }
  throw new Error('Expected browser/API state was not observed before deadline');
}
async function pickTask(id) {
  await page.locator('.task-item').filter({ hasText: id }).click();
  await until(async () => await page.locator('.detail-header .task-id').textContent() === id);
  await until(async () => (await page.locator('.obs-task-line').textContent()).includes(id));
}
try {
  await page.goto(fixture.url);
  await page.locator('#loading').waitFor({ state: 'hidden' });
  await until(async () => await page.locator('#observatory-graph .obs-node').count() === 6);
  assert.equal(await page.locator('.obs-station[data-actor="claude:implement"]').count(), 1);
  assert.equal(await page.locator('.obs-station[data-actor="codex:review"]').count(), 1);
  assert.match(await page.locator('#observatory-graph').textContent(), /수정 요청/);
  assert.equal(logs.length, 0);
  await page.locator('#observatory-graph .obs-node').nth(2).click();
  assert.match(await page.locator('#observatory-inspector').textContent(), /changes_requested|수정 요청/);
  assert.equal(await page.locator('#observatory-inspector img, #observatory-inspector script').count(), 0);
  assert.equal(await page.evaluate(() => window.unsafe), undefined);
  await page.locator('#observatory-mode-roles').click();
  assert.equal(await page.locator('#observatory-mode-roles').getAttribute('aria-pressed'), 'true');
  assert.equal(await page.locator('#observatory-graph .obs-node').count(), 3);
  await page.locator('#observatory-mode-steps').click();
  await page.locator('#observatory-graph .obs-node').nth(1).click();
  await page.getByRole('button', { name: '출력 로그 열기', exact: true }).click();
  await page.locator('#observatory-inspector .obs-log').filter({ hasText: 'demo check: VALUE=1' }).waitFor();
  assert.equal(logs.length, 1);
  assert.match(await page.locator('#observatory-inspector .obs-log').textContent(), /<script>/);
  assert.equal(await page.evaluate(() => window.unsafe), undefined);
  console.log('PASS actual trace, retry loop, actor identity, graph selection, escaped data, explicit log access');

  const countBefore = mutations.length;
  await page.locator('#observatory-position').focus();
  await page.locator('#observatory-position').press('Home');
  await page.locator('#observatory-position').press('ArrowRight');
  await page.locator('#observatory-position').press('ArrowRight');
  assert.match(await page.locator('#observatory-replay-label').textContent(), /기록|재생|시점/);
  assert.equal(await page.locator('.obs-station.is-active').count(), 0);
  await page.getByRole('button', { name: '다음 이벤트', exact: true }).click();
  await page.locator('#observatory-live').click();
  assert.equal(mutations.length, countBefore);
  const runs = await page.locator('#observatory-run option').evaluateAll(items => items.map(item => ({ value: item.value, text: item.textContent })));
  assert.ok(runs.length >= 3);
  const past = runs.at(-1).value;
  await page.locator('#observatory-run').selectOption(past);
  await until(async () => await page.locator('#observatory-graph .obs-node').count() === 1);
  await page.locator('#observatory-run').selectOption('');
  await until(async () => await page.locator('#observatory-graph .obs-node').count() === 6);
  await page.locator('#observatory-search').fill('리뷰');
  assert.ok(await page.locator('#observatory-events .obs-event').count() > 0);
  await page.locator('#observatory-search').fill('');
  const exportResponse = await page.request.get(new URL(await page.locator('#observatory-export').getAttribute('href'), fixture.url).href);
  assert.equal(exportResponse.status(), 200);
  const exported = await exportResponse.json();
  assert.equal(exported.spans.length, 6);
  assert.equal(exported.coverage.cli_internal_tools, false);
  await page.locator('#observatory-mode-roles').click();
  await snapshot('observatory-desktop');
  await page.locator('#observatory-mode-steps').click();
  console.log('PASS replay is read-only, historical run selection, search, structured export');

  await page.setViewportSize({ width: 390, height: 844 });
  await page.locator('#observatory').scrollIntoViewIfNeeded();
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1), true);
  await until(async () => await page.locator('#observatory-graph').evaluate(element => element.scrollWidth <= element.clientWidth + 1));
  await page.locator('#theme-toggle').click();
  assert.equal(await page.locator('html').getAttribute('data-theme'), 'light');
  assert.equal(await page.locator('.obs-station.is-active').count(), 0);
  await snapshot('observatory-mobile');
  await page.setViewportSize({ width: 1512, height: 1100 });
  await page.locator('#theme-toggle').click();
  console.log('PASS responsive layout, light theme and reduced-motion browser');

  await pickTask('OBS-LIVE');
  await page.getByRole('button', { name: '워크플로 실행', exact: true }).click();
  await page.locator('.obs-station[data-actor="claude:implement"].is-active').waitFor({ timeout: 15000 });
  await page.getByRole('button', { name: '작업 보류', exact: true }).click();
  await page.locator('#hold-reason').fill('브라우저 검증: 다음 단계 전에 사람 확인');
  await page.locator('#hold-submit').click();
  await page.getByRole('button', { name: '보류 해제', exact: true }).waitFor();
  await until(async () => {
    const state = await (await page.request.get(fixture.url + '/api/state')).json();
    return state.jobs.some(job => job.task_id === 'OBS-LIVE' && job.status === 'failed');
  });
  let data = await (await page.request.get(fixture.url + '/api/observability?task_id=OBS-LIVE')).json();
  assert.equal(data.spans.filter(span => span.role === 'check' || span.role === 'review').length, 0);
  assert.match(data.task.hold.reason, /브라우저 검증/);
  await page.getByRole('button', { name: '보류 해제', exact: true }).click();
  await page.getByRole('button', { name: '워크플로 실행', exact: true }).waitFor();
  await page.getByRole('button', { name: '워크플로 실행', exact: true }).click();
  await until(async () => {
    const state = await (await page.request.get(fixture.url + '/api/state')).json();
    return state.tasks.some(task => task.id === 'OBS-LIVE' && task.status === 'completed');
  }, 30000);
  await page.locator('#refresh').click();
  await page.getByRole('button', { name: '로컬 작업 완료', exact: true }).waitFor();
  data = await (await page.request.get(fixture.url + '/api/observability?task_id=OBS-LIVE')).json();
  assert.deepEqual(data.spans.map(span => span.role), ['implement', 'check', 'review']);
  assert.ok(data.runs.find(run => run.id === data.selected_run_id).job_id);
  console.log('PASS actual queue execution, live pixel activity, durable hold boundary, unhold and completion');

  await pickTask('OBS-TRACE');
  await fs.rename(fixture.project, fixture.project + '-moved');
  try {
    await page.locator('#refresh').click();
    await until(async () => (await page.locator('.evidence-note').textContent()).includes('관찰'));
    assert.match(await page.locator('#detail').textContent(), /Git 확인 실패|관찰/);
  } finally { await fs.rename(fixture.project + '-moved', fixture.project); }
  await page.locator('#refresh').click();
  assert.deepEqual(errors, []);
  console.log('PASS worktree observation error visibility, recovery, and zero browser exceptions');
} finally {
  await context.close();
  await browser.close();
}
