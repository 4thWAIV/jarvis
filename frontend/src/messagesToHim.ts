/**
 * His messages, as the page reads them (one channel round 2,
 * docs/planning/one-channel/messages-by-work-item.md): every message to him
 * is one thing, from its proven sender, about a Work item or none, saying
 * something short, carrying a package or none, and new, read, answered or
 * done (messages_api.py).
 *
 *   - `inboxMessages`: each Work item with messages (how many are new, its
 *     first new one) and the new messages that belong to no item, newest
 *     first, with how many are read;
 *   - `readMessages`: the ones that belong to no item he has read or
 *     answered and is not done with, newest first: the Inbox's Read
 *     section. Done ones are in neither list;
 *     read at once, on every `inbox` hint and every (re)connect snapshot on
 *     /ws/sessions (sessionsSocket.ts). The Inbox tab, the Work rows' counts
 *     and Updates waiting's one line read it.
 *   - `readConversation`: one item's conversation, oldest first, his replies
 *     in it (the item's Messages tab, WorkMessages.tsx).
 *   - `markMessage`: his mark (read or done), the brain's mark_message too.
 *   - `messageAct`: Read (JARVIS reads that one message aloud, the brain's
 *     read_aloud; it is read once heard in full) and Reply (a capture
 *     addressed to its proven sender for what he says or types next, read
 *     back and sent on his yes; the brain's reply), by the message's id.
 *
 * Every word in a message is its sender's: drawn as text, never markup.
 */

import { liveRead } from "./liveRead";
import { t } from "./locale";
import type { InboxState } from "./packageInbox";
import { createStore } from "./store";

/** The /ws/sessions hint that his messages moved (server.INBOX_HINT). */
export const INBOX_HINT = "inbox";

export const inboxChanged = createStore(0);

export type MessageState = "new" | "read" | "answered" | "done";

/** The package a message carries, as its card shows it. */
export interface CarriedPackage {
  inbox: string;
  package_id: string;
  /** The sender's words. */
  title: string;
  runs: "conversation" | "agent" | "page";
  decisions: number;
  answered: number;
  state: InboxState;
}

/** One message to him, or one thing he sent on a Work item's thread. */
export interface ToHim {
  id: number;
  at: number;
  kind: string;
  /** The sender's words (his own, for what he sent). */
  says: string;
  about: string | null;
  carries: string | null;
  package?: CarriedPackage | null;
  /** Null for anything that is no message (his own replies). */
  state: MessageState | null;
  state_at: number | null;
  message: boolean;
  counterpart: { kind: string | null; ref: string | null };
  /** The record that is its truth: a Work decision's is "<item>:<key>". */
  source?: { kind: string | null; id: string | null };
  /** "tim" for what he sent. */
  from: { sender: string; name: string; proof: string | null; subagent: string | null };
  reply_to?: number;
}

export interface WorkThread {
  work: string;
  title: string;
  new: number;
  total: number;
  latest_at: number;
  first_new: ToHim | null;
}

export interface Inbox {
  work: { items: WorkThread[]; new: number };
  /** The new ones that belong to no item, how many are new and how many
   *  are read (or answered) and not done: the Read section's count. */
  general: { messages: ToHim[]; new: number; read: number; more: boolean };
}

export interface Conversation {
  work: string;
  title: string;
  new: number;
  messages: ToHim[];
  more: boolean;
}

async function json<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, init);
  const body = await res.json().catch(() => null);
  if (!res.ok || body === null || typeof body !== "object") {
    throw new Error((body as { error?: string; detail?: string } | null)?.error
      ?? (body as { detail?: string } | null)?.detail ?? `${url}: ${res.status}`);
  }
  return body as T;
}

/** Both lists, or a failed read: an answer without them is not an inbox. */
async function readInbox(): Promise<Inbox> {
  const [work, general] = await Promise.all([
    json<Inbox["work"]>("/api/messages/work"), json<Inbox["general"]>("/api/messages/general")]);
  if (!Array.isArray(work.items) || !Array.isArray(general.messages)) {
    throw new Error("GET /api/messages: not his messages");
  }
  return { work, general };
}

export const inboxMessages = liveRead(readInbox, [inboxChanged]);

/** The Inbox's Read section: messages that belong to no item, read or
 *  answered and not done, newest first. */
export const readMessages = liveRead(async () => {
  const read = await json<Inbox["general"]>("/api/messages/general?read=1");
  if (!Array.isArray(read.messages)) throw new Error("GET /api/messages/general: not his messages");
  return read;
}, [inboxChanged]);

/** How many messages to him are new: on Work items and on none. */
export function newMessages(inbox: Inbox | null): number {
  return inbox ? inbox.work.new + inbox.general.new : 0;
}

/** One Work item's thread as the Inbox has it, or undefined. */
export function threadOf(inbox: Inbox | null, work: string): WorkThread | undefined {
  return inbox?.work.items.find((i) => i.work === work);
}

/** One Work item's conversation, oldest first. */
export const readConversation = (work: string) =>
  json<Conversation>(`/api/messages/work/${encodeURIComponent(work)}`);

const changed = () => inboxChanged.set(inboxChanged.get() + 1);

/** His mark on one message. The refusal's words, or null. */
export async function markMessage(id: number, state: "read" | "done"): Promise<string | null> {
  try {
    await json(`/api/messages/${id}/mark`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ state }),
    });
    changed();
    return null;
  } catch (e) {
    return (e as Error).message;
  }
}

/** Read aloud, or Reply, on one message: what to tell him it did. */
export async function messageAct(id: number, action: "read" | "reply"): Promise<string> {
  try {
    const body = await json<{ to?: string }>(`/api/messages/${id}/${action}`, { method: "POST" });
    return action === "read" ? t("main.updates.reading") : t("main.updates.replyTo", { name: body.to ?? "" });
  } catch (e) {
    return (e as Error).message || t("main.updates.readFailed");
  }
}

/** A message's first line, for a row's update line or a short list. */
export const firstLine = (says: string) => says.split("\n").find((l) => l.trim())?.trim() ?? "";

/** Where a carried package stands: "1 of 2 answered" while it asks. */
export function carriedProgress(p: CarriedPackage): string {
  if (p.decisions > 0) return t("right.packages.answered", { answered: p.answered, total: p.decisions });
  return p.state === "closed" ? t("main.messages.closed") : t(`right.packages.state.${p.state}`);
}
