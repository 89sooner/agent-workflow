# Agent Workflow 실전 사용 사례

이 문서는 **무엇을 입력하고, 어느 결과를 보고, 어떤 경우에 다음 단계로 넘어가는지** 설명한다. 설치와 전체 명령 목록은 [사용 매뉴얼](USER_GUIDE.ko.md)에 있다.

| 지금 할 일 | 읽을 사례 |
|---|---|
| 처음 등록한 검사가 실제로 동작하는지 확인 | 1. 임시 Git 프로젝트에서 처음부터 마감까지 |
| 어떤 테스트를 추가할지 결정 | 2. 단일 모델 추천 / 3. 독립 모델과 비교 / 4. 추천 품질 측정 |
| Claude에서 멈춘 일을 Codex에서 재개 | 5. 세션 인계와 증거의 최신성 |
| 실제 프로젝트의 백테스트나 화면을 변경 | 6. 프로젝트별 검사 선택 |
| 구현과 독립 코드 리뷰를 맡기거나 대기열에서 복구 | 7. 관리형 작업 / 8. 선행 작업과 대기열 |
| 배포가 됐는지, 재시도해도 되는지 확인 | 9. 병합·배포·상태 점검 |
| 등록하지 않은 세션 관측 또는 반복 업무 분석 | 10. 프로젝트 연결·관측 / 11. 사용 패턴 분석 |

## 1. 임시 Git 프로젝트에서 처음부터 마감까지

**상황.** 설정값을 10에서 20으로 바꾸고 표시값이 일치하는지 검사한다. 이 실습은 실서비스, 원격 저장소, 모델 로그인을 사용하지 않는다. Bash에서 다음 블록을 같은 터미널에 순서대로 입력한다. `aw`는 실습 전용 원장을 사용하는 함수다.

```bash
lab=$(mktemp -d)
mkdir "$lab/repo"
cd "$lab/repo"
git init -q -b main
mkdir core web
printf '%s\n' '{"limit":10}' > core/limits.json
printf '%s\n' '10' > web/limit.txt
git add core web
git -c user.name=Demo -c user.email=demo@example.invalid commit -qm 'initial limit'
aw() { agent-workflow --home "$lab/ledger" "$@"; }
```

프로필은 프로젝트의 Git 루트에서 등록한다. 아래 예제의 공통 검사는 JSON 구문을 검사한다. `limits`는 값의 범위를, `view`는 화면 파일과 설정의 일치를 검사한다. 실습에서는 직접 파일을 수정하므로 현재 작업 트리를 쓴다.

```bash
cat > "$lab/profile.json" <<'JSON'
{
  "project": ".",
  "owner": "codex",
  "reviewer": "none",
  "workspace": {"isolate": false},
  "checks": [["python3", "-m", "json.tool", "core/limits.json"]],
  "base_check_description": "설정 파일 JSON 구문",
  "check_group_descriptions": {"limits": "설정값의 허용 범위", "view": "표시값과 설정의 일치"},
  "check_groups": {
    "limits": [["python3", "-c", "import json; assert 1 <= json.load(open('core/limits.json'))['limit'] <= 100"]],
    "view": [["python3", "-c", "import json; from pathlib import Path; assert int(Path('web/limit.txt').read_text()) == json.load(open('core/limits.json'))['limit']"]]
  }
}
JSON
aw profile save limit-demo --file "$lab/profile.json"
aw profile checks limit-demo --check-group limits --check-group view
aw create LIMIT-20 --profile limit-demo --check-group limits --check-group view \
  --goal '설정값을 20으로 변경' --criterion '허용 범위 내이고 표시값이 일치한다'
printf '%s\n' '{"limit":20}' > core/limits.json
printf '%s\n' '20' > web/limit.txt
```

`profile checks`에는 공통 검사와 선택한 두 묶음의 명령 세 개가 순서대로 나온다. 작업에는 그 시점의 명령 목록이 저장된다. 아래 명령으로 검사 결과를 확인한 뒤 마감한다.

```bash
aw checkpoint LIMIT-20 --summary '설정과 화면 표시를 20으로 변경' --next '등록된 검사 세 개 확인'
aw check LIMIT-20
aw show LIMIT-20
aw finish LIMIT-20
aw handoff LIMIT-20
aw release-status LIMIT-20
```

