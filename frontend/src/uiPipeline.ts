/**
 * The pipeline map: a graph of named junctions and edges, from the main
 * orb up to the top pipeline, along it to one rail per region, down that
 * rail to a registered object's socket, and through the object's internal
 * branch to one of its parts. The fixed topology is the contract's
 * (`UI_CONTRACT.pipeline`); a mounted target (uiRegistry.ts) adds its own
 * rail tap, socket branch and part leaves, named after its presentation.
 *
 * A route is found by walking registered edges only. Geometry is a function
 * of the layout now (measured, never stored), so a resize or a scroll moves
 * the points and never the topology; a junction that cannot be measured,
 * or a branch that would cross another object, makes the route unavailable
 * rather than inventing a shortcut. Pure apart from the measuring, which
 * goes through `junctionPoint` and the targets' own element accessors.
 */

import { rayStart, TOP_LANE_PX } from "./rayGeometry";
import { elementOf, mountedTargets, presentationId, type VisualTarget } from "./uiRegistry";
import { UI_CONTRACT, type AvailabilityReason, type JunctionId, type RailId, type TargetRef } from "./uiContract";

export type Point = readonly [number, number];

export interface RouteEdge {
  id: string;
  /** Static topology, a rail tap, an object's socket branch, or a part's
   *  leaf inside the object (drawn above it). */
  layer: "map" | "rail" | "branch" | "leaf";
  points: Point[];
}

export interface Route { edges: RouteEdge[]; end: Point; socket: Point }

export type RouteOutcome = { route: Route } | { route: null; reason: AvailabilityReason };

const PIPE = UI_CONTRACT.pipeline;

/** How far inside an object its internal margin runs. */
export const INNER_MARGIN_PX = 6;
/** How far below an object's top its socket enters. */
const SOCKET_DROP_PX = 14;

// ── junctions ───────────────────────────────────────────────────────────

const rect = (id: string) => document.getElementById(id)?.getBoundingClientRect() ?? null;

/** Each rail's x on the screen now, or null while it cannot be measured.
 *  Left: the lane at the page's left edge. Main: the gutter between the
 *  centre area and the right panel. Right: the transmissions' own channel
 *  in the right-hand lane (rayGeometry.ts), so both share one lane. */
function railX(rail: RailId): number | null {
  const column = rect("right-column");
  switch (rail) {
    case "rail.left": {
      const left = rect("left-column");
      return left ? left.left / 2 : null;
    }
    case "rail.main":
      return column ? column.left - INNER_MARGIN_PX : null;
    case "rail.right": {
      if (!column) return null;
      const menu = rect("controls");
      const inner = Math.max(column.right, menu?.right ?? 0);
      return inner + (window.innerWidth - inner) / 2;
    }
    default:
      return null;     // overlay and footer rails: not laid out yet
  }
}

const RAIL_OF_TOP = new Map<string, RailId>(PIPE.rails.map((r) => [r.top, r.id as RailId]));

/** Where a junction is now, or null. */
export function junctionPoint(id: JunctionId): Point | null {
  if (id === "orb.source" || id === "trunk.orb") {
    const orb = rect("orb-control");
    if (!orb) return null;
    const [x, y] = rayStart(orb);
    return id === "orb.source" ? [x, y] : [x, TOP_LANE_PX];
  }
  const rail = RAIL_OF_TOP.get(id);
  const x = rail ? railX(rail) : null;
  return x === null ? null : [x, TOP_LANE_PX];
}

/** The bottom of every rail: just above the footer. */
function railBottom(): number {
  const footer = document.querySelector("footer, #footer")?.getBoundingClientRect();
  return (footer ? footer.top : window.innerHeight) - 2;
}

/** Right-angled points from `a` to `b`: horizontal first when they share
 *  neither coordinate (the top pipeline runs across, rails run down). */
function corner(a: Point, b: Point): Point[] {
  if (a[0] === b[0] || a[1] === b[1]) return [a, b];
  return [a, [b[0], a[1]], b];
}

// ── the fixed map ───────────────────────────────────────────────────────

/** The fixed topology's edges, measured; one that cannot be is left out. */
export function mapEdges(): RouteEdge[] {
  const out: RouteEdge[] = [];
  for (const e of PIPE.edges) {
    const a = junctionPoint(e.from as JunctionId), b = junctionPoint(e.to as JunctionId);
    if (a && b) out.push({ id: e.id, layer: "map", points: corner(a, b) });
  }
  for (const r of PIPE.rails) {
    const top = junctionPoint(r.top as JunctionId);
    if (top) out.push({ id: r.id, layer: "rail", points: [top, [top[0], railBottom()]] });
  }
  return out;
}

/** The static edges from the orb to one rail's top, in order (a walk of
 *  the declared graph; null when the rail is not reachable). */
function trunkTo(rail: RailId): string[] | null {
  const top = PIPE.rails.find((r) => r.id === rail)!.top;
  const back = new Map<string, { edge: string; from: string }>();
  const todo: string[] = [PIPE.source];
  const seen = new Set(todo);
  while (todo.length) {
    const at = todo.shift()!;
    if (at === top) break;
    for (const e of PIPE.edges) {
      if (e.from === at && !seen.has(e.to)) {
        seen.add(e.to);
        back.set(e.to, { edge: e.id, from: at });
        todo.push(e.to);
      }
    }
  }
  if (!seen.has(top)) return null;
  const path: string[] = [];
  for (let at: string = top; at !== PIPE.source; at = back.get(at)!.from) path.unshift(back.get(at)!.edge);
  return path;
}

// ── a target's socket and its parts ──────────────────────────────────────

