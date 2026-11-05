# 문서 안내

갱신: 2026-11-05. 현재 사용법과 과거 설계·검증 기록을 구분한다.

| 문서 | 현재 역할 |
|---|---|
| [에이전트 통신 언어](AGENT_COMMUNICATION.ko.md) | ASD-STE100 기본 지시, 검토·피드백·인계, 원문 보존과 관측 전용 범위 |
| [통합 관제실 사용 안내](OBSERVATORY_REL01_USAGE.ko.md) | 통합 화면·진단·대기 정렬·알림; 운영 모드와 파일럿 미완료 구분 |
| [관제실 개선 구현 계획](OBSERVATORY_IMPLEMENTATION_PLAN.ko.md) | 조사 기반 REL-01~04, FR·WP·수용 기준·운영 보존; 검토 상태 |
| [유사 앱 조사와 개선 후보](SIMILAR_APPS_RESEARCH_2026-11-05.ko.md) | 2026-11-05 공식 자료 조사, 기존 기능 비교와 P0~P2 제안; 구현 계획 승인과 구분 |
| [화면에서 프로젝트 연결](CONNECTION_SETUP.ko.md) | UX-01~03: 첫 연결, 빈 화면·오류 진단, 관측 설정과 실행 권한 |
| [OpenCode 관측](OPENCODE_OBSERVATION.ko.md) | 연결·자동 세션 발견·인증·지원 범위; 소스 통합과 운영 적용 구분 |
| [서비스 사용성 개선 계획](USABILITY_SERVICE_PLAN.ko.html) | 실제 화면 진단, 첫 연결·일상 관리 개선 우선순위와 수용 기준 |
| [작업자 3D·이름표·성능](DETAILED_WORKERS.ko.md) | 확대·선택·이름표 충돌 회피, 1/4/12/24명 합성 브라우저 검증 |
| [React 실행 관제실](REACT_MISSION_CONTROL.ko.md) | `/mission` 사용법, 이름·그래프·R3F·이력·재생·개발 검증 |
| [프로젝트 연결](PROJECT_OBSERVATORY.ko.md) | 원장 자동 발견, connect/start/watch, 이름 등록과 관측 범위 |
| [운영 절차](OPERATIONS.ko.md) | 8776/8777 배치, 큐·직접 실행 확인, 백업·반영·health·복구 |
| [기존 SaaS 화면](SAAS_OBSERVATORY.ko.md) | `/` 관리 UI와 기존 Three.js 관제실의 조작 |
| [관측 API와 증거](OBSERVABILITY.ko.md) | task/job/run/span/event, 상태·완료·재생·로그 계약 |
| [자동화](AUTOMATION_V0.3.ko.md) | profile, 격리 작업, 검증, 리뷰, 큐, release 명령 |
| [후속 진행 현황](FOLLOWUP_STATUS.ko.md) | UX-01~03 완료·남은 범위·다음 3D 형상 작업·배포/검증 구분 |
| [기본 개념](USER_GUIDE.ko.md) | v0.2 개념·파일럿 기록과 현재 문서 연결 |
| [초기 구현 계획](IMPLEMENTATION_PLAN.md) | 2026-10의 구현 이력; 현재 미완료 목록으로 사용하지 않음 |
| [조사 근거](OBSERVABILITY_RESEARCH.ko.md) | 2026-10-30 조사 결과; 최신 제품 조사로 간주하지 않음 |
| [초기 구성도](workflow-blueprint.html) | 2026-10-08 설계 시뮬레이션; 실제 관측 화면이 아님 |

`verification*.json`, `unit-tests*.log`와 기존 이미지는 당시 검증 증거이며 현재 실행 결과로 덮어쓰지 않는다.
새 검증 결과는 별도 시점·SHA로 기록한다. 상세 로컬 운영 증거는 `artifacts/`에 보존하며,
공개 저장소에는 운영 DB·로그·비공개 프롬프트·인증 정보를 올리지 않는다.

- [상태별 작업 공간](STATE_ROOMS.ko.md): 상태 분류, 휴게 의자, 연결 미확인·과거 기록 구분, 선택 확대.

- [WP 없는 일상 세션 파일럿](NATIVE_PILOT.ko.md): Claude 훅·OpenCode 통합 관측, 확인 요청함, 별도 이력·재방문.

WP 없는 Codex 관측과 Claude Code·OpenCode·Codex 플러그인 설치/진단/해제: [세 도구 플러그인 안내](NATIVE_PLUGINS.ko.md). 기존 관리형 WP 기능은 유지됩니다.

- [2026-11-05 통합 관제실 검증·미완료 조건](OBSERVATORY_VALIDATION_2026-11-05.ko.md)
