# 통합 관제실 구현·검증 기록 — 2026-11-05

> 이 문서는 커밋·배포 전 개발 검증 시점의 기록이다. 이후 운영 반영은 [운영 절차](OPERATIONS.ko.md)와 해당 릴리스 증거를 기준으로 확인한다.

## 적용 범위와 형상

- 개발 경로: `/tmp/agent-workflow-observatory-20261105`
- 브랜치: `codex/observatory-usability-20261105`, 기준 SHA `388752203b3bc98eda45b4c4dd60f9ea68ef74bc`
- 변경분은 아직 미커밋이다. 기준 SHA만으로 구현 형상을 재현할 수 없으며 작업 원장의 변경 파일 fingerprint와 native review receipt를 함께 확인한다.
- 개발 작업 원장: `/tmp/agent-workflow-observatory-state-20261105`
- 실제 호스트 시험 home: `/tmp/aw-observatory-hosts-20261105/state`
- 운영 소스 `/home/roqkf/agent-workflow`와 기존 8776/8777은 배포·재시작하지 않았다. origin/main 병합·push·운영 pull도 이번에 수행하지 않았다.

## 구현된 사용자 흐름

`/overview`에서 관리형 작업과 일상 세션을 모아 보고, `/sessions`에서 공급자·프로젝트를 선택한다.
연결 진단은 경로·등록·설치·호스트 활성화·첫 수신을 구분한다. 확인 요청은 오래된 대기부터
표시하고, 알림을 직접 켜면 같은 origin의 여러 탭에서 중복을 억제한다.
부모·하위 세션과 실제 도구 활동을 표시하며, 3D가 무거우면 목록 보기로 WebGL을 중지한다.
명시적인 workflow/PR 연결, 이벤트 구간 북마크, 관측 시간·도구 오류 요약도 제공한다.
사용 방법은 [개발 사용 안내](OBSERVATORY_REL01_USAGE.ko.md)를 따른다.

## 자동·브라우저 검증

| 범위 | 결과 | 한계 |
|---|---|---|
| Python 전체 회귀 | 277개, 95.438초, 통과 | 컨트롤러의 실제 임시 DB·HTTP 검사. 독립 검토 샌드박스의 쓰기 제한과 구분 |
| 프런트 | typecheck, mission·상태 공간·이동·정렬·알림·계층·활동 검사 통과 | 빌드 자산 일치는 별도 `check:mission` 결과 확인 |
| 실제 Chromium 8개 흐름 | native, 알림, 계층, 활동, 프로젝트, 근거, 북마크, 시간 요약 통과 | 합성 호스트 fixture이며 실제 세 도구 모델 작업 실증과 구분 |
| 북마크 회귀 | 직접 링크 재진입·구간 복원·출처 내보내기 통과 | 이전 기록에 없던 출처는 미기록 |
| 근거 정리 회귀 | 원본 세션 삭제 후 전체 목록 조회·연결 해제 통과 | 원본 자체를 복원하지 않음 |
| 알림 경합 회귀 | 빈 제어 파일을 150ms 유지한 뒤 원자적으로 명령 전달, 두 탭 단일 알림 통과 | OS 팝업은 계측 생성자로 대체. 실제 Web Locks 사용 |
| 실제 호스트 Chromium | 실제 Codex 세션 선택·원래 ID·3D 공간·시작/작업/응답 종료/세션 종료 이력·HTTP 200 확인 | 임시 observer 서버는 검증 후 종료, worker 없음 |
| NFR-01 | 10소스·1000작업·500세션·10000이벤트, HTTP 100회 p95 47.07ms | 로컬 합성값, 외부 OpenCode 지연 제외 |
| NFR-02 | 최종 100전이 p95 4033.58ms, 최대 4059.61ms | 합성 이벤트·reduced-motion, 모델 호출 없음 |
| NFR-04 최종 재측정 | 24명 입력/12명 3D, 60초 p95 50ms → 목표 33.3ms 미달. 목록 24명·Canvas 0, 60초 p95 16.7ms | Headless Chromium·SwiftShader, rAF 간격이며 GPU 처리 시간과 구분. 자동 전환이 아닌 수동 목록 전환 |
| 외부 원장 보존 | 집계 벤치마크 전후 원장 DB 바이트 동일, native 브라우저 fixture에 workflow DB·daemon.lock 생성 없음 | 운영 전환·운영 DB 복구 리허설은 미수행 |

