# Agent Workflow v0.3 사용 안내

2026-10-09 · Linux / WSL 개인용

**프로젝트 설정을 한 번 등록하고, 작은 작업의 목표와 완료 조건을 지정하면 준비 → 구현 → 검사 → 독립 리뷰 → 인계를 이어간다.** 지정한 작업들을 선행 조건에 따라 연속 실행할 수도 있다. 릴리스를 설정한 작업은 같은 실행에서 푸시·병합·배포까지 진행한다.

이 문서는 현재 구현 기준이다. [v0.2 안내와 Katakuri 파일럿](USER_GUIDE.ko.md), [2026-10-08의 설계 시각화](workflow-blueprint.html)는 이전 단계의 기록이다.

## 1. 이번에 연결된 기능

| 기능 | 실제 동작 |
|---|---|
| 중단 복구 | 시간 초과·일시 오류를 한도 안에서 처리. 부분 구현은 검사 후 독립 리뷰로 연결 |
| 프로젝트 설정 | 검사·준비 명령·참조 문서·역할·모델·범위·시간대 등을 등록하고 작업에 복사 |
| 작업 환경 | 별도 worktree·브랜치 생성, 준비 명령 실행, 성공한 준비 단계 재사용 |
| 충돌·범위 확인 | 같은 저장소의 실행 중인 파일 범위·포트 예약, 허용/금지 경로와 index 변경 확인 |
| 브라우저 검사 | 지정한 로컬 서버 시작, Playwright 검사, 스크린샷·결과 수집, 서버 종료 |
| 문서·인계 | 지정 문서의 관리 구역 갱신, 검증된 코드와 연결된 인계, 작업일지 내보내기 |
| 운영 | 작업별 중단·재개, 알림 원장, 명시한 알림 명령, systemd 또는 로그인 셸 시작 연결 |
| 사용량 정책 | 역할별 모델, 호출·누적 명령 시간 한도, 보고된 토큰·금액, 명시한 일시 오류 대체 모델 |
| 연속 실행 | 지정한 WP 목록에서 선행 조건이 충족된 작업 선택. 실행 수·시간 제한과 실패 시 중단 |
| 릴리스 | 시작 전 설정 해제, GitHub Enterprise 호스트 지정, 기존 SHA·CI·배포 중복 방지 유지 |
| 대시보드 | 프로젝트 설정으로 새 작업 등록, 실행 옵션, 중단·재개, 최근 로그와 알림 |

다음 일의 사업적 우선순위·완료 조건·허용 범위는 사용자나 대화 중인 에이전트가 정한다. 전체 대화를 상시 수집하거나 새 목표를 무제한 발굴하는 기능은 없다.

## 2. 먼저 프로젝트 설정을 등록한다

Python 3.11+, Git, 사용할 Codex·Claude CLI와 해당 로그인·사용 가능량이 필요하다. 프로젝트별 빌드 도구는 별도로 준비한다. `doctor`는 경로·버전을 확인하며 실제 로그인·잔여 크레딧을 보장하지 않는다.

```bash
python3 install.py
agent-workflow doctor
```

아래 JSON을 `workflow.profile.json`으로 저장한다. **프로젝트 경로·검사·허용 경로는 실제 프로젝트에 맞게 바꾼다.** 예시는 Python 프로젝트용이다.

```json
{
  "version": 1,
  "project": "/path/to/project",
  "owner": "claude",
  "reviewer": "codex",
  "refs": [],
  "checks": [["python3", "-m", "unittest", "discover", "-s", "tests"]],
  "setup": [],
  "allowed_paths": ["src", "tests", "docs"],
  "forbidden_paths": ["secrets"],
  "workspace": {"isolate": true, "branch_prefix": "workflow/"},
  "execution": {
    "timeout": 900,
    "repairs": 1,
    "recoveries": 1,
    "max_agent_calls": 12,
    "max_seconds": 7200,
    "models": {},
    "fallback_models": {}
  }
}
```

```bash
agent-workflow profile save my-project --file workflow.profile.json
agent-workflow profile list
agent-workflow profile show my-project
```

