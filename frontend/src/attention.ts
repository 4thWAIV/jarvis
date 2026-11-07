/**
 * Updates: the attention record (attention_store.py), as the page reads it.
 *
 * Every routine event JARVIS used to announce (a message delivered or
 * answered, a session's message or needs, a run ending, a person's message
 * arriving) is an item in that record, said by no one: a digest may speak
 * of some, when the admission policy allows. The page shows what is
 * waiting as one grouped count with Review updates, never one notice per
 * event, and never plays a backlog when he comes back.
 *
 * Read at once, on every `attention` hint and every (re)connect snapshot on
 * /ws/sessions (sessionsSocket.ts), and on every (re)connect of the voice
 * socket (liveRead). Mark seen and Hold spoken updates go through
 * /api/attention, the same calls the brain's mark_updates_seen and
 * set_attention_settings make (attention_api.py).
 */

import { liveRead } from "./liveRead";
import { codeWords, t } from "./locale";
import { createStore } from "./store";

export interface AttentionItem {
  id: number;
  kind: string;
  source_id: string;
  revision: number;
  event: string;
  facts: Record<string, unknown>;
  /** Other people's words (names, subjects): shown as text, never markup. */
  untrusted: Record<string, string>;
  state: "pending" | "seen" | "resolved";
  created_at: number;
  updated_at: number;
  seen_at: number | null;
}

export interface AttentionSettings {
  oldest_event_sec: number;
  digest_cooldown_sec: number;
  settled_exchange_sec: number;
  engagement_sec: number;
  away: boolean;
}

export interface AttentionOverview {
  pending: number;
  availability: "engaged" | "away" | "unknown";
  items: AttentionItem[];
  settings: AttentionSettings;
}

export const attentionChanged = createStore(0);

async function call<T>(method: string, url: string, body?: unknown): Promise<T> {
  const res = await fetch(url, {
    method,
    headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`${method} ${url}: ${res.status}`);
  return res.json();
}

/** The record as read, or an error: an answer without its items and
 *  settings (a server without the attention record) is a failed read, never
 *  a value the notice would break on. */
async function readOverview(): Promise<AttentionOverview> {
  const value = await call<Partial<AttentionOverview> | null>("GET", "/api/attention");
  if (!value || !Array.isArray(value.items) || !value.settings || typeof value.settings !== "object") {
    throw new Error("GET /api/attention: not an attention record");
  }
  return value as AttentionOverview;
}

export const attention = liveRead(readOverview, [attentionChanged]);

/** Mark these seen (every pending one when `ids` is absent). */
export async function markSeen(ids?: number[]): Promise<void> {
  await call("POST", "/api/attention/seen", ids === undefined ? {} : { ids });
  attention.refresh();
}

/** Hold spoken updates (away), or let them be spoken again. */
export async function setAway(away: boolean): Promise<void> {
  await call("PUT", "/api/attention/settings", { away });
  attention.refresh();
}

/** Who or what an item is about, as its source wrote it, or "". */
export function itemName(item: AttentionItem): string {
  const u = item.untrusted;
  return u.agent ?? u.sender ?? u.project ?? u.package ?? u.workflow ?? u.work ?? u.to ?? "";
}

/** One item in words: its kind and event's catalog entry, with the name it
 *  is about; the codes themselves for a pair the catalog has no words for. */
export function itemWords(item: AttentionItem): string {
  const name = itemName(item) || t("main.updates.someone");
  const words = codeWords("main.updates.event", `${item.kind}.${item.event}`, { name });
  return words ?? `${item.kind}: ${item.event}${itemName(item) ? ` (${itemName(item)})` : ""}`;
}
