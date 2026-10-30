/**
 * One item, in the centre: the work surface for the conversation open now.
 * One view however many messages there are; opening another item replaces
 * it, and the one left stays in Active / Outbox with its draft.
 *
 *   - The head: the subject, who it is with (their name, GitHub handle and
 *     the JARVIS it came through), whose turn it is (with the owner's
 *     correction: Move to my side / Waiting on …), and the item's actions:
 *     Respond, Next item, Put back, Mark addressed, Mark unread, Done
 *     (Restore for a dismissed one), Close; Back after following a link.
 *   - The exchange, oldest first: each message with its sender, time,
 *     subject and text (as text), its files, what it replies to, its direct
 *     replies and related messages, and for the owner's own how far it got.
 *     The owner's responses on their way out follow, each with its state
 *     and its ways out (Retry, Cancel, Edit), then the response being
 *     written and its inline review.
 *
 * A message's read receipt is sent once its body has been on screen, never
 * on selection. Nothing arriving changes this view: not its scroll, not the
 * text being written, not the review.
 */

import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import { shownFor } from "./messagingApi";
import { DISMISS_BLOCKERS_SHOWN, type Draft } from "./messagingContract";
import { MessageComposer } from "./MessageComposer";
import { MessageReview } from "./MessageReview";
import {
  blockersOf, bodyShown, cancelSend, closeItem, directory, discard, dismiss, editAgain, editors,
  editableDraft, followLink, goBack, itemNotes, messagesChanged, NEW, nextItem, openQueue,
  pendingCommands, putBack, queue, readItem, restore, resumeDraft, retrySend, setAddressed,
  setTurn, setUnread, startReply,
} from "./queue";
import { FileCard } from "./QueueFiles";
import { Cue, DELIVERY_CUE } from "./QueueIcons";
import {
  cues, DELIVERY_WORDS, delivery, draftsOf, handle, isSelf, jarvisLabel, latestFrom, messagesOf, personName,
  previewOf, recipientLabel, repliesTo, sendWords, subjectOf, threadOf, turnWords,
  type Directory, type QueueState, type ShownMessage,
} from "./queueStore";
import { clockTime } from "./network";

export function QueueItemView({ thread }: { thread: string }) {
  const state = useSyncExternalStore(queue.subscribe, queue.get);
  // A new conversation keeps one view from its first word to its
  // acceptance, through its first save (when it gets its thread id), so
  // the text being written never loses its place.
  if (thread === NEW || (!state.items[thread] && state.drafts[thread])) {
    return <UnsentConversation thread={thread} />;
  }
  return <OneItem key={thread} thread={thread} />;
}

/** The title of the centre's tab for the open item. */
export function queueViewTitle(state: QueueState, dir: Directory, thread: string): string {
  if (thread === NEW) return "New message";
  const subject = subjectOf(state, thread);
  if (subject) return subject;
  const work = state.items[thread]?.work_state;
  return work ? personName(dir, work.correspondent_github_id) : "Message";
}