- `checks`와 `setup`은 **argv 배열의 배열**이다. 암묵적으로 셸을 실행하지 않는다. 복합 명령은 프로젝트 스크립트로 묶는다.
- `refs`는 에이전트가 읽을 문서 경로다. 작업 worktree에 실제로 존재해야 한다.
- 경로는 저장소 기준의 파일·디렉터리다. glob은 지원하지 않는다. `allowed_paths`가 비어 있으면 저장소 전체가 범위가 된다.
- `documents`로 갱신할 문서도 허용 경로에 포함해야 한다.
- 프로필 변경은 **이미 등록된 작업의 설정을 바꾸지 않는다.** 각 작업은 설정 사본과 digest를 보관한다.
- 프로젝트 파일을 발견했다고 명령을 자동 실행하지 않는다. 사용할 설정은 `profile save`로 명시적으로 등록한다.

## 3. 작업을 등록하고 실행한다

```bash
agent-workflow create UI-LINK-001 --profile my-project \
  --goal '현재 상태의 기록 번호로 상세 기록을 열 수 있다' \
  --criterion '번호가 있으면 올바른 링크가 보인다' \
  --criterion '번호가 없으면 링크를 만들지 않는다'

agent-workflow run UI-LINK-001 --dry-run
agent-workflow run UI-LINK-001
```

`isolate: true`이면 기본적으로 상태 폴더의 `workspaces/프로필/작업ID`에 worktree를 만든다. 원본 저장소의 **커밋된 HEAD**에서 시작하며, 원본의 미커밋 변경을 복사하거나 지우지 않는다. 이미 마련한 작업 폴더를 쓰려면 프로필의 `isolate`를 끄거나 등록 시 `--no-isolate`를 지정한다.

기존 방식인 `create --project ... --check ...`도 사용할 수 있다. 자동 환경·범위·서비스 기능은 프로젝트 설정이 연결된 작업에 적용된다.

`run`과 `pipeline`은 같은 실행 경로다. **릴리스 설정이 있으면 두 명령 모두 외부 릴리스까지 진행한다.** 로컬 작업에서 끝내려면 릴리스 설정을 등록하지 않는다.

평소 대화에서는 다음처럼 요청한다.

> agent-workflow 스킬로 my-project 설정을 사용해 이 작업을 등록하고 진행해.
> 목표와 완료 조건을 현재 문서에서 확인해. 구현은 Claude, 독립 리뷰는 Codex로 해.
> 이번 작업의 종료 지점은 로컬 검증과 인계야.

## 4. 대시보드와 백그라운드 실행

```bash
agent-workflow daemon start --port 8776
agent-workflow queue UI-LINK-001
agent-workflow jobs
```

<http://127.0.0.1:8776>에서 **새 작업 등록**을 누르면 저장한 프로필을 선택할 수 있다. 목표와 완료 조건을 입력하고, 별도 작업 폴더 사용 여부를 확인한다. 등록과 실행은 별도 동작이다.

화면의 **실행 옵션**을 비워 두면 작업 설정을 사용한다. 시간 제한·수정 횟수·복구 횟수를 입력하면 그 실행에 적용한다. 작업 목록과 실행 이력은 서로 다르며, 하나의 작업에 여러 실행 시도가 남을 수 있다.

| 동작 | 명령 |
|---|---|
| 작업·증거 조회 | `agent-workflow show TASK_ID` |
| 대기 취소 | `agent-workflow cancel JOB_ID` |
| 해당 실행에만 중단 요청 | `agent-workflow stop JOB_ID` |
| 실패·중단·취소된 실행 재개 | `agent-workflow resume JOB_ID` |
| 알림 조회 | `agent-workflow notifications` |
| 서비스 종료 | `agent-workflow daemon stop` |

`stop`은 데몬 worker에 중단을 요청한다. 파일 변경이나 원격 반영을 되돌리지는 않는다. `resume`은 같은 작업·실행 종류·시간 제한·수정/복구 설정으로 새 큐 항목을 만든다. 재개 자체가 성공이나 배포 재시도 허용을 뜻하지 않는다.

