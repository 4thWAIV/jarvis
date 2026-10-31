/**
 * The right-hand display, mirroring the left column: six labelled tabs
 * along the top in two rows of three, one per thing Tim can see (Folders,
 * Agents, Queue; Workflows, Network, Diagnostics), and the view for the
 * selected one below. Agents is selected to begin with.
 */

import { useState, useSyncExternalStore } from "react";
import { AgentsControl } from "./AgentsControl";
import { FoldersPanel } from "./FoldersPanel";
import { NetworkPanel } from "./NetworkPanel";
import { queue } from "./queue";
import { QueueIcon } from "./QueueIcons";
import { QueuePanel } from "./QueuePanel";
import { attention } from "./queueStore";
import { agentsTabLit } from "./agents";
import { SubsystemList, useAnyDown } from "./SubsystemList";
import { TabRow, type Tab } from "./Tabs";
import { VoiceSummary } from "./VoiceSummary";
import { WorkflowsView } from "./WorkflowsView";

/** One row per subsystem (SubsystemList.tsx), then voice health in more
 *  detail (VoiceSummary.tsx). The brains' account boxes are in the left
 *  column (BrainUsage.tsx). */
function DiagnosticsView() {
  return (
    <div className="right-view">
      <SubsystemList />
      <VoiceSummary />
    </div>
  );
}

const FOLDERS_TAB: Tab = {
  id: "folders",
  title: "Folders",
  icon: (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor"
         strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
      <path d="M3 7a2 2 0 0 1 2-2h4l2 2.5h8a2 2 0 0 1 2 2V18a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" />
    </svg>
  ),
  view: () => <FoldersPanel />,
};

// Defined in no particular order; ORDER below is how they are laid out.
const TABS: Tab[] = [
  FOLDERS_TAB,
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
  {
    id: "network",
    title: "Network",
    icon: (
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor"
           strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
        <circle cx="12" cy="5" r="3" />
        <circle cx="5" cy="18" r="3" />
        <circle cx="19" cy="18" r="3" />
        <path d="M10.5 8L6.5 15M13.5 8l4 7M8 18h8" />
      </svg>
    ),
    view: () => <NetworkPanel />,
  },
  {
    id: "queue",
    title: "Queue",
    icon: <QueueIcon kind="queue" size={18} />,
    view: () => <QueuePanel />,
  },
];

// Left to right, top row first: two rows of three.
const ORDER = ["folders", "agents", "queue", "workflows", "network", "diagnostics"];
const LAID_OUT = ORDER.map((id) => TABS.find((t) => t.id === id)!);
const FIRST = "agents";

export function RightPanel() {
  const [selected, setSelected] = useState(FIRST);
  const lit = useSyncExternalStore(agentsTabLit.subscribe, agentsTabLit.get);
  // Something waiting and unread lights the Queue tab while another is open.
  const unread = attention(useSyncExternalStore(queue.subscribe, queue.get));
  // Something down shows on the Diagnostics tab whichever tab is open.
  const down = useAnyDown();
  const tab = TABS.find((t) => t.id === selected) ?? TABS.find((t) => t.id === FIRST)!;
  const select = (id: string) => {
    setSelected(id);
    if (id === "agents") agentsTabLit.set(false);   // its message is seen now
  };
  return (
    <div id="right-column">
      <TabRow tabs={LAID_OUT} selected={selected} onSelect={select} className="right-tabs" labelled
              lit={(t) => t.id !== selected && ((t.id === "agents" && lit) || (t.id === "queue" && unread))}
              alert={(t) => t.id === "diagnostics" && down} />
      <div className="right-panel" role="tabpanel" aria-label={tab.title}>
        {tab.view()}
      </div>
    </div>
  );
}
