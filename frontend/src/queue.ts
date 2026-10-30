/**
 * The Queue, live: reading JARVIS's messaging routes into the one record
 * (queueStore.ts), the owner's commands on their items, the item open in
 * the centre, inline drafts and the read receipts the page owes. Views read
 * these stores; nothing here draws.
 *
 *   - The snapshot is read at once, on every "messages" hint and every
 *     (re)connect snapshot on /ws/sessions (`messagesChanged`, moved by
 *     sessionsSocket.ts), and on every reconnect of the voice socket
 *     (liveRead). A failed read keeps the last record and says so; it never
 *     empties the queue. Repeats change nothing.
 *   - Which item is open, and the way back from a linked one, is this page's
 *     own navigation (`openQueue`); placement, the addressed cue, dismissal
 *     and whose turn are the owner's state on JARVIS, changed by commands
 *     carrying the revision they were made against.
 *   - One command per item is on its way at a time: a second click on the
 *     same item waits for the first, and a refusal undoes only what that
 *     command showed.
 *   - A draft's editor keeps its own text here, saved after a pause and at
 *     once before the item is left, put back or reviewed; a failed save
 *     keeps the text and says so.
 *   - A read receipt is sent for a message only once its body has been on
 *     screen (`bodyShown`); selecting an item or reading its preview sends
 *     none.
 */

import { captures } from "./capture";
import { liveRead, type LiveStore } from "./liveRead";
import { messagingApi, MessagingError } from "./messagingApi";
import { LIMITS, type Draft, type DraftSave, type RecipientList, type WorkCommand } from "./messagingContract";
import { network } from "./network";
import {
  applyDraft, applyFile, applyHistory, applyItem, applyMessage, applySnapshot, applyWorkState,
  EMPTY, removeDraft, waiting, withPending, type Directory, type QueueState,
} from "./queueStore";
import { createStore } from "./store";

/** A refusal in the contract's words. */
const asError = (e: unknown) => (e instanceof MessagingError ? e : new MessagingError("transport", 0, String(e)));
const shown = (e: unknown) => asError(e).shown;

/** Moves on every "messages" hint on /ws/sessions, and on its every
 *  (re)connect (sessionsSocket.ts). */
export const messagesChanged = createStore(0);

/** What JARVIS has said, without commands on their way. */
export const queueRecord = createStore<QueueState>(EMPTY);

/** The commands on their way, by thread. */
export const pendingCommands = createStore<Record<string, WorkCommand>>({});

/** What the page shows: the record with each command on its way. */
export const queue = createStore<QueueState>(EMPTY);
const show = () => queue.set(withPending(queueRecord.get(), pendingCommands.get()));
queueRecord.subscribe(show);
pendingCommands.subscribe(show);

const record = (change: (s: QueueState) => QueueState) => queueRecord.set(change(queueRecord.get()));

/** Why the last read of the queue failed, until one succeeds. */
export const queueReadError = createStore<string | null>(null);

const snapshotRead = liveRead(() => messagingApi().snapshot(), [messagesChanged]);
let applied: unknown = null;
snapshotRead.subscribe(() => {
  const live = snapshotRead.get();
  queueReadError.set(live.error);
  if (live.error === null && live !== applied) {
    applied = live;
    record((s) => applySnapshot(s, live.value));
  }
});

/** Read the queue again now. */
export function refreshQueue(): void {
  snapshotRead.refresh();
}

// ── Who and which JARVIS ─────────────────────────────────────────────────

// The roster, read once messaging is available (and on every reconnect).
let rosterRead: LiveStore<RecipientList> | null = null;
export const roster = createStore<RecipientList | null>(null);
queueRecord.subscribe(() => {
  if (rosterRead || queueRecord.get().availability !== "available") return;
  rosterRead = liveRead(() => messagingApi().recipients());
  rosterRead.subscribe(() => { if (rosterRead!.get().value) roster.set(rosterRead!.get().value); });
});

