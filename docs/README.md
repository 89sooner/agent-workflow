# 문서 안내

갱신: 2026-11-04. 현재 사용법과 과거 설계·검증 기록을 구분한다.

| 문서 | 현재 역할 |
|---|---|
| [화면에서 프로젝트 연결](CONNECTION_SETUP.ko.md) | UX-01~03: 첫 연결, 빈 화면·오류 진단, 관측 설정과 실행 권한 |
| [OpenCode 관측](OPENCODE_OBSERVATION.ko.md) | 연결·자동 세션 발견·인증·지원 범위; 소스 통합과 운영 적용 구분 |
| [서비스 사용성 개선 계획](USABILITY_SERVICE_PLAN.ko.html) | 실제 화면 진단, 첫 연결·일상 관리 개선 우선순위와 수용 기준 |
| [React 실행 관제실](REACT_MISSION_CONTROL.ko.md) | `/mission` 사용법, 이름·그래프·R3F·이력·재생·개발 검증 |
| [프로젝트 연결](PROJECT_OBSERVATORY.ko.md) | 원장 자동 발견, connect/start/watch, 이름 등록과 관측 범위 |
| [운영 절차](OPERATIONS.ko.md) | 8776/8777 배치, 큐·직접 실행 확인, 백업·반영·health·복구 |
| [기존 SaaS 화면](SAAS_OBSERVATORY.ko.md) | `/` 관리 UI와 기존 Three.js 관제실의 조작 |
| [관측 API와 증거](OBSERVABILITY.ko.md) | task/job/run/span/event, 상태·완료·재생·로그 계약 |
| [자동화](AUTOMATION_V0.3.ko.md) | profile, 격리 작업, 검증, 리뷰, 큐, release 명령 |
| [후속 진행 현황](FOLLOWUP_STATUS.ko.md) | 구현된 기능과 배포/검증 증거의 구분 |
| [기본 개념](USER_GUIDE.ko.md) | v0.2 개념·파일럿 기록과 현재 문서 연결 |
| [초기 구현 계획](IMPLEMENTATION_PLAN.md) | 2026-10의 구현 이력; 현재 미완료 목록으로 사용하지 않음 |
| [조사 근거](OBSERVABILITY_RESEARCH.ko.md) | 2026-10-30 조사 결과; 최신 제품 조사로 간주하지 않음 |
| [초기 구성도](workflow-blueprint.html) | 2026-10-08 설계 시뮬레이션; 실제 관측 화면이 아님 |

`verification*.json`, `unit-tests*.log`와 기존 이미지는 당시 검증 증거이며 현재 실행 결과로 덮어쓰지 않는다.
새 검증 결과는 별도 시점·SHA로 기록한다. 상세 로컬 운영 증거는 `artifacts/`에 보존하며,
공개 저장소에는 운영 DB·로그·비공개 프롬프트·인증 정보를 올리지 않는다.
