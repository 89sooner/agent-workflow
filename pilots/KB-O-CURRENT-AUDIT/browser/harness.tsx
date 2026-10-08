import React from 'react';
import { createRoot } from 'react-dom/client';
import { KillSwitchPanel } from '/@fs/home/roqkf/katakuri-band-o-audit-current-link/web/components/risk/KillSwitchPanel.tsx';
import { parseRiskGateState } from '/@fs/home/roqkf/katakuri-band-o-audit-current-link/web/lib/api/validate.ts';
import { parseAuditLink } from '/@fs/home/roqkf/katakuri-band-o-audit-current-link/web/lib/audit/audit-link.ts';
import raw from '/@fs/home/roqkf/katakuri-band-o-audit-current-link/web/fixtures/risk-gate.json';
import '/@fs/home/roqkf/katakuri-band-o-audit-current-link/web/app/globals.css';
const counts = { prepare: 0, commit: 0, reread: 0 };
window.pilotCalls = counts;
const data = parseRiskGateState(raw);
const state = { status: 'ready', failure: null, receivedAt: '2026-10-08T00:00:00Z', data: { ...data, killSwitch: { ...data.killSwitch, state: 'ON', version: 2, auditId: 'audit_current-01', mutationAuthorized: true, reason: 'TEST_FIXTURE', updatedBy: 'fixture-risk' } } };
const session = { principalId: 'P-fixture', username: 'fixture-risk', role: 'Risk', sessionId: 'S-fixture', expiresAt: '2099-01-01T00:00:00Z', capabilityScopes: ['READ','RISK_APPROVE','MANAGE'], accountScopes: [], authorizationVersion: 1 };
const api = { source: 'http', prepareKillSwitch: async (input) => {
  counts.prepare++;
  return { ok: true, data: { confirmationId:'fixture-confirmation', requestedState:input.desiredState, expectedVersion:2, expiresAt:'2099-01-01T00:00:00Z' } };
}, commitKillSwitch: async () => { counts.commit++; throw new Error('This verification must not commit'); } };
const root = createRoot(document.getElementById('root')!);
if (location.pathname === '/admin/access') {
  root.render(<main><h1>격리 테스트: 감사 링크 수신</h1><pre id="parsed-link">{JSON.stringify(parseAuditLink(location.search))}</pre></main>);
} else {
  root.render(<main style={{maxWidth:1000,margin:'20px auto',padding:20}}><h1>격리 테스트: 실제 A-002 컴포넌트</h1><p>합성 상태·가짜 API만 사용한다. 운영 서버 요청 없음.</p><KillSwitchPanel state={state} session={session} api={api} onChanged={()=>counts.reread++}/></main>);
}
