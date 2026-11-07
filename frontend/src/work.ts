/**
 * The Work tracker, from its one durable record (server: work_store.py,
 * /api/work). The page keeps no account of its own: /ws/sessions says
 * "work" when an item changes (sessionsSocket.ts moves `workChanged`), and
 * every view reads the record again.
 *
 * One item is open in the centre at a time, in the `work` detail slot,
 * through `work.open` and `work.close` (uiNavigation.ts): a click and the
 * brain's request run the same handler. Answering a decision is his own act
 * on the item, not navigation.
 *
 * Everything an item holds was written by the agent reporting it, and is
 * drawn as plain text.
 */

import { liveRead } from "./liveRead";
import { t, wordsOf } from "./locale";
import { createStore } from "./store";
import { bind, detailSlot, readForOpen, registerClose, routeStore } from "./uiNavigation";
import { registerCommand } from "./uiRegistry";

export type WorkState = "planned" | "working" | "waiting_on_tim" | "blocked" | "done";
export type WhoKind = "agent" | "run" | "workflow" | "tim";

export interface WorkWho {
  kind: WhoKind;
  ref: string | null;
  /** The one name of its record, or null (Tim, or a record JARVIS no longer has). */
  name: string | null;
}

export interface WorkPiece {
  key: string;
  title: string;
  who: WorkWho;
  state: WorkState;
}

export interface WorkDecision {
  key: string;
  question: string;
  options: string[];
  recommendation: string;
  opened_at: number;
}

export interface WorkItem {
  id: string;
  title: string;
  source_kind: "zip" | "folder";
  source_path: string;
  /** What a stat of the source found when the record was read. */
  source: "present" | "missing" | "wrong_kind";
  state: WorkState;
  /** Working, with nothing reported for a while: "no recent update". */
  quiet: boolean;
  latest_update: string;
  /** The one useful update: an open decision's question first. */
  headline: string;
  revision: number;
  created_at: number;
  updated_at: number;
  pieces: WorkPiece[];
  decisions: WorkDecision[];
}

export interface WorkEvent {
  item_id: string;
  kind: string;
  summary: string;
  detail: Record<string, unknown>;
  at: number;
  seq: number;
}

/** Moves on every "work" hint, and on every (re)connect of /ws/sessions. */
export const workChanged = createStore(0);

export const STATE_WORDS = wordsOf<WorkState>(
  ["planned", "working", "waiting_on_tim", "blocked", "done"], (s) => t(`right.work.state.${s}`));

async function getJson<T>(path: string): Promise<T> {
  const res = await fetch(path);
  if (!res.ok) throw new Error(`GET ${path}: ${res.status}`);
  return (await res.json()) as T;
}

/** Every item, as the server orders them (not done first, newest first). */
export const workItems = liveRead(
  () => getJson<{ items: WorkItem[] }>("/api/work").then((r) => r.items), [workChanged]);

export const getWorkItem = (id: string) =>
  getJson<{ item: WorkItem; events: WorkEvent[] }>(`/api/work/${encodeURIComponent(id)}`);

/** Who is on a piece, in words: an agent's, run's or workflow's one name,
 *  or "You" for him. */
export function whoWords(who: WorkWho): string {
  if (who.kind === "tim") return t("main.work.who.tim");
  return t(`main.work.who.${who.kind}`, { name: who.name ?? who.ref ?? "" });
}

// ── the item open in the centre ──────────────────────────────────────────

const slot = detailSlot<string>("work");

/** The work item the centre area shows, or null. Writing it runs
 *  `work.open` or `work.close`, as a person's navigation. */
export const openWork = routeStore(slot.value, (id: string | null) =>
  id === null ? bind("work.close", {}) : bind("work.open", { itemId: id }));

registerCommand("work.open", {
  availability: () => ({ available: true }),
  async execute({ itemId }, { signal }) {
    if (!(workItems.get().value ?? []).some((i) => i.id === itemId)) {
      const read = await readForOpen(`/api/work/${encodeURIComponent(itemId)}`, signal);
      if ("refused" in read) return read.refused;
    }
    return { status: "completed", effects: slot.show(itemId) };
  },
});

registerClose("work.close", slot);

/** Answer an open decision. Resolves with the refusal's reason, or null. */
export async function answerWorkDecision(id: string, key: string, answer: string): Promise<string | null> {
  const res = await fetch(`/api/work/${encodeURIComponent(id)}/decisions/${encodeURIComponent(key)}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ answer }),
  }).catch(() => null);
  if (res?.ok) {
    workChanged.set(workChanged.get() + 1);
    return null;
  }
  if (!res) return t("right.workflows.noAnswer");
  const body = (await res.json().catch(() => ({}))) as { error?: string; detail?: string };
  return body.error || body.detail || t("right.workflows.refused", { status: String(res.status) });
}
