import { useEffect, useMemo, useRef, useState } from "react";
import { usePoll, useVisible, useReducedMotion, endpoint, get } from "./api";
import {
  assertTrace,
  atEvent,
  clock,
  when,
  color,
  graphOf,
  label,
  newTransfers,
  pathName,
} from "./model";
import type {
  Capabilities,
  Discovery,
  Event,
  Execution,
  Node,
  Project,
  Trace,
  Transfer,
} from "./types";
import { ExecutionGraph } from "./Graph";
import { Room } from "./Room";
import { ConnectionDialog, diagnosis } from "./Connections";
const empty: Trace = {
  schema_version: 1,
  generated_at: "",
  actors: [],
  spans: [],
  edges: [],
  events: [],
  runs: [],
};
const persisted = (key: string) => {
  try {
    return localStorage.getItem(key) || "";
  } catch {
    return "";
  }
};
const save = (key: string, value: string) => {
  try {
    localStorage.setItem(key, value);
  } catch {}
};
function Status({ value }: { value: string }) {
  return (
    <span className="status" style={{ color: color(value) }}>
      <i style={{ background: color(value) }} />
      {label(value)}
    </span>
  );
}
function Inspector({
  node,
  ledger,
  taskGoal,
  replay,
}: {
  node: Node | undefined;
  ledger: string;
  taskGoal?: string;
  replay: boolean;
}) {
  const [log, setLog] = useState<{
    key: string;
    body: string;
    error?: string;
  } | null>(null);
  const [kind, setKind] = useState("");
  const id = node?.span?.id;
  const url =
    kind && id
      ? endpoint(
          `/api/observability/artifacts/${encodeURIComponent(id)}/${kind}`,
          ledger,
        )
      : null;
  useEffect(() => {
    setLog(null);
    if (!url) return;
    const c = new AbortController();
    void get<{ text: string; truncated?: boolean }>(url, c.signal)
      .then((v) =>
        setLog({
          key: url,
          body: v.text + (v.truncated ? "\n… 로그가 잘렸습니다." : ""),
        }),
      )
      .catch((e) => {
        if (!c.signal.aborted) setLog({ key: url, body: "", error: String(e) });
      });
    return () => c.abort();
  }, [url]);
  useEffect(() => setKind(""), [id, ledger]);
  return (
    <section className="panel inspector" aria-label="선택한 실행 상세">
      <div className="panel-heading">
        <div>
          <span className="eyebrow">EXECUTION DETAIL</span>
          <h2>{node ? "선택한 실행" : "실행을 살펴보세요"}</h2>
        </div>
      </div>
      {!node ? (
        <div className="empty-state">
          <span>↖</span>
          <p>
            그래프의 노드나 공간의 담당자를 선택하면 이름, 실행 상태와 실제
            결과를 확인할 수 있습니다.
          </p>
        </div>
      ) : (
        <>
          <div className="identity-card">
            <div
              className="identity-avatar"
              style={{ borderColor: color(node.status) }}
            >
              {node.kind === "service" ? "⌘" : node.label.slice(0, 1)}
            </div>
            <div>
              <h3>{node.label}</h3>
              <p>
                {label(node.role)} · {node.actor?.provider || "컨트롤러"}
              </p>
              <Status value={node.status} />
            </div>
          </div>
          <p className="source-note">
            {node.actor?.name_source === "assigned"
              ? "실제로 할당한 이름"
              : node.actor?.name_source === "native"
                ? "OpenCode API에 기록된 이름"
                : node.actor?.name_source === "system"
                  ? "시스템 실행기"
                  : "기록 기반 자동 이름"}
          </p>
          <dl className="facts">
            <dt>작업</dt>
            <dd>{node.taskId}</dd>
            <dt>목표</dt>
            <dd>{taskGoal || "목표 미기록"}</dd>
            <dt>실행 호출</dt>
            <dd>
              {node.span?.id ||
                (node.actor?.provider === "opencode"
                  ? "응답 기록 없음"
                  : "아직 실행 전 배정")}
            </dd>
            <dt>요청 모델</dt>
            <dd>{node.actor?.model || node.span?.model || "미기록"}</dd>
            <dt>관측 모델</dt>
            <dd>{node.actor?.observed_model || "미확인"}</dd>
            <dt>네이티브 세션</dt>
            <dd>{node.actor?.native_session_id || "미기록"}</dd>
            <dt>시작</dt>
            <dd>{when(node.span?.started_at)}</dd>
            {!replay && (
              <>
                <dt>종료</dt>
                <dd>{when(node.span?.ended_at)}</dd>
              </>
            )}
          </dl>
          {node.span?.result != null && (
            <details className="evidence" open>
              <summary>기록된 결과</summary>
              <pre>{JSON.stringify(node.span.result, null, 2)}</pre>
            </details>
          )}
          {node.span?.error != null && (
            <div className="notice error">{String(node.span.error)}</div>
          )}
          {!replay && node.span?.input != null && (
            <details className="evidence">
              <summary>전달된 작업 조건</summary>
              <pre>{JSON.stringify(node.span.input, null, 2)}</pre>
            </details>
          )}
          {!replay && node.span?.artifacts?.length ? (
            <div className="log-section">
              <div className="segmented">
                {node.span.artifacts.map((k) => (
                  <button
                    key={k}
                    aria-pressed={kind === k}
                    onClick={() => setKind(k)}
                  >
                    {k === "stdout" ? "표준 출력" : "오류 출력"}
                  </button>
                ))}
              </div>
              {url && (
                <pre aria-label="실행 로그">
                  {log?.key === url ? log.error || log.body : "로그를 읽는 중…"}
                </pre>
              )}
            </div>
          ) : null}
          <p className="source-note">
            {replay
              ? "재생 시점까지의 실행 상태와 결과입니다."
              : node.actor?.provider === "opencode"
                ? "OpenCode API의 세션·응답·도구 상태입니다. 응답 종료와 작업 검증 완료는 다릅니다."
                : "컨트롤러가 기록한 실행입니다. 모델 내부 사고나 도구 호출을 추정하지 않습니다."}
          </p>
        </>
      )}
    </section>
  );
}
export default function App() {
  const [ledger, setLedger] = useState(new URLSearchParams(location.search).get("ledger") || persisted("mission-ledger")),
    [project, setProject] = useState(""),
    [taskId, setTask] = useState(new URLSearchParams(location.search).get("task") || ""),
    [runId, setRun] = useState("");
  const [search, setSearch] = useState(""),
    [filter, setFilter] = useState("all"),
    [taskLimit, setTaskLimit] = useState(40),
    [selected, setSelected] = useState<string | null>(null);
  const [refresh, setRefresh] = useState(0),
    [room, setRoom] = useState(persisted("mission-room") !== "off"),
    [reduce, setReduce] = useState(persisted("mission-motion") === "reduce");
  const [replay, setReplay] = useState<Trace | null>(null),
    [position, setPosition] = useState(0),
    [playing, setPlaying] = useState(false),
    [transfers, setTransfers] = useState<Transfer[]>([]);
  const [olderPage, setOlderPage] = useState<{
      scope: string;
      events: Event[];
    }>({ scope: "", events: [] }),
    [before, setBefore] = useState<number | null | undefined>(),
    [moreLoading, setMoreLoading] = useState(false),
    [moreError, setMoreError] = useState(""),
    [eventPage, setEventPage] = useState(0);
  const visible = useVisible(),
    systemReduce = useReducedMotion();
  const [connecting, setConnecting] = useState(false);
  const [pendingConnection, setPendingConnection] = useState("");
  const capabilities = usePoll<Capabilities>(
    "/api/capabilities",
    30000,
    refresh,
  );
  const discovery = usePoll<Discovery>("/api/connections", 15000, refresh);
  const ledgers = discovery.data?.ledgers || [],
    currentLedger = ledgers.find((l) => l.id === ledger);
  useEffect(() => {
    if (pendingConnection) {
      if (ledgers.some((l) => l.id === pendingConnection)) {
        changeLedger(pendingConnection);
        setPendingConnection("");
      }
      return;
    }
    if (discovery.data && !ledger) {
      const initial = ledgers.find((l) => l.task_count && l.status === "available")?.id ||
        ledgers.find((l) => l.status === "available")?.id || ledgers[0]?.id;
      if (initial) changeLedger(initial);
    }
  }, [discovery.data, ledger, ledgers, pendingConnection]);
  const overview = usePoll<Project>(
    currentLedger?.status === "available"
      ? endpoint(
          "/api/project-observability",
          ledger,
          project ? { project } : {},
        )
      : null,
    4000,
    refresh,
    assertTrace,
  );
  const trace = usePoll<Trace>(
    currentLedger?.status === "available" && taskId
      ? endpoint("/api/observability", ledger, {
          task_id: taskId,
          ...(runId ? { run_id: runId } : {}),
        })
      : null,
    2000,
    refresh,
    assertTrace,
  );
  const live = taskId ? trace.data : overview.data;
  const query = taskId ? trace : overview;
  const scope = `${ledger}:${project}:${taskId}:${runId}:${trace.data?.selected_run_id || ""}`;
  const activeScope = useRef(scope);
  activeScope.current = scope;
  const paginationRequest = useRef<AbortController | null>(null);
  const older = olderPage.scope === scope ? olderPage.events : [];
  const cursor = useRef<{ scope: string; seq: number | null }>({
    scope: "",
    seq: null,
  });
  useEffect(() => {
    setReplay(null);
    setPlaying(false);
    setPosition(0);
    setOlderPage({ scope, events: [] });
    setBefore(undefined);
    paginationRequest.current?.abort();
    setMoreLoading(false);
    setMoreError("");
    setTransfers([]);
    setEventPage(0);
    return () => paginationRequest.current?.abort();
  }, [scope]);
  useEffect(() => {
    save("mission-ledger", ledger);
  }, [ledger]);
  const mergedTrace = useMemo(() => {
    if (!live) return empty;
    const events = [
      ...new Map([...older, ...live.events].map((e) => [e.seq, e])).values(),
    ].sort((a, b) => a.seq - b.seq);
    return { ...live, events };
  }, [live, older]);
  const motion =
    visible &&
    !reduce &&
    !systemReduce &&
    !replay &&
    !query.error &&
    !discovery.error &&
    query.fresh &&
    discovery.fresh &&
    !!live;
  useEffect(() => {
    if (!live) return;
    if (cursor.current.scope !== scope) cursor.current = { scope, seq: null };
    const next = newTransfers(live.events, cursor.current.seq, Date.now());
    cursor.current.seq = next.last;
    if (motion && next.transfers.length) setTransfers(next.transfers);
    else if (!motion) setTransfers([]);
  }, [live, scope, motion]);
  useEffect(() => {
    if (!transfers.length) return;
    const t = setTimeout(() => setTransfers([]), 1800);
    return () => clearTimeout(t);
  }, [transfers]);
  useEffect(() => {
    if (!visible || query.error) setPlaying(false);
  }, [visible, query.error]);
  useEffect(() => {
    if (!playing || !replay) return;
    const t = setInterval(
      () =>
        setPosition((i) => {
          if (i >= replay.events.length - 1) {
            setPlaying(false);
            return i;
          }
          return i + 1;
        }),
      700,
    );
    return () => clearInterval(t);
  }, [playing, replay]);
  const data = replay
    ? atEvent(replay, replay.events[position]?.seq ?? 0)
    : mergedTrace;
  const tasks = overview.data?.tasks || [];
  const focusedTask = trace.data?.task || tasks.find((t) => t.id === taskId);
  const graph = useMemo(
    () =>
      graphOf(
        data,
        taskId && focusedTask ? [focusedTask] : tasks,
        taskId || undefined,
        selected,
      ),
    [data, tasks, taskId, focusedTask, selected],
  );
  const selectedNode =
    graph.nodes.find((n) => n.id === selected) ||
    data.spans
      .filter((s) => s.id === selected)
      .map((s) => ({
        id: s.id,
        taskId: s.task_id,
        actorId: s.actor,
        label: data.actors.find((a) => a.id === s.actor)?.label || s.label,
        role: s.role,
        status: s.status,
        kind: "agent",
        span: s,
        actor: data.actors.find((a) => a.id === s.actor),
        x: 0,
        y: 0,
      }))[0];
  const selectTask = (id: string, run = "") => {
    setTask(id);
    setRun(run);
    setSelected(null);
    setReplay(null);
    setPlaying(false);
    setOlderPage({ scope, events: [] });
    setBefore(undefined);
  };
  const selectNode = (n: Node) => {
    if (taskId !== n.taskId) {
      setTask(n.taskId);
      setRun(n.span?.run_id || "");
      setReplay(null);
      setPlaying(false);
    }
    setSelected(n.id);
  };
  const changeLedger = (id: string) => {
    setLedger(id);
    setProject("");
    selectTask("");
    setSearch("");
    setFilter("all");
  };
  const counts = overview.data?.counts || {};
  const openCode = currentLedger?.kind === "opencode";
  const executions: Execution[] =
    openCode && taskId
      ? (trace.data?.runs || []).map((r) => ({
          ...r,
          source: "opencode",
          run_id: r.id,
        }))
      : overview.data?.executions || [];
  const filtered = tasks
    .filter(
      (t) =>
        (!search ||
          `${t.id} ${t.goal} ${(overview.data?.actors || [])
            .filter((a) => a.task_id === t.id)
            .map((a) => a.label)
            .join(" ")}`
            .toLowerCase()
            .includes(search.toLowerCase())) &&
        (filter === "all" ||
          (filter === "active" &&
            ["running", "implementing", "checking", "reviewing"].includes(
              t.status,
            )) ||
          (filter === "attention" &&
            (["blocked", "retrying"].includes(t.status) || !!t.hold)) ||
          (filter === "completed" && t.status === "completed")),
    )
    .sort(
      (a, b) =>
        Number(b.id === taskId) - Number(a.id === taskId) ||
        Number(
          ["running", "implementing", "checking", "reviewing"].includes(
            b.status,
          ),
        ) -
          Number(
            ["running", "implementing", "checking", "reviewing"].includes(
              a.status,
            ),
          ) ||
        String(b.updated_at || "").localeCompare(String(a.updated_at || "")),
    );
  const eventSource = replay || mergedTrace;
  const replayStart = () => {
    if (replay) {
      setPlaying((v) => !v);
      return;
    }
    if (!mergedTrace.events.length) return;
    setReplay({ ...mergedTrace });
    setPosition(0);
    setEventPage(Math.max(0, Math.ceil(mergedTrace.events.length / 100) - 1));
    setPlaying(true);
    setTransfers([]);
  };
  const loadOlder = async () => {
    const at =
      before === undefined ? trace.data?.pagination?.next_before : before;
    if (!at || !trace.data) return;
    const oldScope = scope;
    paginationRequest.current?.abort();
    const request = new AbortController();
    paginationRequest.current = request;
    setMoreLoading(true);
    setMoreError("");
    try {
      const v = await get<Trace>(
        endpoint("/api/observability", ledger, {
          task_id: taskId,
          run_id: trace.data.selected_run_id || "",
          before: String(at),
        }),
        request.signal,
        assertTrace,
      );
      if (request.signal.aborted || activeScope.current !== oldScope) return;
      setOlderPage((p) => ({
        scope: oldScope,
        events: [...v.events, ...(p.scope === oldScope ? p.events : [])].slice(
          0,
          5000,
        ),
      }));
      setBefore(v.pagination?.has_more ? v.pagination.next_before : null);
      setEventPage(Math.floor((live!.events.length + older.length) / 100));
    } catch (e) {
      if (!request.signal.aborted && activeScope.current === oldScope)
        setMoreError(String(e));
    } finally {
      if (activeScope.current === oldScope) setMoreLoading(false);
    }
  };
  const selectedActors = (
    taskId
      ? data.actors
      : data.actors.filter((a) => a.kind === "agent" || a.kind === "assignment")
  ).slice(0, 100);
  const truncated =
    overview.data?.truncated &&
    Object.values(overview.data.truncated).some(Boolean);
  const observationError = query.error || discovery.error;
  const issue = diagnosis(observationError);
  const emptyRecords =
    !!currentLedger &&
    !!overview.data &&
    !overview.data.tasks.length &&
    !overview.error;
  const canManage =
    capabilities.data?.can_manage_tasks &&
    currentLedger?.id === capabilities.data.primary_ledger_id;
  return (
    <div
      className="mission-shell"
      data-mode={replay ? "replay" : motion ? "live" : "paused"}
    >
      <a className="skip-link" href="#mission-main">
        관제실 본문으로 이동
      </a>
      {connecting && capabilities.data && (
        <ConnectionDialog
          capabilities={capabilities.data}
          initialProject={project || currentLedger?.projects[0]?.path || ""}
          onClose={() => setConnecting(false)}
          onConnected={(id) => {
            setPendingConnection(id);
            setConnecting(false);
            setRefresh((v) => v + 1);
          }}
        />
      )}
      <aside className="sidebar">
        <a className="brand" href="/mission">
          <span className="brand-mark">⌘</span>
          <span>
            AGENT WORKFLOW<small>MISSION CONTROL</small>
          </span>
        </a>
        <a className="connect-entry" href="/sessions">일상 세션 · WP 없이 관측</a>
        <button
          className="connect-entry"
          onClick={() => setConnecting(true)}
          disabled={!capabilities.data}
        >
          프로젝트 연결
        </button>
        <div className="sidebar-section">
          <label htmlFor="ledger">작업 원장 / 연결</label>
          <select
            id="ledger"
            value={ledger}
            onChange={(e) => changeLedger(e.target.value)}
          >
            {!ledgers.length && (
              <option value="">
                {discovery.loading ? "연결 확인 중…" : "연결된 기록 없음"}
              </option>
            )}
            {ledger && !currentLedger && <option value={ledger} disabled>접근 불가 · {ledger}</option>}
            {ledgers.map((l) => (
              <option key={l.id} value={l.id}>
                {l.label || pathName(l.home)} · {l.task_count}
                {l.status !== "available" ? " · 연결 확인 필요" : ""}
              </option>
            ))}
          </select>
          {discovery.data && ledger && currentLedger?.status !== "available" && <p role="alert">
            지정한 원장에 접근할 수 없습니다. 다른 원장의 동명 작업으로 대체하지 않습니다.
            연결을 복구하거나 작업 원장을 직접 선택하세요.
          </p>}
          <p className="ledger-path" title={currentLedger?.home}>
            {currentLedger?.home || "연결된 원장 없음"}
          </p>
          <label htmlFor="project">프로젝트</label>
          <select
            id="project"
            value={project}
            onChange={(e) => {
              setProject(e.target.value);
              selectTask("");
            }}
          >
            <option value="">모든 프로젝트</option>
            {currentLedger?.projects.map((p) => (
              <option key={p.id} value={p.path}>
                {pathName(p.path)}
              </option>
            ))}
          </select>
        </div>
        <button
          className={`overview-button ${!taskId ? "selected" : ""}`}
          onClick={() => selectTask("")}
        >
          ◈ 프로젝트 전체 관제실 <span>{counts.tasks || 0}</span>
        </button>
        <div className="task-search">
          <label className="sr-only" htmlFor="task-search">
            작업·담당자 검색
          </label>
          <input
            id="task-search"
            placeholder="작업·담당자 검색"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
          <label className="sr-only" htmlFor="task-filter">
            작업 상태
          </label>
          <select
            id="task-filter"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
          >
            <option value="all">전체 작업</option>
            <option value="active">진행 중</option>
            <option value="attention">확인 필요</option>
            <option value="completed">완료</option>
          </select>
        </div>
        <div className="task-list" aria-label="작업 목록">
          {filtered.slice(0, taskLimit).map((t) => (
            <button
              key={t.id}
              className={`task-item ${taskId === t.id ? "selected" : ""}`}
              onClick={() => selectTask(t.id)}
              aria-current={taskId === t.id ? "true" : undefined}
            >
              <span className="task-id">{t.id}</span>
              <strong>{t.goal}</strong>
              <Status value={t.hold ? "held" : t.status} />
            </button>
          ))}
          {!filtered.length && (
            <div className="muted" data-testid="task-empty">
              <p>
                {overview.loading
                  ? "기록을 읽는 중…"
                  : search || filter !== "all"
                    ? "검색 조건에 맞는 작업이 없습니다."
                    : !currentLedger
                      ? "프로젝트를 연결해 관측을 시작하세요."
                      : observationError
                        ? "연결 상태를 확인하세요."
                        : "아직 관측된 작업이 없습니다."}
              </p>
              {(search || filter !== "all") && (
                <button
                  onClick={() => {
                    setSearch("");
                    setFilter("all");
                  }}
                >
                  검색 조건 초기화
                </button>
              )}
            </div>
          )}
          {filtered.length > taskLimit && (
            <button onClick={() => setTaskLimit((v) => v + 40)}>
              작업 더 보기
            </button>
          )}
        </div>
        <div className="sidebar-bottom">
          {currentLedger?.kind === "opencode" ? (
            <p>OpenCode에서 평소처럼 작업하세요. 새 세션은 자동 표시됩니다.</p>
          ) : canManage ? (
            <a href="/">작업 등록·실행 관리 ↗</a>
          ) : (
            <p>관측 전용 · 이 연결에서 작업을 등록하거나 실행하지 않습니다.</p>
          )}
          <p>관측 화면 · 실행 변경 없음</p>
        </div>
      </aside>
      <main id="mission-main">
        <header className="topbar">
          <div className="breadcrumbs">
            워크스페이스 <span>/</span>{" "}
            {taskId ? "작업 실행" : "프로젝트 관제실"}
          </div>
          <div className="top-actions">
            <span
              className={`connection ${query.error || discovery.error ? "offline" : ""}`}
            >
              <i />
              {query.error || discovery.error
                ? "연결 확인 필요"
                : live
                  ? "연결됨"
                  : discovery.loading
                    ? "연결 확인 중"
                    : "연결 필요"}
            </span>
            <button
              onClick={() => setRefresh((v) => v + 1)}
              aria-label="기록 새로고침"
            >
              ↻ 새로고침
            </button>
          </div>
        </header>
        <div className="workspace">
          <div className="page-heading">
            <div>
              <div className="eyebrow">OBSERVE THE WORK</div>
              <h1>
                {taskId
                  ? "실행의 흐름을 따라가세요"
                  : "에이전트가 일하는 관제실"}
              </h1>
              <p>
                {focusedTask?.goal ||
                  "배정에서 결과까지, 실제 기록으로 연결된 작업 흐름입니다."}
              </p>
              {taskId && <code>{taskId}</code>}
            </div>
            <div className={`mode-pill ${replay ? "replay" : ""}`}>
              {replay
                ? "◷ 기록 재생"
                : query.error || discovery.error
                  ? "Ⅱ 연결 끊김"
                  : !currentLedger
                    ? "연결 필요"
                    : visible
                      ? "● 실시간 관측"
                      : "Ⅱ 관측 일시 정지"}
              <small>
                {query.at
                  ? clock(new Date(query.at).toISOString())
                  : "동기화 대기"}
              </small>
            </div>
          </div>
          {(query.error || discovery.error) && (
            <div role="alert" className="notice error">
              <h2>{issue.title}</h2>
              <p>{issue.action}</p>
              <p>{observationError}</p>
              <p>
                마지막 성공:{" "}
                {query.at
                  ? when(new Date(query.at).toISOString())
                  : when(currentLedger?.last_success_at || undefined)}{" "}
                · 이전 기록을 유지하며 활동 표시를 멈춥니다.
              </p>
              <button onClick={() => setRefresh((v) => v + 1)}>
                다시 연결
              </button>
            </div>
          )}
          {capabilities.error && !observationError && (
            <p className="notice error" role="alert">
              연결 설정 권한을 확인하지 못했습니다. {capabilities.error}{" "}
              새로고침해 다시 확인하세요.
            </p>
          )}
          {!currentLedger && !discovery.loading && !discovery.error && (
            <section className="welcome-card" data-testid="onboarding-empty">
              <span className="eyebrow">FIRST CONNECTION</span>
              <h2>내 프로젝트의 작업을 여기서 확인하세요</h2>
              <p>
                프로젝트와 사용 도구를 한 번 연결하세요. 새 기록은 자동으로
                나타납니다.
              </p>
              <ol>
                <li>프로젝트 폴더 선택</li>
                <li>도구와 관측 범위 확인</li>
                <li>연결 후 세션 선택</li>
              </ol>
              <button
                onClick={() => setConnecting(true)}
                disabled={!capabilities.data}
              >
                첫 프로젝트 연결
              </button>
              <p className="muted">
                OpenCode는 서버의 새 세션을, Codex·Claude는 workflow에 남긴 실행
                기록을 관측합니다.
              </p>
            </section>
          )}
          {emptyRecords && (
            <section className="notice" data-testid="no-sessions">
              <h2>
                {currentLedger?.kind === "opencode"
                  ? "연결됐지만 이 프로젝트의 세션이 없습니다"
                  : "연결됐지만 아직 작업 기록이 없습니다"}
              </h2>
              <p>
                {currentLedger?.kind === "opencode"
                  ? "OpenCode에서 같은 프로젝트로 세션을 시작하면 자동 표시됩니다. 이미 작업 중이라면 연결한 서버와 프로젝트 폴더가 같은지 확인하세요."
                  : "Codex·Claude의 일반 대화는 자동 수집하지 않습니다. workflow로 기록한 작업이 있다면 선택한 프로젝트와 연결을 확인하세요."}
              </p>
              <button onClick={() => setConnecting(true)}>
                연결 설정 확인
              </button>
              <button onClick={() => setRefresh((v) => v + 1)}>
                기록 다시 조회
              </button>
            </section>
          )}
          {currentLedger && (
            <>
              <div className="metrics">
                {[
                  [
                    "확인된 실행",
                    counts.running || 0,
                    currentLedger?.kind === "opencode"
                      ? "OpenCode가 busy로 보고한 세션"
                      : "진행 중인 controller 프로세스",
                  ],
                  ["실행 대기", counts.queued || 0, "큐에 등록된 작업"],
                  [
                    "상태 미확인",
                    counts.unknown || 0,
                    "종료 증거를 확인할 수 없음",
                  ],
                  ["관측 작업", counts.tasks || 0, "선택한 원장의 작업"],
                ].map(([title, value, desc]) => (
                  <div className="metric" key={title}>
                    <span>{title}</span>
                    <strong>{value}</strong>
                    <small>{desc}</small>
                  </div>
                ))}
              </div>
              {currentLedger?.kind === "opencode" && (
                <p className="notice">
                  OpenCode 읽기 전용 관측 · 새 세션 자동 표시 · 대기는 응답
                  대기이며 작업 검증 완료가 아닙니다. 세션을 선택하면 기록된
                  에이전트 이름·모델·최근 응답과 도구 상태를 확인할 수 있습니다.
                </p>
              )}
              {truncated && (
                <p className="notice">
                  프로젝트 집계는 최근 기록 범위입니다. 잘린 항목의 전체 이력은
                  작업을 선택해 확인하세요.
                </p>
              )}
              <div className="view-toolbar">
                <div>
                  {taskId ? (
                    <label>
                      실행 기록{" "}
                      <select
                        aria-label="실행 기록"
                        value={runId}
                        onChange={(e) => {
                          setRun(e.target.value);
                          setSelected(null);
                          setReplay(null);
                          setPlaying(false);
                        }}
                      >
                        <option value="">현재 / 최근 실행</option>
                        {trace.data?.runs.map((r) => (
                          <option key={r.id} value={r.id}>
                            {when(r.started_at)} · {label(r.status)} ·{" "}
                            {r.id.slice(0, 8)}
                          </option>
                        ))}
                      </select>
                      {trace.data?.runs_truncated && (
                        <span
                          className="muted"
                          role="status"
                          data-testid="history-limit"
                        >
                          최근 조회 한도에 도달했습니다. 이 목록은 전체 이력이
                          아닐 수 있습니다.
                        </span>
                      )}
                    </label>
                  ) : (
                    <span className="muted">
                      노드를 선택해 작업의 전체 이력으로 이동하세요.
                    </span>
                  )}
                </div>
                <div className="view-options">
                  <label>
                    <input
                      type="checkbox"
                      checked={room}
                      onChange={(e) => {
                        setRoom(e.target.checked);
                        save("mission-room", e.target.checked ? "on" : "off");
                      }}
                    />
                    3D 공간
                  </label>
                  <label>
                    <input
                      type="checkbox"
                      checked={reduce || systemReduce}
                      disabled={systemReduce}
                      onChange={(e) => {
                        setReduce(e.target.checked);
                        save(
                          "mission-motion",
                          e.target.checked ? "reduce" : "system",
                        );
                      }}
                    />
                    움직임 줄이기
                  </label>
                </div>
              </div>
              {query.loading && (
                <p className="notice" role="status">
                  실행 기록을 읽고 있습니다…
                </p>
              )}
              <div className="work-grid">
                <div className="work-center">
                  {room && (
                    <Room
                      nodes={graph.nodes}
                      selected={selected}
                      onSelect={selectNode}
                      motion={motion}
                      transfers={transfers}
                      activityKnown={!!replay || (!query.error && !discovery.error && query.fresh && discovery.fresh && !!live)}
                      historical={!!replay || !!runId}
                      visible={visible}
                    />
                  )}
                  <ExecutionGraph
                    graph={graph}
                    selected={selected}
                    onSelect={selectNode}
                    transfers={transfers}
                    motion={motion}
                    replay={!!replay}
                  />
                  <section className="panel">
                    <div className="panel-heading">
                      <div>
                        <span className="eyebrow">PEOPLE & PROCESSES</span>
                        <h2>담당자와 실행기</h2>
                      </div>
                      <span className="muted">
                        {selectedActors.length}개 표시
                      </span>
                    </div>
                    <div className="actor-list">
                      {selectedActors.map((a) => {
                        const node =
                          graph.nodes.find((n) => n.actorId === a.id) ||
                          data.spans.find((s) => s.actor === a.id);
                        return (
                          <button
                            key={a.id}
                            onClick={() => {
                              if (!taskId) {
                                const span = data.spans.find(
                                  (s) => s.actor === a.id,
                                );
                                selectTask(a.task_id, span?.run_id || "");
                              }
                              if (node) setSelected(node.id);
                            }}
                            className="actor-card"
                          >
                            <span
                              className="actor-dot"
                              style={{ background: color(a.status) }}
                            />
                            <span>
                              <strong>{a.label}</strong>
                              <small>
                                {label(a.role)} ·{" "}
                                {a.name_source === "assigned"
                                  ? "할당 이름"
                                  : a.name_source === "native"
                                    ? "OpenCode 기록 이름"
                                    : a.name_source === "system"
                                      ? "시스템"
                                      : "자동 이름"}
                              </small>
                            </span>
                            <Status value={a.status} />
                          </button>
                        );
                      })}
                      {!selectedActors.length && (
                        <p className="muted">담당자 기록이 없습니다.</p>
                      )}
                    </div>
                    {data.actors.length > 100 && (
                      <p className="panel-caption">
                        최대 100개 항목을 표시합니다. 작업을 선택해 범위를
                        좁히세요.
                      </p>
                    )}
                  </section>
                  <section className="panel timeline-panel">
                    <div className="panel-heading">
                      <div>
                        <span className="eyebrow">EVENT STREAM</span>
                        <h2>{replay ? "실행 기록 재생" : "최근 일어난 일"}</h2>
                      </div>
                      <div className="replay-actions">
                        <button
                          disabled={!mergedTrace.events.length || !visible}
                          onClick={replayStart}
                        >
                          {playing
                            ? "Ⅱ 일시 정지"
                            : replay
                              ? "▶ 계속 재생"
                              : "▶ 기록 재생"}
                        </button>
                        {replay && (
                          <button
                            onClick={() => {
                              setReplay(null);
                              setPlaying(false);
                              setEventPage(0);
                            }}
                          >
                            실시간으로 돌아가기
                          </button>
                        )}
                      </div>
                    </div>
                    {replay && (
                      <div className="replay-scrubber">
                        <label htmlFor="event-position">
                          이벤트 {position + 1} / {replay.events.length} ·{" "}
                          {clock(replay.events[position]?.at)}
                        </label>
                        <input
                          id="event-position"
                          type="range"
                          min="0"
                          max={Math.max(0, replay.events.length - 1)}
                          value={position}
                          onChange={(e) => {
                            setPlaying(false);
                            setPosition(Number(e.target.value));
                          }}
                        />
                        <small>
                          이벤트 순서 재생 · 실제 작업을 실행하지 않습니다.
                        </small>
                      </div>
                    )}
                    <ol className="event-list">
                      {[...eventSource.events]
                        .reverse()
                        .slice(eventPage * 100, (eventPage + 1) * 100)
                        .map((e) => (
                          <li
                            data-span-id={e.span_id}
                            data-event-seq={e.seq}
                            key={e.seq}
                            className={
                              replay?.events[position]?.seq === e.seq
                                ? "event-current"
                                : ""
                            }
                          >
                            <time>{clock(e.at)}</time>
                            <i
                              style={{ background: color(e.status || "idle") }}
                            />
                            <button
                              onClick={() => {
                                if (!taskId && e.task_id) {
                                  selectTask(e.task_id, e.run_id || "");
                                  if (e.span_id) setSelected(e.span_id);
                                } else {
                                  if (e.span_id) setSelected(e.span_id);
                                  if (replay) {
                                    setPlaying(false);
                                    setPosition(
                                      replay.events.findIndex(
                                        (x) => x.seq === e.seq,
                                      ),
                                    );
                                  }
                                }
                              }}
                            >
                              <strong>{e.summary || e.kind}</strong>
                              <small>
                                {e.kind} · #{e.seq}
                                {e.task_id ? " · " + e.task_id : ""}
                              </small>
                            </button>
                          </li>
                        ))}
                    </ol>
                    {!eventSource.events.length && (
                      <p className="empty-state">
                        아직 관측 이벤트가 없습니다.
                      </p>
                    )}
                    <div className="panel-footer">
                      <span>
                        {Math.min(
                          eventPage * 100 + 1,
                          eventSource.events.length,
                        )}
                        –
                        {Math.min(
                          (eventPage + 1) * 100,
                          eventSource.events.length,
                        )}{" "}
                        / {eventSource.events.length}개
                      </span>
                      <div className="event-navigation">
                        <button
                          disabled={eventPage === 0}
                          onClick={() => setEventPage((v) => v - 1)}
                        >
                          최신 쪽
                        </button>
                        <button
                          disabled={
                            (eventPage + 1) * 100 >= eventSource.events.length
                          }
                          onClick={() => setEventPage((v) => v + 1)}
                        >
                          이전 쪽
                        </button>
                      </div>
                      {taskId && !replay && (
                        <>
                          <button
                            disabled={
                              moreLoading ||
                              older.length >= 5000 ||
                              before === null ||
                              (before === undefined &&
                                !trace.data?.pagination?.has_more)
                            }
                            onClick={() => void loadOlder()}
                          >
                            {moreLoading ? "읽는 중…" : "이전 기록 불러오기"}
                          </button>
                          <a
                            href={endpoint(
                              "/api/observability/export",
                              ledger,
                              {
                                task_id: taskId,
                                ...(trace.data?.selected_run_id
                                  ? { run_id: trace.data.selected_run_id }
                                  : {}),
                              },
                            )}
                          >
                            JSON 내보내기 ↗
                          </a>
                        </>
                      )}
                    </div>
                    {moreError && (
                      <p role="alert" className="notice error">
                        {moreError}
                      </p>
                    )}
                  </section>
                  <section className="panel">
                    <div className="panel-heading">
                      <div>
                        <span className="eyebrow">RUN HISTORY</span>
                        <h2>{openCode ? "OpenCode 응답 이력" : "실행 이력"}</h2>
                      </div>
                    </div>
                    <div className="run-list">
                      {executions
                        .filter((r) => !taskId || r.task_id === taskId)
                        .slice(0, 30)
                        .map((r) => (
                          <button
                            key={r.id}
                            onClick={() =>
                              selectTask(r.task_id || "", r.run_id || "")
                            }
                          >
                            <span>
                              <strong>{r.task_id}</strong>
                              <small>
                                {r.source === "opencode"
                                  ? "OpenCode 응답"
                                  : r.source === "direct"
                                    ? "직접 실행"
                                    : "큐 실행"}{" "}
                                · {when(r.started_at)} · {label(r.action)}
                              </small>
                            </span>
                            <Status value={r.status} />
                          </button>
                        ))}
                    </div>
                    <p className="panel-caption">
                      {openCode
                        ? taskId
                          ? "최근 응답 30개 표시 · 응답 종료는 작업 검증 완료와 다릅니다."
                          : "세션을 선택하면 최근 응답 이력을 확인할 수 있습니다."
                        : "최근 30개 표시 · 큐와 연결된 실행은 중복 집계하지 않습니다."}
                    </p>
                  </section>
                </div>
                <Inspector
                  key={`${ledger}:${taskId}:${selected || ""}`}
                  node={selectedNode}
                  ledger={ledger}
                  taskGoal={
                    tasks.find((t) => t.id === selectedNode?.taskId)?.goal ||
                    focusedTask?.goal
                  }
                  replay={!!replay}
                />
              </div>
              {(data.warnings || []).map((w) => (
                <p className="notice" key={w}>
                  {w}
                </p>
              ))}
            </>
          )}
          <footer className="workspace-footer">
            {openCode
              ? "실행 중은 OpenCode 상태 API의 busy입니다. 세션 대기와 응답 종료, 작업 검증 완료는 서로 다릅니다."
              : "실행 중은 컨트롤러 프로세스의 생존 확인을 뜻합니다. 모델 내부 활동은 기록된 범위만 표시합니다."}
            <span>React · TypeScript · React Three Fiber</span>
          </footer>
        </div>
      </main>
    </div>
  );
}
