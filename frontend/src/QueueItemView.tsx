/**
 * One item, in the centre: the work surface for the conversation open now.
 * One view however many messages there are; opening another item replaces
 * it, and the one left stays in Active with its draft.
 *
 *   - The head: the subject, who it is with (their name, with the GitHub
 *     handle as its tooltip, and the JARVIS it came through), whose turn it
 *     is (with the owner's correction: Move to my side / Waiting on …), and
 *     the item's actions: Respond, Next, Put back, Mark addressed, Mark
 *     unread, Done (Restore for a dismissed one), Close; Back after
 *     following a link.
 *   - The exchange, oldest first: each message with its sender (handle and
 *     JARVIS as the tooltip), time, subject when it is not the item's, text
 *     (as text), its files, what it replies to, its direct replies and
 *     related messages, and for the owner's own how far it got. Read by you
 *     is an icon; Unread is said.
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
 * (`queueAttachment`). Back, Next, Close and a link to another item
 * run `queue.back`, `queue.next`, `queue.close` and `queue.follow`
 * (queue.ts), the same commands the brain runs, with their side effects:
 * the next item becomes Active, leaving a reply saves its draft, and a
 * body shown sends its read receipt. Respond and Reply (`queue.reply`),
 * Edit on a failed response (`queue.draft.edit`), a link to another
 * message (`queue.message.show`) and the questions before Done and before
 * discarding (`queue.done.ask`, `queue.discard.ask`) are commands too: they
 * open or show, and save nothing. The item's work commands, sends,
 * discards and files are his own acts, never navigation.
 */

import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import { codeWords, saidShown, SaidText, t as tNow, useT } from "./locale";
import type { Draft } from "./messagingContract";
import { MessageComposer } from "./MessageComposer";
import { MessageReview } from "./MessageReview";
import {
  askedNoMore, asking, blockersOf, bodyShown, cancelSend, closeItem, directory, discard, dismiss, editors,
  editableDraft, itemNotes, messagesChanged, messagingSaid, NEW, nextItem, openQueue,
  pendingCommands, putBack, queue, readItem, restore, resumeDraft, retrySend, setAddressed,
  setTurn, setUnread,
} from "./queue";
import { FileCard } from "./QueueFiles";
import { ReviewProgressLabel, TaskLine } from "./BundleLines";
import { Cue, DELIVERY_CUE } from "./QueueIcons";
import {
  cues, DELIVERY_WORDS, delivery, draftsOf, handle, isSelf, jarvisLabel, latestFrom, messagesOf, personName,
  previewOf, recipientLabel, repliesTo, sendWords, subjectOf, threadOf, turnWords,
  type Directory, type QueueState, type ShownMessage,
} from "./queueStore";
import { clockTime } from "./network";
import { CENTRE_SOCKET } from "./ConversationView";
import { bind, bindingProps, runBinding, type Binding } from "./uiNavigation";
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
  const asked = useSyncExternalStore(asking.subscribe, asking.get);
  const doneStep = asked.thread === thread ? asked.done : "closed";

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
  const reply = (messageId: string) => bind("queue.reply", { messageId });
  const next = () => setNoneWaiting(!nextItem());
  const done = () => {
    if (blockers.length) { runBinding(bind("queue.done.ask", { step: "blockers" })); return; }
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
          <span className="item-person" title={handle(dir, correspondent) ?? undefined}>
            {personName(dir, correspondent)}
          </span>
          {handle(dir, correspondent) && <span className="visually-hidden"> {handle(dir, correspondent)}</span>}
          {latestTheirs && <span className="item-via"> · {jarvisLabel(dir, latestTheirs.message.source_node_id)}</span>}
        </p>
        <div className="item-cues" ref={ui.part("turn")}>
          <Cue kind={c.turn === "mine" ? "your-turn" : "their-turn"}>{turnWords(dir, work)}</Cue>
          {c.addressed && <Cue kind="addressed">{t("main.queue.addressed")}</Cue>}
          {c.dismissed && <Cue kind="dismissed">{t("main.queue.dismissed")}</Cue>}
          <ReviewProgressLabel thread={thread} />
          {!self && !c.dismissed && (c.turn === "mine"
            ? <button data-ui-action="domain" type="button" className="item-turn" disabled={busy}
                      onClick={() => void setTurn(thread, correspondent)}>
                {t("main.queue.waitingOn", { person: personName(dir, correspondent) })}
              </button>
            : <button data-ui-action="domain" type="button" className="item-turn" disabled={busy}
                      onClick={() => void setTurn(thread, work.owner_github_id)}>{t("main.queue.moveToMe")}</button>)}
        </div>
        <div className="item-actions" role="group" aria-label={t("main.queue.thisItem")}>
          {!c.dismissed && parentForReply && !ed && !editable && (
            <button type="button" {...bindingProps(reply(parentForReply.message_id))}>{t("main.queue.respond")}</button>
          )}
          <button type="button" onClick={next} ref={ui.part("next")} data-ui-command="queue.next">{t("main.queue.next")}</button>
          {!c.dismissed && work.placement !== "queued" && (
            <button data-ui-action="domain" type="button" disabled={busy} onClick={() => void putBack(thread)}>{t("main.queue.putBack")}</button>
          )}
          {!c.dismissed && (
            <button data-ui-action="domain" type="button" disabled={busy} aria-pressed={c.addressed}
                    onClick={() => void setAddressed(thread, !c.addressed)}>
              {c.addressed ? t("main.queue.notAddressed") : t("main.queue.markAddressed")}
            </button>
          )}
          {!c.dismissed && !c.unread && (
            <button data-ui-action="domain" type="button" disabled={busy} onClick={() => void setUnread(thread, true)}>{t("main.queue.markUnread")}</button>
          )}
          {c.dismissed
            ? <button data-ui-action="domain" type="button" disabled={busy} onClick={() => void restore(thread)}>{t("main.queue.restore")}</button>
            : <button data-ui-action="domain" type="button" disabled={busy} onClick={done}>{t("main.queue.done")}</button>}
          <button type="button" onClick={close} ref={ui.part("close")} data-ui-command="queue.close">{t("main.common.close")}</button>
        </div>
        {noneWaiting && <p className="item-note" role="status">{t("main.queue.noneWaiting")}</p>}
        {busy && <p className="item-note" role="status">{t("main.queue.saving")}</p>}
        {notes[thread] && <SaidText said={notes[thread]} as="p" className="item-note warn" role="alert" />}
        {loadError && <p className="read-failure" role="status">{t("main.queue.unreadableAgain", { error: loadError })}</p>}
        {doneStep !== "closed" && blockers.length > 0 && (
          <DismissBlockers thread={thread} blockers={blockers} sure={doneStep === "discard"} />
        )}
      </header>

      <div className="item-scroll">
        <ol className="item-exchange">
          {messages.map((m) => (
            <li key={m.message.message_id}>
              <MessageCard m={m} state={state} dir={dir} thread={thread} itemSubject={subject}
                           reply={c.dismissed || editable || ed ? undefined : reply(m.message.message_id)} />
            </li>
          ))}
          {outgoing.map((d) => (
            <li key={d.message_id}><OutgoingDraft draft={d} thread={thread} dir={dir} itemSubject={subject} /></li>
          ))}
        </ol>
        {editable?.send.state === "awaiting_approval" && !ed?.dirty && <MessageReview draft={editable} />}
        {ed && <div ref={ui.part("composer")}><MessageComposer editorKey={thread} request={ui.part("request")} /></div>}
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
        {draft && !editable && <OutgoingDraft draft={draft} thread={thread} dir={dir} itemSubject={draft.subject} />}
        {draft?.send.state === "awaiting_approval" && !ed?.dirty && <MessageReview draft={draft} />}
        {ed && <div ref={ui.part("composer")}><MessageComposer editorKey={thread} request={ui.part("request")} /></div>}
      </div>
    </div>
  );
}

