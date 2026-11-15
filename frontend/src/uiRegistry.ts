/**
 * The one registry of navigation commands and visual targets. Every
 * navigation a person makes (a tab, a row, a link, a key) and every one the
 * brain asks for (uiClient.ts) goes through `dispatch`, which validates the
 * arguments against the contract (uiValidate.ts), checks the caller and the
 * command's availability, runs its one registered handler, and moves the
 * navigation revision. There is no second route: a feature registers its
 * handler next to its own state, and its controls call `dispatch`.
 *
 * Targets are what can be pointed at: one per mounted presentation of one
 * entity (the same node on the right and in the centre is two). A feature
 * registers each as it mounts, with element accessors for the whole and for
 * each declared part, and unregisters it as it unmounts. Elements never
 * leave the page; discovery exports references, labels and visibility only.
 *
 * Declared in the contract (jarvis/ui_contract/), implemented here: a
 * handler for an undeclared command is a type error, a duplicate is refused,
 * and `missingHandlers` lists declared commands nothing has registered.
 */

import { useCallback, useLayoutEffect, useRef } from "react";
import { createStore } from "./store";
import {
  type Actor, type AvailabilityReason, type CommandArgs, type CommandId, type RailId, type SideEffect,
  type Status, type TargetRef, type Visibility, UI_CONTRACT,
} from "./uiContract";
import { argsProblem, commandDecl, kindDecl, partsIn, targetProblem } from "./uiValidate";

// ── navigation commands ─────────────────────────────────────────────────

export interface CommandContext {
  actor: Actor;
  /** The brain request this runs for; null for a person's own control. */
  requestId: string | null;
  signal: AbortSignal;
}

export interface CommandOutcome {
  status: Status;
  effects: SideEffect[];
  reason?: string;
}

export interface Availability { available: boolean; reason?: AvailabilityReason }

export interface CommandHandler<C extends CommandId> {
  availability(args: CommandArgs[C]): Availability;
  /** Applies the navigation; resolves once the page shows it. */
  execute(args: CommandArgs[C], context: CommandContext): CommandOutcome | Promise<CommandOutcome>;
}

const handlers = new Map<CommandId, CommandHandler<never>>();

/** Every navigation, a person's or the brain's, moves this once. */
export const navigationRevision = createStore(0);
/** Who made the latest navigation: a person's supersedes the brain's. */
export const lastNavigationActor = createStore<Actor | null>(null);

export function registerCommand<C extends CommandId>(id: C, handler: CommandHandler<C>): () => void {
  if (!commandDecl(id)) throw new Error(`ui: ${id} is not declared in the contract`);
  if (handlers.has(id)) throw new Error(`ui: ${id} already has a handler`);
  handlers.set(id, handler as unknown as CommandHandler<never>);
  return () => { if (handlers.get(id) === (handler as unknown)) handlers.delete(id); };
}

/** Declared commands with no handler registered (the coverage check). */
export function missingHandlers(): string[] {
  return UI_CONTRACT.commands.map((c) => c.id).filter((id) => !handlers.has(id as CommandId));
}

/** Whether `id` can run with `args` now, and why not. */
export function commandAvailability(id: CommandId, args: Record<string, unknown>): Availability {
  const handler = handlers.get(id);
  if (!handler) return { available: false, reason: "not-mounted" };
  return handler.availability(args as never);
}

/** The effects `id` declares for these arguments (its `when` conditions). */
export function declaredEffects(id: CommandId, args: Record<string, unknown>): SideEffect[] {
  const decl = UI_CONTRACT.commands.find((c) => c.id === id)!;
  return decl.effects
    .filter((e) => !("when" in e) || Object.entries(e.when as Record<string, unknown>)
      .every(([k, v]) => args[k] === v))
    .map((e) => e.effect as SideEffect);
}

const afterPaint = () => new Promise<void>((resolve) => requestAnimationFrame(() => resolve()));

const refused = (status: Status, reason: string): CommandOutcome => ({ status, effects: [], reason });

