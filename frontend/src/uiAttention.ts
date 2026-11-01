/**
 * White attention: one cue's whole life, from finding its target to the
 * pipeline going quiet again. Resolve the exact presentation (revealing it
 * through its registered navigation when asked), take the orb's overlay
 * (orbSource.ts; a real transmission keeps it, and the cue waits, shown
 * as queued, for at most the contract's sourceQueueMs before it answers
 * busy), emit white from the orb, send three pulses along the registered
 * route (uiPipeline.ts), ring and outline the target on arrival, hold,
 * fade and clear. Every moment comes from one schedule (uiSignal.ts), the
 * same one the drawing reads. The request is answered at arrival, never at
 * scheduling; with reduced motion the whole static connection is shown at
 * once and the answer says so.
 *
 * Its own channel: never a message Transfer, so no tab lights, no receipt,
 * no read state, no business arrival callback. Attention arrival resolves
 * only the UI request.
 *
 * One scheduler for every cue: the shared frame loop (effectLife.ts),
 * running only while a cue is showing, measures each route again (a
 * resize, a scroll or a list commit moves points, never edges) and
 * publishes what to draw (`cues`, drawn by UiPipelineLayer.tsx). A cue
 * whose route would change its edges, whose target goes, is recycled to
 * another entity or is covered, is cancelled there and then: it never
 * finishes at a stale position. Each cue owns its EffectLife: its frames,
 * its wait for the orb, its abort listener and its orb token, and gives
 * back only its own.
 *
 * A cue is cancelled when a person navigates, its target goes or can no
 * longer be reached, it is cleared, its request is withdrawn (a turn
 * interrupted, an expiry) or the page's connection goes (uiClient.ts).
 */

import { EffectLife, onFrame } from "./effectLife";
import { attentionTransmit, claimForAttention, releaseSource } from "./orbSource";
import { createStore } from "./store";
import {
  elementOf, findTarget, lastNavigationActor, navigationRevision, visibilityOf, type VisualTarget,
} from "./uiRegistry";
import { reveal } from "./uiNavigationReveal";
import { polyline, resolveRoute, topologyOf, type Route } from "./uiPipeline";
import { leaving, orbEmission, phaseAt, scheduleFor, TIMING, type Point, type Schedule } from "./uiSignal";
import {
  type Arrival, type AvailabilityReason, type EndpointStyle, type Lifecycle, type SideEffect,
  type Status, type TargetRef, UI_CONTRACT,
} from "./uiContract";
import { isQuiet } from "./voiceHealth";

export { TIMING };
export const ATTENTION_COLOUR = "#ffffff";

/** One cue as UiPipelineLayer.tsx draws it, this frame. */
export interface CueFrame {
  cueId: string;
  label: string;
  /** Waiting for a real transmission to give the orb back: its route is
   *  shown, dashed, and nothing moves yet. */
  queued: boolean;
  /** Milliseconds since emission began (0 while queued). */
  elapsed: number;
  schedule: Schedule;
  still: boolean;
  route: Route;
  points: Point[];
  /** The target's box at arrival, for its contour. */
  box: DOMRect | null;
  style: EndpointStyle;
}

/** What is showing; the layer reads it. */
export const cues = createStore<CueFrame[]>([]);

export interface IndicateOptions {
  reveal?: "never" | "navigate";
  style?: EndpointStyle;
  holdMs?: number;
  label?: string;
  requestId: string | null;
  signal: AbortSignal;
  onEvent: (event: Lifecycle, cueId: string) => void;
}

export interface IndicateOutcome {
  status: Status;
  effects: SideEffect[];
  cueId?: string;
  arrival?: Arrival;
  reason?: string;
  resolvedTarget?: TargetRef;
}

interface Cue {
  id: string;
  ref: TargetRef;
  target: VisualTarget;
  /** When emission began; null while waiting for the orb. */
  started: number | null;
  schedule: Schedule;
  still: boolean;
  style: EndpointStyle;
  label: string;
  route: Route;
  topology: string;
  box: DOMRect | null;
  token: symbol | null;
  arrived: boolean;
  life: EffectLife;
  onEvent: IndicateOptions["onEvent"];
  answer: (outcome: IndicateOutcome) => void;
  lastEvent: Lifecycle;
  effects: SideEffect[];
}