실행 로그는 화면에서 최근 64 KiB를 볼 수 있다. 전체 로그 경로는 `jobs`에 남는다. 에이전트 입력·출력과 검사별 증거는 작업 상태 폴더에 있다.

## 5. 복구와 사용량 한도

새 작업의 기본 복구 횟수는 1회다. v0.2에서 등록되어 `execution` 설정이 없는 작업은 자동 복구 기본값이 0이다. 실행별로 `--recoveries 0`부터 `3`까지 지정할 수 있다.

| 중단 이유 | 처리 |
|---|---|
| 시간 초과 후 구현 변경이 남음 | 기준 HEAD를 확인하고 검사한다. 지정된 독립 리뷰도 통과해야 완료 |
| 시간 초과·일시 오류, 코드 변경 없음 | 복구 한도 안에서 같은 역할을 다시 호출 |
| 리뷰 중 코드 변경 | 현재 리뷰를 완료 증거로 쓰지 않음 |
| 권한·인증·사용량 한도·out of credits | 자동 반복·계정 전환 없이 중단. 원인 해결 후 재개 |
| 사용자 취소 | 복구로 되살리지 않음 |
| 응답 JSON 계약 위반 | 오류와 로그를 남기고 중단 |
| 배포 결과 불명확 | 자동 복구 대상에서 제외. 실제 적용 상태 확인 후 명시적으로 처리 |

부분 구현 복구는 완료 조건에 맞는 검사와 독립 리뷰가 있어야 한다. 단순히 파일이 생겼다는 이유로 성공 처리하지 않는다. `check`를 다시 실행하면 기존 리뷰는 초기화된다. 이미 유효한 검사·리뷰가 있으면 `run`으로 재개해 재사용한다.

역할별 모델과 한도는 `execution-config TASK_ID --file execution.json`으로 바꿀 수 있다.

```json
{
  "timeout": 1200,
  "repairs": 1,
  "recoveries": 1,
  "max_agent_calls": 8,
  "max_seconds": 3600,
  "models": {},
  "fallback_models": {}
}
```

`models`에는 `implement`·`review`별로 해당 CLI가 지원하는 모델명을 넣는다. `fallback_models`는 같은 역할의 대체 모델 목록이며, **일시적 실행 오류에만** 순서대로 사용한다. 계정 한도를 피해 다른 계정으로 전환하지 않는다.

사용 가능한 검토자를 명시적으로 바꾸려면 `assign`을 사용한다. Codex로 구현한 작업을 같은 공급자의 **별도 읽기 전용 세션**에서 검토하려면 `--reviewer codex-session`을 선택한다.

```bash
agent-workflow assign TASK_ID --owner codex --reviewer codex-session
agent-workflow run TASK_ID
```

이 검토는 구현 세션의 대화를 이어받지 않는 새 Codex CLI 실행이다. 실행 기록·코드 지문·작업 조건·결과가 연결돼야 하며, 단순히 통과 JSON을 만들어 `review-import`하는 것으로 대체할 수 없다. 서로 다른 공급자의 검토가 필요한 프로젝트는 기존 Claude↔Codex 조합을 유지한다. 한도 오류가 났다고 이 경로로 자동 전환하지는 않는다.

- 호출 횟수와 누적 관리 명령 시간은 재개·설정 변경 후에도 유지된다. 성공한 구현만이 아니라 실제 CLI 호출 시도도 횟수에 포함된다.
- 강제 종료로 명령의 결과가 유실되면 해당 명령에 예약한 시간은 `uncertain_seconds`로 보수적으로 잡는다.
- 토큰·금액은 CLI가 제공한 값만 남긴다. 알 수 없는 값은 `null`이다.
- `stop_after_reported_usd`는 선택 항목이다. 보고된 금액이 기준에 도달하면 이후 모델 호출을 멈춘다. 단일 호출의 최종 청구액을 미리 제한하거나 공급자 결제 상한을 보장하지 않는다.

