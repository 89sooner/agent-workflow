# WP 없는 일상 세션 관측 파일럿

2026-11-04. OpenCode와 Claude Code를 평소처럼 사용하며 관측하는 로컬 파일럿이다.
기존 관리형 작업·WP·검사·리뷰·큐·배치·릴리스 기능은 그대로 유지한다.
사내 로그인·SSO·팀 권한·중앙 수집 기능은 이번 범위에 없다.

## 두 화면

- /sessions: 네이티브 세션 통합 목록, 프로젝트·도구 검색, 확인 요청함, 상태 이력, 3D 공간
- /mission: 기존 관리형 작업·원장·실행·검증·이력. 서로 링크로 이동한다.

자동 발견된 세션을 workflow 작업이나 큐에 등록하지 않는다. 승인·답변·중단은 원래 도구에서 한다.
새 UI의 응답 종료/세션 종료와 관리형 작업의 검사·리뷰 완료는 다른 의미다.

## 관측 서버 시작

운영 서버를 중단하지 말고 별도 observer home과 빈 포트를 사용한다.

```bash
python3 -m agent_workflow --home /path/to/observer watch --port 8888
```

/sessions를 연다. watch는 queue worker를 시작하지 않는다.
일상 세션 이력은 observer home의 native-observation.sqlite3에만 저장하며 state.sqlite3를 만들지 않는다.
OpenCode의 메타데이터는 이 화면의 polling 조회 때 기록된다. 화면을 닫으면 수집이 멈추므로 완전한 감사 로그가 아니다.
serve --read-only는 이미 저장한 일상 세션 이력만 읽으며 OpenCode를 새로 수집하지 않는다.

## OpenCode

1. **OpenCode 연결**에서 실제 실행 중인 1.x 서버와 프로젝트 디렉터리를 연결한다.
2. 이후 평소처럼 OpenCode 세션을 만들고 작업한다. 세션마다 create/start/WP 등록은 필요 없다.
3. /sessions에서 Claude와 함께 표시한다. /mission의 기존 OpenCode 상세 관측도 유지한다.

busy/idle/retry 상태와 /permission, /question GET 목록을 읽는다.
승인·질문 목록의 sessionID만 상태 분류에 사용하며 요청 본문·질문·패턴은 보관하지 않는다.
해당 API가 없거나 형식이 다르면 경고를 표시하고 대기 상태를 추측하지 않는다.
세션 목록에서 사라진 기록은 종료로 단정하지 않고 미확인으로 보존한다.
idle은 OpenCode 상태 스냅샷의 의미이며 세션 프로세스 생존 보장은 아니다.
2.x는 기존 연결 정책대로 지원 대상이 아니다.

## Claude Code

1. **Claude Code 처음 연결하기**에 프로젝트 절대 경로를 입력한다.
2. **설치 명령 준비**로 표시된 실행 위치와 명령을 확인한다.
3. 해당 관제실 서버의 터미널에서 명령을 한 번 실행한다.

```bash
python3 -m agent_workflow --home /path/to/observer native install --project /path/to/project
```

프로젝트의 .claude/settings.local.json에 수동 결정 없는 command hook을 추가한다.
기존 permissions·hooks를 보존하고 원본 설정을 같은 폴더에 백업한다.
같은 observer에 재설치하면 이전 observer command를 교체하고 중복 등록하지 않는다.
이 명령은 모델을 호출하거나 세션을 시작하지 않는다.

Claude Code에서 설정을 재로딩하거나 새 세션을 시작한다. 이후 해당 프로젝트의 세션·하위 에이전트를 자동 관측한다.
연결 전 과거 대화를 스캔하거나 기존 실행을 강제로 재시작하지 않는다.
정확히 연결한 cwd만 수집한다. 별도 worktree/다른 cwd는 해당 프로젝트로 추가 설치해야 한다.
훅 실행 경로는 설치한 소스 위치에 연결되므로 그 checkout을 삭제하지 않는다. 소스 위치 변경 시 재설치한다.

```bash
python3 -m agent_workflow --home /path/to/observer native uninstall --project /path/to/project
```

해제는 이 observer의 훅만 제거하고 다른 설정·관측 이력은 보존한다.
상태 파일·백업은 로컬 파일 권한으로 보호한다. 동일 프로젝트에 여러 observer를 중복 설치하는 것은 피한다.

