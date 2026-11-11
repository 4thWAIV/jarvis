/**
 * Updates waiting, at the top of the centre area: one grouped count of the
 * attention record's pending items and Review updates, never one notice per
 * event (attention.ts). Shown while anything is pending, or while spoken
 * updates are held; gone otherwise.
 *
 * On Conversation it is a line under the tabs, and Review updates opens the
 * list in place: each update in words, by its kind and event, with the name
 * it is about as its source wrote it (text, never markup), and Seen; Mark
 * all seen marks every pending one. Hold spoken updates says he is away:
 * nothing routine is spoken until Resume. None of these speak, send or
 * approve anything.
 *
 * Messages to him are not listed one by one: one line says how many are
 * new ("3 new messages": on Work items, on none, and people's waiting
 * unread; messagesToHim.ts and queue.ts) with Open, which goes to the Inbox
 * (`right.tab.select`), and the minimise mark (`updates.messages.shown`,
 * as the Inbox's): minimised, it says only "3 new" beside the mark, and
 * this browser remembers it. Only JARVIS's own facts (a run finished,
 * communications down) are counted and listed as updates; an update that
 * is a message (an agent's answer, a session's message, a decision, a
 * package, a person's message) is that line's.
 *
 * On every other view (`compact`, CentreNotices.tsx) it is one small chip in
 * the tab row ("3 updates") that never opens by itself: clicking it opens
 * the same list as a small dropdown over the view, with Mark all seen and
 * Hold in it, closed by Escape, a click outside it, or the chip again. With
 * nothing pending but updates held, the chip says so with Resume beside it.
 *
 * The list is closed whenever the centre's view changes, so a list left open
 * on one view never comes back open on the next.
 *
 * The notice is a target hung from the main rail, its count, Review, Mark
 * all seen, Hold and the list as parts. Review (the chip, when compact) is
 * the registered command `updates.review`, the same for his click and the
 * brain's request; Seen and Hold are the brain's mark_updates_seen and
 * set_attention_settings. A part inside the closed dropdown is revealed by
 * opening it with `updates.review`.
 */

import { useEffect, useState, useSyncExternalStore } from "react";
import { attention, itemName, itemWords, markSeen, setAway, type AttentionItem } from "./attention";
import { inboxMessages, newMessages } from "./messagesToHim";
import { useT } from "./locale";
import { queue } from "./queue";
import { waiting, type QueueState } from "./queueStore";
import { MinimiseToggle } from "./MessageCard";
import { createStore, rememberedFlag } from "./store";
import { bind, bindingProps, mainView } from "./uiNavigation";
import { registerRevealer } from "./uiNavigationReveal";
import { byHand, registerCommand, useUiTarget } from "./uiRegistry";

/** @routeState Whether Review updates' list is open. */
const reviewOpen = createStore(false);

/** An update that is a message to him (its words are someone's): counted
 *  in the one messages line, never listed as an update. */
const isMessage = (item: AttentionItem) => item.message != null || item.kind === "package" || item.kind === "message";

/** JARVIS's own facts waiting: what is listed as updates. */
const factsOf = (items: AttentionItem[] | undefined) =>
  (items ?? []).filter((i) => i.state === "pending" && !isMessage(i));

/** People's items waiting unread in the Inbox. */
const unreadPeople = (state: QueueState) => waiting(state).filter((i) => i.work_state.unread_reminder).length;

/** How many messages to him are new: from agents and from people. */
const messagesNow = () => newMessages(inboxMessages.get().value) + unreadPeople(queue.get());

const anyPending = () => factsOf(attention.get().value?.items).length > 0 || messagesNow() > 0;

/** Whether the new messages line is minimised; kept in this browser. */
const messagesMinimised = rememberedFlag("jarvis.updates.messagesMinimised");

registerCommand("updates.messages.shown", {
  availability: () => messagesNow() > 0 ? { available: true } : { available: false, reason: "no-entity" },
  execute({ shown }) {
    messagesMinimised.set(!shown);
    return { status: "completed", effects: [] };
  },
});

registerCommand("updates.review", {
  availability: () => anyPending() ? { available: true } : { available: false, reason: "no-entity" },
  execute(args) {
    reviewOpen.set(args.open);
    return { status: "completed", effects: [] };
  },
});

// Any change of the centre's view closes the list.
mainView.subscribe(() => reviewOpen.set(false));

// Mark all seen, Hold and the list sit in the chip's dropdown on a reading
// view: closed, they are brought out by opening it.
registerRevealer("main.notice", "updatesNotice", async (ref, { navigate }) => {
  if (ref.part !== "list" && ref.part !== "markSeen" && ref.part !== "away") return "no-entity";
  if (!anyPending()) return "no-entity";
  return (await navigate("updates.review", { open: true })) ? true : "no-entity";
});

