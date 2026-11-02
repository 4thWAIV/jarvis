/**
 * The pipeline map: a graph of named junctions and edges, from the main
 * orb up to the top pipeline, along it to one rail per region, along that
 * rail to a registered object's socket, and through the object's internal
 * branch to one of its parts. The fixed topology is the contract's
 * (`UI_CONTRACT.pipeline`); a mounted target (uiRegistry.ts) adds its own
 * rail tap, socket branch and part leaves, named after its presentation.
 *
 * Where each rail runs, always in a corridor kept clear for it:
 *
 *   - left:    down the middle of the lane at the page's left edge;
 *   - main:    down the gutter between the centre views and the right column;
 *   - right:   down the right-hand lane's channel, the one the rays use
 *              (rayGeometry.ts `rightChannelX`), so both share one line;
 *   - footer:  across the gap kept above the footer, from the left rail to
 *              the right one, reached down the left rail;
 *   - overlay: while a modal is open, down the margin just outside its
 *              left edge, drawn in the modal's own layer (host "modal").
 *
 * A route is found by walking registered edges only. Geometry is a function
 * of the layout now (measured, never stored), so a resize or a scroll moves
 * the points and never the topology. A socket enters its object at the
 * first point on its facing edge clear of the object's controls. A leaf
 * whose straight run would cross a control or another part goes over or
 * under the part's row through the gap beside it and drops into the part's
 * top or bottom edge. A branch that would cross another object, a leaf
 * with no clear way over or under, a junction that cannot be measured, or
 * a source the page covers, makes the route unavailable rather than
 * inventing a shortcut. Every turn a route
 * takes, between edges or inside one, is a 45-degree bevel of BEVEL_PX
 * (uiSignal.ts bevelled()), drawn and travelled alike.
 */

import { rayStart, rightChannelX, TOP_LANE_PX } from "./rayGeometry";
import { lanes } from "./transferEndpoints";
import { elementOf, mountedTargets, presentationId, visibilityOf, type VisualTarget } from "./uiRegistry";
import { BEVEL_PX, bevelled, joined, length, type Point } from "./uiSignal";
import {
  UI_CONTRACT, type AvailabilityReason, type JunctionId, type RailId, type TargetRef, type Visibility,
} from "./uiContract";

export type { Point };

/** Which layer an edge is drawn in: the page's own (under the side
 *  columns, with the rays), or an open modal's, above it. */
export type Host = "base" | "modal";

export interface RouteEdge {
  id: string;
  /** Static topology, a rail or a rail's tap, an object's socket branch,
   *  or a part's leaf inside the object (drawn above it). */
  layer: "map" | "rail" | "branch" | "leaf";
  host: Host;
  points: Point[];
}

export interface Route { edges: RouteEdge[]; end: Point; socket: Point }

export type RouteOutcome = { route: Route } | { route: null; reason: AvailabilityReason };

const PIPE = UI_CONTRACT.pipeline;

/** How far inside an object its internal margin runs. */
export const INNER_MARGIN_PX = 6;
/** How far below an object's top its socket enters, at the least. */
const SOCKET_DROP_PX = 14;
/** The gap a socket or leaf keeps from a control it passes. */
const CLEAR_PX = 4;
/** The corridor along an object's facing edge: its inner margin, the gap
 *  kept from anything beside it, and room for a leaf's bevel as it turns
 *  in from the margin (uiSignal.ts BEVEL_PX). */
export const CORRIDOR_PX = INNER_MARGIN_PX + CLEAR_PX + BEVEL_PX;
/** How far outside a part its leaf ends. */
const LEAF_END_PX = 3;
/** How far outside an open modal its rail runs. */
const OVERLAY_GAP_PX = 8;

/** What a route may not run across inside an object: anything a person
 *  presses or types in. */
const RESERVED = "button, a[href], input, select, textarea, summary, [role='button'], [role='tab'], "
  + "[role='link'], [contenteditable='true']";

