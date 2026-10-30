/**
 * The Queue's one normalized record of what JARVIS has said about the
 * owner's messages, and everything the page reads out of it. Pure: no
 * fetch, no timers, no React (queue.ts does the reading and writing), so
 * every rule here is tested on its own (tests/js/queue_store_cases.ts).
 *
 *   - items by thread id (the page's item id IS the thread id): the
 *     owner's work state, the item's message and draft ids, a command kept
 *     while the team server was out of reach, why it cannot be dismissed,
 *     and whether it is listed in Waiting or Active / Outbox now;
 *   - messages by id (immutable once accepted), with their files;
 *   - receipts by message id;
 *   - drafts by message id, and the new conversations not yet accepted;
 *   - the dismissed items read so far, a page at a time.
 *
 * Every mutable record keeps the higher revision it has seen (work state,
 * receipts; drafts by draft revision), so a delayed or repeated answer
 * never moves a fact back and a repeat changes nothing. Nothing absent from
 * an answer is forgotten, but an item a snapshot no longer lists leaves
 * Waiting and Active / Outbox (it was dismissed elsewhere). A snapshot for
 * another pairing replaces everything: it is not this owner's.
 *
 * Wire names stay snake_case, as messagingContract.ts has them; there is no
 * second shape to convert to.
 */

import type {
  AcceptedMessage, Availability, DismissBlocker, Draft, HistoryPage, LocalFile, LocalItem,
  LocalMessage, QueueSnapshot, ReceiptFacts, SendState, TeamMember, WorkCommand,
  WorkCommandRequest, WorkState,
} from "./messagingContract";

export interface ItemRecord {
  work_state: WorkState;
  /** By (accepted_at, message_id). */
  message_ids: string[];
  draft_ids: string[];
  pending_command: WorkCommandRequest | null;
  dismiss_blockers: DismissBlocker[];
  /** In Waiting or Active / Outbox by the latest word on it. */
  listed: boolean;
}

export interface MessageRecord {
  message: AcceptedMessage;
  direction: "incoming" | "outgoing";
  self_message: boolean;
  files: LocalFile[];
}

export interface QueueState {
  /** False when this JARVIS has no messaging routes at all. */
  offered: boolean;
  /** Whether a snapshot has been read yet. */
  loaded: boolean;
  server_id: string | null;
  owner_github_id: number | null;
  availability: Availability | null;
  stale: boolean;
  history_count: number;
  items: Record<string, ItemRecord>;
  messages: Record<string, MessageRecord>;
  receipts: Record<string, ReceiptFacts>;
  drafts: Record<string, Draft>;
  /** New conversations not yet accepted, by their message id. */
  unsent: string[];
  /** Dismissed items read so far, newest arrival first. */
  history: string[];
  /** Where the next page of them starts; null once all are read. */
  history_next: string | null;
  history_read: boolean;
}

export const EMPTY: QueueState = {
  offered: true, loaded: false, server_id: null, owner_github_id: null, availability: null,
  stale: false, history_count: 0, items: {}, messages: {}, receipts: {}, drafts: {},
  unsent: [], history: [], history_next: null, history_read: false,
};

// ── Keeping the newer of two ─────────────────────────────────────────────

function higher<T extends { revision: number }>(held: T | undefined, got: T): T {
  return held && held.revision >= got.revision ? held : got;
}

/** A save always moves the draft revision; at the same revision the later
 *  answer has the send's newer state. */
function newerDraft(held: Draft | undefined, got: Draft): Draft {
  return held && held.draft_revision > got.draft_revision ? held : got;
}

function time(iso: string): number {
  const t = Date.parse(iso);
  return Number.isNaN(t) ? 0 : t;
}

/** (accepted_at, message_id), as the contract orders messages. */
function messageOrder(a: AcceptedMessage, b: AcceptedMessage): number {
  return time(a.accepted_at) - time(b.accepted_at) || (a.message_id < b.message_id ? -1 : 1);
}

/** Newest arrival first, the thread id breaking ties (item_order_key,
 *  reversed). Put back never moves arrival_at, so an item put back returns
 *  to where it was. */
export function itemOrder(a: WorkState, b: WorkState): number {
  return time(b.arrival_at) - time(a.arrival_at)
    || (a.thread_id === b.thread_id ? 0 : a.thread_id < b.thread_id ? 1 : -1);
}

// ── Taking JARVIS's answers in ───────────────────────────────────────────

