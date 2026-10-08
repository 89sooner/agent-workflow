# 첫 실전 파일럿: PR Search WP-061

현재 상태는 **준비 / 구현 미착수**다. 실행 원장 ID는 `PRS-WP061`이며 기존 WP-061의 번호와 범위를 유지한다.

선정 이유: 승인된 요구사항과 구체적인 DoD가 있고, 미완료 코드가 확인되며, 외부 운영 접근 없이 로컬 공용 함수·API·실행기·UI 판정 경로를 검사할 수 있다. PR Search WP-043은 사내 ACC-06 미실행으로 착수 금지이며, Katakuri는 활성 병렬 트랙과 운영 환경이 있어 첫 파일럿에서 제외했다.

- 소스 원본: `/home/roqkf/pr-search` — 수정 없음
- 실행 사본: `/home/roqkf/pr-search-wt/agent-workflow-wp061`
- 브랜치: `pilot/agent-workflow-wp061`
- [실행 계약](execution-brief.md)
- 검사/환경 도구: `pilot.py`
- 기준선 결과: `evidence/baseline-*.log`, `preparation.json`

실행할 때는 새 세션에서 다음처럼 요청한다.

> agent-workflow 스킬로 PRS-WP061을 시작해. 등록된 실행 계약과 전용 워크트리를 사용하고, 기존 main은 건드리지 마. 로컬 구현·필수 검사·독립 리뷰·문서 동기화·인계까지 진행해. 부분 완료를 전체 WP 완료로 보고하지 마.

터미널로 실행하려면:

```bash
agent-workflow show PRS-WP061
agent-workflow run PRS-WP061 --dry-run
agent-workflow queue PRS-WP061 --timeout 1800 --repairs 1
```

큐 대신 전경 실행은 `agent-workflow run PRS-WP061 --timeout 1800 --repairs 1`이다. 위 실행 명령은 이번 준비 단계에서는 호출하지 않았다.

필수 검사 7종과 추가 closure 증거 확인을 등록한다. 기존 검사 통과를 새 WP 완료로 오인하지 않도록, 13종 제약의 positive/negative 사례와 WP DoD 5개에 실제 통과 시험을 연결해야 한다.

## 시험 환경

필요한 경우 `python3 pilot.py up`, 종료는 `python3 pilot.py down`.
전용 컨테이너는 `awp-prs-wp061-pg`와 `awp-prs-wp061-redis`이며, 동적으로 지정한 loopback 포트만 사용한다. 기존 Katakuri·PR Search 운영 서비스와 볼륨을 수정하지 않는다. PostgreSQL 데이터는 이 시험용이며 컨테이너 정지 후 재기동하면 다시 마이그레이션한다. integration gate가 환경 준비를 수행한다.

## 파일럿에서 측정할 것

결과 파일에는 실제로 관측한 값만 기록한다.

- 사람이 결과를 복사해 다른 모델에 전달한 횟수
- 이미 제공한 결정을 다시 설명한 횟수
- 실패/중단 사유, 재개까지의 시간, 맥락 손실 여부
- 기존 WP의 DoD 5개와 제약 13종의 충족 여부
- 로컬 구현·검증·문서·독립 리뷰·인계의 실제 완료 상태

모든 수치가 0이거나 성공일 것이라고 미리 기록하지 않는다. 이번 파일럿은 자동화 사용성의 첫 실측이며, 운영 배포·사내 검증·전체 프로젝트 완료를 뜻하지 않는다.