/** The roster, the network's nodes and the owner, as one directory. */
export const directory = createStore<Directory>({ owner: null, members: [], nodes: [], selfNodeId: null });
const rebuildDirectory = () => {
  const snapshot = network.get().value?.snapshot;
  directory.set({
    owner: queueRecord.get().owner_github_id,
    members: roster.get()?.members ?? [],
    nodes: snapshot?.nodes.map(({ node_id, owner_github_id, owner_login, label }) =>
      ({ node_id, owner_github_id, owner_login, label })) ?? [],
    selfNodeId: snapshot?.you_node_id ?? null,
  });
};
let directoryInputs: unknown[] = [];
const maybeRebuild = () => {
  const inputs = [queueRecord.get().owner_github_id, roster.get(), network.get().value?.snapshot];
  if (inputs.some((v, i) => v !== directoryInputs[i])) {
    directoryInputs = inputs;
    rebuildDirectory();
  }
};
queueRecord.subscribe(maybeRebuild);
roster.subscribe(maybeRebuild);
network.subscribe(maybeRebuild);

// ── The item open in the centre ──────────────────────────────────────────

/** The item open in the centre ("new": a new conversation being written),
 *  and the items to go back to after following links. */
export interface QueueOpen {
  thread: string;
  back: string[];
}
export const NEW = "new";
export const openQueue = createStore<QueueOpen | null>(null);

/** Something to tell the owner about one item (a refusal, a kept command),
 *  by thread. */
export const itemNotes = createStore<Record<string, string>>({});
function note(thread: string, words: string | null): void {
  const notes = { ...itemNotes.get() };
  if (words) notes[thread] = words; else delete notes[thread];
  itemNotes.set(notes);
}

/** The item a row stands for, opened from the Queue: a waiting one becomes
 *  Active. The item left keeps its place and its draft, saved now. */
export function openItem(thread: string): void {
  leaveCurrent();
  openQueue.set({ thread, back: [] });
  const item = queueRecord.get().items[thread];
  if (item?.work_state.placement === "queued") {
    void command(thread, { command: "open", expected_revision: item.work_state.revision });
  }
}

/** Follow a link (a parent, a reply, a related message) without changing
 *  the linked item's place: a dismissed one stays dismissed. The current
 *  one stays where it is, and Back returns to it. */
export function followLink(thread: string): void {
  const now = openQueue.get();
  leaveCurrent();
  openQueue.set({ thread, back: now && now.thread !== thread ? [...now.back, now.thread] : now?.back ?? [] });
}

export function goBack(): void {
  const now = openQueue.get();
  if (!now?.back.length) return;
  leaveCurrent();
  openQueue.set({ thread: now.back[now.back.length - 1], back: now.back.slice(0, -1) });
}

/** Open the next waiting item; the current one stays in Active. False when
 *  nothing is waiting (the current view stays). */
export function nextItem(): boolean {
  const current = openQueue.get()?.thread;
  const next = waiting(queue.get()).find((i) => i.work_state.thread_id !== current);
  if (!next) return false;
  openItem(next.work_state.thread_id);
  return true;
}

export function closeItem(): void {
  leaveCurrent();
  openQueue.set(null);
}

/** Start writing a new conversation. */
export function startNew(): void {
  leaveCurrent();
  if (!editors.get()[NEW]) setEditor(NEW, blankEditor({ recipient: null, thread: null, parent: null, subject: "" }));
  openQueue.set({ thread: NEW, back: [] });
}

function leaveCurrent(): void {
  const now = openQueue.get();
  if (now) void flush(now.thread);
}

// ── Commands ─────────────────────────────────────────────────────────────

/** Send one command on an item. Resolves true once JARVIS has it (kept for
 *  the team server, if it could not be reached). */