function withMessage(state: QueueState, local: LocalMessage): QueueState {
  const id = local.message.message_id;
  const held = state.messages[id];
  return {
    ...state,
    messages: {
      ...state.messages,
      // The message itself never changes; its files' states do.
      [id]: { message: held?.message ?? local.message, direction: local.direction,
              self_message: local.self_message, files: local.files },
    },
    receipts: { ...state.receipts, [id]: higher(state.receipts[id], local.receipts) },
  };
}

function withDraft(state: QueueState, draft: Draft): QueueState {
  return { ...state, drafts: { ...state.drafts, [draft.message_id]: newerDraft(state.drafts[draft.message_id], draft) } };
}

/** One item as JARVIS served it. `listed` says whether it is in Waiting or
 *  Active / Outbox; left out, it follows the item's placement. */
export function applyItem(state: QueueState, item: LocalItem, listed?: boolean): QueueState {
  let next = state;
  for (const m of item.messages) next = withMessage(next, m);
  for (const d of item.drafts) next = withDraft(next, d);
  const thread = item.work_state.thread_id;
  const held = next.items[thread];
  const work = higher(held?.work_state, item.work_state);
  const ids = new Set([...(held?.message_ids ?? []), ...item.messages.map((m) => m.message.message_id)]);
  const messages = next.messages;
  return {
    ...next,
    items: {
      ...next.items,
      [thread]: {
        work_state: work,
        message_ids: [...ids].sort((a, b) => messageOrder(messages[a].message, messages[b].message)),
        draft_ids: item.drafts.map((d) => d.message_id),
        pending_command: item.pending_command,
        dismiss_blockers: item.dismiss_blockers,
        listed: listed ?? work.placement !== "inactive",
      },
    },
    // A new conversation is an item once accepted.
    unsent: next.unsent.filter((id) => id !== thread),
  };
}

/** The Queue's snapshot: Waiting, Active / Outbox and the unsent new
 *  conversations. Null: no messaging routes on this JARVIS. */
export function applySnapshot(state: QueueState, snapshot: QueueSnapshot | null): QueueState {
  if (!snapshot) return { ...EMPTY, offered: false, loaded: true };
  const samePairing = state.server_id === snapshot.server_id
    && state.owner_github_id === snapshot.owner_github_id;
  let next: QueueState = {
    ...(samePairing ? state : EMPTY),
    offered: true,
    loaded: true,
    server_id: snapshot.server_id,
    owner_github_id: snapshot.owner_github_id,
    availability: snapshot.availability,
    stale: snapshot.stale,
    history_count: snapshot.history_count,
  };
  const listed = new Set(snapshot.items.map((i) => i.work_state.thread_id));
  const items = { ...next.items };
  for (const [thread, record] of Object.entries(items)) {
    if (record.listed && !listed.has(thread)) items[thread] = { ...record, listed: false };
  }
  next = { ...next, items };
  for (const item of snapshot.items) next = applyItem(next, item, true);
  for (const d of snapshot.unsent) next = withDraft(next, d);
  return { ...next, unsent: snapshot.unsent.map((d) => d.message_id) };
}

/** The item's state after a command (WorkCommandResult, or a conflict's
 *  `latest`). */
export function applyWorkState(state: QueueState, work: WorkState): QueueState {
  const held = state.items[work.thread_id];
  if (!held) return state;
  const kept = higher(held.work_state, work);
  if (kept === held.work_state) return state;
  return {
    ...state,
    items: { ...state.items, [work.thread_id]: { ...held, work_state: kept, listed: kept.placement !== "inactive" } },
  };
}

/** A draft as a save, review, cancel or retry returned it. */
export function applyDraft(state: QueueState, draft: Draft): QueueState {
  let next = withDraft(state, draft);
  const item = next.items[draft.thread_id];
  if (item) {
    if (!item.draft_ids.includes(draft.message_id)) {
      next = { ...next, items: { ...next.items, [draft.thread_id]: { ...item, draft_ids: [...item.draft_ids, draft.message_id] } } };
    }
  } else if (draft.thread_id === draft.message_id && !next.unsent.includes(draft.message_id)) {
    next = { ...next, unsent: [draft.message_id, ...next.unsent] };
  }
  return next;
}

