/**
 * The conversation's record on the server (conversation_log.py): what Tim
 * said and JARVIS answered before this page was opened, every message to an
 * agent with its status as it is now, each agent's answer and each message
 * an agent sent him. The Conversation view's history and each agent's thread
 * read it here; a "conversation" hint on the voice socket (jarvis.ts) says
 * to read it again.
 *
 * The durable record is authoritative. Live captions bridge the interval
 * before a server record arrives, with utterance/turn correlation where available.
 */

import { codeWords, t, type Said } from "./locale";
import { createStore } from "./store";
import { bind, detailSlot, readForOpen, registerClose, routeStore } from "./uiNavigation";
import { registerCommand } from "./uiRegistry";

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
            heard?: number[]; utt?: number; reply_started_at?: number; client_turn_id?: number };
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

// ── The agent's thread open in the centre ────────────────────────────────

export interface OpenThread { sessionId: string; name: string | null }

const threadSlot = detailSlot<OpenThread>("thread", (a, b) => a.sessionId === b.sessionId);

/** Each agent's name as the conversation record gives it, by session:
 *  what an opened thread is called before its own page is read. */
const agentNames = new Map<string, string | null>();

function noteAgents(page: ConversationPage): ConversationPage {
  for (const i of page.items) {
    if (i.agent_session_id) agentNames.set(i.agent_session_id, i.agent_name ?? agentNames.get(i.agent_session_id) ?? null);
  }
  if (page.agent) agentNames.set(page.agent.session_id, page.agent.name);
  return page;
}

/** The agent whose thread the centre area shows, or null. Writing it runs
 *  `thread.open` or `thread.close`, as a person's navigation. */
export const openThread = routeStore(threadSlot.value, (next: OpenThread | null) =>
  next ? bind("thread.open", { sessionId: next.sessionId }) : bind("thread.close", {}));

registerCommand("thread.open", {
  availability: () => ({ available: true }),
  async execute({ sessionId }, { signal }) {
    if (!agentNames.has(sessionId)) {
      // Not in the record this page has read: JARVIS says whether there
      // is anything to or from it, and what it is called.
      const read = await readForOpen<ConversationPage>(
        `/api/conversation/agents/${encodeURIComponent(sessionId)}`, signal,
        (page) => page.items.length > 0 || (!!page.agent && page.agent.state !== "gone"));
      if ("refused" in read) return read.refused;
      noteAgents(read.body);
    }
    return { status: "completed",
             effects: threadSlot.show({ sessionId, name: agentNames.get(sessionId) ?? null }) };
  },
});

registerClose("thread.close", threadSlot);

async function getJson<T>(path: string): Promise<T> {
  const res = await fetch(path);
  if (!res.ok) throw new Error(`GET ${path}: ${res.status}`);
  return (await res.json()) as T;
}

export const getConversation = (before?: number | null) =>
  getJson<ConversationPage>(`/api/conversation${before != null ? `?before=${before}` : ""}`).then(noteAgents);

export const getThread = (sessionId: string, before?: number | null) =>
  getJson<ConversationPage>(`/api/conversation/agents/${encodeURIComponent(sessionId)}`
    + (before != null ? `?before=${before}` : "")).then(noteAgents);

/** What a message's status is called on the page. */
export const statusWords = (status: string): string => codeWords("main.message.status", status) ?? status;

/** What an agent's session state is called (session_watch.py's derived
 *  states). */
export const agentStateWords = (state: string): string => codeWords("main.agent.state", state) ?? state;

/** Restore a cleared capture (the Restore button). Resolves with the
 *  refusal (the server's own words, shown as they came, or the page's), or
 *  null. */
export async function restoreCapture(item: ConversationItem): Promise<Said | null> {
  const res = await fetch(`/api/capture/${item.capture_id}/restore?notice=${item.id}`,
                          { method: "POST" });
  if (res.ok) {
    conversationChanged.set(conversationChanged.get() + 1);
    return null;
  }
  const body = (await res.json().catch(() => ({}))) as { detail?: string };
  return body.detail ? { original: body.detail } : { words: () => t("main.conversation.restoreRefused", { status: res.status }) };
}