async function command(thread: string, change: WorkCommand): Promise<boolean> {
  if (pendingCommands.get()[thread]) return false;
  pendingCommands.set({ ...pendingCommands.get(), [thread]: change });
  note(thread, null);
  try {
    const result = await messagingApi().workCommand(thread, change);
    record((s) => applyWorkState(s, result.work_state));
    if (result.pending) note(thread, "Kept on this JARVIS until the team server answers.");
    return true;
  } catch (e) {
    const err = asError(e);
    if (err.code === "revision_conflict" && err.latest) {
      record((s) => applyWorkState(s, err.latest as QueueState["items"][string]["work_state"]));
    }
    note(thread, err.shown);
    return false;
  } finally {
    const pending = { ...pendingCommands.get() };
    delete pending[thread];
    pendingCommands.set(pending);
  }
}

const revisionOf = (thread: string) => queueRecord.get().items[thread]?.work_state.revision;

function on(thread: string, change: (expected: number) => WorkCommand): Promise<boolean> {
  const expected = revisionOf(thread);
  return expected === undefined ? Promise.resolve(false) : command(thread, change(expected));
}

export const setAddressed = (thread: string, addressed: boolean) =>
  on(thread, (r) => ({ command: "set_addressed", addressed, expected_revision: r }));

export const setUnread = (thread: string, unread: boolean) =>
  on(thread, (r) => ({ command: "set_unread_reminder", unread, expected_revision: r }));

/** Move to my side / Waiting on <correspondent>: the owner's cue only. */
export const setTurn = (thread: string, githubId: number) =>
  on(thread, (r) => ({ command: "set_action_owner", action_owner_github_id: githubId, expected_revision: r }));

export const restore = (thread: string) => on(thread, (r) => ({ command: "restore", expected_revision: r }));

/** Put back in the queue, at its original place. Its draft is saved first;
 *  if that fails the item stays open and says why. */
export async function putBack(thread: string): Promise<boolean> {
  if (!(await flush(thread))) return false;
  if (!(await on(thread, (r) => ({ command: "put_back", expected_revision: r })))) return false;
  if (openQueue.get()?.thread === thread) openQueue.set(null);
  return true;
}

/** Done: dismissed, once nothing on it would be lost. The caller has shown
 *  `blockers` and offered their ways out first. */
export async function dismiss(thread: string): Promise<boolean> {
  if (!(await on(thread, (r) => ({ command: "dismiss", expected_revision: r })))) return false;
  if (openQueue.get()?.thread === thread) openQueue.set(null);
  return true;
}

// ── Dismissed items ──────────────────────────────────────────────────────

export const historyError = createStore<string | null>(null);

/** Read the first page of dismissed items, or the next one. Never more
 *  than a page at a time. */
export async function readHistory(first: boolean): Promise<void> {
  const from = first ? null : queueRecord.get().history_next;
  if (!first && !from) return;
  try {
    const page = await messagingApi().history(from, LIMITS.HISTORY_PAGE_DEFAULT);
    historyError.set(null);
    record((s) => applyHistory(s, page, first));
  } catch (e) {
    historyError.set(shown(e));
  }
}

/** Read one item in full (opening it, following a link, or on a hint while
 *  it is open). */
export async function readItem(thread: string): Promise<void> {
  const item = await messagingApi().item(thread);
  record((s) => applyItem(s, item));
}

// A review's capture closing (sent, deleted, or replaced by an edit) means
// its draft moved on: read the queue and that item again, without waiting
// for the hint.
let reviewCaptures = new Set<number>();
const watchReviews = () => {
  const open = new Set(captures.get().map((c) => c.id));
  const drafts = Object.values(queueRecord.get().drafts);
  for (const id of reviewCaptures) {
    if (open.has(id)) continue;
    const draft = drafts.find((d) => d.capture_id === id);
    refreshQueue();
    if (draft && queueRecord.get().items[draft.thread_id]) readItem(draft.thread_id).catch(() => {});
  }
  reviewCaptures = new Set([...open].filter((id) => drafts.some((d) => d.capture_id === id)));
};
captures.subscribe(watchReviews);
queueRecord.subscribe(watchReviews);