**기대 결과.** `verification.passed`와 `verification_is_current`가 모두 참이고 `finish` 후 작업이 `completed`가 된다. 릴리스 조회 결과는 `local_completion=succeeded`, `merge=not_configured`다. 실패를 연습하려면 마감 **전**에 화면 파일을 19로 두고 `check`를 실행한다. `show`에서 실패한 명령을 확인하고 값을 바로잡은 뒤 다시 검사한다. 이 실습에서 선택한 `reviewer=none`은 독립 코드 리뷰를 수행하지 않는다.

## 2. 변경 내용에 맞는 검사 추천 받기

**상황.** 개발자가 프로젝트의 검사 묶음을 등록했지만, 이번 변경에 필요한 묶음을 모른다. 사례 1의 저장소와 미커밋 변경을 사용한다. 실제 업무에서는 현재 작업 중인 프로젝트의 프로필을 선택한다.

```bash
aw recommend-checks limit-demo --criterion '20이 허용 범위에 있고 표시값과 같다' \
  --provider codex --reviewer none --preview
```

`providers`와 `input`을 읽는다. `input.change`는 기준 커밋 이후의 스테이징된 변경, 스테이징 후 작업 트리 변경, 새 파일의 상대 경로를 담는다. 새 파일의 본문은 보내지 않는다. 추적된 패치도 크기 제한이 있다. `truncated`가 참이면 생략된 변경을 사람이 별도로 확인한다. 금지 경로, 일반적인 자격 증명 파일과 일부 비밀 문자열은 제외되지만 전체 비밀 탐지를 보장하지 않는다. **완료 조건과 묶음 설명도 공급자에게 전달되는 입력**이므로 미리보기에서 함께 확인한다.

```bash
aw recommend-checks limit-demo --criterion '20이 허용 범위에 있고 표시값과 같다' \
  --provider codex --reviewer none > "$lab/single.json"
```

정상 응답에서 `primary.reason`, `suggested_groups`, `checks`를 읽는다. `checks`에는 기본 검사가 포함된다. 실제 명령은 작업 등록 뒤 `check`가 실행한다. 추천을 원장에 연결하려면 사례 3의 `--record`와 사례 4의 `--recommendation`을 사용한다. 단발 JSON 출력만 필요한 경우에는 이 예제처럼 `--record`를 생략한다. 기존 작업의 검사 계약을 바꾸려면 해당 작업에서 `check-add`를 사용하고 새로운 검증 증거를 확보한다.

**변형.** 브랜치에서 이미 커밋한 수정까지 포함하려면 `--base main` 또는 비교할 로컬 커밋 SHA를 쓴다. 연결된 별도 작업 트리는 `--project /path/to/worktree`로 지정한다. 다른 저장소를 가리키면 명령이 실패한다. 선택 가능한 묶음이 없는 프로필은 공통 검사만 반환하며 모델을 호출하지 않는다.

## 3. 여러 구성요소의 변경을 두 모델이 독립적으로 검토하기

**상황.** 사례 1처럼 `core/`와 `web/`를 함께 수정했다. 첫 모델이 값의 범위 검사만 선택하면 화면 회귀가 빠질 수 있다.

```bash
aw recommend-checks limit-demo --criterion '범위와 화면 표시가 일치한다' --preview
aw recommend-checks limit-demo --criterion '범위와 화면 표시가 일치한다' \
  --provider codex --reviewer claude --record > "$lab/dual.json"
```

두 공급자는 같은 입력을 받으며 첫 응답을 두 번째 모델에 전달하지 않는다. 기본 `--reviewer auto`는 같은 원장·프로필 설정·영역·공급자 순서의 기존 평가를 먼저 살핀다. 이 실습을 처음 실행하면 표본이 부족해서 두 최상위 영역을 보고 검토자를 고른다. 루트의 파일들은 한 영역으로 센다. 이번 입력이 잘린 경우 항상 독립 검토를 요청한다. `--reviewer none`은 한 모델만 쓰며, `--reviewer claude`처럼 지정하면 검토를 명시적으로 요청한다. 호출당 제한은 `--timeout`으로 조절한다.

