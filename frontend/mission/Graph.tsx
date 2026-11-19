import { useId, useMemo, useState } from "react";
import type { Graph as Model, Node, Trace, Transfer } from "./types";
import { color, label } from "./model";
import { executionStatus, roleGraph } from "./roleGraph";
import "./role-graph.css";

interface Props {
  trace?: Trace;
  graph: Model;
  selected: string | null;
  onSelect: (n: Node) => void;
  transfers: Transfer[];
  motion: boolean;
  replay: boolean;
}
export function ExecutionGraph({
  graph: stepGraph,
  trace,
  selected: selectedSpan,
  onSelect,
  transfers,
  motion,
  replay,
}: Props) {
  const [zoom, setZoom] = useState(1),
    marker = useId().replaceAll(":", "");
  const [roleMode, setRoleMode] = useState(false);
  const projection = useMemo(
    () =>
      roleMode && trace ? roleGraph(stepGraph, trace, selectedSpan) : null,
    [roleMode, trace, stepGraph, selectedSpan],
  );
  const graph = projection?.graph || stepGraph;
  const selected = projection?.selected || selectedSpan;
  const statusText = (status: string) =>
    status === "changes_requested" ? "수정 요청" : label(status);
  const nodesById = new Map(graph.nodes.map((n) => [n.id, n]));
  const active = new Set(transfers.flatMap((t) => [t.source, t.target]));
  return (
    <section className="panel graph-panel" aria-label="실행 흐름 그래프">
      <div className="panel-heading">
        <div>
          <span className="eyebrow">EXECUTION GRAPH</span>
          <h2>작업이 이어지는 흐름</h2>
        </div>
        <div className="graph-tools">
          <button
            onClick={() => setZoom((z) => Math.max(0.55, z - 0.15))}
            aria-label="그래프 축소"
          >
            −
          </button>
          <button onClick={() => setZoom(1)} aria-label="그래프 배율 초기화">
            {Math.round(zoom * 100)}%
          </button>
          <button
            onClick={() => setZoom((z) => Math.min(1.6, z + 0.15))}
            aria-label="그래프 확대"
          >
            +
          </button>
        </div>
      </div>
      {trace && (
        <div
          className="role-graph-mode"
          role="group"
          aria-label="그래프 표시 방식"
        >
          <button aria-pressed={!roleMode} onClick={() => setRoleMode(false)}>
            실행 순서
          </button>
          <button aria-pressed={roleMode} onClick={() => setRoleMode(true)}>
            역할 개요
          </button>
        </div>
      )}
      <p className="panel-caption">
        {projection
          ? "역할별 호출과 연결 횟수입니다. 역할을 선택하면 마지막 실행의 근거를 엽니다."
          : "선은 기록된 실행 순서입니다. 노드를 선택하면 담당자와 결과가 함께 표시됩니다."}
      </p>
      {!graph.nodes.length ? (
        <div className="empty-state">
          <span>◇</span>
          <h3>아직 실행 단계가 없습니다</h3>
          <p>작업을 선택하면 배정 정보와 기록된 이벤트를 확인할 수 있습니다.</p>
        </div>
      ) : (
        <div
          className="graph-viewport"
          tabIndex={0}
          aria-label="가로로 탐색 가능한 실행 그래프"
        >
          <svg
            className="execution-graph"
            width={graph.width * zoom}
            height={graph.height * zoom}
            viewBox={`0 0 ${graph.width} ${graph.height}`}
            role="group"
            aria-label={
              projection ? "작업별 역할 개요" : "작업별 실제 실행 순서"
            }
          >
            <defs>
              <marker
                id={marker}
                viewBox="0 0 8 8"
                refX="7"
                refY="4"
                markerWidth="7"
                markerHeight="7"
                orient="auto"
              >
                <path d="M0 0L8 4L0 8" fill="#67788d" />
              </marker>
              <pattern
                id={marker + "grid"}
                width="24"
                height="24"
                patternUnits="userSpaceOnUse"
              >
                <circle cx="1" cy="1" r="1" fill="#273545" />
              </pattern>
            </defs>
            <rect
              width={graph.width}
              height={graph.height}
              fill={`url(#${marker}grid)`}
            />
            {graph.tasks.map((task, row) => (
              <g key={task.id}>
                <text x="20" y={50 + row * 148} className="lane-id">
                  {task.id.slice(0, 19)}
                </text>
                <text x="20" y={70 + row * 148} className="lane-status">
                  {replay ? "이벤트 시점 재생" : label(task.status)}
                </text>
                <line
                  x1="20"
                  y1={142 + row * 148}
                  x2={graph.width - 20}
                  y2={142 + row * 148}
                  stroke="#263244"
                  strokeDasharray="3 7"
                />
              </g>
            ))}
            {graph.edges.map((edge) => {
              const a = nodesById.get(edge.source)!,
                b = nodesById.get(edge.target)!;
              const reverse = projection && a.x >= b.x && a.y === b.y;
              const path = reverse
                ? `M ${a.x + 120} ${a.y - 38} C ${a.x + 120} ${a.y - 73},${b.x + 60} ${b.y - 73},${b.x + 60} ${b.y - 38}`
                : `M ${a.x + 180} ${a.y} C ${a.x + 205} ${a.y},${b.x - 25} ${b.y},${b.x} ${b.y}`;
              const count = projection?.edgeCounts.get(edge.id);
              return (
                <g key={edge.id}>
                  <title>
                    {edge.label}
                    {count != null && ` · ${count}회`}
                  </title>
                  <path
                    d={path}
                    className="graph-edge"
                    markerEnd={`url(#${marker})`}
                  />
                  {count != null && (
                    <text
                      className="role-edge-count"
                      textAnchor="middle"
                      x={(a.x + b.x) / 2 + 90}
                      y={reverse ? a.y - 65 : (a.y + b.y) / 2 - 5}
                    >
                      {count}회
                    </text>
                  )}
                  {motion && active.has(b.actorId) && (
                    <path d={path} className="graph-edge signal" />
                  )}
                </g>
              );
            })}
            {graph.nodes.map((n) => {
              const count = projection?.countByNode.get(n.id);
              const status = executionStatus(n.span, n.status);
              return (
                <g
                  key={n.id}
                  className={`graph-node ${selected === n.id ? "selected" : ""} ${motion && n.status === "running" ? "working" : ""}`}
                  transform={`translate(${n.x},${n.y - 38})`}
                  role="button"
                  tabIndex={0}
                  aria-label={`${n.label} · ${label(n.role)} · ${statusText(status)}${count ? ` · ${count.calls}회` : ""}`}
                  aria-pressed={selected === n.id}
                  onClick={() => onSelect(n)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") {
                      e.preventDefault();
                      onSelect(n);
                    }
                  }}
                  data-node-id={n.id}
                >
                  <title>
                    {n.label} · {n.taskId}
                  </title>
                  <rect className="node-card" width="180" height="86" rx="12" />
                  <rect
                    x="0"
                    y="15"
                    width="3"
                    height="55"
                    rx="1"
                    fill={
                      status === "changes_requested" ? "#edc875" : color(status)
                    }
                  />
                  <text x="14" y="23" className="node-role">
                    {label(n.role)}
                    {n.kind === "assignment" ? " · 배정" : ""}
                  </text>
                  <text x="14" y="45" className="node-name">
                    {count
                      ? `${count.calls}회 · 통과 ${count.succeeded} · 확인 ${count.attention}`
                      : n.label.length > 17
                        ? n.label.slice(0, 16) + "…"
                        : n.label}
                  </text>
                  <circle
                    cx="18"
                    cy="65"
                    r="3"
                    fill={
                      status === "changes_requested" ? "#edc875" : color(status)
                    }
                  />
                  <text x="28" y="69" className="node-state">
                    {replay && n.status === "running"
                      ? "당시 실행 중"
                      : statusText(status)}
                  </text>
                </g>
              );
            })}
          </svg>
        </div>
      )}
      <div className="panel-footer">
        <span>
          {projection
            ? "최근 작업 최대 6개 · 조회된 실행 기준"
            : "최근 작업 최대 6개 · 작업당 단계 최대 8개"}
        </span>
        <span>
          {projection
            ? `역할·배정 ${graph.nodes.length}개`
            : `${graph.nodes.length} / ${graph.total}개 단계·배정 표시`}
        </span>
      </div>
    </section>
  );
}
