# 에이전트 통신 언어

agent-workflow가 생성하는 에이전트 지시에는 **ASD-STE100 Simplified Technical
English, Issue 9**를 기본 언어 정책으로 적용한다. 별도 작업 옵션은 필요 없다.
이 정책은 프롬프트의 지시이며, 생성 결과에 대한 표준 준수 인증이나 자동 언어 검사는 아니다.

## 적용 경로

| 경로 | 동작 |
|---|---|
| `brief ID` | 대화형 작업 재개 지시 앞에 공통 언어 정책을 추가한다. 모델 호출은 없다. |
| `brief ID --role review` | 독립 검토 지시와 응답 설명 필드에 같은 정책을 적용한다. |
| `run` / `pipeline` / 큐 실행 | 구현·검토·수정 재시도에 사용하는 새 `prompt.txt`에 정책을 포함한다. |
| 구현 보고 → 검토, 검토 의견 → 수정 | 원본 보고를 보존하여 전달하고, 새 요청·응답에 정책을 지시한다. |
| `handoff ID` | 새 `handoff/resume.txt`를 같은 정책의 영어로 생성한다. |
| 배포된 `agent-workflow` 스킬 | 스킬을 읽은 에이전트에게 요청·응답·인계에 같은 규칙을 지시한다. |

핵심 규칙은 짧은 문장, 능동태, 명령형 지시, 절차 문장당 최대 20단어,
문장당 한 가지 지시, 필요한 조건을 행동보다 먼저 명시하는 것이다.
같은 대상에는 같은 용어를 사용하고, 행동·필요한 결과·실제 증거를 명확히 쓴다.
표준의 승인 어휘·의미를 사용하도록 지시하되 소프트웨어 분야의 문서화된 기술 용어를 허용한다.

응답 JSON의 `summary`, `next_action`, `findings[].detail`,
`findings[].evidence`의 설명 부분에 정책을 적용한다.
JSON 키와 `ready`, `pass`, `changes_requested`, `blocked` 같은 상태 값은 바꾸지 않는다.
코드, 명령어, 경로, task ID, 사용자 목표·수용 기준·범위, 인용한 로그·과거 보고도 원문을 보존한다.
한국어 요구사항을 영어로 번역해 DB에 덮어쓰지 않는다.
사용자가 요청한 화면 문구와 사용자용 문서는 사용자의 언어로 작성한다.

### 소프트웨어 용어

이 프로젝트에서는 `task`(원장 작업), `brief`(생성 지시), `checkpoint`(진행 기록),
`handoff`(인계), `controller`(실행 제어기), `queue worker`(큐 실행 프로세스),
`baseline`(기준 상태), `JSON schema`(응답 구조)를 기술 용어로 사용한다.
CLI의 `check`, `review`, `run` 등은 정확한 명령어로 보존한다.

## 사용 방법

변경된 버전의 CLI에서 기존 작업의 지시를 다시 생성한다.
다음 예시의 `TASK-ID`와 원장 경로는 실제 값으로 바꾼다.

```bash
agent-workflow --home /path/to/state-home brief TASK-ID
agent-workflow --home /path/to/state-home brief TASK-ID --role review
agent-workflow --home /path/to/state-home handoff TASK-ID
```

`brief`는 모델을 실행하지 않는다. `handoff`는 해당 원장에 새 인계 파일을 저장한다.
에이전트에게 생성된 지시를 전달하면 이후 요청·응답에 정책을 적용하도록 지시한다.
`resume JOB-ID`는 큐 작업을 재등록하는 명령이므로 지시 확인용으로 사용하지 않는다.
자동 실행에서는 다음 새 호출부터 적용한다. 이미 실행 중인 프로세스의 입력,
기존 실행 로그와 인계 파일은 소급 변경하지 않는다.
대화형 작업에서 직접 입력하는 메시지도 이 언어를 사용하려면 생성한 brief 또는 스킬을 전달한다.

예시 지시:

```text
Read the task requirements.
Do a check of the current source files.
Do not restart the queue worker.
Run the specified tests.
Report the results. Include the evidence.
```

## 관측과 실행의 차이

OpenCode·Claude Code·Codex의 **일상 세션 관측 플러그인**과 OpenCode 서버 연결은
외부 세션에서 발생한 이벤트를 읽는다. 이 연결만으로 외부 에이전트의 시스템 프롬프트나
대화 언어를 바꾸지 않는다. 수신된 이벤트·과거 메시지도 번역하거나 덮어쓰지 않는다.
외부 세션에도 적용하려면 그 세션에서 사용하는 프로젝트 지침이나 프롬프트에 이 정책을 전달해야 한다.

이 변경은 관제실 UI, 큐 실행 조건, 작업 권한, 응답 JSON 검증 구조를 바꾸지 않는다.
영어가 아닌 모델 응답을 언어 검사만으로 실패 처리하거나 추가 모델 호출로 재작성하지 않는다.
전체 어휘·문법·의미의 적합성은 별도의 검토가 필요하며, 모델 출력 준수를 보증하지 않는다.

공통 프롬프트 정책은 `agent_workflow/communication.py`에 있다.
구현·검토는 `runner.brief()`, 재개 지시는 `cli.continuation()`이 같은 정책을 사용한다.

## 근거

- [ASD 공식 표준 안내](https://www.asd-ste100.org/): Issue 9, 2025-01-15.
- [ASD 공식 FAQ](https://www.asd-ste100.org/STE_faq.html): 통제 어휘, 기술 용어, 능동태와 적용 범위.
- [공식 표준, 절차 작성 규칙 5.1~5.4](https://www.asd-ste100.org/assets/files/ASD-STE100_ISSUE9.pdf): 문장 길이·지시 수·명령형·조건.

공식 표준과 사전 전체를 저장소에 복제하지 않는다.
