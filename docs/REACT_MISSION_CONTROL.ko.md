# React 실행 관제실 사용 안내

갱신: 2026-11-02 · TypeScript + React + Three.js + React Three Fiber.

`/mission`은 실제 작업을 관측하는 새 화면이고 `/`는 기존 등록·실행·중단 관리 화면이다.
둘은 같은 Python HTTP API와 SQLite 원장을 사용한다. `/mission`은 GET만 호출한다.
새 화면을 열었다고 worker나 모델 호출이 시작되지는 않는다. 단, 일반 `serve` 서버 자체는
worker를 실행하므로 관측 전용 서버에는 `watch` 또는 `serve --read-only`를 사용한다.

## 현재 WSL 주소와 원장

| 목적 | 주소 | state home | 실행 제어 |
|---|---|---|---|
| Katakuri H 작업 관측 | http://127.0.0.1:8777/mission | `/tmp/katakuri-h-local-queue-workflow-20261029` | 서버 전체 읽기 전용 |
| 기본 원장 관측 | http://127.0.0.1:8776/mission | `~/.local/state/agent-workflow` | 관제실은 GET만 사용 |
| 기본 원장 관리 | http://127.0.0.1:8776/ | 위와 동일 | 기존 worker·관리 API |
| 개발 미리보기 | http://127.0.0.1:8880/mission | `/tmp/agent-workflow-react-preview-20261102` | 별도 읽기 전용 연결 |

위 주소는 이 WSL 배치의 설정이다. **8765는 Katakuri 운영 제어 서버**이므로 관제실을 위해
종료하거나 같은 포트를 재사용하지 않는다. 다른 PC에서는 빈 포트와 실제 state home을 선택한다.
미리보기와 운영 적용 여부는 구분한다. 배포 SHA·health 기록은 [운영 절차](OPERATIONS.ko.md)로 확인한다.

## 사용 순서

1. 운영 H 관제실 `http://127.0.0.1:8777/mission`을 연다. 왼쪽 **작업 원장**에서 `katakuri-h-local-queue-workflow-20261029`를 선택한다.
2. **프로젝트 전체 관제실**에서 실행·대기·미확인 수, 최근 작업 흐름을 본다. 원장/프로젝트를 바꾸면 이전 범위의 이력과 재생 상태는 초기화된다.
3. **작업·담당자 검색**에 작업 ID나 실제 배정 이름을 입력한다. 작업을 선택하면 해당 작업의 최근 실행이 열린다.
4. 3D 담당자 또는 그래프 노드를 선택한다. 오른쪽 패널에서 배정 이름의 출처, 역할, 요청 모델/관측 모델, 실행 ID, 시작·종료, 실제 결과를 본다. 그래프 선택과 3D 선택은 같은 실행을 가리킨다.
5. **실행 기록** 드롭다운에서 이전 run을 선택한다. 아래 **실행 이력**에서도 작업·run을 선택할 수 있다. 날짜/시간은 브라우저의 현지 시간이다.
6. **표준 출력 / 오류 출력**으로 실제 span 로그를 읽는다. 미기록 모델·세션 ID는 미확인으로 표시한다.
7. **기록 재생**을 누르면 불러온 이벤트의 고정 스냅샷을 순서대로 재생한다. 슬라이더로 이동하고, **실시간으로 돌아가기**로 종료한다. 재생은 실행 명령이 아니며 그 시점 이후의 결과를 미리 표시하지 않는다.
8. 이벤트는 100개씩 표시한다. **이전 쪽 / 최신 쪽**은 불러온 이벤트를 탐색하고, **이전 기록 불러오기**는 서버의 이전 페이지를 추가한다. **JSON 내보내기**는 해당 작업/run의 기록을 내려받는다.
9. 3D는 드래그 회전·스크롤 확대·**시점 초기화**를 지원한다. **3D 공간**을 끄거나 **움직임 줄이기**를 선택할 수 있다. OS reduced-motion 설정을 우선한다. WebGL을 사용할 수 없어도 그래프·담당자·이력은 사용할 수 있다.
10. 작업 등록·실행·중단은 왼쪽 아래 **작업 등록·실행 관리**에서 기존 화면으로 이동한다. 8777 H 서버는 읽기 전용이다. 기본 원장의 등록·실행 관리는 8776 기존 관리 화면에서 한다. 원장을 바꾸면 대상이 달라지므로 state home을 먼저 확인한다.

