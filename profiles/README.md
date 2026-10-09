# 실제 프로젝트 설정

프로필은 프로젝트의 검사, 변경 범위, 역할, 작업 폴더, 실행 한도를 묶는다.
파일을 저장소에 추가하는 것만으로 실행되지는 않는다. 사용할 파일을 명시적으로 등록한다.

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
