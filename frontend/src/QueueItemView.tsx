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
 *
 * Registered for pointing at: the item (`queueThread`: its head, whose
 * turn it is, Back, Next, Close and the reply being written), each message
 * (`queueMessage`: its card, body and receipt) and each file on a message
 * (`queueAttachment`). Back, Next item, Close and a link to another item
 * run `queue.back`, `queue.next`, `queue.close` and `queue.follow`
 * (queue.ts), the same commands the brain runs, with their side effects:
 * the next item becomes Active, leaving a reply saves its draft, and a
 * body shown sends its read receipt. The item's work commands, replies,
 * sends and files are his own acts, never navigation.
 */

import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import { codeWords, saidShown } from "./conversation";
import { t as tNow, useT } from "./locale";
import type { Draft } from "./messagingContract";
import { MessageComposer } from "./MessageComposer";
import { MessageReview } from "./MessageReview";
import {
  blockersOf, bodyShown, cancelSend, closeItem, directory, discard, dismiss, editAgain, editors,
  editableDraft, followLink, goBack, itemNotes, messagesChanged, messagingSaid, NEW, nextItem, openQueue,
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
import { CENTRE_SOCKET, SaidText } from "./ConversationView";
import { bind, bindingProps } from "./uiNavigation";
import { useUiTarget } from "./uiRegistry";

/** A part's callback ref, from a registered target. */
type PartRef = (part: string) => (el: Element | null) => void;

/** The open item, registered as the Queue's `queueThread` in the centre. */
const useItemTarget = (thread: string | null, label: string) =>
  useUiTarget(thread ? { ref: { region: "main", view: "main.queue", kind: "queueThread", key: thread },
                         label, socket: CENTRE_SOCKET } : null);

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
  if (thread === NEW) return tNow("main.queue.newMessage");
  const subject = subjectOf(state, thread);
  if (subject) return subject;
  const work = state.items[thread]?.work_state;
  return work ? personName(dir, work.correspondent_github_id) : tNow("tabs.main.queue");
}

