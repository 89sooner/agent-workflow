# 실제 v0.3 WP — R29 웹 시험 데이터 정합성

## 목표와 권한

사용자가 남은 자동화를 이어서 실행하도록 요청했다. 현재 Katakuri 인계와 TODO에
남은 작은 O 트랙 작업을 `agent-workflow` v0.3의 프로젝트 프로필로 끝낸다.
작업 ID `KB-WEB-FIXTURE-R29`는 도구의 실행 식별자이며 새 제품 WP/CR/ADR 번호가 아니다.

현재 main과 origin/main은 `13b10f2b7a12e62bf224dd7f5cb5b4a3b66cc7a6`다.
준비 중 PR #164가 병합되어 최초 관찰한 `72b0a21f`에서 이동했다.
변경 전 새 기준을 확인했으며, risk-gate.json의 네 필드가 R28인 상태는 그대로다.
열린 PR은 준비 시점에 0개였다. 원본 main과 과거 감사 링크 worktree는 수정하지 않는다.

## 좁은 변경 범위

- 제품 데이터는 `web/fixtures/risk-gate.json` 한 파일만 변경한다.
- `agent-context/todos.md`와 `agent-context/session-handoff.md`에는 자기 블록만 맨 위에 추가할 수 있다.
- 상세 기록은 `agent-context/sessions/2026-10-10-web-fixture-r29.md`에만 작성한다.
- 다른 트랙의 코드와 기록을 되돌리지 않는다. 이 저장소에는 다른 작업자가 있다.
- 엔진, 선언, 핀, 운영 API, 생성기, 테스트 기대값, 의존성을 수정하지 않는다.
- LS/계좌/주문/운영 서버에 접속하지 않는다. push/merge/deploy는 이 로컬 실행의 범위 밖이다.
- 새로운 generation이나 공유 원장 번호를 만들지 않는다.

## 근거

1. 루트 AGENTS.md와 최상위 지시문 §8의 시작 선언을 따른다.
2. `agent-context/todos.md`의 R29 이후 `web/fixtures/risk-gate.json` 재생성 항목을 읽는다.
3. R 트랙이 남긴 `agent-context/p4b0_artifacts/r29_call_limit_consumption_20261009/logs/web_fixture_regeneration_delta_for_o_track.diff`를 읽는다.
4. `scripts/generate_web_fixtures.py`의 `live_responses`, `check`, `write`를 읽는다.
5. `tests/test_web_boundary.py::test_15_fixtures_equal_live_fastapi_responses_byte_for_byte`를 읽는다.

이 fixture의 정본은 임시 데이터베이스에서 생성한 실제 FastAPI 응답이다.
정본 응답을 기존 fixture와 바이트 단위로 비교하는 검사를 유지한다.
두 곳의 `executionGenerationId`와 `declarationPath`만 R28에서 R29로 바뀐다.
선언 경로는 `tools/live/hsi_ls_stage_exec_r29_execution_generation_declaration_wp_top_043.json`이다.
그 밖의 값은 그대로 보존한다. 다른 fixture도 변해야 한다면 원인과 추가 범위를 보고한다.

## 구현 방법과 완료 근거

Claude 실행에는 파일 도구만 있다. 기존 diff와 선언을 읽고 해당 네 필드를 정확히 갱신한다.
직접 셸을 호출할 필요는 없다. 제어기가 실제 생성기 대조와 전체 웹 경계 시험을 실행한다.
문서에는 구현과 검사 대기를 구분한다. 아직 하지 않은 검사나 리뷰를 통과했다고 쓰지 않는다.
시험 데이터만 갱신하므로 화면 새 기능, 운영 배포, R29 운영 수집기 전환으로 표현하지 않는다.
기존 혼합 TODO 항목 전체를 완료로 바꾸지 않는다. 런북, 알림, 운영 반영은 별도 잔여 항목이다.

성공 조건:

1. `scripts/generate_web_fixtures.py --check`가 전체 fixture의 실제 응답 일치를 확인한다.
2. 기존 웹 경계 시험이 통과한다. 빌드 산출물 부재로 발생하는 bundle 검사 skip은 그대로 보고한다.
3. 제품 변경은 risk-gate.json의 네 필드로 한정한다.
4. 별도 Codex 리뷰가 검사와 범위를 확인한다.
5. 작업 원장, 인계, 자동 작업일지를 실제 결과에 연결한다.

시작 선언의 요지: 기존 전략 동일성을 유지하며 Ops API 응답과 웹 시험 데이터의 불일치를
줄인다. 공식 연결 구간은 R29 선언을 표시하는 기존 API 응답에서 웹 fixture까지다.
검증은 실제 생성기 대조와 웹 경계 시험이다. 새 generation은 필요 없다.
다음 통합 단계는 검증한 변경의 허브 인계다.
