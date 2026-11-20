# 개발 환경과 검증

[문서 찾기](README.md) · [설치와 첫 실행](USER_GUIDE.ko.md#setup) · [운영 반영](OPERATIONS.ko.md#rollout)

명령은 agent-workflow 저장소 루트에서 실행합니다. Python 컨트롤러와 SQLite 원장은 표준 라이브러리를 사용합니다. React 화면을 수정할 때는 Node.js 22와 npm도 준비합니다.

| 바꾼 부분 | 확인할 동작 | 명령 |
|---|---|---|
| Python 실행기·큐·릴리스·HTTP | 임시 Git 저장소와 원장의 회귀 테스트 | `python3 -m unittest discover -s tests -v` |
| React 관제실 | 타입, 화면 상태와 생성된 자산의 일치 | 아래 빌드 명령 |
| 목표 등록·추천·큐·보류·재개 | 실제 HTTP 서버와 Chromium의 관리 화면 | `python3 tests/ui_integration.py` |
| 실행 그래프·작업자·이력 | 브라우저와 임시 SQLite 원장 | `python3 tests/mission_integration.py` |
| 프로젝트 첫 연결·OpenCode 관측 | 연결 폼과 세션 표시 | `python3 tests/onboarding_integration.py`, `python3 tests/opencode_integration.py` |

```bash
npm ci --no-audit --no-fund
npm run typecheck
npm run test:mission
npm run build
npm run check:mission
```

화면 소스는 `frontend/mission/`, 서버가 제공하는 번들은 `agent_workflow/static/`에 있습니다. 브라우저 통합 시험에는 Playwright와 해당 버전의 Chromium이 필요합니다. `WORKFLOW_PLAYWRIGHT_PATH`로 설치한 Playwright 모듈 경로를 지정할 수 있습니다. [CI 환경과 실제 실행 순서](../.github/workflows/ci.yml)

실제 모델의 로그인과 구조화 응답까지 확인하려면 `python3 tests/live_smoke.py --owner claude --reviewer codex --timeout 180`을 실행합니다. 이 검사는 계정 사용량을 소비하고 로컬 `artifacts/live-*`에 시범 저장소와 결과를 남깁니다.
