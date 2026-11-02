/**
 * One white signal's schedule and its path, pure: no page, no store. The
 * contract's one timing table (`UI_CONTRACT.timing`) decides both what is
 * drawn at each moment (UiPipelineLayer.tsx) and when the request is
 * answered and each lifecycle event is reported (uiAttention.ts), so the
 * drawing, the orb's motion and the acknowledgement cannot drift apart.
 *
 *   0 .. emitMs              the orb emits white; nothing travels yet
 *   emitMs .. arrivalAt      the first pulse runs the whole route, drawing
 *                            the line behind it, in travelMs of movement;
 *                            at each box it passes into on its way (a
 *                            panel, a tile, a tab strip: uiPipeline.ts
 *                            `marks`) it stops for stopMs while that box's
 *                            border is marked; two more pulses follow it,
 *                            pulseOffsetMs apart, stopping at the same
 *                            borders
 *   arrivalAt                arrival: rings start, the contour appears, the
 *                            request is answered (arrivalAtMs, plus stopMs
 *                            for each box)
 *   .. holdUntil             the route stays connected and lit
 *   .. resetBy               route and contour fade; then clear
 *
 * A longer explanation extends the hold (at most maxExtraHoldMs), never the
 * travel. Its imports name their `.ts` extension so node can run it as it
 * is (tests/ui_signal_cases.mts).
 */

import { UI_CONTRACT } from "./uiContract.ts";

export const TIMING = UI_CONTRACT.timing;

/** How long one arrival ring takes to grow and fade (the design's
 *  "Arrive · 700 ms"); the second starts ringGapMs after the first. */
export const RING_MS = TIMING.ringMs;

export type Point = readonly [number, number];

export interface Schedule {
  /** How far along the route (0 the orb, 1 the target) each box the
   *  signal stops at lies, in the order it reaches them. */
  stops: readonly number[];
  arrivalAt: number;
  holdUntil: number;
  resetBy: number;
}

/** The schedule for a cue whose route stops at `stops` (fractions along
 *  it) and whose hold is extended by `extraHoldMs`. Every stop delays the
 *  arrival, the hold and the reset alike by stopMs. */
export function scheduleFor(extraHoldMs = 0, stops: readonly number[] = []): Schedule {
  const extra = Math.max(0, Math.min(extraHoldMs, TIMING.maxExtraHoldMs));
  const paused = stops.length * TIMING.stopMs;
  return { stops: [...stops].sort((a, b) => a - b), arrivalAt: TIMING.arrivalAtMs + paused,
           holdUntil: TIMING.holdUntilMs + paused + extra, resetBy: TIMING.resetByMs + paused + extra };
}

export type Phase = "emitting" | "travelling" | "holding" | "fading" | "done";

/** Where a moving cue is, `elapsed` ms after emission began. Arrival is the
 *  instant travelling ends (elapsed reaches the schedule's arrivalAt). */
export function phaseAt(elapsed: number, s: Schedule): Phase {
  if (elapsed < TIMING.emitMs) return "emitting";
  if (elapsed < s.arrivalAt) return "travelling";
  if (elapsed < s.holdUntil) return "holding";
  if (elapsed < s.resetBy) return "fading";
  return "done";
}

/** How far along the route (0..1) a pulse is `moving` ms after it left
 *  the orb: at an even pace over travelMs of movement, standing still for
 *  stopMs at each of the schedule's stops. */
export function along(moving: number, s: Schedule): number {
  let at = 0;
  let left = Math.max(0, moving);
  for (const stop of s.stops) {
    const need = (stop - at) * TIMING.travelMs;
    if (left <= need) return at + left / TIMING.travelMs;
    left -= need;
    at = stop;
    if (left <= TIMING.stopMs) return at;
    left -= TIMING.stopMs;
  }
  return Math.min(1, at + left / TIMING.travelMs);
}

/** How much of the route is drawn (0..1): nothing before the orb emits,
 *  then up to the first pulse, so the line grows behind it; all of it
 *  once it has arrived, and at once when still. */
export function drawnTo(elapsed: number, s: Schedule, still: boolean): number {
  if (still) return 1;
  return elapsed < TIMING.emitMs ? 0 : along(elapsed - TIMING.emitMs, s);
}

/** How far along the route (0 the orb, 1 the target) each pulse still on
 *  it is. A pulse leaves the orb at emitMs + i * pulseOffsetMs (along()). */
export function pulseFractions(elapsed: number, s: Schedule): number[] {
  const out: number[] = [];
  for (let i = 0; i < TIMING.pulseCount; i++) {
    const moving = elapsed - TIMING.emitMs - i * TIMING.pulseOffsetMs;
    const p = along(moving, s);
    if (moving > 0 && p < 1) out.push(p);
  }
  return out;
}

/** Each box mark's progress (0..1) while it shows, in the schedule's stop
 *  order; null when not. A box is marked from the moment the first pulse
 *  reaches its border, for boxMarkMs. */
export function markProgress(elapsed: number, s: Schedule): (number | null)[] {
  return s.stops.map((stop, k) => {
    const t = (elapsed - TIMING.emitMs - stop * TIMING.travelMs - k * TIMING.stopMs) / TIMING.boxMarkMs;
    return t >= 0 && t < 1 ? t : null;
  });
}

/** Each arrival ring's progress (0..1) while it shows; null when not. */
export function ringProgress(elapsed: number, s: Schedule): (number | null)[] {
  return [0, 1].map((i) => {
    const t = (elapsed - s.arrivalAt - i * TIMING.ringGapMs) / RING_MS;
    return t > 0 && t < 1 ? t : null;
  });
}

/** The cue's opacity: whole until the hold ends, then down to nothing by
 *  resetBy. */