| 결과 필드 | 판단 예시 |
|---|---|
| `routing_decision` | 사용할 공급자와 선택 근거가 나온다. `--preview`로 실제 호출 전에 볼 수 있다. |
| `recommendation_id` | `--record`를 붙여 완료한 추천의 원장 내 ID다. 다음 사례에 재사용한다. |
| `comparison.agreed` | 두 모델이 함께 선택했다. 공통 검사와 중복되는지 확인한다. |
| `primary_only`, `reviewer_only` | 한쪽만 선택했다. 화면 변경이 있는지 diff와 완료 조건을 대조한다. |
| `suggested_groups` | 두 선택의 합집합이다. 불필요하면 작업 등록 시 해당 묶음을 생략한다. |
| `status=review_incomplete` | 첫 선택만 남고 두 번째 호출이 실패했다. 종료 코드 3과 `review.error`를 확인한다. 두 모델의 합의로 취급하지 않는다. |

두 번째 모델은 **검사 선택**을 검토한다. 관리형 작업의 독립 **코드 리뷰**는 사례 7의 절차에서 실제 변경 및 검사 결과를 본다. 추천 결과만으로 작업 완료나 릴리스 상태가 바뀌지 않는다.

## 4. 추천 품질을 실제 작업에서 측정하기

**상황.** 이중 분석의 추가 호출이 값을 하는지 알고 싶다. 작업 담당자가 실제로 필요하다고 판정한 묶음을 기준으로 쓴다. 품질을 비교할 때는 가능하면 모델 결과를 보기 전에 그 기준을 정한다.

```bash
aw evaluate-checks "$lab/dual.json" --expected-group limits --expected-group view
```

예를 들어 첫 모델이 `limits`만 골랐고 두 번째 모델이 `view`를 추가했다면 `primary.missed=["view"]`, `combined.missed=[]`가 된다. 반대로 화면 변경이 없는 작업에서 `view`를 추가했다면 `combined.extra`에 남는다. 공통 검사로 충분한 작업은 `--expected-none`으로 평가한다. 이 명령은 저장한 완전한 추천 JSON에서 계산하므로 모델을 다시 호출하지 않는다.

2026년 11월 19일 위 실습의 숫자 10→20 변경을 실제 두 CLI에 전달한 결과, 두 모델 모두 `limits`와 `view`를 선택했다. 두 묶음을 정답으로 평가하자 1차·합산 모두 누락 0개, 과잉 0개였다. 이 사례에서는 두 번째 호출로 추가된 검사가 없었다. [실제 추천 JSON](examples/limit-demo-recommendation.json)을 동봉했다. 소스 저장소 루트에서 아래 명령으로 같은 결과를 오프라인에서 재현할 수 있다.

```bash
agent-workflow evaluate-checks docs/examples/limit-demo-recommendation.json --expected-group limits --expected-group view
```

### 추천을 다음 작업에 연결해 실측하기

사례 3에서 저장한 추천 ID를 읽는다. `--home`은 사례 1의 `aw` 함수가 가리키는 원장으로 유지하고, 동일한 저장소·프로필·완료 조건을 사용한다. 기존 추천 JSON만으로는 새 원장의 추천 ID를 만들 수 없다.

```bash
rec_id=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["recommendation_id"])' "$lab/dual.json")
aw recommendation show "$rec_id"
aw create LIMIT-REVIEW --profile limit-demo --recommendation "$rec_id" --no-isolate \
  --goal '변경된 설정과 화면을 함께 검사' --criterion '범위와 화면 표시가 일치한다'
aw check LIMIT-REVIEW
aw recommendation feedback LIMIT-REVIEW --expected-group limits --expected-group view
aw recommendation report --profile limit-demo
```

`--recommendation`만 전달하면 두 모델이 제안한 합집합이 작업에 저장된다. 제안 중 일부만 필요하다면 `create`에 `--check-group view`처럼 원하는 이름을 지정한다. 공통 검사만 채택할 때는 `--base-checks-only`를 붙인다. 추천 기록과 별도로 추가한 개별 `--check`는 작업의 검사 목록에 포함된다. 미커밋 변경이 있는 원본 작업 트리에서는 `--no-isolate`로 같은 파일을 검증한다. 작업을 등록하기 전에 완료 조건, 보이는 diff, 프로필이 바뀌어 지문이 어긋났으면 새 추천을 받는다.