## 무엇을 보여 주는가

- 그래프의 선: 원장에 기록된 `previous_span_id` 실행 순서. 임의의 협업 관계나 병렬 실행을 생성하지 않는다. 서로 다른 작업은 별도 행으로 표시한다.
- 3D 담당자: API actor identity를 사용한다. 실제 배정 이름을 우선하며, 없으면 provider·역할·작업 기반 자동 이름임을 명시한다. 시스템 검사 실행기와 에이전트를 구분한다.
- 움직임: 생존 확인된 `running` 실행의 작업 동작, 새로 관측된 시작·종료 이벤트의 전달 표시. 첫 접속의 과거 이벤트를 새 활동으로 재생하지 않는다.
- 연결 오류·숨긴 탭·재생·reduced-motion에서는 실시간 움직임을 중지한다. 탭을 다시 열면 새 조회가 성공할 때까지 이전 기록의 움직임을 재개하지 않는다. 초기/빈 상태는 작업 중인 것처럼 꾸미지 않는다.
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

## 개발 및 검증

```bash
npm ci --ignore-scripts --no-audit --no-fund
npm run typecheck
npm run test:mission
npm run build
npm run check:vendor
npm run check:mission
```

`npm run dev:mission`으로 소스를 감시하며 빌드할 수 있다. 자산은 Python 패키지에 포함되므로
일반 이용자에게 Node 서버나 npm 설치를 요구하지 않는다. 개발 Node 버전은 22를 사용한다.
Python 실행기는 3.11 이상이 필요하다. 원장을 조회만 하려면 다음처럼 별도 빈 포트에서 실행한다.

```bash
python3 -m agent_workflow --home /path/to/observer watch --port 8878
```

원장의 경로를 이미 안다면 `connect PROJECT --ledger STATE_HOME`으로 observer에 연결을 저장한다.
일반 `serve`와 자동 큐 재개를 관측 서버 시작으로 혼동하지 않는다.

```bash
# 이 호스트의 동시 검증을 보호하는 공유 잠금이다.
flock -w 600 /tmp/katakuri-host-heavy-check.lock python3 -m unittest discover -s tests
flock -w 600 /tmp/katakuri-host-heavy-check.lock python3 tests/mission_integration.py
flock -w 600 /tmp/katakuri-host-heavy-check.lock python3 tests/saas_integration.py
```

브라우저 도구 경로는 `WORKFLOW_PLAYWRIGHT_PATH`, 스크린샷 폴더는 `WORKFLOW_SCREENSHOT_DIR`로
지정한다. 테스트는 임시 DB·Git·HTTP·subprocess·실제 Chromium을 사용하고 에이전트 CLI만 합성한다.
모델 성과나 실제 운영 작업의 성공으로 해석하지 않는다. 재생 순서·지연된 원장 응답·오래된 선택
단계·실제 Canvas 움직임과 reduced-motion 정지·오프라인·키보드·모바일·WebGL fallback을 검사한다.

## 표시 범위와 한계

- 그래프: 최근 작업 최대 6개, 작업당 최대 8단계. 활성·선택 단계는 표시 범위에 유지한다.
- 3D: 최대 12개 담당자/실행기. 같은 시스템 actor의 반복 호출은 최근 호출을 대표로 표시한다.
- 담당자 목록 100개, 실행 이력 목록 30개. 더 오래된 run은 작업별 실행 드롭다운에서 선택한다.
- 서버도 작업/run/span/event 개수 제한을 적용한다. 화면이 전체 원장을 전부 렌더링한 것으로 해석하지 않는다. 이전 이벤트는 페이지로 추가한다.
- 프로젝트 poll 4초, 작업 trace 2초, 연결 목록 15초. 서로 다른 조회의 표시 시점에 짧은 차이가 있을 수 있다. 실패 시 마지막 기록을 유지하고 backoff 재시도한다.
- 이번 작업은 관제 UI의 프런트 구조를 개선한 것이다. Python 실행기를 Rust/Go로 바꾸거나, 원장 없는 임의의 외부 에이전트 내부 활동을 자동 수집하는 기능은 포함하지 않는다.
- 검증 JSON과 로그는 기록 당시의 증거다. 현재 배포 SHA는 운영 checkout과 HTTP 자산을 함께 확인한다. 공개 저장소에 운영 로그·DB·비공개 작업 프롬프트를 커밋하지 않는다.

