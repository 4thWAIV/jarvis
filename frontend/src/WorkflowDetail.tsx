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
 * Everything here was written by the session running the workflow and is
 * drawn as plain text.
 */

import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { useLiveRead } from "./liveRead";
import { TabRow, type Tab } from "./Tabs";
import { TaskBar, useTick } from "./WorkflowsView";
import { AgeBar, OpenIcon, OpenIcons, phaseStates, PhaseTrack, Ring,
         WorkflowOrb } from "./WorkflowVisuals";
import { ago, answerDecision, getEvents, getSessionTranscript, getTranscript, getWorkflow,
         openWorkflow, STATE_WORDS, workflowsChanged, type OpenItem,
         type TranscriptEntry, type Workflow, type WorkflowItem } from "./workflows";

const TRANSCRIPT_EVERY_MS = 5000;

function clock(seconds: number): string {
  return new Date(seconds * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit",
                                                            second: "2-digit" });
}

export function WorkflowDetail({ id }: { id: string }) {
  const live = useLiveRead(() => getWorkflow(id), [workflowsChanged]);
  const [tab, setTab] = useState("overview");
  useTick(30000);
  const w = live.value;
  const tabs: Tab[] = w ? [
    { id: "overview", title: "Overview", icon: null, view: () => <Overview w={w} /> },
    { id: "phases", title: "Phases", icon: null, view: () => <Phases w={w} /> },
    { id: "tasks", title: "Tasks", icon: null, view: () => <Tasks w={w} /> },
    { id: "transcript", title: "Transcript", icon: null, view: () => <Transcript id={id} /> },
    { id: "progress", title: "Progress", icon: null, view: () => <Progress w={w} /> },
    { id: "waits", title: "Waits", icon: null,
      view: () => <Opened kind="wait" items={w.open.filter((o) => o.kind === "wait")} empty="Waiting on nothing" /> },
    { id: "decisions", title: "Decisions", icon: null, view: () => <Decisions w={w} /> },
    { id: "blockers", title: "Blockers", icon: null,
      view: () => <Opened kind="blocker" items={w.open.filter((o) => o.kind === "blocker")} empty="Nothing in the way" /> },
    { id: "events", title: "Events", icon: null, view: () => <Events id={id} /> },
  ] : [];
  const shown = tabs.find((t) => t.id === tab) ?? tabs[0];
  return (
    <div id="workflow-detail">
      <div className="workflow-detail-head">
        <WorkflowOrb state={w?.state ?? "running"} size={88} particles={600} />
        <span className="workflow-detail-title">
          <span className="workflow-detail-name">{w?.name ?? "Workflow"}</span>
          {w && <span className={`workflow-detail-state state-${w.state}`}>{STATE_WORDS[w.state]}</span>}
        </span>
        <button type="button" className="workflow-close" title="Close" aria-label="Close"
                onClick={() => openWorkflow.set(null)}>×</button>
      </div>
      {live.error && <p className="read-failure">Couldn't read this workflow ({live.error}); trying again.</p>}
      {w && <>
        <TabRow tabs={tabs} selected={shown.id} onSelect={setTab} className="workflow-tabs" words />
        <div className="workflow-tab-view" role="tabpanel" aria-label={shown.title}>
          {shown.view()}
        </div>
      </>}
    </div>
  );
}

function Overview({ w }: { w: Workflow }) {
  return (
    <div className="workflow-overview">
      <PhaseTrack w={w} labels />
      <div className="workflow-overview-rings">
        <Ring done={w.phases_done} total={w.phases_total} size={84} label="Phases done" />
        <span className="ring-caption">phases</span>
        <Ring done={w.tasks_done} total={w.tasks_total} size={84} label="Tasks done" />
        <span className="ring-caption">tasks</span>
        <OpenIcons w={w} />
      </div>
      {w.last_summary && <p className="workflow-caption">{w.last_summary}</p>}
      <p className="workflow-times">started {ago(w.started_at)} · heard from {ago(w.last_event_at)}</p>
    </div>
  );
}

const ITEM_ICONS: Record<string, string> = {
  pending: "○", running: "◐", waiting: "◔", blocked: "■", done: "●", failed: "✕", skipped: "–",
};

function ItemIcon({ item }: { item: WorkflowItem }) {
  return <span className={`item-icon item-${item.state}`} title={item.state}>{ITEM_ICONS[item.state] ?? "○"}</span>;
}

function Phases({ w }: { w: Workflow }) {
  const phases = phaseStates(w);
  if (!phases.length) return <p className="workflow-empty">No phases</p>;
  const tasks = w.items.filter((i) => i.kind === "task");
  return (
    <ol className="phase-stepper">
      {phases.map(({ item, at }, n) => {
        const own = tasks.filter((t) => t.phase === item.slug);
        const done = own.filter((t) => t.state === "done" || t.state === "skipped").length;
        return (
          <li key={item.slug} className={`phase-step at-${at}`} data-phase={item.slug}>
            <span className="phase-step-dot">{at === "done" ? "✓" : n + 1}</span>
            <span className="phase-step-title">{item.title}</span>
            {own.length > 0 && <><TaskBar done={done} total={own.length} />
              <span className="phase-step-count">{done}/{own.length}</span></>}
          </li>
        );
      })}
    </ol>
  );
}

function Tasks({ w }: { w: Workflow }) {
  const tasks = w.items.filter((i) => i.kind === "task");
  if (!tasks.length) return <p className="workflow-empty">No tasks yet</p>;
  const groups = phaseStates(w).map(({ item, at }) => ({ phase: item, at,
    tasks: tasks.filter((t) => t.phase === item.slug) })).filter((g) => g.tasks.length);
  const loose = tasks.filter((t) => !t.phase);
  return (
    <div className="task-board">
      {groups.map((g) => (
        <section key={g.phase.slug} className={`task-column at-${g.at}`}>
          <h5>{g.phase.title}</h5>
          {g.tasks.map((t) => <TaskCard key={t.slug} task={t} />)}
        </section>
      ))}
      {loose.length > 0 && <section className="task-column"><h5>Other</h5>
        {loose.map((t) => <TaskCard key={t.slug} task={t} />)}</section>}
    </div>
  );
}

function TaskCard({ task: t }: { task: WorkflowItem }) {
  return (
    <div className={`task-card item-${t.state}`} data-task={t.slug}>
      <ItemIcon item={t} />
      <span className="task-card-title">{t.title}</span>
      {t.total ? <TaskBar done={t.done ?? 0} total={t.total} /> : null}
    </div>
  );
}

function Progress({ w }: { w: Workflow }) {
  const counted = w.items.filter((i) => i.kind === "task" && i.total);
  return (
    <div className="workflow-progress">
      <div className="workflow-overview-rings">
        <Ring done={w.phases_done} total={w.phases_total} size={120} label="Phases done" />
        <span className="ring-caption">phases</span>
        <Ring done={w.tasks_done} total={w.tasks_total} size={120} label="Tasks done" />
        <span className="ring-caption">tasks</span>
      </div>
      {counted.map((t) => (
        <div key={t.slug} className="progress-row">
          <ItemIcon item={t} /><span className="task-card-title">{t.title}</span>
          <TaskBar done={t.done ?? 0} total={t.total ?? 0} />
          <span className="phase-step-count">{t.done ?? 0}/{t.total}</span>
        </div>
      ))}
    </div>
  );
}

function Opened({ kind, items, empty }: { kind: "wait" | "blocker"; items: OpenItem[]; empty: string }) {
  if (!items.length) return <p className="workflow-empty">{empty}</p>;
  return (
    <div className="open-cards">
      {items.map((o) => (
        <div key={o.key} className={`open-card open-card-${kind}`}>
          <OpenIcon kind={kind} />
          <span className="open-card-text">{o.summary}</span>
          <AgeBar since={o.opened_at} />
          <span className="open-card-age">{ago(o.opened_at)}{o.detail.until ? ` · until ${String(o.detail.until)}` : ""}</span>
        </div>
      ))}
    </div>
  );
}

function Decisions({ w }: { w: Workflow }) {
  const open = w.open.filter((o) => o.kind === "decision");
  if (!open.length) return <p className="workflow-empty">No decision is waiting on you</p>;
  return (
    <ul className="workflow-decisions-list">
      {open.map((o) => <Decision key={o.key} id={w.id} decision={o} />)}
    </ul>
  );
}

function Decision({ id, decision: o }: { id: string; decision: OpenItem }) {
  const [text, setText] = useState("");
  const [refused, setRefused] = useState<string | null>(null);
  const [sending, setSending] = useState(false);
  const options = Array.isArray(o.detail.options) ? o.detail.options.map(String) : [];
  const recommended = typeof o.detail.recommendation === "string" ? o.detail.recommendation : null;
  // A recommendation may say why after the option's own words.
  const isRecommended = (option: string) =>
    !!recommended && (recommended === option || recommended.startsWith(option));
  const answer = (value: string) => {
    if (!value.trim() || sending) return;
    setSending(true);
    setRefused(null);
    answerDecision(id, o.key, value.trim()).then((why) => {
      setSending(false);
      setRefused(why);
      if (!why) setText("");
    }, (e: Error) => { setSending(false); setRefused(e.message); });
  };
  return (
    <li className="workflow-decision" data-key={o.key}>
      <OpenIcon kind="decision" />
      <p className="workflow-decision-question">{o.summary}</p>
      {recommended && <p className="workflow-recommended">★ {recommended}</p>}
      {options.length > 0 && (
        <div className="workflow-options">
          {options.map((option) => (
            <button key={option} type="button" disabled={sending}
                    className={`workflow-option${isRecommended(option) ? " recommended" : ""}`}
                    onClick={() => answer(option)}>{option}</button>
          ))}
        </div>
      )}
      <div className="workflow-answer">
        <input type="text" value={text} placeholder="Another answer..."
               onChange={(e) => setText(e.target.value)}
               onKeyDown={(e) => { if (e.key === "Enter") answer(text); }} />
        <button type="button" disabled={sending || !text.trim()} onClick={() => answer(text)}>Answer</button>
      </div>
      {refused && <p className="read-failure workflow-refused">{refused}</p>}
    </li>
  );
}

function Transcript({ id }: { id: string }) {
  const live = useLiveRead(() => getTranscript(id), [workflowsChanged]);
  const agents = live.value;
  return (
    <div className="workflow-transcript">
      {live.error && <p className="read-failure">Couldn't read the transcript ({live.error}); trying again.</p>}
      {agents && agents.length === 0 && <p className="workflow-empty">No agent has attached to this workflow</p>}
      {agents?.map((a) => <AgentTranscript key={a.session_id} sessionId={a.session_id}
                                           fallbackName={a.name ?? a.session_id} gone={a.state === "gone"} />)}
    </div>
  );
}

const ROLE_LABELS: Record<TranscriptEntry["role"], string> = {
  you: "Prompt", jarvis: "From JARVIS", agent: "Agent", tool: "Tool", result: "Result",
};

/** One agent's transcript: the latest page, kept current, and earlier pages
 *  added above it on request. Stays at the bottom while it is there. */
function AgentTranscript({ sessionId, fallbackName, gone }: {
  sessionId: string; fallbackName: string; gone: boolean }) {
  const [latest, setLatest] = useState<TranscriptEntry[]>([]);
  const [older, setOlder] = useState<TranscriptEntry[]>([]);
  const [earlier, setEarlier] = useState<number | null>(null);
  const [name, setName] = useState(fallbackName);
  const [state, setState] = useState("");
  const [error, setError] = useState<string | null>(null);
  const box = useRef<HTMLDivElement>(null);
  const atBottom = useRef(true);

  useEffect(() => {
    if (gone) return;
    let stopped = false;
    const read = () => getSessionTranscript(sessionId).then((page) => {
      if (stopped) return;
      setLatest(page.entries);
      setName(page.name);
      setState(page.state);
      setError(null);
      setEarlier((e) => (older.length ? e : page.earlier));
    }).catch((e: Error) => { if (!stopped) setError(e.message); });
    read();
    const timer = setInterval(read, TRANSCRIPT_EVERY_MS);
    const unsubscribe = workflowsChanged.subscribe(read);
    return () => { stopped = true; clearInterval(timer); unsubscribe(); };
  }, [sessionId, gone, older.length]);

  useLayoutEffect(() => {
    const el = box.current;
    if (el && atBottom.current) el.scrollTop = el.scrollHeight;
  }, [latest]);

  async function loadEarlier() {
    if (earlier == null) return;
    try {
      const page = await getSessionTranscript(sessionId, earlier);
      setOlder((o) => [...page.entries, ...o]);
      setEarlier(page.earlier);
    } catch (e) {
      setError((e as Error).message);
    }
  }

  // The latest page and the earlier ones overlap once the transcript grows.
  const seen = new Set(latest.map((e) => `${e.offset}:${e.role}:${e.text.length}`));
  const entries = [...older.filter((e) => !seen.has(`${e.offset}:${e.role}:${e.text.length}`)), ...latest];
  return (
    <section className="workflow-agent">
      <h4>{name} <span className="workflow-item-slug">{gone ? "ended" : state}</span></h4>
      {gone && <p className="workflow-empty">This agent's session has ended.</p>}
      {error && <p className="read-failure">Couldn't read its transcript ({error}); trying again.</p>}
      <div className="transcript-entries" ref={box}
           onScroll={(e) => {
             const el = e.currentTarget;
             atBottom.current = el.scrollHeight - el.scrollTop - el.clientHeight < 24;
           }}>
        {earlier != null && <button type="button" className="transcript-earlier" onClick={loadEarlier}>
          Load earlier</button>}
        {entries.map((e, i) => (
          <div key={`${e.offset}-${i}`} className={`transcript-entry role-${e.role}`}>
            <span className="transcript-role">{e.role === "tool" && e.tool ? e.tool
                : e.role === "result" && e.tool ? `${e.tool} result` : ROLE_LABELS[e.role]}
              {e.at != null && <span className="transcript-time">{clock(e.at)}</span>}</span>
            {e.text && <pre>{e.text}</pre>}
          </div>
        ))}
        {!gone && entries.length === 0 && !error && <p className="workflow-empty">Nothing in its transcript yet</p>}
      </div>
    </section>
  );
}

function Events({ id }: { id: string }) {
  const live = useLiveRead(() => getEvents(id), [workflowsChanged]);
  const [kind, setKind] = useState("");
  const events = live.value ?? [];
  const kinds = [...new Set(events.map((e) => e.kind))].sort();
  const shown = [...(kind ? events.filter((e) => e.kind === kind) : events)].reverse();
  return (
    <div className="workflow-events">
      {live.error && <p className="read-failure">Couldn't read the events ({live.error}); trying again.</p>}
      <select value={kind} onChange={(e) => setKind(e.target.value)} aria-label="Kind">
        <option value="">Every kind</option>
        {kinds.map((k) => <option key={k} value={k}>{k}</option>)}
      </select>
      <table>
        <tbody>
          {shown.map((e) => (
            <tr key={e.seq} data-kind={e.kind}>
              <td>{e.seq}</td><td>{clock(e.at)}</td><td>{e.kind}</td>
              <td>{e.item ?? ""}</td><td>{e.summary}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