프로젝트를 `connect`로 연 경우에는 같은 등록 원장에서 추천을 기록한 뒤 `start`에 연결할 수 있다. 예를 들어 아래는 LIMIT-REVIEW와 같은 추천으로 자동 ID 작업을 **별도로 하나 더** 등록하는 예다. 실제 실험에서는 필요한 작업만 등록한다.

```bash
aw connect . --ledger "$lab/ledger" --registration-home "$lab/ledger" --profile limit-demo \
  --criterion '범위와 화면 표시가 일치한다'
aw start '표시값 확인' --project . --recommendation "$rec_id" --no-isolate --check-group view
```

**관리 화면 사용.** 새 작업 등록에서 해당 프로젝트 설정을 고르고 **저장한 추천 ID → 추천 묶음 불러오기**를 누른다. 제안된 묶음을 확인하고 체크 상태를 바꿀 수 있다. 추천 때의 완료 조건을 같은 순서로 입력한다. 미커밋 변경을 검사한다면 별도 작업 폴더 옵션을 해제한다. 등록·검사 후 작업 상세의 **검사 추천과 실제 작업 결과**에서 모델별 추천, 채택 여부, 묶음별 실제 검사 건수와 초를 읽는다. **실제로 필요했던 검사 묶음**을 체크하고 **필요 묶음 저장**을 누른다. 아무 항목도 체크하지 않고 저장하면 기본 검사로 충분했다는 판정이다. 읽기 전용 화면에서는 기존 판정만 볼 수 있다.

**보고서 읽기.** `tasks[].expected_groups=null`은 아직 평가하지 않은 작업이고 빈 배열은 사람이 공통 검사만 필요하다고 판정한 결과다. 검사 성공·실패가 필요성의 정답을 만들지는 않는다. `quality.rescued_groups`는 2차 모델이 추가한 필수 묶음, `quality.extra_groups`는 불필요한 추가 묶음이다. `quality.unnecessary_review_check_seconds`에는 실제로 채택·실행한 과잉 묶음 중 다른 검사와 겹치지 않은 명령의 관측 시간만 들어간다. `null`은 필요한 시간 기록이 없다는 뜻이다. `median_review_ms`로 두 번째 모델을 기다린 시간도 함께 비교한다. 해당 프로필에 필요한 묶음 자체가 없으면 프로필 보완 항목으로 남긴다.

### 실측이 다음 자동 호출에 반영되는 조건

비슷한 실제 업무에서 두 공급자를 명시하고 `--record`로 기록·채택·평가한다. 다음 자동 추천의 미리보기에서 `routing_decision.evidence.labeled_inputs`를 확인한다. 원장·프로필 이름과 지문·최상위 영역 집합·공급자 순서가 같은 경우에만 비교한다. 입력 지문이 서로 다른 사람 평가 표본 중 최근 최대 10개를 사용하고, 같은 입력의 재시도나 추천 재사용으로 건수가 부풀지 않는다. 정답이 충돌하거나 입력이 잘린 비교는 제외한다.

- 5건 미만이면 두 영역 이상에서 두 모델을 사용한다. 이번 입력이 잘리면 표본 수와 관계없이 두 모델을 사용한다.
- 예를 들어 다중 영역 5건 모두에서 첫 모델이 필요한 검사를 전부 선택했다면 다음 유사 변경은 `measured_no_gain`으로 한 모델을 쓴다.
- 반대로 단일 영역 5건 중 3건에서 2차 검토가 누락을 구제했고, 추가 필수 묶음 수가 과잉 묶음 수보다 많으면 `measured_rescue`로 독립 검토를 추가한다. 다른 경우에는 변경 영역 기준을 유지한다.

자동으로 한 모델만 부르는 작업은 이중 검토의 효과를 관측할 수 없다. 필요한 업무 유형에서 검토자를 가끔 직접 지정해 비교 표본을 갱신한다. 초기 표본은 개인의 기준을 조정하는 용도로 쓰고 실제 결함 발견·전체 생산성 평가는 별도로 확인한다.

## 5. Claude에서 마감하고 다음 Codex 세션에서 재개하기

**상황.** 짧게 고친 작업에 관리형 작업 ID가 없다. 프로젝트 디렉터리에서 다음과 같이 실행한다.

```bash
agent-workflow close-session --summary '표시값을 설정에서 읽도록 변경하고 관련 테스트를 확인했다' \
  --next '작은 화면에서도 같은 값이 보이는지 확인한다'
agent-workflow pickup
```

