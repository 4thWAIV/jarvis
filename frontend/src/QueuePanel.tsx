/**
 * The right panel's Queue: one owner's work, newest arrival first. Waiting
 * at the top (what has come in and not been taken up), Active / Outbox
 * under it (opened work, outgoing exchanges and whatever is on its way out,
 * kept together), and the dismissed items behind a collapsed Show
 * dismissed, read a page at a time. There are no folders.
 *
 * A row is the person and the JARVIS it came through, the subject (or the
 * first line), how long ago it arrived, whose turn it is, a strip of the
 * owner's own cues (Unread or Read by you, Addressed, You replied, files,
 * Related) and, apart from them, how far the owner's latest message got.
 * Clicking a row opens the item in the centre; a waiting one becomes
 * Active. Nothing here moves focus or scrolls when something arrives.
 *
 * Each row carries its thread id (`data-thread-id`) and an endpoint
 * element, the person badge (`.queue-endpoint`), for a transfer's ray;
 * `queueTarget` finds it, or the Queue tab when the row is not in view.
 */

import { useLayoutEffect, useSyncExternalStore } from "react";
import { AVAILABILITY_SHOWN, type Draft } from "./messagingContract";
import {
  directory, followLink, historyError, NEW, openItem, openQueue, pendingCommands, queue,
  queueReadError, readHistory, restore, startNew,
} from "./queue";
import { Cue, DELIVERY_CUE, QueueIcon } from "./QueueIcons";
import {
  active, cues, DELIVERY_WORDS, dismissed, jarvisLabel, latestFrom, messagesOf, personName, previewOf,
  sendWords, subjectOf, turnWords, unsentDrafts, waiting, type Directory, type ItemRecord,
  type QueueState,
} from "./queueStore";
import { createStore } from "./store";
import { shortAgo } from "./timeFormat";
import { useTick } from "./WorkflowsView";

/** Counts the Queue's commits to the page: a ray re-measures its row on it. */
export const queueLaidOut = createStore(0);

/** Where a transfer on this item should end: its row's person badge while
 *  the row is in view, otherwise the Queue tab. Never another item's row,
 *  and the list is never scrolled to find it. */
export function queueTarget(thread: string): Element | null {
  const list = document.querySelector(".queue-view");
  const row = list?.querySelector(`li[data-thread-id="${CSS.escape(thread)}"] .queue-endpoint`);
  if (list && row) {
    const r = row.getBoundingClientRect();
    const v = list.getBoundingClientRect();
    if (r.height > 0 && r.bottom > v.top && r.top < v.bottom) return row;
  }
  return document.querySelector(".right-tabs [aria-label='Queue']");
}

const JUST_NOW_MS = 60_000;

