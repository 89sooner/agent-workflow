# 두 프로젝트에서 같은 목표 실행기 검증

## agent-workflow 자체 변경

`AW-P2-RESUME-01`은 검사한 코드와 현재 코드가 다를 때 재개 안내에 두 지문의 앞 12자리를 표시하는 작업이다.
agent-workflow 저장소의 HEAD `9b0c443d`에서 원격이 없는 로컬 복제본을 만들고, 작업 중이던 Python 소스와
관련 시험·프로필을 그 복제본에 커밋했다. 목표 실행기가 시작한 기준 커밋은 `b2aab08f`다.
복제본과 실제 작업 트리에는 Katakuri의 세 문서(`AGENTS.md`, `REFACTORING_SUPREME_DIRECTIVE.md`,
`agent-context/session-handoff.md`)가 없었다. 등록한 지침은 프로젝트 자체의 `docs/DEVELOPMENT.ko.md`였다.

| 단계 | 실제 결과 |
|---|---|
| 변경 전 | 기존 재개 패킷 시험 10개 통과. 새 수용 시험은 두 지문을 찾지 못해 실패 |
| 구현 | Claude CLI가 관리 작업 트리의 `agent_workflow/resume_packet.py` 한 파일 수정 |
| 검사 | 컨트롤러가 등록 명령 2개 실행. 기존 시험 10개와 새 수용 시험 1개 통과 |
| 독립 리뷰 | 별도의 읽기 전용 Codex 세션이 같은 지문과 조건별 검사 ID를 확인하고 `pass` 반환 |
| 완료 | `completed`. 검사·리뷰·완료 코드 지문 일치, 모델 호출 2회. 검증된 한 파일 변경을 현재 작업 사본에 반영 |

측정 당시 원장과 작업 트리는 이 호스트의 `artifacts/p2-self-host/`에 보존했다.
저장소 루트에서 다음 명령으로 같은 ID의 정책·검사·리뷰 근거를 다시 읽을 수 있다.

```bash
python3 -B -m agent_workflow --home artifacts/p2-self-host/ledger goal status AW-P2-RESUME-01
```

[고정 프로필](../../profiles/agent-workflow-resume-goal.json) · [실제 실행 영수증](self-host.json) · [검증된 변경](self-host.patch) · [수용 시험](../../tests/test_resume_fingerprint_notice.py)

## 기존 Katakuri 원장 재개

P2 변경 전에 보존한 실행기로 `KB-P2-LEGACY`를 등록하고 HSI/M2의 조건 하나를 외부 근거 대기에 뒀다.
업그레이드 시점에 이미 구현 파일 쓰기 1회, 모델 호출 2회, 보고 토큰 20개와 통과한 검사 기록이 있었다.
새 공통 CLI와 기존 CLI로 같은 SQLite 행을 조회했다. 근거를 제출하고 누적 토큰 30에서 중단되는 것을 확인한 뒤
같은 ID의 한도를 40으로 올려 독립 리뷰까지 마쳤다. 최종 모델 호출은 누적 4회, 구현 파일 쓰기는 총 1회였다.
조건 2개가 모두 수락됐고 종목과 마일스톤은 HSI/M2였다. 반복 실행으로 완료된 작업을 다시 시작할 수 없었다.

이 시험에서 Git·SQLite·검사는 실제로 실행했다. Katakuri 구현자와 리뷰어는 고정 응답을 반환하는 별도 CLI 프로세스였다.
허용 범위 밖 파일과 보호 파일의 변경은 완료 전에 차단됐고 원본은 보존됐다. 릴리스 설정, 리뷰어 해제, 조기 완료,
미허용 근거 형식과 Katakuri 저장소의 일반 프로필 목표 등록도 거절됐다. 실제 실행 인자와 프롬프트에서
도구·네트워크 제한 및 브로커 접근, 모의·실거래 주문, 자격 증명, 운영 DB·서비스 변경, 배포, 자동 단계 승격 금지 지침을 확인했다.
파일 범위 검사와 릴리스 거부는 컨트롤러가 집행한다. 브로커 금지 문구만으로 임의의 로컬 검사 프로그램을 격리할 수는 없으므로
프로필의 준비·검사 명령은 신뢰하는 오프라인 명령으로 등록해야 한다.

다음 명령은 Git에 보존된 이전 커밋에서 새 임시 원장을 만들어 업그레이드를 재현한다. 그 커밋은 검사 환경 지문 도입 전이므로
첫 재개에 추가 검사 1회가 필요했다. 두 구버전 모두 같은 ID에서 완료됐다.

```bash
python3 -B pilots/p2-goal-mode/legacy.py --output /tmp/katakuri-goal-compat.json
```

[실제 중단 원장의 재개 기록](legacy.json) · [이전 커밋에서 재현한 기록](legacy-from-head.json) · [재현 코드](legacy.py)
