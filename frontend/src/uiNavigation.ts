/**
 * The page's one route state and the only code that changes it: which tab
 * the right panel shows, which view the centre shows, and which item each
 * of the centre's detail slots holds. Nothing else sets them. Every change
 * is a registered command (uiRegistry.ts) run by `dispatch`, so a person's
 * click or key and the brain's request run the same handler, with the same
 * availability check and the same side effects.
 *
 *   - `rightTab` and `mainView` are read-only outside this module.
 *   - Each centre detail (workflow, thread, voice diagnostics, subsystem,
 *     network, queue item, work package page, received file, work item) is a `detailSlot`, created by the module that
 *     owns its data, which registers that detail's open and close commands
 *     next to its own state. Opening shows the detail at once; closing it
 *     while it is shown goes back to Conversation. There are no effects
 *     following the slots, so nothing races a selection.
 *   - A detail tab can be selected only while its slot holds an item
 *     (`openDetails`); otherwise `main.tab.select` refuses it as
 *     `no-entity` rather than showing it empty.
 *   - Controls carry a `Binding` (command and arguments) and run it with
 *     `runBinding`; `bindingProps` gives a button its handler and the
 *     `data-ui-command` / `data-ui-args` marks that say what it runs.
 *
 *   - `layout` is the page's arrangement: normal, or one workflow or page
 *     package taking over the centre and the right side (the right panel
 *     folds to a rail of its tab icons, the takeover bar replaces the centre
 *     tabs). It holds only while the centre shows that view and its slot
 *     holds that item: every command that changes either restores the
 *     normal layout in its own handler (`layout.restored`). A reload starts
 *     normal, as the rest of the route state does.
 *
 * Showing a view never moves keyboard focus, and nothing here reports
 * human activity: that comes from trusted input alone (humanActivity.ts).
 */

import { agentsTabLit } from "./agents";
import { createStore, type Store } from "./store";
import { byHand, commandAvailability, navigationRevision, registerCommand, type CommandOutcome } from "./uiRegistry";
import { UI_CONTRACT, type CommandArgs, type CommandId, type SideEffect } from "./uiContract";

export type RightTab = CommandArgs["right.tab.select"]["tab"];
export type MainTab = CommandArgs["main.tab.select"]["tab"];
/** The centre's views that hold one item at a time. */
export type DetailTab = Exclude<MainTab, "conversation" | "folders" | "main">;
/** The details that can take over the page. */
export type TakeoverView = "workflow" | "package";

/** The page's arrangement: normal, or one item taking over, with the right
 *  panel's tab opened over it from the rail (`flyout`), or the read-back's
 *  message boxes as a sheet (`sheet`), or neither: one at a time. */
export type Layout = { mode: "normal" }
  | { mode: "takeover"; view: TakeoverView; key: string; flyout: boolean; sheet: boolean };

/** A value to read and follow; only its owner can change it. */
export interface ReadStore<T> {
  get(): T;
  subscribe(listener: () => void): () => void;
}

const readOnly = <T>(s: Store<T>): ReadStore<T> => ({ get: s.get, subscribe: s.subscribe });

const STATIC_MAIN: readonly MainTab[] = ["conversation", "folders", "main"];

// ── the route state ─────────────────────────────────────────────────────

const right = createStore<RightTab>("agents");
/** The right panel's tab. Agents to begin with. */
export const rightTab = readOnly(right);

/** The view the page opens on: `?view=<id>` names any static view. */
function firstMain(): MainTab {
  const asked = new URLSearchParams(window.location.search).get("view");
  return STATIC_MAIN.includes(asked as MainTab) ? asked as MainTab : "conversation";
}

const main = createStore<MainTab>(firstMain());
/** The centre's selected view. */
export const mainView = readOnly(main);

// ── the layout ──────────────────────────────────────────────────────────

const NORMAL: Layout = { mode: "normal" };
const arrangement = createStore<Layout>(NORMAL);
/** Normal, or the item that has taken over the page. */
export const layout = readOnly(arrangement);

// ── the centre's detail slots ───────────────────────────────────────────

const slots = new Map<DetailTab, ReadStore<unknown>>();
/** How each detail that can take over names its item. */
const takeoverKeys = new Map<DetailTab, (item: unknown) => string>();

/** The key of the item the centre shows, when it is one that can take
 *  over the page; else null. */
function shownTakeover(): { view: TakeoverView; key: string } | null {
  const view = main.get();
  const keyOf = takeoverKeys.get(view as DetailTab);
  const item = slots.get(view as DetailTab)?.get();
  return keyOf && item != null ? { view: view as TakeoverView, key: keyOf(item) } : null;
}

