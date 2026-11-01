import type { Task, Trace, Span, Graph, Node, Event } from "./types";
export const labels: Record<string, string> = {
  running: "실행 중",
  unknown: "상태 미확인",
  succeeded: "완료",
  failed: "실패",
  blocked: "확인 필요",
  held: "보류",
  idle: "대기",
  assigned: "배정됨",
  queued: "실행 대기",
  interrupted: "중단됨",
  cancelled: "취소됨",
  completed: "로컬 완료",
  ready: "준비됨",
  implementing: "구현 중",
  checking: "검증 중",
  reviewing: "리뷰 중",
  needs_review: "리뷰 대기",
  ready_to_finish: "마감 대기",
  implement: "구현",
  check: "검사",
  review: "독립 리뷰",
  controller: "조정",
  release: "통합",
  deploy: "배포",
  health: "상태 확인",
  integration_check: "통합 검사",
  setup: "준비",
  pending: "시작 전",
};
export const label = (s: string) => labels[s] || s;
export const color = (s: string) =>
  s === "running"
    ? "#66e6bb"
    : s === "failed" || s === "blocked"
      ? "#ff8e88"
      : s === "unknown" || s === "interrupted"
        ? "#edc875"
        : s === "succeeded" || s === "completed"
          ? "#8ca7ff"
          : "#7d8b9e";
export const clock = (s?: string | null) =>
  s
    ? new Date(s).toLocaleTimeString("ko-KR", { hour12: false })
    : "시간 미기록";
export const when = (s?: string | null) =>
  s
    ? new Date(s).toLocaleString("ko-KR", {
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
        hourCycle: "h23",
      })
    : "시간 미기록";
export const pathName = (s: string) =>
  s.split(/[\\/]/).filter(Boolean).at(-1) || s;
