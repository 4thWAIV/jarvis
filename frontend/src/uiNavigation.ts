/**
 * Which view each tabbed region shows: the right panel's tab and the
 * centre's view, one shared route state instead of a private `selected` in
 * RightPanel and CentreArea. Only the handlers registered here set it; the
 * panels read it and their tab rows dispatch `right.tab.select` and
 * `main.tab.select` (uiRegistry.ts), so a person's click and the brain's
 * request run the same handler with the same side effects.
 *
 * The centre's detail tabs (workflow, thread, diagnostics, subsystem,
 * network, queue) are offered only while their item is open; selecting one
 * that is not is refused as `no-entity`, never shown empty.
 */

import { agentsTabLit } from "./agents";
import { createStore } from "./store";
import { registerCommand } from "./uiRegistry";
import type { CommandArgs } from "./uiContract";

export type RightTab = CommandArgs["right.tab.select"]["tab"];
export type MainTab = CommandArgs["main.tab.select"]["tab"];

const STATIC_MAIN: readonly MainTab[] = ["conversation", "folders", "main"];

/** The right panel's tab. Agents to begin with. */
export const rightTab = createStore<RightTab>("agents");

/** The view the page opens on: `?view=<id>` names any static view. */
function firstMain(): MainTab {
  const asked = new URLSearchParams(window.location.search).get("view");
  return STATIC_MAIN.includes(asked as MainTab) ? asked as MainTab : "conversation";
}

/** The centre's selected view. */
export const mainView = createStore<MainTab>(firstMain());

/** Which detail tabs have their item open now (CentreArea.tsx reports it). */
export const openDetails = createStore<ReadonlySet<MainTab>>(new Set());

registerCommand("right.tab.select", {
  availability: () => ({ available: true }),
  execute({ tab }) {
    rightTab.set(tab);
    if (tab === "agents") {                 // its message is seen now
      agentsTabLit.set(false);
      return { status: "completed", effects: ["agents.tab-attention-cleared"] };
    }
    return { status: "completed", effects: [] };
  },
});

registerCommand("main.tab.select", {
  availability: ({ tab }) => STATIC_MAIN.includes(tab) || openDetails.get().has(tab)
    ? { available: true } : { available: false, reason: "no-entity" },
  execute({ tab }) {
    mainView.set(tab);
    return { status: "completed", effects: ["centre.view-shown"] };
  },
});