// ── Read receipts ────────────────────────────────────────────────────────

const readAsked = new Set<string>();


/** The body of a message to the owner has been on screen: send its read
 *  receipt, once. A failed send is tried again the next time it shows. The
 *  personal Unread reminder is cleared once the newest message to the owner
 *  on the item has been read. */
export async function bodyShown(messageId: string): Promise<void> {
  const state = queueRecord.get();
  const m = state.messages[messageId];
  if (!m || m.message.recipient_github_id !== state.owner_github_id) return;
  if (state.receipts[messageId]?.read_at || readAsked.has(messageId)) return;
  readAsked.add(messageId);
  try {
    const read = await messagingApi().read(messageId);
    record((s) => applyMessage(s, read));
  } catch {
    readAsked.delete(messageId);
    return;
  }
  const after = queueRecord.get();
  const item = after.items[m.message.thread_id];
  const newestToMe = item?.message_ids.filter(
    (id) => after.messages[id].message.recipient_github_id === after.owner_github_id).pop();
  if (item?.work_state.unread_reminder && newestToMe === messageId) {
    void setUnread(m.message.thread_id, false);
  }
}

// ── Inline drafts ────────────────────────────────────────────────────────

/** A response or new conversation as the owner is writing it. The text is
 *  the editor's own; `savedRevision` is the draft revision it was last
 *  saved as (null until the draft exists on JARVIS). */
export interface Editor {
  messageId: string | null;
  recipient: number | null;
  /** The item it answers; null for a new conversation. */
  thread: string | null;
  /** The exact message it replies to; never worked out from quoted text. */
  parent: string | null;
  related: string[];
  subject: string;
  text: string;
  attachmentIds: string[];
  savedRevision: number | null;
  dirty: boolean;
  saving: boolean;
  /** Files being copied to JARVIS now. */
  adding: number;
  error: string | null;
  /** The draft as it is on JARVIS, after it changed under this editor. */
  conflict: Draft | null;
}

/** By the key the centre opens it under: the item's thread id, or NEW. */
export const editors = createStore<Record<string, Editor>>({});

function setEditor(key: string, editor: Editor | null): void {
  const all = { ...editors.get() };
  if (editor) all[key] = editor; else delete all[key];
  editors.set(all);
}

function blankEditor(fields: Pick<Editor, "recipient" | "thread" | "parent" | "subject">): Editor {
  return { ...fields, messageId: null, related: [], text: "", attachmentIds: [], savedRevision: null,
           dirty: false, saving: false, adding: 0, error: null, conflict: null };
}

function fromDraft(d: Draft): Editor {
  return {
    messageId: d.message_id, recipient: d.recipient_github_id,
    thread: d.thread_id === d.message_id ? null : d.thread_id,
    parent: d.in_reply_to_message_id, related: d.related_message_ids, subject: d.subject,
    text: d.text, attachmentIds: d.attachments.map((a) => a.attachment_id),
    savedRevision: d.draft_revision, dirty: false, saving: false, adding: 0, error: null, conflict: null,
  };
}

const EDITABLE = new Set(["draft", "awaiting_approval"]);

/** The item's one draft the owner can still change, if it has one. */
export function editableDraft(state: QueueState, thread: string): Draft | null {
  if (state.drafts[thread] && EDITABLE.has(state.drafts[thread].send.state)) return state.drafts[thread];
  return (state.items[thread]?.draft_ids ?? []).map((id) => state.drafts[id])
    .find((d) => d && EDITABLE.has(d.send.state)) ?? null;
}

/** Respond on an item, to one exact message of it. An unsent response
 *  already there is taken up again, as it is. */