/** Where a target's socket enters it now, or null. */
export function socketPoint(target: VisualTarget): Point | null {
  const el = target.element();
  if (!el) return null;
  const r = el.getBoundingClientRect();
  if (r.width === 0 && r.height === 0) return null;
  const x = target.socket.side === "right" ? r.right : r.left;
  return [x, r.top + Math.min(SOCKET_DROP_PX, r.height / 2)];
}

/** The tap on the rail and the branch into the socket. */
function socketEdges(target: VisualTarget): RouteEdge[] | null {
  const x = railX(target.socket.rail);
  const top = junctionPoint(PIPE.rails.find((r) => r.id === target.socket.rail)!.top as JunctionId);
  const entry = socketPoint(target);
  if (x === null || !top || !entry) return null;
  const id = presentationId(target.ref);
  return [
    { id: `${target.socket.rail}:tap:${id}`, layer: "rail", points: [top, [x, entry[1]]] },
    { id: `socket:${id}`, layer: "branch", points: [[x, entry[1]], entry] },
  ];
}

/** The leaf from the socket down the object's inner margin to the part's
 *  facing edge, never across the part's own label. */
function leafEdge(target: VisualTarget, part: string, entry: Point): RouteEdge | null {
  const el = elementOf(target, part);
  if (!el) return null;
  const r = el.getBoundingClientRect();
  if (r.width === 0 && r.height === 0) return null;
  const right = target.socket.side === "right";
  const margin = entry[0] + (right ? -INNER_MARGIN_PX : INNER_MARGIN_PX);
  const y = r.top + r.height / 2;
  const edge = right ? r.right + 3 : r.left - 3;
  return { id: `leaf:${presentationId(target.ref)}:${part}`, layer: "leaf",
           points: [entry, [margin, entry[1]], [margin, y], [edge, y]] };
}

/** Whether a horizontal or vertical run crosses a box. */
function crosses(a: Point, b: Point, r: DOMRect): boolean {
  const [x1, x2] = [Math.min(a[0], b[0]), Math.max(a[0], b[0])];
  const [y1, y2] = [Math.min(a[1], b[1]), Math.max(a[1], b[1])];
  return x1 < r.right && x2 > r.left && y1 < r.bottom && y2 > r.top;
}

/** A branch may not cross another object on its way in. */
function crossesAnotherObject(edge: RouteEdge, target: VisualTarget): boolean {
  for (const other of mountedTargets()) {
    if (other === target) continue;
    const el = other.element();
    if (!el || el.contains(target.element()) || target.element()?.contains(el)) continue;
    const r = el.getBoundingClientRect();
    for (let i = 1; i < edge.points.length; i++) {
      if (crosses(edge.points[i - 1], edge.points[i], r)) return true;
    }
  }
  return false;
}

/** The route from the orb to `target` (and `part`), along registered edges
 *  only, measured now. */
export function resolveRoute(target: VisualTarget, ref: TargetRef): RouteOutcome {
  const trunk = trunkTo(target.socket.rail);
  if (!trunk) return { route: null, reason: "no-route" };
  const byId = new Map(mapEdges().map((e) => [e.id, e]));
  const edges: RouteEdge[] = [];
  for (const id of trunk) {
    const e = byId.get(id);
    if (!e) return { route: null, reason: "no-route" };
    edges.push(e);
  }
  const socket = socketEdges(target);
  if (!socket) return { route: null, reason: "no-route" };
  if (crossesAnotherObject(socket[1], target)) return { route: null, reason: "no-route" };
  edges.push(...socket);
  const entry = socket[1].points[1];
  if (!ref.part) return { route: { edges, end: entry, socket: entry } };
  const leaf = leafEdge(target, ref.part, entry);
  if (!leaf) return { route: null, reason: "no-adapter" };
  edges.push(leaf);
  return { route: { edges, end: leaf.points[leaf.points.length - 1], socket: entry } };
}

/** Every mounted, visible object's socket branch, for the faint map. */
export function socketBranches(visible: (t: VisualTarget) => boolean): RouteEdge[] {
  const out: RouteEdge[] = [];
  for (const t of mountedTargets()) {
    if (!visible(t)) continue;
    const s = socketEdges(t);
    if (s) out.push(s[1]);
  }
  return out;
}

// ── travelling along a route ─────────────────────────────────────────────

/** A route's points, joined, without repeats. */
export function polyline(edges: RouteEdge[]): Point[] {
  const out: Point[] = [];
  for (const e of edges) {
    for (const p of e.points) {
      const last = out[out.length - 1];
      if (!last || last[0] !== p[0] || last[1] !== p[1]) out.push(p);
    }
  }
  return out;
}

export function length(points: Point[]): number {
  let n = 0;
  for (let i = 1; i < points.length; i++) {
    n += Math.abs(points[i][0] - points[i - 1][0]) + Math.abs(points[i][1] - points[i - 1][1]);
  }
  return n;
}

/** The point `fraction` of the way along `points` (0 the orb, 1 the end). */
export function pointAlong(points: Point[], fraction: number): Point {
  let left = Math.max(0, Math.min(1, fraction)) * length(points);
  for (let i = 1; i < points.length; i++) {
    const [a, b] = [points[i - 1], points[i]];
    const run = Math.abs(b[0] - a[0]) + Math.abs(b[1] - a[1]);
    if (left <= run && run > 0) {
      const t = left / run;
      return [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t];
    }
    left -= run;
  }
  return points[points.length - 1];
}

export const pathOf = (points: Point[]): string =>
  points.map((p, i) => `${i ? "L" : "M"} ${p[0].toFixed(1)} ${p[1].toFixed(1)}`).join(" ");