## 상태와 요청함

| 근거 | 표시 |
|---|---|
| Claude SessionStart | 유휴: 세션 시작 관측 |
| UserPromptSubmit, PreToolUse, PostToolUse | 작업 중 |
| AskUserQuestion 호출, Stop, idle_prompt | 입력 대기 |
| PermissionRequest, permission_prompt | 승인 대기 |
| PostToolUseFailure, StopFailure | 확인 필요 |
| SessionEnd, SubagentStop | 종료 |
| 마지막 Claude 훅 이후 5분 이상 | 현재 상태 미확인, 마지막 확인 상태 별도 표시 |
| OpenCode busy / idle / retry | 작업 중 / 유휴 / 재시도 대기 |
| OpenCode permission / question 목록 포함 | 승인 대기 / 입력 대기 |
| 연결 단절·알 수 없는 상태 | 관측 미확인 |

실제 훅의 도착 순서로 기록한다. 훅 누락·강제 종료를 완전하게 검출하는 heartbeat는 아니다.
확인 요청함은 입력 대기·승인 대기·확인 필요 상태를 모은다. 원래 도구에서 해결되면 다음 관측에 따라 해소된다.
종료 세션은 기본 목록과 3D에서 제외하며 **종료 이력 포함**으로 다시 본다.
상태 갱신이나 필터 변경은 작업 실행이 아니다.

## 이력·재방문·보존 범위

선택한 세션·검색어·도구 필터는 URL에 반영된다. 주소를 복사하거나 다시 열어 같은 세션과 이력으로 돌아간다.
서버가 꺼지거나 연결이 끊겨도 수집해 둔 메타데이터 이력은 남는다.
최근 세션 500개·전체 이벤트 10,000개·선택 이력 200개 제한이 있으며 오래된 기록부터 정리한다.
명시적인 보존 기간 정책이나 중앙 감사 저장소는 아니다.

프롬프트·답변·추론·도구 입력/출력 본문·Claude transcript 파일은 수집하지 않는다.
세션 ID·프로젝트 경로·이름·모델·도구 이름·상태·시각·부모 ID만 저장한다.
OpenCode 세션 제목과 프로젝트 경로 자체에 민감 정보가 포함될 수 있으므로 공유 전 확인한다.
Claude 훅 입력은 1MiB로 제한하며 파싱·저장 실패에도 승인/거부/중단 결정을 반환하지 않는다.
브라우저에서 임의 코드를 실행하거나 프로젝트 설정을 변경하는 API는 추가하지 않았다. 최초 훅 설치는 명시적인 로컬 CLI 단계다.

## 검증 범위

- 실제 Claude Code 2.1.316 --init-only: 격리 설정으로 SessionStart/SessionEnd 수집, 모델 프롬프트 없이 종료.
- 실제 OpenCode 1.3.17: 격리 서버, 빈 세션 생성 후 자동 발견, GET health/permission/question.
- 승인·입력·도구 오류·하위 에이전트·단절·재지정은 합성 payload/HTTP fixture 및 실제 Chromium으로 검증.
- 기존 전체 Python 회귀 및 관리형 관제실·연결 화면 브라우저 회귀.
- 실제 유료 모델이 작업하는 장시간 세션·모든 CLI 버전은 검증하지 않았다.

근거: [Claude 공식 훅](https://code.claude.com/docs/en/hooks), [OpenCode 서버](https://opencode.ai/docs/server/).
검사: python3 -m unittest discover -s tests, python3 tests/native_integration.py, npm run typecheck, npm run test:mission, npm run check:mission.

## 현재 파일럿

소스 /tmp/agent-workflow-native-pilot-20261104, 브랜치 codex/native-observation-pilot.
미리보기 http://127.0.0.1:8888/sessions, observer /tmp/native-pilot-preview-20261104.
기존 Katakuri 프로젝트·사용자 전역 Claude 설정·운영 8765/8776/8777·queue worker는 변경하지 않았다.
앞서 구현한 방 구조·이동 애니메이션 소스도 포함했다. 운영 배포·origin/main 병합은 별도다.

## 2026-11-05 확장

Codex와 세 도구 플러그인 방식이 추가되었습니다. 기존 직접 Claude 훅 및 OpenCode HTTP 방식은 유지합니다. [최신 설치·해제·진단 안내](NATIVE_PLUGINS.ko.md).
