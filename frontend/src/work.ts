/**
 * The Work tracker, from its one durable record (server: work_store.py,
 * held worked out by work_view.py). The page reads the whole list once, on
 * load and on every (re)connect of /ws/sessions (`workResync`), and then
 * takes each change as it comes: the server sends one frame per changed
 * item (`work_item`, `work_item_history`, `work_item_removed`) with the
 * view's revision, and `applyWorkFrame` replaces that one item by id and
 * places it as the frame's order says, every other item kept as it was (so
 * only its row draws again). A frame whose revision is not the next one
 * means one was missed: the list is read again in full. The open item's
 * detail takes the same frames for its own item (`workItemFrame`).
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
import { formatDateTime, t, wordsOf } from "./locale";
import { createStore } from "./store";
import type { CommandArgs } from "./uiContract";
import { bind, detailSlot, readForOpen, registerClose, routeStore } from "./uiNavigation";
import { registerRevealer } from "./uiNavigationReveal";
import { registerCommand } from "./uiRegistry";

/** "working" only ever on live evidence: a subagent on the piece writing now. */
export type WorkState = "planned" | "ready" | "working" | "activity_unknown" | "verifying" | "waiting_on_tim" | "blocked" | "done";

/** A subagent on a piece (agent:<session>/sub:<agent id>), as its own files
 *  show it now. `doing` is its last tool or line: its own words. */
export interface WorkLiveAgent {
  name: string;
  state: "working" | "finished" | "stale" | "waiting";
  written: number;
  doing: string;
  active_within: number;
}
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
  activity_reason?: string;
  /** Claimed working with no report for a day: shown stalled. */
  stalled?: boolean;
  /** The subagent on it, as its own files show it now. */
  live?: WorkLiveAgent | null;
  /** The summary of the latest report about it. */
  note: string;
  note_at: number | null;
  /** The reporting agent's own words for who did it: untrusted, shown beside the owner. */
  worker: string;
  /** Who sent its latest report, as its access key proves it: the key's agent, else its label. */
  reporter_name?: string;
  /** The keys of the pieces it comes after, and those of them not done yet. */
  after: string[];
  waits_for: string[];
  evidence: WorkEvidence[];
  /** While working or verifying, when it goes quiet with no further report
   *  (epoch seconds); null in any other state. */
  quiet_at: number | null;
  /** Held (waiting on him or blocked): what holds it, each a link such as
   *  decision:<key>, or a blocker's words. */
  until: string[];
  /** The state it moves to once nothing holds it; "" is ready. */
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

/** A decision no longer open: answered by him on the page or by voice,
 *  withdrawn by its asker (`answer` is why), or closed by his signing off
 *  its package without an answer (`answer` is ""). */
export interface WorkAnswered {
  key: string;
  question: string;
  answer: string;
  where: "page" | "voice" | "withdrawn" | "signed_off";
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
  activity_reason?: string;
  /** Verifying, with nothing reported for a while: "no recent update". */
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
  /** His projects holding its source, worked out when read. */
  projects: { id: string; name: string }[];
  follow_up: WorkFollowUp | null;
  /** Each act he is needed for, the first first (work_api.needs_of). */
  needs_you?: WorkNeed[];
  /** Needs nothing of him and nobody is moving it. */
  stalled?: boolean;
  /** The subagent it shows as what it is doing: working now, else waiting
   *  on its own background work. */
  live?: WorkLiveAgent | null;
  /** What his latest act in a review package did, when that was his latest review. */
  package_review: WorkPackageReview | null;
  /** Open decisions waiting on him while the item itself goes on. */
  also_waiting: number;
  /** An item with no pieces that is held: what holds it. */
  until: string[];
  /** When he approved it done (done is his alone to give). */
  approved_at: number | null;
  /** Whether he can approve it now: no decision open, no piece waiting,
   *  blocked or still working (work_store.approvable). */
  approvable: boolean;
  /** His words when he said it is not done yet. */
  review_note: string;
  review_note_at: number | null;
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
  /** The server's view revision this list is as of; each frame moves it by one. */
  revision: number;
}

/** The order of both lists after a change, by id. */
export interface WorkOrder {
  items: string[];
  history: string[];
}

/** One change of the held view (server: work_view.py): an item changed in
 *  place or newly listed (`work_item`), moved to History
 *  (`work_item_history`), or gone from the list (`work_item_removed`). */