function OneItem({ thread }: { thread: string }) {
  const t = useT();
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

  const ui = useItemTarget(thread, subjectOf(state, thread) || t("tabs.main.queue"));
  const item = state.items[thread];
  if (!item) {
    return (
      <div id="queue-item-view" className="queue-item-view" ref={ui.whole}>
        <p className="read-failure" role="status">
          {loadError ? t("main.queue.unreadable", { error: loadError }) : t("main.queue.reading")}
        </p>
        <div className="item-actions"><button type="button" ref={ui.part("close")}
                                              {...bindingProps(bind("queue.close", {}))}>{t("main.common.close")}</button></div>
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
    <div id="queue-item-view" className="queue-item-view" data-thread-id={thread} ref={ui.whole}>
      <header className="item-head" ref={ui.part("header")}>
        {open && open.back.length > 0 && (
          <button type="button" className="item-back" ref={ui.part("back")} {...bindingProps(bind("queue.back", {}))}>
            {t("main.queue.backTo", { title: queueViewTitle(state, dir, open.back[open.back.length - 1]) })}
          </button>
        )}
        <h2 className="item-subject">{subject || t("main.queue.noSubject")}</h2>
        <p className="item-who">
          <span className="item-person">{personName(dir, correspondent)}</span>
          {handle(dir, correspondent) && <span className="item-handle"> {handle(dir, correspondent)}</span>}
          {latestTheirs && <span className="item-via"> · {jarvisLabel(dir, latestTheirs.message.source_node_id)}</span>}
        </p>
        <div className="item-cues" ref={ui.part("turn")}>
          <Cue kind={c.turn === "mine" ? "your-turn" : "their-turn"}>{turnWords(dir, work)}</Cue>
          {c.addressed && <Cue kind="addressed">{t("main.queue.addressed")}</Cue>}
          {c.dismissed && <Cue kind="dismissed">{t("main.queue.dismissed")}</Cue>}
          {!self && !c.dismissed && (c.turn === "mine"
            ? <button type="button" className="item-turn" disabled={busy}
                      onClick={() => void setTurn(thread, correspondent)}>
                {t("main.queue.waitingOn", { person: personName(dir, correspondent) })}
              </button>
            : <button type="button" className="item-turn" disabled={busy}
                      onClick={() => void setTurn(thread, work.owner_github_id)}>{t("main.queue.moveToMe")}</button>)}
        </div>
        <div className="item-actions" role="group" aria-label={t("main.queue.thisItem")}>
          {!c.dismissed && parentForReply && !ed && !editable && (
            <button type="button" onClick={() => respond(parentForReply.message_id)}>{t("main.queue.respond")}</button>
          )}
          <button type="button" onClick={next} ref={ui.part("next")} data-ui-command="queue.next">{t("main.queue.next")}</button>
          {!c.dismissed && work.placement !== "queued" && (
            <button type="button" disabled={busy} onClick={() => void putBack(thread)}>{t("main.queue.putBack")}</button>
          )}
          {!c.dismissed && (
            <button type="button" disabled={busy} aria-pressed={c.addressed}
                    onClick={() => void setAddressed(thread, !c.addressed)}>
              {c.addressed ? t("main.queue.notAddressed") : t("main.queue.markAddressed")}
            </button>
          )}
          {!c.dismissed && !c.unread && (
            <button type="button" disabled={busy} onClick={() => void setUnread(thread, true)}>{t("main.queue.markUnread")}</button>
          )}
          {c.dismissed
            ? <button type="button" disabled={busy} onClick={() => void restore(thread)}>{t("main.queue.restore")}</button>
            : <button type="button" disabled={busy} onClick={done}>{t("main.queue.done")}</button>}
          <button type="button" onClick={close} ref={ui.part("close")} data-ui-command="queue.close">{t("main.common.close")}</button>
        </div>
        {noneWaiting && <p className="item-note" role="status">{t("main.queue.noneWaiting")}</p>}
        {busy && <p className="item-note" role="status">{t("main.queue.saving")}</p>}
        {notes[thread] && <SaidText said={notes[thread]} as="p" className="item-note warn" role="alert" />}
        {loadError && <p className="read-failure" role="status">{t("main.queue.unreadableAgain", { error: loadError })}</p>}
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
        {ed && <div ref={ui.part("composer")}><MessageComposer editorKey={thread} /></div>}
      </div>
    </div>
  );
}

/** A new conversation not yet accepted: its editor, its review, or how
 *  its send stands. */
function UnsentConversation({ thread }: { thread: string }) {
  const t = useT();
  const state = useSyncExternalStore(queue.subscribe, queue.get);
  const ed = useSyncExternalStore(editors.subscribe, editors.get)[thread];
  const dir = useSyncExternalStore(directory.subscribe, directory.get);
  const back = useSyncExternalStore(openQueue.subscribe, openQueue.get)?.back ?? [];
  const draft = state.drafts[thread] ?? (ed?.messageId ? state.drafts[ed.messageId] : undefined);
  useEffect(() => { if (thread !== NEW) resumeDraft(thread); }, [thread]);
  const editable = !draft || draft.send.state === "draft" || draft.send.state === "awaiting_approval";
  const ui = useItemTarget(thread === NEW ? null : thread, draft?.subject || t("main.queue.newMessage"));
  return (
    <div id="queue-item-view" className="queue-item-view" data-thread-id={thread === NEW ? undefined : thread}
         ref={ui.whole}>
      <header className="item-head" ref={ui.part("header")}>
        {back.length > 0 && <button type="button" className="item-back" ref={ui.part("back")}
                                    {...bindingProps(bind("queue.back", {}))}>{t("main.queue.back")}</button>}
        <h2 className="item-subject">{draft?.subject || t("main.queue.newMessage")}</h2>
        {draft && <p className="item-who">{t("main.queue.toPerson", { person: recipientLabel(dir, draft.recipient_github_id) })}</p>}
        <div className="item-actions"><button type="button" ref={ui.part("close")}
                                                {...bindingProps(bind("queue.close", {}))}>{t("main.common.close")}</button></div>
      </header>
      <div className="item-scroll">
        {draft && !editable && <OutgoingDraft draft={draft} thread={thread} dir={dir} />}
        {draft?.send.state === "awaiting_approval" && !ed?.dirty && <MessageReview draft={draft} />}
        {ed && <div ref={ui.part("composer")}><MessageComposer editorKey={thread} /></div>}
      </div>
    </div>
  );
}

/** Why Done has to wait, each reason in the contract's words, and the ways
 *  out that are really there. */
function DismissBlockers({ thread, blockers, onClose }: {
  thread: string; blockers: string[]; onClose: () => void;
}) {
  const t = useT();
  const [sure, setSure] = useState(false);
  return (
    <div className="item-blockers" role="alert">
      <p>{t("main.queue.blockers")}</p>
      <ul>
        {blockers.map((b) => <li key={b}>{codeWords("main.messaging.blocker", b) ?? b}</li>)}
      </ul>
      <div className="item-actions">
        {blockers.includes("unsent_text") && !blockers.includes("send_unresolved") && (sure ? (
          <button type="button" onClick={async () => { if (await discard(thread)) { onClose(); void dismiss(thread); } }}>
            {t("main.queue.discardAndDismiss")}
          </button>
        ) : (
          <button type="button" onClick={() => setSure(true)}>{t("main.queue.discardReply")}</button>
        ))}
        <button type="button" onClick={() => { onClose(); void putBack(thread); }}>{t("main.queue.putBackInstead")}</button>
        <button type="button" onClick={onClose}>{t("main.queue.keepWorking")}</button>
      </div>
    </div>
  );
}

/** One accepted message of the exchange. */
function MessageCard({ m, state, dir, thread, onReply }: {
  m: ShownMessage; state: QueueState; dir: Directory; thread: string; onReply?: () => void;
}) {
  const t = useT();
  const msg = m.message;
  const owner = state.owner_github_id;
  const toMe = msg.recipient_github_id === owner;
  const fromMe = msg.sender_github_id === owner;
  const body = useRef<HTMLDivElement | null>(null);
  const ui = useUiTarget({ ref: { region: "main", view: "main.queue", kind: "queueMessage", key: msg.message_id },
                           label: t("main.queue.from", { person: personName(dir, msg.sender_github_id) }), socket: CENTRE_SOCKET });
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
             data-message-id={msg.message_id} aria-label={t("main.queue.from", { person: personName(dir, msg.sender_github_id) })}
             ref={(el) => { ui.whole(el); ui.part("card")(el); }}>
      <header className="message-head">
        <span className="message-person">{fromMe ? t("main.queue.you") : personName(dir, msg.sender_github_id)}</span>
        {!fromMe && handle(dir, msg.sender_github_id) && (
          <span className="message-handle">{handle(dir, msg.sender_github_id)}</span>
        )}
        <span className="message-via">{t("main.queue.via", { jarvis: jarvisLabel(dir, msg.source_node_id) })}</span>
        {fromMe && <span className="message-to">{t("main.queue.toLower", { person: recipientLabel(dir, msg.recipient_github_id) })}</span>}
        <time className="message-time" dateTime={msg.accepted_at}>{clockTime(msg.accepted_at)}</time>
      </header>
      {msg.in_reply_to_message_id && (
        <p className="message-link">
          {parent
            ? <>{t("main.queue.inReplyTo")}{" "}
              <button type="button" className="link-button" onClick={() => jump(parent.message_id)}>
                {personName(dir, parent.sender_github_id)}: “{previewOf(parent) || parent.subject}”
              </button></>
            : <span>{t("main.queue.inReplyToMissing")}</span>}
        </p>
      )}
      {msg.subject && <h3 className="message-subject">{msg.subject}</h3>}
      <div className="message-text" ref={(el) => { body.current = el; ui.part("body")(el); }}>{msg.text}</div>
      {msg.attachments.length > 0 && (
        <div className="message-files">
          {msg.attachments.map((a) => (
            <FileCard key={a.attachment_id} fileRef={a} messageId={msg.message_id} incoming={toMe && !fromMe}
                      file={m.files.find((f) => f.attachment_id === a.attachment_id)} />
          ))}
        </div>
      )}
      <footer className="message-foot">
        {(toMe || fromMe) && <span className="message-receipt" ref={ui.part("receipt")}>
          {toMe && (m.receipts?.read_at
            ? <Cue kind="read-by-you" title={m.receipts.read_at}>{t("main.queue.readByYou")}</Cue>
            : <Cue kind="unread">{t("main.queue.unread")}</Cue>)}
          {fromMe && (
            <Cue kind={DELIVERY_CUE[delivery(msg, m.receipts)]}
                 title={m.receipts?.nodes.map((n) => jarvisLabel(dir, n.node_id)).join(", ") || undefined}>
              {DELIVERY_WORDS(dir, msg.recipient_github_id)[delivery(msg, m.receipts)]}
            </Cue>
          )}
        </span>}
        {replies.length > 0 && (
          <span className="message-replies">
            <Cue kind="replied">{t("main.queue.replies", { count: replies.length })}</Cue>
            {replies.map((r) => (
              <button key={r.message_id} type="button" className="link-button" onClick={() => jump(r.message_id)}>
                {r.sender_github_id === owner ? t("main.queue.you") : personName(dir, r.sender_github_id)}:{" "}
                {DELIVERY_WORDS(dir, r.recipient_github_id)[delivery(r, state.receipts[r.message_id])]}
              </button>
            ))}
          </span>
        )}
        {msg.related_message_ids.length > 0 && (
          <span className="message-related">
            <Cue kind="related">{t("main.queue.related")}</Cue>
            {msg.related_message_ids.map((id) => {
              const there = threadOf(state, id);
              const related = state.messages[id]?.message;
              return there
                ? <button key={id} type="button" className="link-button" onClick={() => jump(id)}>
                    {subjectOf(state, there) || previewOf(related) || t("main.queue.aMessage")}
                  </button>
                : <span key={id} className="link-missing">{t("main.queue.messageMissing")}</span>;
            })}
          </span>
        )}
        {onReply && <button type="button" className="message-reply" onClick={onReply}>{t("main.queue.replyToThis")}</button>}
      </footer>
    </article>
  );
}