## 6. 범위·시간대와 준비 명령

실행기는 같은 Git 저장소의 작업들 사이에서 파일 범위와 서비스 포트를 예약한다. 겹치는 범위는 동시에 관리 실행하지 않는다. 기본 데몬 worker는 여전히 한 번에 한 작업을 처리한다.

준비 명령은 프로필의 `setup`에 넣는다. `prepare TASK_ID` 또는 최초 `run`에서 실행한다. 실패하거나 결과가 불명확한 준비 명령은 상태를 확인한 뒤 `prepare TASK_ID --retry-setup`으로 재시도한다.

실행 제한 시간대 예시:

```json
{
  "blocked_windows": [
    {"timezone": "Asia/Seoul", "days": [0, 1, 2, 3, 4], "start": "18:00", "end": "21:30"}
  ]
}
```

위 조각을 전체 프로필에 넣는다. 월요일은 0, 일요일은 6이다. 자정을 넘는 구간은 시작하는 날 기준이다. 대기 작업은 제한 시간대를 기다리며, 관리 에이전트·검사 명령은 다음 제한 시각을 넘기지 않도록 시간 제한을 받는다.

경로 검사는 추적·미추적 파일과 Git index 변경을 확인한다. 이 검사는 완료 판단과 실행 중 충돌을 막는 기능이다. 도구 밖의 에디터까지 잠그거나 모든 프로세스의 파일 쓰기를 운영체제 수준에서 차단하지는 않는다. 각 CLI의 권한·샌드박스도 함께 적용된다.

## 7. 실제 브라우저 검사를 연결한다

Playwright와 Chromium이 필요하다. 선택 기능이며 일반 CLI·대시보드에는 필요하지 않다. Node에서 `playwright`를 찾거나, `WORKFLOW_PLAYWRIGHT_PATH`로 설치 위치를 지정한다. 이 환경의 web-probe 런타임도 사용할 수 있다.

프로필의 예시 조각:

```json
{
  "services": [{
    "name": "web",
    "argv": ["npm", "--prefix", "web", "run", "dev", "--", "--host", "127.0.0.1", "--port", "3137"],
    "port": 3137,
    "ready_url": "http://127.0.0.1:3137/",
    "startup_seconds": 30
  }],
  "checks": [["agent-workflow", "browser-check", "--file", "tests/workflow-browser.json"]]
}
```

서버 명령은 프로젝트의 실제 실행 방식에 맞춘다. 검사 JSON 예시:

```json
{
  "url": "http://127.0.0.1:3137/",
  "steps": [
    {"action": "visible", "selector": "#name"},
    {"action": "fill", "selector": "#name", "value": "검사 입력"},
    {"action": "click", "selector": "#submit"},
    {"action": "text", "selector": "#result", "value": "완료"}
  ]
}
```

`visible`, `fill`, `click`, `text`, `url` 단계를 지원한다. 내장 검사기는 지정한 **127.0.0.1의 동일 origin**만 허용한다. 실제 운영 API 대신 테스트용 응답·환경을 프로젝트에서 준비한다. 임의 웹앱의 API를 알아서 흉내 내지는 않는다.

검사 명령은 `WORKFLOW_EVIDENCE_DIR`, `WORKFLOW_TASK_ID`, `WORKFLOW_SERVICE_WEB_URL` 같은 값을 받는다. 성공 시 스크린샷과 `browser-proof.json`이 검사 증거에 연결된다. 실패하면 통과로 기록하지 않고 서버를 종료한다. 기존에 포트를 사용하던 다른 서버는 종료하지 않는다.

## 8. 문서·작업일지·정리

```json
{
  "documents": ["docs/workflow-notes.md"],
  "exports": {"directory": "/path/to/vault/dailywork", "timezone": "Asia/Seoul"}
}
```

