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
import type { InboxDetail } from "./packageInbox";
import { liveRead } from "./liveRead";
import { t, wordsOf } from "./locale";
import { createStore } from "./store";
import type { CommandArgs } from "./uiContract";
import { bind, detailSlot, readForOpen, registerClose, routeStore } from "./uiNavigation";
import { registerRevealer } from "./uiNavigationReveal";
import { registerCommand } from "./uiRegistry";

export type WorkState = "planned" | "working" | "verifying" | "waiting_on_tim" | "blocked" | "done"
  /** Read time only: every piece done and no decision open. */
  | "ready_to_close";
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
  /** Waiting on him: the answer it waits for (a link such as decision:<key>), or "". */
  until: string;
  /** The state it moves to on that answer; "" is working. */
  then: string;
  waiting_since: number | null;
  /** Waiting on him with no link to an answer for over a day. */
  unlinked: boolean;
}

export interface WorkDecision {
  key: string;
  question: string;
  options: string[];
  recommendation: string;
  opened_at: number;
  /** A page package to look at before answering, by its id or name, or "". */
  package: string;
}

/** A decision he has answered: on the page or by voice. */
export interface WorkAnswered {
  key: string;
  question: string;
  answer: string;
  where: "page" | "voice";
  answered_at: number;
  package: string;
}

export type BlockerKind = "decision" | "no_follow_up" | "blocked" | "item_waiting" | "piece_blocked"
  | "piece_waiting" | "piece_after";

/** One thing holding the item up, in the order the server lists them: his
 *  decisions, the item itself blocked, blocked pieces, pieces waiting on
 *  him, pieces waiting on other pieces. */
export interface WorkBlocker {
  kind: BlockerKind;
  /** The decision's or piece's key; "" for the item itself. */
  key: string;
  title: string;
  text: string;
  waits_for: string[];
  /** A wait on him with no link to an answer, open for over a day. */
  unlinked: boolean;
}

/** He answered a decision and nothing has been reported since, with no
 *  piece working: waiting for its asker to start. */
export interface WorkFollowUp {
  key: string;
  question: string;
  /** The asker's access-key label, or "" when unknown. */
  asker: string;
  answered_at: number;
  /** Half an hour gone with no report: "answered, no follow-up". */
  overdue: boolean;
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
  /** Newest first. */
  answered: WorkAnswered[];
  blockers: WorkBlocker[];
  closed_at: number | null;
  /** Closed, or done a while: listed under History. */
  history: boolean;
  /** How many questions the packages sent for it still wait on him for. */
  package_questions: number;
  /** As a piece's, from the item's latest report. */
  quiet_at: number | null;
  follow_up: WorkFollowUp | null;
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
  /** The open items. */
  items: WorkItem[];
  groups: WorkGroup[];
  /** Closed, or done a while; the most recently closed or finished first. */
  history: WorkItem[];
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
  ["planned", "working", "verifying", "waiting_on_tim", "blocked", "done", "ready_to_close"],
  (s) => t(`right.work.state.${s}`));

/** Every item, as the server orders them (not done first, newest first), and
 *  the groups the Work tab heads them with. */
export const workList = liveRead(() => getJson<WorkList>("/api/work"), [workChanged]);

/** One item in full: with its latest events and the open packages sent for
 *  it (`make_package --work`), each with its questions. */
export const getWorkItem = (id: string) =>
  getJson<{ item: WorkItem; events: WorkEvent[]; packages: InboxDetail[] }>(
    `/api/work/${encodeURIComponent(id)}`);

/** One blocker in a line: what holds it, and the piece's or item's note. */
export function blockerWords(item: WorkItem, b: WorkBlocker): string {
  const titleOf = (key: string) => item.pieces.find((p) => p.key === key)?.title ?? key;
  if (b.kind === "piece_after") {
    return t("main.work.blocker.piece_after", { title: b.title, pieces: b.waits_for.map(titleOf).join(", ") });
  }
  if (b.kind === "no_follow_up") {
    return t("main.work.blocker.no_follow_up", { title: b.title, asker: askerWords(b.text) });
  }
  const said = t(`main.work.blocker.${b.kind}`, { title: b.title });
  const line = b.text && b.kind !== "decision" ? `${said}: ${b.text}` : said;
  return b.unlinked ? `${line} · ${t("main.work.unlinked")}` : line;
}