/** A draft that was discarded. */
export function removeDraft(state: QueueState, messageId: string): QueueState {
  const draft = state.drafts[messageId];
  if (!draft) return state;
  const drafts = { ...state.drafts };
  delete drafts[messageId];
  const item = state.items[draft.thread_id];
  return {
    ...state,
    drafts,
    unsent: state.unsent.filter((id) => id !== messageId),
    items: item ? { ...state.items, [draft.thread_id]: { ...item, draft_ids: item.draft_ids.filter((id) => id !== messageId) } }
                : state.items,
  };
}

/** A message as the read route returned it. */
export function applyMessage(state: QueueState, local: LocalMessage): QueueState {
  return withMessage(state, local);
}

/** A file's state, after a download asked for it. */
export function applyFile(state: QueueState, messageId: string, file: LocalFile): QueueState {
  const held = state.messages[messageId];
  if (!held) return state;
  const files = held.files.some((f) => f.attachment_id === file.attachment_id)
    ? held.files.map((f) => (f.attachment_id === file.attachment_id ? file : f))
    : [...held.files, file];
  return { ...state, messages: { ...state.messages, [messageId]: { ...held, files } } };
}

/** One page of dismissed items; `first` starts the list again. */
export function applyHistory(state: QueueState, page: HistoryPage, first: boolean): QueueState {
  let next = state;
  for (const item of page.items) next = applyItem(next, item, false);
  const ids = page.items.map((i) => i.work_state.thread_id);
  const history = first ? ids : [...next.history, ...ids.filter((id) => !next.history.includes(id))];
  return { ...next, history, history_next: page.continuation, history_read: true };
}

// ── A command on its way ─────────────────────────────────────────────────

/** How an item shows while a command on it is on its way: only what the
 *  command plainly changes, for the page alone. The answer replaces it, and
 *  a refusal takes it away again, leaving the item as it was. */
export function predicted(work: WorkState, change: WorkCommand): WorkState {
  switch (change.command) {
    case "open": return { ...work, placement: "active", dismissed_at: null };
    case "put_back": return { ...work, placement: "queued", dismissed_at: null };
    case "dismiss": return { ...work, placement: "inactive" };
    case "restore": return work.placement === "inactive" ? { ...work, placement: "queued", dismissed_at: null } : work;
    case "set_addressed":
      return { ...work, addressed_at: change.addressed ? work.addressed_at ?? new Date().toISOString() : null };
    case "set_unread_reminder": return { ...work, unread_reminder: change.unread };
    case "set_action_owner":
      return { ...work, action_owner_github_id: isSelf(work) ? work.owner_github_id : change.action_owner_github_id };
  }
}

/** The state as the page shows it, with each item's command on its way
 *  applied by `predicted`. */
export function withPending(state: QueueState, pending: Record<string, WorkCommand>): QueueState {
  const threads = Object.keys(pending).filter((t) => state.items[t]);
  if (!threads.length) return state;
  const items = { ...state.items };
  for (const t of threads) {
    const work = predicted(items[t].work_state, pending[t]);
    items[t] = { ...items[t], work_state: work, listed: work.placement !== "inactive" };
  }
  return { ...state, items };
}

// ── Reading it out ───────────────────────────────────────────────────────

function listedAt(state: QueueState, placement: WorkState["placement"]): ItemRecord[] {
  return Object.values(state.items)
    .filter((i) => i.listed && i.work_state.placement === placement)
    .sort((a, b) => itemOrder(a.work_state, b.work_state));
}

/** Waiting: newest arrival first. */
export function waiting(state: QueueState): ItemRecord[] {
  return listedAt(state, "queued");
}

/** Active / Outbox: opened incoming work and outgoing exchanges together. */
export function active(state: QueueState): ItemRecord[] {
  return listedAt(state, "active");
}

/** The dismissed items read so far that are still dismissed. */
export function dismissed(state: QueueState): ItemRecord[] {
  return state.history.map((id) => state.items[id])
    .filter((i): i is ItemRecord => !!i && i.work_state.placement === "inactive");
}

/** The new conversations on their way out, newest first. */
export function unsentDrafts(state: QueueState): Draft[] {
  return state.unsent.map((id) => state.drafts[id]).filter((d): d is Draft => !!d);
}

export interface ShownMessage extends MessageRecord {
  receipts: ReceiptFacts;
}

export function messagesOf(state: QueueState, thread: string): ShownMessage[] {
  return (state.items[thread]?.message_ids ?? []).map((id) => ({
    ...state.messages[id], receipts: state.receipts[id],
  }));
}