const MODAL_VIEWS = new Set<string>(UI_CONTRACT.views.filter((v) => v.kind === "modal").map((v) => v.id));

// ── rails ───────────────────────────────────────────────────────────────

const rect = (id: string) => document.getElementById(id)?.getBoundingClientRect() ?? null;

/** A rail now: down at x = `at` from `from` to `to`, or across at y = `at`. */
interface RailLine { axis: "down" | "across"; at: number; from: number; to: number; host: Host }

/** The y of the corridor kept above the footer, where the footer rail runs
 *  and every downward rail ends. */
function footerY(): number {
  const footer = rect("footer");
  return (footer ? footer.top : window.innerHeight) - 4;
}

/** The open modal's surface (an overlay target of a modal view that is on
 *  show), or null. */
function openModal(): DOMRect | null {
  for (const t of mountedTargets()) {
    if (!MODAL_VIEWS.has(t.ref.view)) continue;
    const el = t.element();
    if (el && visibilityOf(el) === "visible") return el.getBoundingClientRect();
  }
  return null;
}

function railLine(rail: RailId): RailLine | null {
  const bottom = footerY();
  switch (rail) {
    case "rail.left": {
      const left = rect("left-column");
      return left ? { axis: "down", at: left.left / 2, from: TOP_LANE_PX, to: bottom, host: "base" } : null;
    }
    case "rail.main": {
      const column = rect("right-column");
      return column ? { axis: "down", at: column.left - INNER_MARGIN_PX, from: TOP_LANE_PX, to: bottom, host: "base" }
                    : null;
    }
    case "rail.right": {
      const at = lanes();
      return at ? { axis: "down", at: rightChannelX(at.column, at.menuRight, at.width), from: TOP_LANE_PX,
                    to: bottom, host: "base" } : null;
    }
    case "rail.footer": {
      const left = railLine("rail.left"), right = railLine("rail.right");
      return left && right ? { axis: "across", at: bottom, from: left.at, to: right.at, host: "base" } : null;
    }
    case "rail.overlay": {
      const modal = openModal();
      if (!modal || modal.left - OVERLAY_GAP_PX <= 0) return null;
      return { axis: "down", at: modal.left - OVERLAY_GAP_PX, from: TOP_LANE_PX, to: modal.bottom, host: "modal" };
    }
    default:
      return null;
  }
}

const railStart = (r: RailLine): Point => r.axis === "down" ? [r.at, r.from] : [r.from, r.at];
const railEnd = (r: RailLine): Point => r.axis === "down" ? [r.at, r.to] : [r.to, r.at];

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
  const line = rail ? railLine(rail) : null;
  return line ? railStart(line) : null;
}

/** Points from `a` to `b`: across first when they share neither
 *  coordinate (the top pipeline runs across, rails run down), the turn
 *  bevelled at 45 degrees. */
function corner(a: Point, b: Point): Point[] {
  if (a[0] === b[0] || a[1] === b[1]) return [a, b];
  return bevelled([[a, [b[0], a[1]], b]])[0];
}

/** Edges with every turn between and inside them bevelled (bevelled()),
 *  each keeping its id, layer and host. */
function bevelEdges(edges: RouteEdge[]): RouteEdge[] {
  const runs = bevelled(edges.map((e) => e.points));
  return edges.map((e, i) => ({ ...e, points: runs[i] }));
}

// ── the fixed map ───────────────────────────────────────────────────────

/** The fixed topology's edges and rails, measured; one that cannot be is
 *  left out (an overlay rail with no modal open, for one). */
export function mapEdges(): RouteEdge[] {
  const out: RouteEdge[] = [];
  for (const e of PIPE.edges) {
    const a = junctionPoint(e.from as JunctionId), b = junctionPoint(e.to as JunctionId);
    if (a && b) out.push({ id: e.id, layer: "map", host: "base", points: corner(a, b) });
  }
  for (const r of PIPE.rails) {
    const line = railLine(r.id as RailId);
    if (line) out.push({ id: r.id, layer: "rail", host: line.host, points: [railStart(line), railEnd(line)] });
  }
  return out;
}

