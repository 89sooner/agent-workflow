# Agent Workflow 사용·운영 매뉴얼

**어제 하던 일을 이어갈 때는 Git 저장소에서 `agent-workflow pickup`을 실행한다.** 작업 요약과 다음 행동을 확인할 수 있고, 같은 작업 트리·브랜치의 등록 작업이 있으면 현재 코드에서 검사가 여전히 유효한지도 확인할 수 있다. 새 기능을 구현부터 검사·독립 리뷰까지 맡길 때는 목표와 검사를 작업으로 등록한다.

2026-11-19 로컬 소스 기준. 패키지 버전은 `agent-workflow --version`, 설치본의 실제 옵션은 `agent-workflow 명령 --help`에서 확인한다. 기존 릴리스에 같은 버전 문자열이 있을 수 있으므로 새 명령의 제공 여부는 도움말로 확인한다. 이 문서는 개발자·프로젝트 담당자·로컬 관제실 운영자를 위한 안내서다. 업무별로 준비물, 실행 순서, 기대 결과와 실패 후 조치가 필요한 경우 [상세 사용 사례](USE_CASES.ko.md)를 사용한다. 당시 파일럿과 제약은 [v0.2 기록](USER_GUIDE_V0.2.ko.md)에 보존했다.

| 하려는 일 | 시작할 곳 |
|---|---|
| 오늘 작업을 닫고 다른 에이전트로 이어가기 | [등록 없는 세션](#sessions) |
| 어떤 검사를 붙일지 결정하고 작업 등록 | [프로필과 추천](#profiles) |
| 대화형으로 진행하거나 두 에이전트에게 맡기기 | [구현·검사·리뷰](#execution) |
| 여러 작업을 순서대로 처리하거나 중단한 실행 복구 | [큐·보류·배치](#queue) |
| 프로젝트·세션을 한 화면에서 보기 | [관제실과 연결](#observe) |
| 로컬 완료와 배포 확인을 구별하고 재개 | [릴리스](#release) |
| 오류·누락된 기록을 진단하거나 새 설치로 이전 | [운영·복구](#ops) |
| 기억나는 명령 이름으로 동작 찾기 | [CLI 찾아보기](#commands) |

<a id="setup"></a>

## 1. 설치와 첫 확인

Linux 또는 WSL의 Python 3.11 이상과 Git이 필요하다. Python 코어는 표준 라이브러리를 사용한다. 일반 관리 화면과 관제실의 빌드 자산은 패키지에 들어 있다. 모델을 호출할 때는 선택한 Codex·Claude CLI와 로그인, 사용 가능량이 필요하다. GitHub 릴리스에는 해당 원격의 Git 접근과 인증된 `gh`가 필요하다. 브라우저 수용 검사에는 Node.js, Playwright, Chromium을 준비한다.

소스에서 설치할 때는 **agent-workflow 저장소 루트**에서 실행한다.

```bash
python3 install.py
export PATH="$HOME/.local/bin:$PATH"
agent-workflow --version
agent-workflow doctor
agent-workflow --help
```

설치기는 `~/.local/bin/agent-workflow`를 소스의 실행 파일에 연결한다. 이미 존재하는 `.codex`, `.claude`, `.claude-soonho`, `.claude-alex` 홈에는 공용 스킬을 연결한다. 같은 이름의 다른 대상과 충돌하면 경로를 출력하고 중단한다. 이 방식은 연결한 소스 디렉터리를 계속 사용한다. 별도 Python 환경에서는 빌드한 wheel을 설치하거나 저장소 루트에서 `python3 -m agent_workflow --help`로 CLI를 시험할 수 있다. `doctor`의 `available`은 실행 파일을 찾았다는 의미다. 계정 인증·한도·모델 응답은 실제 호출에서 확인한다.

**처음부터 끝까지 시험하기.** 아래 예시는 새 임시 상태 폴더에 Git 저장소, 로컬 bare 원격, 시범 사이트를 만든다. 모델 로그인이나 GitHub 계정이 없어도 된다. 명령은 같은 셸에서 순서대로 실행한다.

```bash
trial_state=$(mktemp -d)
agent-workflow --home "$trial_state" demo-setup
agent-workflow --home "$trial_state" release-status DEMO-RELEASE
agent-workflow --home "$trial_state" release DEMO-RELEASE
agent-workflow --home "$trial_state" release-status DEMO-RELEASE
```

첫 조회에서 `local_completion=succeeded`, `merge=not_attempted`를 확인한다. 릴리스가 끝나면 `merge=succeeded`, `deployment=applied`, `health=succeeded`를 확인한다. 모든 Git·배포 효과가 해당 시범 폴더에 남는다. 일반 프로젝트는 상태 폴더 **밖에 있는 커밋된 Git 저장소**를 사용한다.

### 경로와 식별자

| 항목 | 의미와 위치 |
|---|---|
| 프로젝트 | 대상 Git 작업 트리. `--project`는 작업 내용을 읽거나 검사할 위치다. 격리 작업은 생성된 worktree 경로를 사용한다. |
| 상태 홈 또는 원장 | `--home PATH` → `AGENT_WORKFLOW_HOME` → `~/.local/state/agent-workflow` 순으로 정한다. 전역 옵션인 `--home`은 하위 명령 **앞**에 둔다. 홈이 다르면 같은 작업 ID도 다른 작업이다. |
| 작업 ID | 목표·완료 조건·검사·리뷰를 묶는 원장의 ID. `create` 때 지정하거나 `start`로 생성한다. |
| 실행 ID | `queue`가 반환하는 `id`. `jobs`에서 찾고 `stop`, `cancel`, `resume`에 전달한다. 작업 하나에 재시도 이력이 여러 개일 수 있다. |
| 프로젝트 프로필 | 상태 홈에 저장된 신뢰할 수 있는 설정. 저장소의 JSON은 `profile save`로 등록한 뒤 사용한다. |
| 호스트 세션 ID | Claude Code·Codex·OpenCode가 생성한다. 세션 메타데이터 조회와 원래 도구로 복귀할 때 사용한다. |

<a id="sessions"></a>

## 2. 등록 없는 세션과 사용 이력

```bash
agent-workflow close-session --summary '모바일 검색 결과 정렬을 수정하고 관련 테스트를 통과했다.' \
  --next '좁은 화면에서 정렬 버튼의 접근성 이름을 확인한다.'
agent-workflow pickup
```

Git 저장소 또는 하위 디렉터리에서 실행한다. 다른 위치에 있으면 두 명령에 `--project /경로/저장소`를 붙인다. 공용 스킬을 사용하는 세션에서는 “세션 마감하고 다음 작업을 남겨줘”, “지난 작업 이어서 진행해”로 요청할 수 있다.

- **마감**은 작업 트리와 브랜치별로 요약, 다음 행동, 당시 HEAD와 변경 경로를 담은 작업일지와 재개 메모를 저장한다. 반환된 `worklog`, `resume`은 실제 파일 경로다. 같은 상태·문구로 바로 재시도하면 기존 메모를 재사용한다.
- **외부 일지 사본**은 `close-session`에 `--worklog-dir`과 기존 폴더를 지정하면 된다. 대상 파일시스템은 하드 링크를 지원해야 한다. 사용자 편집과 충돌하면 기존 사본을 보존한다. 내보내기 실패 시 오류에 표시된 로컬 일지가 복구 지점이다.
- **재개**는 마지막 메모의 존재 여부 `found`, 현재 Git 상태, 기존 `agent-context` 인계 색인·요약·할 일 문서, 최근 등록 작업을 보여준다. 메모의 경로 상태 비교는 이름과 Git 상태 코드를 사용한다. 내용 차이는 `git diff`로 읽는다. 이에 비해 등록 작업의 검사 증거는 파일 내용과 참조 문서도 비교한다. [검증 결과 해석](#execution)
- 이 경량 메모에는 직접 입력한 요약과 다음 행동이 들어간다. 호스트의 전체 대화 내보내기나 기존 프로젝트의 인계 패키지가 필요한 경우 해당 도구의 절차도 사용한다.

`patterns`는 로컬 CLI 입력 이력의 월별·주제별 빈도, 세션 수, 명령 조합을 집계한다. 기본 입력은 홈의 Codex·Claude 이력과 Claude 추가 프로필이며 설정된 별도 CLI 홈도 조사한다. `--source`를 지정하면 해당 `history.jsonl`들로 범위를 지정한다. 원문과 임의의 명령 인자는 출력하지 않는다.

```bash
agent-workflow patterns --since 2026-10-01 --until 2026-10-31
```

날짜 양끝은 UTC 기준으로 포함한다. 여러 주제가 한 입력에 동시에 잡힐 수 있다. 같은 세션에서 두 명령을 사용한 횟수는 실행 순서나 실제 완료의 증거와 구별한다.

<a id="profiles"></a>

## 3. 프로필·검사 선택·작업 등록

**프로필**은 반복 사용할 기본 검사를 담는다. **검사 묶음**은 특정 업무에 추가할 검사다. 다음 예시는 agent-workflow 저장소 루트에서 개발용 프로필을 등록한다. `aw-dev`가 다른 설정에 사용 중이라면 별도 이름을 선택한다. `save`는 같은 이름의 설정을 갱신한다.

```bash
agent-workflow profile save aw-dev --file profiles/agent-workflow-development.json
agent-workflow profile list
agent-workflow profile show aw-dev
agent-workflow profile checks aw-dev --check-group handoff
```

`profile checks` 결과의 `available_groups`는 선택 가능한 이름, `check_groups`는 선택 결과, `checks`는 실행될 인자 배열이다. 기본 검사를 먼저 실행하고 선택한 묶음과 개별 검사를 추가한다. 묶음을 고른 경우 이미 포함된 명령을 추가로 중복 실행하지 않으며 기본 목록에 의도적으로 넣은 반복은 유지한다. 이름이 틀리면 등록 전에 오류가 난다. Katakuri용 프로필에는 합성 백테스트와 선택 가능한 뷰어 검사가 있다. [예제 프로필과 등록 위치](../profiles/README.md)

### 변경에 맞는 검사 추천

추천은 설치된 모델 CLI의 로그인을 사용하며 실제 호출 시 공급자 사용량이 발생할 수 있다. Codex 추천 호출에는 사용자 설정·규칙을 무시하는 옵션이 적용되고, Claude 추천 호출은 제한 모드를 사용한다. 일반 대화의 사용자 지정 공급자·모델 라우팅 설정과 다를 수 있다. 이 설치에서 실측한 버전은 Codex 0.170.0과 Claude 2.1.325이다. 이전 CLI가 제한 옵션을 지원하지 않으면 추천 호출은 실패한다.

작업에 필요한 검사를 사람이 고르기 어려울 때 `recommend-checks`가 Git 변경과 완료 조건을 비교한다. 모델은 프로필에 있는 선택 묶음의 이름과 근거를 반환한다. 기본 검사 수와 설명, 묶음 설명을 모델에 제공하고, 실행할 명령은 로컬 프로필에서 가져온다. 프로필에 `base_check_description`과 `check_group_descriptions`를 작성하면 각 검사가 어떤 동작을 다루는지 이름만으로 추측하는 일을 줄일 수 있다.

1. **전송할 입력 확인.** `--preview`는 로컬에서 입력과 사용할 공급자를 출력한다. 기본 대상은 프로필 프로젝트의 HEAD 기준 변경이며, 스테이징 전후의 추적 파일 diff와 추적되지 않은 파일의 경로를 포함한다. 다른 기준 커밋은 `--base REF`, 같은 Git 저장소의 별도 worktree는 `--project PATH`로 지정한다. 알려진 비밀 경로·패턴과 프로필 금지 경로를 제외하지만 새 형식의 민감 정보나 완료 조건에 직접 적은 개인정보가 있을 수 있다. 미리보기의 `input.change`와 `change_summary`에서 패치, 제외된 경로 수, 잘림 여부를 확인한다.
2. **추천 방식 선택.** 기본 제공자는 `codex`다. `--reviewer auto`는 보이는 변경이 둘 이상의 최상위 영역에 걸치거나 입력이 잘리면 다른 공급자를 호출한다. 루트 파일들은 한 영역으로 계산한다. `--reviewer none`은 한 모델만 사용한다. 검토자에 다른 공급자를 직접 지정하면 두 모델이 같은 입력을 독립적으로 선택한다. 선택 묶음이 없는 프로필은 모델 호출을 건너뛴다. `--timeout`은 모델 호출당 초 단위 한도이며 기본 90초, 허용 범위는 1~600초다.
3. **결과 검토.** `primary`와 `review`에 각 모델의 설명, `comparison`에 일치한 이름 및 한쪽만 고른 이름이 나온다. `suggested_groups`는 합집합이다. 출력의 `checks`는 추천대로 선택했을 때 실행할 명령 목록이다. 필수 조건을 충족하는 이름만 다음 등록 명령에 `--check-group`으로 넣는다. 추천 자체는 원장 등록이나 검사를 실행하지 않는다.

```bash
agent-workflow recommend-checks aw-dev --criterion '재개 시 바뀐 코드의 검사 결과를 표시한다.' --preview
report_dir=$(mktemp -d)
agent-workflow recommend-checks aw-dev --criterion '재개 시 바뀐 코드의 검사 결과를 표시한다.' \
  --provider codex --reviewer claude > "$report_dir/handoff.json"
```

완료 조건이 여럿이면 `--criterion`을 반복한다. 실제 추천은 선택한 공급자의 CLI 로그인과 사용량을 이용한다. 첫 모델이 실패하면 CLI 오류와 종료 코드 2를 반환한다. 두 번째 호출만 실패하면 종료 코드 3과 `status=review_incomplete`, 첫 모델의 결과를 반환한다. 자동화에서는 종료 코드와 상태를 모두 읽는다. 두 번째 결과가 꼭 필요하면 해당 공급자 오류를 해결하고 다시 실행한다.

**추천 품질 확인.** 개발자가 이번 변경에 실제로 필요하다고 판단한 **선택 묶음**을 완료된 추천 JSON과 대조한다. 예를 들어 인계 테스트만 추가하면 충분했다고 판단했다면 아래처럼 실행한다.

```bash
agent-workflow evaluate-checks "$report_dir/handoff.json" --expected-group handoff
```

필요한 묶음이 여러 개이면 `--expected-group`을 반복한다. 기본 검사만으로 충분했다면 `--expected-none`을 사용한다. `primary`와 `combined`에서 빠뜨린 항목(`missed`), 불필요한 항목(`extra`), 정확히 일치했는지(`exact_match`)를 읽는다. 이 비교는 로컬에서 수행한다. 실제 코드의 통과 여부는 등록된 검사를 실행해 판정한다. 구현 완료 후의 독립 코드 리뷰는 별도 단계다. [검사 선택과 누락 검토 사례](USE_CASES.ko.md)

### 작업 공간과 등록 방식

```bash
agent-workflow create HANDOFF-01 --profile aw-dev --no-isolate --check-group handoff \
  --goal '재개 시 변경된 코드의 검사 상태를 표시한다.' \
  --criterion '검사 이후 파일 내용을 고치면 이전 결과에 재검사 필요가 표시된다.'
agent-workflow show HANDOFF-01
agent-workflow run HANDOFF-01 --dry-run
```

위 예시는 **현재 작업 트리**와 미커밋 변경을 포함한다. 프로필의 기본 격리 모드에서는 커밋된 HEAD로 새 worktree·브랜치를 만들고, 실제 작업 위치는 `task.project`에 표시한다. 이미 등록된 작업은 프로필 설정과 검사 목록의 사본을 갖는다. 프로필을 수정한 뒤 새 작업을 생성하면 변경된 설정을 적용한다.

프로필 없이 등록할 때는 `create ID --project 경로 --goal … --criterion … --check '명령 인자'`를 사용한다. `--criterion`, `--ref`, `--check`는 반복할 수 있다. 저장소에서 이미 사용하는 검사를 지정한다. 명령은 shell을 거치지 않는 argv이므로 파이프와 리다이렉션이 필요한 기존 절차는 프로젝트 스크립트로 등록한다. refs는 구현·리뷰·검증에서 고려할 문서 경로다. WP·SRS가 있으면 완료 조건과 함께 참조한다.

반복 등록할 프로젝트는 한 번 연결해 둘 수 있다. 아래 예제는 현재 프로젝트에 사용하려는 프로필이 이미 등록된 원장 경로를 사용한다.

```bash
agent-workflow connect . --ledger "$HOME/.local/state/agent-workflow" --profile aw-dev \
  --criterion '등록한 필수 검사 통과'
agent-workflow start '인계 문서의 링크를 확인한다.' --project . --check-group handoff
```

`start`가 반환한 `state_home`과 `task.id`로 조회·실행한다. 등록 후보 원장이 여러 개면 `connect --registration-home PATH`로 선택한다. `start`는 등록까지 수행한다. 실행은 등록된 상태를 확인한 뒤 `run` 또는 `queue`로 시작한다.

### 프로필에서 구성할 수 있는 항목

| 설정 | 적용 시점과 용도 |
|---|---|
| `project`, `owner`, `reviewer`, `refs` | 대상 저장소와 구현·검토 담당, 참조할 파일을 등록한다. 상대 프로젝트 경로는 등록할 때의 작업 디렉터리에서 해석한다. |
| `checks`, `check_groups`, `setup` | 각각 공통 검사, 이름 붙인 추가 검사, 작업 환경 준비 argv를 둔다. 추천 입력에는 선택 항목인 `base_check_description`과 `check_group_descriptions`로 각 검사의 범위를 설명할 수 있다. `prepare`가 이미 성공한 준비 단계를 재사용한다. |
| `workspace`, `allowed_paths`, `forbidden_paths` | 격리 여부·브랜치 접두사·작업 폴더와 변경 범위를 정한다. 격리 환경에서 필요한 의존성도 준비해야 한다. |
| `services`, `blocked_windows` | 검사 동안 띄울 로컬 서비스와 준비 URL을 지정하고, 실행 제한 시간대를 구성한다. |
| `execution` | 역할별 모델, 일시 오류 대체 모델, 호출 횟수·누적 시간·수정/복구 한도를 설정한다. 금액 제한은 CLI가 보고한 비용을 이용한다. |
| `documents`, `exports`, `notifications` | 관리할 Markdown 블록, 작업일지 내보내기 디렉터리와 시간대, 외부 알림 명령을 선택한다. |

필드별 JSON과 로컬 서버의 준비 상태, 포트 충돌, 문서 관리 예제는 [프로필·자동화 설정](AUTOMATION_V0.3.ko.md)을 참조한다.

<a id="execution"></a>

## 4. 구현·검사·리뷰와 증거 해석

| 단계 | 대화형 진행 | 자동 진행 |
|---|---|---|
| 작업 이해 | `show ID`, `brief ID --role implement`에서 목표·완료 조건·참조 문서·다음 행동 확인 | `run ID --dry-run`으로 검사, 역할, 한도, 릴리스 설정 확인 |
| 구현 | 요청된 범위를 수정하고 `checkpoint ID --summary '내용' --next '다음 행동'` | `run ID` 또는 `pipeline ID`로 지정한 구현 CLI 실행 |
| 검증 | `check ID`가 실제 검사 프로세스를 실행하고 종료 코드와 코드·참조 지문 보관 | 실패 원인을 다음 수정 시도에 전달하고 설정된 한도까지 반복 |
| 독립 검토 | `brief ID --role review`, `result-schema`로 실제 검토자에게 검토 요청. 결과를 `review-import`로 등록 | 지정한 검토자가 별도 세션에서 검토 |
| 마감·인계 | `finish ID`로 증거 확인 후 완료 처리, `handoff ID`로 재개 자료 생성, `finalize ID`로 내보내기 | 실제 검사와 지정 리뷰 통과 후 마감. 릴리스가 설정되어 있으면 다음 단계까지 수행 |

검토에 사용할 수 있는 담당은 `claude`, `codex`, `codex-session`, `none`이다. 구현과 검토는 서로 다른 담당으로 지정한다. `codex-session`은 구현 대화를 물려받지 않는 새 Codex 검토 세션이며 실제 호출 영수증을 확인한다. `none`을 선택한 작업도 실제 검사를 통과해야 완료 처리할 수 있다.

수동 검토는 검토 **시작 시점**의 `show` 결과에서 `observed_git.fingerprint`와 참조 문서가 있을 때 `refs_fingerprint`를 기록한다. 실제 독립 검토자가 돌려준 JSON의 `outcome`, `summary`, `next_action`, `findings`를 확인하고 해당 지문·검토자 이름과 함께 `review-import`에 넘긴다. 검사 재실행은 예전 리뷰를 초기화한다. 유효한 검사·리뷰가 이미 있다면 `run`의 재개 경로가 이를 재사용한다. [수동 검토 명령과 JSON 예제](../skills/agent-workflow/references/commands.md)

**상태 읽기.** 일반적인 작업은 `ready` → `implementing` → `checking` → `needs_review`/`reviewing` → `ready_to_finish` → `completed`를 거친다. 독립 리뷰를 생략한 작업은 검사 뒤 바로 마감할 수 있다. `blocked`이면 `show`의 `blocker`, 마지막 검사 결과·리뷰 지적을 읽는다. 현재 코드가 바뀌면 원장에 과거 `completed`가 남아도 그때의 완료 증거는 만료될 수 있다. `show` 등 조회는 JSON을 출력한다. `check`·`run`처럼 실제 실행하는 명령은 결과 앞에 진행 로그도 출력하므로, 다른 도구에서 결과를 읽을 때는 후속 조회 명령을 사용한다.

| `pickup`의 등록 작업 `evidence` | 판단과 다음 행동 |
|---|---|
| `verification=current` | 코드 내용·인덱스·참조·보호 경로와 검사 통과 기록이 맞는다. 지정된 리뷰 상태를 이어서 확인한다. |
| `stale` | 기준 코드 또는 참조가 달라졌다. `reason`에서 원인을 읽고 열린 작업은 다시 검사한다. 마감된 작업의 새 코드에는 새 작업을 등록한다. |
| `failed` 또는 `missing` | 현재 입력에서 검사가 실패했거나 결과가 없다. 실패 로그를 보거나 유효한 검사 명령을 등록하고 실행한다. |
| `unavailable` | 지문을 확인할 수 없다. 참조 파일, 접근 권한, 외부 Git 필터 사용 여부를 확인한다. |
| `review=not_required` | 해당 작업이 리뷰 생략으로 등록됐다. 검사·완료 증거는 개별 필드에서 확인한다. |

`rerun_checks`와 `reason`을 함께 읽는다. 검사 명령이 없거나 결과를 판단할 수 없을 때 `null`이 나올 수 있다. `check-add ID '명령'`으로 필수 검사를 보태면 이전 검사·리뷰의 근거가 무효화된다. 현재 작업을 다른 세션에 전달할 때 `handoff ID`가 반환한 디렉터리의 `resume.txt`, `state.json`, `worklog.md`를 사용한다. `finalize`의 외부 내보내기에서는 사용자가 편집한 작업일지를 보존한다.

<a id="queue"></a>

## 5. 백그라운드 실행, 보류와 의존성

등록된 작업 ID가 있고 사용할 원장과 실행 범위를 확인했다면 아래 경로를 사용한다. 포트는 해당 컴퓨터에서 비어 있는 번호를 고른다.

```bash
agent-workflow daemon start --port 8879
agent-workflow queue HANDOFF-01
agent-workflow jobs
agent-workflow notifications
agent-workflow daemon status
```

- `queue`의 기본 액션은 `pipeline`이며 릴리스 설정이 있으면 릴리스까지 이어진다. 이미 로컬 마감한 작업의 릴리스만 대기시키려면 `--action release`를 선택한다. 같은 작업을 중복 대기·실행할 수 없다.
- 대기 중인 실행은 `cancel JOB_ID`, 실행 중인 큐 작업은 `stop JOB_ID`로 제어한다. `stop`은 중단을 요청하므로 `jobs`의 최종 상태를 확인한다. 재개 가능한 `failed`·`interrupted`·`cancelled` 실행에는 `resume JOB_ID`를 사용한다. 원래 작업·실행 옵션에 연결된 새 큐 항목이 만들어진다.
- `hold ID --reason '사유'`는 작업의 큐 시작·다음 단계를 보류한다. 현재 실행 단계는 종료될 때까지 진행될 수 있다. `unhold ID`로 해제하면 대기 상태의 실행이 다시 진행될 수 있다. 직접 포그라운드에서 실행한 프로세스는 해당 터미널에서 제어한다.
- `daemon stop`은 서버 전체에 적용한다. 개별 실행을 제어할 때는 작업 ID와 실행 ID를 구별한다. WSL 종료 등으로 실행이 끊겼다면 같은 원장에서 `jobs`로 중단된 항목을 찾고 로그·작업 폴더를 확인한 뒤 재개한다.
- `service-install --backend auto --port 8879`로 systemd 사용자 서비스 또는 Bash 로그인 방식 설치 파일을 준비한다. 자동 시작을 활성화하려는 경우 `--enable`을 추가한다. Bash 방식은 해당 셸 로그인에 의존한다.

여러 작업을 차례로 처리할 때는 같은 원장에 **먼저 등록된 ID**만 선택한다. 예를 들어 A의 병합 결과를 B가 사용해야 한다면 실행 전에 `depends B --on A --require merged`를 지정한다. `completed` 조건은 로컬 완료 증거를 요구한다. 격리 worktree가 선행 변경을 상속해야 하는 경우 병합 조건과 소스 브랜치를 확인한다.

```bash
agent-workflow depends B --on A --require merged
agent-workflow batch create delivery --task A --task B --max-jobs 2 --max-seconds 3600
agent-workflow batch start delivery
agent-workflow batch show delivery
```

데몬 worker가 준비된 항목을 처리한다. `batch list`로 묶음을 찾고, `batch pause delivery`로 후속 실행 등록을 보류한다. 이미 큐에 들어간 실행은 `jobs`에서 따로 확인한다. 순환 의존성이나 기한 초과는 등록·진행 오류로 출력된다.

<a id="release"></a>

## 6. 커밋·병합·배포와 상태 조회

자동 릴리스할 작업은 **깨끗한 기준선**에서 등록한다. 대상 원격, 병합할 기본 브랜치와 별도 작업 브랜치, 자동 커밋할 상대 경로, 사용할 CI, 배포 명령·상태 검사 명령을 결정한다. GitHub 모드는 `--repo OWNER/REPO`와 실제 원격 URL을 대조한다. 사내 GitHub Enterprise는 `--hostname`으로 해당 호스트를 지정한다. `--mode local`은 절대 경로로 지정한 로컬 Git 원격을 사용하는 시범·검증에 쓴다.

```bash
agent-workflow release-config SITE-01 --mode github --repo OWNER/REPO --remote origin \
  --base main --branch workflow/site-01 --path src --path tests \
  --deploy 'python3 scripts/deploy.py' --health 'python3 scripts/health.py'
agent-workflow pipeline SITE-01 --dry-run
agent-workflow release-status SITE-01
```

위 명령의 ID·저장소·경로·배포 및 상태 스크립트를 실제 프로젝트 값으로 바꾼다. 배포가 필요 없으면 `--deploy`, `--health`를 모두 생략한다. `release-config` 자체는 설정을 기록한다. `run`·`pipeline`은 설정된 릴리스를 실행할 수 있고, 이미 로컬 완료한 작업은 `release ID`로 이어간다. 설정을 취소하려면 릴리스가 시작되기 전에 `release-clear ID`를 사용한다.

GitHub 경로에서는 검증된 변경을 커밋하고 원격 작업 브랜치에 푸시한 뒤 PR과 후보 SHA의 CI를 확인한다. 병합된 리비전에서도 등록된 필수 검사를 수행한다. CI가 의도적으로 없는 저장소에만 `--allow-no-ci`를 선택한다. 배포·상태 확인 명령은 병합 리비전의 checkout에서 실행되며 `WORKFLOW_REVISION`, `WORKFLOW_TASK_ID`를 사용할 수 있다. 상태 스크립트는 해당 버전이 실제 제공되는지 확인하는 내용을 구현해야 한다.

| `release-status ID`의 필드 | 읽는 법 |
|---|---|
| `local_verification`, `local_completion` | 원장에 남은 검사와 마감 결과다. 현재 코드에서 재사용할 수 있는지는 `pickup` 또는 `show`로 조사한다. |
| `merge`, `merge_commit`, `pull_request` | 병합 영수증의 존재, 병합 SHA와 PR 번호를 읽는다. `uncertain`은 완료 여부를 단정할 증거가 부족하다는 뜻이다. |
| `integration_check`, `integration_revision` | 통합 검사의 결과와 검사한 SHA를 확인한다. 로컬 후보 검사와 원격 기본 브랜치 병합을 각각 판독한다. |
| `deployment`, `health`, `deployment_revision` | `applied`와 `failed`가 함께 나오면 배포 적용은 끝났고 후속 상태 검사가 실패했다. 최종 상태 확인을 마친 리비전을 따로 표시한다. |
| `error`, `updated_at` | 마지막 릴리스 오류와 기록 시각이다. 조회는 로컬 원장을 읽으므로 과거 기록은 현재 서비스 생존 여부와 구별한다. |

적용 성공 후 상태 검사만 실패했다면 원인을 고치고 `release ID`로 상태 검사부터 재개한다. 적용 자체가 실패했거나 결과가 불명확하면 실제 배포 대상에 반영된 버전을 먼저 확인한다. 다시 적용해야 할 때만 `release ID --retry-deploy`를 사용한다.

<a id="observe"></a>

## 7. 관제실, 세션 관측과 결과 연결

| 실행 모드 | 용도 | 가능한 변경 |
|---|---|---|
| `serve --port PORT`, `daemon start --port PORT` | 단일 기본 원장의 관리 화면과 worker 운영 | 기본 원장 작업 등록·큐·중단·재개와 관측 설정. 연결된 다른 원장은 조회용이다. |
| `watch --port PORT` | 여러 원장과 프로젝트의 기록을 모으는 관측 서버 | 관측용 연결·근거·북마크 등 자체 메타데이터를 저장한다. worker는 시작하지 않는다. |
| `serve --read-only --port PORT` | 이미 있는 DB와 연결을 읽기 전용으로 제공 | 변경 API가 차단되고 worker는 시작하지 않는다. |

셋 모두 루프백에 노출된다. 예를 들어 관측만 시작하려면 `agent-workflow --home "$HOME/.local/state/aw-observer" watch --port 8878`을 실행하고 같은 컴퓨터에서 `http://127.0.0.1:8878/overview`를 연다. 원래 관리 서버와 관측 서버를 같이 띄울 때 상태 홈과 포트를 분리한다. 이 WSL의 기존 운영 포트와 수신 프로세스 확인 방법은 [운영 절차](OPERATIONS.ko.md)에 있다.

| 화면 | 할 수 있는 일 |
|---|---|
| `/` 관리 | 현재 원장과 프로젝트 선택, 작업 생성, 검사 묶음 선택, 실행 옵션, 대기열·보류·로그·릴리스 결과 확인 |
| `/overview` 통합 | 프로젝트별 관리 작업과 네이티브 세션을 함께 찾고 연결 상태·진단·확인 요청을 탐색 |
| `/mission` 실행 관제실 | 원장·프로젝트·작업·실행을 선택해 에이전트 활동, 단계 그래프, 이벤트, 상세 근거, 3D·목록과 과거 실행 재생 확인 |
| `/sessions` 일상 세션 | 프로젝트·공급자·상태 필터, 부모/하위 세션, 마지막 수신 시각, 결과 근거, 북마크·기간별 통계 확인 |

**관리 작업 연결**은 화면의 프로젝트 연결이나 CLI `connect`를 사용한다. **작업 ID가 없는 일상 세션**에는 다음 중 환경에 맞는 관측 경로를 설정한다.

- **Claude Code·Codex 직접 훅.** 관측 홈을 지정해 `native install --provider claude --project /프로젝트` 또는 `--provider codex`를 실행한다. Codex는 호스트의 프로젝트·훅 신뢰 설정을 확인하고 새 세션을 시작한다. `native status`로 최근 수신과 등록을 확인하고 직접 훅 해제는 `native uninstall`로 수행한다.
- **세 공급자 플러그인.** 호스트에 플러그인을 설치한다. 예를 들어 Claude는 `native register --provider claude --project /프로젝트`로 등록한다. Codex는 `codex`, OpenCode는 `opencode`를 공급자로 선택한다. `native unregister`가 프로젝트 등록을 해제한다. 한 공급자·프로젝트에서 직접 훅과 플러그인 사용이 겹치면 이벤트가 중복될 수 있다. [호스트별 설치·신뢰·진단](NATIVE_PLUGINS.ko.md)
- **OpenCode HTTP 연결.** 이미 실행 중인 로컬 OpenCode 서버의 URL을 `opencode connect URL --project /프로젝트`에 넣는다. 비밀번호가 있으면 `--password-env`에 환경변수 **이름**을 지정하고 관측 서버 프로세스에도 값을 전달한다. `opencode list`로 출처 ID를 확인하고 `opencode disconnect ID`로 연결을 해제한다. [OpenCode 연결 방법](OPENCODE_OBSERVATION.ko.md)

워크플로 홈이 다른 서버에서 사건을 찾고 있으면 목록이 비어 보일 수 있다. 필터와 선택한 프로젝트, 연결 소스·훅, 최근 수신 시각 순으로 확인한다. 호스트 측의 실제 사용자 승인이나 응답은 해당 도구에서 한다. 세션 상세에서는 원래 세션 ID·프로젝트 경로를 복사하거나, 특정 원장 작업·GitHub PR 주소를 결과 근거로 연결할 수 있다. 북마크는 저장된 이벤트 구간을 내보낸다. 명시적 결과가 없는 호출과 보존 기간을 지난 이벤트는 미확인으로 해석한다. [관측 화면·알림·통계](OBSERVATORY_REL01_USAGE.ko.md) · [그래프·3D와 실행 재생](REACT_MISSION_CONTROL.ko.md)

<a id="ops"></a>

## 8. 실패 복구와 운영

| 증상 | 확인할 기록 | 다음 행동 |
|---|---|---|
| 작업이나 세션이 보이지 않는다 | 사용한 `--home`, `list`, `start`가 반환한 홈, 프로젝트·브랜치, 관측 소스 | 같은 원장을 지정한다. 일상 세션은 `native status`나 `opencode list`, UI의 수신 시각을 확인한다. |
| 검사가 실패하거나 실행 중 소스가 바뀌었다 | `show ID`의 검사별 종료 코드·출력, 현재 지문, `blocker` | 실패를 수정한다. 되살릴 미완료 작업은 같은 ID로 재개한다. 기존 완료 뒤의 새 목표에는 새 ID를 사용한다. |
| 환경 준비나 로컬 서비스가 실패한다 | setup·service 로그, 실제 포트 점유, 준비 URL, 실행 제한 시간대 | 원인을 고친 뒤 실패한 준비는 `prepare ID --retry-setup`으로 재개한다. 작업이 남긴 검사 서비스는 `cleanup ID --stop-services`로 확인·정리한다. |
| 모델 호출 시간 초과, 일시 오류, 남은 수정이 있다 | 작업의 `failure`, `recoveries`, 사용 한도, 실제 Git 변경 | 유한 복구 안에서 재시도한다. 남은 구현은 실제 검사와 지정 리뷰로 확인한다. 한도 밖이면 원인 해결 후 `run ID`로 재개한다. |
| 인증·권한·quota·계약에 맞지 않는 응답 | 실행 로그, 공급자 오류 종류, `show ID`의 담당과 사용량 | 로그인·권한·가용량을 점검한다. 담당을 변경할 경우 `assign ID --owner … --reviewer …`로 명시한다. |
| 배치나 큐가 움직이지 않는다 | `jobs`, `batch show NAME`, `daemon status`, 보류·선행 조건·시간 한도 | worker·원장을 확인한다. 선행 작업이 필요한 상태까지 마감/병합됐는지 보고 재개한다. |
| 검사·리뷰의 현재성을 확인할 수 없다 | `pickup`의 `evidence.reason`, `show`의 참조·보호 경로, Git 작업 트리 | 파일·경로를 복구한 뒤 다시 조회한다. 마지막 원장 기록과 현재 검증을 구별한다. |
| 배포 도중 중단됐다 | `release-status ID`, 실제 대상의 버전과 상태 | 적용과 상태 검사를 독립적으로 확인한 뒤 [릴리스 복구](#release) 절차를 따른다. |

`execution-config ID --file 정책.json`으로 열린 작업의 역할별 모델, 호출·시간 제한, 오류 복구 정책을 갱신할 수 있다. 이전 사용량이 이어진다. `--repairs`는 결함 수정의 추가 시도, `--recoveries`는 시간 초과·일시 오류 등에 적용할 복구 한도다. 공급자 요금은 해당 CLI가 보고한 범위에서 확인한다. 구현·리뷰에는 등록된 에이전트 통신 정책이 적용된다. [에이전트 통신 정책](AGENT_COMMUNICATION.ko.md)

**저장과 이전.** 상태 홈의 `state.sqlite3`에는 작업·큐·이벤트 등 관리 상태가 있고, `tasks/ID/`에는 요청·응답·검사별 로그와 인계 파일, `jobs/`에는 큐 로그가 있다. `session-notes/`는 경량 마감 메모, `connections.json`, `opencode.json`은 관측 연결, `native-observation.sqlite3`는 일상 세션 관측에 사용한다. 로그에는 소스·프롬프트·명령 출력이 포함될 수 있다. 서버가 가동 중인 DB는 SQLite 온라인 백업으로 보존하고 상태 홈의 관련 파일도 함께 보관한다. 다른 컴퓨터로 이동하면 절대 경로, Git worktree 위치, 실행 파일, 로그인과 환경변수를 재확인한다.

**업데이트.** 설치 방식을 확인한 뒤 소스 링크 또는 패키지를 갱신한다. 서버는 기존 Python 코드를 메모리에 유지할 수 있으므로 실제 반영 전에 대기·실행 중인 jobs, 활성 배치, 직접 실행 중인 프로세스를 확인한다. 운영 변경 시 해당 원장·모드·URL에서 상태와 관제 화면을 검증한다. `cleanup ID --remove-worktree`는 완료·깨끗함·원본 HEAD에 통합됐음을 확인한 도구 소유 작업 폴더만 제거한다. [원장 백업, 갱신과 복구](OPERATIONS.ko.md)

<a id="commands"></a>

## 9. CLI 찾아보기

공통 형식은 `agent-workflow [--home PATH] 명령 …`이다. 명령의 필수 옵션·허용값은 설치본에서 `--help`로 확인한다.

| 목적 | 명령과 용도 |
|---|---|
| 설치·시범 | `doctor` 실행 파일·버전 확인, `demo-setup` 로컬 시범 원격과 작업 준비 |
| 경량 세션 | `close-session` 마감, `pickup` 인계·검증 결과 조회, `patterns` 입력 이력 집계 |
| 작업·연결 | `create` 명시 ID 등록, `connect` 프로젝트·원장·프로필 연결, `start` 자동 ID 등록, `list` 작업 목록, `show` 작업과 현재 증거 조회 |
| 검사 계획 | `profile save/show/list/checks` 설정 저장·조회·검사 미리보기, `recommend-checks` 변경 기반 검사 묶음 추천, `evaluate-checks` 저장된 추천 품질 확인, `check-add` 열린 작업에 검사 추가 |
| 준비·담당 | `prepare` 환경 준비, `assign` 구현·검토자 변경, `execution-config` 모델·한도 설정 |
| 대화형 수행 | `brief` 역할별 입력, `checkpoint` 진행 기록·다음 행동, `check` 검사 실행, `result-schema` 리뷰 스키마, `review-import` 독립 검토 결과 등록, `finish` 로컬 완료 |
| 자동 수행·인계 | `run`·`pipeline` 유한 자동 실행, `handoff` 인계 파일, `finalize` 작업일지와 설정된 내보내기, `cleanup` 환경 정리 |
| 대기열·보류 | `queue`, `jobs`, `cancel`, `stop`, `resume`으로 실행 관리. `hold`, `unhold`로 개별 작업 보류·해제. `notifications`로 결과 알림 기록 조회 |
| 순차 작업 | `depends` 선행 작업과 조건 등록, `batch create/show/list/start/pause`로 작업 묶음 관리 |
| 릴리스 | `release-config` 대상 등록, `release-clear` 시작 전 설정 해제, `release` 릴리스 수행·재개, `release-status` 기록된 결과 조회 |
| 화면·서비스 | `serve` 전경 서버, `watch` 여러 원장의 관측 서버, `daemon start/stop/status` 백그라운드 서버, `service-install` 자동 시작 설정 |
| 호스트 관측 | `native install/uninstall` 직접 훅 관리, `native register/unregister/status` 프로젝트별 플러그인 관측 연결·진단. `opencode connect/list/disconnect` HTTP 소스 관리 |
| 화면 검사 | `browser-check --file 시나리오.json`을 등록된 검사 명령에 넣는다. `WORKFLOW_EVIDENCE_DIR`에 Playwright 결과를 기록한다. 같은 로컬 HTTP 출처의 `visible`, `click`, `fill`, `text`, `url` 단계를 지원한다. |

프로필 예제와 작업별 절차는 [상세 사용 사례](USE_CASES.ko.md), 화면 가이드와 버전별 기록은 [문서 목록](README.md)에서 찾을 수 있다.
