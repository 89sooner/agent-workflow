// Optional Playwright runner for explicitly configured local acceptance scenarios.
import { createRequire } from 'node:module';
import fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import assert from 'node:assert/strict';
const require = createRequire(import.meta.url);
let playwright;
try { playwright = require('playwright'); }
catch { playwright = require(process.env.WORKFLOW_PLAYWRIGHT_PATH || path.join(os.homedir(), '.cache/webprobe/runtime/node_modules/playwright')); }
const config = JSON.parse(await fs.readFile(process.argv[2], 'utf8'));
const address = new URL(config.url);
assert.equal(address.protocol, 'http:', 'Only explicitly configured local HTTP fixtures are supported');
assert.equal(address.hostname, '127.0.0.1');
assert.ok(Array.isArray(config.steps) && config.steps.length > 0, 'Acceptance steps are required');
const directory = process.env.WORKFLOW_EVIDENCE_DIR;
assert.ok(directory, 'Run this through an agent-workflow check');
await fs.mkdir(directory, { recursive: true });
const browser = await playwright.chromium.launch({ headless: true });
const report = { url: config.url, steps: [], errors: [], externalRequests: [], passed: false };
try {
  const context = await browser.newContext({ viewport: config.viewport || { width: 1280, height: 900 } });
  // Browser checks against a local fixture cannot accidentally reach a production API.
  await context.route('**/*', async route => {
    const url = new URL(route.request().url());
    if (url.origin !== address.origin) {
      report.externalRequests.push(url.origin);
      return route.abort();
    }
    return route.continue();
  });
  const page = await context.newPage();
  page.on('pageerror', error => report.errors.push(error.message));
  await page.goto(config.url, { waitUntil: 'domcontentloaded' });
  for (const step of config.steps) {
    const locator = step.selector ? page.locator(step.selector) : null;
    if (step.action === 'visible') await locator.waitFor({ state: 'visible', timeout: 10000 });
    else if (step.action === 'click') await locator.click();
    else if (step.action === 'fill') await locator.fill(step.value);
    else if (step.action === 'text') await locator.filter({ hasText: step.value }).waitFor({ state: 'visible', timeout: 10000 });
    else if (step.action === 'url') await page.waitForURL(step.value);
    else throw new Error('Unknown acceptance action: ' + step.action);
    report.steps.push({ action: step.action, selector: step.selector || null, passed: true });
  }
  assert.deepEqual(report.errors, [], 'Uncaught browser errors');
  assert.deepEqual(report.externalRequests, [], 'Unexpected external requests');
  report.passed = true;
  await page.screenshot({ path: path.join(directory, 'screenshot.png'), fullPage: true });
} catch (error) {
  report.failure = String(error);
  throw error;
} finally {
  await fs.writeFile(path.join(directory, 'browser-proof.json'), JSON.stringify(report, null, 2) + '\n');
  await browser.close();
}
