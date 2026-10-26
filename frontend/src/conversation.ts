/**
 * The conversation's record on the server (conversation_log.py): what Tim
 * said and JARVIS answered before this page was opened, every message to an
 * agent with its status as it is now, each agent's answer and each message
 * an agent sent him. The Conversation view's history and each agent's thread
 * read it here; a "conversation" hint on the voice socket (jarvis.ts) says
 * to read it again.
 *
 * What is said while the page is open is drawn from the live captions
 * (captions.ts) as it always was; the record adds what came before the page
 * opened, and what only the server sees (messages and their answers).
 */

import { createStore } from "./store";

export type ItemKind = "heard" | "typed" | "jarvis" | "capture" | "to_agent"
  | "from_agent" | "answer" | "notice";

export interface MessageState {
  id: number;
  status: string;           // staged, queued, sent, delivered, working, answered, failed, ...
  answered: boolean;
  failure: string | null;
}

export interface ConversationItem {
  id: number;
  at: number;
  kind: ItemKind;
  text: string;
  agent_session_id: string | null;
  agent_name: string | null;
  message_id: number | null;
  session_message_id: number | null;
  capture_id: number | null;
  /** Kind-specific: a capture notice's capture_event ("cleared", "restored")
   *  and who did it ("by"). */
  detail: { capture_event?: string; by?: string; how?: string; as?: number;
            heard?: number[] };
  message: MessageState | null;
}

export interface ConversationPage {
  items: ConversationItem[];
  /** The cursor for the page before (the oldest item's id), or null. */
  earlier: number | null;
  agent?: { session_id: string; name: string | null; state: string };
}

/** Moves on every "conversation" hint and every (re)connect. */
export const conversationChanged = createStore(0);

/** The agent whose thread the centre area shows, or null. */
export const openThread = createStore<{ sessionId: string; name: string | null } | null>(null);

/** When this page opened, in seconds: what was said before it comes from the
 *  record, what is said after from the live captions. */
export const pageOpenedAt = Date.now() / 1000;

async function getJson<T>(path: string): Promise<T> {
  const res = await fetch(path);
  if (!res.ok) throw new Error(`GET ${path}: ${res.status}`);
  return (await res.json()) as T;
}

export const getConversation = (before?: number | null) =>
  getJson<ConversationPage>(`/api/conversation${before != null ? `?before=${before}` : ""}`);

export const getThread = (sessionId: string, before?: number | null) =>
  getJson<ConversationPage>(`/api/conversation/agents/${encodeURIComponent(sessionId)}`
    + (before != null ? `?before=${before}` : ""));

/** What a message's status is called on the page. */
export const STATUS_WORDS: Record<string, string> = {
  staged: "Sending", queued: "Queued", cancelled: "Cancelled", sent: "Sent",
  undelivered: "Not picked up", delivered: "Delivered", working: "Working on it",
  answered: "Answered", failed: "Failed",
};

/** The kinds the live captions already draw while the page is open. */
const LIVE_KINDS: ReadonlySet<ItemKind> = new Set(["heard", "typed", "jarvis", "capture", "notice"]);

/** Whether the history shows this record item: everything from before the
 *  page opened, and after it only what the live captions do not draw. */
export function fromRecord(item: ConversationItem): boolean {
  // A capture cleared or restored is never spoken, so it is always the record's.
  return item.at < pageOpenedAt || !LIVE_KINDS.has(item.kind) || !!item.detail?.capture_event;
}

/** Restore a cleared capture (the Restore button). Resolves with the
 *  refusal's reason, or null. */
export async function restoreCapture(item: ConversationItem): Promise<string | null> {
  const res = await fetch(`/api/capture/${item.capture_id}/restore?notice=${item.id}`,
                          { method: "POST" });
  if (res.ok) {
    conversationChanged.set(conversationChanged.get() + 1);
    return null;
  }
  const body = (await res.json().catch(() => ({}))) as { detail?: string };
  return body.detail || `the server refused it (${res.status})`;
}