/** Who asked a decision: its access key's label, or "the agent that asked". */
export function askerWords(label: string): string {
  return label || t("main.work.askerUnknown");
}

/** An answered decision nobody has started on: "Answered — waiting for … to start". */
export function followUpWords(f: WorkFollowUp): string {
  return t("right.work.followUp", { asker: askerWords(f.asker) });
}

/** Who is on a piece, in words: an agent's, run's or workflow's one name,
 *  or "You" for him. */
export function whoWords(who: WorkWho): string {
  if (who.kind === "tim") return t("main.work.who.tim");
  return t(`main.work.who.${who.kind}`, { name: who.name ?? who.ref ?? "" });
}

// ── the item open in the centre ──────────────────────────────────────────

const slot = detailSlot<string>("work");

export type WorkTab = CommandArgs["work.tab.select"]["tab"];
export const WORK_TABS: readonly WorkTab[] = ["overview", "decisions", "blocking", "pieces", "reports"];

/** The open item's tab: its own choice while it stays open, else the first. */
export const workTab = createStore<{ itemId: string | null; tab: WorkTab }>({ itemId: null, tab: "overview" });

export const tabOf = (itemId: string): WorkTab => {
  const now = workTab.get();
  return now.itemId === itemId ? now.tab : "overview";
};

// An item closed, or replaced by another, forgets its tab.
slot.value.subscribe(() => {
  if (workTab.get().itemId !== null && slot.value.get() !== workTab.get().itemId) {
    workTab.set({ itemId: null, tab: "overview" });
  }
});

registerCommand("work.tab.select", {
  availability: ({ itemId }) => slot.value.get() === itemId
    ? { available: true } : { available: false, reason: "no-entity" },
  execute({ itemId, tab }) {
    workTab.set({ itemId, tab });
    return { status: "completed", effects: [] };
  },
});

/** Whether the Work tab's History is shown; changed only by
 *  `work.showHistory`, its toggle and the brain alike. */
export const showHistory = createStore(false);

registerCommand("work.showHistory", {
  availability: () => ({ available: true }),
  execute({ shown }) { showHistory.set(shown); return { status: "completed", effects: ["list.filtered"] }; },
});

const listed = (itemId: string) => {
  const list = workList.get().value;
  return [...(list?.items ?? []), ...(list?.history ?? [])].find((i) => i.id === itemId);
};

// Pointing at an item in History opens History, as its toggle does.
registerRevealer("right.workflows", "workItem", async (ref) => {
  const list = workList.get().value;
  if (!list) return "not-loaded";
  const item = listed(ref.key!);
  if (!item) return "entity-gone";
  if (item.history) showHistory.set(true);
  return true;
});

/** The tab each kind of thing in an item is drawn on. */
const TAB_OF = { workPiece: "pieces", workDecision: "decisions", workBlocker: "blocking" } as const;

// Pointing at a piece, decision or blocker not drawn: its item is opened on
// the tab that draws it, by the registered commands.
for (const kind of Object.keys(TAB_OF) as (keyof typeof TAB_OF)[]) {
  registerRevealer("main.work", kind, async (ref, { navigate }) => {
    const itemId = ref.key!.split("/")[0];
    const tab = TAB_OF[kind];
    const opened = slot.value.get() === itemId
      ? await navigate("work.tab.select", { itemId, tab })
      : await navigate("work.open", { itemId, tab });
    return opened ? true : "no-entity";
  });
}

/** The work item the centre area shows, or null. Writing it runs
 *  `work.open` or `work.close`, as a person's navigation. */
export const openWork = routeStore(slot.value, (id: string | null) =>
  id === null ? bind("work.close", {}) : bind("work.open", { itemId: id }));

registerCommand("work.open", {
  availability: () => ({ available: true }),
  async execute({ itemId, tab }, { signal }) {
    if (!listed(itemId)) {
      const read = await readForOpen(`/api/work/${encodeURIComponent(itemId)}`, signal);
      if ("refused" in read) return read.refused;
    }
    const effects = slot.show(itemId);
    workTab.set({ itemId, tab: tab ?? tabOf(itemId) });
    return { status: "completed", effects };
  },
});

registerClose("work.close", slot);

/** Answer an open decision. Resolves with the refusal's reason, or null. */
export const answerWorkDecision = (id: string, key: string, answer: string) =>
  postAnswer(`/api/work/${encodeURIComponent(id)}/decisions/${encodeURIComponent(key)}`,
             answer, workChanged);