- `documents`에는 지정 파일의 `agent-workflow:작업ID` 관리 구역만 생성·갱신한다. 다른 본문은 유지한다. 문서 변경은 검사·리뷰 **전에** 포함한다.
- 기존 프로젝트의 임의 체크박스나 SRS 전체를 추측해 완료로 바꾸지 않는다. 관리 구역에는 목표·구현 기록·완료 조건과 최종 원장 위치를 적는다.
- 작업일지는 `YYYY-MM-DD_TASK_ID.md`로 저장한다. 실제 검사·리뷰·릴리스 상태, 흐름도, 10줄 이내 요약을 담는다.
- 같은 작업을 다시 내보내도 파일이 중복되지 않는다. 사용자가 별도로 수정한 기존 작업일지는 덮어쓰지 않는다.
- 검증한 저장소 안으로 완료 후 작업일지를 내보내는 설정은 거부한다. 완료 증거를 스스로 바꾸지 않기 위해서다.

```bash
agent-workflow finalize TASK_ID
agent-workflow handoff TASK_ID
agent-workflow cleanup TASK_ID
```

검사 서버는 검사 종료 시 자동 정리된다. 일반 `cleanup`은 미반영 코드를 보존한다. `cleanup TASK_ID --remove-worktree`는 도구가 만든 **완료·clean·원본 HEAD에 이미 통합된** 작업 폴더만 제거한다. 강제 종료 뒤 남은 검사 서버는 `cleanup TASK_ID --stop-services`로 해당 작업의 기록과 프로세스 식별자를 확인해 정리할 수 있다.

전체 대화 export, 기존 `agent-context/_handoff` 팩·Obsidian 전체 인덱스 재구축은 이 내보내기와 별도다.

## 9. 연속 실행

먼저 실제 작업 A와 B를 등록한다. 다음 예시는 A 다음 B를 처리하되 실행 수와 시간을 제한한다.

```bash
agent-workflow depends B --on A --require completed
agent-workflow batch create small-work --task A --task B --max-jobs 2 --max-seconds 3600
agent-workflow batch start small-work
agent-workflow batch show small-work
agent-workflow batch list
```

데몬이 지정한 목록에서 실행 가능한 작업을 고른다. 실패·취소·완료 증거 부족이면 해당 묶음을 멈춘다. `max-jobs`는 대기열에 등록한 실행 시도 수의 한도이며, 실패·대기 취소도 포함한다. 등록 직후 중단돼도 큐에 저장한 소속으로 사용 횟수를 복구한다. 배치 기한이 끝나면 대기 작업을 취소하고 해당 실행을 중단한다. 이미 발생한 원격 효과를 되돌리지는 않는다.

`batch pause NAME`은 **다음 작업 예약**을 멈춘다. 이미 큐에 들어간 실행은 별도로 `stop JOB_ID`를 사용한다. `batch start NAME`으로 재개해도 기존 실행 수·기한은 초기화되지 않는다. 예산 종료 후에는 새 범위·한도로 새 묶음을 등록한다.

`completed` 선행 조건은 작업 순서의 조건이다. 별도 worktree에 선행 작업의 미커밋 변경을 자동 복사하지 않는다. 코드 상속이 필요하면 `--require merged`를 사용한다. 이 경우 아직 실행하지 않은 깨끗한 관리 worktree만 선행 병합 커밋으로 fast-forward한다. 서로 갈라진 이력을 임의로 병합하지 않는다.

## 10. 자동 시작과 알림

```bash
agent-workflow service-install --port 8776
agent-workflow service-install --port 8776 --enable
```

첫 명령은 시작 파일만 준비한다. `--enable`은 감지한 방식으로 활성화한다.

| 방식 | 실행 시점 |
|---|---|
| 사용자 systemd | 사용자 서비스가 시작될 때. 실패 시 재시작 설정 포함 |
| Bash 로그인 연결 | 대화형 로그인 셸을 열 때. 이미 실행 중인 데몬은 중복 시작하지 않음 |

`--backend systemd` 또는 `--backend shell`로 선택할 수도 있다. 이 WSL은 사용자 systemd가 offline으로 확인됐으므로 로그인 연결 방식이 맞는다. **대화형 Bash 로그인 연결은 WSL 부팅 자체나 Windows 로그인 예약과 다르다.** 자동화 명령의 비대화형 셸에서는 시작하지 않는다. WSL을 종료하면 데몬도 종료된다.

