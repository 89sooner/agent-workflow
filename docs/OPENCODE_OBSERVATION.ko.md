# OpenCode 세션 관측

2026-11-03 · OpenCode 관측 구현 안내. 소스 병합과 운영 적용은 별도이며, 운영 8776/8777에는 아직 반영하지 않았다.

OpenCode 서버와 프로젝트를 **한 번 연결하면 이후 새 세션을 자동으로 표시**한다.
세션마다 agent-workflow `create`/`start`를 실행할 필요가 없다. 기존 Codex·Claude의
workflow 원장 관측과는 별도 연결이며, OpenCode 세션을 관리 작업으로 등록하지 않는다.

## 연결하기

기능이 포함된 checkout에서 Python 3.11 이상으로 실행한다. 기존 OpenCode 서버가
사용하는 실제 URL과 작업 디렉터리를 사용한다. URL은 포트를 포함한 loopback 주소만
지원한다. 아래 4096/8878은 예시이며 실행 중인 다른 서비스를 종료해 확보하지 않는다.

```bash
python3 -m agent_workflow --home /path/to/observer opencode connect \
  http://127.0.0.1:4096 --project /path/to/project --name '프로젝트 OpenCode'
python3 -m agent_workflow --home /path/to/observer watch --port 8878
```

브라우저에서 `http://127.0.0.1:8878/mission`을 열고 **작업 원장 / 연결**에서
`프로젝트 OpenCode`를 선택한다. OpenCode에서 평소처럼 작업하면 새 세션이 목록에
나타난다. 세션을 선택한 후 그래프 노드를 누르면 이름·모델·도구 상태를 확인한다.
**실행 기록**에서 이전 응답을 선택할 수 있다.

