/**
 * The page's end of the brain's UI requests, over the voice socket it
 * already has (jarvis.ts). On every (re)connect the page says who it is
 * (`ui_hello`: its client ID, protocol, capabilities, revisions); the server
 * answers with this connection's epoch (`ui_welcome`). A `ui_request` is
 * then carried out only if it is for this page, this epoch, this protocol,
 * not expired, not seen before and valid against the contract; one at a
 * time, in order; and answered once (`ui_result`), with its lifecycle
 * reported on the way (`ui_event`). Navigation runs the registered handlers
 * through `dispatch` as the brain; attention runs `indicate`.
 *
 * Human input wins: a request that expected a navigation revision the page
 * has moved past is refused as stale, and between a journey's steps the
 * revision is checked again. A dropped connection clears every cue and
 * forgets the epoch, and a request still queued for the old epoch is
 * dropped, so nothing timed out is replayed. A request's `expiresAt` is
 * its one deadline: never started after it, abandoned (answered `expired`
 * with whatever it had already done) if still running at it. A hidden page
 * refuses navigation and pointing (`not_visible`): it paints nothing, so
 * neither could be confirmed. When he stops JARVIS mid-turn (Escape or
 * Stop, jarvis.ts), `interrupt` withdraws that turn's requests: the one
 * running and those queued behind it are answered `cancelled`, and every
 * cue is cleared.
 */

import type { JarvisSocket } from "./ws";
import { clearCues, indicate, type IndicateOutcome } from "./uiAttention";
import {
  commandAvailability, dispatch, elementOf, layoutRevision, mountedTargets,
  navigationRevision, visibilityOf,
} from "./uiRegistry";
import { mainView, rightTab } from "./uiNavigation";
import { operationProblem } from "./uiValidate";
import {
  UI_CONTRACT, type CommandId, type Lifecycle, type Operation, type UIRequest, type UIResult,
} from "./uiContract";

const CLIENT_ID = `page-${crypto.randomUUID().slice(0, 18)}`;
const SEEN_MAX = 200;
/** Operations that need the page painting: refused while it is hidden. */
const VISIBLE_ONLY: ReadonlySet<Operation["kind"]> = new Set(["navigate", "indicate", "journey"]);

type Answer = Omit<UIResult, "type" | "requestId" | "clientId" | "epoch" | "navigationRevision" | "layoutRevision">;

/** The bounded map: views shown, commands and their availability, and
 *  each mounted target's reference, label, visibility and parts. */
function uiMap(op: Extract<Operation, { kind: "map" }>) {
  const inScope = (view: string) => (!op.view || view === op.view)
    && (!op.region || view.startsWith(`${op.region}.`));
  const targets = mountedTargets()
    .filter((t) => inScope(t.ref.view))
    .slice(0, UI_CONTRACT.limits.mapMaxTargets)
    .map((t) => ({ ref: t.ref, label: t.label.slice(0, UI_CONTRACT.limits.labelMax),
                   visibility: visibilityOf(elementOf(t)) }));
  const commands = UI_CONTRACT.commands
    .filter((c) => inScope(c.view) && (c.actors as readonly string[]).includes("brain"))
    .map((c) => {
      const noArgs = Object.keys(c.args).length === 0;
      const a = noArgs ? commandAvailability(c.id as CommandId, {}) : null;
      return { id: c.id, args: Object.keys(c.args), ...(a ? { available: a.available, reason: a.reason } : {}) };
    });
  return { shown: { right: `right.${rightTab.get()}`, main: `main.${mainView.get()}` }, commands, targets };
}

export interface UiClient {
  /** He stopped the turn: withdraw its requests and clear every cue. */
  interrupt(): void;
}

