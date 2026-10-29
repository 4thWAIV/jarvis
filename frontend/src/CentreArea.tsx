/**
 * The centre area, between the left column and the right panel: a header row
 * of view tabs along its top, each named in words, and the selected view
 * below.
 *
 *   - Conversation (ConversationView.tsx), shown to begin with.
 *   - Main: the centre as it was before views (the capture boxes above the
 *     four-line captions), unchanged. Hidden from the header for now; the
 *     page opens on it at `?view=main`, so it keeps working and stays tested.
 *   - Workflow (WorkflowDetail.tsx): one workflow in full. Offered only while
 *     one is open, which clicking its row in the Workflows view does; it is
 *     selected then, and closing it goes back to Conversation. A subagent's
 *     row opens its detail in the same place, named Subagent.
 *   - Thread (AgentThread.tsx): everything to and from one agent, the same
 *     way: offered while open, which clicking an agent's name in the
 *     Conversation history does.
 *   - Diagnostics (VoiceDiagnosticsView.tsx): voice health in full, the same
 *     way: offered while open, which clicking the Diagnostics tab's voice
 *     health or the health dot does.
 *   - A subsystem (SubsystemView.tsx), named after it, the same way: offered
 *     while open, which clicking its row in the Diagnostics tab does.
 *   - Team server (TeamServerView.tsx) or one JARVIS instance (NodeView.tsx),
 *     named after it, the same way: offered while open, which clicking the
 *     Team Server tile or an instance's tile in the Network tab does.
 *
 * Conversation stays on the page, hidden, while another of these is shown,
 * so going back finds it as it was left, scrolled where it was.
 */

import { useEffect, useState, useSyncExternalStore } from "react";
import { AgentThread } from "./AgentThread";
import { openThread } from "./conversation";
import { CaptionBox } from "./CaptionBox";
import { CaptureList } from "./CaptureBox";
import { ConversationView } from "./ConversationView";
import { TabRow, type Tab } from "./Tabs";
import { WorkflowDetail } from "./WorkflowDetail";
import { openWorkflow, subagentOf } from "./workflows";
import { openDiagnostics } from "./voiceHealth";
import { openSubsystem } from "./subsystems";
import { network, openNetwork } from "./network";
import { NodeView } from "./NodeView";
import { TeamServerView } from "./TeamServerView";
import { useSubsystems } from "./SubsystemList";
import { SubsystemView } from "./SubsystemView";
import { VoiceDiagnosticsView } from "./VoiceDiagnosticsView";

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

const THREAD_VIEW: Tab = {
  id: "thread",
  title: "Thread",
  icon: null,
  view: () => null,
};

const DIAGNOSTICS_VIEW: Tab = {
  id: "diagnostics",
  title: "Diagnostics",
  icon: null,
  view: () => <VoiceDiagnosticsView />,
};

const SUBSYSTEM_VIEW: Tab = {
  id: "subsystem",
  title: "Subsystem",
  icon: null,
  view: () => null,
};

const NETWORK_VIEW: Tab = {
  id: "network",
  title: "Network",
  icon: null,
  view: () => null,
};

const WORKFLOW_VIEW: Tab = {
  id: "workflow",
  title: "Workflow",
  icon: null,
  view: () => null,
};

/** The view the page opens on: `?view=<id>` names any view, hidden or not. */
function firstView(): string {
  const asked = new URLSearchParams(window.location.search).get("view");
  return VIEWS.some((v) => v.id === asked) ? asked! : VIEWS[0].id;
}

export function CentreArea() {
  const [selected, setSelected] = useState(firstView);
  const workflow = useSyncExternalStore(openWorkflow.subscribe, openWorkflow.get);
  const thread = useSyncExternalStore(openThread.subscribe, openThread.get);
  const diagnostics = useSyncExternalStore(openDiagnostics.subscribe, openDiagnostics.get);
  const subsystem = useSyncExternalStore(openSubsystem.subscribe, openSubsystem.get);
  const subsystemName = useSubsystems().list.find((s) => s.id === subsystem)?.name;
  const networkOpen = useSyncExternalStore(openNetwork.subscribe, openNetwork.get);
  const nodes = useSyncExternalStore(network.subscribe, network.get).value?.snapshot?.nodes;
  // Opening a workflow or a thread shows it; closing it goes back to the
  // first view.
  useEffect(() => {
    setSelected((now) => (workflow ? "workflow" : now === "workflow" ? VIEWS[0].id : now));
  }, [workflow]);
  useEffect(() => {
    setSelected((now) => (thread ? "thread" : now === "thread" ? VIEWS[0].id : now));
  }, [thread]);
  useEffect(() => {
    setSelected((now) => (diagnostics ? "diagnostics" : now === "diagnostics" ? VIEWS[0].id : now));
  }, [diagnostics]);
  useEffect(() => {
    setSelected((now) => (subsystem ? "subsystem" : now === "subsystem" ? VIEWS[0].id : now));
  }, [subsystem]);
  useEffect(() => {
    setSelected((now) => (networkOpen ? "network" : now === "network" ? VIEWS[0].id : now));
  }, [networkOpen]);
  const views: Tab[] = [
    ...VIEWS,
    ...(thread ? [{ ...THREAD_VIEW, title: thread.name ?? "Thread",
                    view: () => <AgentThread key={thread.sessionId} sessionId={thread.sessionId}
                                             name={thread.name} /> }] : []),
    ...(diagnostics ? [DIAGNOSTICS_VIEW] : []),
    ...(subsystem ? [{ ...SUBSYSTEM_VIEW, title: subsystemName ?? "Subsystem",
                       view: () => <SubsystemView key={subsystem} id={subsystem} /> }] : []),
    ...(networkOpen ? [networkOpen.kind === "server"
      ? { ...NETWORK_VIEW, title: "Team server", view: () => <TeamServerView /> }
      : { ...NETWORK_VIEW, title: nodes?.find((n) => n.node_id === networkOpen.nodeId)?.label ?? "Instance",
          view: () => <NodeView key={networkOpen.nodeId} nodeId={networkOpen.nodeId} /> }] : []),
    ...(workflow ? [{ ...WORKFLOW_VIEW, title: subagentOf(workflow) ? "Subagent" : WORKFLOW_VIEW.title,
                      view: () => <WorkflowDetail key={workflow} id={workflow} /> }] : []),
  ];
  const view = views.find((v) => v.id === selected) ?? views[0];
  const conversation = VIEWS[0];
  // Kept, hidden, under every view but Main (which is the centre as it was
  // before views, without it).
  const kept = view.id !== "main";
  return (
    <>
      <TabRow tabs={views} selected={view.id} onSelect={setSelected} className="centre-tabs" words />
      {kept && (
        <div className={`centre-kept${view.id === conversation.id ? "" : " hidden"}`}
             inert={view.id !== conversation.id}>
          {conversation.view()}
        </div>
      )}
      {view.id !== conversation.id && view.view()}
    </>
  );
}