/** Run one navigation command: the only way any view changes. */
export async function dispatch<C extends CommandId>(
  id: C, args: CommandArgs[C],
  // The actor is always said: a caller that forgot it must not pass for a person.
  context: Pick<CommandContext, "actor"> & Partial<CommandContext>,
): Promise<CommandOutcome> {
  const ctx: CommandContext = { requestId: null, signal: new AbortController().signal, ...context };
  const problem = argsProblem(id, args, ctx.actor);
  if (problem) return refused("invalid", problem);
  const handler = handlers.get(id) as CommandHandler<C> | undefined;
  if (!handler) return refused("unavailable", "not-mounted");
  const availability = handler.availability(args);
  if (!availability.available) return refused("unavailable", availability.reason ?? "no-entity");
  if (ctx.signal.aborted) return refused("cancelled", "superseded");
  const outcome = await handler.execute(args, ctx);
  if (outcome.status === "completed") {
    lastNavigationActor.set(ctx.actor);
    navigationRevision.set(navigationRevision.get() + 1);
    // Applied means painted: the views it shows have committed and their
    // targets registered before anyone is told it is done.
    await afterPaint();
  }
  return outcome;
}

/** A person's navigation, from a control: fire and forget. */
export function byHand<C extends CommandId>(id: C, args: CommandArgs[C]): void {
  void dispatch(id, args, { actor: "human" });
}

// ── visual targets ──────────────────────────────────────────────────────

/** Which rail a target's socket hangs from, and which of its edges faces
 *  that rail (the branch enters there). */
export interface Socket { rail: RailId; side: "left" | "right" }

export interface VisualTarget {
  ref: TargetRef;
  /** JARVIS's own words for it, or the entity's display name. */
  label: string;
  socket: Socket;
  /** The whole presentation's element; null when not on the page. */
  element(): Element | null;
  /** One declared part's element; null when that part is not shown. */
  part(part: string): Element | null;
}

const targets = new Map<string, VisualTarget>();

/** Moved on every registration change: geometry must be measured again. */
export const layoutRevision = createStore(0);

export const presentationId = (ref: TargetRef): string =>
  `${ref.view}|${ref.kind}|${ref.key ?? ""}`;

/** Register a mounted presentation. Refuses an invalid reference and a
 *  second registration of the same presentation (never the first match). */
export function registerTarget(target: VisualTarget): () => void {
  const { part: _, ...whole } = target.ref;
  const problem = targetProblem(whole);
  if (problem) throw new Error(`ui: target ${presentationId(target.ref)}: ${problem}`);
  const id = presentationId(target.ref);
  if (targets.has(id)) throw new Error(`ui: ${id} is registered twice`);
  targets.set(id, target);
  layoutRevision.set(layoutRevision.get() + 1);
  return () => {
    if (targets.get(id) !== target) return;
    targets.delete(id);
    layoutRevision.set(layoutRevision.get() + 1);
  };
}

/** The exact presentation `ref` names, or null. */
export function findTarget(ref: TargetRef): VisualTarget | null {
  return targets.get(presentationId(ref)) ?? null;
}

export function mountedTargets(): VisualTarget[] {
  return [...targets.values()];
}

// ── boxes ───────────────────────────────────────────────────────────────

/** The boxes that hold targets without being targets themselves, each by
 *  a stable id (never its words): the right panel's tab body, the two
 *  rows of view tabs, a workflow's tab strip, the bordered panel under it
 *  holding the open tab, and its phase stepper, and the Folders view's tab
 *  strip. A route marks every box it passes into on its
 *  way to a target, outermost first (uiPipeline.ts `boxesAround`), these
 *  and every registered target that holds the one it points at (a tile, a
 *  centre view, a workflow's detail). */
export const BOX_IDS = ["right.panel", "right.tabs", "main.tabs", "main.workflow.tabs", "main.workflow.view",
                        "main.workflow.phases", "main.folders.tabs", "main.work.tabs"] as const;
export type BoxId = typeof BOX_IDS[number];

const boxes = new Map<Element, BoxId>();

/** A callback ref registering its element as box `id` while it is on the
 *  page. */
export function useUiBox(id: BoxId): (el: Element | null) => void {
  const held = useRef<Element | null>(null);
  return useCallback((el: Element | null) => {
    if (held.current) boxes.delete(held.current);
    held.current = el;
    if (el) boxes.set(el, id);
    layoutRevision.set(layoutRevision.get() + 1);
  }, [id]);
}

/** Every registered box on the page, with its id. */
export function registeredBoxes(): { id: BoxId; element: Element }[] {
  return [...boxes].filter(([el]) => el.isConnected).map(([element, id]) => ({ id, element }));
}

