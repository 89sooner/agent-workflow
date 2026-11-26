# 실제 프로젝트 설정

프로필은 프로젝트의 검사, 변경 범위, 역할, 작업 폴더, 실행 한도를 묶는다.
파일을 저장소에 추가하는 것만으로 실행되지는 않는다. 사용할 파일을 명시적으로 등록한다.

[문서 찾기](../docs/README.md) · [프로필 등록과 검사 선택](../docs/USER_GUIDE.ko.md#profiles)

<a id="project-goals"></a>

## 프로젝트 목표 모드

프로필의 `goal_mode`에 정책 버전 `1`, 개발 범위 `scope`, 저장소 안의 지침 파일 목록 `instructions`,
허용할 UTF-8 근거 파일 확장자 `input_extensions`를 선언한다. `refs`로 추가 자료를 연결한다.
작업할 파일이나 디렉터리를 `allowed_paths`에 명시하고, 실행 가능한 검사와 독립 리뷰어를 등록한다.
목표를 등록할 때 컨트롤러는 커밋된 HEAD에서 별도 작업 트리를 만들고, 그 사본에서 지침·참조 파일을 확인한다.
원장에 등록 당시 프로필과 정책 지문이 남으므로 나중에 같은 이름의 프로필을 갱신해도 기존 작업은 당시 정책으로 재개한다.

다음은 agent-workflow 저장소 루트에서 사용하는 예다. 검사와 대상 파일을 포함한 커밋을 기준으로 새 작업을 등록한다.
아래 등록은 모델을 호출하지 않는다. `goal run`을 실행하거나 데몬에 `queue`하면 모델 호출 예산을 사용한다.

```bash
agent-workflow profile save aw-resume-goal --file profiles/agent-workflow-resume-goal.json
agent-workflow goal create AW-RESUME-02 --profile aw-resume-goal \
  --goal '재개 패킷에서 코드 지문이 바뀐 검사 근거를 식별한다' \
  --criterion '코드가 달라진 검사 근거에는 검사 당시와 현재 코드 지문 앞 12자리가 순서대로 표시되고, 참조만 바뀌거나 근거가 유효하면 이 표시가 없다.'
agent-workflow goal status AW-RESUME-02
agent-workflow goal run AW-RESUME-02
```

완료 조건을 생략하면 읽기 전용 계획 단계가 조건을 나눈다. 대기 중인 조건에는
`goal supply ID --criterion N --ref FILE`로 자료를 제출한다. `.txt`, `.md`, `.json`, `.csv` 중
프로필이 허용한 형식만 제출할 수 있다. 빈 목록은 외부 근거 제출을 사용하지 않는다는 뜻이다.
`goal budget ID`로 한도를 조정하고 `goal run ID`로 같은 작업을 재개한다.
관제실에서는 **새 작업 등록 → 프로젝트 설정 → 프로젝트 목표 연속 실행**에서 등록한다.
프로필의 준비·검사 명령은 로컬 권한으로 실행되므로 해당 프로젝트에서 신뢰하는 명령을 지정한다.

Katakuri 목표는 기존 `katakuri-goal create`로 등록한다. 전용 지침·인계, 종목·마일스톤, 로컬 개발 제한이 적용된다.
이미 등록한 Katakuri ID는 `katakuri-goal status/run`과 `goal status/run` 양쪽에서 같은 원장 행을 사용한다.
[일반 목표 프로필](agent-workflow-resume-goal.json) · [두 프로젝트 검증 기록](../pilots/p2-goal-mode/README.md)

## 업무별 검사 묶음

| 프로필 파일 | 공통 검사 | 선택할 수 있는 묶음 |
|---|---|---|
| [agent-workflow-development.json](agent-workflow-development.json) | 작업 등록·실행 회귀 테스트 | `handoff`, `release`, `dashboard` |
| [katakuri-backtest-tests.json](katakuri-backtest-tests.json) | Katakuri 백테스트 드라이버의 합성 입력 검사 | `viewer`로 같은 데이터의 뷰어 표현도 검사 |
| [katakuri-offline-engine.json](katakuri-offline-engine.json) | 파일 기반 엔진 CLI의 종목·버전별 입력, 출력과 보관 자료 검증 | 공통 검사만 사용 |

`kb-backtest-tests`의 수정 범위는 테스트 파일 두 개다. 오프라인 엔진의 CLI 구현·명세·테스트를 함께 수정할 때는 `katakuri-offline-engine.json`을 사용한다. [목표 등록 예제](../docs/KATAKURI_GOAL.ko.md#cli)

다음 명령은 이 저장소 루트에서 실행합니다. Katakuri 프로필은 현재 WSL의 프로젝트 경로를 사용합니다. 작업 범위는 두 회귀 테스트 파일의 정비로 지정했으며, 뷰어 묶음은 외부 시세 파일이 필요한 시험을 제외합니다. 이 프로필은 주문 전송과 서비스 배포를 수행하지 않습니다.

```bash
agent-workflow profile save kb-backtest-tests --file profiles/katakuri-backtest-tests.json
agent-workflow profile checks kb-backtest-tests --check-group viewer
```

`profile checks`는 선택된 명령과 순서를 보여줍니다. `create --profile kb-backtest-tests --check-group viewer`로 등록된 작업에서 `check ID`를 실행하면 백테스트와 뷰어 검사가 모두 실행됩니다.

## Katakuri 웹 fixture

`katakuri-web-fixtures.json`은 이 WSL의 Katakuri 웹 시험 데이터 정비에 사용한다.
실제 Ops API의 임시 응답과 fixture를 비교하고 기존 웹 경계 시험을 실행한다.
LS나 운영 서버에 접속하는 명령은 포함하지 않는다. UI 동작 변경이나 엔진 작업에는
그에 맞는 검사와 범위를 가진 별도 프로필이 필요하다.

```bash
agent-workflow profile save katakuri-web-fixtures --file profiles/katakuri-web-fixtures.json
agent-workflow profile show katakuri-web-fixtures
```

| 설정 | 값과 의미 |
|---|---|
| 프로젝트 | `/home/roqkf/katakuri-band`의 커밋된 HEAD |
| 구현 / 검토 | Claude / 별도 Codex 실행 |
| 환경 준비 | Python 3.11 이상과 기존 오프라인 검사 의존성 확인 |
| 작업 폴더 | 기본 원장의 `workspaces/katakuri-web-fixtures/<ID>`에 생성 |
| 허용 범위 | 웹 fixture와 자기 인계 기록. 구체적인 작업은 이보다 좁게 등록 |
| 자동 검사 | `generate_web_fixtures.py --check`, `pytest tests/test_web_boundary.py` |
| 한도 | 프로세스당 600초, 수정 1회, 복구 1회, 에이전트 호출 5회, 누적 명령 2,400초 |
| 작업일지 | 이 도구의 `artifacts/worklogs/`에 자동 내보내기 |
| 릴리스 | 미설정. 구현과 검증, 인계까지 실행 |

새 작업에는 새 ID와 현재 문서의 목표를 사용한다. 완료한 작업을 재실행해 새로운 작업으로
바꾸지 않는다. [실제 R29 작업의 브리프](../pilots/KB-WEB-FIXTURE-R29/execution-brief.md)는
한 번 실행한 범위이며 다른 작업의 권한을 대신하지 않는다.

### 조건별 검사 결과

새 버전의 웹 fixture 프로필을 등록하면 목표 모드에서 다음 완료 조건을 사용할 수 있다.
필요한 문장만 `--criterion` 또는 관제실의 완료 조건에 그대로 입력한다.

| 완료 조건 | 연결되는 검사 |
|---|---|
| 웹 fixture가 생성기 결과와 일치한다 | 등록된 생성기 비교 명령의 종료 코드와 시간 초과 여부 |
| 빌드된 웹 번들의 경계 검사가 통과한다 | 웹 경계 pytest 실행의 JUnit 보고서에서 `tests.test_web_boundary::test_16_bundle_scan_when_built`의 결과 |

프로필의 `criterion_checks`는 조건 문장을 공통 `checks`에 한 번 등록된 명령과 연결한다.
`testcase`를 지정하면 검사 명령이 그 실행의 `WORKFLOW_EVIDENCE_DIR/junit.xml`에
작성한 UTF-8 pytest JUnit 보고서(최대 8 MiB)에서 정확히 한 건을 찾는다.
명시한 조건과 자동 계획에서 확정된 조건 모두 문장이 정확히 일치할 때 적용된다.
문구가 다른 조건은 기존 독립 리뷰 절차를 따른다. 수정한 프로필은 새로 등록하는 작업에 적용된다.

`katakuri-goal status ID`의 `criteria[].check_evidence`와 관제실의 **조건 검사**에서
실행 ID, 명령, 관측 시각, 코드·참조 지문을 확인할 수 있다. 보고서를 읽은 경우 SHA-256도 남는다.
관제실의 **원본 검사 결과 보기**는 같은 작업의 해당 실행으로 이동한다.
CLI의 `verification_is_current`는 저장한 코드·참조 지문이 여전히 유효한지 표시한다.
Git이 무시하는 빌드 산출물의 상태는 검사 시점의 관측값이다.
번들 시험이 필수인데 생략됐다면 목표가 대기한다. 허용된 절차로 목표 작업 트리에 번들을
준비한 뒤 같은 목표를 다시 실행하면 검사와 독립 리뷰를 이어간다.
필수로 지정하지 않은 번들 시험의 생략은 fixture 일치 조건의 완료를 막지 않는다.

Katakuri는 LF 작업 폴더를 요구한다. 이 호스트의 다른 Git 설정과 독립적으로 실행하려면
작업을 등록하고 실행하는 터미널에 다음 설정을 적용한다. Git 설정 파일은 변경하지 않는다.

```bash
export GIT_CONFIG_COUNT=2
export GIT_CONFIG_KEY_0=core.autocrlf
export GIT_CONFIG_VALUE_0=false
export GIT_CONFIG_KEY_1=core.eol
export GIT_CONFIG_VALUE_1=lf
```

이 예시는 기존 `GIT_CONFIG_COUNT` 설정이 없는 터미널을 전제로 한다.
기존에 지정한 Git 환경 설정이 있으면 덮어쓰지 말고 해당 목록에 두 항목을 합친다.
다른 PC에서는 JSON의 `project`, `exports.directory`를 실제 경로로 바꾼다.
검사 도구와 사용할 CLI의 로그인은 그 PC에서 준비한다.

현재 프로필의 준비 명령은 의존성 존재를 확인한다. 패키지를 다운로드하거나 자동 설치하지
않는다. 준비가 실패하면 로그의 누락 항목을 해결한 뒤 `prepare ID --retry-setup`으로 재개한다.
프로필을 수정해도 이미 등록한 작업의 설정 사본은 바뀌지 않는다.
