import assert from 'node:assert/strict';
import { execFile, spawn } from 'node:child_process';
import { once } from 'node:events';
import fs from 'node:fs/promises';
import { createRequire } from 'node:module';
import { homedir, tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { setTimeout as delay } from 'node:timers/promises';
import { promisify } from 'node:util';

const require = createRequire(import.meta.url);
const { chromium } = require(process.env.WORKFLOW_PLAYWRIGHT_PATH ||
  join(homedir(), '.cache/webprobe/runtime/node_modules/playwright'));
const binaryArg = process.argv[2];
assert.ok(binaryArg, 'Pass the OpenCode 2.2.7 binary');
const binary = resolve(binaryArg);
const run = promisify(execFile);
const root = await fs.mkdtemp(join(tmpdir(), 'workflow-opencode-reconnect-'));
const project = join(root, 'project');
const state = join(root, 'state');
const descriptor = join(state, 'opencode', 'service.json');
const workflowHome = join(root, 'workflow');
const children = [];
const python = ['-m', 'agent_workflow', '--home', workflowHome];

function launch(command, args, options) {
  const child = spawn(command, args, options);
  const process = { child, error: null };
  child.on('error', error => { process.error = error; });
  children.push(process);
  return process;
}

async function waitForProcess(process, ready, label) {
  const deadline = Date.now() + 20_000;
  while (Date.now() < deadline) {
    if (process.error) throw process.error;
    assert.equal(process.child.exitCode, null, label + ' exited before readiness');
    assert.equal(process.child.signalCode, null, label + ' stopped before readiness');
    const result = await ready();
    if (result) return result;
    await delay(100);
  }
  throw new Error(label + ' readiness timed out');
}

async function stop({ child }) {
  if (!child.pid || child.exitCode !== null || child.signalCode !== null) return;
  const exited = once(child, 'exit');
  child.kill('SIGTERM');
  await Promise.race([exited, delay(5000, null, { ref: false })]);
  if (child.exitCode === null && child.signalCode === null) {
    child.kill('SIGKILL');
    await exited;
  }
}

async function request(url, route, { password, body } = {}) {
  const headers = { 'Content-Type': 'application/json' };
  if (password) headers.Authorization = 'Basic ' + Buffer.from('opencode:' + password).toString('base64');
  return fetch(url + route, {
    headers,
    method: body === undefined ? 'GET' : 'POST',
    body: body === undefined ? undefined : JSON.stringify(body),
    signal: AbortSignal.timeout(6000),
  });
}

async function json(url, route, options) {
  const response = await request(url, route, options);
  assert.ok(response.ok, route + ' returned HTTP ' + response.status);
  return response.json();
}

async function startOpenCode(name) {
  const isolated = join(root, name);
  const process = launch(binary, ['serve', '--hostname', '127.0.0.1', '--port', '0'], {
    cwd: project,
    env: {
      PATH: '/usr/bin:/bin',
      HOME: isolated,
      XDG_CONFIG_HOME: join(isolated, 'config'),
      XDG_DATA_HOME: join(isolated, 'data'),
      XDG_STATE_HOME: join(isolated, 'state'),
      XDG_CACHE_HOME: join(isolated, 'cache'),
      OPENCODE_DISABLE_MODELS_FETCH: 'true',
      OPENCODE_DISABLE_AUTOUPDATE: 'true',
    },
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  let banner = '';
  for (const stream of [process.child.stdout, process.child.stderr]) {
    stream.on('data', data => { banner += data; });
  }
  return waitForProcess(process, async () => {
    const url = banner.match(/server listening on (http:\/\/127\.0\.0\.1:\d+)/)?.[1];
    const password = banner.match(/server password (\S+)/)?.[1];
    if (!url || !password) return null;
    const { pid, version } = await json(url, '/api/info', { password });
    assert.equal(version, '2.2.7');
    assert.equal(pid, process.child.pid);
    return { process, service: { url, password, pid, version } };
  }, 'OpenCode');
}

async function publish(service) {
  // Publish the replacement endpoint and credential together, as in the local service file.
  const pending = descriptor + '.next';
  await fs.writeFile(pending, JSON.stringify(service), { mode: 0o600 });
  await fs.rename(pending, descriptor);
}

async function createSession({ url, password }, title) {
  const { data } = await json(url, '/api/session', {
    password,
    body: { title, location: { directory: project } },
  });
  assert.equal(typeof data.id, 'string');
  return data;
}

let browser;
try {
  await fs.mkdir(project);
  await fs.mkdir(join(state, 'opencode'), { recursive: true });
  await run('git', ['-C', project, 'init', '--quiet']);
  const first = await startOpenCode('first');
  await publish(first.service);
  const seed = await createSession(first.service, 'Before OpenCode restart');

  const env = { ...process.env, XDG_STATE_HOME: state, HOME: root };
  const { stdout } = await run('python', [...python, 'opencode', 'connect', first.service.url,
    '--project', project, '--follow-local-service'], { env, timeout: 15_000 });
  const { source } = JSON.parse(stdout);
  const registry = join(workflowHome, 'opencode.json');
  const registered = await fs.readFile(registry, 'utf8');
  const dashboard = launch('python', [...python, 'serve', '--port', '0'], { env, stdio: 'ignore' });
  const { url } = await waitForProcess(dashboard, async () => {
    try {
      return JSON.parse(await fs.readFile(join(workflowHome, 'server.json'), 'utf8'));
    } catch (error) {
      if (error.code === 'ENOENT') return null;
      throw error;
    }
  }, 'Dashboard');

  browser = await chromium.launch({ headless: true, args: ['--enable-unsafe-swiftshader'] });
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, reducedMotion: 'reduce' });
  const errors = [];
  const writes = [];
  let pageLoads = 0;
  page.on('load', () => { pageLoads++; });
  page.on('pageerror', error => errors.push(error.message));
  page.on('request', request => {
    if (request.method() !== 'GET') writes.push(request.method());
  });
  await page.goto(url + '/mission?ledger=' + source.id);
  await page.locator('.task-item').filter({ hasText: seed.id }).waitFor();
  const { primary_ledger_id: primary } = await json(url, '/api/capabilities');
  const ledger = page.locator('#ledger');
  assert.ok(primary);
  const managedOption = ledger.locator(`option[value="${primary}"]`);
  await managedOption.waitFor({ state: 'attached' });

  // Start the replacement while the first port is still bound so the ports must differ.
  const replacement = await startOpenCode('replacement');
  assert.notEqual(replacement.service.url, first.service.url, 'the port must change');
  assert.ok(replacement.service.password !== first.service.password, 'the password must change');
  const staleAuth = await request(replacement.service.url, '/api/info', { password: first.service.password });
  assert.equal(staleAuth.status, 401);
  await staleAuth.body?.cancel();
  await stop(first.process);
  const disconnected = page.getByRole('alert').filter({ hasText: '지정한 원장에 접근할 수 없습니다' });
  await disconnected.waitFor({ timeout: 38_000 });
  assert.equal(await ledger.inputValue(), source.id);
  await managedOption.waitFor({ state: 'attached' });

  await publish(replacement.service);
  const title = 'New session after OpenCode port and password changed';
  const newSession = await createSession(replacement.service, title);
  const row = page.locator('.task-item').filter({ hasText: title });
  await row.waitFor({ timeout: 38_000 });
  assert.equal(pageLoads, 1, 'the open browser must discover the session without reload');
  assert.equal(await ledger.inputValue(), source.id);
  await managedOption.waitFor({ state: 'attached' });
  await row.click();
  await page.locator('.page-heading code').filter({ hasText: newSession.id }).waitFor();

  const { ledgers } = await json(url, '/api/connections');
  const recovered = ledgers.find(entry => entry.id === source.id);
  assert.equal(recovered?.home, replacement.service.url);
  assert.equal(recovered.status, 'available');
  assert.equal(ledgers.find(entry => entry.id === primary)?.status, 'available');
  assert.ok(await fs.readFile(registry, 'utf8') === registered, 'the registry must not be rewritten');
  assert.ok(!registered.includes(first.service.password) && !registered.includes(replacement.service.password));
  assert.deepEqual(errors, []);
  assert.deepEqual(writes, []);

  if (process.env.WORKFLOW_SCREENSHOT_DIR) {
    await fs.mkdir(process.env.WORKFLOW_SCREENSHOT_DIR, { recursive: true });
    await page.screenshot({ path: join(process.env.WORKFLOW_SCREENSHOT_DIR, 'opencode-reconnected.png') });
  }
  console.log('OpenCode 2.2.7 reconnect: port and password changed, old password rejected, same connection ID',
    source.id, 'automatically discovered', newSession.id);
} finally {
  if (browser) await browser.close();
  await Promise.all(children.map(stop));
  await fs.rm(root, { recursive: true, force: true });
}
