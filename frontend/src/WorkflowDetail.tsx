/**
 * One workflow, in full: the centre area's Workflow view, opened by clicking
 * its row in the right panel's Workflows view. Its own row of tabs:
 *
 * Drawn to be seen at a glance, in the workflow's light red, with words only
 * where they carry meaning (WorkflowVisuals.tsx holds the pictures):
 *
 *   - the header: the workflow's orb, large, its name and its state;
 *   - Overview: the phase stepper, rings of phases and tasks done, the open
 *     decisions, blockers and waits as icons, and the last report as a
 *     caption;
 *   - Phases: a stepper, each phase a step coloured by where it stands,
 *     with a bar of its own tasks under it;
 *   - Tasks: one card per task under its phase, a state icon and a bar;
 *   - Transcript: each attached agent's conversation, prompts, messages
 *     from JARVIS, replies, tool calls and their results each in their own
 *     style, newest at the bottom, read again every few seconds while it
 *     shows, with earlier pages on request;
 *   - Progress: large rings for phases and tasks, and a bar per counted task;
 *   - Waits and Blockers: a card each, its icon, what it is, and a bar of
 *     how long it has been open;
 *   - Decisions: the question large, its options as big buttons with the
 *     recommended one lit, and a box for any other answer;
 *   - Events: the whole log, filtered by kind.
 *
 * Each phase and task shows its live activity (nodeActivity.tsx): a
 * travelling glow while it is running and has had an event within the quiet
 * window, "Paused" once quiet, still red when blocked or failed, still amber
 * while the workflow waits on his decision, and one flash for each event on
 * it.
 *
 * The subagents its attached sessions dispatched while it ran are listed
 * under the Overview, and each one's transcript follows the attached
 * agents' in Transcript. A subagent that belongs to no running workflow is
 * opened here too, from its own row in the list (SubagentDetail): its orb,
 * what it was asked to do, the session that dispatched it, and its
 * transcript.
 *
 * Navigation here is registered (uiRegistry.ts), so a click and the
 * brain's request run the same handler: the tab (`workflow.tab.select`),
 * the events' kind (`workflow.events.filter`), a transcript's earlier page
 * (`workflow.transcript.earlier`), a subagent's row (`subagent.open`) and
 * Close (`workflow.close`). Which tab and kind the open workflow shows is
 * kept in this module and changed only by those commands; a workflow
 * opened anew starts on Overview with every kind. Answering a decision and
 * Dismiss are his own acts on the workflow, not navigation.
 *
 * Everything that can be pointed at is registered with stable keys: the
 * workflow (its header, state, Close, Dismiss and each tab), each phase,
 * task, wait, blocker and decision (`<workflow>/<slug or key>`), the
 * transcript, each attached agent's transcript (`<workflow>/<session>`),
 * each subagent (`<session>/<agent>`) and the events. A phase, task or open
 * item is shown on its own tab: the tab is selected first (a journey's
 * navigate step), then it is pointed at.
 *
 * Everything here was written by the session running the workflow, or by
 * its subagents, and is drawn as plain text.
 */

import { useEffect, useLayoutEffect, useRef, useState, useSyncExternalStore, type ReactNode } from "react";
import { CENTRE_SOCKET, earlierPages } from "./ConversationView";
import { agentStateWords } from "./conversation";
import { useLiveRead } from "./liveRead";
import { codeWords, formatDateTime, t as tNow, useT } from "./locale";
import { createStore } from "./store";
import { DecisionCard } from "./DecisionCard";
import { TabRow, type Tab } from "./Tabs";
import type { CommandArgs, TargetRef } from "./uiContract";
import { bind, bindingProps, runBinding } from "./uiNavigation";
import { registerRevealer } from "./uiNavigationReveal";
import { registerCommand, useUiBox, useUiTarget, type Availability } from "./uiRegistry";
import { TakeoverButton } from "./TakeoverBar";
import { kindDecl, splitKey } from "./uiValidate";
import { TaskBar, useTick } from "./WorkflowsView";
import { activityOf, ActivityMark, ShownSince, useActivityClock, useArrivalFlash, useShownSince,
         workflowItemStanding } from "./nodeActivity";
import { AgeBar, OpenIcon, OpenIcons, phaseStates, PhaseTrack, Ring,
         WorkflowOrb } from "./WorkflowVisuals";
import { ago, answerDecision, dismissals, dismissWorkflow, FINISHED, getEvents,
         getSessionTranscript, getTranscript, getWorkflow, isGone,
         openOnTab, openWorkflow, STATE_WORDS, SUBAGENT_LOOK, SUBAGENT_WORDS, subagentOf, workflowsChanged,
         type OpenItem, type SubagentRow, type SubagentState, type TranscriptEntry,
         type TranscriptPage, type Workflow, type WorkflowItem } from "./workflows";

