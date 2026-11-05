# 관측 API 계약 — WP-OBS-01

> 상태: baseline (2026-11-05 사용자의 순차 구현 지시에 따른 REL-01 기반 계약)

상위 범위: [구현 계획](OBSERVATORY_IMPLEMENTATION_PLAN.ko.md). 구현 상태와 호스트 실증 여부는 별개다.

## 식별과 상태

Native 세션 키는 기존 SHA256(provider + NUL + source + NUL + native_id)의 앞 32자다. workflow는 ledger_id/task_id/run_id를 유지한다. UI 집계는 두 엔터티를 구분한다.

현재 공급자별 지원은 실제 소스 기준이다. 아래는 이벤트 계약 지원이며 현재 호스트에서 실증 완료했다는 뜻이 아니다.

| 경로 | 수신 방식 | 활동·대기 | 설치·활성 진단 |
|---|---|---|---|
| Claude direct/plugin | SessionStart, UserPromptSubmit, Pre/PostToolUse, PermissionRequest, Stop, SessionEnd, SubagentStart/Stop, Notification 등 | 이벤트 매핑된 running/input/approval/attention/ended | direct 명령 일치 검사 가능, plugin 로딩·호스트 활성은 unknown |
| Codex direct/plugin | SessionStart/End, UserPromptSubmit, Pre/PostToolUse, PermissionRequest, SubagentStart/Stop, Stop, Interrupt, PreModelRequest | 이벤트 매핑. 호스트 이벤트 미지원이면 미관측 | direct 명령 검사 가능. 프로젝트 신뢰·/hooks 검토는 자동 보증 불가 |
| OpenCode API | 등록된 로컬 서버의 세션·질문·승인 목록 GET | 조회 성공 범위에서만 현재 상태 | 훅 설치 not-applicable, 서버 실시간 연결은 기존 소스 오류로 확인 |
| OpenCode plugin | allowlist metadata 이벤트 | 관측된 활동·대기·종료, 발견 bootstrap은 unknown | 등록은 확인 가능, 실제 로딩은 unknown |

Native 훅 마지막 수신 300초 이상이면 현재 상태 unknown. ended는 기록 종료로 보존한다. 마지막 이벤트는 현재 호스트 활성화의 증거가 아니다. tool input/output·프롬프트·대화 전문은 수집하지 않는다.

## GET /api/connections/diagnostics

Query: `project`는 서버 컴퓨터의 절대 경로(삭제된 경로 허용), `provider`는 claude/codex/opencode. 읽기 전용·observer·manage에서 호출 가능하며 같은 origin 정책을 따른다. 네트워크·프로세스 실행·훅 설치·원장 생성 없이 로컬 증거만 읽는다.

응답 예시:

```json
{"schema_version":1,"project":"/work/repo","provider":"codex","mode":"direct","last_seen":null,"steps":[{"id":"registration","status":"pass","reason":"프로젝트가 등록되었습니다.","action":"프로젝트 연결 또는 native install/register를 사용하세요."},{"id":"host_activation","status":"unknown","reason":"호스트 신뢰·설정 재로딩은 자동 확인하지 않습니다.","action":"Codex는 /hooks 검토, 다른 도구는 플러그인·훅 활성화와 새 세션을 확인하세요."}],"execution_started":false}
```

예시는 steps 일부를 생략했다. 정상 응답 순서는 project → registration → installation → host_activation → first_event. 설정 손상 시 project/registration까지만 반환하고 fail로 설명한다. 단계 상태: pass/fail/unknown/not-applicable. mode는 direct/plugin/api/null, first_event pass는 과거 수신 이력만 의미한다.

- 200: 실패 단계도 진단 데이터로 반환. 설정 파싱 실패는 내용을 노출하지 않음.
- 400: 상대 경로·미지원 공급자 등 잘못된 요청. 기존 `{ "error": "..." }` 형식.
- 400: 기존 GET origin 검사 거절 (기존 서버 동작 유지).
- 기존 `/api/native/sessions`, `/api/native/setup`, `/api/connections`, `/api/capabilities` 호환 유지.

## 이후 API의 결정 순서

`/api/overview`는 WP-OBS-03에서 다중 원장·native 식별을 보존하는 DTO를 추가한다. `/api/attention`은 WP-OBS-05에서 대기 전이 ID·시작 시각 계약을 추가한다. `/api/bookmarks`는 WP-OBS-13 범위다. 이 문서에서 아직 존재하지 않는 API를 제공 완료로 취급하지 않는다.

