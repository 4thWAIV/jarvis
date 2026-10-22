/**
 * The centre area, between the left column and the right panel: a header row
 * of view tabs along its top, each named in words, and the selected view
 * below.
 *
 *   - Conversation (ConversationView.tsx), shown to begin with.
 *   - Main: the centre as it was before views (the capture boxes above the
 *     four-line captions), unchanged. Hidden from the header for now; the
 *     page opens on it at `?view=main`, so it keeps working and stays tested.
 */

import { useState } from "react";
import { CaptionBox } from "./CaptionBox";
import { CaptureList } from "./CaptureBox";
import { ConversationView } from "./ConversationView";
import { TabRow, type Tab } from "./Tabs";

function MainView() {
  return (
    // The open captures, then the captions directly below them, both centred
    // above the footer.
    <div id="centre-bottom">
      <CaptureList />
      <CaptionBox />
    </div>
  );
}

const VIEWS: Tab[] = [
  {
    id: "conversation",
    title: "Conversation",
    icon: (
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor"
           strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
        <path d="M4 5h16v10H9l-5 4z" />
        <line x1="8" y1="9" x2="16" y2="9" />
        <line x1="8" y1="12" x2="13" y2="12" />
      </svg>
    ),
    view: () => <ConversationView />,
  },
  {
    id: "main",
    title: "Main",
    icon: (
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor"
           strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
        <rect x="3" y="4" width="18" height="16" rx="2" />
      </svg>
    ),
    view: () => <MainView />,
    hidden: true,
  },
];

/** The view the page opens on: `?view=<id>` names any view, hidden or not. */
function firstView(): string {
  const asked = new URLSearchParams(window.location.search).get("view");
  return VIEWS.some((v) => v.id === asked) ? asked! : VIEWS[0].id;
}

export function CentreArea() {
  const [selected, setSelected] = useState(firstView);
  const view = VIEWS.find((v) => v.id === selected) ?? VIEWS[0];
  return (
    <>
      <TabRow tabs={VIEWS} selected={selected} onSelect={setSelected} className="centre-tabs" words />
      {view.view()}
    </>
  );
}