const TRANSCRIPT_EVERY_MS = 5000;

function clock(seconds: number): string {
  return formatDateTime(seconds * 1000, { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

// ── what the open workflow shows ────────────────────────────────────────

type WorkflowTab = CommandArgs["workflow.tab.select"]["tab"];

interface Shown { workflowId: string | null; tab: WorkflowTab; kind: string }

const FIRST: Omit<Shown, "workflowId"> = { tab: "overview", kind: "" };

/** The open workflow's tab and event kind ("" for every kind). */
const shown = createStore<Shown>({ workflowId: null, ...FIRST });

/** What `workflowId` shows: its own choice while it stays open, else the
 *  first tab and every kind. */
const shownFor = (workflowId: string): Shown => {
  const now = shown.get();
  return now.workflowId === workflowId ? now : { workflowId, ...FIRST };
};

// A workflow closed, or replaced by another, forgets its tab and kind.
openWorkflow.subscribe(() => {
  if (shown.get().workflowId !== null && openWorkflow.get() !== shown.get().workflowId) {
    shown.set({ workflowId: null, ...FIRST });
  }
});

/** Offered for the workflow the centre holds, never another. */
const whileOpen = (workflowId: string): Availability =>
  openWorkflow.get() === workflowId ? { available: true } : { available: false, reason: "no-entity" };

/** Show `tab` of the open workflow `workflowId`. */
const select = (workflowId: string, tab: WorkflowTab) => shown.set({ ...shownFor(workflowId), tab });

// workflow.open with a tab opens it there.
openOnTab(select);

registerCommand("workflow.tab.select", {
  availability: ({ workflowId }) => whileOpen(workflowId),
  execute({ workflowId, tab }) {
    select(workflowId, tab);
    return { status: "completed", effects: [] };
  },
});

registerCommand("workflow.events.filter", {
  availability: ({ workflowId }) => whileOpen(workflowId),
  execute({ workflowId, kind }) {
    shown.set({ ...shownFor(workflowId), kind: kind ?? "" });
    return { status: "completed", effects: ["list.filtered"] };
  },
});

// ── bringing a workflow's item into view ────────────────────────────────

/** The tab each kind of item in a workflow is drawn on. */
const TAB_OF = {
  workflowPhase: "phases", workflowTask: "tasks", workflowDecision: "decisions", workflowWait: "waits",
  workflowBlocker: "blockers", workflowAgent: "transcript", workflowTranscript: "transcript",
  workflowEvents: "events",
} as const satisfies Partial<Record<DetailKind, WorkflowTab>>;

// Pointing at a phase, task, open item, transcript or events that is not
// drawn: its workflow (the first part of its key, or the key itself) is
// opened and the tab that draws it selected, both by their registered
// commands, run as the caller with their effects reported. Nothing else.
for (const kind of Object.keys(TAB_OF) as (keyof typeof TAB_OF)[]) {
  registerRevealer("main.workflow", kind, async (ref, { navigate }) => {
    const decl = kindDecl(kind)!;
    const workflowId = decl.keyOf ? splitKey(decl, ref.key!)[0] : ref.key!;
    const tab = TAB_OF[kind];
    const opened = openWorkflow.get() === workflowId
      ? await navigate("workflow.tab.select", { workflowId, tab })
      : await navigate("workflow.open", { workflowId, tab });
    return opened ? true : "no-entity";

  });
}

// A subagent's own detail (its header, transcript and earlier page) is
// opened by subagent.open. Its row inside a workflow belongs to whichever
// workflow lists it, which its key does not say: not revealed.
registerRevealer("main.workflow", "subagent", async (ref, { navigate }) => {
  if (ref.part === "row") return "not-mounted";
  const [sessionId, agentId] = splitKey(kindDecl("subagent")!, ref.key!);
  return await navigate("subagent.open", { sessionId, agentId }) ? true : "no-entity";
});

// ── transcripts' earlier pages ──────────────────────────────────────────

/** A transcript's key among those shown: its session, or `<session>/<agent>`
 *  for a subagent's. */
const transcriptKey = (sessionId: string, agentId?: string) => (agentId ? `${sessionId}/${agentId}` : sessionId);

const transcriptPages = earlierPages<TranscriptEntry>((key, before) => {
  const [sessionId, agentId] = key.split("/");
  return getSessionTranscript(sessionId, before, agentId)
    .then((page) => ({ items: page.entries, earlier: page.earlier }));
});

registerCommand("workflow.transcript.earlier", {
  availability: ({ sessionId, agentId }) => transcriptPages.availability(transcriptKey(sessionId, agentId)),
  execute: ({ sessionId, agentId }, { signal }) => transcriptPages.load(transcriptKey(sessionId, agentId), signal),
});

// ── targets ─────────────────────────────────────────────────────────────

type DetailKind = "workflow" | "workflowPhase" | "workflowTask" | "workflowDecision" | "workflowWait"
  | "workflowBlocker" | "workflowTranscript" | "workflowAgent" | "workflowEvents" | "subagent";

/** One thing in the workflow view, registered while it is drawn. */
function useDetailTarget(kind: DetailKind, key: string, label: string) {
  const ref: TargetRef = { region: "main", view: "main.workflow", kind, key };
  return useUiTarget({ ref, label, socket: CENTRE_SOCKET });
}

/** A part's callback ref, from a registered target. */
type PartRef = (part: string) => (el: Element | null) => void;

/** Joins a component's own ref and a registered part's. */
const both = <E extends Element>(own: { current: E | null }, part: (el: Element | null) => void) =>
  (el: E | null) => { own.current = el; part(el); };

// ── the views ───────────────────────────────────────────────────────────

/** A workflow's detail, or a subagent's when `id` names one. */
export function WorkflowDetail({ id }: { id: string }) {
  const subagent = subagentOf(id);
  return subagent ? <SubagentDetail {...subagent} /> : <OneWorkflow id={id} />;
}

/** The workflow's tabs, in order; each is titled `main.workflow.tab.<id>`. */
const TABS: readonly WorkflowTab[] = ["overview", "phases", "tasks", "transcript", "progress", "waits",
                                      "decisions", "blockers", "events"];

function OneWorkflow({ id }: { id: string }) {
  const t = useT();
  const live = useLiveRead(() => getWorkflow(id), [workflowsChanged]);
  const now = useSyncExternalStore(shown.subscribe, shown.get);
  const { tab } = now.workflowId === id ? now : FIRST;
  useTick(30000);
  const w = live.value;
  useSyncExternalStore(dismissals.subscribe, dismissals.get);
  const gone = w ? isGone(w) : false;
  const ui = useDetailTarget("workflow", id, w?.name ?? t("main.workflow.workflow"));
  const tabView = useUiBox("main.workflow.view");
  const views: Record<WorkflowTab, (w: Workflow) => ReactNode> = {
    overview: (w) => <Overview w={w} />,
    phases: (w) => <Phases w={w} />,
    tasks: (w) => <Tasks w={w} />,
    transcript: () => <Transcript id={id} />,
    progress: (w) => <Progress w={w} />,
    waits: (w) => <Opened workflowId={w.id} kind="wait" items={w.open.filter((o) => o.kind === "wait")}
                          empty={t("main.workflow.noWaits")} />,
    decisions: (w) => <Decisions w={w} />,
    blockers: (w) => <Opened workflowId={w.id} kind="blocker" items={w.open.filter((o) => o.kind === "blocker")}
                             empty={t("main.workflow.noBlockers")} />,
    events: () => <Events id={id} />,
  };
  const tabs: Tab[] = w ? TABS
    .map((tab) => ({ id: tab, title: t(`main.workflow.tab.${tab}`), icon: null, view: () => views[tab](w) })) : [];
  const selected = tabs.find((t) => t.id === tab) ?? tabs[0];
  const row = useRef<HTMLDivElement | null>(null);
  useTabParts(row, id, ui.part, tabs.length > 0);
  const since = useShownSince(id);
  return (
    <ShownSince.Provider value={since}>
    <div id="workflow-detail" ref={ui.whole}>
      <div className="workflow-detail-head" ref={ui.part("header")}>
        <WorkflowOrb workflowId={w?.id} state={w?.state ?? "running"} size={88} particles={600} />
        <span className="workflow-detail-title">
          <span className="workflow-detail-name">{w?.name ?? t("main.workflow.workflow")}</span>
          {w && <span className="workflow-detail-pills">
            <span className={`workflow-detail-state state-${w.state}`} ref={ui.part("state")}>{STATE_WORDS[w.state]}</span>
            {gone && <span className="workflow-detail-dismissed">{t("main.workflow.dismissed")}</span>}
          </span>}
        </span>
        {w && FINISHED.has(w.state) && !gone && (
          <button data-ui-action="domain" type="button" className="workflow-detail-dismiss" ref={ui.part("dismiss")}
                  onClick={() => void dismissWorkflow(w.id)}>{t("main.workflow.dismiss")}</button>
        )}
        <TakeoverButton partRef={ui.part("takeover")} />
        <button type="button" className="workflow-close" title={t("main.common.close")} aria-label={t("main.common.close")}
                ref={ui.part("close")} {...bindingProps(bind("workflow.close", {}))}>×</button>
      </div>
      {live.error && <p className="read-failure">{t("main.workflow.unreadable", { error: live.error })}</p>}
      {w && selected && <>
        <div ref={row} style={{ flex: "none" }}>
          <TabRow tabs={tabs} selected={selected.id} className="workflow-tabs" words box="main.workflow.tabs"
                  binding={(t) => bind("workflow.tab.select", { workflowId: id, tab: t.id as WorkflowTab })} />
        </div>
        <div className="workflow-tab-view" role="tabpanel" aria-label={selected.title} ref={tabView}>
          {selected.view()}
        </div>
      </>}
    </div>
    </ShownSince.Provider>
  );
}

/** Each tab's button as the workflow's `tab.<id>` part. The row draws the
 *  buttons; each is found by the command and arguments it runs (its
 *  binding's marks), never by its words or its place. */
function useTabParts(row: { current: HTMLDivElement | null }, workflowId: string, part: PartRef, drawn: boolean) {
  useLayoutEffect(() => {
    const buttons = [...(row.current?.querySelectorAll<HTMLElement>('[data-ui-command="workflow.tab.select"]') ?? [])];
    const set = TABS.map((tab) => {
      const el = buttons.find((b) => {
        const args = JSON.parse(b.dataset.uiArgs ?? "{}") as { workflowId?: string; tab?: string };
        return args.workflowId === workflowId && args.tab === tab;
      }) ?? null;
      part(`tab.${tab}`)(el);
      return tab;
    });
    return () => set.forEach((tab) => part(`tab.${tab}`)(null));
  }, [row, workflowId, part, drawn]);
}

/** The rings of phases and tasks done, at `size`. */
function DoneRings({ w, size }: { w: Workflow; size: number }) {
  const t = useT();
  return (
    <>
      <Ring done={w.phases_done} total={w.phases_total} size={size} label={t("main.workflow.phasesDone")} />
      <span className="ring-caption">{t("main.workflow.phasesCaption")}</span>
      <Ring done={w.tasks_done} total={w.tasks_total} size={size} label={t("main.workflow.tasksDone")} />
      <span className="ring-caption">{t("main.workflow.tasksCaption")}</span>
    </>
  );
}

function Overview({ w }: { w: Workflow }) {
  const t = useT();
  return (
    <div className="workflow-overview">
      <PhaseTrack w={w} labels />
      <div className="workflow-overview-rings">
        <DoneRings w={w} size={84} />
        <OpenIcons w={w} />
      </div>
      {w.last_summary && <p className="workflow-caption">{w.last_summary}</p>}
      <p className="workflow-times">{t("main.workflow.times", { started: ago(w.started_at), heard: ago(w.last_event_at) })}</p>
      {!!w.subagents?.length && <Subagents agents={w.subagents} />}
    </div>
  );
}

/** The subagents a workflow's sessions dispatched: one line each, working
 *  ones first, each opening its own detail. */
function Subagents({ agents }: { agents: SubagentRow[] }) {
  const t = useT();
  return (
    <section className="workflow-subagents">
      <h5>{t("main.workflow.subagents")}</h5>
      <ul>
        {agents.map((a) => <SubagentLine key={a.key} a={a} />)}
      </ul>
    </section>
  );
}

function SubagentLine({ a }: { a: SubagentRow }) {
  const t = useT();
  const title = a.description || a.type || t("main.workflow.subagent");
  const ui = useDetailTarget("subagent", transcriptKey(a.session_id, a.id), title);
  return (
    <li>
      <button type="button" className="workflow-subagent" data-subagent-key={a.key} ref={ui.whole}
              data-state={a.state} {...bindingProps(bind("subagent.open", { sessionId: a.session_id, agentId: a.id }))}>
        <span className={`subagent-dot subagent-${a.state}`} ref={ui.part("state")} />
        <span className="workflow-subagent-name" ref={ui.part("row")}>{title}</span>
        <span className="workflow-subagent-of">{a.session_name}</span>
        {a.workflow_run && <span className="workflow-subagent-of workflow-subagent-run">
          {t("main.workflow.workflowRun", { run: a.workflow_run })}</span>}
        <span className="workflow-subagent-age">{ago(a.written)}</span>
        <span className={`workflow-subagent-state subagent-${a.state}`}>{SUBAGENT_WORDS[a.state]}</span>
      </button>
    </li>
  );
}

/** One subagent, opened from its row: its orb ringed by its state, what it
 *  was asked to do, the session that dispatched it, and its transcript. */
function SubagentDetail({ sessionId, agentId }: { sessionId: string; agentId: string }) {
  const t = useT();
  const [page, setPage] = useState<TranscriptPage | null>(null);
  const state = (page?.state ?? "") as SubagentState;
  const known = state in SUBAGENT_LOOK;
  const ui = useDetailTarget("subagent", transcriptKey(sessionId, agentId), page?.name ?? t("main.workflow.subagent"));
  return (
    <div id="workflow-detail" className="subagent-detail" data-subagent-key={`agent:${sessionId}/${agentId}`}
         ref={ui.whole}>
      <div className="workflow-detail-head" ref={ui.part("header")}>
        <WorkflowOrb workerId={agentId} state={known ? SUBAGENT_LOOK[state] : "silent"} size={88} particles={600} />
        <span className="workflow-detail-title">
          <span className="workflow-detail-name">{page?.name ?? t("main.workflow.subagent")}</span>
          {page && <span className="workflow-detail-pills">
            <span className={`workflow-detail-state state-${known ? SUBAGENT_LOOK[state] : "silent"}`}
                  ref={ui.part("state")}>
              {known ? SUBAGENT_WORDS[state] : t("main.workflow.unknownState")}</span>
            {page.session_name && <span className="workflow-detail-dismissed">
              {t("main.workflow.subagentOf", { name: page.session_name })}</span>}
          </span>}
        </span>
        <TakeoverButton partRef={ui.part("takeover")} />
        <button type="button" className="workflow-close" title={t("main.common.close")} aria-label={t("main.common.close")}
                ref={ui.part("close")} {...bindingProps(bind("workflow.close", {}))}>×</button>
      </div>
      <div className="workflow-tab-view subagent-transcript">
        <AgentTranscript sessionId={sessionId} subagent={agentId} fallbackName={t("main.workflow.subagent")}
                         gone={false} bare onPage={setPage} partRef={ui.part} />
      </div>
    </div>
  );
}

const ITEM_ICONS: Record<string, string> = {
  pending: "○", running: "◐", waiting: "◔", blocked: "■", done: "●", failed: "✕", skipped: "–",
};

function ItemIcon({ item, partRef }: { item: WorkflowItem; partRef?: (el: Element | null) => void }) {
  useT();
  return <span className={`item-icon item-${item.state}`} title={codeWords("main.workflow.item", item.state) ?? item.state}
               ref={partRef}>
    {ITEM_ICONS[item.state] ?? "○"}</span>;
}

function Phases({ w }: { w: Workflow }) {
  const t = useT();
  const phases = phaseStates(w);
  const stepper = useUiBox("main.workflow.phases");
  if (!phases.length) return <p className="workflow-empty">{t("main.workflow.noPhases")}</p>;
  const tasks = w.items.filter((i) => i.kind === "task");
  return (
    <ol className="phase-stepper" ref={stepper}>
      {phases.map(({ item, at }, n) => (
        <PhaseStep key={item.slug} workflowId={w.id} workflowState={w.state} item={item} at={at} n={n}
                   own={tasks.filter((t) => t.phase === item.slug)} />
      ))}
    </ol>
  );
}

/** A phase's or task's live activity (nodeActivity.tsx) and the ref that
 *  flashes it once for each event on it. */
function useItemActivity(item: WorkflowItem, workflowState: string) {
  const now = useActivityClock([item.quiet_at]);
  return { activity: activityOf(workflowItemStanding(item.state, workflowState), item.quiet_at, now),
           arrived: useArrivalFlash(item.heard_at) };
}

function PhaseStep({ workflowId, workflowState, item, at, n, own }: {
  workflowId: string; workflowState: string; item: WorkflowItem; at: string; n: number; own: WorkflowItem[] }) {
  const ui = useDetailTarget("workflowPhase", `${workflowId}/${item.slug}`, item.title);
  const done = own.filter((t) => t.state === "done" || t.state === "skipped").length;
  const { activity, arrived } = useItemActivity(item, workflowState);
  return (
    <li className={`phase-step at-${at}`} data-phase={item.slug} data-activity={activity ?? undefined}
        ref={(el) => { ui.whole(el); ui.part("row")(el); }}>
      <span className="phase-step-dot" ref={(el) => { ui.part("state")(el); arrived(el); }}>
        {at === "done" ? "✓" : n + 1}</span>
      <span className="phase-step-title">{item.title}</span>
      {activity === "paused" && <ActivityMark activity={activity} target={ui.part("activity")} />}
      {own.length > 0 && <><TaskBar done={done} total={own.length} />
        <span className="phase-step-count" ref={ui.part("progress")}>{done}/{own.length}</span></>}
    </li>
  );
}

function Tasks({ w }: { w: Workflow }) {
  const t = useT();
  const tasks = w.items.filter((i) => i.kind === "task");
  if (!tasks.length) return <p className="workflow-empty">{t("main.workflow.noTasks")}</p>;
  const groups = phaseStates(w).map(({ item, at }) => ({ phase: item, at,
    tasks: tasks.filter((t) => t.phase === item.slug) })).filter((g) => g.tasks.length);
  const loose = tasks.filter((t) => !t.phase);
  return (
    <div className="task-board">
      {groups.map((g) => (
        <section key={g.phase.slug} className={`task-column at-${g.at}`}>
          <h5>{g.phase.title}</h5>
          {g.tasks.map((t) => <TaskCard key={t.slug} workflowId={w.id} workflowState={w.state} task={t} />)}
        </section>
      ))}
      {loose.length > 0 && <section className="task-column"><h5>{t("main.workflow.otherTasks")}</h5>
        {loose.map((t) => <TaskCard key={t.slug} workflowId={w.id} workflowState={w.state} task={t} />)}</section>}
    </div>
  );
}

function TaskCard({ workflowId, workflowState, task: t }: {
  workflowId: string; workflowState: string; task: WorkflowItem }) {
  const ui = useDetailTarget("workflowTask", `${workflowId}/${t.slug}`, t.title);
  const { activity, arrived } = useItemActivity(t, workflowState);
  return (
    <div className={`task-card item-${t.state}`} data-task={t.slug} data-activity={activity ?? undefined}
         ref={(el) => { ui.whole(el); ui.part("row")(el); arrived(el); }}>
      <ItemIcon item={t} partRef={ui.part("state")} />
      <span className="task-card-title">{t.title}
        {activity === "paused" && <> <ActivityMark activity={activity} target={ui.part("activity")} /></>}</span>
      {t.total ? <TaskBar done={t.done ?? 0} total={t.total} barRef={ui.part("progress")} /> : null}
    </div>
  );
}

function Progress({ w }: { w: Workflow }) {
  const counted = w.items.filter((i) => i.kind === "task" && i.total);
  return (
    <div className="workflow-progress">
      <div className="workflow-overview-rings">
        <DoneRings w={w} size={120} />
      </div>
      {counted.map((t) => <ProgressRow key={t.slug} workflowId={w.id} workflowState={w.state} task={t} />)}
    </div>
  );
}

/** A counted task on the Progress tab: the same task, its bar large. */
function ProgressRow({ workflowId, workflowState, task: t }: {
  workflowId: string; workflowState: string; task: WorkflowItem }) {
  const ui = useDetailTarget("workflowTask", `${workflowId}/${t.slug}`, t.title);
  const { activity, arrived } = useItemActivity(t, workflowState);
  return (
    <div className="progress-row" data-activity={activity ?? undefined}
         ref={(el) => { ui.whole(el); ui.part("row")(el); arrived(el); }}>
      <ItemIcon item={t} partRef={ui.part("state")} /><span className="task-card-title">{t.title}
        {activity === "paused" && <> <ActivityMark activity={activity} target={ui.part("activity")} /></>}</span>
      <TaskBar done={t.done ?? 0} total={t.total ?? 0} />
      <span className="phase-step-count" ref={ui.part("progress")}>{t.done ?? 0}/{t.total}</span>
    </div>
  );
}

function Opened({ workflowId, kind, items, empty }: {
  workflowId: string; kind: "wait" | "blocker"; items: OpenItem[]; empty: string }) {
  if (!items.length) return <p className="workflow-empty">{empty}</p>;
  return (
    <div className="open-cards">
      {items.map((o) => <OpenCard key={o.key} workflowId={workflowId} kind={kind} o={o} />)}
    </div>
  );
}

function OpenCard({ workflowId, kind, o }: { workflowId: string; kind: "wait" | "blocker"; o: OpenItem }) {
  const t = useT();
  const ui = useDetailTarget(kind === "wait" ? "workflowWait" : "workflowBlocker", `${workflowId}/${o.key}`,
                             t(`main.workflow.open.${kind}`));
  return (
    <div className={`open-card open-card-${kind}`} data-key={o.key} ref={(el) => { ui.whole(el); ui.part("row")(el); }}>
      <OpenIcon kind={kind} />
      <span className="open-card-text">{o.summary}</span>
      <AgeBar since={o.opened_at} />
      <span className="open-card-age">{ago(o.opened_at)}{o.detail.until ? <> · {t("main.workflow.until", { until: String(o.detail.until) })}</> : null}
        {o.unlinked ? <> · <span className="open-card-unlinked">{t("main.workflow.unlinked")}</span></> : null}</span>
    </div>
  );
}

function Decisions({ w }: { w: Workflow }) {
  const t = useT();
  const open = w.open.filter((o) => o.kind === "decision");
  if (!open.length) return <p className="workflow-empty">{t("main.workflow.noDecisions")}</p>;
  return (
    <ul className="workflow-decisions-list">
      {open.map((o) => <Decision key={o.key} id={w.id} decision={o} />)}
    </ul>
  );
}

/** One decision, drawn by the shared card (DecisionCard.tsx). */
function Decision({ id, decision: o }: { id: string; decision: OpenItem }) {
  const t = useT();
  const ui = useDetailTarget("workflowDecision", `${id}/${o.key}`, t("main.workflow.open.decision"));
  const options = Array.isArray(o.detail.options) ? o.detail.options.map(String) : [];
  const recommended = typeof o.detail.recommendation === "string" ? o.detail.recommendation : null;
  return <DecisionCard question={o.summary} options={options} recommendation={recommended} ui={ui}
                       dataKey={o.key} icon={<OpenIcon kind="decision" />}
                       answer={(value) => answerDecision(id, o.key, value)} />;
}

function Transcript({ id }: { id: string }) {
  const t = useT();
  const live = useLiveRead(() => getTranscript(id), [workflowsChanged]);
  const ui = useDetailTarget("workflowTranscript", id, t("main.workflow.tab.transcript"));
  const agents = live.value?.agents;
  return (
    <div className="workflow-transcript" ref={(el) => { ui.whole(el); ui.part("list")(el); }}>
      {live.error && <p className="read-failure">{t("main.workflow.transcriptUnreadable", { error: live.error })}</p>}
      {agents && agents.length === 0 && <p className="workflow-empty">{t("main.workflow.noAgents")}</p>}
      {agents?.map((a) => <AgentTranscript key={a.session_id} sessionId={a.session_id} workflowId={id}
                                           fallbackName={a.name ?? a.session_id} gone={a.state === "gone"} />)}
      {live.value?.subagents.map((a) => (
        <AgentTranscript key={a.key} sessionId={a.session_id} subagent={a.id}
                         fallbackName={a.description || t("main.workflow.subagent")} gone={false} />
      ))}
    </div>
  );
}

/** Who an entry of a transcript is from: a tool by its own name. */
function roleLabel(e: TranscriptEntry): string {
  if (e.role === "tool" && e.tool) return e.tool;
  if (e.role === "result" && e.tool) return tNow("main.workflow.toolResult", { tool: e.tool });
  return tNow(`main.workflow.role.${e.role}`);
}

/** How an agent's transcript is registered: an attached agent's in its
 *  workflow (`workflowAgent`), a subagent's on its own (`subagent`), or
 *  through the parts of the subagent detail it sits in (`partRef`). */
type TranscriptPart = "section" | "transcript" | "entries" | "earlier";

function useTranscriptTarget(sessionId: string, name: string, workflowId?: string, subagent?: string,
                             partRef?: PartRef): (part: TranscriptPart) => (el: Element | null) => void {
  const own = useUiTarget(partRef ? null : {
    ref: subagent
      ? { region: "main", view: "main.workflow", kind: "subagent", key: transcriptKey(sessionId, subagent) }
      : { region: "main", view: "main.workflow", kind: "workflowAgent", key: `${workflowId}/${sessionId}` },
    label: name, socket: CENTRE_SOCKET,
  });
  if (partRef) return partRef;
  // An attached agent's section is its whole; a subagent's is its transcript.
  return (part: TranscriptPart) => (part === "section" || part === "transcript"
    ? (el) => { own.whole(el); own.part(part)(el); } : own.part(part));
}

/** One agent's transcript: the latest page, kept current, and earlier pages
 *  added above it by `workflow.transcript.earlier`. Stays at the bottom
 *  while it is there. With `subagent`, that subagent of the session
 *  instead; `bare` leaves out its own heading, `onPage` hears each latest
 *  page read, and `partRef` registers it through the detail it sits in. */
function AgentTranscript({ sessionId, workflowId, fallbackName, gone, subagent, bare, onPage, partRef }: {
  sessionId: string; workflowId?: string; fallbackName: string; gone: boolean; subagent?: string; bare?: boolean;
  onPage?: (page: TranscriptPage) => void; partRef?: PartRef }) {
  const [latest, setLatest] = useState<TranscriptEntry[]>([]);
  const [cursor, setCursor] = useState<number | null | undefined>(undefined);
  const [name, setName] = useState(fallbackName);
  const [state, setState] = useState("");
  const [parent, setParent] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const box = useRef<HTMLDivElement | null>(null);
  const atBottom = useRef(true);
  const key = transcriptKey(sessionId, subagent);
  const { older, earlier } = transcriptPages.use(key, cursor);
  const part = useTranscriptTarget(sessionId, name, workflowId, subagent, partRef);
  const t = useT();

  useEffect(() => {
    if (gone) return;
    let stopped = false;
    const read = () => getSessionTranscript(sessionId, null, subagent).then((page) => {
      if (stopped) return;
      setLatest(page.entries);
      setName(page.name);
      setState(page.state);
      setParent(page.session_name ?? null);
      onPage?.(page);
      setError(null);
      setCursor(page.earlier);
    }).catch((e: Error) => { if (!stopped) setError(e.message); });
    read();
    const timer = setInterval(read, TRANSCRIPT_EVERY_MS);
    const unsubscribe = workflowsChanged.subscribe(read);
    return () => { stopped = true; clearInterval(timer); unsubscribe(); };
  }, [sessionId, subagent, gone]);                    // eslint-disable-line react-hooks/exhaustive-deps

  useLayoutEffect(() => {
    const el = box.current;
    if (el && atBottom.current) el.scrollTop = el.scrollHeight;
  }, [latest]);

  // The latest page and the earlier ones overlap once the transcript grows.
  const seen = new Set(latest.map((e) => `${e.offset}:${e.role}:${e.text.length}`));
  const entries = [...older.filter((e) => !seen.has(`${e.offset}:${e.role}:${e.text.length}`)), ...latest];
  return (
    <section className="workflow-agent" ref={subagent ? undefined : part("section")}>
      {!bare && <h4>{name} <span className="workflow-item-slug">{gone ? t("main.workflow.ended") : agentStateWords(state)}</span>
        {subagent && parent && <span className="workflow-item-slug"> · {t("main.workflow.subagentOfLower", { name: parent })}</span>}</h4>}
      {gone && <p className="workflow-empty">{t("main.workflow.sessionEnded")}</p>}
      {error && <p className="read-failure">{t("main.workflow.itsTranscriptUnreadable", { error })}</p>}
      <div className="transcript-entries"
           ref={both(box, part(subagent ? "transcript" : "entries"))}
           data-ui-action="scroll"
           onScroll={(e) => {
             const el = e.currentTarget;
             atBottom.current = el.scrollHeight - el.scrollTop - el.clientHeight < 24;
           }}>
        {earlier != null && <button type="button" className="transcript-earlier" ref={part("earlier")}
                                    {...bindingProps(bind("workflow.transcript.earlier",
                                      subagent ? { sessionId, agentId: subagent } : { sessionId }))}>
          {t("main.workflow.loadEarlier")}</button>}
        {entries.map((e, i) => (
          <div key={`${e.offset}-${i}`} className={`transcript-entry role-${e.role}`}>
            <span className="transcript-role">{roleLabel(e)}
              {e.at != null && <span className="transcript-time">{clock(e.at)}</span>}</span>
            {e.text && <pre>{e.text}</pre>}
          </div>
        ))}
        {!gone && entries.length === 0 && !error && <p className="workflow-empty">{t("main.workflow.emptyTranscript")}</p>}
      </div>
    </section>
  );
}

/** An event's kind in words (workflow_store.py's kinds), or the kind itself. */
const kindWords = (kind: string): string => codeWords("main.workflow.event", kind) ?? kind;

function Events({ id }: { id: string }) {
  const t = useT();
  const live = useLiveRead(() => getEvents(id), [workflowsChanged]);
  const open = useSyncExternalStore(shown.subscribe, shown.get);
  const kind = open.workflowId === id ? open.kind : "";
  const ui = useDetailTarget("workflowEvents", id, t("main.workflow.tab.events"));
  const events = live.value ?? [];
  const kinds = [...new Set(events.map((e) => e.kind))].sort();
  const list = [...(kind ? events.filter((e) => e.kind === kind) : events)].reverse();
  return (
    <div className="workflow-events" ref={ui.whole}>
      {live.error && <p className="read-failure">{t("main.workflow.eventsUnreadable", { error: live.error })}</p>}
      <select value={kind} aria-label={t("main.workflow.kind")} ref={ui.part("filter")} data-ui-command="workflow.events.filter"
              onChange={(e) => runBinding(bind("workflow.events.filter",
                e.target.value ? { workflowId: id, kind: e.target.value } : { workflowId: id }))}>
        <option value="">{t("main.workflow.everyKind")}</option>
        {kinds.map((k) => <option key={k} value={k}>{kindWords(k)}</option>)}
      </select>
      <table ref={ui.part("list")}>
        <tbody>
          {list.map((e) => (
            <tr key={e.seq} data-kind={e.kind}>
              <td>{e.seq}</td><td>{clock(e.at)}</td><td>{kindWords(e.kind)}</td>
              <td>{e.item ?? ""}</td><td>{e.summary}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
