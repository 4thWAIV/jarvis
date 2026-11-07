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
 *   - A work item (WorkDetail.tsx), named by its title: offered while one
 *     is open, which clicking its row in the Work tab does; the Work list
 *     stays in view, and closing it goes back to Conversation.
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
 *   - A file a teammate sent (BundleMemberView.tsx), the same way: offered
 *     while one is open, which its Open in the Queue or the brain's
 *     show_bundle_member does. Opening another replaces it.
 *   - Settings (Settings.tsx), the same way: offered while it is open,
 *     which the menu's Settings and Check for updates, the sign-in
 *     reminder's Sign in and first-time setup do.
 *
 * Conversation stays on the page, hidden, while another of these is shown,
 * so going back finds it as it was left, scrolled where it was.
 *
 * While a workflow or a page package has taken over the page
 * (uiNavigation.ts `layout`), the takeover bar (TakeoverBar.tsx) stands in
 * place of the tabs and the notices, holding them and the microphone, and
 * every view widens over the right panel, which folds to a rail.
 *
 * Above whichever view is shown, under the tabs, the sign-in reminder
 * (TeamSignInReminder.tsx, in the notice stack CentreNotices.tsx, with
 * updates waiting, UpdatesNotice.tsx) while this JARVIS is not signed in to a team
 * server; the views move down to make room for it, so it covers nothing.
 */

import { useEffect, useState, useSyncExternalStore } from "react";
import { bind, layout, mainView, type MainTab } from "./uiNavigation";
import { AgentThread } from "./AgentThread";
import { openThread } from "./conversation";
import { CaptionBox } from "./CaptionBox";
import { CaptureList } from "./CaptureBox";
import { ConversationView } from "./ConversationView";
import { useT } from "./locale";
import { TabRow, type Tab } from "./Tabs";
import { WorkDetail } from "./WorkDetail";
import { openWork, workList } from "./work";
import { WorkflowDetail } from "./WorkflowDetail";
import { openWorkflow, subagentOf, workflows } from "./workflows";
import { TakeoverBar, TakeoverSheet } from "./TakeoverBar";
import { openDiagnostics } from "./voiceHealth";
import { openSubsystem, subsystemName as nameOf } from "./subsystems";
import { network, openNetwork } from "./network";
import { NodeView } from "./NodeView";
import { TeamServerView } from "./TeamServerView";
import { useSubsystems } from "./SubsystemList";
import { SubsystemView } from "./SubsystemView";
import { VoiceDiagnosticsView } from "./VoiceDiagnosticsView";
import { directory, openQueue, queue } from "./queue";
import { FoldersView } from "./FoldersView";
import { QueueItemView, queueViewTitle } from "./QueueItemView";
import { CentreNotices } from "./CentreNotices";
import { openPackage } from "./packages";
import { PackageView } from "./PackageView";
import { openBundleMember } from "./bundles";
import { BundleMemberView } from "./BundleMemberView";
import { settingsOpen, SettingsView } from "./Settings";
import { MicCorner, MicPortal } from "./MicControl";
import { openKnowledge } from "./knowledge";
import { KnowledgeView } from "./KnowledgeView";

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

/** A view as defined here: its words come from the catalog by its id when
 *  the area is drawn (`tabs.main.<id>`), or are its item's own name. */
type MainTabView = Omit<Tab, "id" | "title"> & { id: MainTab };