다음 세션을 같은 Git 작업 트리와 브랜치에서 시작하면 `next_action`과 기록 당시·현재 HEAD, 변경 경로 상태를 함께 볼 수 있다. 최초 사용이면 `found=false`여도 기존 `agent-context` 문서와 등록 작업이 있을 수 있다. 일지 사본도 필요하면 마감할 때 `--worklog-dir`에 기존 디렉터리를 준다. 사본 생성 실패 시 출력된 로컬 일지를 보존하고 대상 폴더 문제를 해결한다.

등록 작업이 있는 경우 `registered_tasks[].evidence`를 읽는다. `current`는 현재 코드에 유효한 통과 기록, `failed`는 현재 입력에서 실패, `stale`은 입력 변경, `missing`은 증거 없음, `unavailable`은 비교 불가다. `rerun_checks=null`이면 이유를 먼저 읽는다. 가벼운 메모의 변경 경로 비교는 파일 내용을 보증하지 않는다. **등록된 작업의 검사 증거**는 내용 지문과 참조·보호 경로까지 확인한다. 완료된 작업 이후 코드가 바뀌었으면 새 작업으로 검증한다.

### 재개 시간을 별도 기록표 없이 비교하기

같은 작업 트리·브랜치의 비슷한 재개 6회를 대상으로 평소 방식과 `pickup` 방식을 3회씩 번갈아 사용한다. 비교 방식 한 가지를 선택해 시작하고 **올바른 첫 수정·검사에 착수할 때** 종료한다. 아래 블록은 두 번의 서로 다른 세션을 보여준다. 명령 사이에서 실제 맥락 확인과 작업 선택을 한다.

```bash
# 첫 번째 세션: 평소 방식. 원장 메모를 보여주지 않고 시계만 시작한다.
agent-workflow resume-time start
# 처음 올바른 작업을 시작할 때. 맥락 재설명이 1번 있었다고 가정한다.
agent-workflow resume-time finish --reexplanations 1
```

```bash
# 두 번째 세션: 인계 내용과 측정 ID를 함께 받는다.
agent-workflow pickup --measure
# 표시된 인계를 읽고 첫 수정·검사를 시작할 때 호출한다.
agent-workflow resume-time finish
agent-workflow resume-time report
```

처음 선택한 작업이 틀렸다면 `finish`에 `--wrong-pick`을 붙인다. 같은 작업 트리에서 브랜치를 바로잡았다면 처음 반환된 측정 ID를 명시해 `resume-time finish 측정ID --wrong-pick`으로 종료한다. 이 표식은 완료된 표본을 유지하면서 재선택 횟수를 더한다. 중간에 회의로 자리를 비웠거나 측정 방식을 바꾸면 `resume-time abandon 측정ID`로 해당 시행을 종료한다. 중단 표본은 중앙값에서 빠진다. 같은 브랜치에 진행 중인 시계는 명령을 다시 실행해도 원래 시작 시각을 유지한다. 같은 측정 동안 표시할 인계 근거가 바뀌면 기존 시계를 먼저 마무리한다.

마감을 위한 **문구 작성**이 늘었는지도 측정하려면, 세션을 닫기로 결정한 순간부터 다음처럼 사용한다.

```bash
agent-workflow resume-time start-close
# 여기서 요약·다음 행동을 작성한 뒤 마감한다.
agent-workflow close-session --summary '표시값 일치 검사 통과' --next '좁은 화면에서 읽기 확인'
agent-workflow resume-time report
```

보고서의 `manual`과 `pickup`에서 `finished`, `median_elapsed_ms`, `reexplanations`, `wrong_picks`를 비교한다. 예컨대 평소 100초, pickup 50초라면 `median_saved_ms=50000`이다. `close.median_elapsed_ms`는 초안 타이머를 켠 경우에만 나오며 `close.median_tool_ms`는 새 메모의 Git 조회와 Markdown 쓰기에 걸린 시간이다. 둘은 밀리초 단위다. 평소 방식에서도 마감 메모를 쓰던 사람이라면 실제로 더 들어간 준비 비용만 별도로 고려한다. 반복해 기억한 같은 작업·다른 규모의 작업·장시간 방치한 시계는 비교를 왜곡할 수 있다.

