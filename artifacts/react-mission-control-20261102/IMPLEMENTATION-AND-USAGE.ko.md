# React 실행 관제실 구현 및 사용 안내

## 적용 범위

이번 변경은 `/mission`에 TypeScript + React + Three.js + React Three Fiber 관제실을 추가한다.
Python HTTP API·SQLite 원장·작업 실행기는 유지한다. 기존 `/`는 등록·실행·중단·설정 관리 화면이다.
새 관제실은 GET 조회만 하며 작업을 실행하지 않는다. Node.js는 개발 빌드에만 필요하다.

- 개발 브랜치: `feat/react-mission-control`
- 개발 소스: `/tmp/agent-workflow-react-mission-20261102`
- 기준 main SHA: `81b55e8b554b9c931d3692654a4acbb3989560a9`
- 관제실: http://127.0.0.1:8880/mission
- 미리보기 서버 home: `/tmp/agent-workflow-react-preview-20261102`
- 관측 대상: `/tmp/katakuri-h-local-queue-workflow-20261029`
- 개발 진행 원장: `/tmp/agent-workflow-react-mission-state-20261102`
- 개발 task: `AW-REACT-MISSION-20261102`

운영 소스 `/home/roqkf/agent-workflow`와 8765·8776·8777·8877 서버를 교체하지 않는다.
이번 기능은 원격 푸시·main 병합·운영 배포 대상에 포함하지 않았다.

## 사용 순서

1. 위 8880 `/mission` URL을 연다. 왼쪽 **작업 원장**에서 `katakuri-h-local-queue-workflow-20261029`를 선택한다.
2. **프로젝트 전체 관제실**에서 실행·대기·미확인 수, 최근 작업 흐름을 본다. 원장/프로젝트를 바꾸면 이전 범위의 이력과 재생 상태는 초기화된다.
3. **작업·담당자 검색**에 작업 ID나 실제 배정 이름을 입력한다. 작업을 선택하면 해당 작업의 최근 실행이 열린다.
4. 3D 담당자 또는 그래프 노드를 선택한다. 오른쪽 패널에서 배정 이름의 출처, 역할, 요청 모델/관측 모델, 실행 ID, 시작·종료, 실제 결과를 본다. 그래프 선택과 3D 선택은 같은 실행을 가리킨다.
5. **실행 기록** 드롭다운에서 이전 run을 선택한다. 아래 **실행 이력**에서도 작업·run을 선택할 수 있다. 날짜/시간은 브라우저의 현지 시간이다.
6. **표준 출력 / 오류 출력**으로 실제 span 로그를 읽는다. 미기록 모델·세션 ID는 미확인으로 표시한다.
7. **기록 재생**을 누르면 불러온 이벤트의 고정 스냅샷을 순서대로 재생한다. 슬라이더로 이동하고, **실시간으로 돌아가기**로 종료한다. 재생은 실행 명령이 아니며 그 시점 이후의 결과를 미리 표시하지 않는다.
8. 이벤트는 100개씩 표시한다. **이전 쪽 / 최신 쪽**은 불러온 이벤트를 탐색하고, **이전 기록 불러오기**는 서버의 이전 페이지를 추가한다. **JSON 내보내기**는 해당 작업/run의 기록을 내려받는다.
9. 3D는 드래그 회전·스크롤 확대·**시점 초기화**를 지원한다. **3D 공간**을 끄거나 **움직임 줄이기**를 선택할 수 있다. OS reduced-motion 설정을 우선한다. WebGL을 사용할 수 없어도 그래프·담당자·이력은 사용할 수 있다.
10. 작업 등록·실행·중단은 왼쪽 아래 **작업 등록·실행 관리**에서 기존 화면으로 이동한다. 현재 8880 미리보기 서버 전체는 읽기 전용이므로 실제 제어는 기존 운영 관리 서버에서 한다.

## 무엇을 보여 주는가

- 그래프의 선: 원장에 기록된 `previous_span_id` 실행 순서. 임의의 협업 관계나 병렬 실행을 생성하지 않는다. 서로 다른 작업은 별도 행으로 표시한다.
- 3D 담당자: API actor identity를 사용한다. 실제 배정 이름을 우선하며, 없으면 provider·역할·작업 기반 자동 이름임을 명시한다. 시스템 검사 실행기와 에이전트를 구분한다.
- 움직임: 생존 확인된 `running` 실행의 작업 동작, 새로 관측된 시작·종료 이벤트의 전달 표시. 첫 접속의 과거 이벤트를 새 활동으로 재생하지 않는다.
- 연결 오류·숨긴 탭·재생·reduced-motion에서는 실시간 움직임을 중지한다. 초기/빈 상태는 작업 중인 것처럼 꾸미지 않는다.
- 컨트롤러 프로세스 생존과 모델 내부 활동은 서로 다르다. 도구 호출/내부 사고/실제 처리율은 원장에 기록되지 않은 한 추정하지 않는다.
- 프로젝트 집계는 현재 데이터이고 재생은 선택한 이벤트 기록이다. 재생 그래프의 행에는 현재 완료 상태를 과거 상태처럼 표시하지 않는다.