## 검사와 실제 호스트 확인

`tests/test_diagnostics.py`는 비파괴성, 공급자 격리, direct 훅 변경, 플러그인 등록과 실제 활성화 구분, 설정 손상을 검사한다. 새 진단 단계의 실제 세 도구 호스트별 UI 실증은 WP-OBS-04에서 수행하며, 그 전까지 미검증이다.

## WP-OBS-02 추가 필드

`/api/native/sessions`의 세션에 `state_since`(현재 관측 상태로 전이한 시각), `waiting_since`(fresh 확인 요청 상태의 시작 시각), `freshness_reason`을 추가한다. 시각은 epoch seconds 또는 null이다. 기존 DB에서 정확한 전이 근거가 없으면 null이며 last_seen으로 추정하지 않는다. 반복 동일 상태·tool 변경은 시작 시각을 바꾸지 않는다. stale이면 waiting_since는 null이고 observed_state와 state_since는 과거 근거로 보존한다.

사유 enum: observed / event_expired / not_registered / source_unavailable. 신선한 기록은 현재 호스트 실행을 보장하지 않는다. `session_transitions`는 관측 DB 쓰기 경로에서만 생성하며 읽기 전용 snapshot은 구형 DB를 변경하지 않는다.

## WP-OBS-03 통합 API — /api/overview

GET은 기존 origin/Host 검사를 따른다. 응답: schema_version=1, projects, items, errors, execution_started=false. project는 Git 공통 디렉터리 ID로 묶고 item은 managed/native를 구분한다. managed ID는 원장 ID와 task ID, native ID는 기존 관측 ID를 보존한다. href는 기존 /mission?ledger=...&task=... 또는 /sessions?session=... 이다. 원장마다 최근 1000작업까지 표시하며 초과를 errors로 알린다. OpenCode API는 native snapshot에서 집계해 중복 표시를 피한다. 원장과 native 중 한쪽 실패 시에도 다른 쪽을 표시한다.

## 활동·프로젝트 메타데이터

`/api/native/sessions`는 `observed_at`(조회 시작 시 서버 epoch seconds), 각 세션의 `project_id`, `activity`를 추가한다. 프로젝트 ID는 Git 공통 디렉터리 기준이며 원래 `project` 경로를 유지한다. 계산은 30초 단위의 제한된 캐시를 사용한다.

`activity`는 신선한 도구 시작 이벤트에서만 reading/writing/command로 분류한다. 종료 이벤트 뒤에는 working, 미지원 도구는 working, 오래된 관측은 unknown이다. 입력·승인·종료 등 명시적 상태는 그대로 분리한다. 내부 도구 입력이나 모델 사고로 활동을 추정하지 않는다.

알림은 첫 조회를 기준으로 삼는다. 서버 `observed_at` 이후 새로 생긴 첫 요청은 알리고, 그 이전 상태가 늦게 발견되거나 복구된 것은 과거 알림으로 재생하지 않는다. 같은 origin의 여러 탭만 중복 억제 대상이다.

## 결과 근거 — /api/native/evidence

GET `?session=관측ID`는 명시적으로 연결된 근거만 반환한다. POST는 기존 origin·CSRF 검사와 관측 설정 권한을 사용한다. 일반 read-only 서버에서는 거절하며 observer는 자신의 관측 메타데이터만 변경한다.

- 추가: `{action:"add",session,kind:"workflow",ledger,task}` 또는 `{action:"add",session,kind:"pr",url}`.
- 해제: `{action:"remove",session,id}`.
- workflow는 현재 연결 allowlist의 원장만 read-only로 조회한다. task의 기록된 브랜치·검사 결과이며 현재 코드 재검증을 뜻하지 않는다. 검사 결과가 없으면 null이다.
- PR은 query·fragment·사용자 정보가 없는 `https://github.com/OWNER/REPO/pull/NUMBER`만 허용한다. 외부 PR API는 호출하지 않으며 상태는 미조회다. Enterprise 호스트는 현재 미지원이다.
- 중복 추가는 같은 ID를 반환한다. 전체 근거 연결은 1000개 제한이다. 원장 접근이 사라져도 연결을 자동으로 다른 작업에 귀속하지 않는다.