/** A response of the owner's on its way out: its state in words, its files
 *  and what can honestly be done with it now. Nothing is called accepted
 *  before the team server said so. */
function OutgoingDraft({ draft, thread, dir }: { draft: Draft; thread: string; dir: Directory }) {
  const t = useT();
  const s = draft.send.state;
  const failure = s === "failed" && draft.send.failure;
  return (
    <article className={`queue-message outgoing on-its-way send-${s}`} data-message-id={draft.message_id}>
      <header className="message-head">
        <span className="message-person">{t("main.queue.you")}</span>
        <span className="message-to">{t("main.queue.toLower", { person: recipientLabel(dir, draft.recipient_github_id) })}</span>
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
        <p className="item-note">{t("main.queue.mayHaveArrived")}</p>
      )}
      <div className="item-actions">
        {(s === "failed" || s === "accept_unknown") && (
          <button type="button" onClick={() => void retrySend(draft)}>{t("main.queue.retry")}</button>
        )}
        {(s === "approved" || s === "failed") && (
          <button type="button" onClick={() => void cancelSend(draft)}>{t("main.queue.cancelSend")}</button>
        )}
        {(s === "failed" || s === "cancelled") && (
          <button type="button" onClick={() => editAgain(thread, draft)}>{t("main.queue.edit")}</button>
        )}
      </div>
    </article>
  );
}

/** Why a send failed, from its contract code. */
const shownFailure = (code: string) =>
  saidShown(messagingSaid(code, { words: () => tNow("main.queue.notSent") })).text;