export function fadeAt(elapsed: number, s: Schedule): number {
  if (elapsed <= s.holdUntil) return 1;
  return Math.max(0, 1 - (elapsed - s.holdUntil) / (s.resetBy - s.holdUntil));
}

/** The orb's white emission for a cue: rising over the emission, held with
 *  the route, and easing out with the route's own fade. */
export function orbEmission(s: Schedule): { seconds: number; riseSeconds: number; fadeSeconds: number } {
  return { seconds: s.resetBy / 1000, riseSeconds: TIMING.emitMs / 1000,
           fadeSeconds: (s.resetBy - s.holdUntil) / 1000 };
}

// ── a route's path ──────────────────────────────────────────────────────

/** Points joined into one run, without repeats. */
export function joined(runs: readonly (readonly Point[])[]): Point[] {
  const out: Point[] = [];
  for (const run of runs) {
    for (const p of run) {
      const last = out[out.length - 1];
      if (!last || last[0] !== p[0] || last[1] !== p[1]) out.push(p);
    }
  }
  return out;
}

/** How far back from a right-angled turn its 45-degree bevel starts, on
 *  each leg: every turn the pipeline takes is cut this way, never square.
 *  The corridor kept along an object's edge (uiPipeline.ts CORRIDOR_PX)
 *  is widened by it, so a bevel never reaches content. */
export const BEVEL_PX = 6;

/** The unit step from `a` to `b` along one axis, or null when the run is
 *  not straight across or down (or has no length). */
function axisStep(a: Point, b: Point): Point | null {
  const dx = b[0] - a[0], dy = b[1] - a[1];
  if (dx === 0 && dy !== 0) return [0, Math.sign(dy)];
  if (dy === 0 && dx !== 0) return [Math.sign(dx), 0];
  return null;
}

const run = (a: Point, b: Point): number => Math.hypot(b[0] - a[0], b[1] - a[1]);

/** Runs that follow one another, with every right-angled turn between and
 *  inside them cut by a 45-degree bevel of BEVEL_PX (less when a leg is
 *  too short: half a leg another turn shares, the whole of an end leg).
 *  Each run keeps its own points and the runs still meet end to start; at
 *  a turn where two runs meet, the bevel belongs to the later one. A turn
 *  already bevelled is left as it is. */
export function bevelled(runs: readonly (readonly Point[])[]): Point[][] {
  const flat: Point[] = [];
  const owned = runs.map((r) => r.map((p) => {
    const last = flat[flat.length - 1];
    if (!last || last[0] !== p[0] || last[1] !== p[1]) flat.push(p);
    return flat.length - 1;
  }));
  const end = flat.length - 1;
  const cut = flat.map((p, i): [Point, Point] | null => {
    if (i === 0 || i === end) return null;
    const into = axisStep(flat[i - 1], p), out = axisStep(p, flat[i + 1]);
    if (!into || !out || into[0] * out[0] + into[1] * out[1] !== 0) return null;
    const c = Math.min(BEVEL_PX, run(flat[i - 1], p) / (i - 1 > 0 ? 2 : 1), run(p, flat[i + 1]) / (i + 1 < end ? 2 : 1));
    return [[p[0] - c * into[0], p[1] - c * into[1]], [p[0] + c * out[0], p[1] + c * out[1]]];
  });
  return owned.map((idx) => {
    const out: Point[] = [];
    const add = (p: Point) => {
      const last = out[out.length - 1];
      if (!last || last[0] !== p[0] || last[1] !== p[1]) out.push(p);
    };
    idx.forEach((k, j) => {
      const at = cut[k];
      if (!at) add(flat[k]);
      else if (j === idx.length - 1 && idx.length > 1) add(at[0]);         // the next run takes the bevel
      else { add(at[0]); add(at[1]); }
    });
    return out;
  });
}

/** Length along a route's runs, bevels included. */
export function length(points: readonly Point[]): number {
  let n = 0;
  for (let i = 1; i < points.length; i++) n += run(points[i - 1], points[i]);
  return n;
}

/** The point `fraction` of the way along `points` (0 the orb, 1 the end). */
export function pointAlong(points: readonly Point[], fraction: number): Point {
  let left = Math.max(0, Math.min(1, fraction)) * length(points);
  for (let i = 1; i < points.length; i++) {
    const [a, b] = [points[i - 1], points[i]];
    const r = run(a, b);
    if (left <= r && r > 0) {
      const t = left / r;
      return [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t];
    }
    left -= r;
  }
  return points[points.length - 1];
}

/** The part of `points` from its start to `distance` along it. */
export function upTo(points: readonly Point[], distance: number): Point[] {
  const out: Point[] = points.length ? [points[0]] : [];
  let left = distance;
  for (let i = 1; i < points.length && left > 0; i++) {
    const [a, b] = [points[i - 1], points[i]];
    const r = run(a, b);
    if (left >= r) { out.push(b); left -= r; continue; }
    const t = left / r;
    out.push([a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t]);
    break;
  }
  return out;
}

/** Which way a route leaves its start, on the screen: the orb leans that
 *  way as it emits, as it does for a message's ray. */
export function leaving(points: readonly Point[]): [number, number] {
  for (let i = 1; i < points.length; i++) {
    const dx = Math.sign(points[i][0] - points[0][0]), dy = Math.sign(points[i][1] - points[0][1]);
    if (dx || dy) return dy ? [0, dy] : [dx, 0];
  }
  return [0, -1];
}

export const pathOf = (points: readonly Point[]): string =>
  points.map((p, i) => `${i ? "L" : "M"} ${p[0].toFixed(1)} ${p[1].toFixed(1)}`).join(" ");