## 6. Katakuri 백테스트나 agent-workflow 화면을 바꿨을 때

`agent-workflow` 소스 루트에서 다음 프로필 중 하나를 등록한다. `profile save`는 같은 이름의 저장 프로필을 갱신하므로 개인 프로필을 유지하려면 새 이름을 사용한다.

```bash
agent-workflow profile save kb-regression --file profiles/katakuri-backtest-tests.json
agent-workflow profile checks kb-regression --check-group viewer
agent-workflow profile save aw-dev --file profiles/agent-workflow-development.json
agent-workflow profile checks aw-dev --check-group dashboard
```

**Katakuri.** 기본 명령은 합성 입력으로 백테스트 드라이버를 검사한다. `viewer`는 결과의 HTML·표시 데이터 투영을 검사한다. 외부 시세 파일이 필요한 두 시험은 해당 명령의 대상에서 빠진다. 이 프로필의 작업 범위는 회귀 테스트 두 파일이다. 다른 PC에서는 등록 전에 JSON의 프로젝트 경로를 맞춘다.

**관제실.** 대시보드에서 새 작업을 등록할 때 `aw-dev`와 `dashboard`를 선택하면 공통 검사에 HTTP 및 브라우저 검사가 추가된다. 로컬 포트 사용과 Playwright Chromium이 필요하다. 에이전트의 세션 마감만 수정했다면 `handoff` 묶음을 선택할 수 있다. 독립 작업 트리는 커밋된 HEAD에서 시작한다. 작업 중인 미커밋 코드를 검증하려면 `create --no-isolate`로 현재 트리를 명시한다.

GitHub의 `Workflow verification`을 확인할 때는 Chromium 설치 단계와 대시보드 수용 검사 단계를 각각 확인한다. 브라우저 작업은 Playwright 1.51.1이 지원하는 Ubuntu 24.04 러너를 사용한다. 실행 옵션을 고치고 있다면 로컬 새로고침이나 주기적인 상태 조회 뒤에도 잘못된 값의 오류가 표시되는지 확인한다.

## 7. 구현과 독립 코드 리뷰를 실제로 맡기기

**준비.** Git 프로젝트, 로그인한 모델 CLI, 완료 조건, 실제 실행 가능한 검사 명령이 필요하다. 예시는 `aw-dev`를 등록한 저장소에서 실행한다.

```bash
agent-workflow create VIEW-21 --profile aw-dev --check-group dashboard \
  --goal '작업 상세 화면의 상태 표시를 수정' --criterion '실패한 상태 검사가 화면에 표시된다' \
  --owner claude --reviewer codex
agent-workflow run VIEW-21 --dry-run
```

계획에서 실제 프로젝트 경로, 역할, 검사 목록, 시간 한도와 릴리스 설정을 읽는다. 진행할 때 `agent-workflow run VIEW-21`을 실행한다. 구현 → 등록된 검사 실행 → 독립 리뷰 → 필요한 수정 → 로컬 마감의 결과는 `show VIEW-21`에 남는다. 검사가 실패하면 stdout과 실패한 명령을, 리뷰에 차단 지적이 있으면 해당 근거를 확인한다. 계정 한도에 걸렸을 때는 로그인·사용 가능량을 해결한 뒤 같은 ID로 재개한다.

직접 구현하는 경우에는 `checkpoint`, `check`, `brief --role review`, `review-import`, `finish`를 순서대로 쓴다. 같은 공급자의 새 독립 세션을 원할 때의 `codex-session`은 실제 네이티브 실행 영수증이 필요하다. 직접 작성한 리뷰 JSON을 이 영수증으로 대체할 수 없다. [검토 입력과 명령 형식](../skills/agent-workflow/references/commands.md)

## 8. 작업 A가 끝난 뒤에만 B를 실행하고 중단에서 복구하기

**준비.** 실제 목표와 검사를 가진 A·B가 같은 원장에 등록돼 있어야 한다. 아래는 작업 ID가 `API-1`, `WEB-2`인 경우다.

```bash
agent-workflow depends WEB-2 --on API-1 --require merged
agent-workflow batch create feature-set --task API-1 --task WEB-2 --max-jobs 2 --max-seconds 3600
agent-workflow batch show feature-set
```

