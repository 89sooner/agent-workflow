# 상태별 작업 공간

2026-11-04. 상태별 공간의 사용 안내다. 운영 적용 여부는 실제 서버의 자산과 배포 보고서로 확인한다.

| 공간 | 분류 기준 | 표현 |
|---|---|---|
| 작업실 | 최신 관측의 running | 책상·모니터·키보드, 실행 활동에 따른 타이핑 |
| 대기·휴게실 | idle, pending, assigned, queued, ready, retrying, succeeded, completed, needs_review, ready_to_finish | 책상 없이 휴게 의자에 앉은 자세 |
| 확인 필요 | failed, blocked, held, interrupted, cancelled, unknown 및 알 수 없는 상태 | 별도 색상의 공간과 의자 |
| 확인 필요 | 관측 연결 오류·새 응답 대기 | 이전 running을 현재 실행 또는 idle로 추정하지 않음 |

완료 기록은 현재 세션이 실제로 idle이라는 증거가 아니다. 선택 정보의 원래 상태와 공간 안내를 함께 확인한다. 분류는 화면에 표시된 actor/실행 노드 기준이며 PC 전체 에이전트를 발견하는 기능은 아니다.

## 사용

관제실에서 작업을 선택하면 색상으로 구분한 세 공간과 각각의 표시 인원이 나타난다. 실행 상태가 갱신되면 해당 공간으로 재배치된다. 공간 사이 이동은 즉시 반영하며 이동 애니메이션으로 실제 활동을 꾸미지 않는다.

작업자를 선택하고 **선택한 작업자 가까이**를 누르면 확대한다. 확대 중 상태가 바뀌면 선택한 작업자의 새 위치를 따라간다. **시점 초기화**는 전체 공간을 보여준다. 기존 이름표 충돌 회피·전체 이름·실행 이력 선택은 유지한다.

**움직임 줄이기**는 애니메이션만 끈다. 실행 중인 작업자를 대기실로 보내지 않는다. 과거 run이나 재생을 선택하면 **선택한 기록 시점 기준**으로 표시하며 현재 실시간 활동과 구분한다.

최대 12명 표시, 목록·그래프를 통한 선택, WebGL fallback은 기존과 같다. 상단 공간별 숫자는 현재 3D에 표시한 노드의 수이며 전체 프로젝트의 에이전트 수가 아니다.

## 검증

상태 분류·미확인 처리·공간 배치 단위 검사, 합성 Chromium의 1/4/12/24명 장면, 실행→대기→실패→연결 미확인→실행 전환, 혼합 상태, 선택·확대·이름표 경계, 모바일, 움직임 줄이기를 검사한다. 기존 실제 원장 fixture의 실행·재생·연결 단절·이력 회귀도 검사한다.

재현 명령은 npm run test:mission, npm run typecheck, npm run check:mission, node tests/workers_browser.mjs, python3 tests/mission_integration.py다. 브라우저 검사에는 WORKFLOW_PLAYWRIGHT_PATH와 설치된 Chromium이 필요하다.

미리보기: http://127.0.0.1:8886/mission
소스: /tmp/agent-workflow-state-rooms-20261104
state home: /tmp/agent-workflow-state-rooms-preview-20261104
watch 모드로 제공하며 큐 worker를 시작하지 않는다.

운영 URL: H 읽기 전용 http://127.0.0.1:8777/mission, 기본 관리 http://127.0.0.1:8776/mission. 배포 뒤 기존 탭은 새로고침한다. 8886은 개발 미리보기이며 운영 주소가 아니다.