/** Why Done has to wait, each reason in the contract's words, and the ways
 *  out that are really there; `sure` once he asked to discard the reply. */
function DismissBlockers({ thread, blockers, sure }: { thread: string; blockers: string[]; sure: boolean }) {
  const t = useT();
  return (
    <div className="item-blockers" role="alert">
      <p>{t("main.queue.blockers")}</p>
      <ul>
        {blockers.map((b) => <li key={b}>{codeWords("main.messaging.blocker", b) ?? b}</li>)}
      </ul>
      <div className="item-actions">
        {blockers.includes("unsent_text") && !blockers.includes("send_unresolved") && (sure ? (
          <button data-ui-action="domain" type="button" onClick={async () => { if (await discard(thread)) { askedNoMore(); void dismiss(thread); } }}>
            {t("main.queue.discardAndDismiss")}
          </button>
        ) : (
          <button type="button" {...bindingProps(bind("queue.done.ask", { step: "discard" }))}>{t("main.queue.discardReply")}</button>
        ))}
        <button data-ui-action="domain" type="button" onClick={() => { askedNoMore(); void putBack(thread); }}>{t("main.queue.putBack")}</button>
        <button type="button" {...bindingProps(bind("queue.done.ask", { step: "closed" }))}>{t("main.common.cancel")}</button>
      </div>
    </div>
  );
}