활동 표시 수정 후의 최종 작업자 재측정은 증거 디렉터리의 `resume/workers-final.log`와
`/tmp/observatory-workers-final-20261105/workers-performance.json`을 기준으로 확인한다.
최종 훅→DOM 100전이 재측정은 `resume/latency-final.log`에 별도로 남긴다.
초기 측정과 최종 재측정은 별도 파일로 보존했다. 최종 환경은 Chromium 151.0.7922.34, DPR 1, Ryzen 5 7500F, WSL2, SwiftShader이며 `resume/workers-final-environment.json`에서 확인한다.

알림의 이전 간헐 실패는 제어 파일 생성과 쓰기 사이의 빈 내용을 fixture가 명령으로 소비할 수 있던
경합을 확인해 보강했다. 이것만으로 모든 알림 경합이 없다고 단정하지 않으며 반복 검증 결과를
증거 디렉터리에 남긴다.

## 실제 호스트 실증

| 도구 | 관측한 사실 | 미확인 |
|---|---|---|
| Claude Code | 격리 초기화에서 시작·종료 수신. 세션 `d6069bc7-c5a4-4f6c-a4fa-f24c6bc40356` | 실제 모델 작업·하위 에이전트 전체 생명주기 |
| OpenCode 1.3.17 | 격리 API 서버에서 빈 세션 `ses_e56b96f7effeO6Gu3ul7iVCKKj` 발견 | 실제 작업·하위 세션 이벤트. 빈 세션 상태는 unknown |
| Codex 0.166.0-alpha.6 | 처음 1턴에서는 수신 없음. `/hooks`의 관측 훅 11개 명시 신뢰 후 기존 세션 종료 수신. 이후 추가 승인한 1턴에서 OK 응답과 `SessionStart/UserPromptSubmit/PreModelRequest/Stop/SessionEnd` 모두 수신. 최신 실제 세션 `01a1aaab-1384-7cd1-99df-fc8cc816ff22`, 관측 ID `8ef4e89d4ec055ea1f1678dbd1415870` | 실제 도구·하위 에이전트 이벤트. 도구·파일 접근 없는 모델 테스트만 수행 |

