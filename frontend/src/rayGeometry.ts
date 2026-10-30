/**
 * The rays' geometry, pure: no store and no page lookup (transferEndpoints.ts
 * finds the boxes; RayLane.tsx draws). A ray runs in right angles: up out of
 * the main orb's centre to the top lane, across it to the channel in the
 * middle of the right-hand lane, down the channel, and left into its end;
 * or, when the end is a tab's icon, straight down into it from the top lane.
 * Its path is always the same six numbers (`rayPoints`), whichever way the
 * message travels along it.
 */

/** A box on the screen: what getBoundingClientRect gives. */
export interface Box { left: number; top: number; width: number; height: number; right: number }

/** Where a ray leaves the orb: its centre. The ray is drawn behind the orb
 *  (lower in the page than the left column), so it comes out from inside. */
export function rayStart(orb: Box): [number, number] {
  return [orb.left + orb.width / 2, orb.top + orb.height / 2];
}

// How far below the top of the page the ray crosses: above the centre
// area's tabs (which start at --centre-header-top, 16px).
export const TOP_LANE_PX = 8;

/** The path from the orb to `target`'s centre. `toTab`: the target is a
 *  tab's icon, reached straight down from the top lane (the last run is
 *  empty, so the path keeps the same six numbers). Otherwise the channel is
 *  the middle of what is left of the right-hand lane beside the right
 *  column and the menu button (`menuRight`), so the ray never runs through
 *  the menu. */
export function rayRoute(orb: Box, target: Box, toTab: boolean, column: Box,
                         menuRight: number, viewportWidth: number): string {
  const [sx, sy] = rayStart(orb);
  const ex = target.left + target.width / 2, ey = target.top + target.height / 2;
  if (toTab) return `M ${sx} ${sy} V ${TOP_LANE_PX} H ${ex} V ${ey} H ${ex}`;
  const inner = Math.max(column.right, menuRight);
  const channel = inner + (viewportWidth - inner) / 2;
  return `M ${sx} ${sy} V ${TOP_LANE_PX} H ${channel} V ${ey} H ${ex}`;
}

/** The numbers in a ray's path: start x and y, the top lane's y, the
 *  channel's x, then the end's y and x (rayRoute). */
export function rayPoints(d: string): number[] {
  return (d.match(/-?[\d.]+/g) ?? []).map(Number);
}

/** Which way the ray leaves the orb, on the screen: from its start to its
 *  first corner. An incoming message arrives at the orb from there too. */
export function rayDirection(d: string): [number, number] {
  const [, sy, top] = rayPoints(d);
  return [0, Math.sign(top - sy) || -1];
}

/** Which way the ray arrives from at its end: from the end back to its
 *  last corner (the channel). */
export function rayArrival(d: string): [number, number] {
  const [, , , cx, , ex] = rayPoints(d);
  return [Math.sign(cx - ex) || 1, 0];
}

/** Where the ray ends: its target's centre. */
export function rayEnd(d: string): [number, number] {
  const [, , , , ey, ex] = rayPoints(d);
  return [ex, ey];
}

/** Where the ray begins: the main orb's centre. */
export function rayOrigin(d: string): [number, number] {
  const [sx, sy] = rayPoints(d);
  return [sx, sy];
}