완료·실패·중단은 로컬 알림 원장과 화면에 남는다. 외부 알림은 프로필의 `notifications.command`에 사용자가 선택한 전달 명령을 등록한 경우만 실행한다. 그 명령은 `WORKFLOW_NOTIFICATION_FILE`의 JSON을 읽을 수 있다. 상대·채널·웹훅을 자동으로 찾지 않는다. 전달이 실패하거나 결과가 불명확하면 무작정 중복 전송하지 않는다.

## 11. 릴리스와 사내 사용

기존 `release-config` 사용법은 유지된다. GitHub Enterprise는 `--hostname git.company.example`처럼 실제 호스트를 지정한다. 원격 URL과 호스트·저장소가 일치해야 한다. 같은 대상 호스트로 PR·CI·병합 API를 호출한다.

```bash
agent-workflow release-config TASK_ID --repo OWNER/REPO --hostname github.com \
  --remote origin --base main --branch workflow/task-branch \
  --path src --path tests --path docs \
  --deploy 'python3 scripts/deploy.py' --health 'python3 scripts/health.py'
agent-workflow release-clear TASK_ID
```

위는 설정과 해제 형태를 보여 주는 예시다. 실제 대상과 스크립트를 확인해 사용한다. `release-clear`는 **릴리스가 시작되기 전**에만 가능하다. 시작한 릴리스의 기록을 지워 원격 효과를 숨기지 않는다.

배포 명령은 `WORKFLOW_REVISION`을 받고, health 명령은 실제 적용 버전을 확인해야 한다. 성공한 배포 뒤 health만 실패하면 다음 호출은 health부터 재개한다. 실패·불명확한 배포는 실제 상태를 확인한 뒤 `release TASK_ID --retry-deploy`로 처리한다.

사내에서는 허용된 CLI·계정·모델 연결과 Git 서버를 준비해야 한다. GitHub Enterprise의 호스트 지정 경로는 구현했지만, 사용자의 사내 서버에서 직접 실행한 결과는 아니다. GitLab·Bitbucket 전용 릴리스 API, 공동 서버용 인증·조직 권한·SSO는 포함하지 않는다. 현재 서버는 loopback 개인용이다.

## 12. 검증과 남은 경계

검증은 실제 임시 Git 저장소·관리 자식 프로세스·localhost HTTP 서버·브라우저에서 수행했다. [검증 기록](verification-v0.3.json), [구현 진행 기록](IMPLEMENTATION_PLAN.md)을 참조한다. 독립 리뷰와 최종 완료 판정은 `agent-workflow show AW-AUTOMATION-001`에서 현재 코드와 함께 확인할 수 있다.

- 실제 Claude 구현 → 검사 → Codex 리뷰 연결을 작은 임시 저장소에서 확인했다. 공급자 한도로 중단된 시도는 성공한 실행과 별도 기록한다.
- 대시보드 등록·옵션·중단·재개와 375~1440px 화면을 확인했다. 이 UI 검사는 API fixture를 사용하며 실제 사용자 큐를 변경하지 않는다.
- 내장 브라우저 검사기는 실제 버튼·입력을 확인했고, 외부 요청을 차단해 실패로 기록했다. 같은 포트의 연속 사용과 검사 서버 종료도 확인했다.
- 실제 외부 운영 배포, 사내 Git 서버, 외부 메신저 전달은 대상이 지정되지 않아 수행하지 않았다. 배포·롤백 정책은 대상 프로젝트에서 정해야 한다.
- 기본 데몬은 단일 worker다. 병렬 작업 자동 배정, 자연어 백로그 상시 발굴, 모든 문서의 의미를 자동 동기화하는 기능은 별도 확장이다.

기존 완료 작업은 그대로 보존한다. v0.3을 적용했다고 과거 파일럿이 무감독 성공이나 운영 배포로 바뀌지는 않는다.