A의 실제 병합이 B의 입력이라면 `merged`와 해당 릴리스 설정이 필요하다. 로컬 완료만 필요하면 `completed`를 선택한다. 워크트리 간 변경 전달과 명령 실행 순서는 별도다. 배치를 돌릴 원장의 데몬을 시작한 뒤 `batch start feature-set`으로 예약하고 `jobs`와 `notifications`에서 결과를 확인한다.

`batch pause feature-set`은 이후 예약을 멈춘다. 이미 실행 중인 개별 작업에는 `stop JOB_ID`를 사용한다. 중단 이유와 로그를 확인한 후 `resume JOB_ID`가 동일 설정으로 다시 큐에 넣는지 `jobs`로 확인한다. 작업 단위 보류는 `hold TASK_ID --reason '선행 결과 확인 중'`과 `unhold TASK_ID`로 관리한다.

## 9. “배포됐어?”라는 질문에 답하기

먼저 `agent-workflow release-status TASK_ID`를 실행한다. 조회는 작업 폴더가 없어도 남은 원장 기록을 읽을 수 있다.

| 관찰한 상태 | 답변과 다음 행동 |
|---|---|
| `local_completion=succeeded`, `merge=not_configured` | 로컬 작업이 완료됐다. 해당 작업에 릴리스 대상이 설정되지 않았다. |
| `merge=succeeded`, `integration_check=failed` | 병합 커밋은 존재하고 해당 커밋 검사에서 실패했다. 통합 검사 결과와 수정 범위를 확인한다. |
| `deployment=applied`, `health=failed` | 적용 명령은 성공했고 이어진 점검은 실패했다. 서비스 상태를 확인한다. 같은 릴리스를 정상 재개하면 점검부터 이어간다. |
| `deployment=uncertain` | 적용을 시작했으나 확정 결과가 없다. 실제 대상의 배포 이력을 대조하고 재적용 여부를 결정한다. |

릴리스를 새로 구성할 때는 원격 저장소, 기준·작업 브랜치, 허용 경로, 배포·점검 명령을 작업별로 지정한다. `pipeline ID --dry-run`에서 대상을 확인한 뒤 실행하며, 불확실한 적용을 다시 수행하기로 결정한 경우에만 `release ID --retry-deploy`를 쓴다. `release-status`의 기록 시각과 리비전은 현재 서비스의 실시간 응답을 뜻하지 않는다. [릴리스 설정 예제](../skills/agent-workflow/references/commands.md)

## 10. 여러 프로젝트와 일반 세션 관측하기

**원장이 여러 개인 경우.** 관심 프로젝트에서 `connect`로 작업 원장을 연결한다. 발견 후보가 여럿이면 `--ledger`와 `--registration-home`을 지정한다. 이후 `start '작업 목표' --criterion '확인 기준'`이 반환하는 `state_home`과 작업 ID를 기록한다. 다른 원장의 상세 조회는 `agent-workflow --home 경로 show ID`를 사용한다.

**관측만 하는 경우.** `watch --port 8877`로 띄운 관제실의 프로젝트 연결 화면에서 원장이나 OpenCode 서버를 연결한다. /mission의 작업자·활동·근거에서 마지막 수신 시각과 출처를 확인한다. WP 없는 Claude·Codex 세션을 보려면 해당 프로젝트의 네이티브 관측 설정과 신뢰 절차가 필요하다. 화면이 비어 있으면 worker를 추가로 시작하기 전에 대상 프로젝트 경로, 공급자 등록, 마지막 이벤트를 확인한다. [세션 연결 및 진단](CONNECTION_SETUP.ko.md)

## 11. 다음에 자동화할 일을 입력 기록에서 찾기

```bash
agent-workflow patterns --since 2026-10-01 --until 2026-10-31
```

공급자별 입력·세션 수, 주제별 세션 수, 명령이 함께 나타난 세션 수를 비교한다. 예를 들어 인계와 일지가 같은 세션에 자주 등장하면 사례 5를, 화면 검증이 반복되면 사례 6의 묶음을 검토한다. UTC 날짜 구간의 양끝을 포함하며, 키워드 한 입력이 여러 주제에 들어갈 수 있다. 호출 횟수만으로 두 명령의 순서나 실제 작업 성공을 판단하지 않는다. 추가 입력 이력은 `--source FILE`을 반복해 지정할 수 있다.