OpenCode 서버가 없다면 사용자가 별도 터미널에서 해당 프로젝트의 서버를 준비해야 한다.
이 연결 명령은 서버를 시작하거나 기존 대화를 옮기지 않는다. 새 서버를 시작해도
이미 다른 OpenCode 인스턴스에서 실행 중인 활동까지 이전되는 것은 아니다.
기존 인스턴스의 주소·작업 디렉터리를 연결해야 해당 실행 상태를 관측한다.
[OpenCode 서버 사용법](https://opencode.ai/docs/server/)을 참고한다.

연결은 observer home의 `opencode.json`에 저장된다. 연결 설정과 관제실이 같은 home을
사용해야 한다. 프로젝트 경로는 OpenCode 세션의 directory와 일치해야 한다. 여러 worktree를
보려면 각 작업 디렉터리를 별도 연결한다. 현재 전체 통합 프로젝트 보기는 후속 개선 대상이다.

## 인증과 연결 해제

OpenCode가 HTTP Basic 인증을 사용한다면, 비밀번호를 환경변수에 준비한 후 **변수 이름**을
지정한다. 같은 변수를 관제실 서버 프로세스에도 전달해야 한다.

```bash
# MY_OPENCODE_PASSWORD에는 기존 OpenCode 서버의 비밀번호가 준비되어 있어야 한다.
python3 -m agent_workflow --home /path/to/observer opencode connect \
  http://127.0.0.1:4096 --project /path/to/project \
  --password-env MY_OPENCODE_PASSWORD
python3 -m agent_workflow --home /path/to/observer opencode list
python3 -m agent_workflow --home /path/to/observer opencode disconnect opencode-실제연결ID
```

기본 사용자 이름은 `opencode`이며 `--username`으로 바꿀 수 있다. 비밀번호 값은 연결 파일에
저장하지 않는다. 연결 해제는 observer 설정만 지우며 OpenCode 서버와 세션을 보존한다.
이 동작은 승인·프롬프트 전송·세션 중단·파일 변경·큐 실행을 수행하지 않는다.

## 표시되는 정보와 해석

| 항목 | 의미 |
|---|---|
| 세션 목록 | 지정한 directory에서 읽은 최근 세션, 제목·네이티브 ID·마지막 갱신 |
| 실행 중 | OpenCode 상태 API의 `busy`; 로컬 workflow controller PID의 생존 여부와 다름 |
| 대기 | `idle`; OpenCode 1.3.17은 idle 세션을 상태 목록에서 제거함 |
| 재시도 대기 | `retry`; 현재 재시도 상세 본문은 수집하지 않음 |
| 상태 미확인 | 해석할 수 없는 상태 또는 끝나지 않은 과거 메시지·도구 |
| 담당자 이름 | 선택한 assistant 메시지의 `agent`; 없으면 자동 이름으로 표시 |
| 관측 모델 | 선택한 메시지의 `providerID/modelID`; 요청 모델은 별도 미기록 |
| 실행 기록 | 최근 assistant 메시지별 응답 이력; workflow 검증·리뷰 계약과 별개 |
| 그래프 선 | 메시지에 소속된 실제 도구 호출 또는 API의 부모 세션 관계 |
| 완료 | 선택한 응답/도구 종료 기록. 프로젝트 검사 통과나 배포 완료를 뜻하지 않음 |

세션 개요는 세션 제목으로 자동 표시하고, 실제 에이전트 이름과 모델은 세션 상세 조회 후
확인한다. 프롬프트·텍스트 답변·내부 추론·도구 입력/출력 본문은 반환하거나 파일에 저장하지
않는다. HTTP 메시지 조회 과정에서는 응답을 메모리에서 읽고 필요한 메타데이터만 골라낸다.
세션 제목·이름 자체에도 사용자가 작성한 내용이 있을 수 있다.

현재는 polling snapshot이다. 기본 화면 조회 간격은 프로젝트 4초·선택한 응답 2초,
연결 목록 15초이며 서버의 연결 목록 캐시와 요청 시간이 추가될 수 있다. 탭을 숨기면
조회를 멈춘다. 짧게 지나간 중간 상태의 완전한 기록을 보장하지 않는다.
연결이 끊기면 오류를 표시하고 실시간 움직임을 멈춘다. 서버가 없어도 보는 영구 이력,
이벤트 재생, SSE 스트림, OpenCode 실행 제어는 포함하지 않는다.

연결 최대 8개, 세션 최근 200개, 메시지 최근 100개, HTTP 응답 4 MiB로 제한한다.
조회 범위 밖 세션/응답은 없는 기록으로 추정하지 않고 오류 또는 잘림을 표시한다.
OpenCode 1.3.17에서 실제 검증했다. 1.x라도 API 차이는 별도 확인이 필요하며 2.x는
현재 연결 단계에서 지원하지 않는 버전으로 안내한다.

## 확인 결과와 개발 검사

- 실제 OpenCode 1.3.17: health·세션 API 조회, 브라우저를 연 뒤 만든 새 세션의 자동 발견.
- 합성 실행 fixture: 실제 이름·모델 필드, 도구 그래프, 이전 응답, 인증 실패, 단절·복구.
- 관측 요청은 GET만 사용하고 workflow DB/task/job을 생성하지 않음을 검사.
- 실제 유료 모델 호출로 생성한 실행의 수용 검증은 이번 검사에 포함하지 않았다.

```bash
python3 -m unittest discover -s tests -p test_opencode.py
python3 tests/opencode_integration.py
npm run typecheck
npm run test:mission
npm run build:mission
npm run check:mission
```

브라우저 검사는 기존 Playwright 실행 환경을 사용한다. `WORKFLOW_PLAYWRIGHT_PATH`로
경로를 지정할 수 있다. 격리된 실제 OpenCode까지 추가 검사하려면
`OPENCODE_TEST_URL`, `OPENCODE_TEST_PROJECT`를 설정한다. **이 테스트는 지정 서버에
빈 검증 세션을 추가하므로 기존 운영 세션 서버 대신 전용 테스트 인스턴스를 사용한다.**
모델을 호출하지 않으며 새 검증 세션 ID를 출력한다.

사용성 개선의 우선순위와 후속 작업은 [서비스 사용성 개선 계획](USABILITY_SERVICE_PLAN.ko.html)에 있다.