Codex 프로젝트 신뢰와 훅 검토·신뢰는 격리 홈에만 적용했다. 최초 한 번 실행의 신뢰 우회만으로는 수신되지 않았고, `/hooks`에 active 0·review 11로 표시됐다. 명시 신뢰 후 active 상태와 실제 종료 이벤트 수신을 확인했다.
모델 시험용 인증 복사본은 종료 시 삭제했다. 원본 인증 설정은 변경하지 않았다.
모델 응답이 끝난 사실만으로 관측 성공을 판정하지 않는다. 별도 추가 승인 1턴의 실제 DB 이벤트 5종과 Chromium 이력이 근거다. 총 모델 테스트는 최초 1턴과 추가 승인 1턴이며 자동 재시도하지 않았다.
공식 [훅 문서](https://learn.chatgpt.com/docs/hooks)의 신뢰·플러그인·이벤트 계약과 대조했다. 훅 인식·등록·신뢰·첫 수신을 각각 확인해야 하며, 이번 종료 이벤트 성공을 모든 생명주기 검증으로 확대하지 않는다.

## 실제 호스트 URL과 원장

최신 실제 Codex 검증 URL은 `http://127.0.0.1:36485/sessions?session=8ef4e89d4ec055ea1f1678dbd1415870`이었다.
검증용 임시 observer 서버는 종료했으며 운영 URL이 아니다. 관측 home은
`/tmp/aw-observatory-hosts-20261105/state`, 구현 작업 원장은
`/tmp/agent-workflow-observatory-state-20261105`다. 기존 운영 8776/8777의 PID와 HTTP 200,
HTML 해시는 초기 확인과 같고, 운영 checkout SHA와 기존 문서 수정도 보존됐다.
근거는 `resume/operating-preservation.json`이다.

## 독립 검토와 잔여 게이트

개발 패키지 WP-OBS-01~14는 각각 실제 검사와 지정 독립 리뷰를 완료했다. 초기 01·02·03·05·06은 Claude, 나머지는 사용자 승인 후 별도 Codex 세션 검토다. 패키지별 완료는 해당 시점의 fingerprint를 뜻하며 최종 문서·통합 변경의 판정은 통합 작업 원장의 최신 receipt를 따른다.

초기 통합 독립 Codex 검토 `AW-OBS-INTEGRATED-20261105`는 북마크 직접 링크, 고아 근거 정리,
북마크 출처 보존 3건을 차단 사항으로 반환했다. 세 항목 모두 수정하고 단위·브라우저 회귀를
추가했다. 이후 WP-OBS-09 개별 리뷰에서 활동 구체가 어깨 안에 가려지는 결함도 발견해 머리 위 색·형상 표시로 수정했다. 일반·reduced-motion의 실제 캔버스 픽셀로 가시성을 검사한다. WP-OBS-10 개별 검토에서 발견한 프로젝트별 요청 건수·종료 이력의 공간 누락도 공통 필터로 수정하고 실제 브라우저 회귀를 추가했다. WP-OBS-11 리뷰의 원장 자동 대체 오류도 수정했다. 사라진 원장의 링크와 다른 원장의 동명 작업을 실제 브라우저에서 검증한다. 근거 API도 자동 발견 목록 대신 현재 등록 원장만 허용하도록 수정하고, 미등록 거절·연결 해제·재발견 경계의 HTTP 회귀를 추가했다. WP-OBS-14의 세션 보관 한도 경고 누락과 시계 역전 시 도구 결과 누락도 수정했다. 북마크의 SQLite 정수 범위 초과는 HTTP 400으로 처리한다. 최종 재검토 결과와 코드 fingerprint는 해당 작업 원장의 실제 native receipt가 기준이다.
검토자가 직접 작성하지 않은 통과 JSON으로 이를 대신하지 않는다.

- 신규 사용자 3명 파일럿은 사용자 결정으로 **미완료**다.
- 비용·토큰 기능 FR-OBS-14/REL-04/WP-OBS-15는 사용자 결정으로 **보류**다.
- 실제 Codex 시작·작업·응답 종료·세션 종료 수신은 확인했다. 세 도구의 미검증 도구·하위 에이전트 생명주기는 **미완료**다.
- 운영 배포·운영 롤백 리허설은 **미수행**이다. REL-01 전체 출시 완료로 선언하지 않는다.

## 증거 위치와 재실행

원시 로그·JSON·스크린샷은 개발 checkout의
`artifacts/observatory-implementation-20261105/` 아래 보존한다. 이 디렉터리는 Git ignore 대상이며
보고서만 추적한다. 실제 검토 receipt는 별도 작업 원장의 `tasks/<ID>/runs/`에 있다.
코드가 바뀌면 이전 검증·리뷰 fingerprint를 현재 코드의 근거로 재사용하지 않는다.

```bash
# Python 3.11+와 Node가 PATH에 준비된 격리 개발 checkout
python3 -m unittest discover -s tests
npm run typecheck
npm run test:mission
npm run check:mission
npm run test:notifications:browser
npm run test:evidence:browser
npm run test:bookmarks:browser
npm run test:metrics:browser
```
