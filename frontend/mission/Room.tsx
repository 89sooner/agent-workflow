import {
  Component,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { Canvas, useFrame, useThree } from "@react-three/fiber";
import { CanvasTexture, Group, Mesh, Vector3 } from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import type { Node, Transfer } from "./types";
import { color } from "./model";
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
function Controls({ reset, compact }: { reset: number; compact: boolean }) {
  const { camera, gl, invalidate } = useThree();
  const controls = useRef<OrbitControls | null>(null);
  useEffect(() => {
    const c = new OrbitControls(camera, gl.domElement);
    controls.current = c;
    const change = () => invalidate();
    c.enableDamping = false;
    c.minDistance = 6;
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
    camera.position.set(
      ...((compact ? [5, 6, 7] : [9, 10, 12]) as [number, number, number]),
    );
    controls.current?.target.set(0, 0.5, 1);
    controls.current?.update();
    invalidate();
  }, [reset, compact, camera, invalidate]);
  return null;
}
function Nameplate({ name }: { name: string }) {
  const texture = useMemo(() => {
    const canvas = document.createElement("canvas");
    canvas.width = 512;
    canvas.height = 80;
    const ctx = canvas.getContext("2d")!;
    ctx.fillStyle = "#132033";
    ctx.fillRect(0, 0, 512, 80);
    ctx.fillStyle = "#e6eef8";
    ctx.font = "500 30px sans-serif";
    ctx.textAlign = "center";
    ctx.fillText(name.length > 22 ? name.slice(0, 21) + "…" : name, 256, 50);
    return new CanvasTexture(canvas);
  }, [name]);
  useEffect(() => () => texture.dispose(), [texture]);
  return (
    <sprite position={[0, 2.35, 0]} scale={[2.6, 0.4, 1]}>
      <spriteMaterial map={texture} depthTest={false} />
    </sprite>
  );
}
function Workstation({
  node,
  position,
  selected,
  onSelect,
  animate,
}: {
  node: Node;
  position: [number, number, number];
  selected: boolean;
  onSelect: (n: Node) => void;
  animate: boolean;
}) {
  const arm = useRef<Group>(null),
    head = useRef<Mesh>(null);
  const { invalidate } = useThree();
  useFrame(({ clock }) => {
    if (arm.current)
      arm.current.rotation.x = animate
        ? Math.sin(clock.elapsedTime * 5) * 0.12
        : 0;
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
      <mesh position={[0, 1.18, -0.5]}>
        <boxGeometry args={[1, 0.64, 0.1]} />
        <meshStandardMaterial color="#131d2d" />
      </mesh>
      <mesh position={[0, 1.18, -0.435]}>
        <planeGeometry args={[0.86, 0.49]} />
        <meshStandardMaterial
          color={color(node.status)}
          emissive={color(node.status)}
          emissiveIntensity={animate ? 0.75 : 0.15}
        />
      </mesh>
      <mesh position={[0, 0.8, 0.13]} rotation={[-Math.PI / 2, 0, 0]}>
        <planeGeometry args={[0.65, 0.23]} />
        <meshStandardMaterial color="#7890aa" />
      </mesh>
      <mesh position={[0, 0.65, 0.55]}>
        <boxGeometry args={[0.52, 0.67, 0.38]} />
        <meshStandardMaterial
          color={
            node.kind === "agent" || node.kind === "assignment"
              ? "#4d718e"
              : "#616485"
          }
        />
      </mesh>
      <mesh ref={head} position={[0, 1.22, 0.55]}>
        <sphereGeometry args={[0.24, 16, 12]} />
        <meshStandardMaterial color="#b7cadd" />
      </mesh>
      <group ref={arm} position={[0, 0.94, 0.4]}>
        {[-0.34, 0.34].map((x) => (
          <mesh key={x} position={[x, -0.03, -0.12]} rotation={[0.8, 0, 0]}>
            <boxGeometry args={[0.14, 0.38, 0.15]} />
            <meshStandardMaterial color="#7a9db9" />
          </mesh>
        ))}
      </group>
      <mesh position={[0, 0.4, 0.8]}>
        <boxGeometry args={[0.65, 0.8, 0.12]} />
        <meshStandardMaterial color="#22364b" />
      </mesh>
      <Nameplate name={node.label} />
    </group>
  );
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
}: {
  nodes: Node[];
  selected: string | null;
  onSelect: (n: Node) => void;
  motion: boolean;
  transfers: Transfer[];
  visible: boolean;
}) {
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
  const columns = Math.max(1, Math.min(4, shown.length)),
    rows = Math.max(1, Math.ceil(shown.length / columns));
  const floorWidth = Math.max(6, columns * 2.6 + 2),
    floorDepth = Math.max(6, rows * 2.8 + 2);
  const positions = shown.map((_, i): [number, number, number] => [
    ((i % columns) - (columns - 1) / 2) * 2.6,
    0,
    (Math.floor(i / columns) - (rows - 1) / 2) * 2.8 + 1,
  ]);
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
        <button onClick={() => setReset((v) => v + 1)}>시점 초기화</button>
      </div>
      <div
        className="room-canvas"
        data-testid="r3f-room"
        data-active={
          !lost && motion && shown.some((n) => n.status === "running")
        }
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
              camera={{ position: [9, 10, 12], fov: 45 }}
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
              <ambientLight intensity={1.5} />
              <directionalLight position={[6, 12, 4]} intensity={2.3} />
              <pointLight
                position={[-7, 5, -3]}
                color="#7ef4cf"
                intensity={20}
              />
              <mesh position={[0, -0.14, 1]}>
                <boxGeometry args={[floorWidth, 0.25, floorDepth]} />
                <meshStandardMaterial color="#1d2d40" />
              </mesh>
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
                  animate={motion && node.status === "running"}
                />
              ))}
              {motion &&
                transfers.map((t) => {
                  const from = point(t.source),
                    to = point(t.target);
                  return from && to ? (
                    <Packet key={t.seq} transfer={t} from={from} to={to} />
                  ) : null;
                })}
              <Controls reset={reset} compact={shown.length <= 4} />
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
