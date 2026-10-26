/**
 * The right-hand display, mirroring the left column: a row of icons along
 * the top, one per thing Tim can see, running from the right edge leftward
 * (the first is furthest right), and the view for the selected one below.
 */

import { useState, useSyncExternalStore } from "react";
import { AgentsControl } from "./AgentsControl";
import { agentsTabLit } from "./agents";
import { brains } from "./brains";
import { TabRow, type Tab } from "./Tabs";
import { VoiceSummary } from "./VoiceSummary";
import { WeeklyUsage } from "./WeeklyUsage";
import { WorkflowsView } from "./WorkflowsView";

/** Voice health first (VoiceSummary.tsx), then every brain JARVIS has, each
 *  with its own account box; when there is more
 *  than one, the one in use is marked BRAIN and the other STANDBY. Until the
 *  list is read, the running brain's box alone, and a line saying the list
 *  could not be read for as long as that is true (it is read again until it
 *  is: brains.ts). */
function DiagnosticsView() {
  const live = useSyncExternalStore(brains.subscribe, brains.get);
  const added = live.value?.filter((b) => b.added) ?? null;
  const several = added !== null && added.length > 1;
  return (
    <div className="right-view">
      <VoiceSummary />
      {live.error && <p className="read-failure">Couldn't read the brains ({live.error}); trying again.</p>}
      {several
        ? added!.map((b) => <WeeklyUsage key={b.name} provider={b.name}
                                         role={b.active ? "brain" : "standby"} />)
        : <WeeklyUsage />}
    </div>
  );
}

// In order from the right edge leftward; the first is selected to begin with.
const TABS: Tab[] = [
  {
    id: "agents",
    title: "Agents",
    icon: (
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor"
           strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
        <circle cx="16" cy="6" r="2.5" />
        <circle cx="16" cy="12" r="2.5" />
        <circle cx="16" cy="18" r="2.5" />
        <line x1="4" y1="6" x2="11" y2="6" />
        <line x1="4" y1="12" x2="11" y2="12" />
        <line x1="4" y1="18" x2="11" y2="18" />
      </svg>
    ),
    view: () => <div className="right-view agents-view"><AgentsControl /></div>,
  },
  {
    id: "diagnostics",
    title: "Diagnostics",
    icon: (
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor"
           strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
        <polyline points="2,12 6,12 9,4 15,20 18,12 22,12" />
      </svg>
    ),
    view: () => <DiagnosticsView />,
  },
  {
    id: "workflows",
    title: "Workflows",
    icon: (
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor"
           strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
        <rect x="3" y="4" width="6" height="5" rx="1" />
        <rect x="15" y="10" width="6" height="5" rx="1" />
        <rect x="3" y="16" width="6" height="5" rx="1" />
        <path d="M9 6.5h3v12H9M12 12.5h3" />
      </svg>
    ),
    view: () => <WorkflowsView />,
  },
];

export function RightPanel() {
  const [selected, setSelected] = useState(TABS[0].id);
  const lit = useSyncExternalStore(agentsTabLit.subscribe, agentsTabLit.get);
  const tab = TABS.find((t) => t.id === selected) ?? TABS[0];
  const select = (id: string) => {
    setSelected(id);
    if (id === "agents") agentsTabLit.set(false);   // its message is seen now
  };
  return (
    <div id="right-column">
      <TabRow tabs={TABS} selected={selected} onSelect={select} className="right-tabs"
              lit={(t) => t.id === "agents" && lit && selected !== "agents"} />
      <div className="right-panel" role="tabpanel" aria-label={tab.title}>
        {tab.view()}
      </div>
    </div>
  );
}