export function startReply(thread: string, parent: string, recipient: number, subject: string): void {
  if (editors.get()[thread]) return;
  const draft = editableDraft(queueRecord.get(), thread);
  setEditor(thread, draft ? fromDraft(draft) : blankEditor({ recipient, thread, parent, subject }));
}

/** An item opened with an unsent draft on it shows that draft's editor. */
export function resumeDraft(thread: string): void {
  if (editors.get()[thread]) return;
  const draft = editableDraft(queueRecord.get(), thread);
  if (draft) setEditor(thread, fromDraft(draft));
}

// An editor with nothing unsaved follows its draft when it changes on
// JARVIS (an agent's edit, a review from voice); one with unsaved text
// keeps it and finds out on its next save.
queueRecord.subscribe(() => {
  const state = queueRecord.get();
  for (const [key, ed] of Object.entries(editors.get())) {
    const d = ed.messageId ? state.drafts[ed.messageId] : null;
    if (d && !ed.dirty && !ed.saving && ed.savedRevision !== null && d.draft_revision > ed.savedRevision) {
      setEditor(key, { ...fromDraft(d), adding: ed.adding });
    }
  }
});

const SAVE_AFTER_MS = 800;
const timers = new Map<string, ReturnType<typeof setTimeout>>();
const saving = new Map<string, Promise<boolean>>();

/** The owner changed the editor: saved after a pause. */
export function edit(key: string, change: Partial<Pick<Editor, "text" | "subject" | "recipient">>): void {
  const ed = editors.get()[key];
  if (!ed) return;
  setEditor(key, { ...ed, ...change, dirty: true, error: null });
  clearTimeout(timers.get(key));
  timers.set(key, setTimeout(() => { timers.delete(key); void save(key); }, SAVE_AFTER_MS));
}

/** Save now whatever is unsaved. True when nothing is left unsaved. */
export function flush(key: string): Promise<boolean> {
  clearTimeout(timers.get(key));
  timers.delete(key);
  return save(key);
}

function hasContent(ed: Editor): boolean {
  return !!(ed.text.trim() || ed.subject.trim() || ed.attachmentIds.length);
}

async function save(key: string): Promise<boolean> {
  const inFlight = saving.get(key);
  if (inFlight) {
    await inFlight;
    return save(key);
  }
  const ed = editors.get()[key];
  if (!ed || !ed.dirty) return true;
  if (!ed.messageId && !hasContent(ed)) return true;          // nothing to keep yet
  if (ed.recipient === null) {
    setEditor(key, { ...ed, error: "Choose who it is for, and it will be kept as a draft." });
    return false;
  }
  const body: DraftSave = {
    expected_draft_revision: ed.savedRevision, thread_id: ed.thread,
    in_reply_to_message_id: ed.parent, related_message_ids: ed.related,
    recipient_github_id: ed.recipient, subject: ed.subject, text: ed.text,
    attachment_ids: ed.attachmentIds,
  };
  setEditor(key, { ...ed, saving: true });
  const done = (async () => {
    try {
      const api = messagingApi();
      const draft = ed.messageId ? await api.saveDraft(ed.messageId, body) : await api.createDraft(body);
      record((s) => applyDraft(s, draft));
      const now = editors.get()[key] ?? ed;
      const changed = now.text !== body.text || now.subject !== body.subject || now.recipient !== body.recipient_github_id;
      const saved: Editor = {
        ...now, messageId: draft.message_id, savedRevision: draft.draft_revision,
        attachmentIds: draft.attachments.map((a) => a.attachment_id), related: draft.related_message_ids,
        dirty: changed, saving: false, error: null, conflict: null,
      };
      // A new conversation is an item of its own from its first save.
      const newKey = key === NEW ? draft.thread_id : key;
      if (newKey !== key) {
        setEditor(key, null);
        if (openQueue.get()?.thread === key) openQueue.set({ thread: newKey, back: openQueue.get()!.back });
      }
      setEditor(newKey, saved);
      if (changed) edit(newKey, {});
      return !changed;
    } catch (e) {
      const err = asError(e);
      const now = editors.get()[key] ?? ed;
      if (err.code === "revision_conflict" && err.latest) {
        const latest = err.latest as Draft;
        record((s) => applyDraft(s, latest));
        setEditor(key, { ...now, saving: false, conflict: latest,
                         error: "This draft was changed elsewhere. Your text is kept here." });
      } else {
        setEditor(key, { ...now, saving: false, error: `Not saved: ${err.shown} Your text is kept here.` });
      }
      return false;
    } finally {
      saving.delete(key);
    }
  })();
  saving.set(key, done);
  return done;
}