const showing = new Map<string, Cue>();
let stopFrames: (() => void) | null = null;
let counter = 0;

function stillMotion(): boolean {
  return typeof matchMedia === "function" && matchMedia("(prefers-reduced-motion: reduce)").matches;
}

// ── the one scheduler ───────────────────────────────────────────────────

function publish(now: number): void {
  cues.set([...showing.values()].map((c) => ({
    cueId: c.id, label: c.label, queued: c.started === null, elapsed: c.started === null ? 0 : now - c.started,
    schedule: c.schedule, still: c.still, route: c.route, points: polyline(c.route.edges), box: c.box,
    style: c.style,
  })));
}

function emit(cue: Cue, event: Lifecycle): void {
  if (cue.lastEvent === event) return;
  cue.lastEvent = event;
  cue.onEvent(event, cue.id);
}

function arrive(cue: Cue, element: Element, arrival: Arrival): void {
  cue.arrived = true;
  cue.box = element.getBoundingClientRect();
  emit(cue, "arrived");
  cue.answer({ status: "completed", effects: cue.effects, cueId: cue.id, arrival, resolvedTarget: cue.ref });
}

function tick(): void {
  const now = performance.now();
  for (const cue of [...showing.values()]) {
    // Dirty geometry: measured again every frame, on the same edges.
    const element = elementOf(cue.target, cue.ref.part);
    const seen = visibilityOf(element);
    const again = seen === "visible" ? resolveRoute(cue.target, cue.ref) : null;
    if (!again?.route || findTarget(cue.ref) !== cue.target || topologyOf(again.route) !== cue.topology) {
      // Gone, recycled, covered or rerouted: never finish at a stale
      // position, never jump to another path.
      const reason = seen !== "visible" ? seen : again && !again.route ? again.reason : "no-route";
      end(cue, "cancelled", cue.arrived ? undefined
        : { status: seen === "visible" ? "blocked" : "not_visible", effects: cue.effects, reason });
      continue;
    }
    cue.route = again.route;
    if (cue.started === null) continue;              // queued for the orb
    const t = now - cue.started;
    if (cue.still) {
      cue.box = element!.getBoundingClientRect();
      if (t >= cue.schedule.holdUntil) end(cue, "cleared");
      continue;
    }
    switch (phaseAt(t, cue.schedule)) {
      case "emitting": emit(cue, "emitting"); break;
      case "travelling": emit(cue, "travelling"); break;
      case "holding":
        if (!cue.arrived) { arrive(cue, element!, "animated"); break; }
        cue.box = element!.getBoundingClientRect();
        emit(cue, "holding");
        break;
      case "fading":
        if (!cue.arrived) arrive(cue, element!, "animated");
        cue.box = element!.getBoundingClientRect();
        emit(cue, "fading");
        break;
      case "done":
        end(cue, "cleared");
        break;
    }
  }
  publish(now);
  if (!showing.size && stopFrames) { stopFrames(); stopFrames = null; }
}

function schedule(): void {
  if (!stopFrames) stopFrames = onFrame(tick);
}

/** End one cue: its frames, its wait, its listener, its orb token, its
 *  route and contour. */
function end(cue: Cue, event: "cleared" | "cancelled", outcome?: IndicateOutcome): void {
  if (!showing.delete(cue.id)) return;
  if (cue.token) {
    const early = event === "cancelled" || cue.started === null
      || performance.now() - cue.started < cue.schedule.resetBy;
    releaseSource(cue.token, early);
  }
  cue.life.end();
  if (outcome) cue.answer(outcome);
  else if (!cue.arrived) cue.answer({ status: "cancelled", effects: cue.effects, reason: "cancelled" });
  emit(cue, event);
  publish(performance.now());
  if (!showing.size && stopFrames) { stopFrames(); stopFrames = null; }
}