/** One accepted message of the exchange; `reply` starts a reply to it. */
function MessageCard({ m, state, dir, itemSubject, reply }: {
  m: ShownMessage; state: QueueState; dir: Directory; thread: string; itemSubject: string; reply?: Binding;
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
  // The sender's handle and the JARVIS it came through: the name's tooltip,
  // and said to screen readers.
  const senderDetail = [!fromMe && handle(dir, msg.sender_github_id),
                        t("main.queue.via", { jarvis: jarvisLabel(dir, msg.source_node_id) })]
    .filter(Boolean).join(" · ");

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

  /** A link to another message: scrolled to here, or its own item opened. */
  const show = (messageId: string) => bindingProps(bind("queue.message.show", { messageId }));

  return (
    <article className={`queue-message ${fromMe ? "outgoing" : "incoming"}${m.self_message ? " self" : ""}`}
             data-message-id={msg.message_id} aria-label={t("main.queue.from", { person: personName(dir, msg.sender_github_id) })}
             ref={(el) => { ui.whole(el); ui.part("card")(el); }}>
      <header className="message-head">
        <span className="message-person" title={senderDetail}>
          {fromMe ? t("main.queue.you") : personName(dir, msg.sender_github_id)}
        </span>
        <span className="visually-hidden"> · {senderDetail}</span>
        {fromMe && (
          <span className="message-to" title={recipientLabel(dir, msg.recipient_github_id)}>
            {t("main.queue.toLower", { person: personName(dir, msg.recipient_github_id) })}
          </span>
        )}
        <time className="message-time" dateTime={msg.accepted_at}>{clockTime(msg.accepted_at)}</time>
      </header>
      {msg.in_reply_to_message_id && (
        <p className="message-link">
          {parent
            ? <>{t("main.queue.inReplyTo")}{" "}
              <button type="button" className="link-button" {...show(parent.message_id)}>
                {personName(dir, parent.sender_github_id)}: “{previewOf(parent) || parent.subject}”
              </button></>
            : <span>{t("main.queue.inReplyToMissing")}</span>}
        </p>
      )}
      {msg.subject && msg.subject !== itemSubject && <h3 className="message-subject">{msg.subject}</h3>}
      <TaskLine task={msg.task} attachments={msg.attachments} target={ui.part("request")} />
      <div className="message-text" ref={(el) => { body.current = el; ui.part("body")(el); }}>{msg.text}</div>
      {msg.attachments.length > 0 && (
        <div className="message-files">
          {msg.attachments.map((a) => (
            <FileCard key={a.attachment_id} fileRef={a} messageId={msg.message_id} incoming={toMe && !fromMe}
                      file={m.files.find((f) => f.attachment_id === a.attachment_id)}
                      report={fromMe
                        ? m.receipts?.openability.find((r) => r.attachment_id === a.attachment_id
                                                              && r.sha256 === a.sha256)
                        : undefined}
                      cites={fromMe
                        ? m.cited_in_reply.filter((c) => c.attachment_id === a.attachment_id
                                                          && c.sha256 === a.sha256)
                        : undefined} />
          ))}
        </div>
      )}
      <footer className="message-foot">
        {(toMe || fromMe) && <span className="message-receipt" ref={ui.part("receipt")}>
          {toMe && (m.receipts?.read_at
            ? <Cue kind="read-by-you" quiet>{t("main.queue.readByYou")}</Cue>
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
              <button key={r.message_id} type="button" className="link-button" {...show(r.message_id)}
                      title={DELIVERY_WORDS(dir, r.recipient_github_id)[delivery(r, state.receipts[r.message_id])]}>
                {r.sender_github_id === owner ? t("main.queue.you") : personName(dir, r.sender_github_id)}
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
                ? <button key={id} type="button" className="link-button" {...show(id)}>
                    {subjectOf(state, there) || previewOf(related) || t("main.queue.aMessage")}
                  </button>
                : <span key={id} className="link-missing">{t("main.queue.messageMissing")}</span>;
            })}
          </span>
        )}
        {reply && <button type="button" className="message-reply" {...bindingProps(reply)}>{t("main.queue.replyToThis")}</button>}
      </footer>
    </article>
  );
}

/** A response of the owner's on its way out: its state in words, its files
 *  and what can honestly be done with it now. Nothing is called accepted
 *  before the team server said so. */
function OutgoingDraft({ draft, thread, dir, itemSubject }: {
  draft: Draft; thread: string; dir: Directory; itemSubject: string;
}) {
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
      {draft.subject && draft.subject !== itemSubject && <h3 className="message-subject">{draft.subject}</h3>}
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
          <button data-ui-action="domain" type="button" onClick={() => void retrySend(draft)}>{t("main.queue.retry")}</button>
        )}
        {(s === "approved" || s === "failed") && (
          <button data-ui-action="domain" type="button" onClick={() => void cancelSend(draft)}>{t("main.queue.cancelSend")}</button>
        )}
        {(s === "failed" || s === "cancelled") && (
          <button type="button" {...bindingProps(bind("queue.draft.edit", { messageId: draft.message_id }))}>{t("main.queue.edit")}</button>

        )}
      </div>
    </article>
  );
}

/** Why a send failed, from its contract code. */
const shownFailure = (code: string) =>
  saidShown(messagingSaid(code, { words: () => tNow("main.queue.notSent") })).text;
