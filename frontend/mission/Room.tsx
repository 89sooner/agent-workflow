import {
  Component,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { Canvas, useFrame, useThree } from "@react-three/fiber";
import {
  Group,
  Mesh,
  Vector3,
  CapsuleGeometry,
  MeshStandardMaterial,
} from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import type { Node, Transfer } from "./types";
import { color, label } from "./model";
import { roomZone, roomLayout, type RoomZone } from "./rooms";
function ContextHealth({ onLost }: { onLost: () => void }) {
  const { gl } = useThree();
  useEffect(() => {
    const canvas = gl.domElement;
    const lost = (event: Event) => {
      event.preventDefault();
      if (canvas.isConnected) onLost();
    };
    canvas.addEventListener("webglcontextlost", lost);
    return () => canvas.removeEventListener("webglcontextlost", lost);
  }, [gl, onLost]);
  return null;
}
function Controls({
  reset,
  extent,
  focus,
}: {
  reset: number;
  extent: number;
  focus: [number, number, number] | null;
}) {
  const { camera, gl, invalidate } = useThree();
  const controls = useRef<OrbitControls | null>(null);
  useEffect(() => {
    const c = new OrbitControls(camera, gl.domElement);
    controls.current = c;
    const change = () => invalidate();
    c.enableDamping = false;
    c.minDistance = 2.8;
    c.maxDistance = 32;
    c.maxPolarAngle = Math.PI * 0.47;
    c.target.set(0, 0, 1);
    c.addEventListener("change", change);
    c.update();
    return () => {
      c.removeEventListener("change", change);
      c.dispose();
      controls.current = null;
    };
  }, [camera, gl, invalidate]);
  useEffect(() => {
    camera.position.set(extent * 0.9, extent * 0.9, -extent);
    controls.current?.target.set(0, 0.5, 1);
    controls.current?.update();
    invalidate();
  }, [reset, extent, camera, invalidate]);
  useEffect(() => {
    if (!focus) return;
    camera.position.set(focus[0] + 2.8, 2.7, focus[2] - 2.8);
    controls.current?.target.set(focus[0], 1, focus[2]);
    controls.current?.update();
    invalidate();
  }, [focus, camera, invalidate]);
  return null;
}
// Screen-space labels stay readable at any zoom level. Selection wins collisions.
function Nameplates({
  nodes,
  positions,
  selected,
  onSelect,
}: {
  nodes: Node[];
  positions: [number, number, number][];
  selected: string | null;
  onSelect: (node: Node) => void;
}) {
  const { gl, size, invalidate } = useThree();
  const entries = useRef<
    { node: Node; element: HTMLButtonElement; point: Vector3 }[]
  >([]);
  const overlayRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    const host = gl.domElement.parentElement!;
    const overlay = document.createElement("div");
    overlay.className = "room-nameplates";
    Object.assign(overlay.style, {
      position: "absolute",
      inset: "0",
      pointerEvents: "none",
      overflow: "hidden",
    });
    host.appendChild(overlay);
    overlayRef.current = overlay;
    return () => {
      entries.current = [];
      overlayRef.current = null;
      overlay.remove();
    };
  }, [gl]);
  useEffect(() => {
    const overlay = overlayRef.current!;
    const previous = new Map(
      entries.current.map((entry) => [entry.node.id, entry]),
    );
    entries.current = nodes.map((node, i) => {
      const existing = previous.get(node.id);
      previous.delete(node.id);
      const element = existing?.element ?? document.createElement("button");
      element.className = "room-nameplate";
      element.title = node.label + " · " + label(node.role);
      element.setAttribute("aria-label", element.title);
      element.setAttribute("aria-pressed", String(node.id === selected));
      element.textContent = element.title;
      Object.assign(element.style, {
        position: "absolute",
        width: "156px",
        height: "32px",
        boxSizing: "border-box",
        padding: "4px 8px",
        overflow: "hidden",
        textOverflow: "ellipsis",
        whiteSpace: "nowrap",
        font: "12px sans-serif",
        background: "#132033",
        color: "#e6eef8",
        border:
          node.id === selected ? "2px solid #7bffd0" : "1px solid #526479",
        borderRadius: "5px",
        pointerEvents: "auto",
        visibility: existing ? element.style.visibility : "hidden",
      });
      element.onclick = () => onSelect(node);
      if (!existing) overlay.appendChild(element);
      return {
        node,
        element,
        point: new Vector3(positions[i][0], 2.35, positions[i][2]),
      };
    });
    for (const entry of previous.values()) entry.element.remove();
    invalidate();
  }, [nodes, positions, selected, onSelect, gl, invalidate]);
  const projected = useMemo(() => new Vector3(), []);
  useFrame(({ camera }) => {
    const width = Math.min(156, size.width - 16),
      height = 32;
    const boxes: { x: number; y: number }[] = [];
    const ordered = [...entries.current].sort(
      (a, b) => Number(b.node.id === selected) - Number(a.node.id === selected),
    );
    for (const { node, element, point } of ordered) {
      projected.copy(point).project(camera);
      const valid =
        width > 0 &&
        size.height >= height + 16 &&
        projected.z >= -1 &&
        projected.z <= 1;
      const x = Math.max(
        8,
        Math.min(
          size.width - width - 8,
          ((projected.x + 1) * size.width) / 2 - width / 2,
        ),
      );
      const y = Math.max(
        8,
        Math.min(
          size.height - height - 8,
          ((1 - projected.y) * size.height) / 2 - height,
        ),
      );
      const inside =
        node.id === selected ||
        (Math.abs(projected.x) <= 1 && Math.abs(projected.y) <= 1);
      const collision = boxes.some(
        (b) => Math.abs(b.x - x) < width + 6 && Math.abs(b.y - y) < height + 6,
      );
      const show = valid && inside && !collision;
      element.style.visibility = show ? "visible" : "hidden";
      if (show) {
        element.style.width = width + "px";
        element.style.transform = `translate(${x}px, ${y}px)`;
        boxes.push({ x, y });
      }
    }
  });
  return null;
}
function Workstation({
  node,
  position,
  selected,
  onSelect,
  animate,
  zone,
}: {
  node: Node;
  position: [number, number, number];
  selected: boolean;
  onSelect: (n: Node) => void;
  animate: boolean;
  zone: RoomZone;
}) {
  const arm = useRef<Group>(null),
    head = useRef<Group>(null);
  const { invalidate } = useThree();
  const [detail, setDetail] = useState(true);
  const detailRef = useRef(true);
  const finger = useMemo(
    () => ({
      geometry: new CapsuleGeometry(0.014, 0.055, 2, 6),
      material: new MeshStandardMaterial({ color: "#a8bfce", roughness: 0.65 }),
    }),
    [],
  );
  useEffect(
    () => () => {
      finger.geometry.dispose();
      finger.material.dispose();
    },
    [finger],
  );
  useFrame(({ clock, camera }) => {
    const near =
      selected ||
      Math.hypot(
        camera.position.x - position[0],
        camera.position.y - position[1],
        camera.position.z - position[2],
      ) < 9;
    if (near !== detailRef.current) {
      detailRef.current = near;
      setDetail(near);
    }
    if (arm.current)
      arm.current.rotation.x = animate
        ? Math.sin(clock.elapsedTime * 5) * 0.035
        : zone === "work"
          ? 0
          : -0.65;
    if (head.current)
      head.current.rotation.z = animate
        ? Math.sin(clock.elapsedTime * 1.7) * 0.035
        : 0;
    if (animate) invalidate();
  });
  useEffect(() => {
    invalidate();
  }, [animate, invalidate]);
  return (
    <group
      position={position}
      onClick={(e) => {
        e.stopPropagation();
        onSelect(node);
      }}
    >
      <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, 0.02, 0]}>
        <ringGeometry args={[1.05, selected ? 1.14 : 1.08, 40]} />
        <meshBasicMaterial
          color={selected ? "#e8fff5" : color(node.status)}
          transparent
          opacity={selected ? 1 : 0.5}
        />
      </mesh>
      {zone === "work" && (
        <>
          <mesh position={[0, 0.75, -0.2]}>
            <boxGeometry args={[1.9, 0.12, 1]} />
            <meshStandardMaterial color="#34475e" />
          </mesh>
          {[-0.75, 0.75].map((x) => (
            <mesh key={x} position={[x, 0.38, -0.2]}>
              <boxGeometry args={[0.08, 0.75, 0.65]} />
              <meshStandardMaterial color="#26364a" />
            </mesh>
          ))}
          <mesh position={[-0.5, 1.14, -0.5]}>
            <boxGeometry args={[0.8, 0.56, 0.1]} />
            <meshStandardMaterial color="#131d2d" />
          </mesh>
          <mesh position={[-0.5, 1.14, -0.435]}>
            <planeGeometry args={[0.69, 0.43]} />
            <meshStandardMaterial
              color={color(node.status)}
              emissive={color(node.status)}
              emissiveIntensity={animate ? 0.75 : 0.15}
            />
          </mesh>
          <mesh position={[0, 0.816, 0.13]} rotation={[-Math.PI / 2, 0, 0]}>
            <planeGeometry args={[0.8, 0.26]} />
            <meshStandardMaterial color="#7890aa" />
          </mesh>
        </>
      )}
      {zone !== "work" && (
        <group>
          <mesh position={[0, 0.43, 0.55]}>
            <boxGeometry args={[0.85, 0.18, 0.65]} />
            <meshStandardMaterial
              color={zone === "lounge" ? "#57749a" : "#96724f"}
              roughness={0.9}
            />
          </mesh>
          {[-0.39, 0.39].map((x) => (
            <mesh key={x} position={[x, 0.6, 0.55]}>
              <boxGeometry args={[0.12, 0.3, 0.65]} />
              <meshStandardMaterial color="#34465d" />
            </mesh>
          ))}
        </group>
      )}
      {/* Rounded suit panels and exposed joints keep the seated silhouette readable. */}
      <mesh position={[0, 0.7, 0.55]} scale={[1, 1.15, 0.72]}>
        <capsuleGeometry args={[0.24, 0.22, 4, 12]} />
        <meshStandardMaterial
          color={
            node.kind === "agent" || node.kind === "assignment"
              ? "#4d718e"
              : "#616485"
          }
          roughness={0.65}
          metalness={0.18}
        />
      </mesh>
      <mesh position={[0, 0.99, 0.55]}>
        <cylinderGeometry args={[0.09, 0.11, 0.13, 12]} />
        <meshStandardMaterial color="#25384a" metalness={0.5} roughness={0.4} />
      </mesh>
      <group ref={head} position={[0, 1.23, 0.55]}>
        <mesh scale={[1, 1.06, 0.92]}>
          <sphereGeometry args={[0.245, 20, 14]} />
          <meshStandardMaterial
            color="#c5d5df"
            roughness={0.55}
            metalness={0.2}
          />
        </mesh>
        <mesh position={[0, 0.015, -0.195]} scale={[1, 0.55, 0.32]}>
          <sphereGeometry args={[0.205, 16, 10]} />
          <meshStandardMaterial
            color="#14293b"
            roughness={0.28}
            metalness={0.18}
          />
        </mesh>
        {[-1, 1].map((side) => (
          <group key={side}>
            <mesh position={[side * 0.078, 0.02, -0.253]}>
              <boxGeometry args={[0.058, 0.022, 0.012]} />
              <meshStandardMaterial
                color="#a7e5ec"
                emissive="#64b7c8"
                emissiveIntensity={0.35}
              />
            </mesh>
            <mesh
              position={[side * 0.235, 0, 0]}
              rotation={[0, 0, Math.PI / 2]}
            >
              <cylinderGeometry args={[0.078, 0.078, 0.045, 12]} />
              <meshStandardMaterial
                color="#698aa1"
                metalness={0.5}
                roughness={0.4}
              />
            </mesh>
          </group>
        ))}
        <mesh position={[0, -0.12, -0.207]}>
          <boxGeometry args={[0.09, 0.018, 0.02]} />
          <meshStandardMaterial color="#668195" />
        </mesh>
      </group>
      <group ref={arm} position={[0, 0.92, 0.4]}>
        {[-1, 1].map((side) => (
          <group key={side} position={[side * 0.3, 0, 0.1]}>
            <mesh>
              <sphereGeometry args={[0.11, 12, 8]} />
              <meshStandardMaterial
                color="#8ea9ba"
                roughness={0.4}
                metalness={0.3}
              />
            </mesh>
            <mesh
              position={[0, -0.13, -0.055]}
              rotation={[0.4, 0, side * 0.12]}
            >
              <capsuleGeometry args={[0.075, 0.15, 3, 10]} />
              <meshStandardMaterial color="#5f819b" roughness={0.6} />
            </mesh>
            <mesh position={[0, -0.24, -0.12]}>
              <sphereGeometry args={[0.073, 10, 8]} />
              <meshStandardMaterial
                color="#25384a"
                metalness={0.4}
                roughness={0.45}
              />
            </mesh>
            <mesh position={[0, -0.17, -0.23]} rotation={[1.1, 0, 0]}>
              <capsuleGeometry args={[0.063, 0.17, 3, 10]} />
              <meshStandardMaterial color="#8ea9ba" roughness={0.5} />
            </mesh>
            <mesh position={[0, -0.105, -0.35]} scale={[1, 0.48, 1.25]}>
              <sphereGeometry args={[0.075, 10, 8]} />
              <meshStandardMaterial color="#c5d5df" roughness={0.5} />
            </mesh>
            {detail &&
              [0, 1, 2].map((index) => (
                <mesh
                  key={index}
                  geometry={finger.geometry}
                  material={finger.material}
                  dispose={null}
                  position={[(index - 1) * 0.035, -0.104, -0.425]}
                  rotation={[Math.PI / 2, 0, 0]}
                ></mesh>
              ))}
          </group>
        ))}
      </group>
      {[-1, 1].map((side) => (
        <group key={side}>
          <mesh
            position={[side * 0.14, 0.38, 0.4]}
            rotation={[Math.PI / 2, 0, 0]}
          >
            <capsuleGeometry args={[0.095, 0.22, 3, 10]} />
            <meshStandardMaterial color="#4d657e" roughness={0.7} />
          </mesh>
          <mesh position={[side * 0.14, 0.22, 0.23]}>
            <capsuleGeometry args={[0.075, 0.18, 3, 10]} />
            <meshStandardMaterial color="#69859c" roughness={0.6} />
          </mesh>
          <mesh position={[side * 0.14, 0.075, 0.15]} scale={[0.9, 0.5, 1.6]}>
            <sphereGeometry args={[0.12, 12, 8]} />
            <meshStandardMaterial color="#24364b" roughness={0.85} />
          </mesh>
        </group>
      ))}
      <mesh position={[0, 0.4, 0.8]}>
        <boxGeometry args={[0.65, 0.8, 0.12]} />
        <meshStandardMaterial color="#22364b" />
      </mesh>
      <mesh position={[0.16, 0.87, 0.36]} rotation={[Math.PI / 2, 0, 0]}>
        <cylinderGeometry
          args={[
            0.055,
            0.055,
            0.018,
            node.role === "review" ? 3 : node.role === "check" ? 4 : 6,
          ]}
        />
        <meshStandardMaterial color="#e0c893" roughness={0.7} />
      </mesh>
    </group>
  );
}
function RenderMetrics() {
  const { gl } = useThree();
  useFrame(() => {
    const host = gl.domElement.parentElement;
    if (host) {
      host.dataset.drawCalls = String(gl.info.render.calls);
      host.dataset.triangles = String(gl.info.render.triangles);
    }
  });
  return null;
}
function Packet({
  transfer,
  from,
  to,
}: {
  transfer: Transfer;
  from: [number, number, number];
  to: [number, number, number];
}) {
  const ref = useRef<Mesh>(null),
    { invalidate } = useThree();
  useFrame(() => {
    const p = Math.max(0, Math.min(1, (Date.now() - transfer.at) / 1600));
    if (ref.current) {
      ref.current.visible = p < 1;
      ref.current.position.lerpVectors(
        new Vector3(...from),
        new Vector3(...to),
        p,
      );
      ref.current.position.y += Math.sin(p * Math.PI) * 1.3;
    }
    if (p < 1) invalidate();
  });
  return (
    <mesh ref={ref}>
      <sphereGeometry args={[0.1, 12, 8]} />
      <meshBasicMaterial
        color={transfer.kind === "process_finished" ? "#a4b6ff" : "#7bffd0"}
      />
    </mesh>
  );
}
class Boundary extends Component<
  { children: ReactNode; fallback: ReactNode },
  { failed: boolean }
