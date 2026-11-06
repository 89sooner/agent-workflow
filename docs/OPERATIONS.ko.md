# 관제실 운영 반영과 복구

갱신: 2026-11-05 · 이 문서는 현재 WSL 로컬 배치를 설명한다.

## 대상과 경계

| 포트 | 역할 | 코드/상태 | 변경 원칙 |
|---|---|---|---|
| 8765 | Katakuri 운영 제어 | 별도 Katakuri 서비스 | 관제실 배포로 종료·재시작하지 않음 |
| 8776 | 기본 원장 관리 서버와 worker | `/home/roqkf/agent-workflow`, `~/.local/state/agent-workflow` | jobs·batches·직접 실행 확인 후에만 갱신 |
| 8777 | H 원장 읽기 전용 서버 | 위 운영 소스, `/tmp/katakuri-h-local-queue-workflow-20261029` | `serve --read-only` 유지 |
| 8877 | 과거 스냅샷 미리보기 | 별도 preview home | 운영 배포 대상으로 간주하지 않음 |
| 8880 | React 개발 미리보기 | 별도 feature worktree와 observer home | 운영과 구분; 운영 확인 URL로 대체하지 않음 |
| 8884 | 첫 연결 개선 미리보기 | `/tmp/agent-workflow-usability-20261104`, `/tmp/agent-workflow-usability-observer-20261104` | `watch`; 설정만 변경 가능, worker 없음. 임시 미리보기의 생존 여부는 접속 시 확인 |

새 관제실은 `/mission`, 기존 관리 화면은 `/`다. 메타데이터 `server.json`에서 URL·PID·
process_identity·read_only를 확인하고 `/proc/<pid>/cmdline`, cwd, 실제 리스너와 대조한다.
파일에 적힌 PID만 보고 임의 프로세스를 종료하지 않는다. `server.lock`은 대시보드 잠금이고
`daemon.lock`은 worker 잠금이다. 읽기 전용 서버는 worker를 만들지 않는다.

## 연결 UI의 운영상 의미

개선된 사용법은 [화면에서 프로젝트 연결](CONNECTION_SETUP.ko.md)에 있다. 소스 병합만으로
기존 프로세스가 새 Python API를 불러오는 것은 아니다. 정적 UI와 서버 capability를 각각 확인한다.
`GET /api/capabilities`의 `mode`, `can_configure_connections`, `can_manage_tasks`로 현재 모드를 확인한다.
응답의 요청 토큰은 공개 로그나 보고서에 남기지 않는다.

- `watch`: 연결 설정과 관측 메타데이터(근거·북마크)를 저장할 수 있다. workflow 작업·큐·모델 실행과 worker를 시작하지 않는다.
- `serve --read-only`: 연결 설정을 포함한 POST를 차단한다. 사용자는 지원 범위와 제한 안내를 볼 수 있다.
- 일반 `serve`: 연결 설정 가능. 관리 링크는 자신의 기본 원장에만 표시하며 기존 worker는 계속 동작한다.

`connections.json`과 `opencode.json`은 해당 관제실의 home에 저장된다. 기존 연결을 유지하려면
업그레이드·이전 시 이 파일도 보존한다. OpenCode 인증 환경변수는 새 서버 프로세스에 별도로 전달한다.
읽기 전용 연결을 위해 기존 관리 서버를 중단할 필요는 없다. 별도 observer home과 빈 포트의
`watch`에서 연결 UI를 사용할 수 있다. 2026-11-05 사용자는 이번 통합 관제실 변경의 운영 반영도 승인했다. 배포 전후 상태와 실제 브라우저 결과를 별도로 기록한다.

## 반영 순서

1. 작업 브랜치의 소스·문서·빌드 자산을 검사하고 지정된 독립 리뷰 결과를 확인한다.
   검사와 리뷰는 동일한 코드/참조 지문에 연결되어야 한다. 리뷰 실패·quota·권한 오류를
   통과로 바꾸거나 native receipt를 수작업으로 만들지 않는다.
2. 실제 `origin`과 `main`을 fetch한다. 보호 규칙과 CI를 확인한 PR을 병합하고 병합 SHA를 고정한다.
3. 운영 checkout의 미커밋 변경을 확인한다. 사용자의 변경을 reset/clean으로 제거하지 않는다.
4. 읽기 전용 SQL로 두 원장의 queued/running jobs, 활성 batch를 확인한다. jobs가 비어 있어도
   `run/check/pipeline/release` 직접 실행이 있을 수 있으므로 실제 프로세스까지 확인한다.
   진행 작업을 임의 중단하지 않고 완료 또는 안전한 반영 시점을 기다린다.
5. SQLite online backup으로 DB와 스키마를 보존하고 이전 소스 SHA, 서버 PID/identity/cwd/모드,
   jobs·batches 스냅샷을 남긴다. 활성 WAL이 있는 DB를 파일 하나만 복사하지 않는다.
