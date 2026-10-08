# PRS-WP061 실행 계약 — 준비됨 / 구현 미착수

이 문서는 기존 **PR Search WP-061 전체의 로컬 구현·검증·문서 동기화**를 한 번 수행하기 위한 실행 입력이다. 새 WP나 승인된 제품 범위를 만들지 않는다. 준비 요청만으로 실행하지 않으며, 사용자가 이 작업의 실행을 지시하거나 큐에 넣은 뒤 적용한다.

## 기준과 대상

- 작업 ID: `PRS-WP061` / 원래 WP: `WP-061 의미 capability 제약 엔진`
- 소스 정본: `/home/roqkf/pr-search` (이 체크아웃에서는 작업하지 않음)
- 실행 워크트리: `/home/roqkf/pr-search-wt/agent-workflow-wp061`
- 브랜치: `pilot/agent-workflow-wp061`
- 준비 시 HEAD와 원격 main: `2748b9c3831cc7701596bbcdac0ffb9f5915ab6a` (열린 PR 0 확인)
- SRS: baseline v2.55. FR-GH-003은 approved, Must, CR-005/CR-008의 승인 범위.
- 구현 담당: Claude / 독립 검토: Codex. 자동 실행에서는 Claude에 파일 읽기·편집 도구만 있고, 아래 검사 명령은 제어기가 실행한다.
- **준비/기존 테스트 통과는 WP 완료가 아니다.** 준비 시 WP-061은 in_progress이며 그대로 유지했다.

## 반드시 읽을 근거 — 전체 이력 대신 필요한 절부터

각 경로는 실행 워크트리 기준. 현재 source가 복사된 과거 보고보다 우선이다.

| 정본 | 위치 | 읽을 내용 |
|---|---|---|
| 프로젝트 지침 | `AGENTS.md`, `CLAUDE.md`, `docs/README.md` | 변경·문서·검증 규율 |
| WP | `docs/40_delivery/pr_search_work_packages.md` 2788행부터 WP-061 절 | 범위와 완료 기준 5개 |
| 요구사항 | `docs/10_requirements/srs_final.md` 1383행부터 FR-GH-003 절 | AC-1~9, 특히 AC-7의 13종과 AC-8~9 |
| 결정 | `docs/30_technical_architecture/pr_search_architecture_decision_records.md` 937행부터 ADR-017 | 공용 제약/구조화 invocation/단일 빌더 |
| 진행 원장 | `docs/40_delivery/pr_search_implementation_traceability.md` 3장 WP-045·061·066 행 | 선행 구현과 아직 열리지 않은 기능 |
| 웹 지침 | `apps/web/AGENTS.md` | 웹 파일 변경 시 설치된 Next.js 문서 읽기 |

행 번호는 위 HEAD 기준이며 이동하면 제목으로 찾는다. 문서 변경 전 해당 디렉터리 AGENTS.md를 따른다.

## 확인한 현재 구현과 채워야 할 범위

- `packages/gh-cli/src/types.ts`: 관계 제약 union 9종. 옵션 모델은 enum/int/json_fields/bool 중심이다.
- `packages/gh-cli/src/constraints.ts`: 관계 판정 일부 구현. invocation은 positional/stdin/files를 거절하며 저장소가 항상 필요하고 host context는 true로 공급한다. min/max items는 JSON 필드에 구현돼 있다.
- `packages/gh-cli/src/argv.ts`: 기존 공용 argv 빌더.
- 소비자: `apps/web/lib/gh.ts`, `apps/search-api/src/gh/executions.ts`, `apps/gh-executor/src/runner.ts`.
- 기존 `pr.list`의 유효/무효 판정·argv·인가·감사·운영 정책을 보존한다.
- WP-045는 전체로는 in_progress다. 공용 manifest/인벤토리/분류 기반은 구현됐지만 실제 사내 호스트 지원·최종 parity는 이 파일럿에서 검증하거나 완료로 바꾸지 않는다.

**13종 각각의 유효·무효 입력, 값 정규화와 네 소비자의 일치가 전체 목표다.** 이미 있는 관계 판정을 다시 만드는 것보다 일반 입력·반복 값·컨텍스트 모델의 빠진 연결을 우선 확인한다. 단순 helper 테스트만 추가해 WP를 done으로 만들지 않는다.

## 경계

주요 변경 범위: `packages/gh-cli/src/**` 및 관련 시험, `apps/search-api/src/gh/**`와 통합 시험, `apps/gh-executor/src/**`와 관련 시험, `apps/web/lib/gh.ts`·관련 시험. 필요하면 승인 계약을 구현하는 가장 작은 타입/파서 연결만 변경한다.

- WP에 명시된 **폼 렌더링·실행기 신규 기능**은 제외. 기존 화면과 실행기의 검증 연결·회귀 검사는 포함.
- 새로운 gh capability의 실제 실행 허용, `pr.merge` 등 쓰기 capability 활성화, Recipe·workspace·파일 저장 서비스 신규 구축은 제외.
- 컨텍스트의 branch/workspace/GHES-version은 제약 평가에 필요한 사실/모델로 다룬다. 실제 사내 GHES 접근이나 호스트 지원 확인으로 대체하지 않는다.
- 인가·출력 제한·비밀 처리·운영 승인 정책을 완화하지 않는다. 운영 DB와 기존 컨테이너를 사용하지 않는다.
- 승인된 요구를 바꿔야만 구현 가능하다면 해당 충돌과 선택지를 기록한다. 의미를 임의로 축소해 통과시키지 않는다.
- 이 파일럿에서 커밋·푸시·PR·병합·배포는 하지 않는다. 범위는 로컬 WP 결과까지다. 원격에 쓰는 release-config는 없다.

