# 문서 찾기

[처음 설치하기](USER_GUIDE.ko.md#setup) · [8776 관리 화면 열기](http://127.0.0.1:8776/) · [전체 CLI 명령](USER_GUIDE.ko.md#commands) · [저장소 홈](../README.md)

[2026-11-27 릴리즈 노트](FOLLOWUP_STATUS.ko.md#release-2026-11-27) · [실제 OpenCode 접속 변경과 원복](OPERATIONS.ko.md#opencode-live-rotation)

## 일상 개발

| 필요한 결과 | 절차와 예제 |
|---|---|
| 프로젝트별 지침으로 목표 등록·완료 | [프로필과 CLI](../profiles/README.md#project-goals) · [agent-workflow 자체 실행과 Katakuri 호환 검증](../pilots/p2-goal-mode/README.md) |
| Katakuri 목표 등록·백그라운드 실행 | [화면에서 시작](KATAKURI_GOAL.ko.md#web) · [CLI로 시작](KATAKURI_GOAL.ko.md#cli) · [작업 범위에 맞는 프로필](../profiles/README.md) |
| 멈춘 목표의 다음 행동 결정 | [근거 제출과 예산 조정](KATAKURI_GOAL.ko.md#resume) · [완료 판정 기준](KATAKURI_GOAL.ko.md#acceptance) |
| Claude 작업을 Codex에서 이어받기 | [마감과 재개 명령](USER_GUIDE.ko.md#sessions) · [인계 실습](USE_CASES.ko.md#handoff) |
| 테스트 선택·추천·측정 | [프로필과 검사 묶음](USER_GUIDE.ko.md#profiles) · [검사 추천 실습](USE_CASES.ko.md#recommend) · [추천 품질 평가](USE_CASES.ko.md#measure-checks) |
| 구현부터 리뷰까지 맡기기 | [수행 단계와 완료 증거](USER_GUIDE.ko.md#execution) · [실행 예제](USE_CASES.ko.md#implement) · [에이전트 지시·인계 언어](AGENT_COMMUNICATION.ko.md) |
| 순차 실행·보류·재개 | [큐와 작업 의존성](USER_GUIDE.ko.md#queue) · [중단에서 복구하는 예제](USE_CASES.ko.md#dependencies) |
| 작업이 병합·배포됐는지 확인 | [릴리스 단계와 결과](USER_GUIDE.ko.md#release) · [상태 조회 예제](USE_CASES.ko.md#deployment) |

## 화면과 세션 관측

| 필요한 화면 또는 정보 | 사용법 |
|---|---|
| React 작업 등록·실행 제어·테마 | [화면 조작](REACT_MISSION_CONTROL.ko.md#controls) · [새 작업과 AI 검사 추천](REACT_MISSION_CONTROL.ko.md#register) · [작업·실행 관리](REACT_MISSION_CONTROL.ko.md#manage) |
| 프로젝트를 연결하거나 빈 화면 진단 | [처음 연결과 문제 해결](CONNECTION_SETUP.ko.md) · [원장 발견과 CLI 연결](PROJECT_OBSERVATORY.ko.md) |
| 일반 Claude·Codex·OpenCode 세션 보기 | [플러그인 설치·진단·해제](NATIVE_PLUGINS.ko.md) · [OpenCode 서버 연결](OPENCODE_OBSERVATION.ko.md) |
| 알림, 세션 계층, 북마크, 시간과 근거 | [통합 화면 사용법](OBSERVATORY_REL01_USAGE.ko.md) · [실행 기록·로그·재생](OBSERVABILITY.ko.md) |
| 3D 작업자와 방의 상태 해석 | [작업 공간의 상태](STATE_ROOMS.ko.md) · [작업자 선택·확대](DETAILED_WORKERS.ko.md) |

## 서버 운영과 도구 개발

- 관리 서버를 변경할 때는 [포트와 원장](OPERATIONS.ko.md#servers), [백업·재시작·확인](OPERATIONS.ko.md#rollout), [복구](OPERATIONS.ko.md#rollback) 순서로 확인합니다.
- 작업 실패나 환경 이전에는 [CLI 복구 안내](USER_GUIDE.ko.md#ops)를 사용합니다. 제품 코드를 변경할 때는 [개발 환경과 검사 명령](DEVELOPMENT.ko.md), [관측 API 계약](OBSERVATORY_API_CONTRACT.ko.md)을 참고합니다.

## 이전 버전과 검증 기록

| 자료 | 찾아볼 내용 |
|---|---|
| [v0.2 사용 기록](USER_GUIDE_V0.2.ko.md) · [v0.3 자동화](AUTOMATION_V0.3.ko.md) · [후속 작업 기록](FOLLOWUP_STATUS.ko.md) | 초기 파일럿, 작업 프로필, 복구와 릴리스 도입 과정 |
| [일상 세션 파일럿](NATIVE_PILOT.ko.md) · [이전 SaaS 화면](SAAS_OBSERVATORY.ko.md) | 초기 연결 절차와 당시 화면 |
| [초기 계획](IMPLEMENTATION_PLAN.md) · [구성도](workflow-blueprint.html) · [관제실 계획](OBSERVATORY_IMPLEMENTATION_PLAN.ko.md) · [사용성 계획](USABILITY_SERVICE_PLAN.ko.html) | 설계 시점의 요구사항과 수용 기준 |
| [관측 설계 조사](OBSERVABILITY_RESEARCH.ko.md) · [유사 앱 조사](SIMILAR_APPS_RESEARCH_2026-11-05.ko.md) | 설계 결정을 내릴 때 사용한 비교 자료 |
| [통합 관제실 검증](OBSERVATORY_VALIDATION_2026-11-05.ko.md) · [준비 검증](READINESS_VALIDATION_2026-11-05.ko.md) · [파일럿 진행표](../pilots/READINESS_PILOT.ko.md) | 검증 당시의 증거, 적용 범위와 남은 조건 |

이 디렉터리의 `verification*.json`, `unit-tests*.log`, `images/`에는 해당 시점의 결과가 보관되어 있습니다.