/** The static edges from the orb to one rail's top, in order (a walk of
 *  the declared graph; null when the rail is not reachable). */
export function trunkTo(rail: RailId): string[] | null {
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

const hostOf = (target: VisualTarget): Host => target.socket.rail === "rail.overlay" ? "modal" : "base";

function boxOf(el: Element | null): DOMRect | null {
  if (!el) return null;
  const r = el.getBoundingClientRect();
  return r.width === 0 && r.height === 0 ? null : r;
}

/** The controls inside `el` a route keeps clear of, on show; never the
 *  addressed part `except` itself or a control inside it. */
function reservedIn(el: Element, except?: Element): DOMRect[] {
  const out: DOMRect[] = [];
  el.querySelectorAll(RESERVED).forEach((c) => {
    if (except && except.contains(c)) return;
    const r = boxOf(c);
    if (r) out.push(r);
  });
  return out;
}

/** Where a target's socket enters it now, or null: on its facing edge, at
 *  least SOCKET_DROP_PX below its top and stepped down past any control
 *  that reaches into the corridor along that edge (a close button). Null
 *  when no clear point is left on the edge. */
export function socketPoint(target: VisualTarget): Point | null {
  const el = target.element();
  const r = boxOf(el);
  if (!el || !r) return null;
  const right = target.socket.side === "right";
  const x = right ? r.right : r.left;
  const inCorridor = reservedIn(el)
    .filter((c) => right ? c.right > r.right - CORRIDOR_PX : c.left < r.left + CORRIDOR_PX)
    .sort((a, b) => a.top - b.top);
  let y = r.top + Math.min(SOCKET_DROP_PX, r.height / 2);
  for (const c of inCorridor) {
    if (y >= c.top - CLEAR_PX && y <= c.bottom + CLEAR_PX) y = c.bottom + CLEAR_PX;
  }
  return y < r.bottom - 1 ? [x, y] : null;
}

/** The tap along the rail and the branch into the socket; null when the
 *  rail or the socket cannot be measured, or the socket is beyond the
 *  rail's reach. */
function socketEdges(target: VisualTarget): RouteEdge[] | null {
  const line = railLine(target.socket.rail);
  const entry = socketPoint(target);
  if (!line || !entry) return null;
  const id = presentationId(target.ref);
  const host = hostOf(target);
  const start = railStart(line);
  if (line.axis === "down") {
    if (entry[1] < line.from || entry[1] > line.to) return null;
    return bevelEdges([
      { id: `${target.socket.rail}:tap:${id}`, layer: "rail", host, points: [start, [line.at, entry[1]]] },
      { id: `socket:${id}`, layer: "branch", host, points: [[line.at, entry[1]], entry] },
    ]);
  }
  // Across (the footer): along the rail to just outside the socket, then
  // to the socket's height and in.
  const cx = entry[0] + (target.socket.side === "right" ? INNER_MARGIN_PX : -INNER_MARGIN_PX);
  if (cx < Math.min(line.from, line.to) || cx > Math.max(line.from, line.to)) return null;
  return bevelEdges([
    { id: `${target.socket.rail}:tap:${id}`, layer: "rail", host, points: [start, [cx, line.at]] },
    { id: `socket:${id}`, layer: "branch", host, points: [[cx, line.at], [cx, entry[1]], entry] },
  ]);
}

/** Whether a horizontal or vertical run crosses a box. */
function crosses(a: Point, b: Point, r: DOMRect): boolean {
  const [x1, x2] = [Math.min(a[0], b[0]), Math.max(a[0], b[0])];
  const [y1, y2] = [Math.min(a[1], b[1]), Math.max(a[1], b[1])];
  return x1 < r.right && x2 > r.left && y1 < r.bottom && y2 > r.top;
}

function edgeCrosses(edge: RouteEdge, boxes: DOMRect[]): boolean {
  for (const r of boxes) {
    for (let i = 1; i < edge.points.length; i++) {
      if (crosses(edge.points[i - 1], edge.points[i], r)) return true;
    }
  }
  return false;
}

/** A branch may not cross another object on its way in. */
function crossesAnotherObject(edge: RouteEdge, target: VisualTarget): boolean {
  const own = target.element();
  const others: DOMRect[] = [];
  for (const other of mountedTargets()) {
    if (other === target) continue;
    const el = other.element();
    if (!el || el.contains(own) || own?.contains(el) || visibilityOf(el) === "hidden") continue;
    const r = boxOf(el);
    if (r) others.push(r);
  }
  return edgeCrosses(edge, others);
}

/** Every line of text inside `el` but outside the addressed part `except`:
 *  the labels a detour over or under a row keeps clear of. */
function labelsIn(el: Element, except: Element): DOMRect[] {
  const out: DOMRect[] = [];
  const walk = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
  const range = document.createRange();
  for (let n = walk.nextNode(); n; n = walk.nextNode()) {
    if (!n.textContent?.trim() || except.contains(n)) continue;
    range.selectNodeContents(n);
    for (const r of range.getClientRects()) if (r.width > 0 && r.height > 0) out.push(r);
  }
  return out;
}

/** The leaf over (`up`) or under the part's row: from the socket along the
 *  inner margin to the middle of the gap between that row and the next
 *  thing beyond it (or the object's own edge), across to above or below the
 *  part's centre, and down or up into its top or bottom edge. Null when
 *  there is no such gap; the caller checks what the run crosses. Its turns
 *  are bevelled with the rest of the route. */
function detour(entry: Point, margin: number, part: DOMRect, whole: DOMRect, obstacles: DOMRect[],
                up: boolean): Point[] | null {
  const cx = part.left + part.width / 2;
  const [lo, hi] = [Math.min(margin, cx) - CLEAR_PX, Math.max(margin, cx) + CLEAR_PX];
  const inSpan = obstacles.filter((o) => o.left < hi && o.right > lo);
  // The row: the part and whatever beside it in the span shares its band.
  const row = inSpan.filter((o) => o.top < part.bottom && o.bottom > part.top);
  const rowEdge = up ? Math.min(part.top, ...row.map((o) => o.top)) : Math.max(part.bottom, ...row.map((o) => o.bottom));
  const beyond = up ? Math.max(whole.top, ...inSpan.filter((o) => o.bottom <= rowEdge).map((o) => o.bottom))
                    : Math.min(whole.bottom, ...inSpan.filter((o) => o.top >= rowEdge).map((o) => o.top));
  if (Math.abs(rowEdge - beyond) < 2 * CLEAR_PX) return null;
  const y = (rowEdge + beyond) / 2;
  const end = up ? part.top - LEAF_END_PX : part.bottom + LEAF_END_PX;
  return [entry, [margin, entry[1]], [margin, y], [cx, y], [cx, end]];
}

/** The leaf from the socket down the object's inner margin to the part's
 *  facing edge. Where that straight run would cross a control or another
 *  of the object's parts (a part beside others in one row), the leaf goes
 *  over or under the row instead (detour()), keeping clear of every
 *  control, part and label; the shorter of the two that clear. "no-adapter"
 *  when the part has no element on show; "no-route" when neither side
 *  clears (never across a label to reach a value). */
function leafEdge(target: VisualTarget, part: string, entry: Point): RouteEdge | AvailabilityReason {
  const el = elementOf(target, part);
  const r = boxOf(el);
  if (!el || !r) return "no-adapter";
  const right = target.socket.side === "right";
  const margin = entry[0] + (right ? -INNER_MARGIN_PX : INNER_MARGIN_PX);
  const leaf = (points: Point[]): RouteEdge => ({ id: `leaf:${presentationId(target.ref)}:${part}`, layer: "leaf",
                                                  host: hostOf(target), points });
  const straight = leaf(r.left < margin && margin < r.right
    // The part reaches over the margin (a part beside its whole, at its
    // edge): down or up the margin into its top or bottom edge.
    ? [entry, [margin, entry[1]],
       [margin, entry[1] < r.top ? r.top - LEAF_END_PX : Math.min(entry[1], r.bottom + LEAF_END_PX)]]
    : [entry, [margin, entry[1]], [margin, r.top + r.height / 2],
       [right ? Math.min(r.right + LEAF_END_PX, margin) : Math.max(r.left - LEAF_END_PX, margin), r.top + r.height / 2]]);
  const whole = target.element();
  const kept: DOMRect[] = whole ? reservedIn(whole, el) : [];
  const decl = (UI_CONTRACT.kinds.find((k) => k.kind === target.ref.kind)?.parts ?? []) as readonly string[];
  for (const other of decl) {
    if (other === part) continue;
    const o = elementOf(target, other);
    // The whole itself (a row registered as its own `row` part) holds the
    // leaf; it is never in the way.
    if (!o || o === whole || o.contains(el) || el.contains(o)) continue;
    const box = boxOf(o);
    if (box) kept.push(box);
  }
  if (!edgeCrosses(straight, kept)) return straight;
  const box = boxOf(whole);
  if (!whole || !box) return "no-route";
  const obstacles = [...kept, ...labelsIn(whole, el)];
  const clear = [true, false]
    .map((up) => detour(entry, margin, r, box, obstacles, up))
    .filter((p): p is Point[] => p !== null && !edgeCrosses(leaf(p), obstacles))
    .sort((a, b) => length(a) - length(b));
  return clear.length ? leaf(clear[0]) : "no-route";
}

const SOURCE_REASON: Record<Exclude<Visibility, "visible">, AvailabilityReason> = {
  offscreen: "offscreen", hidden: "hidden", occluded: "occluded", unmounted: "not-mounted",
};

/** Whether the main orb, every route's source, can be seen now; a modal
 *  covering it blocks every route (there is never a second orb). */
export function sourceProblem(): AvailabilityReason | null {
  const seen = visibilityOf(document.getElementById("orb-control"));
  return seen === "visible" ? null : SOURCE_REASON[seen];
}

/** The route from the orb to `target` (and `part`), along registered edges
 *  only, measured now. */
export function resolveRoute(target: VisualTarget, ref: TargetRef): RouteOutcome {
  const source = sourceProblem();
  if (source) return { route: null, reason: source };
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
  if (crossesAnotherObject(socket[socket.length - 1], target)) return { route: null, reason: "no-route" };
  edges.push(...socket);
  const branch = socket[socket.length - 1].points;
  const entry = branch[branch.length - 1];
  if (!ref.part) return { route: { edges: bevelEdges(edges), end: entry, socket: entry } };
  const leaf = leafEdge(target, ref.part, entry);
  if (typeof leaf === "string") return { route: null, reason: leaf };
  edges.push(leaf);
  return { route: { edges: bevelEdges(edges), end: leaf.points[leaf.points.length - 1], socket: entry } };
}

/** The edges a route uses, in order: its topology. Geometry may move under
 *  a cue; this may not. */
export const topologyOf = (route: Route): string => route.edges.map((e) => e.id).join(" ");

/** Every mounted, visible object's socket branch, for the faint map. */
export function socketBranches(visible: (t: VisualTarget) => boolean): RouteEdge[] {
  const out: RouteEdge[] = [];
  for (const t of mountedTargets()) {
    if (!visible(t)) continue;
    const s = socketEdges(t);
    if (s) out.push(s[s.length - 1]);
  }
  return out;
}

/** A run of edges as one line. */
export const polyline = (edges: RouteEdge[]): Point[] => joined(edges.map((e) => e.points));