export function draftsOf(state: QueueState, thread: string): Draft[] {
  return (state.items[thread]?.draft_ids ?? []).map((id) => state.drafts[id]).filter((d): d is Draft => !!d);
}

/** A message's direct replies: accepted messages naming it as their exact
 *  parent. A related link is not one. */
export function repliesTo(state: QueueState, messageId: string): AcceptedMessage[] {
  return Object.values(state.messages).map((m) => m.message)
    .filter((m) => m.in_reply_to_message_id === messageId)
    .sort(messageOrder);
}

/** The thread a message belongs to, when this JARVIS has it. */
export function threadOf(state: QueueState, messageId: string): string | null {
  return state.messages[messageId]?.message.thread_id ?? null;
}

export function isSelf(work: WorkState): boolean {
  return work.correspondent_github_id === work.owner_github_id;
}

/** Whose turn: the owner-local action field only, never colour or reads.
 *  A message to oneself is always the owner's turn. */
export function turn(work: WorkState): "mine" | "theirs" {
  return isSelf(work) || work.action_owner_github_id === work.owner_github_id ? "mine" : "theirs";
}

export type Delivery = "awaiting" | "received" | "read";

/** How far an outgoing message got: the recipient's facts only. For a
 *  message to oneself the owner's own read is their personal cue (Read by
 *  you), shown apart, so here it goes no further than received. */
export function delivery(message: AcceptedMessage, receipts: ReceiptFacts | undefined): Delivery {
  const self = message.sender_github_id === message.recipient_github_id;
  return receipts?.read_at && !self ? "read"
    : receipts?.received_at || receipts?.read_at ? "received" : "awaiting";
}

const UNRESOLVED: SendState[] = ["awaiting_approval", "approved", "submitting", "accept_unknown", "failed"];

export interface ItemCues {
  turn: "mine" | "theirs";
  unread: boolean;
  /** The owner's own read of the latest message to them. */
  readByYou: boolean;
  addressed: boolean;
  /** An accepted reply of the owner's names the latest message to them. */
  replied: boolean;
  files: number;
  related: boolean;
  /** The latest message the owner sent, as far as it got. */
  delivery: Delivery | null;
  /** A response of the owner's on its way out and not yet accepted. */
  sending: Draft | null;
  dismissed: boolean;
}

export function cues(state: QueueState, thread: string): ItemCues | null {
  const item = state.items[thread];
  if (!item) return null;
  const work = item.work_state;
  const owner = work.owner_github_id;
  const messages = messagesOf(state, thread);
  const toMe = messages.filter((m) => m.message.recipient_github_id === owner);
  const fromMe = messages.filter((m) => m.message.sender_github_id === owner);
  const latestToMe = toMe[toMe.length - 1];
  const latestFromMe = fromMe[fromMe.length - 1];
  const sending = draftsOf(state, thread).find((d) => UNRESOLVED.includes(d.send.state)) ?? null;
  return {
    turn: turn(work),
    unread: work.unread_reminder,
    readByYou: !work.unread_reminder && !!latestToMe?.receipts?.read_at,
    addressed: work.addressed_at !== null,
    replied: !!latestToMe && fromMe.some((m) => m.message.in_reply_to_message_id === latestToMe.message.message_id),
    files: messages.reduce((n, m) => n + m.message.attachments.length, 0),
    related: messages.some((m) => m.message.related_message_ids.length > 0),
    delivery: latestFromMe ? delivery(latestFromMe.message, latestFromMe.receipts) : null,
    sending,
    dismissed: work.placement === "inactive",
  };
}

/** The correspondent's latest message on the item, if they have sent one:
 *  it says which of their JARVIS the item came through. */
export function latestFrom(state: QueueState, thread: string, githubId: number): ShownMessage | undefined {
  return messagesOf(state, thread).filter((m) => m.message.sender_github_id === githubId).pop();
}

/** The item's subject: its first message's, else its newest draft's. */
export function subjectOf(state: QueueState, thread: string): string {
  const first = messagesOf(state, thread)[0]?.message ?? state.drafts[thread];
  return first?.subject ?? "";
}

/** The first line of the latest message's text, or its file count. */
export function previewOf(message: { text: string; attachments: unknown[] } | undefined): string {
  if (!message) return "";
  const line = message.text.split("\n").find((l) => l.trim()) ?? "";
  if (line) return line;
  const n = message.attachments.length;
  return n ? `${n} file${n === 1 ? "" : "s"}` : "";
}

