# Codex 일상 관측과 세 도구 플러그인

2026-11-05 구현. 기존 WP·큐·검증·리뷰·릴리스 및 `/mission`은 유지합니다.
`/sessions`에서 OpenCode, Claude Code, Codex를 필터링하고 상태·부모 세션·관측 이력을 봅니다.
이 기능은 모델 호출, 승인 응답, 작업 할당, 큐 실행을 하지 않습니다.

## 선택할 설치 방식

- **직접 훅**: Claude Code 또는 Codex 프로젝트 설정에 관측 명령을 추가합니다.
- **플러그인**: 각 호스트에 패키지를 한 번 설치하고, 관측할 프로젝트만 등록합니다.
  Claude/Codex의 직접 훅과 플러그인은 한 프로젝트·도구에서 하나만 사용합니다.
- **OpenCode HTTP**: 기존 서버 연결 방식도 그대로 사용할 수 있습니다. 같은 프로젝트를
  HTTP와 플러그인으로 연결하면 출처가 다른 두 기록이 보일 수 있으므로 하나를 선택합니다.

## 먼저 독립 관측 환경 준비

Python 3.11+ 가상환경에 이번 릴리스의 `local_agent_workflow-0.4.1` wheel을 설치하세요.
운영 환경에 설치하거나 기존 서버를 재시작할 필요는 없습니다.

```sh
python3.12 -m venv /absolute/observer-venv
/absolute/observer-venv/bin/python -m pip install /absolute/local_agent_workflow-0.4.1-py3-none-any.whl
. /absolute/observer-venv/bin/activate
export AGENT_WORKFLOW_HOME=/absolute/my-observer-state
agent-workflow --home "$AGENT_WORKFLOW_HOME" watch --port 8891
```

`http://127.0.0.1:8891/sessions`에 접속합니다. `watch`는 큐 worker를 시작하지 않습니다.
위 포트는 예시이며, 사용 중인 포트를 종료하지 말고 비어 있는 포트를 선택하세요.
다른 터미널에서 도구를 실행할 때도 가상환경과 `AGENT_WORKFLOW_HOME`을 동일하게 설정합니다.
GUI에서 실행한 도구가 셸 환경변수를 상속하지 않을 수 있으므로 실제 수신 시각을 확인해야 합니다.
원격/클라우드 세션에는 해당 실행 환경의 설치와 접근 가능한 별도 관측 경로가 필요합니다.

## Codex 직접 연결

```sh
agent-workflow --home "$AGENT_WORKFLOW_HOME" native install --provider codex --project /absolute/project
```

`.codex/hooks.json`에 이 관측기의 훅만 추가하며 기존 훅을 보존하고 백업합니다.
Codex에서 프로젝트를 신뢰하고 `/hooks`를 열어 명령을 검토·신뢰한 뒤 새 세션을 시작하세요.
등록/설치만으로 훅이 실행되지는 않습니다. 자동 신뢰, trust hash 작성, 승인 우회는 하지 않습니다.
해제는 같은 인자로 `native uninstall`을 사용합니다. Claude는 `--provider claude`입니다.

## 플러그인으로 설치

`agent-workflow-marketplace-0.1.0.zip`을 풀거나 이 브랜치/릴리스 저장소를 사용합니다.
공개 마켓플레이스나 npm에 게시한 상태는 아닙니다. 사내 Git 또는 로컬 경로로 설치할 수 있습니다.
플러그인 압축 파일과 Python 코어 wheel은 별도이며 둘 다 필요합니다.

**Claude Code**

```text
/plugin marketplace add /absolute/marketplace
/plugin install agent-workflow-observer@agent-workflow
```

**Codex**

```sh
codex plugin marketplace add /absolute/marketplace
codex plugin add agent-workflow-observer@agent-workflow
```

Codex의 훅 신뢰 검토는 동일하게 필요합니다. 설치된 훅이 `untrusted`인 것은 정상적인 초기 상태입니다.

**OpenCode 1.x**

`plugins/opencode/index.js`를 프로젝트의 `.opencode/plugins/agent-workflow-observer.js`로 복사하거나,
배포 tarball을 설치한 다음 `opencode.json`의 기존 `plugin` 배열에 설치된 JS 경로를 추가합니다.

```sh
npm install --prefix /absolute/project/.opencode --ignore-scripts /absolute/agent-workflow-observer-0.1.0.tgz
```

OpenCode 1.3.17의 `file:` 플러그인 항목은 JS 모듈을 직접 읽으므로 tarball 경로를 넣으면 안 됩니다.

```json
{"plugin":["file:///absolute/project/.opencode/node_modules/agent-workflow-observer/index.js"]}
```

기존 배열/설정 전체를 덮어쓰지 마세요. 로컬 파일과 패키지를 동시에 설치하지 마세요.
필요하면 `AGENT_WORKFLOW_PYTHON=/absolute/observer-venv/bin/python`으로 사용할 Python을 지정합니다.

**도구별 프로젝트 등록 — 한 번만 필요**

```sh
agent-workflow --home "$AGENT_WORKFLOW_HOME" native register --provider codex --project /absolute/project
agent-workflow --home "$AGENT_WORKFLOW_HOME" native register --provider claude --project /absolute/project
agent-workflow --home "$AGENT_WORKFLOW_HOME" native register --provider opencode --project /absolute/project
```