const VIEWS: MainTabView[] = [
  {
    id: "conversation",
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
    icon: null,
    view: () => <FoldersView />,
  },
  {
    id: "main",
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

const KNOWLEDGE_VIEW: MainTabView = {
  id: "knowledge",
  icon: null,
  view: () => <KnowledgeView />,
};

const THREAD_VIEW: MainTabView = {
  id: "thread",
  icon: null,
  view: () => null,
};

const DIAGNOSTICS_VIEW: MainTabView = {
  id: "diagnostics",
  icon: null,
  view: () => <VoiceDiagnosticsView />,
};

const SUBSYSTEM_VIEW: MainTabView = {
  id: "subsystem",
  icon: null,
  view: () => null,
};

const NETWORK_VIEW: MainTabView = {
  id: "network",
  icon: null,
  view: () => null,
};

const QUEUE_VIEW: MainTabView = {
  id: "queue",
  icon: null,
  view: () => null,
};

const PACKAGE_VIEW: MainTabView = {
  id: "package",
  icon: null,
  view: () => null,
};

const FILE_VIEW: MainTabView = {
  id: "file",
  icon: null,
  view: () => null,
};

const SETTINGS_VIEW: MainTabView = {
  id: "settings",
  icon: null,
  view: () => <SettingsView />,
};

const WORK_VIEW: MainTabView = {
  id: "work",
  icon: null,
  view: () => null,
};

const WORKFLOW_VIEW: MainTabView = {
  id: "workflow",
  icon: null,
  view: () => null,
};

/** The selected view and every detail's item are the shared route state
 *  (uiNavigation.ts): opening an item shows its view and closing it goes
 *  back to Conversation, in the open and close commands themselves. This
 *  area only draws them. */
export function CentreArea() {
  const selected = useSyncExternalStore(mainView.subscribe, mainView.get);
  const workflow = useSyncExternalStore(openWorkflow.subscribe, openWorkflow.get);
  const work = useSyncExternalStore(openWork.subscribe, openWork.get);
  const workTitle = ((l) => [...(l?.items ?? []), ...(l?.history ?? [])])(
    useSyncExternalStore(workList.subscribe, workList.get).value).find((i) => i.id === work)?.title;
  const thread = useSyncExternalStore(openThread.subscribe, openThread.get);
  const diagnostics = useSyncExternalStore(openDiagnostics.subscribe, openDiagnostics.get);
  const subsystem = useSyncExternalStore(openSubsystem.subscribe, openSubsystem.get);
  const open = useSubsystems().list.find((s) => s.id === subsystem);
  const subsystemName = open ? nameOf(open) : undefined;
  const networkOpen = useSyncExternalStore(openNetwork.subscribe, openNetwork.get);
  const nodes = useSyncExternalStore(network.subscribe, network.get).value?.snapshot?.nodes;
  const queueOpen = useSyncExternalStore(openQueue.subscribe, openQueue.get)?.thread ?? null;
  const queueState = useSyncExternalStore(queue.subscribe, queue.get);
  const people = useSyncExternalStore(directory.subscribe, directory.get);
  const packageOpen = useSyncExternalStore(openPackage.subscribe, openPackage.get);
  const fileOpen = useSyncExternalStore(openBundleMember.subscribe, openBundleMember.get);
  const settings = useSyncExternalStore(settingsOpen.subscribe, settingsOpen.get);
  const knowledgeOpen = useSyncExternalStore(openKnowledge.subscribe, openKnowledge.get);
  const takenOver = useSyncExternalStore(layout.subscribe, layout.get).mode === "takeover";
  const workflowList = useSyncExternalStore(workflows.subscribe, workflows.get).value;
  const t = useT();
  const titled = (v: MainTabView): Tab => ({ ...v, title: t(`tabs.main.${v.id}`) });
  const views: Tab[] = [
    ...VIEWS.map(titled),
    ...(thread ? [{ ...THREAD_VIEW, title: thread.name ?? t("tabs.main.thread"),
                    view: () => <AgentThread key={thread.sessionId} sessionId={thread.sessionId}
                                             name={thread.name} /> }] : []),
    ...(diagnostics ? [titled(DIAGNOSTICS_VIEW)] : []),
    ...(subsystem ? [{ ...SUBSYSTEM_VIEW, title: subsystemName ?? t("tabs.main.subsystem"),
                       view: () => <SubsystemView key={subsystem} id={subsystem} /> }] : []),
    ...(networkOpen ? [networkOpen.kind === "server"
      ? { ...NETWORK_VIEW, title: t("tabs.main.teamServer"), view: () => <TeamServerView /> }
      : { ...NETWORK_VIEW, title: nodes?.find((n) => n.node_id === networkOpen.nodeId)?.label ?? t("tabs.main.network"),
          view: () => <NodeView key={networkOpen.nodeId} nodeId={networkOpen.nodeId} /> }] : []),
    ...(queueOpen ? [{ ...QUEUE_VIEW, title: queueViewTitle(queueState, people, queueOpen),
                       view: () => <QueueItemView thread={queueOpen} /> }] : []),
    ...(packageOpen ? [{ ...PACKAGE_VIEW, title: packageOpen.name || t("tabs.main.package") }] : []),
    ...(fileOpen ? [{ ...titled(FILE_VIEW),
                      view: () => <BundleMemberView key={`${fileOpen.attachmentId}:${fileOpen.memberId}`}
                                                    member={fileOpen} /> }] : []),
    ...(settings ? [titled(SETTINGS_VIEW)] : []),
    ...(knowledgeOpen ? [titled(KNOWLEDGE_VIEW)] : []),
    ...(work ? [{ ...WORK_VIEW, title: workTitle ?? t("tabs.main.work"),
                  view: () => <WorkDetail key={work} id={work} /> }] : []),
    ...(workflow ? [{ ...WORKFLOW_VIEW, title: t(subagentOf(workflow) ? "tabs.main.subagent" : "tabs.main.workflow"),
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
  // The bar names what took over: a package or a subagent by its tab's
  // title, a workflow by its own name.
  const takeoverName = (view.id === WORKFLOW_VIEW.id && workflow
    ? workflowList?.workflows.find((w) => w.id === workflow)?.name : undefined) ?? view.title;
  return (
    <>
      {takenOver ? <TakeoverBar name={takeoverName} /> : <>
        <TabRow tabs={views} selected={view.id} binding={(v) => bind("main.tab.select", { tab: v.id as MainTab })}
                className="centre-tabs" words box="main.tabs" />
        <CentreNotices />
      </>}
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
      {/* The microphone: in the message input on Conversation, in the bar
          while taken over, else here. */}
      {view.id !== conversation.id && !takenOver && <MicCorner />}
      {takenOver && <TakeoverSheet />}
      <MicPortal />
    </>
  );
}
