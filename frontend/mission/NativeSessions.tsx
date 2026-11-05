import { SessionMetrics } from "./SessionMetrics";
import { SessionBookmarks } from "./SessionBookmarks";
import { SessionEvidence } from "./SessionEvidence";
import { activityLabel } from "./activity";
import { SessionHierarchy } from "./SessionHierarchy";
import { SessionReturn } from "./SessionReturn";
import { SessionNotifications } from "./SessionNotifications";
import { attentionFirst, isAttention } from "./attention";
import { ConnectionDiagnostics } from "./ConnectionDiagnostics";
import { useEffect, useState, useMemo } from "react";
import { get, usePoll } from "./api";
import { ConnectionDialog } from "./Connections";
import { Room } from "./Room";
import type { Capabilities, Node } from "./types";

type Session = {
  id: string;
  provider: string;
  source: string;
  native_id: string;
  project: string;
  project_id?: string;
  name: string;
  state: string;
  observed_state: string;
  activity?: string;
  last_seen: number;
  tool: string;
  parent: string;
  model: string;
  fresh: boolean;
  state_since?: number | null;
  waiting_since?: number | null;
  freshness_reason?: string;
};
type Snapshot = {
  observed_at?: number;
  sessions: Session[];
  history: {
    seq: number;
    at: number;
    event: string;
    state: string;
    tool: string;
  }[];
  errors: { source: string; error: string }[];
  sources: { project: string }[];
};
const states: Record<string, string> = {
  running: "작업 중",
  input: "입력 대기",
  approval: "승인 대기",
  idle: "유휴",
  ended: "종료",
  unknown: "관측 미확인",
  attention: "확인 필요",
  retrying: "재시도 대기",
};
const needsAttention = isAttention;
const time = (t: number) => new Date(t * 1000).toLocaleString();
export default function NativeSessions() {
  const initial = new URLSearchParams(location.search);
  const [initialBookmark] = useState({session: initial.get("session"), id: initial.get("bookmark")});
  const [selected, setSelected] = useState(initial.get("session") || "");
  const [search, setSearch] = useState(initial.get("q") || "");
  const [provider, setProvider] = useState(initial.get("provider") || "");
  const [projectFilter, setProjectFilter] = useState(initial.get("project") || "");
  const [ended, setEnded] = useState(false),
    [inbox, setInbox] = useState(false);
  const [refresh, setRefresh] = useState(0),
    [connecting, setConnecting] = useState(false);
  const [setupProvider, setSetupProvider] = useState("claude");
  const [project, setProject] = useState(""),
    [setup, setSetup] = useState<{
      command: string;
      cwd: string;
      requested: string;
      provider: string;
    } | null>(null),
    [setupError, setSetupError] = useState("");
  const [reduce, setReduce] = useState(false),
    [systemReduce, setSystemReduce] = useState(false),
    [visible, setVisible] = useState(!document.hidden);
  const [notificationPolling, setNotificationPolling] = useState(false);
  const snapshot = usePoll<Snapshot>(
    "/api/native/sessions?" + new URLSearchParams({ session: selected }),
    4000,
    refresh,
    undefined,
    notificationPolling,
  );
  const capabilities = usePoll<Capabilities>(
    "/api/capabilities",
    60000,
    refresh,
  );
  useEffect(() => {
    const query = new URLSearchParams();
    if (selected) query.set("session", selected);
    if (selected === initialBookmark.session && initialBookmark.id) query.set("bookmark", initialBookmark.id);
    if (search) query.set("q", search);
    if (provider) query.set("provider", provider);
    if (projectFilter) query.set("project", projectFilter);
    history.replaceState(
      null,
      "",
      "/sessions" + (query.size ? "?" + query : ""),
    );
  }, [selected, search, provider, projectFilter, initialBookmark]);
  useEffect(() => {
    const media = matchMedia("(prefers-reduced-motion: reduce)");
    const change = () => {
      setSystemReduce(media.matches);
      setVisible(!document.hidden);
    };
    change();
    media.addEventListener("change", change);
    document.addEventListener("visibilitychange", change);
    return () => {
      media.removeEventListener("change", change);
      document.removeEventListener("visibilitychange", change);
    };
  }, []);
  const sessions = useMemo(() => (snapshot.data?.sessions || []).map((s) =>
    snapshot.error || !snapshot.fresh
      ? { ...s, state: s.state === "ended" ? "ended" : "unknown", fresh: false }
      : s,
  ), [snapshot.data, snapshot.error, snapshot.fresh]);
  const scoped = sessions.filter((s) =>
    (!provider || s.provider === provider) &&
    (!projectFilter || (s.project_id || s.project) === projectFilter) &&
    (s.name + " " + s.project + " " + s.native_id).toLowerCase().includes(search.toLowerCase()),
  );
  const filtered = attentionFirst(scoped).filter((s) =>
    (ended || s.state !== "ended") && (!inbox || needsAttention(s)),
  );
  const projects = [...new Map(sessions.map(s=>[s.project_id || s.project, s.project])).entries()];
  const summary = {working:filtered.filter(s=>s.state==="running").length, waiting:filtered.filter(s=>s.state==="idle").length, attention:filtered.filter(needsAttention).length, unknown:filtered.filter(s=>s.state==="unknown").length, ended:filtered.filter(s=>s.state==="ended").length, retrying:filtered.filter(s=>s.state==="retrying").length};
  const chosen = sessions.find((s) => s.id === selected && (!projectFilter || (s.project_id || s.project) === projectFilter));
  const nodes: Node[] = filtered
    .map((s) => ({
      id: s.id,
      actorId: s.id,
      taskId: s.id,
      label: s.name,
      role: s.provider,
      kind: "agent",
      activity: s.fresh ? s.activity : "unknown",
      status:
        s.state === "input"
          ? "idle"
          : s.state === "approval"
            ? "blocked"
            : s.state === "attention"
              ? "blocked"
              : s.state,
      x: 0,
      y: 0,
    }));
  async function prepare() {
    setSetup(null);
    setSetupError("");
    try {
      const result = await get<{ command: string; cwd: string }>(
        "/api/native/setup?" + new URLSearchParams({ project, provider: setupProvider }),
      );
      setSetup({ ...result, requested: project, provider: setupProvider });
    } catch (e) {
      setSetupError(String(e));
    }
  }
  return (
    <main className="native-page">
      <header className="panel native-heading">
        <div>
          <span className="eyebrow">DAILY SESSIONS</span>
          <h1>일상 세션 관측</h1>
          <p>
            WP 등록 없이 OpenCode·Claude Code·Codex 활동을 함께 봅니다. 관측은 작업
            실행·승인·큐 등록을 하지 않습니다.
          </p>
        </div>
        <a href="/mission">관리형 작업 관제실</a>
      </header>
      <section className="panel native-controls">
        <button
          onClick={() => setConnecting(true)}
          disabled={!capabilities.data}
        >
          OpenCode 연결
        </button>
        <button onClick={() => setRefresh((v) => v + 1)}>새로고침</button>
        <label>
          도구{" "}
          <select
            aria-label="도구 필터"
            value={provider}
            onChange={(e) => setProvider(e.target.value)}
          >
            <option value="">전체 도구</option>
            <option value="claude">Claude Code</option>
            <option value="codex">Codex</option>
            <option value="opencode">OpenCode</option>
          </select>
        </label>
        <label>
          검색{" "}
          <input
            aria-label="세션·프로젝트 검색"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="세션·프로젝트·ID"
          />
        </label>
        <label>
          <input
            type="checkbox"
            checked={ended}
            onChange={(e) => setEnded(e.target.checked)}
          />
          종료 이력 포함
        </label>
        <label>
          <input
            type="checkbox"
            checked={inbox}
            onChange={(e) => setInbox(e.target.checked)}
          />
          확인 요청함 ({scoped.filter(needsAttention).length})
        </label>
        <label>
          <input
            type="checkbox"
            checked={reduce || systemReduce}
            disabled={systemReduce}
            onChange={(e) => setReduce(e.target.checked)}
          />
          움직임 줄이기
        </label>
      </section>
    <ConnectionDiagnostics />
      <SessionNotifications sessions={sessions} fresh={snapshot.fresh && !snapshot.error} onEnabledChange={setNotificationPolling} observedAt={snapshot.data?.observed_at} />
      <details className="panel native-setup">
        <summary>Claude Code·Codex 처음 연결하기</summary>
        <label>연결 도구 <select aria-label="관측 훅 도구" value={setupProvider} onChange={e => {setSetupProvider(e.target.value); setSetup(null);}}>
          <option value="claude">Claude Code</option><option value="codex">Codex</option>
        </select></label>
        <p>
          프로젝트별 관측 훅을 한 번 설치하면 이후 새 세션을 자동 발견합니다.
          기존 훅은 보존합니다. 아래 명령을 해당 관제실 서버의 터미널에서 실행한
          뒤 설정을 재로딩하거나 새 세션을 시작하세요. Codex는 프로젝트 신뢰와 /hooks에서 훅 검토가 필요합니다.
        </p>
        <label>
          프로젝트 절대 경로{" "}
          <input
            aria-label={setupProvider === "codex" ? "Codex 프로젝트 경로" : "Claude 프로젝트 경로"}
            value={project}
            onChange={(e) => {
              setProject(e.target.value);
              setSetup(null);
            }}
          />
        </label>
        <button onClick={() => void prepare()}>설치 명령 준비</button>
        {setupError && <p role="alert">{setupError}</p>}
        {setup && setup.requested === project && setup.provider === setupProvider && (
          <div>
            <p>실행 위치: {setup.cwd}</p>
            <pre>{setup.command}</pre>
            <button
              onClick={() => {
                void navigator.clipboard
                  .writeText(setup.command)
                  .catch(() =>
                    setSetupError(
                      "복사 권한이 없습니다. 위 명령을 선택해 복사하세요.",
                    ),
                  );
              }}
            >
              명령 복사
            </button>
            <p>
              {setupProvider === "codex" ? ".codex/hooks.json" : ".claude/settings.local.json"}에 관측 훅을 추가하고 백업합니다.
              모델을 실행하지 않습니다.
            </p>
          </div>
        )}
      </details>
      {snapshot.error && (
        <p role="alert">
          연결을 확인하세요. 저장된 마지막 기록을 표시합니다: {snapshot.error}
        </p>
      )}
      {snapshot.data?.errors.map((e) => (
        <p className="notice" key={e.source + e.error}>
          {e.source}: {e.error} · 이전 기록은 보존됩니다.
        </p>
      ))}
      <p className="panel-caption">
        Claude·Codex 및 OpenCode 플러그인은 마지막 이벤트 이후 5분이 지나면 현재 상태를 미확인으로 표시합니다.
        OpenCode 유휴는 세션 생존을 보장하지 않습니다. 응답 종료와 관리형 작업의
        검증 완료는 다릅니다.
      </p>
      {!filtered.length && (
        <section className="panel native-empty">
          <h2>
            {sessions.length
              ? "조건에 맞는 세션 없음"
              : "아직 관측된 세션이 없습니다"}
          </h2>
          <p>
            {sessions.length
              ? "검색·도구·확인 요청함 필터를 확인하세요."
              : "OpenCode를 연결하거나 Claude·Codex 관측 훅을 설치한 다음 평소처럼 작업하세요."}
          </p>
          <button
            onClick={() => {
              setSearch("");
              setProvider("");
              setInbox(false);
              setEnded(true);
            }}
          >
            필터 초기화·이력 보기
          </button>
        </section>
      )}
      <SessionHierarchy sessions={filtered} selected={selected} onSelect={setSelected} />
      <section className="panel" aria-label="프로젝트 공간 요약">
        <label>프로젝트 공간 <select aria-label="일상 세션 프로젝트" value={projectFilter} onChange={e=>{setProjectFilter(e.target.value);setSelected("")}}>
          <option value="">전체 프로젝트</option>{projects.map(([id,path])=><option key={id} value={id}>{path}</option>)}
        </select></label>
        <p>필터 세션 {filtered.length} · 작업 중 {summary.working} · 유휴 {summary.waiting} · 확인 요청 {summary.attention} · 미확인 {summary.unknown} · 재시도 {summary.retrying} · 종료 {summary.ended}</p>
        <p>목록과 3D 공간은 같은 프로젝트·도구·검색 조건을 사용합니다. 3D 표시 한도를 넘는 세션도 목록에서 선택할 수 있습니다.</p>
      </section>
      <SessionMetrics project={projectFilter} provider={provider} />
      <div className="native-columns">
        <section className="panel native-list" aria-label="일상 세션 목록">
          {filtered.map((s) => (
            <button
              key={s.id}
              className="native-session"
              aria-pressed={selected === s.id}
              onClick={() => setSelected(s.id)}
            >
              <strong>{s.name}</strong>
              <span>
                {s.provider} · {states[s.state] || s.state}
              </span>
              <small>{s.project}</small>
              <small>마지막 관측 {time(s.last_seen)}</small>
              {needsAttention(s) && <small>{s.waiting_since == null ? "대기 시작 시각 미기록" : "대기 시작 " + time(s.waiting_since)}</small>}
            </button>
          ))}
        </section>
        <div>
          <Room
            nodes={nodes}
            selected={selected}
            onSelect={(n) => setSelected(n.id)}
            motion={
              !reduce &&
              !systemReduce &&
              visible &&
              snapshot.fresh &&
              !snapshot.error
            }
            transfers={[]}
            visible={visible}
          />
          {selected && !chosen && !snapshot.loading && (
            <p className="notice">
              선택한 세션이 최근 보관 범위에 없습니다. 목록에서 다른 세션을
              선택하세요.
            </p>
          )}
          {chosen && (
            <section className="panel native-detail">
              <h2>{chosen.name}</h2>
              <p>
                {states[chosen.state]} · 마지막 확인 상태:{" "}
                {states[chosen.observed_state]}
              </p>
              <p>마지막 수신: {time(chosen.last_seen)} · {
                !snapshot.fresh ? "화면 데이터를 다시 확인해야 합니다." :
                ({ observed: "최근 관측 기록", event_expired: "마지막 이벤트 이후 5분 경과",
                   not_registered: "프로젝트 관측 등록 없음", source_unavailable: "소스 조회 미확인" })[chosen.freshness_reason || ""] || "관측 근거 미확인"
              }</p>
              {chosen.fresh && chosen.waiting_since != null && <p>대기 시작: {time(chosen.waiting_since)}</p>}
              <p>관측 활동: {activityLabel(chosen.fresh ? chosen.activity : "unknown")}</p>
              <p>네이티브 세션: {chosen.native_id}</p>
              <p>프로젝트: {chosen.project}</p>
              <p>
                도구: {chosen.tool || "미기록"} · 모델:{" "}
                {chosen.model || "미기록"}
              </p>
              {chosen.parent && <p>부모 세션: {chosen.parent}</p>}
              <SessionReturn key={chosen.id} project={chosen.project} nativeId={chosen.native_id} provider={chosen.provider} />
              <p>
                현재 주소를 복사하면 이 세션으로 다시 돌아올 수 있습니다.
                승인·입력은 원래 도구에서 처리하세요.
              </p>
              <SessionEvidence key={chosen.id+":evidence"} session={chosen.id} capabilities={capabilities.data} />
              <SessionBookmarks key={chosen.id+":bookmarks"} initialBookmark={chosen.id === initialBookmark.session ? initialBookmark.id || "" : ""} session={chosen.id} history={snapshot.data?.history||[]} capabilities={capabilities.data} />
              <h3>관측 이력</h3>
              <ol className="native-history">
                {snapshot.data?.history.map((e) => (
                  <li key={e.seq}>
                    {time(e.at)} · {e.event} · {states[e.state] || e.state}{" "}
                    {e.tool}
                  </li>
                ))}
              </ol>
            </section>
          )}
        </div>
      </div>
      <p className="panel-caption">
        최근 세션 500개·상태 이벤트 10,000개·선택 이력 200개까지 보관합니다.
        OpenCode HTTP 연결은 화면을 보는 동안 조회하며, 플러그인은 도구 실행 중
        이벤트를 기록합니다. 중간 상태가 누락될 수 있고 세션 제목에도 사용자
        정보가 포함될 수 있습니다.
      </p>
      {connecting && capabilities.data && (
        <ConnectionDialog
          capabilities={capabilities.data}
          initialProject=""
          onClose={() => setConnecting(false)}
          onConnected={() => {
            setConnecting(false);
            setRefresh((v) => v + 1);
          }}
        />
      )}
    </main>
  );
}