/** Whether `el` is laid out but not drawn (visibility: hidden, as a
 *  chevron kept in place while there is nothing to page to). */
export function undrawn(el: Element): boolean {
  return typeof getComputedStyle === "function" && getComputedStyle(el).visibility !== "visible";
}

/** The part of `el`'s box that can be seen, or null when none can: inside
 *  the window and inside every ancestor that clips what overflows it (a
 *  scrolling view or list), up to a fixed-position one, which nothing
 *  above it clips. Given `inside`, a box drawn inside `el` (a line of its
 *  text), the part of that box instead, which `el` clips too. */
export function seenPart(el: Element, inside?: DOMRect): { left: number; top: number; right: number; bottom: number } | null {
  const r = inside ?? el.getBoundingClientRect();
  let left = Math.max(r.left, 0), top = Math.max(r.top, 0);
  let right = Math.min(r.right, window.innerWidth - 1), bottom = Math.min(r.bottom, window.innerHeight - 1);
  const styled = typeof getComputedStyle === "function";
  const clip = (at: Element) => {
    const style = getComputedStyle(at);
    if (style.overflowX === "visible" && style.overflowY === "visible") return;
    const c = at.getBoundingClientRect();
    left = Math.max(left, c.left); top = Math.max(top, c.top);
    right = Math.min(right, c.right); bottom = Math.min(bottom, c.bottom);
  };
  if (styled && inside) clip(el);
  for (let at: Element | null = el; styled && at && getComputedStyle(at).position !== "fixed"; ) {
    at = at.parentElement ?? null;
    if (!at) break;
    clip(at);
  }
  return right > left && bottom > top ? { left, top, right, bottom } : null;
}

/** Whether `el` is on screen and not covered, hidden or inert. Scrolled
 *  out of its view or list, it is off screen: scrolling brings it back. */
export function visibilityOf(el: Element | null): Visibility {
  if (!el || !el.isConnected) return "unmounted";
  if (el.closest(".hidden, [inert], [hidden]")) return "hidden";
  const r = el.getBoundingClientRect();
  if (r.width === 0 && r.height === 0) return "hidden";
  if (undrawn(el)) return "hidden";
  const seen = seenPart(el);
  if (!seen) return "offscreen";
  // The middle of the part of it that can be seen.
  const x = (seen.left + seen.right) / 2;
  const y = (seen.top + seen.bottom) / 2;
  const top = document.elementFromPoint(x, y);
  // Something drawn over it covers it; the element that holds it does not
  // (it is what the point finds when the element ignores the pointer, as
  // the status line's words do).
  if (top && top !== el && !el.contains(top) && !top.contains(el)) {
    return "occluded";
  }
  return "visible";
}

/** The element a reference's part (or whole) is drawn by, now. */
export function elementOf(target: VisualTarget, part?: string): Element | null {
  if (!part) return target.element();
  const kind = kindDecl(target.ref.kind)!;
  return partsIn(kind, target.ref.view)?.includes(part) ? target.part(part) : null;
}

/** A component's registration of its own presentation: `whole` and
 *  `part(name)` are callback refs for its elements. Registered while
 *  mounted with a reference (null registers nothing: no entity, no target),
 *  so an unmounted object leaves no target and no branch behind. */
export function useUiTarget(spec: { ref: TargetRef; label: string; socket: Socket } | null) {
  const whole = useRef<Element | null>(null);
  const parts = useRef(new Map<string, Element>());
  const latest = useRef(spec);
  latest.current = spec;
  const id = spec ? presentationId(spec.ref) : null;
  useLayoutEffect(() => {
    const now = latest.current;
    if (!now) return;
    return registerTarget({
      ref: { region: now.ref.region, view: now.ref.view, kind: now.ref.kind,
             ...(now.ref.key === undefined ? {} : { key: now.ref.key }) },
      get label() { return latest.current?.label ?? now.label; },
      socket: now.socket,
      element: () => whole.current,
      part: (name) => parts.current.get(name) ?? null,
    });
  }, [id]);
  return {
    whole: (el: Element | null) => { whole.current = el; },
    part: (name: string) => (el: Element | null) => {
      if (el) parts.current.set(name, el);
      else parts.current.delete(name);
    },
  };
}