## 구현 파일

| 경로 | 역할 |
|---|---|
| `frontend/mission/App.tsx` | 원장·프로젝트·작업·run 선택, 상세·로그·이력·재생 |
| `frontend/mission/api.ts` | 범위별 polling, 취소, backoff, visibility 처리 |
| `frontend/mission/model.ts` | 실제 데이터 → 그래프, 이벤트 시점 상태, 전달 이벤트 |
| `frontend/mission/Graph.tsx` | SVG 실행 그래프, 실제 간선, 확대·키보드 선택 |
| `frontend/mission/Room.tsx` | R3F Canvas, 절차적 작업 공간·이름·활동, OrbitControls, WebGL fallback |
| `frontend/mission/mission.css` | 반응형 UI, 상태 표시, 제한된 전환 |
| `scripts/build-mission.mjs` | 고정 의존성의 로컬 JS/CSS·라이선스 빌드와 일치 검사 |
| `agent_workflow/server.py` | `/mission`과 정적 자산 allowlist, 취소된 GET 연결 정리 |
| `tests/mission_integration.py`, `tests/mission_scenarios.mjs` | 격리된 실제 DB·HTTP·프로세스·Chromium 수용 검사 |
| `scripts/test-mission.mjs` | 재생 시간순서·이름·중복 전달 방지·선택 단계 보존 회귀 검사 |

프런트 런타임은 React/React DOM 19.3.1, R3F 9.8.2, Three.js 0.187.0이다.
TypeScript 7.0.2와 esbuild 0.28.2를 빌드에 사용한다. Next.js 서버는 추가하지 않았다.
서버와 UI 모두 기존 로컬 API를 사용하며 외부 CDN·폰트·3D 모델 요청이 없다.

## 개발 및 재현

```bash
cd /tmp/agent-workflow-react-mission-20261102
npm ci
npm run typecheck
npm run test:mission
npm run build
npm run check:vendor
npm run check:mission
```

개발 중에는 `npm run dev:mission`으로 자산을 다시 빌드하고 브라우저를 새로고침한다.
설치된 Python 패키지에는 빌드 산출물을 포함하므로 관제실 이용자에게 Node 설치를 요구하지 않는다.

별도 관측 서버를 다시 열 때는 반드시 `watch` 또는 `serve --read-only`를 사용한다.
기존 운영 서버를 종료하거나 일반 `serve`를 새로 시작할 필요가 없다.

```bash
# 이미 연결 설정이 저장된 이번 미리보기 home을 사용한다.
python3 -m agent_workflow --home /tmp/agent-workflow-react-preview-20261102 \
  serve --read-only --port 8880
```

위 명령은 8880이 비어 있을 때만 실행한다. 이미 열려 있으면 URL로 접속한다.
Python 3.11 이상이 필요하며 이 환경에서는 `/home/roqkf/.pyenv/versions/3.12.3/bin/python3`를 사용했다.

검사는 운영 자원과 겹치지 않게 공유 잠금 아래 실행한다.

```bash
flock -w 600 /tmp/katakuri-host-heavy-check.lock \
  python3 -m unittest discover -s tests
flock -w 600 /tmp/katakuri-host-heavy-check.lock \
  python3 tests/mission_integration.py
```

Playwright는 `WORKFLOW_PLAYWRIGHT_PATH`, 스크린샷 디렉터리는 `WORKFLOW_SCREENSHOT_DIR`로 지정할 수 있다.
테스트 에이전트는 명시적인 합성 CLI다. 테스트 DB·Git·HTTP·subprocess·브라우저는 실제로 실행하며 외부 모델이나 운영 작업을 실행하지 않는다.

## 표시 범위와 한계

- 그래프: 최근 작업 최대 6개, 작업당 최대 8단계. 활성·선택 단계는 표시 범위에 유지한다.
- 3D: 최대 12개 담당자/실행기. 같은 시스템 actor의 반복 호출은 최근 호출을 대표로 표시한다.
- 담당자 목록 100개, 실행 이력 목록 30개. 더 오래된 run은 작업별 실행 드롭다운에서 선택한다.
- 서버도 작업/run/span/event 개수 제한을 적용한다. 화면이 전체 원장을 전부 렌더링한 것으로 해석하지 않는다. 이전 이벤트는 페이지로 추가한다.
- 프로젝트 poll 4초, 작업 trace 2초, 연결 목록 15초. 서로 다른 조회의 표시 시점에 짧은 차이가 있을 수 있다. 실패 시 마지막 기록을 유지하고 backoff 재시도한다.
- 이번 작업은 관제 UI의 프런트 구조를 개선한 것이다. Python 실행기를 Rust/Go로 바꾸거나, 원장 없는 임의의 외부 에이전트 내부 활동을 자동 수집하는 기능은 포함하지 않는다.
- 최종 검증 상태·실제 확인 task ID·소스 SHA는 로컬 증거 폴더의 `VERIFICATION.ko.md`에 기록한다(검사 산출물은 Git 추적 제외).