> {
  state = { failed: false };
  static getDerivedStateFromError() {
    return { failed: true };
  }
  render() {
    return this.state.failed ? this.props.fallback : this.props.children;
  }
}
export function Room({
  nodes,
  selected,
  onSelect,
  motion,
  transfers,
  visible,
  activityKnown = true,
  historical = false,
}: {
  nodes: Node[];
  selected: string | null;
  onSelect: (n: Node) => void;
  motion: boolean;
  transfers: Transfer[];
  visible: boolean;
  activityKnown?: boolean;
  historical?: boolean;
}) {
  const [focus, setFocus] = useState<string | null>(null);
  const [reset, setReset] = useState(0),
    [lost, setLost] = useState(false);
  const selectedNode = nodes.find((n) => n.id === selected);
  const latest = [...new Map(nodes.map((n) => [n.actorId, n])).values()];
  latest.sort(
    (a, b) =>
      Number(b.status === "running") - Number(a.status === "running") ||
      Number(b.kind === "agent") - Number(a.kind === "agent"),
  );
  const shown = latest.slice(0, 12);
  if (selectedNode && !shown.some((n) => n.id === selectedNode.id)) {
    if (shown.length < 12) shown.push(selectedNode);
    else shown[11] = selectedNode;
  }
  const zones = shown.map((n) => roomZone(n.status, activityKnown));
  const {
    areas,
    positions,
    width: floorWidth,
    depth: floorDepth,
  } = roomLayout(zones);
  const focusedIndex = shown.findIndex((n) => n.id === focus);
  const focusX = positions[focusedIndex]?.[0],
    focusZ = positions[focusedIndex]?.[2];
  const focusPosition = useMemo<[number, number, number] | null>(
    () =>
      focusX === undefined || focusZ === undefined ? null : [focusX, 0, focusZ],
    [focus, focusX, focusZ],
  );
  const hub: [number, number, number] = [-floorWidth / 2 + 0.5, 1, 1];
  const point = (actor: string) => {
    const i = shown.findIndex((n) => n.actorId === actor);
    return i >= 0
      ? ([positions[i][0], 1.5, positions[i][2]] as [number, number, number])
      : actor === "controller" || actor.endsWith(":controller")
        ? hub
        : null;
  };
  const fallback = (
    <div className="room-fallback">
      3D를 표시할 수 없습니다. 실행 그래프와 담당자 목록에서 같은 기록을
      확인하세요.
    </div>
  );
  return (
    <section className="panel room-panel">
      <div className="panel-heading">
        <div>
          <span className="eyebrow">AGENT WORKSPACE</span>
          <h2>함께 일하는 공간</h2>
        </div>
        <div>
          <button
            disabled={!selectedNode || lost}
            onClick={() => {
              const i = shown.findIndex((n) => n.id === selected);
              if (i >= 0) setFocus(shown[i].id);
            }}
          >
            선택한 작업자 가까이
          </button>
          <button
            onClick={() => {
              setFocus(null);
              setReset((v) => v + 1);
            }}
          >
            시점 초기화
          </button>
        </div>
      </div>
      <div
        className="room-zones"
        style={{
          display: "flex",
          flexWrap: "wrap",
          gap: 12,
          padding: "8px 18px",
        }}
      >
        {areas.map((area) => (
          <span
            key={area.id}
            data-zone={area.id}
            style={{ borderLeft: `5px solid ${area.color}`, paddingLeft: 8 }}
          >
            {area.label} · {area.count}
          </span>
        ))}
      </div>
      <p className="panel-caption">
        {historical
          ? "선택한 기록 시점 기준"
          : activityKnown
            ? "최근 관측 기록 기준"
            : "연결·최신 상태 확인 전: 현재 활동 미확인"}
        {
          " · 대기실의 완료 표시는 기록 종료를 뜻하며 현재 세션의 idle을 보장하지 않습니다."
        }
      </p>
      {selectedNode && (
        <p
          className="room-selection"
          style={{ padding: "0 18px", overflowWrap: "anywhere" }}
        >
          <strong>{selectedNode.label}</strong> · {label(selectedNode.role)} ·{" "}
          {label(selectedNode.status)}
        </p>
      )}
      <div
        className="room-canvas"
        data-testid="r3f-room"
        data-active={!lost && activityKnown && motion && zones.includes("work")}
      >
        {!nodes.length && (
          <div className="room-empty-overlay">
            배정 또는 실행 기록을 기다립니다.
          </div>
        )}
        {lost ? (
          fallback
        ) : (
          <Boundary fallback={fallback}>
            <Canvas
              frameloop={visible ? "demand" : "never"}
              dpr={[1, 1.5]}
              camera={{ position: [10, 9, -11], fov: 45 }}
              gl={{ antialias: true, alpha: false }}
              fallback={fallback}
              onCreated={({ gl }) => {
                gl.domElement.setAttribute(
                  "aria-label",
                  "실제 실행 담당자 3D 작업 공간",
                );
              }}
            >
              <color attach="background" args={["#111d2d"]} />
              <ambientLight intensity={1.2} />
              <directionalLight position={[4, 8, -5]} intensity={2} />
              <pointLight
                position={[-7, 5, -3]}
                color="#7ef4cf"
                intensity={20}
              />
              <mesh position={[0, -0.14, 1]}>
                <boxGeometry args={[floorWidth, 0.25, floorDepth]} />
                <meshStandardMaterial color="#1d2d40" />
              </mesh>
              {areas.map((area) => (
                <mesh
                  key={area.id}
                  position={[0, 0.008, area.center]}
                  rotation={[-Math.PI / 2, 0, 0]}
                >
                  <planeGeometry args={[floorWidth - 0.4, area.depth]} />
                  <meshStandardMaterial color={area.color} roughness={1} />
                </mesh>
              ))}
              <gridHelper
                args={[
                  Math.max(floorWidth, floorDepth),
                  12,
                  "#375067",
                  "#263a50",
                ]}
                position={[0, 0.001, 1]}
              />
              <mesh position={hub}>
                <octahedronGeometry args={[0.5, 0]} />
                <meshStandardMaterial color="#73d9be" wireframe />
              </mesh>
              {shown.map((node, i) => (
                <Workstation
                  key={node.id}
                  node={node}
                  position={positions[i]}
                  selected={selected === node.id}
                  onSelect={onSelect}
                  zone={zones[i]}
                  animate={motion && zones[i] === "work"}
                />
              ))}
              {motion &&
                activityKnown &&
                transfers.map((t) => {
                  const from = point(t.source),
                    to = point(t.target);
                  return from && to ? (
                    <Packet key={t.seq} transfer={t} from={from} to={to} />
                  ) : null;
                })}
              <Controls
                reset={reset}
                extent={Math.max(floorWidth, floorDepth, 10)}
                focus={focusPosition}
              />
              <Nameplates
                nodes={shown}
                positions={positions}
                selected={selected}
                onSelect={onSelect}
              />
              <RenderMetrics />
              <ContextHealth onLost={() => setLost(true)} />
            </Canvas>
          </Boundary>
        )}
      </div>
      <div className="panel-footer">
        <span>드래그로 회전 · 스크롤로 확대 · 담당자 선택</span>
        <span>{shown.length}개 담당자·실행기</span>
      </div>
      <div className="room-legend">
        <i style={{ background: "#66e6bb" }} /> 확인된 실행 활동{" "}
        <i style={{ background: "#a4b6ff" }} /> 실제 결과 전달{" "}
        <span>키보드 탐색은 아래 담당자 목록을 사용하세요.</span>
      </div>
    </section>
  );
}