사용할 도구만 등록합니다. 이후에는 WP나 에이전트를 수동 등록하지 않고 평소처럼 작업합니다.
정확히 일치하는 프로젝트 경로만 허용하며 하위 디렉터리/다른 worktree는 별도 등록합니다.
이미 직접 훅을 설치했다면 먼저 해당 도구의 `native uninstall`을 실행하세요.

## 정상 관측 확인과 해제

```sh
agent-workflow --home "$AGENT_WORKFLOW_HOME" native status --provider codex --project /absolute/project
agent-workflow --home "$AGENT_WORKFLOW_HOME" native unregister --provider codex --project /absolute/project
```

`registration`은 수집 동의, `last_seen`은 실제 마지막 이벤트 수신을 뜻합니다.
새 세션을 시작해도 `last_seen`이 없으면 코어 설치/Python 경로, 환경변수, 프로젝트 경로,
호스트 플러그인 활성화, Codex 훅 신뢰를 확인하세요. 명령은 worker를 시작하지 않습니다.
등록 해제 후 이력은 보존됩니다. 이후 호스트에서 이 플러그인만 제거하세요.
Codex: `codex plugin remove agent-workflow-observer@agent-workflow`.
Claude: `/plugin uninstall agent-workflow-observer@agent-workflow`.
OpenCode: 해당 배열 항목 또는 복사한 파일만 제거합니다.

## 상태·데이터의 의미

- Codex: SessionStart→유휴, UserPromptSubmit/PreModelRequest/도구 실행→작업 중,
  PermissionRequest→승인 대기, request_user_input/Stop/Interrupt→입력 대기,
  SessionEnd→종료. SubagentStart/Stop은 부모 ID와 별도 작업자를 기록합니다.
- Claude의 기존 생명주기 상태 매핑을 유지합니다.
- OpenCode 플러그인: 세션 생성/유휴/작업/재시도/삭제 및 승인·질문·도구 실행 이벤트를 기록합니다.
  승인·질문 응답 직후에는 후속 상태 이벤트가 오기 전까지 미확인으로 표시합니다.
- 플러그인/훅에서 5분간 이벤트가 없으면 미확인입니다. 이벤트가 없는 장시간 작업을
  살아 있는 작업으로 단정하지 않습니다. Stop은 WP 완료가 아닙니다.
- 저장 대상은 세션·프로젝트·도구·모델 이름, 부모 ID, 상태·시각입니다.
  대화 원문·프롬프트·도구 인자·승인 내용·응답은 저장하지 않고 transcript 파일도 열지 않습니다.
- OpenCode 플러그인은 초기 이벤트 구독 경합을 보완하기 위해 시작 후 최근 세션 메타데이터를 최대 100개 한 번 조회합니다. 새로 발견한 기록의 활동은 미확인이며 이미 받은 이벤트 상태를 덮어쓰지 않습니다. 과거 이벤트를 복원하지 않습니다.
- OpenCode 플러그인은 UI를 닫아도 이벤트를 기록합니다. 기존 HTTP 방식은 화면 polling 동안만 수집합니다.
  플러그인의 대기열은 64개로 제한되며 과부하/실패 때 누락될 수 있습니다. 완전한 감사 로그가 아닙니다.
- 저장 한도는 세션 500개, 이벤트 10,000개, 선택 세션 이력 조회 200개입니다.
  수집 프로세스 장애는 원래 도구를 막지 않도록 조용히 처리하므로 관제실의 최신 시각을 확인하세요.
- 사내 공유 인증·권한·중앙 서버와 OpenCode 2.x 지원은 이번 범위에 포함하지 않습니다.

## 빌드·검증

```sh
python3 -m unittest tests.test_native tests.test_native_plugins
node tests/plugins.mjs
python3 tests/native_integration.py
python3 scripts/package-plugins.py
python3 -m pip wheel . --no-deps --wheel-dir dist/plugins
```

배포 산출물은 `dist/plugins/`에 생성됩니다. 패키지 설치/제거 검사는 반드시 임시
CODEX_HOME/CLAUDE_CONFIG_DIR/XDG 디렉터리와 프로젝트에서 수행하세요.

공식 연동 규약: [Codex hooks](https://learn.chatgpt.com/docs/hooks),
[Codex packaging](https://developers.openai.com/plugins/build/plugins),
[Claude plugins](https://code.claude.com/docs/en/plugins-reference),
[OpenCode plugins](https://docs.opencode.ai/docs/plugins/).

### 이번 검증의 범위

- 실제 Codex CLI `0.166.0-alpha.6`: 격리 마켓플레이스 설치, app-server의 11개 훅 인식,
  `untrusted` 상태 보존. 상태 매핑은 실제 훅 실행 파일에 전달한 규약 fixture로 검증했습니다.
  사용자 신뢰 승인 후 유료 모델을 수행하는 장시간 Codex 세션은 이번에 실행하지 않았습니다.
- 실제 Claude Code `2.1.316`: 격리 설정 홈의 플러그인 설치 및 `--init-only` 시작/종료 이벤트 수신.
- 실제 OpenCode `1.3.17`: npm tarball 설치 및 별도 서버의 빈 세션 이벤트/시작 시 발견 확인.
  모델 요청은 전송하지 않았고 테스트 서버는 종료했습니다.
- 실제 Chromium: 세 도구 목록, Codex 필터·승인 상태·설치 명령, 이력·재방문, 모바일,
  기존 `/mission` 연결 및 브라우저 쓰기 요청 없음 확인.
- 외부 공개 마켓플레이스 심사, 다른 OS, 원격 호스트, OpenCode 2.x, 장시간 모델 실행은 미검증입니다.
