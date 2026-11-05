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
      "ended",
      "needs_review",
      "ready_to_finish",
    ].includes(status)
  )
    return "lounge";
  return "attention";
}
export function roomLayout(zones: RoomZone[]) {
  const depth = Math.max(2, Math.ceil(zones.length / 2)) * 2.8;
  const areas = roomZones.map((zone, i) => ({
    ...zone,
    count: zones.filter((z) => z === zone.id).length,
    x: (i - 1) * 6.4,
    center: 1,
    depth,
  }));
  const offsets = { work: 0, lounge: 0, attention: 0 };
  const positions = zones.map((zone) => {
    const area = areas.find((a) => a.id === zone)!;
    const index = offsets[zone]++;
    return [
      area.x + (index % 2 ? 1.5 : -1.5),
      0,
      1 - depth / 2 + 1.4 + Math.floor(index / 2) * 2.8,
    ] as [number, number, number];
  });
  return {
    areas,
    positions,
    width: 19.2,
    depth: depth + 4,
    corridor: 1 - depth / 2 - 1.2,
  };
}
