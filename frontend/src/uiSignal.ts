/**
 * One white signal's schedule and its path, pure: no page, no store. The
 * contract's one timing table (`UI_CONTRACT.timing`) decides both what is
 * drawn at each moment (UiPipelineLayer.tsx) and when the request is
 * answered and each lifecycle event is reported (uiAttention.ts), so the
 * drawing, the orb's motion and the acknowledgement cannot drift apart.
 *
 *   0 .. emitMs              the orb emits white; nothing travels yet
 *   emitMs .. arrivalAtMs    the first pulse runs the whole route; two more
 *                            follow it, pulseOffsetMs apart
 *   arrivalAtMs              arrival: rings start, the contour appears, the
 *                            request is answered
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
  holdUntil: number;
  resetBy: number;
}

/** The schedule for a cue whose hold is extended by `extraHoldMs`. */
export function scheduleFor(extraHoldMs = 0): Schedule {
  const extra = Math.max(0, Math.min(extraHoldMs, TIMING.maxExtraHoldMs));
  return { holdUntil: TIMING.holdUntilMs + extra, resetBy: TIMING.resetByMs + extra };
}

export type Phase = "emitting" | "travelling" | "holding" | "fading" | "done";

/** Where a moving cue is, `elapsed` ms after emission began. Arrival is the
 *  instant travelling ends (elapsed reaches arrivalAtMs). */
export function phaseAt(elapsed: number, s: Schedule): Phase {
  if (elapsed < TIMING.emitMs) return "emitting";
  if (elapsed < TIMING.arrivalAtMs) return "travelling";
  if (elapsed < s.holdUntil) return "holding";
  if (elapsed < s.resetBy) return "fading";
  return "done";
}

/** Whether the route is drawn yet: once the orb has begun to emit it out. */
export function routeLit(elapsed: number, still: boolean): boolean {
  return still || elapsed >= TIMING.emitMs;
}

/** How far along the route (0 the orb, 1 the target) each pulse still on
 *  it is. A pulse leaves the orb at emitMs + i * pulseOffsetMs and takes
 *  travelMs to arrive. */
export function pulseFractions(elapsed: number): number[] {
  const out: number[] = [];
  for (let i = 0; i < TIMING.pulseCount; i++) {
    const p = (elapsed - TIMING.emitMs - i * TIMING.pulseOffsetMs) / TIMING.travelMs;
    if (p > 0 && p < 1) out.push(p);
  }
  return out;
}

/** Each arrival ring's progress (0..1) while it shows; null when not. */
export function ringProgress(elapsed: number): (number | null)[] {
  return [0, 1].map((i) => {
    const t = (elapsed - TIMING.arrivalAtMs - i * TIMING.ringGapMs) / RING_MS;
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

/** Length along right-angled runs. */
export function length(points: readonly Point[]): number {
  let n = 0;
  for (let i = 1; i < points.length; i++) {
    n += Math.abs(points[i][0] - points[i - 1][0]) + Math.abs(points[i][1] - points[i - 1][1]);
  }
  return n;
}

/** The point `fraction` of the way along `points` (0 the orb, 1 the end). */
export function pointAlong(points: readonly Point[], fraction: number): Point {
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
