# 관제실 운영 반영과 복구

[문서 찾기](README.md) · [포트와 원장](#servers) · [운영 반영](#rollout) · [실패와 복구](#rollback) · [OpenCode 접속 변경 검증](#opencode-live-rotation)

이 절차는 관제실 프로그램을 업데이트할 때 사용한다. 특정 개발 작업의 배포 여부는 [작업별 릴리스 상태](USER_GUIDE.ko.md#release)에서 확인한다.

<a id="servers"></a>

## 대상과 경계

| 포트 | 역할 | 코드/상태 | 변경 원칙 |
|---|---|---|---|
| 8765 | Katakuri 운영 제어 | 별도 Katakuri 서비스 | 관제실 배포로 종료·재시작하지 않음 |
| 8776 | 기본 원장 관리 서버와 worker | `/home/roqkf/agent-workflow`, `~/.local/state/agent-workflow` | jobs·batches·직접 실행 확인 후에만 갱신 |
| 8777 | H 원장 읽기 전용 서버 | 위 운영 소스, `/tmp/katakuri-h-local-queue-workflow-20261029` | `serve --read-only` 유지 |
| 8877 | 과거 스냅샷 미리보기 | 별도 preview home | 운영 배포 대상으로 간주하지 않음 |
| 8880 | React 개발 미리보기 | 별도 feature worktree와 observer home | 운영과 구분; 운영 확인 URL로 대체하지 않음 |
| 8884 | 첫 연결 개선 미리보기 | `/tmp/agent-workflow-usability-20261104`, `/tmp/agent-workflow-usability-observer-20261104` | `watch`; 설정만 변경 가능, worker 없음. 임시 미리보기의 생존 여부는 접속 시 확인 |

`/`와 `/mission`은 같은 React 운영 관제실을 제공한다. 메타데이터 `server.json`에서 URL·PID·
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
- 일반 `serve`: 연결 설정과 기본 원장의 작업 관리를 제공한다. 다른 원장을 관측 중이면 **관리 원장에서 새 작업 등록**으로 기본 원장에 전환한다. worker는 기존 원장의 큐를 처리한다.

`connections.json`과 `opencode.json`은 해당 관제실의 home에 저장된다. 기존 연결을 유지하려면
업그레이드·이전 시 이 파일도 보존한다. 고정 주소 OpenCode의 인증 환경변수는 서버 프로세스에 전달한다.
로컬 서비스 추적 연결은 현재 주소와 인증을 OpenCode 서비스 파일에서 읽는다.
읽기 전용 연결을 위해 기존 관리 서버를 중단할 필요는 없다. 별도 observer home과 빈 포트의
`watch`에서 연결 UI를 사용할 수 있다. 배포 전후 상태와 실제 브라우저 결과를 별도로 기록한다.

<a id="rollout"></a>

## 반영 순서

1. 작업 브랜치의 소스·문서·빌드 자산을 검사하고 지정된 독립 리뷰 결과를 확인한다.
   검사와 리뷰는 동일한 코드/참조 지문에 연결되어야 한다. 리뷰 실패·quota·권한 오류를
   통과로 바꾸거나 native receipt를 수작업으로 만들지 않는다.
2. 실제 `origin`과 `main`을 fetch한다. 보호 규칙과 CI를 확인한 PR을 병합하고 병합 SHA를 고정한다.
3. 운영 checkout의 미커밋 변경을 확인한다. 사용자의 변경을 reset/clean으로 제거하지 않는다.
4. 대상 원장의 queued/running jobs와 활성 batch를 읽기 전용으로 확인한다. jobs가 비어 있어도
   `run/check/pipeline/release` 및 `katakuri-goal run` 직접 실행이 있을 수 있으므로 실제 프로세스까지 확인한다.
   진행 작업을 임의 중단하지 않고 완료 또는 안전한 반영 시점을 기다린다.
5. SQLite online backup으로 DB와 스키마를 보존하고 이전 소스 SHA, 서버 PID/identity/cwd/모드,
   jobs·batches 스냅샷을 남긴다. 활성 WAL이 있는 DB를 파일 하나만 복사하지 않는다.
6. 요청한 포트의 리스너·PID·시작 식별자·명령·원장이 일치하는 서버를 정상 종료한다. 8765와 다른 에이전트는 유지한다.
   운영 checkout을 검증한 `origin/main` SHA로 fast-forward한다.
7. 재시작 직전 큐·batch를 재확인한다. 새 대기 작업이 생겼으면 일반 `serve`를 무심코 시작하지 않는다.
   대상 포트의 기존 모드로 시작한다(8776 관리, 8777 읽기 전용). 변경된 state home을 쓰지 않는다.
8. `/`와 `/mission`이 동일한 React 셸을 제공하는지 확인한다. `/static/mission.js`, `mission.css`, 아이콘·라이선스의 HTTP 상태와 파일 해시, CSP, 기본 원장과 H 원장의 조회 범위를 대조한다. 8776에서 기본 원장의 **새 작업 등록**과 다른 원장의 **관리 원장에서 새 작업 등록**을 열고 기존 프로필이 표시되는지 확인한다. 프로덕션 확인은 폼을 닫거나 로컬 미리보기 단계에서 끝낼 수 있다. 실제 브라우저에서 작업·이전 run·그래프·3D·이력을 선택한다.
   연결 UI의 지원 범위, 빈 화면·오류 안내, 각 서버의 설정/관리 권한도 확인한다.
   신규 연결 저장 검사는 운영 서버 대신 별도 observer/fixture에서 먼저 수행한다.
9. 운영 HEAD = `origin/main` = 배포 목표 SHA인지 확인하고, 반영한 포트의 URL과 health를 기록한다.
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

<a id="rollback"></a>

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

<a id="opencode-live-rotation"></a>

## 실제 OpenCode 2.2.7 접속 정보 변경과 원복

이 절차는 **이미 연결한 로컬 OpenCode 2.2.7 관리 서비스**의 포트와 비밀번호를 잠시
바꿔 8776의 자동 복구를 확인하는 시험이다. OpenCode를 두 번 재시작하므로 진행 중인
작업과 승인·질문이 없는 시간에 실행한다. 8776의 관리 프로세스와 기존 원장은 시험
내내 유지한다. 명령은 저장소 루트에서 연 별도 Bash 셸 하나에서 순서대로 실행한다. `opencode --version`이
2.2.7이고 `python3`, `curl`, `jq`를 사용할 수 있어야 한다.

### 1. 기존 연결과 원래 설정 확인

8776의 `/mission`에서 **작업 원장 / 연결**의 OpenCode를 선택하고 기존 세션이 보이는 탭을 앞에 둔다.
화면을 새로고침하면 자동 탐색 시험은 다시 시작한다. `opencode.json`에서 이 프로젝트의
기존 연결에 `follow_local_service: true`가 있는지 확인한다. 이 값이 없으면 먼저
[재연결 설정 안내](OPENCODE_OBSERVATION.ko.md#follow-local-opencode)를 따른다.

아래 명령은 기본 8776 원장을 사용한다. 실행 중인 8776의 원장 경로가 다르면
`aw_state`에 그 경로를 지정한다. 비밀번호가 들어 있는 OpenCode 파일은 사용자만 열 수
있는 임시 디렉터리에 백업한다. 격리 관제실에서 연습할 때는 `AW_DASHBOARD_URL`을
그 관제실의 로컬 주소로 지정한다.

<!-- opencode-runbook:prepare -->
```bash
set -o pipefail
umask 077
aw_verify=$(mktemp -d "${TMPDIR:-/tmp}/aw-opencode-rotation.XXXXXX")
printf '검증 백업 디렉터리 %s\n' "$aw_verify"
aw_state="$HOME/.local/state/agent-workflow"
aw_dashboard="${AW_DASHBOARD_URL:-http://127.0.0.1:8776}"
aw_project=$(pwd -P)
oc_config="${XDG_CONFIG_HOME:-$HOME/.config}/opencode/service.json"
oc_runtime="${XDG_STATE_HOME:-$HOME/.local/state}/opencode/service.json"

opencode --version
jq -e '{url,pid,version} | select(.version == "2.2.7")' "$oc_runtime"
aw_source_id=$(jq -er --arg project "$aw_project" \
  '[.sources[] | select(.directory == $project and .follow_local_service == true)]
   | select(length == 1) | .[0].id' \
  "$aw_state/opencode.json")
printf 'OpenCode connection: %s\n' "$aw_source_id"
jq -er .pid "$aw_state/server.json" > "$aw_verify/dashboard-pid"
oc_original_url=$(jq -er '.url | select(test("^http://127[.]0[.]0[.]1:[0-9]+$"))' "$oc_runtime")
oc_original_port=${oc_original_url##*:}
oc_original_pid=$(jq -er .pid "$oc_runtime")
cp -p -- "$oc_config" "$aw_verify/original-service.json"
sha256sum "$aw_state/connections.json" "$aw_state/opencode.json" > "$aw_verify/registries.sha256"
{
  declare -p aw_verify aw_state aw_dashboard aw_source_id oc_config oc_runtime oc_original_url oc_original_port
  for aw_env in XDG_CONFIG_HOME XDG_STATE_HOME XDG_DATA_HOME XDG_CACHE_HOME TMPDIR; do
    declare -p "$aw_env" 2>/dev/null || printf 'unset %s\n' "$aw_env"
  done
} > "$aw_verify/paths.bash"
```

<!-- opencode-runbook:ledger-helpers -->
```bash
aw_ledgers() {
  curl --noproxy '*' -fsS "$aw_dashboard/api/connections" |
    jq -eS --arg id "$aw_source_id" --arg url "$1" '
      .ledgers
      | select(any(.[]; .kind != "opencode"))
      | select(all(.[]; .status == "available"))
      | select(any(.[]; .id == $id and .kind == "opencode" and .home == $url))
      | map({id,kind,status}) | sort_by(.id)'
}
oc_idle() {
  opencode api GET /api/session/active | jq -e '.data | type == "object" and length == 0' &&
    opencode api GET /api/permission/request | jq -e '.data | type == "array" and length == 0' &&
    opencode api GET /api/form | jq -e '.data | type == "array" and length == 0'
}
```

<!-- opencode-runbook:preflight -->
```bash
aw_ledgers "$oc_original_url" | tee "$aw_verify/ledgers-before.json"
oc_idle
```

기존 원장과 OpenCode 연결 상태가 모두 `available`이고 마지막 세 점검이 모두 `true`인지
확인한다. 하나라도 다르면 시험을 시작하기 전에 원인을 확인한다. 접속 정보 파일 전체나
인증 헤더를 화면·공유 로그에 출력하지 않는다.

### 2. 임시 포트와 비밀번호로 바꾸기

원래 설정의 다른 항목은 보존한다. 아래 함수는 같은 디렉터리에 모드 `0600`의 임시 파일을
쓴 뒤 한 번에 교체한다. 터미널에는 포트·PID·버전만 표시한다.

<!-- opencode-runbook:service-helpers -->
```bash
oc_write_config() {
  python3 - "$oc_config" "$aw_verify/original-service.json" "$@" <<'PY'
import json
import os
from pathlib import Path
import secrets
import socket
import sys
import tempfile

target, backup = map(Path, sys.argv[1:3])
payload = backup.read_bytes()
mode = sys.argv[3]
if mode == "rotate":
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    data = json.loads(payload)
    data.update(port=port, password=secrets.token_urlsafe(32))
    payload = json.dumps(data).encode()
    with backup.with_name("temporary-service.json").open("xb") as temporary:
        temporary.write(payload)
elif mode == "rebind":
    data = json.loads(payload)
    data["port"] = int(sys.argv[4])
    payload = json.dumps(data).encode()
elif mode != "restore":
    raise SystemExit("Unknown configuration action")

with tempfile.NamedTemporaryFile(dir=target.parent, prefix=".service-", delete=False) as file:
    file.write(payload)
    pending = Path(file.name)
try:
    os.replace(pending, target)
finally:
    pending.unlink(missing_ok=True)
PY
}

oc_check_auth() {
  python3 - "$oc_runtime" "$@" <<'PY'
import base64
import http.client
import json
from pathlib import Path
import sys
from urllib.parse import urlsplit

runtime_path, expected, credential_path = sys.argv[1:]
runtime, credential = (json.loads(Path(arg).read_bytes()) for arg in (runtime_path, credential_path))
url = urlsplit(runtime["url"])
if url.scheme != "http" or url.hostname != "127.0.0.1" or not url.port:
    raise SystemExit("Expected the loopback HTTP service")
token = base64.b64encode(("opencode:" + credential["password"]).encode()).decode()
connection = http.client.HTTPConnection("127.0.0.1", url.port, timeout=6)
try:
    connection.request("GET", "/api/info", headers={"Authorization": "Basic " + token})
    response = connection.getresponse()
    print("HTTP", response.status)
    if response.status != int(expected):
        raise SystemExit(f"Expected HTTP {expected}")
    if response.status == 200:
        info = json.load(response)
        if info["version"] != "2.2.7" or info["pid"] != runtime["pid"]:
            raise SystemExit("Unexpected OpenCode version or PID")
finally:
    connection.close()
PY
}
```

<!-- opencode-runbook:rotate -->
```bash
aw_ledgers "$oc_original_url" > /dev/null &&
  oc_idle && cmp -s "$oc_config" "$aw_verify/original-service.json" &&
  oc_check_auth 200 "$aw_verify/original-service.json" &&
  oc_write_config rotate && opencode service restart &&
  jq -e --arg url "$oc_original_url" --argjson pid "$oc_original_pid" \
    'select(.version == "2.2.7" and .url != $url and .pid != $pid) | {url,pid,version}' "$oc_runtime" &&
  oc_check_auth 200 "$aw_verify/temporary-service.json" &&
  oc_check_auth 401 "$aw_verify/original-service.json"
```

새 주소에는 임시 비밀번호로 HTTP 200, 이전 비밀번호로 HTTP 401이 나와야 한다.
파일을 바꾸기 전에 점검이 실패하면 중지한다. 파일을 바꾼 뒤에 재시작이나 인증 확인이
실패하면 바로 원복 단계로 이동한다.

### 3. 새로고침 없이 새 세션 확인

원래 선택한 OpenCode 연결에서 **이미 열어 둔 탭**을 그대로 사용한다. OpenCode에서 같은
프로젝트의 빈 세션을 만든다. 아래 명령은 테스트 세션만 만들며 모델에 메시지를 보내지 않는다.

```bash
oc_title="8776 OpenCode 재연결 검증 $(date +%Y%m%d-%H%M%S)"
oc_payload=$(jq -nc --arg title "$oc_title" --arg dir "$aw_project" \
  '{title:$title,location:{directory:$dir}}')
opencode api POST /api/session --data "$oc_payload" | jq -er '.data.id'
```

새 제목이 나타나면 항목을 열어 화면의 세션 ID와 명령의 ID를 대조한다. 연결에 표시되는
주소는 새 포트여야 한다. 세션을 연 화면 URL의 `ledger` 값은 `aw_source_id`와 일치해야 한다.
기존 원장 목록도 준비 단계와 같아야 한다.
연결 목록은 주기적으로 갱신되므로 화면의 주소와 세션 목록이 바뀌는 시점은 다를 수 있다.
탭을 앞에 두고 **최대 40초** 동안 기다려도 같은 연결에서 새 세션이 안 보이면 실패로 남기고
원복한다. 연결 해제·재등록이나 8776 재시작으로 결과를 대체하지 않는다.

```bash
aw_ledgers "$(jq -er .url "$oc_runtime")" > "$aw_verify/ledgers-rotated.json" &&
  cmp "$aw_verify/ledgers-before.json" "$aw_verify/ledgers-rotated.json" &&
  sha256sum -c "$aw_verify/registries.sha256" &&
  test "$(jq -er .pid "$aw_state/server.json")" = "$(cat "$aw_verify/dashboard-pid")"
```

### 4. 원래 설정으로 돌리고 연결 확인

파일을 바꾼 뒤에는 시험 결과와 관계없이 백업한 설정을 그대로 돌리고 OpenCode만 재시작한다.
셸이 닫혔다면 같은 사용자로 Bash를 연다. `AW_OPENCODE_RECOVERY_DIR`에 1단계에서
출력한 백업 디렉터리 경로를 지정하고 아래 블록을 실행한다. `paths.bash`에서 관제실 경로,
OpenCode의 XDG 경로와 원래 주소를 복원한다.

<!-- opencode-runbook:load-saved-paths -->
```bash
set -o pipefail
source "${AW_OPENCODE_RECOVERY_DIR:?검증 백업 디렉터리 경로를 지정하세요}/paths.bash"
```

새 셸에서는 1단계의 원장·활동 조회 함수 블록과 2단계의 설정·인증 함수 블록도 다시
실행한다. 그다음 원복 명령을 실행한다. 1단계 백업이나 2단계 변경 명령은 다시 실행하지 않는다.

<!-- opencode-runbook:restore -->
```bash
oc_write_config restore && opencode service restart
jq -e '{url,pid,version}' "$oc_runtime"
```

사용 중인 서비스가 원래 포트로 돌아오면 같은 Chromium 탭이 원래 주소와 같은 연결 ID를
표시하는지 확인한다. 새 세션을 만든 시험에서는 같은 세션이 계속 열리는지도 확인한다.
서비스 주소가 다를 때는
`oc_write_config rebind "$oc_original_port"`로 원래 포트를 한 번 명시한 후
`opencode service restart`로 적용한다. 적용 결과는 `oc_runtime`의 `url`로 확인한다.
이 경우 최종적으로 `oc_write_config restore`를 실행해 원래 설정 파일까지 복원한다.
마지막 파일 복원 후에는 현재 서비스가 원래 포트에서 계속 실행된다. 원본 설정에서 포트를
자동으로 정했다면 이후 서비스 재시작 때는 원본의 포트 선택 방식이 적용된다. 원래 포트가 다른 프로세스에
점유된 경우에는 해당 프로세스를 종료하지 말고 원복이 필요한 상태로 보고하고 백업을 보존한다.

<!-- opencode-runbook:verify-restore -->
```bash
test "$(jq -er .url "$oc_runtime")" = "$oc_original_url" &&
  cmp "$oc_config" "$aw_verify/original-service.json" &&
  oc_check_auth 200 "$aw_verify/original-service.json" &&
  oc_check_auth 401 "$aw_verify/temporary-service.json" &&
  aw_ledgers "$oc_original_url" > "$aw_verify/ledgers-restored.json" &&
  cmp "$aw_verify/ledgers-before.json" "$aw_verify/ledgers-restored.json" &&
  sha256sum -c "$aw_verify/registries.sha256" &&
  test "$(jq -er .pid "$aw_state/server.json")" = "$(cat "$aw_verify/dashboard-pid")" &&
  printf '원복 확인 완료\n'
```

원복이 확인되면 임시 디렉터리에 복사한 `original-service.json`과
`temporary-service.json`을 삭제한다. 공개 기록에는 날짜, 버전, 포트, 연결 ID·세션 ID,
화면 인식 시간과 원복 여부만 남긴다.

### 2026-11-27 실제 8776 검증 결과

| 항목 | 관측값 |
|---|---|
| 실행 버전 | `main` `4da6fff`, 실제 로컬 OpenCode `2.2.7` |
| 포트 | `49374 → 34277 → 49374` |
| 인증 | 변경 후 이전 비밀번호는 HTTP 401. 원복 후 원래 비밀번호로 조회 성공, 임시 비밀번호는 HTTP 401 |
| 관제실과 원장 | 8776 PID `109920` 유지. 기존 workflow 원장 2개 모두 `available`. `connections.json`, `opencode.json` 내용 일치 |
| OpenCode 연결 | `opencode-6d897ab191771b76257c` 유지 |
| 새 세션 | `ses_de5a60050ffel9lJVRAm6DeSj8`이 열린 탭에서 약 2.9초 만에 표시됨. 원복 뒤 동일 세션 조회 성공 |

포트와 인증값이 함께 바뀌는 격리 시나리오도 병합 커밋의 GitHub Actions에서 통과했다.
[자동 재연결 PR #24](https://github.com/89sooner/agent-workflow/pull/24) · [병합 커밋의 브라우저 검사](https://github.com/89sooner/agent-workflow/actions/runs/45913758253/job/140227957077)

GitHub Actions의 [셸 종료 후 원복 검사](../tests/opencode_runbook_recovery.py)는 이 문서의
Bash 코드 블록을 직접 읽는다. 격리 서비스의 포트와 비밀번호를 바꾼 뒤 첫 셸을 강제 종료하고,
새 셸에서 저장된 경로를 불러와 원래 설정과 원장 연결을 확인한다.

### 2026-11-27 실제 8776 셸 강제 종료 후 원복

실제 로컬 OpenCode 2.2.7에서 병합 커밋 `15b7635`의 운영 절차를 실행했다. 포트와
비밀번호를 바꾼 직후 첫 Bash를 `SIGKILL`로 종료했다. 새 Bash에서 `paths.bash`를
읽고 문서의 원복 명령을 실행했다.

| 항목 | 관측값 |
|---|---|
| 포트와 설정 | `49374 → 32981 → 49374`. 원복된 설정 파일이 원본과 바이트 단위로 일치 |
| 인증 | 원래 비밀번호는 HTTP 200, 임시 비밀번호는 HTTP 401 |
| 관제실과 원장 | 8776 PID `109920` 유지. 기존 원장 두 개 모두 `available`. `connections.json`과 `opencode.json` 내용 일치 |
| 연결과 화면 | 연결 ID `opencode-6d897ab191771b76257c` 유지. 열린 Chromium 탭이 새로고침 없이 원래 주소로 다시 연결됐고 기존 세션 `ses_de5a60050ffel9lJVRAm6DeSj8` 조회 성공 |

### 실환경에서 다시 검증할 조건

이 실측으로 동일한 수동 검증을 종료한다. 일상 회귀는 GitHub Actions의 문서 기반 원복
검사로 확인한다. 실환경 재검증은 다음 경우에 영향받는 동작만 확인한다.

- OpenCode 버전이나 관제실의 OpenCode 연결 코드가 바뀐다.
- OpenCode 서비스를 시작하거나 접속 정보를 전달하는 방식이 바뀐다.
- 이 문서의 변경·원복 절차가 바뀐다.
- 실제 운영에서 재연결 또는 원복 문제가 발생한다.

[병합 커밋의 자동 원복 검사 로그](https://github.com/89sooner/agent-workflow/actions/runs/45921952401/job/140254502884)

## 과거 반영 기록

아래는 2026년 11월 5~6일 통합 관제실 릴리스의 대상과 검증 결과다.

### 당시 운영 조건

- 원래 모드와 home을 유지한다. 8776은 관리 모드와 기존 기본 home, 8777은 H home의 엄격한 읽기 전용 모드다.
- 8776 `/overview`는 명시적으로 연결한 기본 원장과 H 원장을 함께 조회한다. 원장 DB를 합치지 않는다. 8777 `/mission`은 기존 H 직접 관측 URL을 유지한다.
- `/sessions`의 일상 관측은 해당 home에 프로젝트/훅/플러그인을 연결한 뒤 시작한다. 배포만으로 기존 대화나 모든 프로세스를 가져오지 않는다. 읽기 전용 8777에서 설정·북마크 저장은 차단한다.
- `jobs`가 queued/running이거나 활성 batch·직접 실행이 있으면 worker가 포함된 관리 서버를 재시작하지 않는다. 전환 직전과 시작 직전에 재확인한다.
- 이번 배포는 SQLite online backup, 기존 로컬 문서의 외부 백업과 이름 있는 stash, 병합 SHA로 fast-forward, 서버 모드 검증, HTTP 자산 해시·CSP·API 및 Chromium 검증을 포함한다.
- 작업 원장: `/tmp/aw-observatory-release-state-20261105`, 작업 `AW-OBS-PUBLISH-20261105`. 실제 배포/health·브라우저 증거: `/tmp/aw-observatory-deploy-20261105/`. 토큰·DB·전체 로그는 공개 Git에 포함하지 않는다.
- 기능 사용법: [통합 관제실 사용 안내](OBSERVATORY_REL01_USAGE.ko.md). 개발 검증과 잔여 조건: [검증 기록](OBSERVATORY_VALIDATION_2026-11-05.ko.md). 사용자 파일럿은 미완료, 비용 기능은 보류다.

### 2026-11-06 격리 복구 리허설

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

### 2026-11-06 준비 검증 변경의 운영 반영

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
