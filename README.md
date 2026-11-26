# Agent Workflow

Claude와 Codex로 진행하는 Git 작업을 로컬 원장에 기록하고, 다음 세션에서 구현·검사·독립 리뷰를 이어가는 도구입니다. 프로젝트 목표 정책을 등록하면 하나의 작업 ID로 완료 조건별 구현부터 독립 리뷰까지 실행할 수 있습니다.

**[8776 React 관제실 열기](http://127.0.0.1:8776/) · [전체 문서](docs/README.md) · [CLI 명령 찾기](docs/USER_GUIDE.ko.md#commands)**

## 지금 할 일

| 하려는 일 | 바로 시작하기 |
|---|---|
| 내 프로젝트의 목표를 맡기기 | 지침·수정 범위·검사·리뷰어를 프로필에 지정하고 `goal create`로 등록합니다. [프로젝트 목표 사용법](profiles/README.md#project-goals) |
| Katakuri 목표를 맡기고 대화를 닫기 | **새 작업 등록 → 프로젝트 설정 → Katakuri 목표 연속 실행**을 선택합니다. 등록 후 **작업·실행 관리 → 워크플로 실행**을 누릅니다. [프로필 선택과 목표 등록](docs/KATAKURI_GOAL.ko.md#web) |
| Katakuri가 멈춘 이유를 보고 이어가기 | 작업 상세에서 대기 조건과 남은 예산을 확인합니다. [근거 제출·예산 조정·재개](docs/KATAKURI_GOAL.ko.md#resume) |
| 어제 작업한 내용을 이어받기 | 작업하던 Git 폴더에서 `agent-workflow pickup`을 실행합니다. [세션 마감과 인계](docs/USER_GUIDE.ko.md#sessions) |
| 변경에 필요한 테스트를 추천받기 | **새 작업 등록 → AI로 검사 묶음 추천받기 → 보낼 내용 미리보기**를 엽니다. [검사 선택과 추천 적용](docs/REACT_MISSION_CONTROL.ko.md#register) |
| 작업을 큐에 넣거나 중단한 실행을 재개하기 | [실행·보류·중단 명령](docs/USER_GUIDE.ko.md#queue) |
| 내 프로젝트 또는 일상 AI 세션을 관측하기 | [프로젝트 연결](docs/CONNECTION_SETUP.ko.md) · [Claude·Codex·OpenCode 플러그인](docs/NATIVE_PLUGINS.ko.md) |
| 배포 여부를 확인하거나 관제실을 재시작하기 | [작업의 병합·배포 결과 조회](docs/USER_GUIDE.ko.md#release) · [관제실 서버 운영 절차](docs/OPERATIONS.ko.md#rollout) |

## 처음 설치할 때

Linux 또는 WSL에서 Python 3.11 이상과 Git을 준비하고, 이 저장소 루트에서 실행합니다. React 화면은 패키지에 포함되어 있습니다.

```bash
python3 install.py
export PATH="$HOME/.local/bin:$PATH"
agent-workflow doctor
agent-workflow daemon status
```

이 WSL의 관리 주소는 8776입니다. 새 설치에서 서버를 시작하려면 빈 포트를 확인한 뒤 `agent-workflow daemon start --port 8776`을 실행합니다. 관리 데몬은 해당 원장의 실행 대기열도 처리합니다. 8765는 이 환경의 Katakuri 운영 제어 서버입니다.

첫 작업을 연습하려면 모델 계정 없이 실행할 수 있는 [로컬 데모](docs/USER_GUIDE.ko.md#setup)를 사용하세요. 저장소를 개발할 때 필요한 빌드와 테스트는 [개발·검증 안내](docs/DEVELOPMENT.ko.md)에 있습니다.