export function assertTrace(value: unknown): asserts value is Trace {
  const v = value as Trace;
  if (
    !v ||
    v.schema_version !== 1 ||
    !Array.isArray(v.spans) ||
    !Array.isArray(v.actors) ||
    !Array.isArray(v.events) ||
    !Array.isArray(v.edges) ||
    !Array.isArray(v.runs)
  )
    throw Error("지원되지 않는 관측 응답입니다.");
  if (
    v.spans.some(
      (s) =>
        typeof s.id !== "string" ||
        typeof s.actor !== "string" ||
        typeof s.status !== "string" ||
        !Number.isFinite(s.sequence),
    ) ||
    v.actors.some(
      (a) => typeof a.id !== "string" || typeof a.label !== "string",
    ) ||
    v.events.some((e) => !Number.isFinite(e.seq) || typeof e.at !== "string")
  )
    throw Error("관측 기록의 필수 필드가 올바르지 않습니다.");
}
export function atEvent(trace: Trace, seq: number | null): Trace {
  if (seq === null) return trace;
  const events = trace.events.filter((e) => e.seq <= seq),
    last = events.at(-1);
  if (!last)
    return {
      ...trace,
      spans: [],
      edges: [],
      events: [],
      actors: [],
    };
  const timestamp = Date.parse(last.at);
  const spans = trace.spans
    .filter((s) => Date.parse(s.started_at) <= timestamp)
    .map((s): Span | null => {
      const started = events.some(
        (e) => e.span_id === s.id && e.kind === "process_started",
      );
      const finished = events.findLast(
        (e) => e.span_id === s.id && e.kind === "process_finished",
      );
      const hasStart = trace.events.some(
        (e) => e.span_id === s.id && e.kind === "process_started",
      );
      if (hasStart && !started) return null;
      const ended = s.ended_at && Date.parse(s.ended_at) <= timestamp;
      // Process exit precedes the structured verdict. Never reveal a later result.
      const resultEvent = events.findLast(
        (e) =>
          e.span_id === s.id &&
          [
            "implementation_returned",
            "review_finished",
            "checks_finished",
            "agent_failed",
          ].includes(e.kind),
      );
      return {
        ...s,
        status:
          finished?.status ||
          (hasStart ? "running" : ended ? s.status : "running"),
        result: resultEvent?.data,
        error:
          resultEvent?.kind === "agent_failed"
            ? resultEvent.summary
            : undefined,
        ended_at: finished || (!hasStart && ended) ? s.ended_at : null,
        duration_ms: finished || (!hasStart && ended) ? s.duration_ms : null,
      };
    })
    .filter((s): s is Span => s !== null);
  const ids = new Set(spans.map((s) => s.id));
  return {
    ...trace,
    events,
    spans,
    edges: trace.edges.filter((e) => ids.has(e.source) && ids.has(e.target)),
    actors: trace.actors
      .filter(
        (a) =>
          spans.some((s) => s.actor === a.id) ||
          events.some((e) => e.actor === a.id || e.recipient === a.id),
      )
      .map((a) => ({
        ...a,
        status: spans.findLast((s) => s.actor === a.id)?.status || "pending",
      })),
  };
}
export function graphOf(
  trace: Trace,
  tasks: Task[],
  focusTask?: string,
  selected?: string | null,
): Graph {
  const ranked = [...tasks]
    .sort(
      (a, b) =>
        Number(b.id === focusTask) - Number(a.id === focusTask) ||
        Number(
          trace.spans.some((s) => s.task_id === b.id && s.status === "running"),
        ) -
          Number(
            trace.spans.some(
              (s) => s.task_id === a.id && s.status === "running",
            ),
          ) ||
        String(b.updated_at || "").localeCompare(String(a.updated_at || "")) ||
        a.id.localeCompare(b.id),
    )
    .slice(0, focusTask ? 1 : 6);
  const nodes: Node[] = [];
  let total = 0;
  ranked.forEach((task, row) => {
    const all = trace.spans
      .filter((s) => s.task_id === task.id)
      .sort((a, b) => a.sequence - b.sequence);
    total += all.length;
    const active = [
      ...all.filter((s) => s.id === selected),
      ...all.filter((s) => s.status === "running" && s.id !== selected),
    ].slice(0, 8);
    const visible =
      all.length <= 8
        ? all
        : [
            ...active,
            ...all
              .filter((s) => !active.includes(s))
              .slice(-(8 - active.length) || all.length),
          ]
            .slice(0, 8)
            .sort((a, b) => a.sequence - b.sequence);
    visible.forEach((s, col) => {
      const actor = trace.actors.find((a) => a.id === s.actor);
      nodes.push({
        id: s.id,
        taskId: task.id,
        actorId: s.actor,
        label: actor?.label || s.label,
        role: s.role,
        status: s.status,
        kind: actor?.kind || "service",
        span: s,
        actor,
        x: 190 + col * 226,
        y: 76 + row * 148,
      });
    });
    if (!all.length) {
      const assigned = trace.actors.filter(
        (a) =>
          a.task_id === task.id &&
          (a.kind === "assignment" || a.kind === "agent"),
      );
      total += assigned.length;
      assigned.slice(0, 8).forEach((a, col) =>
        nodes.push({
          id: a.id,
          taskId: task.id,
          actorId: a.id,
          label: a.label,
          role: a.role,
          status: a.status,
          kind: a.kind,
          actor: a,
          x: 190 + col * 226,
          y: 76 + row * 148,
        }),
      );
    }
  });
  const ids = new Set(nodes.map((n) => n.id));
  return {
    nodes,
    edges: trace.edges.filter((e) => ids.has(e.source) && ids.has(e.target)),
    tasks: ranked,
    total,
    width: Math.max(860, ...nodes.map((n) => n.x + 220)),
    height: Math.max(230, ranked.length * 148 + 30),
  };
}
export function newTransfers(
  events: Event[],
  previous: number | null,
  now: number,
) {
  const last = Math.max(previous || 0, 0, ...events.map((e) => e.seq));
  return {
    last,
    transfers:
      previous === null
        ? []
        : events
            .filter(
              (e) =>
                e.seq > previous &&
                e.actor &&
                e.recipient &&
                e.actor !== e.recipient &&
                ["process_started", "process_finished"].includes(e.kind),
            )
            .slice(-12)
            .map((e) => ({
              seq: e.seq,
              source: e.actor,
              target: e.recipient!,
              at: now,
              kind: e.kind,
            })),
  };
}