/** Back to the normal layout when what took over is no longer shown: run
 *  by every change of the centre's view or of a slot, after it. */
function settleLayout(): Effects {
  const now = arrangement.get();
  if (now.mode === "normal") return [];
  const shown = shownTakeover();
  if (shown && shown.view === now.view && shown.key === now.key) return [];
  arrangement.set(NORMAL);
  return ["layout.restored"];
}
const open = createStore<ReadonlySet<MainTab>>(new Set(STATIC_MAIN));
/** The centre views that can be shown now: the static ones, and each
 *  detail whose slot holds an item. */
export const openDetails = readOnly(open);

function recountOpen(): void {
  open.set(new Set([...STATIC_MAIN, ...[...slots].filter(([, s]) => s.get() !== null).map(([t]) => t)]));
}

/** What opening or closing a slot did, in the contract's words. */
export type Effects = SideEffect[];

export interface DetailSlot<T> {
  /** The item the slot holds, or null. */
  readonly value: ReadStore<T | null>;
  /** Hold `item` and show this detail: the body of an open command. */
  show(item: T): Effects;
  /** Let the item go: the body of a close command. Shown, the centre goes
   *  back to Conversation. */
  close(): Effects;
  /** The owner's own correction of what the slot holds (a draft that
   *  became an item, an item that went away), not a navigation: null
   *  closes the slot the same way. */
  adjust(item: T | null): void;
}

/** One detail slot of the centre. `same` says when two items are one
 *  (the slot holds one item; another replaces it). A workflow's or a
 *  package's slot also says how its item is named (`takeover`), so it can
 *  take over the page. */
export function detailSlot<T>(tab: DetailTab, same: (a: T, b: T) => boolean = Object.is,
                              takeover?: (item: T) => string): DetailSlot<T> {
  if (slots.has(tab)) throw new Error(`ui: the ${tab} slot already exists`);
  const store = createStore<T | null>(null);
  slots.set(tab, store);
  if (takeover) takeoverKeys.set(tab, takeover as (item: unknown) => string);
  store.subscribe(recountOpen);
  const release = (): void => {
    store.set(null);
    if (main.get() === tab) main.set("conversation");
  };
  return {
    value: readOnly(store),
    show(item) {
      const was = store.get();
      const effects: Effects = [];
      if (main.get() !== tab) effects.push("centre.view-shown");
      if (was !== null && !same(was, item)) effects.push("centre.detail-replaced");
      store.set(item);
      main.set(tab);
      return [...effects, ...settleLayout()];
    },
    close() {
      if (store.get() === null) return [];
      release();
      return ["centre.detail-closed", ...settleLayout()];
    },
    adjust(item) {
      if (item === null) release(); else store.set(item);
      settleLayout();
    },
  };
}

/** The close commands that take nothing and only let their slot go. */
type PlainClose = "work.close" | "workflow.close" | "thread.close" | "voice.diagnostics.close" | "subsystem.close" | "network.close"
  | "package.close" | "bundle.member.close" | "knowledge.close" | "project.close";

/** A slot's close command: offered while the slot holds an item.
 *  `closed`, when given, is told the item it let go, whoever closed it. */
export function registerClose<T>(id: PlainClose, slot: DetailSlot<T>, closed?: (item: T) => void): void {
  registerCommand(id, {
    availability: () => slot.value.get() !== null ? { available: true } : { available: false, reason: "no-entity" },
    execute: () => {
      const item = slot.value.get();
      const effects = slot.close();
      if (item !== null) closed?.(item);
      return { status: "completed", effects };
    },
  });
}

/** Show a static centre view (the Folders view for a folder). */
export function showStatic(tab: (typeof STATIC_MAIN)[number]): Effects {
  if (main.get() === tab) return [];
  main.set(tab);
  return ["centre.view-shown", ...settleLayout()];
}

/** What an open command learned of an item this page has not listed. */
export type ItemRead<T> = { body: T } | { refused: CommandOutcome };

/** Read the item at `path` before opening it: one bounded read (the
 *  contract's reveal limit), never a guess. Refused as `entity-gone` when
 *  JARVIS has no such item (404, or `exists` says the body is empty),
 *  `not-loaded` when it could not be asked in time, and `cancelled` when
 *  the request was withdrawn or anyone navigated meanwhile: a late answer
 *  never overrides the view a person has moved to. */
