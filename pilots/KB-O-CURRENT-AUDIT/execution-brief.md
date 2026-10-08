# O 트랙 파일럿 — A-002 현재 감사 ID 링크

사용자가 이 작은 후속 작업의 실행을 승인했다. 준비에서 끝내지 말고 아래 로컬 범위를 구현한다.

## 목표

A-002 긴급 제어의 **현재 상태**에 표시되는 감사 ID에서 기존 A-003 감사 목록을 새 탭으로 연다. 이미 있는 두 단계 결과 상자의 링크와 같은 `AuditIdRef`/`RESULT_AUDIT_ACTION.killSwitch` 경로를 재사용한다.

- 실행 worktree: `/home/roqkf/katakuri-band-o-audit-current-link`
- branch: `feat/top-current-audit-link`
- baseline: `f5e77a8bd81fab309bd61fa792019c98f76fb6b5`
- 도구 작업 ID: `KB-O-CURRENT-AUDIT` (새 WP/CR/ADR 번호가 아님)
- 현재 사용자 지시: “진행해봐라” — 추천한 A-002 링크 작업을 진행. 커밋·푸시·병합·운영 반영은 이 로컬 파일럿 범위 밖.
- 이 코드베이스에는 다른 트랙이 있다. 이 워크트리 밖에서 수정하거나 다른 트랙의 변경을 되돌리지 않는다.

## 근거와 읽기 범위

1. 루트 `AGENTS.md`, `REFACTORING_SUPREME_DIRECTIVE.md` §8, `docs/00_governance/parallel_work_protocol.md` §3~6, `docs/README.md`의 규율을 따른다.
2. `agent-context/todos.md`의 “현재 상태 칸의 마지막 적용 감사 ID” 미수행 항목(기준 HEAD 759행).
3. `web/components/risk/KillSwitchPanel.tsx`의 `CurrentState`, `[data-field="auditId"]`(기준 HEAD 190행), 기존 결과 상자 `AuditIdRef` 사용 예.
4. `web/components/audit/AuditIdRef.tsx`, `web/lib/audit/audit-link.ts`의 기존 URL/새 탭 계약.
5. `web/tests/risk.render.test.tsx`, `risk.control.test.tsx`, `audit.link.test.tsx`의 기존 fixture와 시험.

상위 목표는 위험을 통제하며 운용 가능한 플랫폼 품질 개선이고 실행 동일성 원칙은 보존한다. 공식 경로는 현재 상태 감사 ID → 기존 A-003 읽기 화면이며, 줄일 불일치는 결과 상자에만 있던 탐색 경로다. 새 generation은 필요 없다. 엔진·주문·LS 호출·배포는 하지 않는다. 완료 후 다음 통합 단계는 로컬 결과의 허브 검토다. 시작 선언을 세션 노트에도 남긴다.

## 구현 범위

프로덕션 소스 변경은 `web/components/risk/KillSwitchPanel.tsx` **하나**로 제한한다. 새로운 추상화·API·상태·CSS·dependency를 만들지 않는다. `AuditIdRef`는 이미 import돼 있으며 `of`는 string이다. 현재 상태에는 예를 들어 `of="current"`처럼 결과 상자와 구분되는 값을 사용한다. 기존 `data-field="auditId"`를 보존한다.

- ON/OFF 모두 감사 ID가 실제 응답에 있으면 그 ID를 그대로 보인다.
- 링크는 `auditAction=KILL_SWITCH_CHANGE`와 같은 `auditId`를 사용한다.
- `_blank`, `noopener noreferrer`를 유지한다.
- ID가 없으면 기존 “변경 기록 없음” 표기를 유지하고 링크를 만들지 않는다.
- 읽는 중·실패 시 과거 감사 ID가 현재 상태로 남지 않게 기존 data 흐름을 보존한다.
- 링크 클릭은 신규 제안/확정/취소/자동 재시도/권한 변경을 발생시키지 않는다. 원래 탭의 미입력/입력 중/확인 중 상태를 보존한다.
- A-001 정책의 감사 링크 확장은 이번에 하지 않는다. 감사 화면 세션 오류 문구도 별도 후보이므로 건드리지 않는다.

## 시험과 완료

필요한 회귀 시험은 `web/tests/risk.current-audit-link.test.tsx`에 추가하거나 기존 위험 화면·링크 시험 파일에서 좁게 추가한다. 실제 컴포넌트/스크립트 API를 이용해 양성·음성·상태 보존과 변경성 요청 0을 검증한다. 기존 시험을 삭제/약화하거나 fixture 값으로 결함을 숨기지 않는다.

제어기가 다음을 자동 실행한다. Claude 구현 세션은 파일 도구만 제공하므로 직접 Bash를 호출할 필요가 없다.

1. 독립 수용 검사 `verify-link.cjs`: 변경 전에는 “Current-state audit ID must link” 단언으로 실패했다. ON/OFF × ID 유무, 정확 URL, 새 탭 계약을 실제 컴포넌트의 메모리 렌더로 검사한다.
2. 관련 Vitest 3개 + 새 시험 파일.
3. 웹 typecheck.
4. 변경 범위에 해당하는 ESLint.
5. 허용 파일 범위와 구현/시험 변경 여부 검사.

`/home/roqkf/agent-workflow/pilots/KB-O-CURRENT-AUDIT`의 계약·검사 파일은 고치지 않는다. 실패하면 실제 제품/시험 수정 또는 정확한 차단 사유로 답한다. 구현 뒤 outcome=ready를 반환하면 제어기가 검사와 Codex 독립 리뷰를 연결한다.

## 기록 범위

준비자가 `agent-context/todos.md` 맨 위에 이 O 트랙의 A-002 소유 블록을 넣었다. 그 블록은 이번 작업의 것으로 보존/갱신할 수 있다. 과거 다른 트랙의 블록은 수정하지 않는다.

실제 구현 내용을 `agent-context/sessions/2026-10-08-current-audit-link.md`에 기록하고, 필요하면 `session-handoff.md` 맨 위에 짧은 자기 블록을 덧붙인다. A-002만 구현했으므로 기존 A-001 등을 포함하는 backlog 전체를 완료로 바꾸지 않는다.

검사·리뷰가 아직 끝나지 않았다면 완료했다고 쓰지 않는다. 최종 검사·리뷰의 실행 증거는 agent-workflow 원장과 외부 handoff가 기록한다. 로컬 구현 기록에는 그 작업 ID를 가리킨다. 공유 CR/ADR/구현 원장 번호는 이번에 채번하지 않는다(커밋/병합 범위 밖).