export function QueuePanel() {
  const state = useSyncExternalStore(queue.subscribe, queue.get);
  const error = useSyncExternalStore(queueReadError.subscribe, queueReadError.get);
  const dir = useSyncExternalStore(directory.subscribe, directory.get);
  const open = useSyncExternalStore(openQueue.subscribe, openQueue.get)?.thread ?? null;
  useTick(30000);
  useLayoutEffect(() => { queueLaidOut.set(queueLaidOut.get() + 1); });

  if (!state.offered) {
    return (
      <div className="right-view queue-view">
        <p className="queue-notice">Messaging is not available on this JARVIS.</p>
      </div>
    );
  }
  const waitingRows = waiting(state);
  const activeRows = active(state);
  const unsent = unsentDrafts(state);
  return (
    <div className="right-view queue-view">
      {state.availability && state.availability !== "available" && (
        <p className="queue-notice" data-availability={state.availability}>
          {AVAILABILITY_SHOWN[state.availability]}
        </p>
      )}
      {error && (
        <p className="read-failure" role="status">
          Couldn't read the queue ({error}); showing the last known and trying again.
        </p>
      )}
      {state.stale && !error && (
        <p className="queue-notice stale" role="status">The team server is not answering; this is the last known.</p>
      )}
      <div className="queue-head">
        <button type="button" className="queue-new" onClick={startNew}
                disabled={state.availability !== "available"}
                aria-current={open === NEW ? "true" : undefined}>
          <QueueIcon kind="new" /> New
        </button>
      </div>

      <section className="queue-section" aria-labelledby="queue-waiting-heading">
        <h3 className="queue-heading" id="queue-waiting-heading">
          Waiting <span className="queue-count">{waitingRows.length}</span>
        </h3>
        {state.loaded && waitingRows.length === 0 && <p className="queue-empty">Nothing waiting</p>}
        <ul className="queue-list" data-list="waiting">
          {waitingRows.map((i) => <Row key={i.work_state.thread_id} item={i} state={state} dir={dir}
                                       current={open === i.work_state.thread_id} />)}
        </ul>
      </section>

      <section className="queue-section" aria-labelledby="queue-active-heading">
        <h3 className="queue-heading" id="queue-active-heading">
          Active / Outbox <span className="queue-count">{activeRows.length + unsent.length}</span>
        </h3>
        {state.loaded && activeRows.length + unsent.length === 0 && <p className="queue-empty">Nothing active</p>}
        <ul className="queue-list" data-list="active">
          {unsent.map((d) => <UnsentRow key={d.message_id} draft={d} dir={dir}
                                        current={open === d.thread_id} />)}
          {activeRows.map((i) => <Row key={i.work_state.thread_id} item={i} state={state} dir={dir}
                                      current={open === i.work_state.thread_id} />)}
        </ul>
      </section>

      <History state={state} dir={dir} open={open} />
    </div>
  );
}

/** Who an item is with, and the JARVIS their latest message came through. */
function whoAndVia(state: QueueState, dir: Directory, item: ItemRecord): [string, string | null] {
  const work = item.work_state;
  const theirs = latestFrom(state, work.thread_id, work.correspondent_github_id);
  return [personName(dir, work.correspondent_github_id),
          theirs ? jarvisLabel(dir, theirs.message.source_node_id) : null];
}

function Row({ item, state, dir, current }: {
  item: ItemRecord; state: QueueState; dir: Directory; current: boolean;
}) {
  const work = item.work_state;
  const thread = work.thread_id;
  const c = cues(state, thread)!;
  const pending = useSyncExternalStore(pendingCommands.subscribe, pendingCommands.get)[thread];
  const [who, via] = whoAndVia(state, dir, item);
  const messages = messagesOf(state, thread);
  const latest = messages[messages.length - 1]?.message;
  const subject = subjectOf(state, thread);
  const ago = Date.now() - Date.parse(work.arrival_at);
  const unsentReply = item.dismiss_blockers.includes("unsent_text");
  return (
    <li data-thread-id={thread} className="queue-item">
      <button type="button" onClick={() => openItem(thread)}
              className={`queue-row turn-${c.turn}${current ? " current" : ""}${c.unread ? " unread" : ""}${
                pending ? " pending" : ""}`}
              aria-current={current ? "true" : undefined} aria-busy={pending ? true : undefined}>
        <span className="queue-endpoint" data-github-id={work.correspondent_github_id} aria-hidden="true">
          {who.slice(0, 1).toUpperCase()}
        </span>
        <span className="row-who">
          <span className="row-person">{who}</span>
          {via && <span className="row-via"> · {via}</span>}
        </span>
        <span className="row-age" title={work.arrival_at}>{shortAgo(Math.max(0, ago), JUST_NOW_MS)}</span>
        <span className="row-subject">{subject || previewOf(latest) || "(no subject)"}</span>
        <span className="row-side">
          <Cue kind={c.turn === "mine" ? "your-turn" : "their-turn"}>{turnWords(dir, work)}</Cue>
        </span>
        <span className="row-strip">
          {c.unread && <Cue kind="unread">Unread</Cue>}
          {c.readByYou && <Cue kind="read-by-you">Read by you</Cue>}
          {c.addressed && <Cue kind="addressed">Addressed</Cue>}
          {c.replied && <Cue kind="replied">You replied</Cue>}
          {c.files > 0 && <Cue kind="files">{c.files} file{c.files === 1 ? "" : "s"}</Cue>}
          {c.related && <Cue kind="related">Related</Cue>}
        </span>
        {(c.sending || c.delivery || unsentReply) && (
          <span className="row-delivery">
            {c.sending
              ? <span className="queue-sending">{sendWords(c.sending)}</span>
              : unsentReply
                ? <span className="queue-sending">Reply drafted, not sent</span>
                : <DeliveryCue state={state} dir={dir} thread={thread} />}
          </span>
        )}
      </button>
    </li>
  );
}

