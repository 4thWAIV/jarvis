/**
 * Live activity on the nodes of tracked work: the Work tab's items and
 * pieces (work.ts) and a workflow's phases and tasks (workflows.ts).
 *
 * Every look comes from the record, never from a clock of the page's own:
 *
 *   - working: a workflow item running and heard from within the quiet
 *     window, i.e. its `quiet_at` (from the server) is still ahead. It
 *     moves: a slow glow travelling along its edge (style.css).
 *   - paused: in a working state but `quiet_at` has passed: dimmed, still,
 *     marked "Paused".
 *   - blocked: still red. waiting: waiting on him, still amber. done: still,
 *     with a check.
 *
 * Work items and pieces never derive generation from report freshness;
 * their claimed working state is activity_unknown and verification is still.
 * Each new report on a node gives one brief flash the moment the page reads
 * it, after a `work` or `workflows` hint (`useArrivalFlash`): its heard time
 * later than both when its view began to show and the last one it flashed
 * for.
 *
 * The page only re-renders on its own when something is working: one timer,
 * for the soonest moment a working node goes quiet (`useActivityClock`).
 *
 * Reduced motion (the system's) and Steady (`data-motion="steady"` on the
 * document) keep every colour and mark and drop the motion: the glow stands
 * still and there is no flash (`motionStill`).
 */

import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { useT } from "./locale";

export type Activity = "working" | "paused" | "blocked" | "waiting" | "done";

/** What a node's own state says, before the clock is asked. */
export type Standing = "active" | "blocked" | "waiting" | "done" | null;

/** A node's look now: its standing, with an active one working until its
 *  `quietAt` (epoch seconds) and paused after. */
export function activityOf(standing: Standing, quietAt: number | null, nowSec: number): Activity | null {
  if (standing === "active") return quietAt !== null && quietAt > nowSec ? "working" : "paused";
  return standing;
}

/** A work item's or piece's standing (work.ts states). */
export function workStanding(state: string): Standing {
  switch (state) {
    // Working is sent only on live evidence (a subagent on the piece writing
    // now), so it glows. A report or verification is not generation
    // telemetry: activity_unknown and verifying never do.
    case "working": return "active";
    case "verifying": case "activity_unknown": return null;
    case "blocked": return "blocked";
    case "waiting_on_tim": return "waiting";
    case "done": return "done";
    default: return null;
  }
}

/** A workflow phase's or task's standing (workflow_store.ItemState). A
 *  running item of a workflow that waits on his decision waits on him. */
export function workflowItemStanding(state: string, workflowState: string): Standing {
  switch (state) {
    case "running": return workflowState === "needs_you" ? "waiting" : "active";
    case "blocked": case "failed": return "blocked";
    case "done": return "done";
    default: return null;
  }
}

/** The page's time in seconds, moved on only at the soonest of `quietAts`
 *  still ahead: no timer at all while nothing shown is working. */
export function useActivityClock(quietAts: readonly (number | null)[]): number {
  const [now, setNow] = useState(() => Date.now() / 1000);
  const current = Date.now() / 1000;
  const next = quietAts.reduce<number | null>(
    (soonest, at) => (at !== null && at > current && (soonest === null || at < soonest) ? at : soonest), null);
  useEffect(() => {
    if (next === null) return;
    const timer = setTimeout(() => setNow(Date.now() / 1000), (next - Date.now() / 1000) * 1000 + 50);
    return () => clearTimeout(timer);
  }, [next]);
  // A read since the last tick may already be later than `now`.
  return Math.max(now, current);
}

/** True when motion is to be dropped: the system asks for reduced motion,
 *  or the page is Steady. */
export function motionStill(): boolean {
  const reduced = typeof matchMedia === "function" && matchMedia("(prefers-reduced-motion: reduce)").matches;
  return reduced || document.documentElement.dataset.motion === "steady";
}

const FLASH_MS = 700;

/** One brief flash on `el`, unless motion is still. */
export function flash(el: Element): void {
  if (motionStill() || typeof (el as HTMLElement).animate !== "function") return;
  (el as HTMLElement).animate(
    [{ boxShadow: "0 0 0 0 rgba(255, 255, 255, 0)", filter: "brightness(1)" },
     { boxShadow: "0 0 14px 3px rgba(224, 242, 254, 0.75)", filter: "brightness(1.6)", offset: 0.2 },
     { boxShadow: "0 0 0 0 rgba(255, 255, 255, 0)", filter: "brightness(1)" }],
    { duration: FLASH_MS, easing: "ease-out" });
}

/** When the view holding a node began to show it (epoch seconds): a report
 *  later than this arrived while he was looking. Provided by each view of
 *  nodes (`useShownSince`); without one, nothing flashes. */
export const ShownSince = createContext(Number.POSITIVE_INFINITY);

/** The moment this view began to show `what` (the item or workflow open,
 *  or nothing for a list): reset when `what` changes. */
export function useShownSince(what: unknown = null): number {
  return useMemo(() => Date.now() / 1000, [what]);
}

/** A ref for a node that flashes once for each report on it (`heard`, its
 *  latest, epoch seconds) that arrived after its view began to show: a node
 *  already reported before he looked does not flash, and one added while he
 *  looks does. */
export function useArrivalFlash(heard: number | null | undefined): (el: Element | null) => void {
  const since = useContext(ShownSince);
  const node = useRef<Element | null>(null);
  const seen = useRef(since);
  if (since > seen.current) seen.current = since;
  useEffect(() => {
    if (heard == null || heard <= seen.current) return;
    seen.current = heard;
    if (node.current) flash(node.current);
  }, [heard]);
  return useCallback((el: Element | null) => { node.current = el; }, []);
}

/** The mark a node carries for its look, where the look needs words: "Paused"
 *  on a quiet working node, a check on a done one. Blocked and waiting keep
 *  the marks they already had (WorkMarkDot, the workflow icons). */
export function ActivityMark({ activity, target }: { activity: Activity | null;
                                                     target?: (el: HTMLElement | null) => void }) {
  const t = useT();
  if (activity === "paused") {
    return <span className="activity-mark activity-paused" title={t("right.work.activity.pausedTitle")}
                 ref={target}>{t("right.work.activity.paused")}</span>;
  }
  if (activity === "done") {
    const words = t("right.work.activity.done");
    return <span className="activity-mark activity-done" role="img" aria-label={words} title={words}
                 ref={target}>✓</span>;
  }
  return null;
}