/** After a conflict: keep the editor's text over the draft's. */
export function keepMine(key: string): void {
  const ed = editors.get()[key];
  if (!ed?.conflict) return;
  setEditor(key, { ...ed, savedRevision: ed.conflict.draft_revision, conflict: null });
  void flush(key);
}

/** After a conflict: take the draft as it is on JARVIS. */
export function takeTheirs(key: string): void {
  const ed = editors.get()[key];
  if (!ed?.conflict) return;
  setEditor(key, { ...fromDraft(ed.conflict), adding: ed.adding });
}

const MB = 1024 * 1024;

/** Copy chosen files to JARVIS privately and add them to the draft, in the
 *  order chosen. The limits are the contract's. */
export async function addFiles(key: string, files: File[]): Promise<void> {
  let ed = editors.get()[key];
  if (!ed || !files.length) return;
  const known = ed.messageId ? queueRecord.get().drafts[ed.messageId]?.attachments ?? [] : [];
  const total = known.reduce((n, a) => n + a.size_bytes, 0) + files.reduce((n, f) => n + f.size, 0);
  const tooBig = files.find((f) => f.size > LIMITS.FILE_MAX_BYTES);
  const refusal = ed.attachmentIds.length + files.length > LIMITS.ATTACHMENTS_MAX
    ? `At most ${LIMITS.ATTACHMENTS_MAX} files.`
    : tooBig ? `${tooBig.name} is larger than ${LIMITS.FILE_MAX_BYTES / MB} MB.`
    : total > LIMITS.PACKAGE_MAX_BYTES ? `The files come to more than ${LIMITS.PACKAGE_MAX_BYTES / MB} MB.`
    : null;
  if (refusal) { setEditor(key, { ...ed, error: refusal }); return; }
  if (!ed.messageId) {
    setEditor(key, { ...ed, dirty: true });
    if (!(await flush(key))) return;
    key = key === NEW ? openQueue.get()?.thread ?? key : key;
    ed = editors.get()[key];
    if (!ed?.messageId) return;
  }
  const messageId = ed.messageId!;
  setEditor(key, { ...ed, adding: ed.adding + files.length, error: null });
  const added: string[] = [];
  let failure: string | null = null;
  for (const file of files) {
    try {
      added.push((await messagingApi().addFile(messageId, file)).attachment_id);
    } catch (e) {
      failure = `${file.name} was not added: ${shown(e)}`;
    }
  }
  const now = editors.get()[key];
  if (!now) return;
  setEditor(key, { ...now, adding: Math.max(0, now.adding - files.length),
                   attachmentIds: [...now.attachmentIds, ...added], dirty: true, error: failure });
  await flush(key);
}

/** Take a file off the draft (its private copy stays until the draft goes). */
export function removeFile(key: string, attachmentId: string): void {
  const ed = editors.get()[key];
  if (!ed) return;
  setEditor(key, { ...ed, attachmentIds: ed.attachmentIds.filter((a) => a !== attachmentId), dirty: true });
  void flush(key);
}

