export type RoomZone = "work" | "lounge" | "attention";
export const roomZones: { id: RoomZone; label: string; color: string }[] = [
  { id: "work", label: "작업실", color: "#24594f" },
  { id: "lounge", label: "대기·휴게실", color: "#354d70" },
  { id: "attention", label: "확인 필요", color: "#705039" },
];
// Observation freshness is independent of animation preferences.
export function roomZone(status: string, activityKnown = true): RoomZone {
  if (!activityKnown) return "attention";
  if (status === "running") return "work";
  if (
    [
      "idle",
      "pending",
      "assigned",
      "queued",
      "ready",
      "retrying",
      "succeeded",
      "completed",
      "needs_review",
      "ready_to_finish",
    ].includes(status)
  )
    return "lounge";
  return "attention";
}
export function roomLayout(zones: RoomZone[]) {
  const columns = Math.max(1, Math.min(4, zones.length));
  let cursor = 0;
  const areas = roomZones.map((zone) => {
    const count = zones.filter((z) => z === zone.id).length;
    const depth = Math.max(1, Math.ceil(count / columns)) * 2.8;
    const area = { ...zone, count, center: cursor + depth / 2, depth };
    cursor += depth + 0.6;
    return area;
  });
  const depth = cursor - 0.6;
  for (const area of areas) area.center += 1 - depth / 2;
  const offsets = { work: 0, lounge: 0, attention: 0 };
  const positions = zones.map((zone) => {
    const area = areas.find((a) => a.id === zone)!;
    const i = offsets[zone]++;
    return [
      ((i % columns) - (columns - 1) / 2) * 2.6,
      0,
      area.center - area.depth / 2 + 1.4 + Math.floor(i / columns) * 2.8,
    ] as [number, number, number];
  });
  return {
    areas,
    positions,
    width: Math.max(6, columns * 2.6 + 2),
    depth: depth + 1,
  };
}