export interface WorkFrame {
  type: "work_item" | "work_item_history" | "work_item_removed";
  revision: number;
  /** The item's whole JSON, exactly as /api/work lists it (not on a removal). */
  item?: WorkItem;
  /** The removed item's id. */
  id?: string;
  /** The list it is in now; null past the listing's reach. */
  list?: "items" | "history" | null;
  order: WorkOrder;
  groups: WorkGroup[];
}

export const WORK_FRAMES: readonly WorkFrame["type"][] = ["work_item", "work_item_history", "work_item_removed"];

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
  /** Who reported it, as its access key proves it; "" for his acts and JARVIS's own. */
  reporter_name?: string;
}

/** Moves when the open item's events and packages are to be read again
 *  (an answer given here, or a package of it answered). Never a read of
 *  the list. */
export const workChanged = createStore(0);

/** Moves when the whole list is to be read again: every (re)connect of
 *  /ws/sessions, and a frame whose revision shows one was missed. */
export const workResync = createStore(0);

/** The latest frame that carried an item, for the open item's detail. */
export const workItemFrame = createStore<{ item: WorkItem; revision: number } | null>(null);

export const STATE_WORDS = wordsOf<WorkState>(
  ["planned", "ready", "working", "activity_unknown", "verifying", "waiting_on_tim", "blocked", "done"],
  (s) => t(`right.work.state.${s}`));

/** Every item, as the server orders them (not done first, newest first), and
 *  the groups the Work tab heads them with. */
/** Fail closed while an older server is still serving legacy declared states. */
export function safeWorkState(state: string, evidenced = false): WorkState {
  // "working" is shown only with its live evidence beside it (a subagent on
  // the piece writing now); any other "working", from an older server or a
  // report, reads activity_unknown.
  if (state === "working") return evidenced ? "working" : "activity_unknown";
  const allowed = ["planned", "ready", "verifying", "waiting_on_tim", "blocked", "done"];
  return allowed.includes(state) ? state as WorkState : "activity_unknown";
}
const liveNow = (p: WorkPiece) => p.live?.state === "working";
export function safeWorkItem(item: WorkItem): WorkItem {
  return {...item, state: safeWorkState(item.state, item.pieces.some(liveNow)),
    pieces: item.pieces.map(p => ({...p, state: safeWorkState(p.state, liveNow(p))}))};
}

// The frames lately come, in the order they came: a full read applies those
// newer than it, so nothing that arrived while it was on its way is lost.
const RECENT_FRAMES = 200;
const recent: WorkFrame[] = [];

/** The list with one frame applied: its item replaced (or dropped) by id,
 *  both lists and the groups as the frame orders them, every other item the
 *  same object as before. */
export function appliedFrame(list: WorkList, frame: WorkFrame): WorkList {
  const known = new Map([...list.items, ...list.history].map((i) => [i.id, i]));
  if (frame.item) known.set(frame.item.id, safeWorkItem(frame.item));
  if (frame.type === "work_item_removed" && frame.id) known.delete(frame.id);
  const pick = (ids: string[]) => ids.flatMap((id) => known.get(id) ?? []);
  return { items: pick(frame.order.items), history: pick(frame.order.history),
           groups: frame.groups, revision: frame.revision };
}

const resync = () => workResync.set(workResync.get() + 1);

/** A full read with the frames that came since it was taken applied; a
 *  gap among them asks for another read. */
function caughtUp(list: WorkList): WorkList {
  let out = list;
  for (const frame of recent) {
    if (frame.revision <= out.revision) continue;
    if (frame.revision !== out.revision + 1) { resync(); break; }
    out = appliedFrame(out, frame);
  }
  return out;
}

export const workList = liveRead(async () => {
  const result = await getJson<WorkList>("/api/work");
  return caughtUp({...result, revision: result.revision ?? 0, items: result.items.map(safeWorkItem),
                   history: (result.history ?? []).map(safeWorkItem)});
}, [workResync]);

/** One frame from /ws/sessions: applied to the list in place when it is the
 *  next revision, kept for the read under way when there is no list yet,
 *  and a full read when one was missed. Never fetches otherwise. */
