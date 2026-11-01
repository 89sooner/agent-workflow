import assert from "node:assert/strict";
import { build } from "esbuild";
const out = await build({
  entryPoints: ["frontend/mission/model.ts"],
  bundle: true,
  write: false,
  platform: "node",
  format: "esm",
});
const { atEvent, graphOf, newTransfers, assertTrace } = await import(
  "data:text/javascript;base64," +
    Buffer.from(out.outputFiles[0].text).toString("base64")
);
const task = { id: "T", goal: "truth", status: "completed" };
const span = {
  id: "s",
  task_id: "T",
  run_id: "r",
  actor: "agent:s",
  role: "implement",
  label: "구현",
  status: "succeeded",
  sequence: 1,
  started_at: "2026-11-01T01:00:00Z",
  ended_at: "2026-11-01T01:00:01Z",
  result: { summary: "future" },
};
const events = [
  {
    seq: 1,
    at: span.started_at,
    span_id: "s",
    kind: "process_started",
    actor: "controller",
    recipient: "agent:s",
    status: "running",
  },
  {
    seq: 2,
    at: span.ended_at,
    span_id: "s",
    kind: "process_finished",
    actor: "agent:s",
    recipient: "controller",
    status: "succeeded",
  },
  {
    seq: 3,
    at: span.ended_at,
    span_id: "s",
    kind: "implementation_returned",
    actor: "agent:s",
    recipient: "controller",
    data: { summary: "observed" },
  },
];
const trace = {
  schema_version: 1,
  generated_at: "",
  spans: [span],
  actors: [
    {
      id: "agent:s",
      label: "지우",
      kind: "agent",
      task_id: "T",
      role: "implement",
      status: "succeeded",
    },
    {
      id: "agent:future",
      label: "미래",
      kind: "agent",
      span_id: "future",
      task_id: "T",
      status: "succeeded",
    },
  ],
  events,
  edges: [],
  runs: [],
};
assertTrace(trace);
assert.throws(() => assertTrace({ ...trace, schema_version: 5 }));
assert.equal(atEvent(trace, 0).spans.length, 0);
assert.equal(atEvent(trace, 1).spans[0].status, "running");
assert.equal(atEvent(trace, 1).spans[0].ended_at, null);
assert.equal(
  atEvent(trace, 2).spans[0].result,
  undefined,
  "process exit must not leak later verdict",
);
assert.deepEqual(atEvent(trace, 3).spans[0].result, { summary: "observed" });
assert.equal(
  atEvent(trace, 1).actors.length,
  1,
  "future agents must stay hidden",
);
assert.equal(
  newTransfers(events, null, 100).transfers.length,
  0,
  "initial snapshot is not new activity",
);
assert.equal(newTransfers(events, 1, 100).transfers.length, 1);
assert.equal(
  newTransfers(events, 3, 100).transfers.length,
  0,
  "polling must not repeat a transfer",
);
const spans = Array.from({ length: 12 }, (_, i) => ({
  ...span,
  id: "s" + i,
  sequence: i,
  status: i === 0 ? "running" : "succeeded",
}));
const bounded = graphOf({ ...trace, spans }, [task], "T", "s1");
assert.equal(bounded.nodes.length, 8);
assert(
  bounded.nodes.some((n) => n.id === "s0"),
  "old running stage retained",
);
assert(
  bounded.nodes.some((n) => n.id === "s1"),
  "selected old stage retained",
);
assert.equal(bounded.total, 12);
assert.equal(graphOf(trace, [task], "T").nodes[0].label, "지우");
assert.equal(
  graphOf(
    { ...trace, edges: [{ id: "unknown", source: "s", target: "absent" }] },
    [task],
  ).edges.length,
  0,
);
assert.equal(
  atEvent({ ...trace, events: [events[1]] }, 2).spans[0].result,
  undefined,
  "truncated history cannot infer a verdict",
);
console.log(
  "Mission model: replay chronology, identity, actual transfers and bounded selection passed.",
);
