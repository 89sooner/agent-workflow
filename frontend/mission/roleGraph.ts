import type { Edge, Graph, Node, Span, Trace } from "./types";

interface RoleCount {
  calls: number;
  succeeded: number;
  attention: number;
}

export function executionStatus(
  span: Span | undefined,
  fallback: string,
): string {
  if (
    !span ||
    ["running", "failed", "interrupted", "unknown"].includes(span.status)
  )
    return fallback;
  if (!span.result || typeof span.result !== "object") return fallback;
  const result = span.result as {
    passed?: boolean;
    outcome?: string;
    verdict?: string;
  };
  if (
    result.outcome === "changes_requested" ||
    result.verdict === "changes_requested"
  )
    return "changes_requested";
  if (result.outcome === "blocked" || result.passed === false) return "blocked";
  if (result.verdict === "pass" || result.passed === true) return "succeeded";
  return fallback;
}

/** Collapse recorded spans within each visible task, before the step view's per-row display cap. */
export function roleGraph(graph: Graph, trace: Trace, selected: string | null) {
  const nodes: Node[] = [];
  const countByNode = new Map<string, RoleCount>();
  const representative = new Map<string, string>();
  const actors = new Map(trace.actors.map((actor) => [actor.id, actor]));
  let selectedRole = selected;
  graph.tasks.forEach((task, row) => {
    const grouped = new Map<string, Span[]>();
    const taskSpans = trace.spans
      .filter((s) => s.task_id === task.id)
      .sort((a, b) => a.sequence - b.sequence);
    for (const span of taskSpans) {
      if (!grouped.has(span.role)) grouped.set(span.role, []);
      grouped.get(span.role)!.push(span);
    }
    if (!taskSpans.length) {
      // A pre-execution assignment has an actor, but no observed call.
      nodes.push(...graph.nodes.filter((node) => node.taskId === task.id));
    }
    let col = 0;
    for (const [role, spans] of grouped) {
      const latest = spans[spans.length - 1];
      const actor = actors.get(latest.actor);
      const count = { calls: spans.length, succeeded: 0, attention: 0 };
      for (const span of spans) {
        const status = executionStatus(span, span.status);
        if (["succeeded", "pass"].includes(status)) count.succeeded++;
        if (
          [
            "blocked",
            "changes_requested",
            "interrupted",
            "failed",
            "unknown",
          ].includes(status)
        )
          count.attention++;
        representative.set(span.id, latest.id);
        if (span.id === selected) selectedRole = latest.id;
      }
      countByNode.set(latest.id, count);
      nodes.push({
        id: latest.id,
        taskId: task.id,
        actorId: latest.actor,
        label: actor?.label || latest.label,
        role,
        status: executionStatus(latest, latest.status),
        kind: actor?.kind || "service",
        actor,
        span: latest,
        x: 190 + 226 * col++,
        y: 76 + 148 * row,
      });
    }
  });

  const edges: Edge[] = [];
  const edgeCounts = new Map<string, number>();
  for (const edge of trace.edges) {
    const from = representative.get(edge.source),
      to = representative.get(edge.target);
    if (!from || !to) continue;
    const id = JSON.stringify([from, to]);
    if (!edgeCounts.has(id))
      edges.push({ ...edge, id, source: from, target: to });
    edgeCounts.set(id, (edgeCounts.get(id) || 0) + 1);
  }
  return {
    graph: {
      ...graph,
      nodes,
      edges,
      width: Math.max(860, ...nodes.map((n) => n.x + 220)),
    },
    selected: selectedRole,
    countByNode,
    edgeCounts,
  };
}
