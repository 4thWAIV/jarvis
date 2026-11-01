/**
 * The right panel's own navigation that no data module holds: where the
 * panel's targets hang from, and the Agents list's Active/All switch and
 * paging, with their registered commands (`agents.filter`, `agents.page`,
 * `agents.reveal`) and the revealer that brings one agent's row into view.
 * AgentsControl.tsx draws the list from this state; a person's switch,
 * chevrons and gestures and the brain's requests run the same handlers.
 *
 * Selecting an agent as a message's recipient (its activation switch) is
 * not navigation and is not here.
 */

import { listAgents, type AgentRow } from "./agents";
import { createStore } from "./store";
import type { CommandArgs, SideEffect } from "./uiContract";
import { rightTab } from "./uiNavigation";
import { registerRevealer } from "./uiNavigationReveal";
import { registerCommand, type Availability, type Socket } from "./uiRegistry";

/** Where the right panel's targets hang from: the right rail, entered on
 *  their right edge. */
export const RIGHT_SOCKET: Socket = { rail: "rail.right", side: "right" };

type ElementRef = (el: Element | null) => void;

/** A row's ref: the row's own button is the whole presentation (its branch
 *  enters that button's edge) and its `row` part. */
export const row = (ui: { whole: ElementRef; part: (name: string) => ElementRef }): ElementRef =>
  (el) => { ui.whole(el); ui.part("row")(el); };

// ── the Agents list ─────────────────────────────────────────────────────

export type Show = CommandArgs["agents.filter"]["show"];
const SHOW_KEY = "jarvis.agents.show";

function readShow(): Show {
  try {
    return localStorage.getItem(SHOW_KEY) === "all" ? "all" : "active";
  } catch {
    return "active";
  }
}

function writeShow(show: Show): void {
  try {
    localStorage.setItem(SHOW_KEY, show);
  } catch {
    // No storage here: the choice lasts until the page closes.
  }
}

/** Active or All: remembered. */
export const agentsShow = createStore<Show>(readShow());
/** The agents as last read; kept while the tab is switched, so the list
 *  shows the last known at once and is then read again. */
export const agentRows = createStore<AgentRow[]>([]);
/** The first row showing, as asked; held in range by `firstShown`. */
export const agentsFirst = createStore(0);
/** How many whole rows fit, measured while the list is on the page; 0 while
 *  it is not (AgentsControl.tsx sets it). */
export const agentsFit = createStore(0);

/** The agents the switch lets through, in their sorted order. */
export const listedAgents = (): AgentRow[] =>
  agentsShow.get() === "all" ? agentRows.get() : agentRows.get().filter((a) => a.active);
/** The last first row that still fills every row. */
export const lastFirst = (): number => Math.max(0, listedAgents().length - agentsFit.get());
/** The first row showing. */
export const firstShown = (): number => Math.min(Math.max(agentsFirst.get(), 0), lastFirst());

/** Show from row `start` (held in range): what that did. */
function moveTo(start: number): SideEffect[] {
  const next = Math.min(Math.max(start, 0), lastFirst());
  if (next === firstShown()) return [];
  agentsFirst.set(next);
  return ["list.paged"];
}

/** Move the list so `sessionId`'s row shows, at the top when it was out of
 *  view: what that did, or null when it is not listed. `agents.reveal` and
 *  the ray to an agent just messaged both use it. */
export function bringAgentIntoView(sessionId: string): SideEffect[] | null {
  const at = listedAgents().findIndex((a) => a.session_id === sessionId);
  if (at < 0) return null;
  const start = firstShown();
  return at >= start && at < start + agentsFit.get() ? [] : moveTo(at);
}

/** The list has left the page: it opens at its top next time. */
export function agentsListLeft(): void {
  agentsFit.set(0);
  agentsFirst.set(0);
}

const onPage = (): Availability =>
  rightTab.get() === "agents" && agentsFit.get() > 0
    ? { available: true } : { available: false, reason: "not-mounted" };

registerCommand("agents.filter", {
  availability: () => ({ available: true }),
  execute({ show }) {
    writeShow(show);
    if (agentsShow.get() === show) return { status: "completed", effects: [] };
    agentsShow.set(show);
    agentsFirst.set(0);
    return { status: "completed", effects: ["list.filtered"] };
  },
});

const STEP = { previous: -1, next: 1 } as const;

registerCommand("agents.page", {
  availability({ direction }) {
    const here = onPage();
    if (!here.available) return here;
    const start = firstShown();
    const room = direction === "previous" ? start > 0 : start < lastFirst();
    return room ? { available: true } : { available: false, reason: "no-entity" };
  },
  execute: ({ direction }) => ({ status: "completed", effects: moveTo(firstShown() + STEP[direction]) }),
});

registerCommand("agents.reveal", {
  availability({ sessionId }) {
    const here = onPage();
    if (!here.available) return here;
    return listedAgents().some((a) => a.session_id === sessionId)
      ? { available: true } : { available: false, reason: "no-entity" };
  },
  execute: ({ sessionId }) => ({ status: "completed", effects: bringAgentIntoView(sessionId) ?? [] }),
});

// Pointing at an agent whose row is not showing: once the tab is shown (the
// row's declared reveal), the list is read if it has not been, and moved to
// the row. One the Active switch hides is not shown by changing the switch.
registerRevealer("right.agents", "agent", async (ref) => {
  const known = () => agentRows.get().some((a) => a.session_id === ref.key);
  if (!known()) {
    try {
      agentRows.set(await listAgents());
    } catch {
      return "not-loaded";
    }
  }
  if (!known()) return "entity-gone";
  return bringAgentIntoView(ref.key!) === null ? "hidden" : true;
});
