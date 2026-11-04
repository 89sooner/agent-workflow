export type Point = [number, number, number];
export interface Journey {
  point: Point;
  target: Point;
  path: Point[];
  stand: number;
  phase: "seated" | "rising" | "walking" | "sitting";
  yaw: number;
}
// The center aisles lead through the front doors onto a shared corridor.
export function route(from: Point, to: Point, corridor: number): Point[] {
  const center = (x: number) => Math.round(x / 6.4) * 6.4;
  const a = center(from[0]),
    b = center(to[0]);
  return [
    [a, 0, from[2]],
    [a, 0, corridor],
    [b, 0, corridor],
    [b, 0, to[2]],
    [...to],
  ].filter(
    (p, i, all) =>
      Math.hypot(
        p[0] - (i ? all[i - 1][0] : from[0]),
        p[2] - (i ? all[i - 1][2] : from[2]),
      ) > 0.001,
  ) as Point[];
}
export function createJourney(point: Point): Journey {
  return {
    point: [...point],
    target: [...point],
    path: [],
    stand: 0,
    phase: "seated",
    yaw: 0,
  };
}
export function retarget(
  j: Journey,
  target: Point,
  corridor: number,
  enabled: boolean,
) {
  if (!enabled) {
    j.point = [...target];
    j.target = [...target];
    j.path = [];
    j.stand = 0;
    j.phase = "seated";
    j.yaw = 0;
    return;
  }
  if (j.target.every((v, i) => Math.abs(v - target[i]) < 0.001)) return;
  // Replan from the current position, never rewind to the old chair.
  j.target = [...target];
  j.path = route(j.point, target, corridor);
  j.phase = j.stand < 1 ? "rising" : "walking";
}
export function stepJourney(j: Journey, delta: number) {
  const dt = Math.min(delta, 0.05); // no catch-up leap after a paused tab
  if (j.phase === "rising") {
    j.stand = Math.min(1, j.stand + dt / 0.25);
    if (j.stand === 1) j.phase = "walking";
  } else if (j.phase === "walking") {
    const p = j.path[0];
    if (!p) {
      j.phase = "sitting";
      return;
    }
    const dx = p[0] - j.point[0],
      dz = p[2] - j.point[2],
      distance = Math.hypot(dx, dz);
    const amount = Math.min(distance, dt * 3);
    if (distance > 0) {
      j.point[0] += (dx / distance) * amount;
      j.point[2] += (dz / distance) * amount;
      const targetYaw = Math.atan2(-dx, -dz);
      j.yaw +=
        Math.atan2(Math.sin(targetYaw - j.yaw), Math.cos(targetYaw - j.yaw)) *
        Math.min(1, dt * 12);
    }
    if (distance <= amount + 0.001) j.path.shift();
  } else if (j.phase === "sitting") {
    j.yaw +=
      Math.atan2(Math.sin(-j.yaw), Math.cos(-j.yaw)) * Math.min(1, dt * 16);
    j.stand = Math.max(0, j.stand - dt / 0.25);
    if (!j.stand) {
      j.phase = "seated";
      j.yaw = 0;
      j.point = [...j.target];
    }
  }
}
