# v0.3 실제 프로젝트 실행: R29 웹 fixture

2026-10-10 KST · 작업 `KB-WEB-FIXTURE-R29` · **로컬 완료**

Katakuri의 최신 인계에 남아 있던 웹 시험 데이터 불일치를 v0.3으로 수정했다.
한 번 등록한 프로필에서 작업 폴더 생성, 환경 확인, Claude 구현, 실제 검사,
Codex 독립 리뷰, 인계와 작업일지 생성까지 연결했다.
실행 시작 후 제어자의 코드 수정, 결과 전달, 재개 지시는 **0회**였다.

## 무엇을 고쳤나

R29가 병합된 코드에 웹 위험 게이트 fixture의 R28 표기가 남아 있었다.
`quantityLimit.binding`과 `freshnessGate.binding`의 실행 세대 ID와 선언 경로를
R29로 갱신했다. 변경은 `web/fixtures/risk-gate.json` 한 파일의 네 필드다.
기존 정책의 `boundContentFp`와 다른 값은 그대로 유지했다.

원본 main에 직접 쓰지 않았다. 도구가 커밋 `13b10f2b`에서 별도 worktree를 만들었다.
이전 `KB-O-CURRENT-AUDIT` 시험의 작업 폴더와 부분 변경도 보존했다.

## 실제 확인 결과

| 항목 | 결과 |
|---|---|
| 변경 전 생성기 대조 | `risk-gate.json: CONTENT_DRIFT`, 종료 코드 1 |
| 프로필 환경 준비 | Python과 기존 오프라인 API 검사 의존성 확인, 종료 코드 0 |
| 변경 후 생성기 대조 | 실제 FastAPI 응답과 fixture 16개 일치, 종료 코드 0 |
| 웹 경계 시험 | 24 passed, 1 skipped, 종료 코드 0 |
| 건너뛴 시험 | `web/.next`가 없어 빌드된 번들 검사는 미실행 |
| 독립 리뷰 | 별도 Codex CLI 프로세스의 PASS |
| 에이전트 호출 | Claude 구현 1회, Codex 리뷰 1회 |
| 수정 반복 / 자동 복구 / 수동 복구 | 각각 0회 |
| 완료 당시 근거 | 검사, 리뷰, 완료의 현재 지문 일치 모두 `true` |
| 인계와 작업일지 | 도구가 자동 생성, 작업일지 형식과 문체 검사 통과 |
| 대시보드 | 실제 `/api/state`에서 프로필과 완료 작업 확인 |

이 기록의 “수동 개입 0회”는 `run` 시작부터 로컬 완료까지의 구간을 뜻한다.
사용자 요청 해석, 작업 선정, 프로필 등록, 실패 기준 확인은 제어자가 준비했다.
전체 프로젝트가 스스로 일감을 정하거나 모든 개발 작업을 무인으로 끝낸다는 뜻은 아니다.

UI 소스와 런타임 코드를 변경하지 않아 새 브라우저 측정이나 전체 웹 빌드는 하지 않았다.
검사는 임시 데이터베이스의 실제 앱 응답을 사용했다. 운영 API와 LS는 호출하지 않았다.

## 현재 위치와 사용법

- 프로필: [`../../profiles/katakuri-web-fixtures.json`](../../profiles/katakuri-web-fixtures.json)
- 프로필 등록과 재사용: [`../../profiles/README.md`](../../profiles/README.md)
- 작업 폴더: `~/.local/state/agent-workflow/workspaces/katakuri-web-fixtures/KB-WEB-FIXTURE-R29`
- 브랜치: `workflow/o/KB-WEB-FIXTURE-R29`
- 원장과 인계: `~/.local/state/agent-workflow/tasks/KB-WEB-FIXTURE-R29/`
- 자동 작업일지: `artifacts/worklogs/2026-10-10_KB-WEB-FIXTURE-R29.md`
- 대시보드: `http://127.0.0.1:8776`

```bash
agent-workflow show KB-WEB-FIXTURE-R29
agent-workflow profile show katakuri-web-fixtures
```

완료한 작업에 다시 구현을 요청하지 않는다. 새로운 목표는 새 ID로 등록한다.
프로필 파일 수정은 이미 등록된 작업의 사본을 변경하지 않는다.

## 통합 범위

10월 10일 로컬 완료 당시 Katakuri 변경은 **커밋·푸시·병합·운영 배포 전**이었다. 통합에 사용할 검증 결과와
[실제 패치](changes.patch)를 보존했다. 혼합 TODO의 런북, 알림, 운영 반영은 남아 있다.
이 시험 데이터 갱신은 R29 운영 반영이나 거래 실행을 뜻하지 않는다.

원장과 자동 인계는 검증된 프로젝트 밖에 생성했다. 리뷰 후에 프로젝트 문서를 덧붙여
검사한 코드 지문을 바꾸지 않았다. [실행 브리프](execution-brief.md)에 허용한 프로젝트
기록 파일은 이번 실행에서 추가하지 않았다. 최신 완료 근거는 [결과](result.json)와
원장의 `handoff/state.json`이다.

기존 v0.2 파일럿은 수동 복구 1회를 포함한다. 이번 성공으로 이전 기록을 소급 변경하지 않는다.

## 2026-10-11 병합 확인

[PR #166](https://github.com/89sooner/katakuri-band/pull/166)이 `main`에 병합됐다.
병합 커밋은 `2c8e83bfcab2fcd0f396f2e1c7097a7f99792fb0`이다. 변경은 기존 한 파일의
네 필드이며, 최종 병합본의 별도 LF worktree에서도 fixture 16개 일치와 웹 경계 시험
24 passed, 1 skipped를 확인했다. 임시 검사 worktree는 정리했다.

GitHub의 check run과 commit status는 각각 0개다. 원격 CI 통과를 주장하지 않는다.
등록한 로컬 검사 결과로 확인한 범위와 bundle 검사 skip은 위와 같다.
운영 배포 명령은 설정하지 않았다. 기존 [로컬 완료 결과](result.json)는 당시 기록으로
보존하며, [별도 통합 기록](integration.json)에 실제 병합과 최종 검사 근거를 남겼다.
