/**
 * The centre area, between the left column and the right panel: a header row
 * of view tabs along its top, each named in words, and the selected view
 * below.
 *
 *   - Conversation (ConversationView.tsx), shown to begin with.
 *   - Folders (FoldersView.tsx): the folder catalog and the selected folder
 *     in full. Always offered; choosing a folder in the right panel's
 *     Folders tab selects it. Once opened it stays on the page, hidden, as
 *     Conversation does.
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
 *   - One Queue item (QueueItemView.tsx), named by its subject, the same
 *     way: offered while one is open, which clicking its row in the Queue
 *     does. One view however many messages there are; opening another item
 *     replaces it.
 *   - A page work package (PackageView.tsx), named after it, the same way:
 *     offered while one is open, which its file's Open page in the Queue or
 *     the brain's open_package_page does. Opening another replaces it. Kept
 *     on the page, hidden, while another view is shown, as Folders is, so a
 *     package part way through is not started again.
 *
 * Conversation stays on the page, hidden, while another of these is shown,
 * so going back finds it as it was left, scrolled where it was.
 *
 * Above whichever view is shown, under the tabs, the sign-in reminder
 * (TeamSignInReminder.tsx) while this JARVIS is not signed in to a team
 * server; the views move down to make room for it, so it covers nothing.
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
import { directory, openQueue, queue } from "./queue";
import { showFolders } from "./folders";
import { FoldersView } from "./FoldersView";
import { QueueItemView, queueViewTitle } from "./QueueItemView";
import { TeamSignInReminder } from "./TeamSignInReminder";
import { openPackage } from "./packages";
import { PackageView } from "./PackageView";

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
    id: "folders",
    title: "Folders",
    icon: null,
    view: () => <FoldersView />,
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

const QUEUE_VIEW: Tab = {
  id: "queue",
  title: "Message",
  icon: null,
  view: () => null,
};

const PACKAGE_VIEW: Tab = {
  id: "package",
  title: "Package",
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
  const queueOpen = useSyncExternalStore(openQueue.subscribe, openQueue.get)?.thread ?? null;
  const queueState = useSyncExternalStore(queue.subscribe, queue.get);
  const people = useSyncExternalStore(directory.subscribe, directory.get);
  const packageOpen = useSyncExternalStore(openPackage.subscribe, openPackage.get);
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
  useEffect(() => {
    setSelected((now) => (queueOpen ? "queue" : now === "queue" ? VIEWS[0].id : now));
  }, [queueOpen]);
  useEffect(() => {
    setSelected((now) => (packageOpen ? "package" : now === "package" ? VIEWS[0].id : now));
  }, [packageOpen]);
  // Choosing a folder in the right panel shows the Folders view.
  const foldersAsked = useSyncExternalStore(showFolders.subscribe, showFolders.get);
  useEffect(() => { if (foldersAsked) setSelected("folders"); }, [foldersAsked]);
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
    ...(queueOpen ? [{ ...QUEUE_VIEW, title: queueViewTitle(queueState, people, queueOpen),
                       view: () => <QueueItemView thread={queueOpen} /> }] : []),
    ...(packageOpen ? [{ ...PACKAGE_VIEW, title: packageOpen.name }] : []),
    ...(workflow ? [{ ...WORKFLOW_VIEW, title: subagentOf(workflow) ? "Subagent" : WORKFLOW_VIEW.title,
                      view: () => <WorkflowDetail key={workflow} id={workflow} /> }] : []),
  ];
  const view = views.find((v) => v.id === selected) ?? views[0];
  const conversation = VIEWS[0];
  const folders = VIEWS[1];
  // Once opened, Folders is kept too, so its filters, selection and scroll
  // are where they were left.
  const [foldersKept, setFoldersKept] = useState(view.id === folders.id);
  useEffect(() => { if (view.id === folders.id) setFoldersKept(true); }, [view.id, folders.id]);
  // Kept, hidden, under every view but Main (which is the centre as it was
  // before views, without it).
  const kept = view.id !== "main";
  return (
    <>
      <TabRow tabs={views} selected={view.id} onSelect={setSelected} className="centre-tabs" words />
      <TeamSignInReminder />
      {kept && (
        <div className={`centre-kept${view.id === conversation.id ? "" : " hidden"}`}
             inert={view.id !== conversation.id}>
          {conversation.view()}
        </div>
      )}
      {(foldersKept || view.id === folders.id) && (
        <div className={`centre-kept${view.id === folders.id ? "" : " hidden"}`}
             inert={view.id !== folders.id}>
          {folders.view()}
        </div>
      )}
      {packageOpen && (
        <div className={`centre-kept${view.id === PACKAGE_VIEW.id ? "" : " hidden"}`}
             inert={view.id !== PACKAGE_VIEW.id}>
          <PackageView key={packageOpen.id} info={packageOpen} />
        </div>
      )}
      {view.id !== conversation.id && view.id !== folders.id && view.id !== PACKAGE_VIEW.id
        && view.view()}
    </>
  );
}
