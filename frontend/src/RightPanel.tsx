/**
 * The right-hand display, mirroring the left column: a row of icons along
 * the top, one per thing Tim can see, running from the right edge leftward
 * (the first is furthest right), and the view for the selected one below.
 */

import { useState, useSyncExternalStore } from "react";
import { brains } from "./brains";
import { unseenDisplay } from "./screen";
import { ScreenView } from "./ScreenView";
import { TabRow, type Tab } from "./Tabs";
import { WeeklyUsage } from "./WeeklyUsage";

/** Every brain JARVIS has, each with its own account box; when there is more
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
    id: "screen",
    title: "Screen",
    icon: (
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor"
           strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
        <rect x="3" y="4" width="18" height="13" rx="2" />
        <line x1="8" y1="21" x2="16" y2="21" />
        <line x1="12" y1="17" x2="12" y2="21" />
      </svg>
    ),
    view: () => <ScreenView />,
  },
];

export function RightPanel() {
  const [selected, setSelected] = useState(TABS[0].id);
  const tab = TABS.find((t) => t.id === selected) ?? TABS[0];
  // The Screen tab lights up, without switching, when something new is shown.
  const unseen = useSyncExternalStore(unseenDisplay.subscribe, unseenDisplay.get);
  return (
    <div id="right-column">
      <TabRow tabs={TABS} selected={selected} onSelect={setSelected} className="right-tabs"
              lit={(t) => t.id === "screen" && unseen && selected !== "screen"} />
      <div className="right-panel" role="tabpanel" aria-label={tab.title}>
        {tab.view()}
      </div>
    </div>
  );
}