/** How far the owner's latest message on the item got. */
function DeliveryCue({ state, dir, thread }: { state: QueueState; dir: Directory; thread: string }) {
  const c = cues(state, thread)!;
  const owner = state.owner_github_id;
  const mine = messagesOf(state, thread).filter((m) => m.message.sender_github_id === owner).pop();
  if (!c.delivery || !mine) return null;
  const words = DELIVERY_WORDS(dir, mine.message.recipient_github_id)[c.delivery];
  return <Cue kind={DELIVERY_CUE[c.delivery]}>{words}</Cue>;
}

/** A new conversation not yet accepted: on its way out, so in the Outbox. */
function UnsentRow({ draft, dir, current }: { draft: Draft; dir: Directory; current: boolean }) {
  const who = personName(dir, draft.recipient_github_id);
  return (
    <li data-thread-id={draft.thread_id} className="queue-item">
      <button type="button" onClick={() => followLink(draft.thread_id)}
              className={`queue-row unsent${current ? " current" : ""}`}
              aria-current={current ? "true" : undefined}>
        <span className="queue-endpoint" data-github-id={draft.recipient_github_id} aria-hidden="true">
          {who.slice(0, 1).toUpperCase()}
        </span>
        <span className="row-who"><span className="row-person">To {who}</span></span>
        <span className="row-subject">{draft.subject || previewOf(draft) || "(no subject)"}</span>
        <span className="row-delivery"><span className="queue-sending">{sendWords(draft)}</span></span>
      </button>
    </li>
  );
}

/** The dismissed items: collapsed until asked for, then a page at a time. */
function History({ state, dir, open }: { state: QueueState; dir: Directory; open: string | null }) {
  const shown = useSyncExternalStore(historyOpen.subscribe, historyOpen.get);
  const error = useSyncExternalStore(historyError.subscribe, historyError.get);
  const rows = dismissed(state);
  const toggle = () => {
    historyOpen.set(!shown);
    if (!shown) void readHistory(true);
  };
  return (
    <div className="queue-history">
      <button type="button" className="queue-history-toggle" aria-expanded={shown} onClick={toggle}>
        {shown ? "▾" : "▸"} {shown ? "Hide" : "Show"} dismissed ({state.history_count})
      </button>
      {shown && (
        <>
          {error && <p className="read-failure" role="status">{error}</p>}
          {state.history_read && rows.length === 0 && <p className="queue-empty">Nothing dismissed</p>}
          <ul className="queue-list done" data-list="dismissed">
            {rows.map((i) => {
              const thread = i.work_state.thread_id;
              const [who] = whoAndVia(state, dir, i);
              return (
                <li key={thread} data-thread-id={thread} className="queue-item">
                  <button type="button" className={`queue-done-row${open === thread ? " current" : ""}`}
                          aria-current={open === thread ? "true" : undefined}
                          onClick={() => followLink(thread)}>
                    <span className="row-person">{who}</span>
                    <span className="row-subject">{subjectOf(state, thread) || "(no subject)"}</span>
                    <Cue kind="dismissed">Dismissed</Cue>
                  </button>
                  <button type="button" className="queue-restore" onClick={() => void restore(thread)}>
                    Restore
                  </button>
                </li>
              );
            })}
          </ul>
          {state.history_next && (
            <button type="button" className="queue-history-more" onClick={() => void readHistory(false)}>
              Show more
            </button>
          )}
        </>
      )}
    </div>
  );
}

/** Whether Show dismissed is open; kept while the tab is switched. */
const historyOpen = createStore(false);
