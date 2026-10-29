/**
 * Long workflows, from the one durable record every workflow reports to
 * (server: workflow_store.py, /api/workflows). The page never keeps its own
 * account of one: /ws/sessions says "workflows" when any of them changes
 * (sessionsSocket.ts moves `workflowsChanged`), and every view
 * reads the record again.
 */

import { liveRead } from "./liveRead";
import { createStore } from "./store";
import { shortAgo } from "./timeFormat";

export type WorkflowState = "running" | "waiting" | "blocked" | "needs_you" | "succeeded"
  | "failed" | "cancelled" | "silent";

export interface WorkflowItem {
  slug: string;
  kind: "phase" | "task";
  phase: string | null;
  title: string;
  state: string;
  done: number | null;
  total: number | null;
}

export interface OpenItem {
  kind: "wait" | "decision" | "blocker";
  key: string;
  summary: string;
  detail: { options?: string[]; recommendation?: string; until?: string; [k: string]: unknown };
  opened_at: number;
}

export interface Workflow {
  id: string;
  name: string;
  project: string;
  state: WorkflowState;
  phase: string | null;
  phases_done: number;
  phases_total: number;
  tasks_done: number;
  tasks_total: number;
  open_waits: string[];
  open_decisions: string[];
  open_blockers: string[];
  agents: string[];
  started_at: number;
  last_event_at: number;
  last_summary: string;
  items: WorkflowItem[];
  open: OpenItem[];
  /** When it finished, or null while it has not. */
  finished_at: number | null;
  /** He dismissed it, or it finished long enough ago to have gone. */
  dismissed: boolean;
  /** When a finished one still in view dismisses itself, or null. */
  dismiss_at: number | null;
  /** Its attached sessions' subagents while it ran: on a single
   *  workflow's read only. */
  subagents?: SubagentRow[];
}

export type SubagentState = "working" | "finished" | "stale";

/** A subagent a session dispatched, as the Workflows area shows it
 *  (server: _subagent_row). Worked out from the session's own files when
 *  read, never reported to the workflow record. */
export interface SubagentRow {
  /** Its name among the workflows: `openWorkflow` opens it by this. */
  key: string;
  session_id: string;
  /** The session that dispatched it. */
  session_name: string;
  project: string;
  id: string;
  /** The sidecar's words, written by another process: drawn as text. */
  description: string;
  type: string;
  background: boolean;
  depth: number;
  /** Finished: its last message ended its turn. Working: written lately.
   *  Stale: neither, which is all the file can say. */
  state: SubagentState;
  started: number | null;
  written: number;
  /** When one no longer working leaves the list, or null while working. */
  dismiss_at: number | null;
}

/** A subagent is drawn as a workflow in this state would be. */
export const SUBAGENT_LOOK: Record<SubagentState, WorkflowState> = {
  working: "running", finished: "succeeded", stale: "silent",
};

export const SUBAGENT_WORDS: Record<SubagentState, string> = {
  working: "Working", finished: "Finished", stale: "Gone quiet",
};

/** The session and subagent a `SubagentRow.key` names, or null for a
 *  workflow's id. */
export function subagentOf(key: string): { sessionId: string; agentId: string } | null {
  if (!key.startsWith("agent:")) return null;
  const rest = key.slice("agent:".length);
  const cut = rest.lastIndexOf("/");
  return cut > 0 ? { sessionId: rest.slice(0, cut), agentId: rest.slice(cut + 1) } : null;
}

export interface WorkflowEvent {
  workflow_id: string;
  kind: string;
  item: string | null;
  summary: string;
  detail: Record<string, unknown>;
  at: number;
  seq: number;
}

export interface TranscriptAgent {
  session_id: string;
  name: string | null;
  state: string;
  last_prompt?: string | null;
  recent_tools?: string[];
  last_text?: string | null;
  last_reply?: string | null;
}

/** One entry of a session's transcript (session_watch.TranscriptEntry). */
export interface TranscriptEntry {
  offset: number;
  at: number | null;
  role: "you" | "jarvis" | "agent" | "tool" | "result";
  text: string;
  tool: string | null;
}

export interface TranscriptPage {
  session_id: string;
  name: string;
  state: string;
  /** With a subagent's transcript: the session that dispatched it. */
  session_name?: string;
  entries: TranscriptEntry[];
  /** The cursor for the page before, or null at the start of the transcript. */
  earlier: number | null;
}

/** A session's transcript, or with `subagent` one of its subagents'. */
export function getSessionTranscript(sessionId: string, before?: number | null,
                                     subagent?: string | null): Promise<TranscriptPage> {
  const query = new URLSearchParams();
  if (before != null) query.set("before", String(before));
  if (subagent) query.set("subagent", subagent);
  const rest = query.toString();
  return getJson<TranscriptPage>(`/api/sessions/${encodeURIComponent(sessionId)}/transcript`
    + (rest ? `?${rest}` : ""));
}

