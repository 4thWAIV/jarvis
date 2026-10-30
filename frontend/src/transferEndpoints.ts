/**
 * Where a ray ends, found in the page each time it is measured: the one
 * place the rays look anything up (rayGeometry.ts is pure, RayLane.tsx
 * draws). Two kinds of end:
 *
 *   - an agent: its orb at the right end of its row in the Agents tab; with
 *     another tab open, the Agents tab's icon, which lights. While the tab
 *     is open but the agent's row is not showing, there is no end and the
 *     ray is not drawn: its last end would be a row that now holds another
 *     agent.
 *   - a queue item (a person's message, or one to oneself): the element the
 *     Queue marks with `data-transfer-endpoint="<thread_id>"`; when that is
 *     not on the page (the Queue is closed, or the row has gone), the Queue
 *     tab's icon. An element carrying the item's own thread id can never be
 *     another item's row.
 *
 * Each end has a layout signal, moved by its list on every commit, so a ray
 * re-measures before a moved row is painted.
 */

import { agentReceipt, agentsLaidOut, agentsTabLit } from "./agents";
import { createStore, type Store } from "./store";
import { rayRoute, type Box } from "./rayGeometry";

/** The attribute a queue row (or its icon) carries for the rays. */
export const TRANSFER_ENDPOINT_ATTR = "data-transfer-endpoint";

/** The Queue's own layout signal: its list moves it on every commit. */
export const queueLaidOut = createStore(0);

export interface Reached {
  colour: string;
  /** The way the ray arrives from at the end (rayArrival). */
  direction: [number, number];
  seconds: number;
}

export interface Endpoint {
  /** The end's box and whether it is a tab's icon; null when there is none
   *  to draw to. */
  find(): { box: Box; toTab: boolean } | null;
  /** The first pulse of an outgoing ray has got there. */
  reached(toTab: boolean, how: Reached): void;
  laidOut: Store<number>;
}

const tabIcon = (title: string) =>
  document.querySelector(`.right-tabs [aria-label='${title}']`);

export function agentEndpoint(sessionId: string, n: number): Endpoint {
  return {
    find() {
      const list = document.querySelector(".agents-list");
      const el = list
        ? list.querySelector(`li[data-session-id="${CSS.escape(sessionId)}"] .agent-orb`)
        : tabIcon("Agents");
      return el ? { box: el.getBoundingClientRect(), toTab: !list } : null;
    },
    reached(toTab, how) {
      if (toTab) agentsTabLit.set(true);
      else agentReceipt.set({ sessionId, n, ...how });
    },
    laidOut: agentsLaidOut,
  };
}

export function queueEndpoint(threadId: string): Endpoint {
  return {
    find() {
      const row = document.querySelector(`[${TRANSFER_ENDPOINT_ATTR}="${CSS.escape(threadId)}"]`);
      const el = row ?? tabIcon("Queue");
      return el ? { box: el.getBoundingClientRect(), toTab: !row } : null;
    },
    // The Queue's own unread cue is the lasting sign; the ray adds none.
    reached() {},
    laidOut: queueLaidOut,
  };
}

/** Every end's layout signal, for a ray to re-measure on. */
export const LAYOUT_SIGNALS: Store<number>[] = [agentsLaidOut, queueLaidOut];

/** The ray's path to `end` now, and whether it ends at a tab's icon; null
 *  while there is nothing to draw to. */
export function measure(end: Endpoint): { d: string; toTab: boolean } | null {
  const orb = document.getElementById("orb-control")?.getBoundingClientRect();
  const column = document.getElementById("right-column")?.getBoundingClientRect();
  const found = end.find();
  if (!orb || !column || !found) return null;
  const menu = document.getElementById("controls")?.getBoundingClientRect();
  return { d: rayRoute(orb, found.box, found.toTab, column, menu?.right ?? 0, window.innerWidth),
           toTab: found.toTab };
}