6. 8776·8777의 정확히 확인된 서버만 정상 종료한다. 8765와 다른 에이전트는 유지한다.
   운영 checkout을 검증한 `origin/main` SHA로 fast-forward한다.
7. 재시작 직전 큐·batch를 재확인한다. 새 대기 작업이 생겼으면 일반 `serve`를 무심코 시작하지 않는다.
   8776은 원래 관리 모드, 8777은 원래 읽기 전용 모드로 시작한다. 변경된 state home을 쓰지 않는다.
8. `/mission` 및 JS/CSS/아이콘·라이선스의 HTTP 200, 파일 해시, CSP, 원장 목록,
   최근 H task/run을 확인한다. 실제 브라우저에서 작업·이전 run·그래프·3D·이력을 선택한다.
   연결 UI의 지원 범위, 빈 화면·오류 안내, 각 서버의 설정/관리 권한도 확인한다.
   신규 연결 저장 검사는 운영 서버 대신 별도 observer/fixture에서 먼저 수행한다.
9. 운영 HEAD = `origin/main` = 배포 목표 SHA인지 확인하고, 실제 8776/8777 URL과 health를 기록한다.
   합성 fixture 결과, 실제 원장 조회, 원격 CI, 독립 리뷰, 운영 반영은 별도 증거로 남긴다.

## 시작 명령의 의미

```bash
cd /home/roqkf/agent-workflow
# 이미 서버가 실행 중이면 중복 실행하지 않는다.
# worker를 포함하므로 jobs와 batches를 먼저 확인한다.
python3 -m agent_workflow --home "$HOME/.local/state/agent-workflow" serve --port 8776
# H 관측은 반드시 읽기 전용이다.
python3 -m agent_workflow --home /tmp/katakuri-h-local-queue-workflow-20261029   serve --read-only --port 8777
```

이 환경에서는 `/home/roqkf/.pyenv/versions/3.12.3/bin/python3`를 사용한다.
`serve --read-only`도 server.json/server.lock을 관리하므로 같은 home의 서버는 하나만 띄운다.
별도 미리보기가 필요하면 별도 observer home에서 연결하고 `watch`를 사용한다.

## 실패와 복구

- 병합 전 검사/리뷰/CI 실패: 운영 소스를 바꾸지 않고 원인을 수정한다.
- 병합 후 health 실패: 병합 사실을 보존하고 배포 성공으로 표시하지 않는다. 성공 여부가
  불명확한 deploy를 반복하지 말고 실제 PID·리스너·자산·SHA를 먼저 확인한다.
- 이전 버전으로 서버를 되돌릴 때는 보존한 SHA의 별도 checkout에서 원래 home/모드로 시작한다.
  SQLite backup 복원은 이후 정상 작업 기록을 지울 수 있으므로 자동 rollback에 포함하지 않는다.
- 사용자 변경이 생기면 강제 checkout/reset으로 덮지 않는다. 현재 코드·작업과 충돌을 해결한 뒤 진행한다.
- 과거 검사 로그를 현재 SHA의 결과로 재사용하지 않는다. 코드·검사 계약이 달라지면 새 검증을 기록한다.

로컬 상태 DB와 전체 실행 로그는 `artifacts/`의 해당 배포 증거 폴더에 보존하며 공개 Git에 포함하지 않는다.

## 정적 UI만 변경된 경우

배포 전후 Python·서버·worker 코드가 같고 /api/capabilities 등 필요한 API가 정상 제공되는 경우에는 서버 재시작 없이 checkout을 fast-forward할 수 있다. 서버는 요청마다 정적 자산을 읽고 no-store로 제공한다. 이때도 원장 온라인 백업·jobs/batches·직접 실행·프로세스 식별자를 기록하고 배포 후 HTTP 해시 및 실제 브라우저를 검사한다. 서버·worker PID와 시작 식별자가 그대로인지 확인한다. 기존 탭은 새로고침해야 새 JS를 불러온다. API 미지원이나 Python 변경이 있으면 이 절차로 전체 배포를 완료했다고 판단하지 않는다.

## 통합 관제실 릴리스의 운영 조건