/** Clear one cue, or all of them; returns what was cleared. */
export function clearCues(cueId?: string): string[] {
  const gone = [...showing.values()].filter((c) => !cueId || c.id === cueId);
  gone.forEach((c) => end(c, "cancelled"));
  return gone.map((c) => c.id);
}

// A person's navigation supersedes every cue under way.
navigationRevision.subscribe(() => {
  if (lastNavigationActor.get() === "human") clearCues();
});

// ── one indication ──────────────────────────────────────────────────────

/** Start the white emission once the cue holds the orb. */
function launch(cue: Cue, token: symbol): void {
  cue.token = token;
  cue.started = performance.now();
  attentionTransmit(token, ATTENTION_COLOUR,
                    { direction: leaving(polyline(cue.route.edges)), ...orbEmission(cue.schedule) });
  emit(cue, "emitting");
}

export async function indicate(ref: TargetRef, options: IndicateOptions): Promise<IndicateOutcome> {
  const cueId = `cue-${++counter}`;
  const effects: SideEffect[] = [];
  const fail = (status: Status, reason: string): IndicateOutcome =>
    ({ status, effects, reason, resolvedTarget: ref });
  if (isQuiet()) return fail("unavailable", "quiet-page" satisfies AvailabilityReason);
  options.onEvent("resolving", cueId);
  let target = findTarget(ref);
  const seen = visibilityOf(target ? elementOf(target, ref.part) : null);
  if (seen !== "visible") {
    if ((options.reveal ?? "never") === "never") {
      return fail(target ? "not_visible" : "not_found", target ? seen : "not-mounted");
    }
    options.onEvent("revealing", cueId);
    const shown = await reveal(ref, { actor: "brain", requestId: options.requestId, signal: options.signal });
    effects.push(...shown.effects);
    if (shown.status !== "completed") return fail(shown.status, shown.reason);
    target = shown.target;
  }
  const first = resolveRoute(target!, ref);
  if (!first.route) {
    return fail(first.reason === "no-adapter" ? "unavailable" : "blocked", first.reason);
  }
  if (options.signal.aborted) return fail("cancelled", "superseded");
  const still = stillMotion();
  return new Promise<IndicateOutcome>((resolve) => {
    let answered = false;
    const life = new EffectLife();
    const cue: Cue = {
      id: cueId, ref, target: target!, started: still ? performance.now() : null,
      schedule: scheduleFor(options.holdMs ?? 0), still, style: options.style ?? "outline",
      label: options.label ?? target!.label, route: first.route!, topology: topologyOf(first.route!),
      box: null, token: null, arrived: false, life, onEvent: options.onEvent, lastEvent: "resolving", effects,
      answer: (outcome) => { if (!answered) { answered = true; resolve(outcome); } },
    };
    showing.set(cueId, cue);
    const onAbort = () => end(cue, "cancelled",
      cue.arrived ? undefined : { status: "cancelled", effects, reason: "superseded" });
    options.signal.addEventListener("abort", onAbort);
    life.hold(() => options.signal.removeEventListener("abort", onAbort));
    schedule();
    if (still) {
      // The whole static connection, at once: no spin, travel or rings,
      // and the orb is left as it is.
      arrive(cue, elementOf(cue.target, ref.part)!, "static-reduced-motion");
      emit(cue, "holding");
      publish(performance.now());
      return;
    }
    // The orb, when a real transmission lets it go; the wait ends with
    // the cue.
    const waiting = new AbortController();
    life.hold(() => waiting.abort());
    void claimForAttention(cue.schedule.resetBy / 1000, UI_CONTRACT.limits.sourceQueueMs, waiting.signal)
      .then((token) => {
        if (!showing.has(cue.id)) {
          if (token) releaseSource(token, true);
          return;
        }
        if (!token) {
          end(cue, "cancelled", { status: "busy", effects, reason: "source-busy" });
          return;
        }
        launch(cue, token);
      });
  });
}