export async function readForOpen<T>(path: string, signal: AbortSignal,
                                     exists: (body: T) => boolean = () => true): Promise<ItemRead<T>> {
  const before = navigationRevision.get();
  let body: T | false | null;
  try {
    const res = await fetch(path, {
      signal: AbortSignal.any([signal, AbortSignal.timeout(UI_CONTRACT.limits.revealWaitMs)]),
    });
    body = res.ok ? await res.json() as T : res.status === 404 ? false : null;
  } catch {
    body = null;
  }
  const refuse = (status: CommandOutcome["status"], reason: string) => ({ refused: { status, effects: [], reason } });
  if (signal.aborted || navigationRevision.get() !== before) return refuse("cancelled", "superseded");
  if (body === null) return refuse("unavailable", "not-loaded");
  if (body === false || !exists(body)) return refuse("unavailable", "entity-gone");
  return { body };
}

// ── controls ────────────────────────────────────────────────────────────

/** What a control runs: one registered command with its arguments. */
export interface Binding<C extends CommandId = CommandId> {
  command: C;
  args: CommandArgs[C];
}

export const bind = <C extends CommandId>(command: C, args: CommandArgs[C]): Binding<C> => ({ command, args });

/** A person's control ran: the same handler the brain's request runs. */
export function runBinding(b: Binding): void {
  byHand(b.command, b.args as never);
}

/** Whether a binding can run now. */
export function bindingAvailable(b: Binding): boolean {
  return commandAvailability(b.command, b.args as Record<string, unknown>).available;
}

/** A control's props for a binding: its click, and the marks that say
 *  which command it runs (for the page's checks and tests). */
export function bindingProps(b: Binding) {
  return {
    onClick: () => runBinding(b),
    "data-ui-command": b.command,
    "data-ui-args": JSON.stringify(b.args),
  };
}

/** A slot's item as the components that read it see it, for the controls
 *  that still write it as a store: writing runs the binding `toBinding`
 *  gives (an open or a close command), never the slot directly. */
export function routeStore<T>(view: ReadStore<T>, toBinding: (next: T) => Binding | null): Store<T> {
  return {
    get: view.get,
    subscribe: view.subscribe,
    set(next: T) {
      const b = toBinding(next);
      if (b) runBinding(b);
    },
  };
}

// ── the tab rows' commands ──────────────────────────────────────────────

registerCommand("right.tab.select", {
  availability: () => ({ available: true }),
  execute({ tab }) {
    right.set(tab);
    // Taken over, the rail has no panel: the tab opens over the takeover.
    const now = arrangement.get();
    if (now.mode === "takeover" && !now.flyout) arrangement.set({ ...now, flyout: true, sheet: false });
    if (tab === "agents" && agentsTabLit.get()) {   // its message is seen now
      agentsTabLit.set(false);
      return { status: "completed", effects: ["agents.tab-attention-cleared"] };
    }
    return { status: "completed", effects: [] };
  },
});

registerCommand("main.tab.select", {
  availability: ({ tab }) => open.get().has(tab) ? { available: true } : { available: false, reason: "no-entity" },
  execute({ tab }) {
    const shown = main.get() !== tab;
    main.set(tab);
    return { status: "completed", effects: [...(shown ? ["centre.view-shown" as const] : []), ...settleLayout()] };
  },
});

// ── the layout's commands ───────────────────────────────────────────────

registerCommand("layout.takeover", {
  // Only the workflow or page package the centre shows can take over.
  availability: () => shownTakeover() ? { available: true } : { available: false, reason: "no-entity" },
  execute() {
    const shown = shownTakeover()!;
    const now = arrangement.get();
    if (now.mode === "takeover" && now.view === shown.view && now.key === shown.key) {
      return { status: "completed", effects: [] };
    }
    arrangement.set({ mode: "takeover", ...shown, flyout: false, sheet: false });
    return { status: "completed", effects: ["layout.taken-over"] };
  },
});

registerCommand("layout.restore", {
  availability: () => arrangement.get().mode === "takeover" ? { available: true }
    : { available: false, reason: "no-entity" },
  execute() {
    arrangement.set(NORMAL);
    return { status: "completed", effects: ["layout.restored"] };
  },
});

registerCommand("layout.sheet", {
  availability: () => arrangement.get().mode === "takeover" ? { available: true }
    : { available: false, reason: "no-entity" },
  execute({ shown }) {
    const now = arrangement.get();
    if (now.mode === "takeover") arrangement.set({ ...now, sheet: shown, flyout: shown ? false : now.flyout });
    return { status: "completed", effects: [] };
  },
});

registerCommand("layout.flyout.close", {
  availability: () => {
    const now = arrangement.get();
    return now.mode === "takeover" && now.flyout ? { available: true } : { available: false, reason: "no-entity" };
  },
  execute() {
    const now = arrangement.get();
    if (now.mode === "takeover") arrangement.set({ ...now, flyout: false });
    return { status: "completed", effects: [] };
  },
});
