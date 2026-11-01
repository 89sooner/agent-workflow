import { useId, useState } from "react";
import type { Graph as Model, Node, Transfer } from "./types";
import { color, label } from "./model";
interface Props {
  graph: Model;
  selected: string | null;
  onSelect: (n: Node) => void;
  transfers: Transfer[];
  motion: boolean;
  replay: boolean;
}
export function ExecutionGraph({
  graph,
  selected,
  onSelect,
  transfers,
  motion,
  replay,
}: Props) {
  const [zoom, setZoom] = useState(1),
    marker = useId().replaceAll(":", "");
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
      <p className="panel-caption">
        선은 기록된 실행 순서입니다. 노드를 선택하면 담당자와 결과가 함께
        표시됩니다.
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
            aria-label="작업별 실제 실행 순서"
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
              const a = graph.nodes.find((n) => n.id === edge.source)!,
                b = graph.nodes.find((n) => n.id === edge.target)!;
              const path = `M ${a.x + 180} ${a.y} C ${a.x + 205} ${a.y},${b.x - 25} ${b.y},${b.x} ${b.y}`;
              return (
                <g key={edge.id}>
                  <title>{edge.label}</title>
                  <path
                    d={path}
                    className="graph-edge"
                    markerEnd={`url(#${marker})`}
                  />
                  {motion && active.has(b.actorId) && (
                    <path d={path} className="graph-edge signal" />
                  )}
                </g>
              );
            })}
            {graph.nodes.map((n) => (
              <g
                key={n.id}
                className={`graph-node ${selected === n.id ? "selected" : ""} ${motion && n.status === "running" ? "working" : ""}`}
                transform={`translate(${n.x},${n.y - 38})`}
                role="button"
                tabIndex={0}
                aria-label={`${n.label} · ${label(n.role)} · ${label(n.status)}`}
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
                  fill={color(n.status)}
                />
                <text x="14" y="23" className="node-role">
                  {label(n.role)}
                  {n.kind === "assignment" ? " · 배정" : ""}
                </text>
                <text x="14" y="45" className="node-name">
                  {n.label.length > 17 ? n.label.slice(0, 16) + "…" : n.label}
                </text>
                <circle cx="18" cy="65" r="3" fill={color(n.status)} />
                <text x="28" y="69" className="node-state">
                  {replay && n.status === "running"
                    ? "당시 실행 중"
                    : label(n.status)}
                </text>
              </g>
            ))}
          </svg>
        </div>
      )}
      <div className="panel-footer">
        <span>최근 작업 최대 6개 · 작업당 단계 최대 8개</span>
        <span>
          {graph.nodes.length} / {graph.total}개 단계·배정 표시
        </span>
      </div>
    </section>
  );
}
