// Browser regression checks use intercepted API fixtures, never real queue mutations.
// Run: node tests/ui_scenarios.mjs [http://127.0.0.1:8776]
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { homedir } from 'node:os';
import { join } from 'node:path';
const require = createRequire(import.meta.url);
let playwright;
try { playwright = require('playwright'); }
catch { playwright = require(process.env.WORKFLOW_PLAYWRIGHT_PATH || join(homedir(), '.cache/webprobe/runtime/node_modules/playwright')); }
const origin = process.argv[2] || 'http://127.0.0.1:8776';
const browser = await playwright.chromium.launch({ headless: true });
const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
const page = await context.newPage();
const errors = [];
page.on('pageerror', (e) => errors.push(e.message));
const task = {
  id: 'UI-READY', goal: '검색 결과의 변경 내용을 검증하고 실행하기', status: 'ready',
  project: '/workspace/검색 프로젝트', owner: 'claude', reviewer: 'codex',
  criteria: ['검색 결과에서 변경 내용을 확인한다'], checkpoint: { summary: '작업 준비 완료', next: '기능 구현' },
  verification: null, review: null, implementation: null, release: null
};
const stale = {
  ...task, id: 'UI-STALE', goal: '<img src=x onerror="window.unsafe=true"> 오래된 검증', status: 'completed',
  verification: { passed: true }, review: { verdict: 'pass' },
  release: { stage: 'configured', error: 'CI 결과를 확인하세요.', config: { base: 'main', branch: 'workflow/test', deploy: ['deploy'] } }
};
let tasks = [task, stale], jobs = [], fail = false;
const requests = [];
let events = [{ at: '2026-10-08T00:00:00Z', kind: 'created', data: { summary: '작업 등록됨', source: 'fixture' } }];
await page.route('**/api/**', async (route) => {
  const req = route.request(), path = new URL(req.url()).pathname;
  if (fail) return route.fulfill({ status: 503, json: { error: '테스트 연결 중단' } });
  if (path === '/api/state') return route.fulfill({ json: { tasks, jobs, daemon: { running: true }, version: 'fixture', csrf_token: 'fixture-token' } });
  if (path.startsWith('/api/tasks/')) {
    const selected = tasks.find((t) => t.id === decodeURIComponent(path.split('/').pop()));
    return route.fulfill({ json: { task: selected, jobs, events, observed_git: {}, freshness: {
      verification_is_current: false, review_is_current: false, completion_is_current: false
    } } });
  }
  if (req.method() === 'POST') {
    assert.equal(req.headers()['x-workflow-token'], 'fixture-token');
    const body = req.postDataJSON(); requests.push({ path, body });
    if (path === '/api/queue') {
      const job = { id: `job-${requests.length}`, task_id: body.task_id, action: body.action, status: 'queued', created_at: '2026-10-08T00:01:00Z' };
      jobs.push(job); return route.fulfill({ json: { job } });
    }
    if (path === '/api/cancel') {
      const job = jobs.find((j) => j.id === body.job_id); job.status = 'cancelled';
      return route.fulfill({ json: { job } });
    }
  }
  return route.fulfill({ status: 404, json: { error: 'Unexpected fixture request' } });
});
const waitLoaded = async () => { await page.locator('#loading').waitFor({ state: 'hidden' }); await page.locator('.detail-title').waitFor(); };
try {
  await page.goto(origin); await waitLoaded();
  assert.equal(await page.locator('#metric-blocked').textContent(), '1');
  assert.equal(await page.locator('#metric-deployed').textContent(), '0');
  await page.keyboard.press('/'); assert.equal(await page.evaluate(() => document.activeElement.id), 'search');
  await page.locator('#search').fill('없는 검색'); assert.match(await page.locator('#task-list').textContent(), /조건에 맞는 작업이 없습니다/);
  await page.locator('#search').fill('');
  await page.getByRole('button', { name: '워크플로 실행', exact: true }).click();
  await page.locator('#jobs .badge.warn').waitFor();
  assert.deepEqual(requests[0], { path: '/api/queue', body: { task_id: 'UI-READY', action: 'pipeline' } });
  assert.equal(await page.getByRole('button', { name: '실행 대기 · 진행 중' }).isDisabled(), true);
  await page.getByRole('button', { name: 'UI-READY 실행 대기 취소' }).click();
  await page.locator('#jobs .badge', { hasText: '취소됨' }).waitFor();
  assert.equal(requests[1].path, '/api/cancel');
  console.log('PASS search, keyboard, CSRF queue, duplicate disable, cancel');

  await page.locator('#filter').selectOption('blocked');
  assert.equal(await page.locator('.task-item').count(), 1);
  await page.locator('.task-item').click();
  await page.locator('.evidence-note.stale').waitFor();
  assert.equal(await page.locator('#detail img').count(), 0);
  assert.equal(await page.evaluate(() => window.unsafe), undefined);
  assert.equal(await page.locator('.workflow-step').nth(1).locator('.step-state').textContent(), '확인 필요');
  assert.equal(await page.locator('.workflow-step').nth(2).locator('.step-state').textContent(), '확인 필요');
  await page.getByRole('button', { name: '통합 · 배포 실행', exact: true }).click();
  await page.getByRole('button', { name: 'UI-STALE 실행 대기 취소' }).waitFor();
  assert.equal(requests.at(-1).body.action, 'release');
  await page.getByRole('button', { name: 'UI-STALE 실행 대기 취소' }).click();
  console.log('PASS attention filter, stale evidence, literal rendering, release request');

  await page.locator('.event-raw summary').first().click();
  await page.locator('.event-raw[open]').waitFor();
  events = [{ at: '2026-10-08T00:02:00Z', kind: 'checkpoint', data: { summary: '새 기록' } }, ...events];
  await page.locator('#refresh').click();
  await page.getByText('새 기록', { exact: true }).waitFor();
  assert.equal(await page.locator('.event-raw[open]').count(), 1);
  assert.equal(await page.locator('.detail-title').textContent(), stale.goal);
  await page.locator('#theme-toggle').click();
  assert.equal(await page.locator('html').getAttribute('data-theme'), 'light');
  await page.reload(); await waitLoaded();
  assert.equal(await page.locator('html').getAttribute('data-theme'), 'light');
  assert.equal(await page.locator('.detail-title').textContent(), stale.goal);
  console.log('PASS selected task and theme persistence, event expansion through refresh');

  for (const width of [375, 390, 768, 1440]) {
    await page.setViewportSize({ width, height: 900 });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, `overflow at ${width}`);
    assert.ok(await page.locator('#refresh').getAttribute('aria-label'));
  }
  await page.emulateMedia({ reducedMotion: 'reduce' });
  assert.equal(await page.locator('.button').first().evaluate((el) => getComputedStyle(el).transitionDuration), '0s');
  console.log('PASS 375/390/768/1440 widths, mobile accessible refresh, reduced motion');

  tasks = []; jobs = []; await page.locator('#refresh').click();
  await page.getByText('첫 워크플로를 시작해 보세요.', { exact: true }).waitFor();
  assert.equal(await page.locator('#metric-active').textContent(), '0');
  assert.equal(await page.locator('.workflow-track').count(), 0);
  fail = true; await page.locator('#refresh').click();
  await page.locator('#error').filter({ hasText: '테스트 연결 중단' }).waitFor();
  fail = false; await page.locator('#refresh').click();
  await page.locator('#error').waitFor({ state: 'hidden' });
  assert.deepEqual(errors, []);
  console.log('PASS empty state, connection failure and recovery, zero uncaught errors');
} finally { await context.close(); await browser.close(); }