function OneItem({ thread }: { thread: string }) {
  const state = useSyncExternalStore(queue.subscribe, queue.get);
  const dir = useSyncExternalStore(directory.subscribe, directory.get);
  const open = useSyncExternalStore(openQueue.subscribe, openQueue.get);
  const pending = useSyncExternalStore(pendingCommands.subscribe, pendingCommands.get)[thread];
  const notes = useSyncExternalStore(itemNotes.subscribe, itemNotes.get);
  const ed = useSyncExternalStore(editors.subscribe, editors.get)[thread];
  const hint = useSyncExternalStore(messagesChanged.subscribe, messagesChanged.get);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [noneWaiting, setNoneWaiting] = useState(false);
  const [dismissing, setDismissing] = useState(false);

  // Read the item in full on opening it and on every hint while it is open.
  useEffect(() => {
    readItem(thread).then(() => setLoadError(null), (e: Error) => setLoadError(e.message || "no answer"));
  }, [thread, hint]);
  useEffect(() => resumeDraft(thread), [thread, state.drafts]);

  const item = state.items[thread];
  if (!item) {
    return (
      <div id="queue-item-view" className="queue-item-view">
        <p className="read-failure" role="status">
          {loadError ? `Couldn't read this item (${loadError}); trying again on the next change.` : "Reading…"}
        </p>
        <div className="item-actions"><button type="button" onClick={closeItem}>Close</button></div>
      </div>
    );
  }

  const work = item.work_state;
  const c = cues(state, thread)!;
  const messages = messagesOf(state, thread);
  const correspondent = work.correspondent_github_id;
  const latestTheirs = latestFrom(state, thread, correspondent);
  const parentForReply = (latestTheirs ?? messages[messages.length - 1])?.message;
  const editable = editableDraft(state, thread);
  const outgoing = draftsOf(state, thread).filter((d) => d !== editable && d.send.state !== "superseded"
                                                    && d.send.state !== "accepted");
  const blockers = blockersOf(state, thread);
  const busy = !!pending;
  const self = isSelf(work);
  const subject = subjectOf(state, thread);

  // Closing returns focus to the item's row, where the owner came from.
  const close = () => {
    closeItem();
    requestAnimationFrame(() => document.querySelector<HTMLElement>(
      `.queue-view li[data-thread-id="${CSS.escape(thread)}"] .queue-row`)?.focus());
  };
  const respond = (parentId: string) =>
    startReply(thread, parentId, correspondent, subject);
  const next = () => setNoneWaiting(!nextItem());
  const done = () => {
    if (blockers.length) { setDismissing(true); return; }
    void dismiss(thread);
  };

  return (
    <div id="queue-item-view" className="queue-item-view" data-thread-id={thread}>
      <header className="item-head">
        {open && open.back.length > 0 && (
          <button type="button" className="item-back" onClick={goBack}>
            ← Back to {queueViewTitle(state, dir, open.back[open.back.length - 1])}
          </button>
        )}
        <h2 className="item-subject">{subject || "(no subject)"}</h2>
        <p className="item-who">
          <span className="item-person">{personName(dir, correspondent)}</span>
          {handle(dir, correspondent) && <span className="item-handle"> {handle(dir, correspondent)}</span>}
          {latestTheirs && <span className="item-via"> · {jarvisLabel(dir, latestTheirs.message.source_node_id)}</span>}
        </p>
        <div className="item-cues">
          <Cue kind={c.turn === "mine" ? "your-turn" : "their-turn"}>{turnWords(dir, work)}</Cue>
          {c.addressed && <Cue kind="addressed">Addressed</Cue>}
          {c.dismissed && <Cue kind="dismissed">Dismissed</Cue>}
          {!self && !c.dismissed && (c.turn === "mine"
            ? <button type="button" className="item-turn" disabled={busy}
                      onClick={() => void setTurn(thread, correspondent)}>
                Waiting on {personName(dir, correspondent)}
              </button>
            : <button type="button" className="item-turn" disabled={busy}
                      onClick={() => void setTurn(thread, work.owner_github_id)}>Move to my side</button>)}
        </div>
        <div className="item-actions" role="group" aria-label="This item">
          {!c.dismissed && parentForReply && !ed && !editable && (
            <button type="button" onClick={() => respond(parentForReply.message_id)}>Respond</button>
          )}
          <button type="button" onClick={next}>Next item</button>
          {!c.dismissed && work.placement !== "queued" && (
            <button type="button" disabled={busy} onClick={() => void putBack(thread)}>Put back</button>
          )}
          {!c.dismissed && (
            <button type="button" disabled={busy} aria-pressed={c.addressed}
                    onClick={() => void setAddressed(thread, !c.addressed)}>
              {c.addressed ? "Not addressed" : "Mark addressed"}
            </button>
          )}
          {!c.dismissed && !c.unread && (
            <button type="button" disabled={busy} onClick={() => void setUnread(thread, true)}>Mark unread</button>
          )}
          {c.dismissed
            ? <button type="button" disabled={busy} onClick={() => void restore(thread)}>Restore to queue</button>
            : <button type="button" disabled={busy} onClick={done}>Done</button>}
          <button type="button" onClick={close}>Close</button>
        </div>
        {noneWaiting && <p className="item-note" role="status">No items are waiting.</p>}
        {busy && <p className="item-note" role="status">Saving…</p>}
        {notes[thread] && <p className="item-note warn" role="alert">{notes[thread]}</p>}
        {loadError && <p className="read-failure" role="status">Couldn't read this item again ({loadError}); showing the last known.</p>}
        {dismissing && blockers.length > 0 && (
          <DismissBlockers thread={thread} blockers={blockers} onClose={() => setDismissing(false)} />
        )}
      </header>

      <div className="item-scroll">
        <ol className="item-exchange">
          {messages.map((m) => (
            <li key={m.message.message_id}>
              <MessageCard m={m} state={state} dir={dir} thread={thread}
                           onReply={c.dismissed || editable || ed ? undefined : () => respond(m.message.message_id)} />
            </li>
          ))}
          {outgoing.map((d) => (
            <li key={d.message_id}><OutgoingDraft draft={d} thread={thread} dir={dir} /></li>
          ))}
        </ol>
        {editable?.send.state === "awaiting_approval" && !ed?.dirty && <MessageReview draft={editable} />}
        {ed && <MessageComposer editorKey={thread} />}
      </div>
    </div>
  );
}

/** A new conversation not yet accepted: its editor, its review, or how
 *  its send stands. */
function UnsentConversation({ thread }: { thread: string }) {
  const state = useSyncExternalStore(queue.subscribe, queue.get);
  const ed = useSyncExternalStore(editors.subscribe, editors.get)[thread];
  const dir = useSyncExternalStore(directory.subscribe, directory.get);
  const back = useSyncExternalStore(openQueue.subscribe, openQueue.get)?.back ?? [];
  const draft = state.drafts[thread] ?? (ed?.messageId ? state.drafts[ed.messageId] : undefined);
  useEffect(() => { if (thread !== NEW) resumeDraft(thread); }, [thread]);
  const editable = !draft || draft.send.state === "draft" || draft.send.state === "awaiting_approval";
  return (
    <div id="queue-item-view" className="queue-item-view" data-thread-id={thread === NEW ? undefined : thread}>
      <header className="item-head">
        {back.length > 0 && <button type="button" className="item-back" onClick={goBack}>← Back</button>}
        <h2 className="item-subject">{draft?.subject || "New message"}</h2>
        {draft && <p className="item-who">To {recipientLabel(dir, draft.recipient_github_id)}</p>}
        <div className="item-actions"><button type="button" onClick={closeItem}>Close</button></div>
      </header>
      <div className="item-scroll">
        {draft && !editable && <OutgoingDraft draft={draft} thread={thread} dir={dir} />}
        {draft?.send.state === "awaiting_approval" && !ed?.dirty && <MessageReview draft={draft} />}
        {ed && <MessageComposer editorKey={thread} />}
      </div>
    </div>
  );
}

/** Why Done has to wait, each reason in the contract's words, and the ways
 *  out that are really there. */
function DismissBlockers({ thread, blockers, onClose }: {
  thread: string; blockers: string[]; onClose: () => void;
}) {
  const [sure, setSure] = useState(false);
  return (
    <div className="item-blockers" role="alert">
      <p>This item can't be dismissed yet:</p>
      <ul>
        {blockers.map((b) => <li key={b}>{DISMISS_BLOCKERS_SHOWN[b as keyof typeof DISMISS_BLOCKERS_SHOWN]}</li>)}
      </ul>
      <div className="item-actions">
        {blockers.includes("unsent_text") && !blockers.includes("send_unresolved") && (sure ? (
          <button type="button" onClick={async () => { if (await discard(thread)) { onClose(); void dismiss(thread); } }}>
            Discard the reply and dismiss
          </button>
        ) : (
          <button type="button" onClick={() => setSure(true)}>Discard the reply…</button>
        ))}
        <button type="button" onClick={() => { onClose(); void putBack(thread); }}>Put back instead</button>
        <button type="button" onClick={onClose}>Keep working</button>
      </div>
    </div>
  );
}

/** One accepted message of the exchange. */
function MessageCard({ m, state, dir, thread, onReply }: {
  m: ShownMessage; state: QueueState; dir: Directory; thread: string; onReply?: () => void;
}) {
  const msg = m.message;
  const owner = state.owner_github_id;
  const toMe = msg.recipient_github_id === owner;
  const fromMe = msg.sender_github_id === owner;
  const body = useRef<HTMLDivElement>(null);
  const parent = msg.in_reply_to_message_id ? state.messages[msg.in_reply_to_message_id]?.message : undefined;
  const replies = repliesTo(state, msg.message_id);

  // The read receipt: once this body has actually been on screen.
  useEffect(() => {
    const el = body.current;
    if (!toMe || m.receipts?.read_at || !el) return;
    const seen = new IntersectionObserver((entries) => {
      if (entries.some((e) => e.isIntersecting)) { seen.disconnect(); void bodyShown(msg.message_id); }
    }, { threshold: 0.01 });
    seen.observe(el);
    return () => seen.disconnect();
  }, [toMe, m.receipts?.read_at, msg.message_id]);

  const jump = (messageId: string) => {
    const there = threadOf(state, messageId);
    if (there === thread) {
      document.querySelector(`[data-message-id="${CSS.escape(messageId)}"]`)?.scrollIntoView({ block: "nearest" });
    } else if (there) {
      followLink(there);
    }
  };

  return (
    <article className={`queue-message ${fromMe ? "outgoing" : "incoming"}${m.self_message ? " self" : ""}`}
             data-message-id={msg.message_id} aria-label={`From ${personName(dir, msg.sender_github_id)}`}>
      <header className="message-head">
        <span className="message-person">{fromMe ? "You" : personName(dir, msg.sender_github_id)}</span>
        {!fromMe && handle(dir, msg.sender_github_id) && (
          <span className="message-handle">{handle(dir, msg.sender_github_id)}</span>
        )}
        <span className="message-via">via {jarvisLabel(dir, msg.source_node_id)}</span>
        {fromMe && <span className="message-to">to {recipientLabel(dir, msg.recipient_github_id)}</span>}
        <time className="message-time" dateTime={msg.accepted_at}>{clockTime(msg.accepted_at)}</time>
      </header>
      {msg.in_reply_to_message_id && (
        <p className="message-link">
          In reply to{" "}
          {parent
            ? <button type="button" className="link-button" onClick={() => jump(parent.message_id)}>
                {personName(dir, parent.sender_github_id)}: “{previewOf(parent) || parent.subject}”
              </button>
            : <span>a message not on this JARVIS</span>}
        </p>
      )}
      {msg.subject && <h3 className="message-subject">{msg.subject}</h3>}
      <div className="message-text" ref={body}>{msg.text}</div>
      {msg.attachments.length > 0 && (
        <div className="message-files">
          {msg.attachments.map((a) => (
            <FileCard key={a.attachment_id} fileRef={a} messageId={msg.message_id} incoming={toMe && !fromMe}
                      file={m.files.find((f) => f.attachment_id === a.attachment_id)} />
          ))}
        </div>
      )}
      <footer className="message-foot">
        {toMe && (m.receipts?.read_at
          ? <Cue kind="read-by-you" title={m.receipts.read_at}>Read by you</Cue>
          : <Cue kind="unread">Unread</Cue>)}
        {fromMe && (
          <Cue kind={DELIVERY_CUE[delivery(msg, m.receipts)]}
               title={m.receipts?.nodes.map((n) => jarvisLabel(dir, n.node_id)).join(", ") || undefined}>
            {DELIVERY_WORDS(dir, msg.recipient_github_id)[delivery(msg, m.receipts)]}
          </Cue>
        )}
        {replies.length > 0 && (
          <span className="message-replies">
            <Cue kind="replied">{replies.length === 1 ? "1 reply" : `${replies.length} replies`}</Cue>
            {replies.map((r) => (
              <button key={r.message_id} type="button" className="link-button" onClick={() => jump(r.message_id)}>
                {r.sender_github_id === owner ? "You" : personName(dir, r.sender_github_id)}:{" "}
                {DELIVERY_WORDS(dir, r.recipient_github_id)[delivery(r, state.receipts[r.message_id])]}
              </button>
            ))}
          </span>
        )}
        {msg.related_message_ids.length > 0 && (
          <span className="message-related">
            <Cue kind="related">Related</Cue>
            {msg.related_message_ids.map((id) => {
              const there = threadOf(state, id);
              const related = state.messages[id]?.message;
              return there
                ? <button key={id} type="button" className="link-button" onClick={() => jump(id)}>
                    {subjectOf(state, there) || previewOf(related) || "a message"}
                  </button>
                : <span key={id} className="link-missing">a message not on this JARVIS</span>;
            })}
          </span>
        )}
        {onReply && <button type="button" className="message-reply" onClick={onReply}>Reply to this</button>}
      </footer>
    </article>
  );
}

/** A response of the owner's on its way out: its state in words, its files
 *  and what can honestly be done with it now. Nothing is called accepted
 *  before the team server said so. */
function OutgoingDraft({ draft, thread, dir }: { draft: Draft; thread: string; dir: Directory }) {
  const s = draft.send.state;
  const failure = s === "failed" && draft.send.failure;
  return (
    <article className={`queue-message outgoing on-its-way send-${s}`} data-message-id={draft.message_id}>
      <header className="message-head">
        <span className="message-person">You</span>
        <span className="message-to">to {recipientLabel(dir, draft.recipient_github_id)}</span>
        <span className="queue-sending" role="status">{sendWords(draft)}</span>
      </header>
      {draft.subject && <h3 className="message-subject">{draft.subject}</h3>}
      <div className="message-text">{draft.text}</div>
      {draft.attachments.length > 0 && (
        <div className="message-files">
          {draft.attachments.map((a) => (
            <FileCard key={a.attachment_id} fileRef={a} incoming={false}
                      file={draft.files.find((f) => f.attachment_id === a.attachment_id)} />
          ))}
        </div>
      )}
      {failure && <p className="item-note warn">{shownFailure(failure)}</p>}
      {s === "accept_unknown" && (
        <p className="item-note">It may already have arrived. Retrying sends the same message, never a second one.</p>
      )}
      <div className="item-actions">
        {(s === "failed" || s === "accept_unknown") && (
          <button type="button" onClick={() => void retrySend(draft)}>Retry</button>
        )}
        {(s === "approved" || s === "failed") && (
          <button type="button" onClick={() => void cancelSend(draft)}>Cancel send</button>
        )}
        {(s === "failed" || s === "cancelled") && (
          <button type="button" onClick={() => editAgain(thread, draft)}>Edit</button>
        )}
      </div>
    </article>
  );
}

const shownFailure = (code: string) => shownFor(code, "It was not sent.");