export function applyWorkFrame(frame: WorkFrame): void {
  recent.push(frame);
  if (recent.length > RECENT_FRAMES) recent.shift();
  if (frame.item) workItemFrame.set({ item: safeWorkItem(frame.item), revision: frame.revision });
  const now = workList.get();
  if (!now.value || frame.revision <= now.value.revision) return;
  if (frame.revision !== now.value.revision + 1) { resync(); return; }
  workList.set({ value: appliedFrame(now.value, frame), error: now.error });
}

/** One item in full: with its latest events and the open packages sent for
 *  it (`make_package --work`), each with its questions. */
export const getWorkItem = async (id: string) => {
  const result = await getJson<{ item: WorkItem; events: WorkEvent[]; packages: InboxDetail[];
                                 revision?: number }>(`/api/work/${encodeURIComponent(id)}`);
  return {...result, revision: result.revision ?? 0, item: safeWorkItem(result.item)};
};

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
  const held = b.waits_for.length ? ` (${b.waits_for.join(", ")})` : "";
  const line = (b.text && b.kind !== "decision" ? `${said}: ${b.text}` : said) + held;
  return b.unlinked ? `${line} · ${t("main.work.unlinked")}` : line;
}

/** Who asked a decision: its access key's label, or "the agent that asked". */
export function askerWords(label: string): string {
  return label || t("main.work.askerUnknown");
}

/** An answered decision nobody has started on: "Answered — waiting for … to start". */
/** One act he is needed for on an item, and where to do it. */
export interface WorkNeed {
  kind: "decision" | "package" | "review_package" | "review" | "piece";
  key?: string;
  inbox?: string;
  /** The question, the package's title or the piece's title (the agent's words). */
  text?: string;
  links?: string[];
}

/** The state an item's pill shows: Stalled, Waiting on a subagent's
 *  background work, or its state. */
export function rowState(item: WorkItem): string {
  if (item.stalled) return "stalled";
  if (item.state === "activity_unknown" && item.live?.state === "waiting") return "background";
  return item.state;
}

export function rowStateWords(item: WorkItem, say: typeof t): string {
  const shown = rowState(item);
  if (shown === "stalled") return say("right.work.state.stalled");
  if (shown === "background") return say("right.work.state.background", { name: item.live!.name });
  return STATE_WORDS[item.state];
}

/** A subagent's line: "Release agent is working now · Using Bash", or
 *  "… is waiting on its own background work". */
export function liveWords(live: WorkLiveAgent): string {
  const said = t(`main.work.live.${live.state}`, { name: live.name });
  return live.state === "working" && live.doing ? `${said} · ${live.doing}` : said;
}

/** A need in words: the act and the place, as the brain's work_status says it. */
export function needWords(need: WorkNeed): string {
  return t(`main.work.need.${need.kind}`, { text: need.text ?? "", links: (need.links ?? []).join(", ") });
}

/** What his latest act in a review package did to the item. */
export interface WorkPackageReview {
  outcome: "approved" | "review_returned" | "work_added" | "no_verdict" | "not_applied";
  at: number;
  /** Why it was not applied (not_applied), in JARVIS's words. */
  why: string;
  package: string;
}

/** "Approved by you in the package, 16:33", and the rest of them. */
export function packageReviewWords(r: WorkPackageReview): string {
  const time = formatDateTime(r.at * 1000, { hour: "2-digit", minute: "2-digit" });
  return t(`main.work.packageReview.${r.outcome}`, { time, why: r.why });
}

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

const slot = detailSlot<string>("work", Object.is, (id) => id);

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

export type ReviewAction = "approve" | "not_done" | "add_work";

/** His review of an item (approve, not done yet, add work), the same call
 *  as the brain's review_work. Resolves with the refusal's reason, or null. */
export async function reviewWork(id: string, action: ReviewAction, words = ""): Promise<string | null> {
  const res = await fetch(`/api/work/${encodeURIComponent(id)}/review`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ action, words }),
  }).catch(() => null);
  if (res?.ok) {
    workChanged.set(workChanged.get() + 1);
    return null;
  }
  if (!res) return t("right.workflows.noAnswer");
  const body = (await res.json().catch(() => ({}))) as { error?: string; detail?: string };
  return body.error || body.detail || t("right.workflows.refused", { status: String(res.status) });
}

/** Answer an open decision. Resolves with the refusal's reason, or null. */
export const answerWorkDecision = (id: string, key: string, answer: string) =>
  postAnswer(`/api/work/${encodeURIComponent(id)}/decisions/${encodeURIComponent(key)}`,
             answer, workChanged);