## 완료 기준과 검사

| ID | 원래 WP DoD | 필요한 증거 |
|---|---|---|
| D1 | 13종 제약 전부 표현·평가 | 각 제약 valid/invalid 통과 사례. 경계값·빈 값·반복·누락 포함 |
| D2 | 동일 invocation/manifest → 동일 argv | 순서/반복/정규화에 대한 결정론 시험 |
| D3 | 클라이언트 우회 API 직접 호출도 같은 사유로 거부 | 실제 로컬 API 통합 시험; 값이 실행 큐에 도달하지 않음 |
| D4 | UI 판정과 서버 판정 불일치 시 실패 | 같은 입력 집합을 두 소비 경로에 전달하는 일치 시험 |
| D5 | 미리보기·실행이 같은 빌더 사용 | 두 경로의 결과 일치 시험 + 독립 검토자의 호출 경로 확인 |

검사 진입점: `/home/roqkf/agent-workflow/pilots/PRS-WP061/pilot.py gate <이름>`.
등록 순서: typecheck → lint → dependencies → unit → integration → a11y → regression → closure.
`integration`은 `awp-prs-wp061-*`라는 전용 PostgreSQL·Redis만 사용하고 필요한 경우 기동한다. 실제 HTTPS GHE 대역·고정 gh 바이너리를 쓰는 기존 통합 시험이다. 실제 사내 검증으로 보고하지 않는다.

검사별 실제 명령은 `pilot.py`의 GATES를 확인한다. unit/integration/a11y에는 JSON 결과가 함께 저장된다. **모든 소스와 문서 변경을 끝낸 뒤 final 검사를 실행한다.** 검사 뒤 코드를 고쳤으면 영향받은 검사뿐 아니라 이 파일럿의 closure가 요구하는 동일 지문 검사 묶음을 다시 만든다.

## 완료 증거 파일 작성

`/home/roqkf/agent-workflow/pilots/PRS-WP061/evidence/acceptance.json`을 마지막 소스 형상에 맞춰 작성한다. 현재는 이 파일이 없으며, closure가 의도적으로 실패한다.

```json
{
  "constraints": {
    "requires": {"valid": "실제 Vitest fullName", "invalid": "실제 Vitest fullName"}
  },
  "wp_dod": {"D1": ["실제 통과 시험 fullName"], "D2": [], "D3": [], "D4": [], "D5": []}
}
```

실행 계약·pilot.py·기준선 로그를 고쳐 검사를 완화하지 않는다. 이 준비 폴더에서는 evidence/acceptance.json과 새 실행 증거만 작성한다. 위 예시는 형식 설명이며 그대로는 실패한다. constraints의 필수 13키:
`requires, conflicts, oneOf, exactlyOne, atLeastOne, implies, repeatable, minItems, maxItems, enum, conditional_required, input_source, context`.
코드 지문은 제어기의 검사 영수증과 closure가 계산한다. 파일 도구만 가진 구현 담당이 셸 명령으로 지문을 계산할 필요는 없다. 각 valid/invalid는 서로 다른 실제 통과 시험 이름이어야 한다. 모든 D1~D5에 실제 통과 시험을 연결한다. 미래 실행 결과를 미리 채우지 않는다.

closure는 각 시험 이름이 실제 final JSON에 있고, 검사 영수증과 현재 코드 지문이 같으며, WP 문서와 구현 원장이 변경됐는지 확인한다. **시험 내용이 요구사항을 정말 증명하는지는 독립 검토자가 소스와 함께 판단한다.** 형식 검증을 완전성 증명으로 보고하지 않는다.

## 문서·인계·검토

기존 `pr_search_work_packages.md`와 `pr_search_implementation_traceability.md`의 WP-061 내용을 코드·증거와 동기화하고, 기존 `agent-context/` 읽기 순서에 맞춰 현재 결정·미완료·다음 행동을 남긴다. 부분만 구현됐으면 in_progress를 유지한다. 전체 DoD·필수 검사·독립 검토가 충족됐을 때만 완료를 보고한다. product baseline 자체의 변경은 이 실행 계약이 승인하지 않는다.

Codex 검토자는 전체 WP에 대해 다음을 확인한다: 13키가 단지 타입에 선언된 것이 아니라 실제 invocation에서 평가되는가, generic fixture와 현재 실행 허용 정책이 구분되는가, UI/API/재검증/빌더가 하나의 정의를 소비하는가, positive/negative 시험이 실제로 실패를 잡는가, 문서가 결과를 과장하지 않는가.

다른 일을 새로 할당하거나 구현 범위를 늘리지 않는다. 중요한 질문이 생기면 근거와 함께 blocked로 보고한다. 기존 성공을 반복 검토하기보다 현재 요구를 깨는 결함을 찾아 수정한다.
