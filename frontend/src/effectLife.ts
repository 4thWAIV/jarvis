/**
 * What every effect drawn from the orb shares, a message's ray
 * (RayLane.tsx) and white attention (uiAttention.ts) alike:
 *
 *   - one animation-frame scheduler (`onFrame`): a single loop, running only
 *     while something is subscribed, that every moving effect follows;
 *   - one geometry invalidation (`onGeometryChange`): every signal that can
 *     move an end or a junction (a target registering or going, a
 *     navigation, a list's commit, a resize, a scroll anywhere), so an
 *     effect measures again before a moved row is painted;
 *   - one lifecycle (`EffectLife`): the frames, timers and releases a single
 *     effect holds, given back together and only its own, at expiry,
 *     cancellation or when its host goes;
 *   - the visually hidden style of the status lines screen readers hear.
 */

import type { CSSProperties } from "react";
import { LAYOUT_SIGNALS } from "./transferEndpoints";
import { layoutRevision, navigationRevision } from "./uiRegistry";

// ── the one frame scheduler ─────────────────────────────────────────────

type FrameCallback = (now: number) => void;
const everyFrame = new Set<FrameCallback>();
let raf = 0;

function run(now: number): void {
  raf = 0;
  for (const callback of [...everyFrame]) {
    if (everyFrame.has(callback)) callback(now);
  }
  if (everyFrame.size && !raf) raf = requestAnimationFrame(run);
}

/** Calls `callback` on every animation frame until the returned function is
 *  called. All moving effects share this one loop; it stops when nothing
 *  is subscribed. */
export function onFrame(callback: FrameCallback): () => void {
  everyFrame.add(callback);
  if (!raf) raf = requestAnimationFrame(run);
  return () => {
    everyFrame.delete(callback);
    if (!everyFrame.size && raf) { cancelAnimationFrame(raf); raf = 0; }
  };
}

/** How many callbacks the scheduler is running (a test's leak check). */
export function framesRunning(): number {
  return everyFrame.size;
}

// ── the one geometry invalidation ───────────────────────────────────────

/** Calls `callback` whenever geometry may have moved, until the returned
 *  function is called. */
export function onGeometryChange(callback: () => void): () => void {
  const stores = [...LAYOUT_SIGNALS, layoutRevision, navigationRevision].map((s) => s.subscribe(callback));
  window.addEventListener("resize", callback);
  window.addEventListener("scroll", callback, { capture: true, passive: true });
  return () => {
    stores.forEach((u) => u());
    window.removeEventListener("resize", callback);
    window.removeEventListener("scroll", callback, { capture: true });
  };
}

// ── one effect's life ───────────────────────────────────────────────────

/** Everything one effect holds while it shows: its frame callbacks, its
 *  timers and its releases (a colour, the orb, a listener). `end` cancels
 *  and gives back all of them, once, so nothing outlives the effect. */
export class EffectLife {
  private stops = new Set<() => void>();
  private timers = new Set<ReturnType<typeof setTimeout>>();
  private releases: (() => void)[] = [];
  private over = false;

  get ended(): boolean { return this.over; }

  /** `callback` once, on the next frame. */
  frame(callback: () => void): void {
    if (this.over) return;
    const stop = onFrame(() => { stop(); this.stops.delete(stop); callback(); });
    this.stops.add(stop);
  }

  /** `callback` on every frame until the effect ends. */
  everyFrame(callback: FrameCallback): void {
    if (this.over) return;
    this.stops.add(onFrame(callback));
  }

  after(ms: number, callback: () => void): void {
    if (this.over) return;
    const id = setTimeout(() => { this.timers.delete(id); callback(); }, ms);
    this.timers.add(id);
  }

  /** Something to give back when the effect ends (run at once if it has). */
  hold(release: () => void): void {
    if (this.over) { release(); return; }
    this.releases.push(release);
  }

  end(): void {
    if (this.over) return;
    this.over = true;
    this.stops.forEach((stop) => stop());
    this.timers.forEach(clearTimeout);
    this.stops.clear();
    this.timers.clear();
    this.releases.splice(0).forEach((release) => release());
  }
}

/** A status line screen readers hear and nobody sees. */
export const VISUALLY_HIDDEN: CSSProperties = {
  position: "absolute", width: 1, height: 1, margin: -1, padding: 0, overflow: "hidden",
  clip: "rect(0 0 0 0)", whiteSpace: "nowrap", border: 0,
};
