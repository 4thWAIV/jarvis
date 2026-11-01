/**
 * White attention: one cue's whole life, from finding its target to the
 * pipeline going quiet again. Resolve the exact presentation (revealing it
 * through its registered navigation when asked), take the orb's overlay
 * (orbSource.ts), emit white from the orb, send three pulses along the
 * registered route (uiPipeline.ts), ring and outline the target on arrival,
 * hold, fade and clear. The request is answered at arrival, never at
 * scheduling; with reduced motion the whole static connection is shown at
 * once and the answer says so.
 *
 * One scheduler for every cue: a single animation frame loop, running only
 * while a cue is showing, measures each route again (a resize, a scroll or
 * a list commit moves points, never edges) and publishes what to draw
 * (`cues`, drawn by UiPipelineLayer.tsx). Each cue owns its frame, timers,
 * route and orb token, and gives back only its own.
 *
 * A cue is cancelled when a person navigates, its target goes or can no
 * longer be reached, it is cleared, or the page's connection goes. Never a
 * message Transfer: no tab lights, no receipt, no read state.
 */

import { claimForAttention, attentionTransmit, releaseSource } from "./orbSource";
import { createStore } from "./store";
import {
  dispatch, elementOf, findTarget, lastNavigationActor, layoutRevision, navigationRevision, visibilityOf,
  type VisualTarget,
} from "./uiRegistry";
import { polyline, resolveRoute, type Point, type Route } from "./uiPipeline";
import { kindDecl } from "./uiValidate";
import {
  type Arrival, type AvailabilityReason, type CommandId, type EndpointStyle, type Lifecycle, type SideEffect,
  type Status, type TargetRef, UI_CONTRACT,
} from "./uiContract";
import { isQuiet } from "./voiceHealth";

export const TIMING = UI_CONTRACT.timing;
export const ATTENTION_COLOUR = "#ffffff";

/** One cue as UiPipelineLayer.tsx draws it, this frame. */
export interface CueFrame {
  cueId: string;
  label: string;
  /** Milliseconds since emission began. */
  elapsed: number;
  holdUntil: number;
  resetBy: number;
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
  started: number;
  holdUntil: number;
  resetBy: number;
  still: boolean;
  style: EndpointStyle;
  label: string;
  route: Route;
  box: DOMRect | null;
  token: symbol | null;
  arrived: boolean;
  onEvent: IndicateOptions["onEvent"];
  answer: (outcome: IndicateOutcome) => void;
  lastEvent: Lifecycle;
  effects: SideEffect[];
}

const showing = new Map<string, Cue>();
let frame = 0;
let counter = 0;

function stillMotion(): boolean {
  return typeof matchMedia === "function" && matchMedia("(prefers-reduced-motion: reduce)").matches;
}

// ── revealing ───────────────────────────────────────────────────────────

/** Wait for `ref`'s presentation to mount and become visible, until the
 *  contract's reveal limit; no fixed sleep, it looks again on every
 *  registration change and every frame in between. */
function waitForTarget(ref: TargetRef, signal: AbortSignal): Promise<VisualTarget | null> {
  return new Promise((resolve) => {
    const deadline = performance.now() + UI_CONTRACT.limits.revealWaitMs;
    let raf = 0;
    const check = () => {
      if (signal.aborted || performance.now() > deadline) return done(null);
      const t = findTarget(ref);
      if (t && visibilityOf(elementOf(t, ref.part)) === "visible") return done(t);
      raf = requestAnimationFrame(check);
    };
    const unsubscribe = layoutRevision.subscribe(() => { cancelAnimationFrame(raf); check(); });
    const done = (t: VisualTarget | null) => { cancelAnimationFrame(raf); unsubscribe(); resolve(t); };
    check();
  });
}

/** The presentation's reveal command, with the key put in. */
function revealOf(ref: TargetRef): { command: CommandId; args: Record<string, unknown> } | null {
  const pres = kindDecl(ref.kind)?.presentations.find((p) => p.view === ref.view) as
    { reveal?: { command: CommandId; args: Record<string, unknown> } } | undefined;
  if (!pres?.reveal) return null;
  const args = Object.fromEntries(Object.entries(pres.reveal.args)
    .map(([k, v]) => [k, v === "$key" ? ref.key : v]));
  return { command: pres.reveal.command, args };
}

// ── the one scheduler ───────────────────────────────────────────────────

function publish(now: number): void {
  cues.set([...showing.values()].map((c) => ({
    cueId: c.id, label: c.label, elapsed: now - c.started, holdUntil: c.holdUntil, resetBy: c.resetBy,
    still: c.still, route: c.route, points: polyline(c.route.edges), box: c.box, style: c.style,
  })));
}

function emit(cue: Cue, event: Lifecycle): void {
  if (cue.lastEvent === event) return;
  cue.lastEvent = event;
  cue.onEvent(event, cue.id);
}