async function onDraft(key: string, act: (messageId: string, revision: number) => Promise<Draft>): Promise<void> {
  const ed = editors.get()[key];
  const draft = ed?.messageId ? queueRecord.get().drafts[ed.messageId] : null;
  if (!ed || !draft) return;
  try {
    const after = await act(draft.message_id, draft.draft_revision);
    record((s) => applyDraft(s, after));
    const now = editors.get()[key];
    if (now) setEditor(key, { ...now, savedRevision: after.draft_revision, error: null });
  } catch (e) {
    const now = editors.get()[key] ?? ed;
    setEditor(key, { ...now, error: shown(e) });
  }
}

/** Freeze this revision for review: JARVIS opens its capture, whose Send is
 *  the owner's approval. Saved first; nothing is reviewed unsaved. */
export async function review(key: string): Promise<void> {
  if (!(await flush(key))) return;
  await onDraft(key, (m, r) => messagingApi().review(m, r));
}

/** Retry an unresolved send of a draft on an item (the same approved
 *  revision and the same identity: never a new send). */
export async function retrySend(draft: Draft): Promise<void> {
  try {
    record((s) => applyDraft(s, draft));
    const after = await messagingApi().retry(draft.message_id, draft.draft_revision);
    record((s) => applyDraft(s, after));
  } catch (e) {
    note(draft.thread_id, shown(e));
  }
}

/** Cancel a send that has not gone yet. */
export async function cancelSend(draft: Draft): Promise<void> {
  try {
    const after = await messagingApi().cancel(draft.message_id, draft.draft_revision);
    record((s) => applyDraft(s, after));
  } catch (e) {
    note(draft.thread_id, shown(e));
  }
}

/** Throw the unsent draft away, on the owner's explicit word. */
export async function discard(key: string): Promise<boolean> {
  clearTimeout(timers.get(key));
  const ed = editors.get()[key];
  const draft = ed?.messageId ? queueRecord.get().drafts[ed.messageId]
    : editableDraft(queueRecord.get(), key);
  if (draft) {
    try {
      await messagingApi().discardDraft(draft.message_id, draft.draft_revision);
    } catch (e) {
      if (ed) setEditor(key, { ...ed, error: shown(e) });
      else note(key, shown(e));
      return false;
    }
    record((s) => removeDraft(s, draft.message_id));
  }
  setEditor(key, null);
  if (key === NEW || draft?.thread_id === draft?.message_id) {
    if (openQueue.get()?.thread === key) openQueue.set(null);
  }
  return true;
}

/** Why the item cannot be dismissed yet: JARVIS's reasons, and this page's
 *  own (text typed and not yet saved, files still being added). */
export function blockersOf(state: QueueState, thread: string) {
  const ed = editors.get()[thread];
  const all = new Set(state.items[thread]?.dismiss_blockers ?? []);
  if (ed?.dirty && hasContent(ed)) all.add("unsent_text");
  if (ed?.adding) all.add("upload_pending");
  return [...all];
}

/** Change a send that failed or was cancelled: its editor again, and the
 *  next save makes a new revision (`revise` on JARVIS). */
export function editAgain(key: string, draft: Draft): void {
  setEditor(key, fromDraft(draft));
}

// ── Files on received messages ───────────────────────────────────────────

/** Fetch a received file to this JARVIS and check it. JARVIS never opens
 *  it; the owner's one action on it is Reveal in Finder. */
export async function downloadFile(messageId: string, attachmentId: string): Promise<void> {
  const thread = queueRecord.get().messages[messageId]?.message.thread_id;
  try {
    const file = await messagingApi().download(messageId, attachmentId);
    record((s) => applyFile(s, messageId, file));
  } catch (e) {
    if (thread) note(thread, shown(e));
  }
}

export async function revealFile(messageId: string, attachmentId: string): Promise<void> {
  const thread = queueRecord.get().messages[messageId]?.message.thread_id;
  try {
    await messagingApi().reveal(messageId, attachmentId);
  } catch (e) {
    if (thread) note(thread, shown(e));
  }
}
