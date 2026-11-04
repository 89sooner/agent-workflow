// Isolated synthetic scenes: never reads or writes an operational task ledger.
import assert from "node:assert/strict";
import { build } from "esbuild";
import { createServer } from "node:http";
import { createRequire } from "node:module";
import { mkdir, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { homedir } from "node:os";
const require = createRequire(import.meta.url);
const { chromium } = require(
  process.env.WORKFLOW_PLAYWRIGHT_PATH ||
    join(homedir(), ".cache/webprobe/runtime/node_modules/playwright"),
);
const result = await build({
  stdin: {
    contents: `
    import React, {useState} from 'react';
    import {createRoot} from 'react-dom/client';
    import {Room} from './frontend/mission/Room';
    const count = Number(new URLSearchParams(location.search).get('count') || 12);
    const nodes = Array.from({length:count}, (_, i) => ({id:'worker-'+i, actorId:'actor-'+i, taskId:'synthetic',
      label:'작업자 '+i+' 긴 이름과 실제 할당 역할 확인', role:i%2?'review':'implement',
      kind:'agent', status:'running', x:0,y:0}));
    function App() {
      const [selected, setSelected] = useState(null);
      const [motion, setMotion] = useState(true);
      const [status,setStatus]=useState("running");
      const [known,setKnown]=useState(true);
      window.setWorkerState=(status,known=true)=>{setStatus(status);setKnown(known)};
      const displayNodes=nodes.map((n,i)=>({...n,status:status==="mixed"?["running","idle","failed"][i%3]:status}));
      const [, rerender] = useState(0); window.rerender = ()=>rerender(n=>n+1);
      return <><button id="last" onClick={()=>setSelected(nodes.at(-1).id)}>마지막 작업자</button>
        <button id="motion" onClick={()=>setMotion(!motion)}>움직임</button>
        <Room activityKnown={known} nodes={displayNodes} selected={selected} onSelect={n=>setSelected(n.id)} motion={motion} transfers={[]} visible={true}/></>;
    }
    createRoot(document.getElementById('root')).render(<App/>);
  `,
    resolveDir: process.cwd(),
    loader: "tsx",
  },
  bundle: true,
  write: false,
  define: { "process.env.NODE_ENV": '"production"' },
});
const html =
  '<!doctype html><meta charset="utf-8"><style>body{margin:0;background:#111d2d;color:white}.room-canvas{height:500px;width:100%;position:relative}.panel-heading{display:flex;flex-wrap:wrap;justify-content:space-between}.room-selection{overflow-wrap:anywhere}button{max-width:100%}</style><div id="root"></div><script src="/bundle.js"></script>';
const server = createServer((req, res) => {
  res.setHeader(
    "Content-Type",
    req.url === "/bundle.js" ? "text/javascript" : "text/html",
  );
  res.end(req.url === "/bundle.js" ? result.outputFiles[0].contents : html);
});
await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
let browser;
const measurements = [],
  errors = [];
const output =
  process.env.WORKFLOW_SCREENSHOT_DIR || "/tmp/workers-browser-evidence";
await mkdir(output, { recursive: true });
try {
  browser = await chromium.launch({
    headless: true,
    args: ["--enable-unsafe-swiftshader"],
  });
  const page = await browser.newPage({
    viewport: { width: 1440, height: 900 },
  });
  page.on("pageerror", (e) => errors.push(e.message));
  async function labels() {
    const boxes = await page
      .locator(".room-nameplate:visible")
      .evaluateAll((elements) =>
        elements.map((el) => {
          const r = el.getBoundingClientRect(),
            host = el.parentElement.getBoundingClientRect();
          return {
            x: r.x,
            y: r.y,
            right: r.right,
            bottom: r.bottom,
            inside:
              r.x >= host.x &&
              r.y >= host.y &&
              r.right <= host.right &&
              r.bottom <= host.bottom,
          };
        }),
      );
    assert(boxes.length > 0, "some names remain visible");
    assert(
      boxes.every((b) => b.inside),
      "labels are inside viewport",
    );
    for (let i = 0; i < boxes.length; i++)
      for (let j = i + 1; j < boxes.length; j++) {
        const a = boxes[i],
          b = boxes[j];
        assert(
          a.right <= b.x ||
            b.right <= a.x ||
            a.bottom <= b.y ||
            b.bottom <= a.y,
          "labels never overlap",
        );
      }
  }
  for (const count of [1, 4, 12, 24]) {
    await page.goto(
      "http://127.0.0.1:" + server.address().port + "/?count=" + count,
    );
    await page.waitForSelector(".room-nameplate:visible");
    await page.waitForTimeout(700);
    assert.equal(
      await page.locator(".room-nameplate").count(),
      Math.min(count, 12),
    );
    await labels();
    const nameplate = page.locator(".room-nameplate:visible").first();
    await nameplate.focus();
    const focusedName = await nameplate.getAttribute("aria-label");
    await page.evaluate(() => window.rerender());
    await page.waitForTimeout(150);
    assert.equal(
      await page.evaluate(() =>
        document.activeElement.getAttribute("aria-label"),
      ),
      focusedName,
    );
    await page.keyboard.press("Enter");
    await page.waitForTimeout(150);
    assert.equal(
      await page.evaluate(() =>
        document.activeElement.getAttribute("aria-label"),
      ),
      focusedName,
    );
    const sample = await page.evaluate(async () => {
      const values = [];
      let last = performance.now();
      await new Promise((resolve) => {
        function tick(now) {
          values.push(now - last);
          last = now;
          if (values.length < 91) requestAnimationFrame(tick);
          else resolve();
        }
        requestAnimationFrame(tick);
      });
      values.shift();
      values.sort((a, b) => a - b);
      const host = document.querySelector("canvas").parentElement;
      const gl = document.querySelector("canvas").getContext("webgl2");
      const ext = gl.getExtension("WEBGL_debug_renderer_info");
      return {
        medianMs: values[45],
        p95Ms: values[85],
        drawCalls: Number(host.dataset.drawCalls),
        triangles: Number(host.dataset.triangles),
        renderer: ext
          ? gl.getParameter(ext.UNMASKED_RENDERER_WEBGL)
          : "unknown",
      };
    });
    measurements.push({
      inputWorkers: count,
      displayedWorkers: Math.min(count, 12),
      ...sample,
    });
    assert(sample.drawCalls > 0 && sample.drawCalls < 700);
    await page.locator("#last").click();
    await page.getByRole("button", { name: "선택한 작업자 가까이" }).click();
    await page.waitForTimeout(300);
    await labels();
    assert.equal(
      await page
        .locator('.room-nameplate[aria-pressed="true"]:visible')
        .count(),
      1,
    );
    await page.screenshot({ path: join(output, "workers-" + count + ".png") });
    await page.getByRole("button", { name: "시점 초기화" }).click();
    await page.setViewportSize({ width: 390, height: 844 });
    await page.waitForTimeout(300);
    await labels();
    const canvasBox = await page.locator("canvas").boundingBox();
    await page.mouse.move(
      canvasBox.x + canvasBox.width / 2,
      canvasBox.y + canvasBox.height / 2,
    );
    await page.mouse.down();
    await page.mouse.move(
      canvasBox.x + canvasBox.width / 2 + 80,
      canvasBox.y + canvasBox.height / 2 + 30,
      { steps: 8 },
    );
    await page.mouse.up();
    await page.mouse.wheel(0, 300);
    await page.waitForTimeout(300);
    await labels();
    await page.locator("#motion").click();
    await page.waitForTimeout(500);
    const a = await page.locator("canvas").screenshot();
    await page.waitForTimeout(300);
    const b = await page.locator("canvas").screenshot();
    assert(a.equals(b), "disabled motion renders a stable scene");
    for (const [status, known, zone] of [["idle",true,"lounge"],["failed",true,"attention"],["running",false,"attention"],["running",true,"work"]]) {
      await page.evaluate(([status, known])=>window.setWorkerState(status,known),[status,known]);
      await page.waitForTimeout(250);
      assert((await page.locator('[data-zone="'+zone+'"]').innerText()).endsWith(String(Math.min(count,12))));
      await page.getByRole("button",{name:"선택한 작업자 가까이"}).click();
      await page.waitForTimeout(200);
      await labels();
      assert.equal(await page.locator('.room-nameplate[aria-pressed="true"]:visible').count(),1);
      if (count===12 && status==="idle") await page.screenshot({path:join(output,"state-room-lounge.png")});
    }
    await page.setViewportSize({ width: 1440, height: 900 });
    if(count===12){
      await page.evaluate(()=>window.setWorkerState("mixed"));
      await page.getByRole("button",{name:"시점 초기화"}).click();
      await page.waitForTimeout(500);
      for(const zone of ["work","lounge","attention"]) assert((await page.locator('[data-zone="'+zone+'"]').innerText()).endsWith("4"));
      await labels();
      await page.screenshot({path:join(output,"state-room-mixed.png")});
    }
  }
  assert.deepEqual(errors, []);
  const report = {
    passed: true,
    environment:
      "Headless Chromium / SwiftShader; 90 rAF intervals per case, not GPU frame time or hardware FPS",
    measurements,
  };
  await writeFile(
    join(output, "workers-performance.json"),
    JSON.stringify(report, null, 2),
  );
  console.log(JSON.stringify(report));
} finally {
  await browser?.close();
  await new Promise((resolve) => server.close(resolve));
}
