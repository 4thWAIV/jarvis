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
 * Each row carries its thread id (`data-thread-id`), and its person badge
 * (`.queue-endpoint`) is where a transfer's ray ends: it carries
 * `data-transfer-endpoint`, which transferEndpoints.ts looks up, and the
 * list moves `queueLaidOut` on every commit so a ray re-measures.
 *
 * Navigation runs registered commands, the same ones the brain's requests
 * run: a Waiting or Active row `queue.open` (a waiting one becomes Active,
 * an unread body is marked read once shown, the item left keeps its saved
 * draft), an unsent or dismissed row `queue.follow` (its place unchanged),
 * Show dismissed `queue.history` and Show more `queue.history.more`. The
 * panel (`queuePanel`) and each row (`queueThread`) are targets the brain
 * can point at; pointing at a row never opens it, so it is neither
 * activated nor read. New and Restore change drafts and placement: they
 * are not navigation and stay their own actions.
 */

import { useLayoutEffect, useSyncExternalStore } from "react";
import { RIGHT_SOCKET, row } from "./uiRight";
import { useT } from "./locale";
import type { Draft } from "./messagingContract";
import {
  directory, historyError, NEW, openQueue, pendingCommands, queue, queueReadError, readHistory, restore,
  startNew,
} from "./queue";
import { Cue, DELIVERY_CUE, QueueIcon } from "./QueueIcons";
import {
  active, cues, DELIVERY_WORDS, dismissed, jarvisLabel, latestFrom, messagesOf, personName, previewOf,
  sendWords, subjectOf, turnWords, unsentDrafts, waiting, type Directory, type ItemRecord,
  type QueueState,
} from "./queueStore";
import { createStore } from "./store";
import { shortAgo } from "./timeFormat";
import { queueLaidOut, TRANSFER_ENDPOINT_ATTR } from "./transferEndpoints";
import { UI_CONTRACT, type SideEffect } from "./uiContract";
import { bind, bindingProps } from "./uiNavigation";
import { registerRevealer } from "./uiNavigationReveal";
import { registerCommand, useUiTarget, type Availability } from "./uiRegistry";
import { useTick } from "./WorkflowsView";

const JUST_NOW_MS = 60_000;

// ── Show dismissed and its pages ────────────────────────────────────────

/** Whether Show dismissed is open; kept while the tab is switched. */
const historyOpen = createStore(false);

const offered = (): Availability =>
  queue.get().offered ? { available: true } : { available: false, reason: "not-signed-in" };

/** Show or hide the dismissed items; showing reads their first page. */
async function showHistory(shown: boolean): Promise<SideEffect[]> {
  const was = historyOpen.get();
  historyOpen.set(shown);
  if (!shown || was) return [];
  await readHistory(true);
  return ["list.paged"];
}

registerCommand("queue.history", {
  availability: offered,
  execute: async ({ shown }) => ({ status: "completed", effects: await showHistory(shown) }),
});

registerCommand("queue.history.more", {
  availability() {
    const ok = offered();
    if (!ok.available) return ok;
    return historyOpen.get() && queue.get().history_next ? { available: true } : { available: false, reason: "no-entity" };
  },
  async execute() {
    await readHistory(false);
    return { status: "completed", effects: ["list.paged"] };
  },
});

/** Whether Waiting or Active / Outbox lists `thread`. */
const listedOpen = (state: QueueState, thread: string) =>
  [...waiting(state), ...active(state)].some((i) => i.work_state.thread_id === thread)
  || unsentDrafts(state).some((d) => d.thread_id === thread);

const listedDismissed = (state: QueueState, thread: string) =>
  dismissed(state).some((i) => i.work_state.thread_id === thread);

// Pointing at a row not on the page once the tab shows: a dismissed item is
// brought in by showing the dismissed items and reading them a page at a
// time, within the reveal limit. Nothing is opened.
registerRevealer("right.queue", "queueThread", async (ref, { signal }) => {
  const thread = ref.key!;
  if (!queue.get().offered) return "not-signed-in";
  if (listedOpen(queue.get(), thread)) return true;
  const deadline = performance.now() + UI_CONTRACT.limits.revealWaitMs;
  await showHistory(true);
  while (!listedDismissed(queue.get(), thread)) {
    if (signal.aborted || !queue.get().history_next || performance.now() > deadline) return "entity-gone";
    await readHistory(false);
  }
  return true;
});

/** One row's presentation for the brain: whose it is and its subject. */
function useRowTarget(thread: string | null, who: string, subject: string) {
  return useUiTarget(thread === null ? null : {
    ref: { region: "right", view: "right.queue", kind: "queueThread", key: thread },
    label: subject ? `${who}: ${subject}` : who, socket: RIGHT_SOCKET,
  });
}

