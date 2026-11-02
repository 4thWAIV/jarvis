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
 * The primary channel and where a route leaves it. A target's primary
 * channel is the declared trunk from the orb to its region's rail (the
 * orb's rise, the top pipeline and, for the right, the run along it past
 * the main rail's top) followed by that rail down its corridor. A route
 * stays on it to the latest branch point that still reaches the target
 * (lateBranch()): level with the target (a part's middle, or an object's),
 * straight in to its facing edge, when that run crosses no line of text,
 * no control and no other object; otherwise level with the lane beside the
 * neighbouring row (over or under the target's row, lane()), across in
 * that lane, past the row before or after it, and down or up into the
 * target's top or bottom edge. Only when neither is clear does the route
 * leave earlier, by the object's socket near its top and a leaf down its
 * inner margin, as described next.
 *
 * Boxes on the way. Every box that holds the target is a waypoint: the
 * registered boxes (uiRegistry.ts BOX_IDS: the right panel's body, a tab
 * strip, the phase stepper), every other target holding it (a tile, a
 * centre view, a workflow's detail) and, for a part, its own object
 * (boxesAround()). Where the route passes into each of them, outermost
 * first, it marks that box's border and stops there a moment (`marks`,
 * the stops of the schedule in uiSignal.ts), then runs on from that border
 * toward the control inside.
 *
 * A route is found by walking registered edges only. Geometry is a function
 * of the layout now (measured, never stored), so a resize or a scroll moves
 * the points and never the topology. A socket enters its object at the
 * first point on its facing edge clear of the object's controls. A leaf
 * whose straight run would cross a control or another part goes over or
 * under the part's row through the gap beside it and drops into the part's
 * top or bottom edge; a socket branch that would cross the next object in
 * its row (a phase step) goes over or under the row the same way and drops
 * into the object's top or bottom edge at its inner margin. A branch that
 * still crosses another object, a leaf
 * with no clear way over or under, a junction that cannot be measured, or
 * a source the page covers, makes the route unavailable rather than
 * inventing a shortcut. Every turn a route
 * takes, between edges or inside one, is a 45-degree bevel of BEVEL_PX
 * (uiSignal.ts bevelled()), drawn and travelled alike.
 */

import { rayStart, rightChannelX, TOP_LANE_PX } from "./rayGeometry";
import { lanes } from "./transferEndpoints";
import {
  elementOf, mountedTargets, presentationId, registeredBoxes, undrawn, visibilityOf, type VisualTarget,
} from "./uiRegistry";
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

/** A box the route passes into on its way, marked as the signal reaches
 *  its border: `at` is how far along the route that is (0 the orb, 1 the
 *  end). */
export interface BoxMark { id: string; box: DOMRect; at: number; host: Host }

export interface Route { edges: RouteEdge[]; end: Point; socket: Point; marks: BoxMark[] }

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

/** An element's box, or null when it has none or is not drawn
 *  (visibility: hidden): neither a target nor in a route's way. */
function boxOf(el: Element | null): DOMRect | null {
  if (!el) return null;
  const r = el.getBoundingClientRect();
  return (r.width === 0 && r.height === 0) || undrawn(el) ? null : r;
}

/** The controls inside `el` a route keeps clear of, on show; never the
 *  addressed part `except` itself, a control inside it, or one holding it
 *  (a box the route passes into). */
function reservedIn(el: Element, except?: Element): DOMRect[] {
  const out: DOMRect[] = [];
  el.querySelectorAll(RESERVED).forEach((c) => {
    if (except && (except.contains(c) || c.contains(except))) return;
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
    const branch = (points: Point[]): RouteEdge => ({ id: `socket:${id}`, layer: "branch", host, points });
    const straight = branch([[line.at, entry[1]], entry]);
    // Straight in unless that crosses another object (a step beside the
    // next in a row): then over or under the row (overTheRow()); with
    // neither clear, the straight branch stays and the route is refused.
    const over = crossesAnotherObject(straight, target) ? overTheRow(target, line.at) : null;
    const points = over ?? straight.points;
    return bevelEdges([
      { id: `${target.socket.rail}:tap:${id}`, layer: "rail", host, points: [start, points[0]] },
      branch(points),
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

/** The boxes of the other objects on show beside `target`: neither inside
 *  it nor holding it. */
function objectsBeside(target: VisualTarget): DOMRect[] {
  const own = target.element();
  const others: DOMRect[] = [];
  for (const other of mountedTargets()) {
    if (other === target) continue;
    const el = other.element();
    if (!el || el.contains(own) || own?.contains(el) || visibilityOf(el) === "hidden") continue;
    const r = boxOf(el);
    if (r) others.push(r);
  }
  return others;
}

/** A branch may not cross another object on its way in. */
function crossesAnotherObject(edge: RouteEdge, target: VisualTarget): boolean {
  return edgeCrosses(edge, objectsBeside(target));
}

/** The innermost other object on show that holds `target` (a workflow's
 *  detail holding its phase steps), or null. */
function holderOf(target: VisualTarget): Element | null {
  const own = target.element();
  let best: Element | null = null;
  for (const other of mountedTargets()) {
    const el = other.element();
    if (other === target || !el || !own || el === own || !el.contains(own)) continue;
    if (!best || best.contains(el)) best = el;
  }
  return best;
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

/** The y of the lane over (`up`) or under `part`'s row, for a run across
 *  from x `a` to x `b`: the middle of the gap between that row (the part
 *  and whatever in the run's span shares its band) and the next thing
 *  beyond it, or `whole`'s own edge. Null when the gap is narrower than
 *  twice CLEAR_PX. */
function lane(part: DOMRect, whole: DOMRect, obstacles: DOMRect[], a: number, b: number, up: boolean): number | null {
  const [lo, hi] = [Math.min(a, b) - CLEAR_PX, Math.max(a, b) + CLEAR_PX];
  const inSpan = obstacles.filter((o) => o.left < hi && o.right > lo);
  const row = inSpan.filter((o) => o.top < part.bottom && o.bottom > part.top);
  const rowEdge = up ? Math.min(part.top, ...row.map((o) => o.top)) : Math.max(part.bottom, ...row.map((o) => o.bottom));
  const beyond = up ? Math.max(whole.top, ...inSpan.filter((o) => o.bottom <= rowEdge).map((o) => o.bottom))
                    : Math.min(whole.bottom, ...inSpan.filter((o) => o.top >= rowEdge).map((o) => o.top));
  return Math.abs(rowEdge - beyond) < 2 * CLEAR_PX ? null : (rowEdge + beyond) / 2;
}

/** Points with each repeat of the one before left out. */
const distinct = (points: Point[]): Point[] =>
  points.filter((p, i) => i === 0 || p[0] !== points[i - 1][0] || p[1] !== points[i - 1][1]);

/** The leaf over (`up`) or under the part's row: from the socket along the
 *  inner margin to the row's lane (lane()), across to above or below the
 *  part's centre, and down or up into its top or bottom edge. Null when
 *  there is no lane; the caller checks what the run crosses. Its turns
 *  are bevelled with the rest of the route. */
function detour(entry: Point, margin: number, part: DOMRect, whole: DOMRect, obstacles: DOMRect[],
                up: boolean): Point[] | null {
  const cx = part.left + part.width / 2;
  const y = lane(part, whole, obstacles, margin, cx, up);
  if (y === null) return null;
  const end = up ? part.top - LEAF_END_PX : part.bottom + LEAF_END_PX;
  return distinct([entry, [margin, entry[1]], [margin, y], [cx, y], [cx, end]]);
}

/** The socket branch over or under the object's row, for an object whose
 *  straight branch would cross the next one beside it (a phase step left
 *  of the last): from the rail at `railX` across the row's lane, inside
 *  the object that holds it (holderOf()), clear of the other objects and
 *  of the holder's controls and lines of text, to above or below the
 *  object's inner margin, and down or up into its top or bottom edge
 *  there; its leaves then run down or up that margin as from any socket.
 *  The shorter of the two that clear, or null. Its turns are bevelled with
 *  the rest of the route. */
function overTheRow(target: VisualTarget, railX: number): Point[] | null {
  const el = target.element(), holder = holderOf(target);
  const r = boxOf(el), whole = boxOf(holder);
  if (!el || !holder || !r || !whole) return null;
  const margin = marginOf(target, r);
  const obstacles = [...objectsBeside(target), ...reservedIn(holder, el), ...labelsIn(holder, el)];
  const ways = [true, false].flatMap((up) => {
    const y = lane(r, whole, obstacles, margin, railX, up);
    if (y === null) return [];
    const points: Point[] = [[railX, y], [margin, y], [margin, up ? r.top : r.bottom]];
    return edgeCrosses({ id: "", layer: "branch", host: "base", points }, obstacles) ? [] : [points];
  });
  return ways.sort((a, b) => length(a) - length(b))[0] ?? null;
}

/** The x of an object's inner margin: INNER_MARGIN_PX inside its facing edge. */
const marginOf = (target: VisualTarget, box: DOMRect): number =>
  target.socket.side === "right" ? box.right - INNER_MARGIN_PX : box.left + INNER_MARGIN_PX;

/** The boxes of the object's other declared parts on show, beside the
 *  addressed part `part` (element `el`): never the whole itself (a row
 *  registered as its own `row` part holds the leaf), nor a part holding
 *  `el` or inside it. */
function otherParts(target: VisualTarget, part: string, el: Element): DOMRect[] {
  const whole = target.element();
  const decl = (UI_CONTRACT.kinds.find((k) => k.kind === target.ref.kind)?.parts ?? []) as readonly string[];
  return decl.flatMap((other) => {
    const o = other === part ? null : elementOf(target, other);
    const box = o && o !== whole && !o.contains(el) && !el.contains(o) ? boxOf(o) : null;
    return box ? [box] : [];
  });
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
  const whole = target.element();
  const box = boxOf(whole);
  if (!whole || !box) return "no-route";
  const margin = marginOf(target, box);
  const leaf = (points: Point[]): RouteEdge => ({ id: `leaf:${presentationId(target.ref)}:${part}`, layer: "leaf",
                                                  host: hostOf(target), points: distinct(points) });
  const straight = leaf(r.left < margin && margin < r.right
    // The part reaches over the margin (a part beside its whole, at its
    // edge): down or up the margin into its top or bottom edge.
    ? [entry, [margin, entry[1]],
       [margin, entry[1] < r.top ? r.top - LEAF_END_PX : Math.min(entry[1], r.bottom + LEAF_END_PX)]]
    : [entry, [margin, entry[1]], [margin, r.top + r.height / 2],
       [right ? Math.min(r.right + LEAF_END_PX, margin) : Math.max(r.left - LEAF_END_PX, margin), r.top + r.height / 2]]);
  const kept: DOMRect[] = [...reservedIn(whole, el), ...otherParts(target, part, el)];
  if (!edgeCrosses(straight, kept)) return straight;
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

// ── the latest branch point, and the boxes on the way ───────────────────

/** Every box holding the target's addressed element (its part, or the
 *  whole object), outermost first: the registered boxes, every other
 *  target holding it, and, for a part, its own object. One drawn at the
 *  same place as another holding it (within a pixel or two on each side)
 *  is left out, so a border is marked once. */
export function boxesAround(target: VisualTarget, part?: string): { id: string; element: Element }[] {
  const end = part ? elementOf(target, part) : target.element();
  if (!end) return [];
  const found: { id: string; element: Element }[] = registeredBoxes().filter((b) => b.element.contains(end));
  for (const other of mountedTargets()) {
    const el = other.element();
    if (el && (other !== target || part) && el.contains(end)) found.push({ id: presentationId(other.ref), element: el });
  }
  const shown = found
    .filter((b, i) => b.element !== end && found.findIndex((o) => o.element === b.element) === i && boxOf(b.element))
    .sort((a, b) => a.element.contains(b.element) ? -1 : b.element.contains(a.element) ? 1 : 0);
  const same = (a: DOMRect, b: DOMRect) => Math.abs(a.left - b.left) <= 2 && Math.abs(a.right - b.right) <= 2
    && Math.abs(a.top - b.top) <= 2 && Math.abs(a.bottom - b.bottom) <= 2;
  return shown.filter((b, i) => !shown.slice(0, i).some((o) => same(boxOf(o.element)!, boxOf(b.element)!)));
}

/** Where along `points` (0..1) the route last passes from outside `box`
 *  into it, or null when it never does (it starts inside and stays, or
 *  never reaches it) or does so only at its last pixel. A route ends
 *  LEAF_END_PX outside its part, so a box whose border is the part's own
 *  edge (a tab strip) counts as entered within that much of it. */
function entryAlong(points: readonly Point[], box: DOMRect): number | null {
  const total = length(points);
  if (!total) return null;
  const pad = LEAF_END_PX + 1;
  const inside = (p: Point) => p[0] > box.left - pad && p[0] < box.right + pad
    && p[1] > box.top - pad && p[1] < box.bottom + pad;
  let walked = 0, entry: number | null = null;
  for (let i = 1; i < points.length; i++) {
    const [a, b] = [points[i - 1], points[i]];
    const run = length([a, b]);
    // Step along the run a pixel at a time: runs are straight or 45 degrees.
    const steps = Math.max(1, Math.ceil(run));
    let was = inside(a);
    for (let k = 1; k <= steps; k++) {
      const t = k / steps;
      const now = inside([a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t]);
      if (now && !was) entry = (walked + run * (k - 1) / steps) / total;
      was = now;
    }
    walked += run;
  }
  return entry !== null && entry * total < total - 1 ? entry : null;
}

/** The boxes a route passes into, each where it first crosses that box's
 *  border on its last way in, in the order the signal reaches them. */
function marksOn(edges: RouteEdge[], target: VisualTarget, part?: string): BoxMark[] {
  const points = polyline(edges);
  const host = hostOf(target);
  const marks: BoxMark[] = [];
  for (const b of boxesAround(target, part)) {
    const box = boxOf(b.element)!;
    const at = entryAlong(points, box);
    if (at !== null) marks.push({ id: b.id, box, at, host });
  }
  return marks.sort((a, b) => a.at - b.at);
}

/** Whether two boxes overlap. */
const meets = (a: DOMRect, b: DOMRect) => a.left < b.right && a.right > b.left && a.top < b.bottom && a.bottom > b.top;

/** Every control and line of text on show in `area`, on the whole page,
 *  but the addressed element's own (and a control holding it): what a
 *  branch from the rail keeps clear of, whichever box they are in. */
function obstaclesIn(area: DOMRect, except: Element): DOMRect[] {
  const out = reservedIn(document.body, except).filter((r) => meets(r, area));
  const walk = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  const range = document.createRange();
  for (let n = walk.nextNode(); n; n = walk.nextNode()) {
    const holder = n.parentElement;
    if (!holder || !n.textContent?.trim() || except.contains(n)) continue;
    const around = holder.getBoundingClientRect();
    if (!around.width || !around.height || !meets(around, area) || holder.closest("#ui-pipeline-status")
        || undrawn(holder)) continue;
    range.selectNodeContents(n);
    for (const r of range.getClientRects()) if (r.width > 0 && r.height > 0 && meets(r, area)) out.push(r);
  }
  return out;
}

/** What a branch from the rail at x `railX` to `el` keeps clear of: every
 *  control and line of text between the rail and it, down the height of
 *  `scope` (the outermost box it passes into), the target's other parts,
 *  and every other object beside the target. */
function obstaclesFor(target: VisualTarget, el: Element, scope: DOMRect, railX: number, part?: string): DOMRect[] {
  const r = el.getBoundingClientRect();
  const left = Math.min(railX, r.left) - CLEAR_PX, right = Math.max(railX, r.right) + CLEAR_PX;
  const area = new DOMRect(left, scope.top, right - left, scope.height);
  return [...obstaclesIn(area, el), ...objectsBeside(target), ...(part ? otherParts(target, part, el) : [])];
}

/** The route's run from its rail to the target, leaving the primary
 *  channel at the latest branch point that still reaches it: straight in
 *  at the target's own level, or, when that crosses anything, in the lane
 *  over or under its row (beside the neighbouring row) and down or up into
 *  its top or bottom edge, the shorter of the two that clear. Null when
 *  the rail is not one that runs down, or none is clear. */
function lateBranch(target: VisualTarget, part?: string): RouteEdge[] | null {
  const line = railLine(target.socket.rail);
  const el = part ? elementOf(target, part) : target.element();
  const r = boxOf(el);
  if (!line || line.axis !== "down" || !el || !r) return null;
  const right = target.socket.side === "right";
  const out = part ? LEAF_END_PX : 0;
  const faceX = right ? r.right + out : r.left - out;
  if (right ? line.at <= faceX : line.at >= faceX) return null;
  const boxes = boxesAround(target, part);
  const scopeBox = boxOf(boxes[0]?.element ?? el)!;
  const obstacles = obstaclesFor(target, el, scopeBox, line.at, part);
  const id = presentationId(target.ref);
  const host = hostOf(target);
  const fits = (y: number) => y >= line.from && y <= line.to;
  const clear = (points: Point[]) =>
    !edgeCrosses({ id: "", layer: "leaf", host, points }, obstacles);
  const level = r.top + r.height / 2;
  let points: Point[] | null = fits(level) && clear([[line.at, level], [faceX, level]])
    ? [[line.at, level], [faceX, level]] : null;
  if (!points) {
    const cx = r.left + r.width / 2;
    points = [true, false].flatMap((up): Point[][] => {
      const y = lane(r, scopeBox, obstacles, line.at, cx, up);
      if (y === null || !fits(y)) return [];
      const run: Point[] = [[line.at, y], [cx, y], [cx, up ? r.top - out : r.bottom + out]];
      return clear(run) ? [run] : [];
    }).sort((a, b) => length(a) - length(b))[0] ?? null;
  }
  if (!points) return null;
  return bevelEdges([
    { id: `${target.socket.rail}:tap:${id}`, layer: "rail", host, points: [railStart(line), points[0]] },
    { id: part ? `leaf:${id}:${part}` : `socket:${id}`, layer: part ? "leaf" : "branch", host, points },
  ]);
}

/** The route by the object's socket near its top and a leaf down its
 *  inner margin: where the latest branch point finds no clear way in. */
function socketRoute(target: VisualTarget, part?: string): { edges: RouteEdge[]; socket: Point } | AvailabilityReason {
  const socket = socketEdges(target);
  if (!socket) return "no-route";
  if (crossesAnotherObject(socket[socket.length - 1], target)) return "no-route";
  const branch = socket[socket.length - 1].points;
  const entry = branch[branch.length - 1];
  if (!part) return { edges: socket, socket: entry };
  const leaf = leafEdge(target, part, entry);
  return typeof leaf === "string" ? leaf : { edges: [...socket, leaf], socket: entry };
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
  const late = lateBranch(target, ref.part);
  let socket: Point;
  if (late) {
    edges.push(...late);
    socket = late[late.length - 1].points[late[late.length - 1].points.length - 1];
  } else {
    const early = socketRoute(target, ref.part);
    if (typeof early === "string") return { route: null, reason: early };
    edges.push(...early.edges);
    socket = early.socket;
  }
  const drawn = bevelEdges(edges);
  const last = drawn[drawn.length - 1].points;
  return { route: { edges: drawn, end: last[last.length - 1], socket, marks: marksOn(drawn, target, ref.part) } };
}

/** The edges a route uses and the boxes it marks, in order: its topology.
 *  Geometry may move under a cue; this may not. */
export const topologyOf = (route: Route): string =>
  [...route.edges.map((e) => e.id), ...route.marks.map((m) => `box:${m.id}`)].join(" ");

/** A run of edges as one line. */
export const polyline = (edges: RouteEdge[]): Point[] => joined(edges.map((e) => e.points));
