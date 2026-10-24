/**
 * One workflow, in full: the centre area's Workflow view, opened by clicking
 * its row in the right panel's Workflows view. Its own row of tabs:
 *
 *   - Overview: state, phase, counts, the last report, when it started and
 *     was last heard from;
 *   - Phases and Tasks: each with its state and progress;
 *   - Transcript: each attached agent's latest turn (its prompt, recent
 *     tools, last text and last reply), read again every few seconds while
 *     it shows;
 *   - Progress: phases and tasks done, as bars, and each task's own count;
 *   - Waits and Blockers: what is open, and for how long;
 *   - Decisions: each open one with its options as buttons, the
 *     recommendation marked, and a box for any other answer;
 *   - Events: the whole log, filtered by kind.
 *
 * Everything here was written by the session running the workflow and is
 * drawn as plain text.
 */

import { useEffect, useState } from "react";
import { useLiveRead } from "./liveRead";
import { TabRow, type Tab } from "./Tabs";
import { TaskBar, useTick } from "./WorkflowsView";
import { ago, answerDecision, getEvents, getTranscript, getWorkflow, openWorkflow,
         phaseText, STATE_WORDS, workflowsChanged, type OpenItem, type Workflow,
         type WorkflowItem } from "./workflows";

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
    { id: "phases", title: "Phases", icon: null,
      view: () => <Items items={w.items.filter((i) => i.kind === "phase")} empty="No phases" /> },
    { id: "tasks", title: "Tasks", icon: null,
      view: () => <Items items={w.items.filter((i) => i.kind === "task")} empty="No tasks yet" /> },
    { id: "transcript", title: "Transcript", icon: null, view: () => <Transcript id={id} /> },
    { id: "progress", title: "Progress", icon: null, view: () => <Progress w={w} /> },
    { id: "waits", title: "Waits", icon: null,
      view: () => <Opened items={w.open.filter((o) => o.kind === "wait")} empty="Waiting on nothing" /> },
    { id: "decisions", title: "Decisions", icon: null, view: () => <Decisions w={w} /> },
    { id: "blockers", title: "Blockers", icon: null,
      view: () => <Opened items={w.open.filter((o) => o.kind === "blocker")} empty="Nothing in the way" /> },
    { id: "events", title: "Events", icon: null, view: () => <Events id={id} /> },
  ] : [];
  const shown = tabs.find((t) => t.id === tab) ?? tabs[0];
  return (
    <div id="workflow-detail">
      <div className="workflow-detail-head">
        <span className={`workflow-dot state-${w?.state ?? "running"}`} />
        <span className="workflow-detail-name">{w?.name ?? "Workflow"}</span>
        {w && <span className="workflow-detail-state">{STATE_WORDS[w.state]}</span>}
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
    <dl className="workflow-facts">
      <dt>State</dt><dd>{STATE_WORDS[w.state]}</dd>
      <dt>Project</dt><dd>{w.project}</dd>
      <dt>Phase</dt><dd>{phaseText(w) || "None"}</dd>
      <dt>Tasks</dt><dd>{w.tasks_done}/{w.tasks_total} done</dd>
      <dt>Open</dt>
      <dd>{w.open_decisions.length} decisions, {w.open_blockers.length} blockers,{" "}
        {w.open_waits.length} waits</dd>
      <dt>Last report</dt><dd>{w.last_summary}</dd>
      <dt>Started</dt><dd>{ago(w.started_at)}</dd>
      <dt>Last heard</dt><dd>{ago(w.last_event_at)}</dd>
    </dl>
  );
}

function count(item: WorkflowItem): string {
  return item.total ? ` ${item.done ?? 0}/${item.total}` : "";
}

function Items({ items, empty }: { items: WorkflowItem[]; empty: string }) {
  if (!items.length) return <p className="workflow-empty">{empty}</p>;
  return (
    <ul className="workflow-items">
      {items.map((i) => (
        <li key={i.slug} data-state={i.state}>
          <span className={`workflow-item-state item-${i.state}`}>{i.state}</span>
          <span className="workflow-item-title">{i.title}</span>
          <span className="workflow-item-slug">{i.slug}{i.phase ? ` · ${i.phase}` : ""}</span>
          {i.total ? <TaskBar done={i.done ?? 0} total={i.total} /> : null}
          <span className="workflow-item-count">{count(i)}</span>
        </li>
      ))}
    </ul>
  );
}

function Progress({ w }: { w: Workflow }) {
  const tasks = w.items.filter((i) => i.kind === "task");
  return (
    <div className="workflow-progress">
      <p>Phases {w.phases_done}/{w.phases_total}</p>
      <TaskBar done={w.phases_done} total={w.phases_total} />
      <p>Tasks {w.tasks_done}/{w.tasks_total}</p>
      <TaskBar done={w.tasks_done} total={w.tasks_total} />
      <Items items={tasks.filter((t) => t.total)} empty="No task reports its own count" />
    </div>
  );
}

function Opened({ items, empty }: { items: OpenItem[]; empty: string }) {
  if (!items.length) return <p className="workflow-empty">{empty}</p>;
  return (
    <ul className="workflow-items">
      {items.map((o) => (
        <li key={o.key}>
          <span className="workflow-item-title">{o.summary}</span>
          <span className="workflow-item-slug">
            {o.key}{o.detail.until ? ` · until ${String(o.detail.until)}` : ""} · open {ago(o.opened_at)}
          </span>
        </li>
      ))}
    </ul>
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
      <p className="workflow-decision-question">{o.summary}</p>
      {recommended && <p className="workflow-recommended">Recommended: {recommended}</p>}
      {options.length > 0 && (
        <div className="workflow-options">
          {options.map((option) => (
            <button key={option} type="button" disabled={sending}
                    className={`workflow-option${option === recommended ? " recommended" : ""}`}
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
  const { refresh } = live;
  useEffect(() => {
    const timer = setInterval(refresh, TRANSCRIPT_EVERY_MS);
    return () => clearInterval(timer);
  }, [refresh]);
  const agents = live.value;
  return (
    <div className="workflow-transcript">
      {live.error && <p className="read-failure">Couldn't read the transcript ({live.error}); trying again.</p>}
      {agents && agents.length === 0 && <p className="workflow-empty">No agent has attached to this workflow</p>}
      {agents?.map((a) => (
        <section key={a.session_id} className="workflow-agent">
          <h4>{a.name ?? a.session_id} <span className="workflow-item-slug">{a.state}</span></h4>
          {a.last_prompt && <><h5>Last prompt</h5><pre>{a.last_prompt}</pre></>}
          {a.recent_tools && a.recent_tools.length > 0 && <><h5>Recent tools</h5>
            <pre>{a.recent_tools.join(", ")}</pre></>}
          {a.last_text && <><h5>Last text</h5><pre>{a.last_text}</pre></>}
          {a.last_reply && a.last_reply !== a.last_text && <><h5>Last reply</h5><pre>{a.last_reply}</pre></>}
        </section>
      ))}
    </div>
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