저장소는 관측 home의 `observer-metadata.sqlite3`(schema version 1, 권한 0600)다. workflow DB와 별개이며 작업·큐·worker를 생성하지 않는다.

## 북마크 — /api/native/bookmarks

GET은 저장된 북마크와 원본 근거의 만료·조회 상태를 반환한다. POST는 근거 연결과 같은 권한 경계를 따른다.

- 추가: `{action:"add",session,label,first_seq,last_seq}`. 이름 1~120자, 같은 세션에 남아 있는 이벤트 200개 이내 구간을 선택한다.
- 삭제: `{action:"remove",id}`. 원본 세션이 만료돼도 북마크를 삭제할 수 있다.
- 전체 북마크는 500개 제한이며 동일 이름·세션·구간 추가는 중복 저장하지 않는다.
- GET `/api/native/bookmarks/export?id=...`는 JSON attachment로 bookmark 메타데이터와 seq/at/event/state/tool만 반환한다.
- 처음·끝·중간 이벤트 또는 원본 세션이 사라지면 만료로 표시하고 export.complete=false다. 원본 DB 접근 실패는 만료로 단정하지 않고 미확인으로 표시한다. 대화 전문·tool input/output·인증정보는 포함하지 않는다.

## 분석 — /api/native/metrics

GET query: `days=1|7|30`, 선택적 `project=공통프로젝트ID`, `provider=claude|codex|opencode`. 로컬 관측 DB만 읽으며 원격 모델이나 OpenCode에 새 작업을 요청하지 않는다.

응답에는 기간, 조회 세션 수, durations_seconds(working/human_waiting/attention/idle/retrying/unknown), 결과가 기록된 도구 수·오류 수·오류율·도구별 분모, 결과 미확인 완료 수, 기록 없음·시계 이상 제외 수, 보관 한도 도달 여부를 포함한다.

시간은 이벤트 이후 최대 300초까지만 인정하고 나머지는 unknown으로 분리한다. 동시 세션 시간은 각각 합산한다. 반복 API 조회 사이 활동 지속을 보장하지 않는 보수적 지표다. 현재 결과 판정은 Claude의 명시적 PostToolUse/PostToolUseFailure만 지원한다. 다른 공급자의 완료 기록은 결과 미확인으로 집계하며 오류율을 0으로 만들지 않는다. 분모가 없으면 오류 수·율은 null이다.

비용·토큰 수집은 사용자의 2026-11-05 보류 결정에 따라 포함하지 않는다.

### 통합 검토 후 보존·정리 계약

- `GET /api/native/evidence`는 전체 근거 연결 최대 1000개를 반환한다. `session`이 있으면 해당 ID로 한정한다. 원본 세션이 정리돼도 조회·삭제할 수 있다. 신규 연결만 현재 존재하는 원본 세션을 요구한다.
- 북마크 신규 생성 시 `provider`, `source`, `native_id`를 함께 저장·내보낸다. 이전 DB는 쓰기 시 열을 추가하며 기존 값은 null(미기록)로 둔다. 조회만으로 마이그레이션하지 않는다.
- `/sessions?session=…&bookmark=…`는 비동기 세션 조회와 URL 동기화 뒤에도 지정한 북마크 구간을 복원한다.

- 근거 GET의 `ledgers`는 현재 홈 및 `connections.json`의 명시 등록 원장만 포함한다. 프로세스·임시 경로·기본 경로 자동 발견은 근거 연결 허가가 아니다. POST와 기존 연결 조회도 현재 등록을 다시 검증하며, 연결 해제 후에는 자동 발견·캐시와 관계없이 접근 불가다. 등록 설정 오류는 `sources_error`로 표시하고 메타데이터 정리 자체는 허용한다.

- 분석 응답의 `retention`은 전체 관측 저장소의 `max_sessions`, `max_events`, `retained_sessions`, `retained_events`, `session_limit_reached`, `event_limit_reached`를 제공한다. 어느 한도라도 현재 도달하면 `retention_limit_reached=true`다. 선택한 프로젝트·공급자가 비어도 저장소 전체 경고는 표시한다.
- 시계 역전 세션은 시간 합산에서 제외하되 유효한 기간 내 시각의 명시적 도구 결과는 별도로 집계한다. 시각 자체가 유효하지 않은 결과는 기간에 귀속하지 않는다.
- 북마크 이벤트 번호는 SQLite 정수 범위(1~2^63-1) 안에서 검증하며 초과 입력은 HTTP 400이다.