export function UpdatesNotice({ compact = false }: { compact?: boolean }) {
  const t = useT();
  const live = useSyncExternalStore(attention.subscribe, attention.get);
  const open = useSyncExternalStore(reviewOpen.subscribe, reviewOpen.get);
  const minimised = useSyncExternalStore(messagesMinimised.subscribe, messagesMinimised.get);
  const inbox = useSyncExternalStore(inboxMessages.subscribe, inboxMessages.get).value;
  const people = useSyncExternalStore(queue.subscribe, queue.get);
  const now = live.value;
  const pending = factsOf(now?.items);
  const fresh = newMessages(inbox) + unreadPeople(people);
  const away = now?.settings.away ?? false;
  const shown = pending.length > 0 || fresh > 0 || away;
  const listed = open && pending.length > 0;
  const ui = useUiTarget(shown ? { ref: { region: "main", view: "main.notice", kind: "updatesNotice" },
                                   label: t("targets.updatesNotice"), socket: { rail: "rail.main", side: "right" } }
                               : null);

  // The dropdown closes on Escape or a click outside it (the chip's own
  // click toggles it). Escape is taken first, so it does not also skip
  // what JARVIS is reading.
  const dropdown = compact && open && (pending.length > 0 || fresh > 0);
  useEffect(() => {
    if (!dropdown) return;
    const close = () => byHand("updates.review", { open: false });
    const key = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      e.stopPropagation();
      e.preventDefault();
      close();
    };
    const press = (e: PointerEvent) => {
      if (e.target instanceof Element && e.target.closest("#updates-notice")) return;
      close();
    };
    document.addEventListener("keydown", key, true);
    document.addEventListener("pointerdown", press, true);
    return () => {
      document.removeEventListener("keydown", key, true);
      document.removeEventListener("pointerdown", press, true);
    };
  }, [dropdown]);

  if (!shown) return null;
  const messages = fresh > 0 && (
    <p className={`updates-messages${minimised ? " minimised" : ""}`} ref={ui.part("messages")}>
      <MinimiseToggle shown={!minimised} hideLabel={t("main.updates.minimise")}
                      showLabel={t("main.updates.restore")} part={ui.part("minimise")}
                      binding={bindingProps(bind("updates.messages.shown", { shown: minimised }))} />
      <span>{minimised ? t("main.updates.messagesShort", { count: fresh })
                       : t("main.updates.messages", { count: fresh })}</span>
      {!minimised && (
        <button type="button" className="settings-btn primary" ref={ui.part("openInbox")}
                {...bindingProps(bind("right.tab.select", { tab: "queue" }))}>{t("main.updates.openInbox")}</button>
      )}
    </p>
  );
  const markAll = pending.length > 0 && (
    <button type="button" className="settings-btn" ref={ui.part("markSeen")}
            data-ui-action="domain" onClick={() => void markSeen()}>{t("main.updates.markAllSeen")}</button>
  );
  const hold = (
    <button type="button" className="settings-btn" ref={ui.part("away")} aria-pressed={away}
            data-ui-action="domain" onClick={() => void setAway(!away)}>
      {away ? t("main.updates.resume") : t("main.updates.hold")}</button>
  );
  const list = listed && (
    <ul className="updates-list" ref={ui.part("list")}>
      {pending.map((item) => (
        <li key={item.id}>
          <span className="updates-words" title={itemName(item) || undefined}>{itemWords(item)}</span>
          <button type="button" className="settings-btn" data-ui-action="domain"
                  onClick={() => void markSeen([item.id])}>
            {t("main.updates.markSeen")}</button>
        </li>
      ))}
    </ul>
  );

  if (compact) {
    return (
      <div id="updates-notice" className="centre-chip" role="status" ref={ui.whole}>
        {pending.length > 0 || fresh > 0 ? (
          <button type="button" className="centre-chip-button" ref={ui.part("review")} aria-expanded={open}
                  aria-haspopup="true" {...bindingProps(bind("updates.review", { open: !open }))}>
            <span ref={ui.part("text")}>{t("main.updates.chip", { count: pending.length + fresh })}</span>
          </button>
        ) : (
          <>
            <span ref={ui.part("text")}>{t("main.updates.chipHeld")}</span>
            {hold}
          </>
        )}
        {dropdown && (
          <div className="updates-dropdown">
            {messages}
            {list}
            <div className="updates-dropdown-actions">{markAll}{hold}</div>
          </div>
        )}
      </div>
    );
  }

  return (
    <div id="updates-notice" role="status" ref={ui.whole}>
      {messages}
      {(pending.length > 0 || away) && <p ref={ui.part("text")}>
        {pending.length > 0 ? t("main.updates.count", { count: pending.length }) : t("main.updates.held")}
      </p>}
      {pending.length > 0 && (
        <button type="button" className="settings-btn primary" ref={ui.part("review")}
                aria-expanded={open} {...bindingProps(bind("updates.review", { open: !open }))}>
          {open ? t("main.updates.hide") : t("main.updates.review")}</button>
      )}
      {markAll}
      {hold}
      {list}
    </div>
  );
}