/** Something waiting that the owner has not read: the Queue tab's cue. */
export function attention(state: QueueState): boolean {
  return waiting(state).some((i) => i.work_state.unread_reminder);
}

/** A send in words. While an approved revision's files are on their way,
 *  that is what it says (Preparing, Uploading); nothing claims acceptance
 *  before the team server answered. */
export function sendWords(draft: Draft): string {
  const s = draft.send.state;
  if (s === "approved" && draft.files.some((f) => f.state === "uploading")) return "Uploading files";
  if (s === "approved" && draft.files.some((f) => f.state === "staged")) return "Preparing files";
  return SEND_WORDS[s];
}

const SEND_WORDS: Record<SendState, string> = {
  draft: "Draft, not sent",
  awaiting_approval: "Awaiting your approval",
  approved: "Approved, about to send",
  submitting: "Sending to the team server",
  accept_unknown: "The team server's answer was lost",
  accepted: "Accepted by the team server",
  failed: "Not sent",
  cancelled: "Cancelled, not sent",
  superseded: "Replaced by a later edit",
};

/** "0 bytes", "812 bytes", "47.1 KB", "3.2 MB". */
export function sizeText(bytes: number): string {
  if (bytes < 1024) return `${bytes} byte${bytes === 1 ? "" : "s"}`;
  const [n, unit] = bytes < 1024 ** 2 ? [bytes / 1024, "KB"] : bytes < 1024 ** 3
    ? [bytes / 1024 ** 2, "MB"] : [bytes / 1024 ** 3, "GB"];
  return `${n.toFixed(1)} ${unit}`;
}

// ── Who and which JARVIS ─────────────────────────────────────────────────

export interface DirectoryNode {
  node_id: string;
  owner_github_id: number;
  owner_login: string;
  label: string;
}

/** The existing roster and the network's nodes: the only identities shown.
 *  Display names are presentation; ids decide. */
export interface Directory {
  owner: number | null;
  members: TeamMember[];
  nodes: DirectoryNode[];
  /** This JARVIS's own node. */
  selfNodeId: string | null;
}

export const UNKNOWN_PERSON = "A member no longer listed";
export const UNKNOWN_JARVIS = "a JARVIS no longer listed";

/** "Me" for the owner; the member's name, or their login; a safe fallback
 *  for someone the roster and network no longer list. */
export function personName(dir: Directory, githubId: number): string {
  if (githubId === dir.owner) return "Me";
  const member = dir.members.find((m) => m.github_id === githubId);
  if (member) return member.name || member.login;
  return dir.nodes.find((n) => n.owner_github_id === githubId)?.owner_login ?? UNKNOWN_PERSON;
}

/** The GitHub handle, when the roster or network knows it. */
export function handle(dir: Directory, githubId: number): string | null {
  const login = dir.members.find((m) => m.github_id === githubId)?.login
    ?? dir.nodes.find((n) => n.owner_github_id === githubId)?.owner_login;
  return login ? `@${login}` : null;
}

export function jarvisLabel(dir: Directory, nodeId: string): string {
  return dir.nodes.find((n) => n.node_id === nodeId)?.label ?? UNKNOWN_JARVIS;
}

/** "Your turn" or "Matt’s turn". */
export function turnWords(dir: Directory, work: WorkState): string {
  return turn(work) === "mine" ? "Your turn" : `${personName(dir, work.correspondent_github_id)}’s turn`;
}

export const DELIVERY_WORDS = (dir: Directory, recipient: number): Record<Delivery, string> => (
  recipient === dir.owner
    ? { awaiting: "Awaiting receipt", received: "Received by your JARVIS", read: "Received by your JARVIS" }
    : { awaiting: "Awaiting receipt", received: "Received by their JARVIS",
        read: `Read by ${personName(dir, recipient)}` });

/** A recipient as the picker names them: the person, and the JARVIS that
 *  holds their inbox ("Me · Tim’s i9", "Matt · Matt’s Mac"). An offline
 *  JARVIS still names its owner; the inbox is the person's. */
export function recipientLabel(dir: Directory, githubId: number): string {
  if (githubId === dir.owner) {
    const self = dir.nodes.find((n) => n.node_id === dir.selfNodeId);
    return `Me · ${self?.label ?? "this JARVIS"}`;
  }
  const labels = dir.nodes.filter((n) => n.owner_github_id === githubId).map((n) => n.label);
  return `${personName(dir, githubId)} · ${labels.length ? labels.join(", ") : "their JARVIS"}`;
}
