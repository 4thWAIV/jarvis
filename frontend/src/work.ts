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

import { getJson, postAnswer } from "./fetchJson";
import { liveRead } from "./liveRead";
import { t, wordsOf } from "./locale";
import { createStore } from "./store";
import { bind, detailSlot, readForOpen, registerClose, routeStore } from "./uiNavigation";
import { registerCommand } from "./uiRegistry";

export type WorkState = "planned" | "working" | "verifying" | "waiting_on_tim" | "blocked" | "done";
export type WhoKind = "agent" | "run" | "workflow" | "tim";

export interface WorkWho {
  kind: WhoKind;
  ref: string | null;
  /** The one name of its record, or null (Tim, or a record JARVIS no longer has). */
  name: string | null;
}

/** A commit, an absolute path or an http(s) URL; recorded, never opened. */
export interface WorkEvidence {
  kind: "commit" | "path" | "url";
  value: string;
}

export interface WorkPiece {
  key: string;
  title: string;
  who: WorkWho;
  state: WorkState;
  /** The summary of the latest report about it. */
  note: string;
  note_at: number | null;
  /** The reporting agent's own words for who did it: untrusted, shown beside the owner. */
  worker: string;
  /** The keys of the pieces it comes after, and those of them not done yet. */
  after: string[];
  waits_for: string[];
  evidence: WorkEvidence[];
  /** While working or verifying, when it goes quiet with no further report
   *  (epoch seconds); null in any other state. */
  quiet_at: number | null;
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
  /** The area it was reported under (untrusted), or "". */
  area: string;
  blocked_pieces: number;
  waiting_pieces: number;
  /** In dependency order: each piece after every piece it names. */
  pieces: WorkPiece[];
  decisions: WorkDecision[];
  /** As a piece's, from the item's latest report. */
  quiet_at: number | null;
}

/** The items under one heading: an area, or the source folder of items with none. */
export interface WorkGroup {
  grouped_by: "area" | "folder";
  /** The area, or the folder's whole path. */
  name: string;
  /** The area, or the folder's last name. */
  label: string;
  ids: string[];
}

export interface WorkList {
  items: WorkItem[];
  groups: WorkGroup[];
}

/** Which mark an item or a piece carries, if any: blocked before waiting on him. */
export type WorkMark = "blocked" | "waiting" | null;

export function pieceMark(piece: WorkPiece): WorkMark {
  return piece.state === "blocked" ? "blocked" : piece.state === "waiting_on_tim" ? "waiting" : null;
}

/** An item's mark: itself or any of its pieces blocked, else waiting on him. */
export function itemMark(item: WorkItem): WorkMark {
  if (item.state === "blocked" || item.blocked_pieces > 0) return "blocked";
  if (item.state === "waiting_on_tim" || item.waiting_pieces > 0) return "waiting";
  return null;
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
  ["planned", "working", "verifying", "waiting_on_tim", "blocked", "done"],
  (s) => t(`right.work.state.${s}`));

/** Every item, as the server orders them (not done first, newest first), and
 *  the groups the Work tab heads them with. */
export const workList = liveRead(() => getJson<WorkList>("/api/work"), [workChanged]);

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
    if (!(workList.get().value?.items ?? []).some((i) => i.id === itemId)) {
      const read = await readForOpen(`/api/work/${encodeURIComponent(itemId)}`, signal);
      if ("refused" in read) return read.refused;
    }
    return { status: "completed", effects: slot.show(itemId) };
  },
});

registerClose("work.close", slot);

/** Answer an open decision. Resolves with the refusal's reason, or null. */
export const answerWorkDecision = (id: string, key: string, answer: string) =>
  postAnswer(`/api/work/${encodeURIComponent(id)}/decisions/${encodeURIComponent(key)}`,
             answer, workChanged);