/** Moves on every "workflows" hint, and on every (re)connect of /ws/sessions. */
export const workflowsChanged = createStore(0);

/** The workflow whose detail the centre area shows, or null. */
export const openWorkflow = createStore<string | null>(null);

export const FINISHED: ReadonlySet<WorkflowState> = new Set(["succeeded", "failed", "cancelled"]);

/** What the page calls each state. */
export const STATE_WORDS: Record<WorkflowState, string> = {
  running: "Running", waiting: "Waiting", blocked: "Blocked", needs_you: "Needs you",
  succeeded: "Finished", failed: "Failed", cancelled: "Cancelled", silent: "Gone quiet",
};

async function getJson<T>(path: string): Promise<T> {
  const res = await fetch(path);
  if (!res.ok) throw new Error(`GET ${path}: ${res.status}`);
  return (await res.json()) as T;
}

/** Running workflows first, most recently heard from first; then the
 *  finished ones, the same way. */
export function ordered(list: Workflow[]): Workflow[] {
  const byHeard = (a: Workflow, b: Workflow) => b.last_event_at - a.last_event_at;
  return [...list.filter((w) => !FINISHED.has(w.state)).sort(byHeard),
          ...list.filter((w) => FINISHED.has(w.state)).sort(byHeard)];
}

/** The workflows, and every subagent in view that belongs to none of the
 *  running ones. */
export const workflows = liveRead(
  () => getJson<{ workflows: Workflow[]; subagents?: SubagentRow[] }>("/api/workflows")
    .then((r) => ({ workflows: ordered(r.workflows), subagents: r.subagents ?? [] })),
  [workflowsChanged]);

export const getWorkflow = (id: string) =>
  getJson<{ workflow: Workflow; subagents?: SubagentRow[] }>(`/api/workflows/${encodeURIComponent(id)}`)
    .then((r) => ({ ...r.workflow, subagents: r.subagents ?? [] }));

export const getEvents = (id: string) =>
  getJson<{ events: WorkflowEvent[] }>(`/api/workflows/${encodeURIComponent(id)}/events`)
    .then((r) => r.events);

export const getTranscript = (id: string) =>
  getJson<{ agents: TranscriptAgent[]; subagents?: SubagentRow[] }>(
    `/api/workflows/${encodeURIComponent(id)}/transcript`)
    .then((r) => ({ agents: r.agents, subagents: r.subagents ?? [] }));

// Dismissed on this page and not yet read back as dismissed: gone at once.
const dismissedHere = createStore<ReadonlySet<string>>(new Set());

/** Whether a workflow has gone from the main list: dismissed (by him, here
 *  or anywhere, or by lingering past its time). It is listed as done. */
export function isGone(w: Workflow, now = Date.now() / 1000): boolean {
  return w.dismissed || dismissedHere.get().has(w.id)
    || (w.dismiss_at != null && now >= w.dismiss_at);
}

export const dismissals = dismissedHere;

/** Dismiss a finished workflow: gone from the list at once, then read again.
 *  Resolves with the refusal's reason, or null. */
export async function dismissWorkflow(id: string): Promise<string | null> {
  dismissedHere.set(new Set([...dismissedHere.get(), id]));
  const res = await fetch(`/api/workflows/${encodeURIComponent(id)}/dismiss`, { method: "POST" })
    .catch(() => null);
  workflowsChanged.set(workflowsChanged.get() + 1);
  if (res?.ok) return null;
  const left = new Set(dismissedHere.get());
  left.delete(id);
  dismissedHere.set(left);
  const body = res ? (await res.json().catch(() => ({}))) as { error?: string; detail?: string } : {};
  return body.error || body.detail || "the server did not answer";
}

/** Answer an open decision. Resolves with the refusal's reason, or null. */
export async function answerDecision(id: string, key: string, answer: string): Promise<string | null> {
  const res = await fetch(`/api/workflows/${encodeURIComponent(id)}/decisions/${encodeURIComponent(key)}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ answer }),
  });
  if (res.ok) {
    workflowsChanged.set(workflowsChanged.get() + 1);
    return null;
  }
  const body = (await res.json().catch(() => ({}))) as { error?: string; detail?: string };
  return body.error || body.detail || `the server refused it (${res.status})`;
}

/** "just now", "2m ago", "3h ago", "2d ago", from a server time in seconds. */
export function ago(seconds: number, now = Date.now() / 1000): string {
  return shortAgo(Math.max(0, now - seconds) * 1000, 45_000);
}

/** The phase a workflow is on, as "phase k/n <phase>". */
export function phaseText(w: Workflow): string {
  if (!w.phases_total) return w.phase ?? "";
  const k = Math.min(w.phases_done + (FINISHED.has(w.state) ? 0 : 1), w.phases_total);
  return `phase ${k}/${w.phases_total}${w.phase ? ` ${w.phase}` : ""}`;
}
