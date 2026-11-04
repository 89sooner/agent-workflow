import { useEffect, useState } from "react";
import { get, usePoll } from "./api";
import { ConnectionDialog } from "./Connections";
import { Room } from "./Room";
import type { Capabilities, Node } from "./types";

type Session = {
  id: string;
  provider: string;
  native_id: string;
  project: string;
  name: string;
  state: string;
  observed_state: string;
  last_seen: number;
  tool: string;
  parent: string;
  model: string;
  fresh: boolean;
};
type Snapshot = {
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
const needsAttention = (s: Session) =>
  ["input", "approval", "attention"].includes(s.state);
const time = (t: number) => new Date(t * 1000).toLocaleString();
export default function NativeSessions() {
  const initial = new URLSearchParams(location.search);
  const [selected, setSelected] = useState(initial.get("session") || "");
  const [search, setSearch] = useState(initial.get("q") || "");
  const [provider, setProvider] = useState(initial.get("provider") || "");
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
  const snapshot = usePoll<Snapshot>(
    "/api/native/sessions?" + new URLSearchParams({ session: selected }),
    4000,
    refresh,
  );
  const capabilities = usePoll<Capabilities>(
    "/api/capabilities",
    60000,
    refresh,
  );
  useEffect(() => {
    const query = new URLSearchParams();
    if (selected) query.set("session", selected);
    if (search) query.set("q", search);
    if (provider) query.set("provider", provider);
    history.replaceState(
      null,
      "",
      "/sessions" + (query.size ? "?" + query : ""),
    );
  }, [selected, search, provider]);
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
  const sessions = (snapshot.data?.sessions || []).map((s) =>
    snapshot.error || !snapshot.fresh
      ? { ...s, state: s.state === "ended" ? "ended" : "unknown", fresh: false }
      : s,
  );
  const filtered = sessions.filter(
    (s) =>
      (!provider || s.provider === provider) &&
      (ended || s.state !== "ended") &&
      (!inbox || needsAttention(s)) &&
      (s.name + " " + s.project + " " + s.native_id)
        .toLowerCase()
        .includes(search.toLowerCase()),
  );
  const chosen = sessions.find((s) => s.id === selected);
  const nodes: Node[] = filtered
    .filter((s) => s.state !== "ended")
    .map((s) => ({
      id: s.id,
      actorId: s.id,
      taskId: s.id,
      label: s.name,
      role: s.provider,
      kind: "agent",
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
          확인 요청함 ({sessions.filter(needsAttention).length})
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
              <p>네이티브 세션: {chosen.native_id}</p>
              <p>프로젝트: {chosen.project}</p>
              <p>
                도구: {chosen.tool || "미기록"} · 모델:{" "}
                {chosen.model || "미기록"}
              </p>
              {chosen.parent && <p>부모 세션: {chosen.parent}</p>}
              <p>
                현재 주소를 복사하면 이 세션으로 다시 돌아올 수 있습니다.
                승인·입력은 원래 도구에서 처리하세요.
              </p>
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