export function QueuePanel() {
  const t = useT();
  const state = useSyncExternalStore(queue.subscribe, queue.get);
  const error = useSyncExternalStore(queueReadError.subscribe, queueReadError.get);
  const dir = useSyncExternalStore(directory.subscribe, directory.get);
  const open = useSyncExternalStore(openQueue.subscribe, openQueue.get)?.thread ?? null;
  const ui = useUiTarget({ ref: { region: "right", view: "right.queue", kind: "queuePanel" },
                           label: "Queue", socket: RIGHT_SOCKET });
  useTick(30000);
  useLayoutEffect(() => { queueLaidOut.set(queueLaidOut.get() + 1); });

  if (!state.offered) {
    return (
      <div className="right-view queue-view" ref={ui.whole}>
        <p className="queue-notice">{t("right.queue.notOffered")}</p>
      </div>
    );
  }
  const waitingRows = waiting(state);
  const activeRows = active(state);
  const unsent = unsentDrafts(state);
  return (
    <div className="right-view queue-view" ref={ui.whole}>
      {state.availability && state.availability !== "available" && (
        <p className="queue-notice" data-availability={state.availability}>
          {t(`right.queue.availability.${state.availability}`)}
        </p>
      )}
      {error && (
        <p className="read-failure" role="status">
          {t("right.queue.readFailed", { error })}
        </p>
      )}
      {state.stale && !error && (
        <p className="queue-notice stale" role="status">{t("right.queue.stale")}</p>
      )}
      <div className="queue-head">
        <button type="button" className="queue-new" onClick={startNew} ref={ui.part("new")}
                disabled={state.availability !== "available"}
                aria-current={open === NEW ? "true" : undefined}>
          <QueueIcon kind="new" /> {t("right.queue.new")}
        </button>
      </div>

      <section className="queue-section" aria-labelledby="queue-waiting-heading" ref={ui.part("waiting")}>
        <h3 className="queue-heading" id="queue-waiting-heading">
          {t("right.queue.waiting")} <span className="queue-count">{waitingRows.length}</span>
        </h3>
        {state.loaded && waitingRows.length === 0 && <p className="queue-empty">{t("right.queue.nothingWaiting")}</p>}
        <ul className="queue-list" data-list="waiting">
          {waitingRows.map((i) => <Row key={i.work_state.thread_id} item={i} state={state} dir={dir}
                                       current={open === i.work_state.thread_id} />)}
        </ul>
      </section>

      <section className="queue-section" aria-labelledby="queue-active-heading" ref={ui.part("active")}>
        <h3 className="queue-heading" id="queue-active-heading">
          {t("right.queue.active")} <span className="queue-count">{activeRows.length + unsent.length}</span>
        </h3>
        {state.loaded && activeRows.length + unsent.length === 0 && <p className="queue-empty">{t("right.queue.nothingActive")}</p>}
        <ul className="queue-list" data-list="active">
          {unsent.map((d) => <UnsentRow key={d.message_id} draft={d} dir={dir}
                                        current={open === d.thread_id}
                                        // One item, one presentation: an unsent
                                        // draft whose item has come is that item.
                                        listed={!!state.items[d.thread_id]} />)}
          {activeRows.map((i) => <Row key={i.work_state.thread_id} item={i} state={state} dir={dir}
                                      current={open === i.work_state.thread_id} />)}
        </ul>
      </section>

      <History state={state} dir={dir} open={open} historyRef={ui.part("history")} moreRef={ui.part("more")} />
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
  const t = useT();
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
  const ui = useRowTarget(thread, who, subject);
  return (
    <li data-thread-id={thread} className="queue-item">
      <button type="button" {...bindingProps(bind("queue.open", { threadId: thread }))} ref={row(ui)}
              className={`queue-row turn-${c.turn}${current ? " current" : ""}${c.unread ? " unread" : ""}${
                pending ? " pending" : ""}`}
              aria-current={current ? "true" : undefined} aria-busy={pending ? true : undefined}>
        <span className="queue-endpoint" data-github-id={work.correspondent_github_id}
              {...{ [TRANSFER_ENDPOINT_ATTR]: thread }} aria-hidden="true">
          {who.slice(0, 1).toUpperCase()}
        </span>
        <span className="row-who" ref={ui.part("sender")}>
          <span className="row-person">{who}</span>
          {via && <span className="row-via"> · {via}</span>}
        </span>
        <span className="row-age" title={work.arrival_at}>{shortAgo(Math.max(0, ago), JUST_NOW_MS)}</span>
        <span className="row-subject" ref={ui.part("subject")}>{subject || previewOf(latest) || t("right.queue.noSubject")}</span>
        <span className="row-side" ref={ui.part("turn")}>
          <Cue kind={c.turn === "mine" ? "your-turn" : "their-turn"}>{turnWords(dir, work)}</Cue>
        </span>
        <span className="row-strip">
          {c.unread && <Cue kind="unread">{t("right.queue.unread")}</Cue>}
          {c.readByYou && <Cue kind="read-by-you">{t("right.queue.readByYou")}</Cue>}
          {c.addressed && <Cue kind="addressed">{t("right.queue.addressed")}</Cue>}
          {c.replied && <Cue kind="replied">{t("right.queue.replied")}</Cue>}
          {c.files > 0 && <Cue kind="files">{t("right.queue.files", { count: c.files })}</Cue>}
          {c.related && <Cue kind="related">{t("right.queue.related")}</Cue>}
        </span>
        {(c.sending || c.delivery || unsentReply) && (
          <span className="row-delivery" ref={ui.part("receipt")}>
            {c.sending
              ? <span className="queue-sending">{sendWords(c.sending)}</span>
              : unsentReply
                ? <span className="queue-sending">{t("right.queue.replyDrafted")}</span>
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
function UnsentRow({ draft, dir, current, listed }: {
  draft: Draft; dir: Directory; current: boolean; listed: boolean;
}) {
  const t = useT();
  const who = personName(dir, draft.recipient_github_id);
  const to = t("right.queue.to", { name: who });
  const ui = useRowTarget(listed ? null : draft.thread_id, to, draft.subject ?? "");
  return (
    <li data-thread-id={draft.thread_id} className="queue-item">
      <button type="button" {...bindingProps(bind("queue.follow", { threadId: draft.thread_id }))}
              className={`queue-row unsent${current ? " current" : ""}`} ref={row(ui)}
              aria-current={current ? "true" : undefined}>
        <span className="queue-endpoint" data-github-id={draft.recipient_github_id}
              {...{ [TRANSFER_ENDPOINT_ATTR]: draft.thread_id }} aria-hidden="true">
          {who.slice(0, 1).toUpperCase()}
        </span>
        <span className="row-who" ref={ui.part("sender")}><span className="row-person">{to}</span></span>
        <span className="row-subject" ref={ui.part("subject")}>
          {draft.subject || previewOf(draft) || t("right.queue.noSubject")}
        </span>
        <span className="row-delivery" ref={ui.part("receipt")}>
          <span className="queue-sending">{sendWords(draft)}</span>
        </span>
      </button>
    </li>
  );
}

type PartRef = (el: Element | null) => void;

/** The dismissed items: collapsed until asked for, then a page at a time. */
function History({ state, dir, open, historyRef, moreRef }: {
  state: QueueState; dir: Directory; open: string | null; historyRef: PartRef; moreRef: PartRef;
}) {
  const t = useT();
  const shown = useSyncExternalStore(historyOpen.subscribe, historyOpen.get);
  const error = useSyncExternalStore(historyError.subscribe, historyError.get);
  const rows = dismissed(state);
  return (
    <div className="queue-history" ref={historyRef}>
      <button type="button" className="queue-history-toggle" aria-expanded={shown}
              {...bindingProps(bind("queue.history", { shown: !shown }))}>
        {shown ? "▾" : "▸"} {t(shown ? "right.queue.hideDismissed" : "right.queue.showDismissed",
                                 { count: state.history_count })}
      </button>
      {shown && (
        <>
          {error && <p className="read-failure" role="status">{error}</p>}
          {state.history_read && rows.length === 0 && <p className="queue-empty">{t("right.queue.nothingDismissed")}</p>}
          <ul className="queue-list done" data-list="dismissed">
            {rows.map((i) => <DismissedRow key={i.work_state.thread_id} item={i} state={state} dir={dir}
                                           current={open === i.work_state.thread_id} />)}
          </ul>
          {state.history_next && (
            <button type="button" className="queue-history-more" ref={moreRef}
                    {...bindingProps(bind("queue.history.more", {}))}>
              {t("right.queue.more")}
            </button>
          )}
        </>
      )}
    </div>
  );
}

function DismissedRow({ item, state, dir, current }: {
  item: ItemRecord; state: QueueState; dir: Directory; current: boolean;
}) {
  const t = useT();
  const thread = item.work_state.thread_id;
  const [who] = whoAndVia(state, dir, item);
  const subject = subjectOf(state, thread);
  const ui = useRowTarget(thread, who, subject);
  return (
    <li data-thread-id={thread} className="queue-item">
      <button type="button" className={`queue-done-row${current ? " current" : ""}`} ref={row(ui)}
              aria-current={current ? "true" : undefined}
              {...bindingProps(bind("queue.follow", { threadId: thread }))}>
        <span className="row-person" ref={ui.part("sender")}>{who}</span>
        <span className="row-subject" ref={ui.part("subject")}>{subject || t("right.queue.noSubject")}</span>
        <Cue kind="dismissed">{t("right.queue.dismissed")}</Cue>
      </button>
      <button type="button" className="queue-restore" onClick={() => void restore(thread)}>
        {t("right.queue.restore")}
      </button>
    </li>
  );
}
