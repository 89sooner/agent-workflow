// Independent behavioral acceptance check; all rendering is in memory.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const Module = require('node:module');
const root = '/home/roqkf/katakuri-band-o-audit-current-link/web';
const rq = Module.createRequire(root + '/package.json');
const ts = rq('typescript');
for (const extension of ['.ts', '.tsx']) Module._extensions[extension] = function (mod, file) {
  mod._compile(ts.transpileModule(fs.readFileSync(file, 'utf8'), { compilerOptions: {
    module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2022
  } }).outputText, file);
};
const React = rq('react');
const { renderToStaticMarkup } = rq('react-dom/server');
const { JSDOM } = rq('jsdom');
const { KillSwitchPanel } = rq('./components/risk/KillSwitchPanel.tsx');
const { parseRiskGateState } = rq('./lib/api/validate.ts');
const data = parseRiskGateState(rq('./fixtures/risk-gate.json'));
let cases = 0;
for (const state of ['ON', 'OFF']) {
  for (const auditId of ['current_audit-01', undefined]) {
    const { auditId: _, ...base } = data.killSwitch;
    const gate = { ...data, killSwitch: { ...base, state, ...(auditId ? { auditId } : {}) } };
    const document = new JSDOM(renderToStaticMarkup(React.createElement(KillSwitchPanel, {
      state: { status: 'ready', data: gate, failure: null, receivedAt: '2026-10-08T00:00:00Z' }
    }))).window.document;
    const field = document.querySelector('[data-region="current-state"] [data-field="auditId"]');
    assert.ok(field, 'current-state audit field must remain');
    const link = field.querySelector('a');
    if (auditId) {
      assert.ok(link, 'Current-state audit ID must link to the audit list');
      const url = new URL(link.getAttribute('href'), 'http://fixture.invalid');
      assert.equal(url.pathname, '/admin/access');
      assert.equal(url.searchParams.get('auditAction'), 'KILL_SWITCH_CHANGE');
      assert.equal(url.searchParams.get('auditId'), auditId);
      assert.equal(field.querySelector('code').textContent, auditId);
      assert.equal(link.target, '_blank');
      assert.ok(link.relList.contains('noopener') && link.relList.contains('noreferrer'));
    } else {
      assert.equal(link, null);
      assert.match(field.textContent, /변경 기록 없음/);
    }
    cases++;
  }
}
console.log(JSON.stringify({ checked: cases, result: 'PASS', scope: 'A-002 current audit ID ON/OFF, present/absent, URL and new-tab contract' }));
