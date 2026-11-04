export interface Task {
  id: string;
  goal: string;
  status: string;
  project?: string;
  owner?: string;
  reviewer?: string;
  hold?: unknown;
  updated_at?: string;
}
export interface Actor {
  id: string;
  label: string;
  role: string;
  kind: string;
  status: string;
  task_id: string;
  span_id?: string | null;
  name_source?: string;
  provider?: string | null;
  model?: string | null;
  observed_model?: string | null;
  native_session_id?: string | null;
}
export interface Span {
  id: string;
  task_id: string;
  run_id: string;
  actor: string;
  role: string;
  label: string;
  status: string;
  sequence: number;
  previous_span_id?: string | null;
  started_at: string;
  ended_at?: string | null;
  duration_ms?: number | null;
  input?: unknown;
  result?: unknown;
  error?: unknown;
  model?: string | null;
  artifacts?: string[];
}
export interface Event {
  seq: number;
  task_id?: string;
  run_id?: string | null;
  span_id?: string | null;
  at: string;
  kind: string;
  actor: string;
  recipient?: string | null;
  summary: string;
  status?: string | null;
  data?: unknown;
}
export interface Edge {
  id: string;
  source: string;
  target: string;
  label: string;
  kind: string;
}
export interface Run {
  id: string;
  task_id?: string;
  action: string;
  status: string;
  started_at: string;
  ended_at?: string | null;
  span_count?: number;
}
export interface Execution extends Run {
  source?: string;
  run_id?: string;
  job_id?: string;
}
export interface Trace {
  source?: string;
  schema_version: number;
  generated_at: string;
  actors: Actor[];
  spans: Span[];
  edges: Edge[];
  events: Event[];
  runs: Run[];
  warnings?: string[];
  task?: Task;
  selected_run_id?: string | null;
  pagination?: { has_more: boolean; next_before: number | null };
  runs_truncated?: boolean;
  legacy?: boolean;
}
export interface Project extends Trace {
  tasks: Task[];
  executions: Execution[];
  counts: Record<string, number>;
  truncated?: Record<string, boolean>;
}
export interface Ledger {
  kind?: string;
  label?: string;
  error?: string;
  id: string;
  home: string;
  status: string;
  task_count: number;
  matching_task_count?: number;
  last_success_at?: string | null;
  projects: { id: string; path: string }[];
}
export interface Capabilities {
  can_configure_connections: boolean;
  can_manage_tasks: boolean;
  primary_ledger_id: string | null;
  csrf_token: string;
  mode: "observer" | "read_only" | "manage";
  tools: { id: string; label: string; support: string; description: string }[];
}
export interface Discovery {
  ledgers: Ledger[];
  warnings: unknown[];
}
export interface Node {
  id: string;
  taskId: string;
  actorId: string;
  label: string;
  role: string;
  status: string;
  kind: string;
  span?: Span;
  actor?: Actor;
  x: number;
  y: number;
}
export interface Graph {
  nodes: Node[];
  edges: Edge[];
  width: number;
  height: number;
  total: number;
  tasks: Task[];
}
export interface Transfer {
  seq: number;
  source: string;
  target: string;
  at: number;
  kind: string;
}
