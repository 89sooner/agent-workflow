import assert from "node:assert/strict";
import fs from "node:fs/promises";
import { createRequire } from "node:module";
import { homedir } from "node:os";
import { join } from "node:path";
import { spawn } from "node:child_process";
const require = createRequire(import.meta.url);
const { chromium } = require(
  process.env.WORKFLOW_PLAYWRIGHT_PATH ||
    join(homedir(), ".cache/webprobe/runtime/node_modules/playwright"),
);
const f = JSON.parse(await fs.readFile(process.argv[2], "utf8"));
const browser = await chromium.launch({
  headless: true,
  args: ["--enable-unsafe-swiftshader"],
});
const context = await browser.newContext({
  viewport: { width: 1600, height: 1050 },
});
const page = await context.newPage();
const errors = [],
  mutations = [],
  external = [];
let runner;
page.on("pageerror", (e) => errors.push(e.message));
page.on("console", (m) => {
  if (
    m.type() === "error" &&
    !m.text().includes("ERR_INTERNET_DISCONNECTED") &&
    !m.text().includes("Failed to fetch")
  )
    errors.push(m.text());
});
page.on("request", (r) => {
  if (r.method() !== "GET") mutations.push(r.url());
  if (new URL(r.url()).origin !== f.url) external.push(r.url());
});
const until = async (fn, timeout = 20000) => {
  const end = Date.now() + timeout;
  while (Date.now() < end) {
    if (await fn()) return;
    await page.waitForTimeout(100);
  }
  throw Error("Expected browser state was not observed");
};
const pick = async (id) => {
  await page.locator("#task-search").fill(id);
  await page.locator(".task-item").filter({ hasText: id }).click();
  await until(
    async () => (await page.locator(".page-heading code").textContent()) === id,
  );
  await page.locator("#task-search").fill("");
  await until(
    async () => !(await page.getByText("실행 기록을 읽고 있습니다…").count()),
  );
};
const snapshot = async (name) => {
  if (!process.env.WORKFLOW_SCREENSHOT_DIR) return;
  await fs.mkdir(process.env.WORKFLOW_SCREENSHOT_DIR, { recursive: true });
  await page.evaluate(() => {
    window.scrollTo(0, 0);
    document.activeElement?.blur();
  });
  await page.screenshot({
    path: join(process.env.WORKFLOW_SCREENSHOT_DIR, name + ".png"),
    fullPage: true,
  });
};
try {
  await page.goto(f.url + "/mission");
  await page.locator(`#ledger option[value="${f.ledger}"]`).waitFor({ state: "attached" });
  // Discovery sorts by ledger update time; select this fixture's execution ledger explicitly.
  await page.locator("#ledger").selectOption(f.ledger);
  await page.waitForSelector(".graph-node");
  assert.equal(await page.title(), "Agent Workflow · Mission Control");
  assert.equal(await page.locator(".room-canvas canvas").count(), 1);
  assert.equal(await page.locator("#ledger").inputValue(), f.ledger);
  // Actual named assignment and a real gated subprocess controlled only by this fixture.
  await pick("OBS-LIVE");
  assert(
    await page
      .locator(".actor-list")
      .innerText()
      .then((t) => t.includes("지우 · 실제 배정")),
  );
  runner = spawn(
    f.python,
    [
      "-m",
      "agent_workflow",
      "--home",
      f.home,
      "run",
      "OBS-LIVE",
      "--timeout",
      "120",
    ],
    {
      cwd: f.source,
      env: {
        ...process.env,
        PATH: f.bin + ":" + process.env.PATH,
        WORKFLOW_SAAS_RELEASE: f.release_file,
        PYTHONDONTWRITEBYTECODE: "1",
      },
      stdio: "ignore",
    },
  );
  await until(
    async () =>
      (await page.locator(".room-canvas").getAttribute("data-active")) ===
      "true",
  );
  await page.locator(".graph-node").filter({ hasText: "지우" }).click();
  assert(
    (await page.locator(".inspector").innerText()).includes(
      "실제로 할당한 이름",
    ),
  );
  assert((await page.locator(".room-selection").innerText()).includes("지우"));
  await page.getByRole("button", { name: "선택한 작업자 가까이" }).click();
  await page.getByRole("button", { name: "시점 초기화", exact: true }).click();
  const movingA = await page.locator(".room-canvas canvas").screenshot();
  await page.waitForTimeout(250);
  const movingB = await page.locator(".room-canvas canvas").screenshot();
  assert(!movingA.equals(movingB), "working character must actually animate");
  await snapshot("react-mission-live");
  await page.emulateMedia({ reducedMotion: "reduce" });
  await until(
    async () =>
      (await page.locator(".room-canvas").getAttribute("data-active")) ===
      "false",
  );
  assert(await page.getByLabel("움직임 줄이기").isDisabled());
  await page.waitForTimeout(250);
  const stillA = await page.locator(".room-canvas canvas").screenshot();
  await page.waitForTimeout(250);
  const stillB = await page.locator(".room-canvas canvas").screenshot();
  assert(
    stillA.equals(stillB),
    "reduced motion must stop actual scene movement",
  );
  await page.emulateMedia({ reducedMotion: "no-preference" });
  await until(
    async () =>
      (await page.locator(".room-canvas").getAttribute("data-active")) ===
      "true",
  );
  await page.evaluate(() => {
    Object.defineProperty(document, "hidden", {
      configurable: true,
      get: () => true,
    });
    document.dispatchEvent(new Event("visibilitychange"));
  });
  await until(
    async () =>
      (await page.locator(".room-canvas").getAttribute("data-active")) ===
      "false",
  );
  let releaseResume;
  const resumeGate = new Promise((resolve) => { releaseResume = resolve; });
  let resumeRequests = 0;
  const resumeRoute = "**/api/observability?**";
  await page.route(resumeRoute, async (route) => {
    resumeRequests++;
    await resumeGate;
    await route.continue();
  });
  await page.evaluate(() => {
    delete document.hidden;
    document.dispatchEvent(new Event("visibilitychange"));
  });
  await until(async () => resumeRequests > 0);
  await page.waitForTimeout(350);
  assert.equal(
    await page.locator(".room-canvas").getAttribute("data-active"),
    "false",
    "cached running records must stay still while the resumed request is pending",
  );
  releaseResume();
  await until(
    async () =>
      (await page.locator(".room-canvas").getAttribute("data-active")) ===
      "true",
  );
  await page.unroute(resumeRoute);
  // Leaving a task disables its poll. Returning must not revive its cached activity.
  await page.locator(".overview-button").click();
  await until(async () => (await page.locator(".page-heading code").count()) === 0);
  let releaseReselect;
  const reselectGate = new Promise((resolve) => { releaseReselect = resolve; });
  let reselectRequests = 0;
  await page.route(resumeRoute, async (route) => {
    reselectRequests++;
    await reselectGate;
    await route.continue();
  });
  await page.locator(".task-item").filter({ hasText: "OBS-LIVE" }).click();
  await until(async () => reselectRequests > 0);
  await page.waitForTimeout(350);
  assert.equal(
    await page.locator(".room-canvas").getAttribute("data-active"),
    "false",
    "returning to the same task must wait for its new response",
  );
  releaseReselect();
  await until(async () =>
    (await page.locator(".room-canvas").getAttribute("data-active")) === "true",
  );
  await page.unroute(resumeRoute);
  await page.evaluate(() => {
    Object.defineProperty(document, "hidden", {
      configurable: true,
      get: () => true,
    });
    document.dispatchEvent(new Event("visibilitychange"));
  });
  await until(async () =>
    (await page.locator(".room-canvas").getAttribute("data-active")) === "false",
  );
  await context.setOffline(true);
  await page.evaluate(() => {
    delete document.hidden;
    document.dispatchEvent(new Event("visibilitychange"));
  });
  await page.getByRole("alert").waitFor();
  assert.equal(
    await page.locator(".room-canvas").getAttribute("data-active"),
    "false",
  );
  await context.setOffline(false);
  await page.getByRole("button", { name: "다시 연결" }).click();
  await until(async () => (await page.getByRole("alert").count()) === 0);
  await fs.writeFile(f.release_file, "browser released synthetic process");
  await until(async () => runner.exitCode !== null, 30000);
  assert.equal(runner.exitCode, 0);
  await pick("OBS-TRACE");
  await page.locator(".graph-node").first().click();
  await page.getByRole("button", { name: "표준 출력", exact: true }).click();
  await until(
    async () =>
      await page
        .getByLabel("실행 로그")
        .innerText()
        .then((t) => !t.includes("읽는 중")),
  );
  await page.getByRole("button", { name: "기록 재생", exact: false }).click();
  await until(
    async () =>
      (await page.locator(".mission-shell").getAttribute("data-mode")) ===
      "replay",
  );
  await page.getByRole("button", { name: "일시 정지", exact: false }).click();
  await page.locator("#event-position").fill("0");
  assert.equal(
    await page.locator(".room-canvas").getAttribute("data-active"),
    "false",
  );
  assert.equal(
    await page.getByRole("button", { name: "표준 출력", exact: true }).count(),
    0,
  );
  await page.getByRole("button", { name: "실시간으로 돌아가기" }).click();
  // More than eight stages: actor/event selection must bring old span into graph and room.
  await pick("MISSION-LONG");
  assert.equal(await page.locator(".graph-node").count(), 8);
  // Load a real older HTTP page, then verify it is navigable.
  while (
    await page.getByRole("button", { name: "이전 기록 불러오기" }).isEnabled()
  ) {
    await page.getByRole("button", { name: "이전 기록 불러오기" }).click();
    await until(
      async () =>
        (await page
          .getByRole("button", { name: "읽는 중…", exact: true })
          .count()) === 0,
    );
  }
  while (
    await page.getByRole("button", { name: "이전 쪽", exact: true }).isEnabled()
  )
    await page.getByRole("button", { name: "이전 쪽", exact: true }).click();
  assert(
    (await page.locator(".event-list").innerText()).includes(
      "MANY-CHECKPOINT-0",
    ),
  );
  await page
    .locator(`.event-list [data-span-id="${f.old_span}"] button`)
    .first()
    .click();
  await until(
    async () =>
      (await page.locator(`[data-node-id="${f.old_span}"]`).count()) > 0,
  );
  await snapshot("react-mission-history");
  // Delay an actual response; switching ledgers must discard it even before new data arrives.
  await pick("OBS-TRACE");
  await pick("MISSION-LONG");
  let delayed, release;
  const gate = new Promise((r) => (release = r));
  await page.route("**/api/observability?*", async (route) => {
    if (new URL(route.request().url()).searchParams.has("before")) {
      const response = await route.fetch();
      delayed = true;
      await gate;
      await route.fulfill({ response }).catch(() => {});
    } else await route.continue();
  });
  await page.getByRole("button", { name: "이전 기록 불러오기" }).click();
  await until(async () => delayed);
  await page.locator("#ledger").selectOption(f.second_ledger);
  release();
  await page.waitForTimeout(1200);
  await page.getByRole("button", { name: "기록 새로고침" }).click();
  await until(
    async () =>
      await page
        .locator(".task-list")
        .innerText()
        .then((t) => t.includes("SECOND-ONLY")),
  );
  assert(
    !(await page.locator(".event-list").innerText()).includes(
      "MANY-CHECKPOINT",
    ),
  );
  await page.unroute("**/api/observability?*");
  await page.locator("#ledger").selectOption(f.ledger);
  await page.waitForSelector(".graph-node");
  await page.locator("#project").selectOption(f.other_project);
  await until(
    async () =>
      await page
        .locator(".task-list")
        .innerText()
        .then((t) => t.includes("SAAS-OTHER") && !t.includes("OBS-TRACE")),
  );
  await page.locator("#project").selectOption("");
  await pick("OBS-TRACE");
  await page.setViewportSize({ width: 390, height: 844 });
  await page.waitForTimeout(300);
  assert(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth + 1,
    ),
  );
  await snapshot("react-mission-mobile");
  await page.setViewportSize({ width: 1600, height: 1050 });
  await page.locator(".graph-node").first().focus();
  await page.keyboard.press("Enter");
  assert.equal(await page.locator(".graph-node[aria-pressed=true]").count(), 1);
  await page
    .locator(".room-canvas canvas")
    .evaluate((canvas) =>
      canvas
        .getContext("webgl2")
        .getExtension("WEBGL_lose_context")
        .loseContext(),
    );
  await page.getByText("3D를 표시할 수 없습니다.", { exact: false }).waitFor();
  assert((await page.locator(".graph-node").count()) > 0);
  assert.deepEqual(mutations, [], "mission UI is observation only");
  assert.deepEqual(external, [], "all assets local");
  assert.deepEqual(errors, []);
  console.log(
    JSON.stringify({
      passed: true,
      checks: [
        "assigned names",
        "live real subprocess",
        "3D canvas",
        "reduced motion",
        "hidden tab",
        "resumed tab waits for fresh response",
        "reselected task waits for fresh response",
        "disconnect/recovery",
        "logs",
        "replay",
        "old-stage selection",
        "pagination",
        "cross-ledger delayed response",
        "project filter",
        "mobile overflow",
        "keyboard",
        "WebGL fallback",
        "zero writes",
        "zero remote assets",
        "zero console errors",
      ],
    }),
  );
} catch (error) {
  console.error(
    "BROWSER DEBUG",
    errors,
    (await page.locator(".workspace").innerText()).slice(-2500),
  );
  await snapshot("react-mission-failure");
  throw error;
} finally {
  await fs.writeFile(f.release_file, "cleanup").catch(() => {});
  if (runner && runner.exitCode === null) {
    runner.kill("SIGTERM");
    await new Promise((r) => runner.once("exit", r));
  }
  await browser.close();
}