export function startUiClient(socket: JarvisSocket): UiClient {
  let epoch: number | null = null;
  // Moves each time he interrupts a turn: a request received before the
  // move and not yet started is the interrupted turn's, never carried out.
  let generation = 0;
  let queue: Promise<void> = Promise.resolve();
  const seen: string[] = [];
  const seenSet = new Set<string>();
  const live = new Map<string, AbortController>();

  const send = (frame: Record<string, unknown>) => socket.send(frame);
  const hello = () => send({
    type: "ui_hello", protocol: UI_CONTRACT.version, clientId: CLIENT_ID,
    capabilities: ["navigate", "indicate", "map", "clear", "journey"],
    navigationRevision: navigationRevision.get(), layoutRevision: layoutRevision.get(),
    visible: document.visibilityState === "visible",
    reducedMotion: typeof matchMedia === "function" && matchMedia("(prefers-reduced-motion: reduce)").matches,
  });

  socket.onOpen(() => { epoch = null; hello(); });
  /** End everything under way: what is running is aborted (and answers
   *  as cancelled), and every cue is cleared. */
  const withdraw = () => {
    live.forEach((c) => c.abort());
    live.clear();
    clearCues();
  };
  socket.onClose(() => { epoch = null; withdraw(); });

  const answer = (req: UIRequest, a: Answer) => send({
    type: "ui_result", requestId: req.requestId, clientId: CLIENT_ID, epoch: req.epoch,
    navigationRevision: navigationRevision.get(), layoutRevision: layoutRevision.get(), ...a,
  });

  const event = (req: UIRequest) => (kind: Lifecycle, cueId: string) => send({
    type: "ui_event", requestId: req.requestId, clientId: CLIENT_ID, epoch: req.epoch,
    event: kind, at: Date.now(), cueId,
  });

  const fromIndicate = (o: IndicateOutcome): Answer => ({
    status: o.status, effects: o.effects, cueId: o.cueId, arrival: o.arrival, reason: o.reason,
    resolvedTarget: o.resolvedTarget,
  });

  async function step(req: UIRequest, op: Operation, signal: AbortSignal): Promise<Answer> {
    switch (op.kind) {
      case "map":
        return { status: "completed", effects: [], map: uiMap(op) };
      case "navigate": {
        const o = await dispatch(op.commandId, op.args as never, { actor: "brain", requestId: req.requestId, signal });
        return { status: o.status, effects: o.effects, reason: o.reason };
      }
      case "indicate":
        return fromIndicate(await indicate(op.target, {
          reveal: op.reveal, style: op.style, holdMs: op.holdMs, label: op.label,
          requestId: req.requestId, signal, onEvent: event(req),
        }));
      case "clear":
        return { status: "completed", effects: [], reason: clearCues(op.cueId).join(" ") || "nothing to clear" };
      case "journey": {
        // Each leg starts at the orb once the one before has finished; a
        // person's navigation in between ends the rest.
        const steps: Answer[] = [];
        let expected = navigationRevision.get();
        let interrupted = false;
        for (const s of op.steps) {
          if (navigationRevision.get() !== expected) { interrupted = true; break; }
          const a = await step(req, s, signal);
          steps.push(a);
          expected = navigationRevision.get();
          if (a.status !== "completed") break;
        }
        const last = steps[steps.length - 1];
        const done = !interrupted && steps.length === op.steps.length && last.status === "completed";
        return { status: done ? "completed" : interrupted ? "cancelled" : last.status,
                 effects: steps.flatMap((s) => s.effects), steps: steps as UIResult["steps"],
                 reason: done ? undefined : interrupted ? "a person navigated" : last.reason };
      }
    }
  }

  async function carryOut(req: UIRequest, received: number): Promise<void> {
    // Queued behind another request when the connection dropped: that
    // epoch is over, and nothing meant for it is replayed on the next.
    if (req.epoch !== epoch) return;
    if (received !== generation) return answer(req, { status: "cancelled", effects: [], reason: "he stopped the turn" });
    if (req.expiresAt <= Date.now()) return answer(req, { status: "expired", effects: [], reason: "expired" });
    if (req.expectedNavigationRevision !== null && req.expectedNavigationRevision !== navigationRevision.get()) {
      return answer(req, { status: "stale", effects: [], reason: "the page has moved on" });
    }
    // A hidden page paints nothing (no animation frames), so a navigation
    // could never be confirmed and a signal never seen: refused, not left
    // to run out the clock.
    if (VISIBLE_ONLY.has(req.operation.kind) && document.visibilityState !== "visible") {
      return answer(req, { status: "not_visible", effects: [], reason: "the page is hidden (another tab or minimised)" });
    }
    const control = new AbortController();
    live.set(req.requestId, control);
    // One deadline: what is still running at it is abandoned, so nothing
    // happens on the page after the server has stopped waiting.
    let pastDeadline = false;
    const deadline = setTimeout(() => { pastDeadline = true; control.abort(); },
                                Math.max(0, req.expiresAt - Date.now()));
    try {
      const done = await step(req, req.operation, control.signal);
      answer(req, pastDeadline && done.status !== "completed"
        ? { ...done, status: "expired", reason: "the request's deadline passed" } : done);
    } catch (e) {
      answer(req, { status: "blocked", effects: [], reason: String(e instanceof Error ? e.message : e).slice(0, 200) });
    } finally {
      clearTimeout(deadline);
      live.delete(req.requestId);
    }
  }

  socket.onMessage((msg) => {
    if (msg.type === "ui_welcome") {
      if (msg.clientId === CLIENT_ID && Number.isInteger(msg.epoch)) epoch = msg.epoch as number;
      return;
    }
    if (msg.type !== "ui_request") return;
    const req = msg as unknown as UIRequest;
    if (typeof req.requestId !== "string" || !req.requestId || req.requestId.length > UI_CONTRACT.limits.idMax) return;
    if (req.clientId !== CLIENT_ID) return;                 // another page's
    if (seenSet.has(req.requestId)) return;                 // a duplicate is answered once
    seenSet.add(req.requestId);
    seen.push(req.requestId);
    if (seen.length > SEEN_MAX) seenSet.delete(seen.shift()!);
    if (req.protocol !== UI_CONTRACT.version) {
      return answer(req, { status: "invalid", effects: [], reason: `protocol ${String(req.protocol)} is not ${UI_CONTRACT.version}` });
    }
    if (epoch === null || req.epoch !== epoch) return answer(req, { status: "stale", effects: [], reason: "connection epoch" });
    const problem = operationProblem(req.operation);
    if (problem) return answer(req, { status: "invalid", effects: [], reason: problem });
    const received = generation;
    if (req.operation.kind === "clear") { void carryOut(req, received); return; }   // never waits behind a cue
    queue = queue.then(() => carryOut(req, received));
  });

  return {
    interrupt() {
      generation += 1;
      withdraw();
    },
  };
}
