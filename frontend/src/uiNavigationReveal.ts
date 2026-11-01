/**
 * Bringing one exact presentation into view before anything points at it:
 * the reveal step of an indication (uiAttention.ts) and of a journey.
 *
 *   1. Already visible: nothing is done.
 *   2. Not on the page, or hidden: the presentation's declared reveal
 *      command runs through `dispatch`, as the caller, with its side
 *      effects reported (the contract's `reveal` on that presentation,
 *      with `$key` filled in). Nothing else is ever opened in its place.
 *   3. Still not mounted: the view's own revealer, if one is registered
 *      for that view and kind, brings the item in (pages a list, loads
 *      earlier records, expands a row), by its stable key.
 *   4. It waits for the target to register and lay out, looking again on
 *      every registration change and every frame, never by a fixed sleep,
 *      until the contract's reveal limit.
 *   5. Scrolled out of view: it is scrolled into view (nearest edge, no
 *      smooth motion), never by moving keyboard focus, and measured again.
 *
 * A person's navigation in the meantime (the navigation revision moves by
 * anyone else) ends it as `cancelled`; whatever had already happened is
 * reported, and his new view is never put back.
 */

import {
  dispatch, elementOf, findTarget, layoutRevision, navigationRevision, visibilityOf,
  type VisualTarget,
} from "./uiRegistry";
import {
  UI_CONTRACT, type Actor, type AvailabilityReason, type CommandId, type SideEffect, type Status,
  type TargetRef, type ViewId,
} from "./uiContract";
import { kindDecl } from "./uiValidate";

export interface RevealContext {
  actor: Actor;
  requestId: string | null;
  signal: AbortSignal;
}

export type RevealOutcome =
  | { status: "completed"; target: VisualTarget; effects: SideEffect[] }
  | { status: Exclude<Status, "completed">; reason: string; effects: SideEffect[] };

/** A view's way of bringing one of its items onto the page once the view
 *  is shown. Resolves true once it has done what it can (the target is
 *  then waited for), or with the reason it cannot. */
export type Revealer = (ref: TargetRef, context: RevealContext) => Promise<true | AvailabilityReason>;

const revealers = new Map<string, Revealer>();

const revealerId = (view: ViewId, kind: string) => `${view}|${kind}`;

/** Register the revealer of `kind` in `view`: one each. */
export function registerRevealer(view: ViewId, kind: string, revealer: Revealer): () => void {
  if (!kindDecl(kind)?.presentations.some((p) => p.view === view)) {
    throw new Error(`ui: ${kind} is not presented in ${view}`);
  }
  const id = revealerId(view, kind);
  if (revealers.has(id)) throw new Error(`ui: ${id} already has a revealer`);
  revealers.set(id, revealer);
  return () => { if (revealers.get(id) === revealer) revealers.delete(id); };
}

/** The presentation's declared reveal command, with the key put in. */
export function revealCommand(ref: TargetRef): { command: CommandId; args: Record<string, unknown> } | null {
  const pres = kindDecl(ref.kind)?.presentations.find((p) => p.view === ref.view) as
    { reveal?: { command: CommandId; args: Record<string, unknown> } } | undefined;
  if (!pres?.reveal) return null;
  const args = Object.fromEntries(Object.entries(pres.reveal.args)
    .map(([k, v]) => [k, v === "$key" ? ref.key : v]));
  return { command: pres.reveal.command, args };
}

const seen = (ref: TargetRef) => {
  const t = findTarget(ref);
  return { target: t, visibility: visibilityOf(t ? elementOf(t, ref.part) : null) };
};

/** Wait until `ready` holds, looking on every registration change and
 *  every frame, until `deadline` or until `stop` says to give up. */
function until(ready: () => boolean, stop: () => boolean, deadline: number): Promise<boolean> {
  return new Promise((resolve) => {
    let frame = 0;
    const look = () => {
      if (ready()) return done(true);
      if (stop() || performance.now() > deadline) return done(false);
      frame = requestAnimationFrame(look);
    };
    const unsubscribe = layoutRevision.subscribe(() => { cancelAnimationFrame(frame); look(); });
    const done = (ok: boolean) => { cancelAnimationFrame(frame); unsubscribe(); resolve(ok); };
    look();
  });
}

/** Bring `ref` into view, as described above. */
export async function reveal(ref: TargetRef, context: RevealContext): Promise<RevealOutcome> {
  const effects: SideEffect[] = [];
  const refuse = (status: Exclude<Status, "completed">, reason: string): RevealOutcome =>
    ({ status, reason, effects });
  const deadline = performance.now() + UI_CONTRACT.limits.revealWaitMs;
  let now = seen(ref);
  if (now.visibility === "visible") return { status: "completed", target: now.target!, effects };

  // Who else navigates while this runs: every move of the revision that
  // is not this reveal's own command.
  let expected = navigationRevision.get();
  const overtaken = () => context.signal.aborted || navigationRevision.get() !== expected;

  if (now.visibility === "unmounted" || now.visibility === "hidden") {
    const command = revealCommand(ref);
    const revealer = revealers.get(revealerId(ref.view, ref.kind));
    if (!command && !revealer) {
      return refuse(now.target ? "not_visible" : "not_found", now.target ? now.visibility : "not-mounted");
    }
    if (command) {
      const done = await dispatch(command.command, command.args as never,
                                  { actor: context.actor, requestId: context.requestId, signal: context.signal });
      effects.push(...done.effects);
      if (done.status !== "completed") return refuse(done.status, done.reason ?? "unavailable");
      // Its own command moved the revision once; anything more is someone else.
      if (context.signal.aborted || navigationRevision.get() !== expected + 1) return refuse("cancelled", "superseded");
      expected = navigationRevision.get();
    }
    if (revealer && seen(ref).visibility === "unmounted") {
      const brought = await revealer(ref, context);
      if (overtaken()) return refuse("cancelled", "superseded");
      if (brought !== true) return refuse("unavailable", brought);
    }
    // Ready once it is mounted, shown and laid out.
    await until(() => ["visible", "offscreen", "occluded"].includes(seen(ref).visibility), overtaken, deadline);
    if (overtaken()) return refuse("cancelled", "superseded");
    now = seen(ref);
  }

  if (now.visibility === "offscreen") {
    elementOf(now.target!, ref.part)!.scrollIntoView({ block: "nearest", inline: "nearest", behavior: "instant" });
    await until(() => seen(ref).visibility !== "offscreen", overtaken, deadline);
    if (overtaken()) return refuse("cancelled", "superseded");
    now = seen(ref);
  }

  if (now.visibility === "visible") return { status: "completed", target: now.target!, effects };
  if (now.visibility === "unmounted") return refuse(now.target ? "not_visible" : "not_found", "not-mounted");
  return refuse("not_visible", now.visibility);
}
