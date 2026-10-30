# 에이전트 작업 시각화 조사와 설계 근거

조사 기준일: 2026-10-30, Asia/Seoul. 이 문서는 제품·연구에서 확인한 사실과 agent-workflow에 적용한 설계 판단을 구분한다. 실제 기능과 운영 계약은 [관제실 사용 안내](OBSERVABILITY.ko.md)에 있다.

## 1. 설계 결론

agent-workflow의 관제실은 역할별 픽셀 작업실, 개별 호출을 펼친 실행 그래프, 시간순 인계 기록, 선택한 단계의 상세를 같은 실행 기록에 연결한다. 작업실은 누가 무엇을 하는지 빠르게 파악하는 데 쓰고, 그래프와 상세 기록은 순서·반복·실패와 근거를 확인하는 데 쓴다.

주 구조는 Langfuse·Phoenix·AgentOps 등의 trace 탐색 방식을 참고했다. Pixel Agents에서는 역할과 활동 상태를 읽기 쉽게 표현하는 방식을 참고했다. 기존 Python 표준 라이브러리 서버, SQLite, 정적 JavaScript/CSS/SVG 안에서 구현하므로 별도의 관측 서비스나 Node 런타임을 사용자에게 요구하지 않는다.

이 조합이 새 agent-workflow 사용자의 생산성을 높인다는 실험 결과는 아직 없다. 아래의 제품 기능, 관련 연구, 현재 프로젝트의 실행 구조를 대조하여 내린 설계 판단이다. 기록되지 않은 에이전트 간 대화나 내부 생각을 화면에서 만들어내지 않는 것이 전제다.

## 2. 조사 범위와 검색 전략

결정할 문제는 “어떤 시각 효과를 추가할 것인가”보다 “로컬 작업의 실행자, 순서, 인계, 중단 원인과 검증 근거를 어떻게 함께 확인할 것인가”였다. 다음 자료군을 조사했다.

| 자료군 | 확인한 내용 | 대표 검색 각도 |
|---|---|---|
| 픽셀·3D 작업실 | 역할 표시, 활동 수집, 실제 상태와 장식의 구분 | 정확한 `sungkee-dev/pixel-agents` 주소, agent office, agent visualiser |
| 로컬 coding-agent 관측기 | CLI별 지원, 세션·하위 에이전트·도구 호출 수집 | Claude/Codex local timeline, session tree, agent monitor |
| LLM 관측 제품 | trace, span, waterfall, 선택 상세, 사용량 | agent graph, expanded/aggregated graph, observability dashboard |
| 실행 그래프 도구 | 설계 그래프와 실제 실행 경로, 중간 상태, replay | LangSmith Studio, AutoGen Studio, Flowise tracing |
| 원 연구 논문 | 효과를 측정한 방법, 표본과 적용 한계 | multi-agent visual analysis, AgentLens, AutoGen Studio evaluation |

검색은 정확한 이름으로 시작하고 유사 용어와 공식 사이트별 검색으로 넓혔다. 저장소 README·소스·GitHub REST metadata, 공식 제품 문서, 원 논문을 우선했다. 최근 제품 정보는 최신성 필터를 포함한 검색과 공식 URL로 교차 확인했다. 언어는 영어·한국어를 제한하지 않았으며 제품별 고정 연도 필터는 적용하지 않았다. 연구의 출판 연도와 제품 관찰일은 따로 표시했다.

공식 설명이나 소스에서 기능을 확인한 조사다. 모든 제품을 설치해 동일한 작업으로 사용성·속도·안정성을 비교한 벤치마크는 아니다. 커뮤니티의 칭찬이나 마케팅 표현을 성능 검증으로 취급하지 않았다.

## 3. 사용자가 제시한 Pixel Agents의 정확한 범위