- 원래 모드와 home을 유지한다. 8776은 관리 모드와 기존 기본 home, 8777은 H home의 엄격한 읽기 전용 모드다.
- 8776 `/overview`는 명시적으로 연결한 기본 원장과 H 원장을 함께 조회한다. 원장 DB를 합치지 않는다. 8777 `/mission`은 기존 H 직접 관측 URL을 유지한다.
- `/sessions`의 일상 관측은 해당 home에 프로젝트/훅/플러그인을 연결한 뒤 시작한다. 배포만으로 기존 대화나 모든 프로세스를 가져오지 않는다. 읽기 전용 8777에서 설정·북마크 저장은 차단한다.
- `jobs`가 queued/running이거나 활성 batch·직접 실행이 있으면 worker가 포함된 관리 서버를 재시작하지 않는다. 전환 직전과 시작 직전에 재확인한다.
- 이번 배포는 SQLite online backup, 기존 로컬 문서의 외부 백업과 이름 있는 stash, 병합 SHA로 fast-forward, 서버 모드 검증, HTTP 자산 해시·CSP·API 및 Chromium 검증을 포함한다.
- 작업 원장: `/tmp/aw-observatory-release-state-20261105`, 작업 `AW-OBS-PUBLISH-20261105`. 실제 배포/health·브라우저 증거: `/tmp/aw-observatory-deploy-20261105/`. 토큰·DB·전체 로그는 공개 Git에 포함하지 않는다.
- 기능 사용법: [통합 관제실 사용 안내](OBSERVATORY_REL01_USAGE.ko.md). 개발 검증과 잔여 조건: [검증 기록](OBSERVATORY_VALIDATION_2026-11-05.ko.md). 사용자 파일럿은 미완료, 비용 기능은 보류다.

## 2026-11-06 격리 복구 리허설

[준비 검증 기록](READINESS_VALIDATION_2026-11-05.ko.md)에 실제 리허설 결과를 남겼다.
운영 기본·H 원장과 실제 관측 원장을 읽기 전용 online backup으로 복제하고,
별도 `/tmp` 경로에서만 DB 손상·복원과 이전 SHA 소스 롤백을 수행했다.
복제 관제실은 엄격한 `serve --read-only`였으며 새 queue worker도 시작하지 않았다.
운영 서버나 원본 DB를 복구 대상으로 사용하지 않았다.

도구는 `scripts/rehearse-recovery.py --help`로 확인한다. 새 임시 출력 디렉터리와
명시적인 이전 커밋 SHA를 요구하며, 입력 home과 출력 경로가 겹치면 거절한다.
실제 원장의 내용·백업·브라우저 증거는 비공개 로컬 경로에 저장한다.

이번 결과는 **백업 당시의 격리 복제본**에 대한 복구 증거다. 운영 중 발생한
새 기록을 보존하면서 과거 백업으로 자동 복원해도 된다는 승인은 아니다.
실제 운영 복원은 장애 범위·마지막 정상 시점·이후 기록을 확인한 뒤 결정한다.

## 2026-11-06 준비 검증 변경의 운영 반영

실제 병렬 호스트 관측, OpenCode 2.x의 명시적 V2 패키지 진입점, 최대 24명 3D
instancing·가구 공유·거리별 세부 형상·MSAA 조정과 복구 도구를 이번 릴리스에 포함한다.
독립 검토에서 발견한 V1 단일 파일 설치 회귀는 기존 자급형 진입점을 복원하고
복사 설치 검사를 추가해 해결했다. 기존 WP·일상 세션·실행 이력 기능은 유지한다.

2026-11-06 사용자는 신규 사용자 3명 파일럿을 이번 릴리스의 필수 조건에서 제외하고
origin/main 병합 및 기존 운영 반영을 승인했다. 사용자 파일럿의 실제 성공 결과는 없다.
릴리스 작업은 `AW-READINESS-RELEASE-20261106`, 증거는
`artifacts/readiness-release-20261106/`다. 배포의 성공 여부는 실제 영수증으로 확인한다.

- 기본 통합 관제실: `http://127.0.0.1:8776/overview`, 일상 세션: `/sessions`.
- H 관제실: `http://127.0.0.1:8777/mission`, 기존 H state home과 읽기 전용 모드 유지.
- Python 이벤트 매핑 변경도 포함하므로 정적 자산 제공만으로 반영 완료를 판단하지 않는다.
- 8776은 웹 서버와 worker가 같은 프로세스다. 기존 jobs·batches·직접 실행이 모두
  유휴임을 직전에 재확인한 뒤 정상 종료·재시작하며 큐 항목을 추가·재시도하지 않는다.
- DB는 온라인 백업하고 기존 task·event·run·span과 jobs·batches를 보존한다.
  8765, 다른 모델 세션·프로젝트 설정과 테스트 외 프로세스는 배포 대상이 아니다.
- 운영 갱신 후 기존 브라우저 탭은 새로고침한다. 작업자 목록에서 선택·가까이 보기,
  3D/목록 전환, 이전 실행 선택, 이벤트·실행 이력을 확인할 수 있다.
- `/sessions`의 도구별 프로젝트 등록·훅 신뢰는 기존 조건을 따른다. 운영 반영만으로
  미등록 프로젝트의 세션이 생기지는 않는다. OpenCode V2 설치는
  [플러그인 안내](NATIVE_PLUGINS.ko.md)의 명시적 `v2/` 경로를 사용한다.