function tick(): void {
  frame = 0;
  const now = performance.now();
  for (const cue of [...showing.values()]) {
    const t = now - cue.started;
    // Dirty geometry: measured again every frame, on the same edges.
    const element = elementOf(cue.target, cue.ref.part);
    const seen = visibilityOf(element);
    const again = seen === "visible" ? resolveRoute(cue.target, cue.ref) : null;
    if (!again?.route || findTarget(cue.ref) !== cue.target) {
      // Gone, covered or unreachable: never finish at a stale position.
      end(cue, "cancelled", cue.arrived ? undefined
        : { status: seen === "visible" ? "blocked" : "not_visible", effects: cue.effects,
            reason: seen === "visible" ? "no-route" : seen });
      continue;
    }
    cue.route = again.route;
    if (cue.still) {
      if (t >= cue.holdUntil) end(cue, "cleared");
      continue;
    }
    if (t < TIMING.emitMs) emit(cue, "emitting");
    else if (t < TIMING.arrivalAtMs) emit(cue, "travelling");
    else if (!cue.arrived) {
      cue.arrived = true;
      cue.box = element!.getBoundingClientRect();
      emit(cue, "arrived");
      cue.answer({ status: "completed", effects: cue.effects, cueId: cue.id, arrival: "animated",
                   resolvedTarget: cue.ref });
    } else if (t < cue.holdUntil) {
      cue.box = element!.getBoundingClientRect();
      emit(cue, "holding");
    } else if (t < cue.resetBy) {
      cue.box = element!.getBoundingClientRect();
      emit(cue, "fading");
    } else {
      end(cue, "cleared");
    }
  }
  publish(now);
  if (showing.size) frame = requestAnimationFrame(tick);
}

function schedule(): void {
  if (!frame) frame = requestAnimationFrame(tick);
}

/** End one cue: its frame share, its orb token, its route and contour. */
function end(cue: Cue, event: "cleared" | "cancelled", outcome?: IndicateOutcome): void {
  if (!showing.delete(cue.id)) return;
  if (cue.token) releaseSource(cue.token, event === "cancelled" || performance.now() - cue.started < cue.resetBy);
  if (outcome) cue.answer(outcome);
  else if (!cue.arrived) cue.answer({ status: "cancelled", effects: cue.effects, reason: "cancelled" });
  emit(cue, event);
  publish(performance.now());
  if (!showing.size && frame) { cancelAnimationFrame(frame); frame = 0; }
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

export async function indicate(ref: TargetRef, options: IndicateOptions): Promise<IndicateOutcome> {
  const cueId = `cue-${++counter}`;
  const effects: SideEffect[] = [];
  const fail = (status: Status, reason: string): IndicateOutcome =>
    ({ status, effects, reason, resolvedTarget: ref });
  if (isQuiet()) return fail("unavailable", "quiet-page" satisfies AvailabilityReason);
  options.onEvent("resolving", cueId);
  let target = findTarget(ref);
  let seen = visibilityOf(target ? elementOf(target, ref.part) : null);
  if (seen !== "visible") {
    if ((options.reveal ?? "never") === "never") {
      return fail(target ? "not_visible" : "not_found", target ? seen : "not-mounted");
    }
    options.onEvent("revealing", cueId);
    const reveal = revealOf(ref);
    if (!reveal) return fail("unavailable", "not-mounted");
    const done = await dispatch(reveal.command, reveal.args as never,
                                { actor: "brain", requestId: options.requestId, signal: options.signal });
    effects.push(...done.effects);
    if (done.status !== "completed") return fail(done.status, done.reason ?? "unavailable");
    target = await waitForTarget(ref, options.signal);
    if (options.signal.aborted) return fail("cancelled", "superseded");
    if (!target) return fail("not_visible", "not-mounted");
    seen = "visible";
  }
  const first = resolveRoute(target!, ref);
  if (!first.route) return fail(first.reason === "no-adapter" ? "unavailable" : "blocked", first.reason);
  const still = stillMotion();
  const extra = Math.min(options.holdMs ?? 0, TIMING.maxExtraHoldMs);
  const holdUntil = TIMING.holdUntilMs + extra, resetBy = TIMING.resetByMs + extra;
  let token: symbol | null = null;
  if (!still) {
    token = await claimForAttention(resetBy / 1000, UI_CONTRACT.limits.sourceQueueMs, options.signal);
    if (!token) return fail(options.signal.aborted ? "cancelled" : "busy", options.signal.aborted ? "superseded" : "source-busy");
  }
  return new Promise<IndicateOutcome>((resolve) => {
    let answered = false;
    const cue: Cue = {
      id: cueId, ref, target: target!, started: performance.now(), holdUntil, resetBy, still,
      style: options.style ?? "outline", label: options.label ?? target!.label, route: first.route!,
      box: null, token, arrived: false, onEvent: options.onEvent, lastEvent: "resolving", effects,
      answer: (outcome) => { if (!answered) { answered = true; resolve(outcome); } },
    };
    showing.set(cueId, cue);
    options.signal.addEventListener("abort", () => end(cue, "cancelled",
      cue.arrived ? undefined : { status: "cancelled", effects, reason: "superseded" }));
    if (still) {
      // The whole static connection, at once: no spin, travel or rings.
      cue.arrived = true;
      cue.box = elementOf(cue.target, ref.part)!.getBoundingClientRect();
      emit(cue, "arrived");
      cue.answer({ status: "completed", effects, cueId, arrival: "static-reduced-motion", resolvedTarget: ref });
      emit(cue, "holding");
    } else {
      attentionTransmit(token!, ATTENTION_COLOUR, { direction: [0, -1], seconds: resetBy / 1000 });
      emit(cue, "emitting");
    }
    schedule();
  });
}