대상은 [sungkee-dev/pixel-agents](https://github.com/sungkee-dev/pixel-agents)다. 확인한 main SHA는 `4642740de60d6ea598e6a3678a0a60ce57406fe3`, package version은 `1.4.1`이다. GitHub metadata의 `fork`는 `false`였다. 다만 package·README·LICENSE가 [pixel-agents-hq/pixel-agents](https://github.com/pixel-agents-hq/pixel-agents)와 저자 Pablo De Lucca를 가리키므로 upstream 기반 복제본으로 판단했다. GitHub에 공식 fork 관계로 등록되어 있다고 표현하지 않는다. Upstream package는 조사 시점 `1.5.0`이었다.

구조는 공통 `AgentEvent`/`HookProvider`, Fastify 서버, React 19 webview, Canvas 2D 작업실로 나뉜다. VS Code 확장과 standalone CLI를 함께 제공하며 Node 20 이상을 요구한다. `bin.pixel-agents`는 `dist/cli.js`를 가리키고 Git 설치의 `prepare`에서 build를 수행한다. 따라서 `npx github:sungkee-dev/pixel-agents`는 지정 저장소의 CLI를 설치·실행하는 방법으로 해석할 수 있다. 이 조사에서 해당 명령을 직접 실행한 것은 아니다.

수집 경로는 Claude hooks와 JSONL 추정 fallback이다. 정상화한 이벤트를 runtime store와 transport를 거쳐 화면에 전달한다. 확인한 provider registry에는 Claude만 있었고, Codex 등은 README의 로드맵이었다. 이 프로젝트를 그대로 실행하는 것만으로 agent-workflow의 Codex·Claude·독립 리뷰·검사·릴리스를 모두 관측할 수 있다고 보기는 어렵다.

코드는 MIT이며 저작권 고지가 있다. 캐릭터·가구 자산 전체의 출처와 별도 조건까지 검증한 것은 아니다. agent-workflow는 기존 sprite를 복사하지 않고 자체 SVG/CSS 표현을 사용한다.

직접 확인한 근거:

- [고정 SHA의 package.json](https://github.com/sungkee-dev/pixel-agents/blob/4642740de60d6ea598e6a3678a0a60ce57406fe3/package.json)
- [공통 provider 계약](https://github.com/sungkee-dev/pixel-agents/blob/4642740de60d6ea598e6a3678a0a60ce57406fe3/core/src/provider.ts)
- [서버 provider registry](https://github.com/sungkee-dev/pixel-agents/blob/4642740de60d6ea598e6a3678a0a60ce57406fe3/server/src/providers/index.ts)
- [LICENSE](https://github.com/sungkee-dev/pixel-agents/blob/4642740de60d6ea598e6a3678a0a60ce57406fe3/LICENSE)

## 4. 제품·프로젝트 13개 비교

아래의 “적용 판단”은 기능 사실을 agent-workflow의 구조에 대조한 해석이다. 외부 제품의 전체 기능을 도입했다는 뜻은 아니다.

| 제품·프로젝트 | 공식 자료에서 확인한 방식 | 참고한 점 | agent-workflow 적용 판단 |
|---|---|---|---|
| [Pixel Agents](https://github.com/sungkee-dev/pixel-agents) | Hook/JSONL 이벤트로 인물 상태와 작업을 Canvas office에 표시 | 역할별 캐릭터, 활동/대기 신호, 이벤트 정상화 경계 | 작업실 표현을 참고하되 Claude 전용 provider에 의존하지 않는다. |
| [Langfuse](https://langfuse.com/docs/observability/features/agent-graphs) | Trace tree·timeline·agent graph. 동일 step의 aggregated 모드와 호출별 expanded 모드 | 개요와 실제 순서 구분, 반복 호출 보존, 선택 상세 | 실행 순서/역할 개요 두 보기의 직접적인 설계 근거다. 전체 플랫폼 설치는 필요하지 않다. |
| [LangSmith Studio](https://docs.langchain.com/langsmith/studio) | Graph node, 중간 state, thread/time travel, Agent Server API 연동 | 설계 그래프와 실행 경로 구분, 특정 시점 탐색 | 과거 기록 조회와 현재 실행 제어를 구분한다. 이를 위해 실행기를 LangGraph로 재작성하지 않는다. |
| [Arize Phoenix](https://arize.com/docs/phoenix/tracing/llm-traces) | OTLP trace, session, 지연·token·오류 상세와 annotation | Run/span 관계, 원인과 측정치의 상세 탐색 | 내부 실행 ID와 단계 ID를 명시한다. 외부 exporter는 향후 선택 확장으로 둔다. |
| [AgentOps](https://docs.agentops.ai/v2/usage/dashboard-info) | Session drawer, waterfall과 선택 event 상세 | 시간·실행 단계에서 실패 원인으로 내려가는 탐색 | 시간순 기록과 inspector를 결합한다. 관측 화면이 검사·리뷰 증거를 대신하지 않는다. |
| [AutoGen Studio](https://microsoft.github.io/autogen/dev/user-guide/autogenstudio-user-guide/usage.html) | Live message stream, control transition graph, agent/tool profiles, declarative team builder | 역할 간 인계와 control 흐름, 실행 제어 의미의 명시 | 실행 흐름 표시를 참고한다. 새 팀 편집기나 실행 프레임워크 도입은 현재 범위를 넘는다. |
| [claude-devtools](https://github.com/matt1398/claude-devtools) | 로컬 Claude session/tree, tool I/O, subagent, usage 탐색 | 필요한 상세를 점진적으로 공개하고 계층을 탐색 | 향후 CLI 내부 관측을 붙일 때 참고한다. 현재는 컨트롤러 기록을 기본 근거로 사용한다. |
| [agentwatch](https://github.com/mishanefedov/agentwatch) | 여러 로컬 CLI adapter, TUI/SSE web timeline·graph, source별 coverage 설명 | 통합 event schema와 지원 범위 공개 | 외부 transcript parser보다 native instrumentation을 우선한다. 작은 프로젝트라는 채택 한계도 고려했다. |
| [Agent Monitor](https://github.com/donvito/agent-monitor) | Codex/Claude/Pi/Hermes 로컬 trace, agent tree, 선택 trace, 모델별 사용량 | Run→agent→trace 탐색, 실제·추정 비용 구분 | 역할·실행·근거를 연결하고 데모 자료는 synthetic임을 표시하는 방식을 참고했다. |
| [Agent Visualiser](https://github.com/JoelHutchinson/agent-visualiser) | Claude JSONL 또는 OTLP를 Vue/canvas office와 agent hierarchy에 연결 | Source가 제공하는 시작·종료 정보에 따른 상태 표현 | 완료 때 받은 span을 실제 실행 중 신호로 오인하지 않도록 한다. 채택 검증은 부족하다. |
| [Agent Office — Munizada](https://github.com/Munizada/agent-office) | React/Three.js 3D 공간, CLI subprocess, SSE. README가 prototype 범위를 명시 | 작업 상태와 장식적 움직임 구분, 시간순 상세 | 별도 CLI slot을 조정된 workflow와 혼동하지 않는다. 3D를 핵심으로 선택할 근거는 부족하다. |
| [Claude Office Visualizer — Doc-Code](https://github.com/Doc-Code/agent-office) | Boss/subagent pixel simulation, WebSocket, 작업 whiteboard | 작업 상태 요약과 delegation 표현 | 랜덤 인사·커피·장식은 실행 근거에 포함하지 않는다. |
| [Flowise](https://docs.flowiseai.com/using-flowise/analytics) | Visual authoring, Agentflow V2 step tracing, V1 checkpoint parent 관계 | Graph definition과 execution/checkpoint 구분 | 고정된 코드 파이프라인은 유지하고 실제 실행 기록을 시각화한다. |

보충 공식 자료: [Langfuse trace view 변경 설명](https://langfuse.com/changelog/2025-03-19-new-trace-view), [AutoGen Studio v0.4.0 안내](https://microsoft.github.io/autogen/0.4.0/user-guide/autogenstudio-user-guide/index.html), [claude-devtools subagent 문서](https://claude-dev.tools/docs/subagents), [Flowise sequential agents/checkpoints](https://docs.flowiseai.com/using-flowise/agentflowv1/sequential-agents).

## 5. “평가가 좋다”는 판단의 근거와 한계

GitHub stars는 관심과 채택의 간접 신호다. 만족도, 생산성, 안정성 또는 결함률의 점수가 아니다. 다음 수치는 2026-10-30 GitHub REST metadata 조회 결과이며 이후 달라질 수 있다.

| 저장소·대상 | 관찰한 stars | 해석 시 주의점 |
|---|---:|---|
| [pixel-agents-hq/pixel-agents](https://api.github.com/repos/pixel-agents-hq/pixel-agents) | 9,972 | Upstream 수치이며 지정 복제본과 구분한다. |
| [sungkee-dev/pixel-agents](https://api.github.com/repos/sungkee-dev/pixel-agents) | 0 | 복제본 자체의 채택 신호다. 구현 기능의 유무를 판정하는 수치는 아니다. |
| [langfuse/langfuse](https://api.github.com/repos/langfuse/langfuse) | 36,735 | 관측 플랫폼 전체에 대한 관심이다. |
| [Arize-ai/phoenix](https://api.github.com/repos/Arize-ai/phoenix) | 12,073 | 특정 graph UI만의 평가가 아니다. |
| [AgentOps-AI/agentops](https://api.github.com/repos/AgentOps-AI/agentops) | 5,928 | SDK·플랫폼 전체 수치다. |
| [microsoft/autogen](https://api.github.com/repos/microsoft/autogen) | 61,627 | AutoGen framework 전체이며 Studio 단독 평가가 아니다. |
| [langchain-ai/langgraph](https://api.github.com/repos/langchain-ai/langgraph) | 44,142 | LangGraph framework 수치이며 LangSmith Studio 만족도가 아니다. |
| [matt1398/claude-devtools](https://api.github.com/repos/matt1398/claude-devtools) | 3,999 | Claude 전용 도구의 채택 신호다. |
| [FlowiseAI/Flowise](https://api.github.com/repos/FlowiseAI/Flowise) | 55,537 | Builder 전체이며 개별 tracing 화면의 평가가 아니다. |
| [mishanefedov/agentwatch](https://api.github.com/repos/mishanefedov/agentwatch) | 17 | 사용 경험을 일반화할 근거가 작다. |
| [donvito/agent-monitor](https://api.github.com/repos/donvito/agent-monitor) | 86 | 신생 프로젝트의 제한적인 채택 신호다. |
| [JoelHutchinson/agent-visualiser](https://api.github.com/repos/JoelHutchinson/agent-visualiser) | 0 | 기능 설명과 사용성 검증을 구분한다. |
| [Munizada/agent-office](https://api.github.com/repos/Munizada/agent-office) | 0 | Prototype 자체의 범위를 존중한다. |
| [Doc-Code/agent-office](https://api.github.com/repos/Doc-Code/agent-office) | 0 | 화려한 표현을 효과의 증거로 취급하지 않는다. |

따라서 널리 사용되는 관측 도구의 탐색 관례를 주 구조로 삼고, 픽셀 작업실을 보완적으로 결합했다. 별도의 통제 실험이나 검증된 만족도 자료 없이 “픽셀 또는 3D가 더 효과적”이라고 결론 내리지 않았다. GitHub의 `NOASSERTION` 라이선스 metadata도 라이선스가 없다는 결론으로 사용하지 않았다.

## 6. 관련 연구 2편

### AgentLens: Visual Analysis for Agent Behaviors in LLM-based Autonomous Systems

Lu et al.의 연구이며 arXiv:2402.08995, DOI `10.1109/TVCG.2024.3394053`이다. 2024년 온라인 공개와 IEEE TVCG 2025 출판 정보를 구분했다.

원 논문은 로그를 계층화하고 outline/agent/monitor 화면을 연결하여 사건 순서와 관련 정보를 탐색한다. 14명 대상 비교 연구에서 Reverie의 2D replay+text baseline 대비 multi-agent interaction 과제 T3/T4 수행 시간 감소를 각각 78.3%, 53.2%로 보고했다. 평균 SUS는 67.5였다. 작은 대학 참가자 표본과 사회 시뮬레이션 도메인이므로 코딩 에이전트 작업에 같은 효과를 기대할 근거로 일반화하지 않았다. 많은 에이전트와 긴 시계열에서 선 겹침과 layout 한계도 있다.

채택한 원칙은 개요와 선택 상세의 연결, 시간순 탐색, 역할별 정렬, 필터다. 논문의 LLM 기반 원인 추론을 도입한 것은 아니다. agent-workflow는 컨트롤러가 기록한 실행 순서와 인계를 사용한다.

근거: [원문](https://arxiv.org/html/2402.08995v1), [IEEE 출판 정보](https://ieeexplore.ieee.org/abstract/document/10520238/).

### AutoGen Studio: A No-Code Developer Tool for Building and Debugging Multi-Agent Systems

Dibia et al., 2024, arXiv:2408.15247. 실시간 stream과 사후 profiler를 함께 제공하고 messages/actions/tool outcomes/cost를 구조화한다.

평가 절은 5개월 동안 20만 회 이상의 설치와 135개가 넘는 GitHub issue를 통한 현장 반복 개선을 보고한다. 통제 실험으로 디버깅 성능을 입증한 수치로 해석하지 않았다. 논문 당시 인증 등을 포함한 production 준비는 범위 밖이라고 명시했다.

채택한 원칙은 진행 중 관측과 완료 후 분석의 병행, 결과물·검사 결과까지 이어지는 탐색이다. 새 workflow builder나 운영용 인증 계층을 이번 구현에 포함했다는 의미는 아니다.

근거: [원문](https://arxiv.org/html/2408.15247v1).

동명 연구는 구분했다. 위 TVCG AgentLens는 2026년 모바일 GUI AgentLens 및 coding-agent 평가 AgentLens와 다르다. 2021년 AgentVis의 콜센터 human-agent glyph 연구는 직접적인 근거에서 제외했다.

## 7. 비교에서 실제 설계로 이어진 결정

| 설계 결정 | 근거 | 현재 프로젝트에서의 이유 |
|---|---|---|
| 컨트롤러에서 직접 관측 | 외부 parser의 provider별 범위 차이, trace 기반 제품의 공통 모델 | 구현·검사·리뷰·릴리스 호출 경계를 이미 컨트롤러가 알고 있다. |
| 역할 작업실과 실행 그래프의 결합 | Pixel Agents의 상태 표현, Langfuse의 aggregated/expanded 구분 | 빠른 상황 파악과 정확한 시퀀스 검토를 모두 제공한다. |
| 단계 ID와 단조 증가 순번 | Run/span 관례, AgentLens의 시간순 탐색 | 같은 초에 여러 사건이 발생해도 순서를 보존하고 반복 실행을 합쳐 숨기지 않는다. |
| 인계와 실제 대화 구분 | AutoGen의 control transition, source coverage 공개 도구 | 현재 구현은 독립 CLI를 컨트롤러가 순서대로 호출한다. 메시지 교환을 추측하지 않는다. |
| 현재 증거와 과거 trace 분리 | 기존 agent-workflow fingerprint 계약 | 과거의 성공을 현재 코드의 통과로 표시하지 않는다. |
| 기록 재생과 재실행 분리 | Studio의 시점 탐색에서 얻은 사용성 원칙 | replay는 조회 커서만 바꾸며 모델 호출·배포를 발생시키지 않는다. |
| 상세 로그는 선택해서 조회 | 관측기의 선택 상세 패턴 | 원문을 기본 API·화면에 중복 노출하지 않고 필요한 근거만 읽는다. |
| 표준 라이브러리 + 정적 SVG/CSS | 기존 설치 비용과 한정된 역할·단계 구조 | 별도 graph/3D engine, 외부 서버, CDN 없이 구현할 수 있다. |

키보드 조작, 작은 화면, 색상 외 텍스트 상태, 줄어든 동작 선호(`prefers-reduced-motion`), 안전한 텍스트 렌더링도 구현 기준으로 삼았다. 이들은 에이전트 처리 성능을 높이는 기능이라기보다 관측 화면을 일관되게 사용할 수 있게 하는 조건이다.

## 8. 남은 검증 과제

사용자 작업에서 “실패 위치 찾기”, “현재 담당과 다음 단계 설명하기”, “리뷰 피드백 이후 수정 횟수 확인하기”를 과제로 삼아 관제실 사용 전후 시간과 정확도를 측정할 수 있다. 실제 측정 전에는 생산성 향상 수치를 주장하지 않는다.

현재 관측은 CLI 내부 도구 호출·subagent·전체 대화를 수집하지 않는다. 이를 추가하려면 provider와 CLI 버전별 adapter, 시작·종료 누락 처리, 별도 계약 테스트가 필요하다. 외부 OTLP exporter, 다중 사용자 인증, 수천 에이전트의 전역 layout 역시 이번 구현 범위와 분리한다.
