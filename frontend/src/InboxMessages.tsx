/**
 * The Inbox's messages from agents, at the top of the Inbox tab (the tab
 * that was the Queue; QueuePanel.tsx), shown signed in to a team server or
 * not: they come from agents on this Mac, not from people.
 *
 *   - a heading, "From agents", with how many are new and Hide messages /
 *     Show messages (`inbox.messages.shown`, his click and the brain's
 *     request alike): hidden, only the heading stays, so people's messages
 *     and the rest of the Inbox are in view. This browser remembers it;
 *   - each Work item with new messages, one line ("Work tracker: 2 new"),
 *     which opens that item's Messages tab (`work.open`, the brain's
 *     request too); an item with none new has no line;
 *   - the new messages that belong to no item, newest first, each a
 *     MessageCard (who, when, NEW, what it says, Read, Reply, Mark read,
 *     and Open when it carries a package);
 *   - under them, Read (`inbox.read.shown`), closed to begin with: the
 *     ones he has read or answered and is not done with, newest first. A
 *     message leaves the new list once read, and the Inbox once done.
 *
 * Nothing here opens, scrolls or moves focus when a message arrives. Each
 * line and each message is a target the brain can point at; pointing at one
 * never opens or reads it (pointing at a read one opens Read, and at any
 * while hidden shows the messages, as their toggles do).
 */

import { useSyncExternalStore } from "react";
import { inboxMessages, readMessages, type ToHim, type WorkThread } from "./messagesToHim";
import { useT } from "./locale";
import { MessageCard } from "./MessageCard";
import { createStore } from "./store";
import { bind, bindingProps } from "./uiNavigation";
import { registerRevealer } from "./uiNavigationReveal";
import { registerCommand, useUiTarget } from "./uiRegistry";
import { RIGHT_SOCKET, row } from "./uiRight";

type PartRef = (el: Element | null) => void;

/** Where this browser keeps whether the messages are hidden. */
const HIDDEN_KEY = "jarvis.inbox.messagesHidden";

function savedHidden(): boolean {
  try {
    return window.localStorage.getItem(HIDDEN_KEY) === "1";
  } catch {
    return false;
  }
}

/** Whether the messages from agents are hidden; kept in this browser. */
const messagesHidden = createStore(savedHidden());

/** Whether Read is open; kept while the tab is switched, closed on load. */
const readOpen = createStore(false);

function hideMessages(hidden: boolean): void {
  messagesHidden.set(hidden);
  try {
    if (hidden) window.localStorage.setItem(HIDDEN_KEY, "1");
    else window.localStorage.removeItem(HIDDEN_KEY);
  } catch {
    // Storage that cannot be written: hidden for this page load only.
  }
}

registerCommand("inbox.messages.shown", {
  availability: () => ({ available: true }),
  execute({ shown }) {
    hideMessages(!shown);
    return { status: "completed", effects: ["list.filtered"] };
  },
});

registerCommand("inbox.read.shown", {
  availability: () => ({ available: true }),
  execute({ shown }) {
    const was = readOpen.get();
    readOpen.set(shown);
    if (shown) hideMessages(false);
    if (shown && !was) readMessages.refresh();
    return { status: "completed", effects: shown && !was ? ["list.paged"] : [] };
  },
});

const isRead = (id: string) => (readMessages.get().value?.messages ?? []).some((m) => String(m.id) === id);

// Pointing at a message or a Work line the Inbox does not draw now: the
// messages are shown, and a read message's Read opened, as their toggles do.
registerRevealer("right.queue", "inboxMessage", async (ref) => {
  hideMessages(false);
  if (isRead(ref.key!)) readOpen.set(true);
  return true;
});
registerRevealer("right.queue", "inboxWork", async () => {
  hideMessages(false);
  return true;
});

export function InboxMessages({ workPart, generalPart, togglePart, readPart }: {
  workPart: PartRef; generalPart: PartRef; togglePart: PartRef; readPart: PartRef;
}) {
  const t = useT();
  const live = useSyncExternalStore(inboxMessages.subscribe, inboxMessages.get);
  const hidden = useSyncExternalStore(messagesHidden.subscribe, messagesHidden.get);
  const items = (live.value?.work.items ?? []).filter((i) => i.new > 0);
  const general = (live.value?.general.messages ?? []).filter((m) => m.state === "new");
  const readCount = live.value?.general.read ?? 0;
  const fresh = (live.value?.work.new ?? 0) + (live.value?.general.new ?? 0);
  const any = items.length > 0 || general.length > 0 || readCount > 0;
  return (
    <>
      {live.error && !live.value && (
        <p className="read-failure" role="status">{t("right.inbox.unreadable", { error: live.error })}</p>
      )}
      {any && (
        <section className={`queue-section inbox-agents${hidden ? " hidden" : ""}`}
                 aria-labelledby="inbox-agents-heading">
          <div className="inbox-agents-head">
            <h3 className="queue-heading" id="inbox-agents-heading">
              {t("right.inbox.fromAgents")}
              {fresh > 0 && <span className="queue-count" title={t("right.inbox.new", { count: fresh })}>{fresh}</span>}
            </h3>
            <button type="button" className="file-action inbox-hide" aria-expanded={!hidden} ref={togglePart}
                    {...bindingProps(bind("inbox.messages.shown", { shown: hidden }))}>
              {hidden ? t("right.inbox.show") : t("right.inbox.hide")}</button>
          </div>
          {!hidden && items.length > 0 && (
            <section className="inbox-work" aria-labelledby="inbox-work-heading" ref={workPart}>
              <h4 className="queue-heading" id="inbox-work-heading">
                {t("right.inbox.byWork")} <span className="queue-count">{items.length}</span>
              </h4>
              <ul className="queue-list" data-list="work">
                {items.map((i) => <WorkLine key={i.work} thread={i} />)}
              </ul>
            </section>
          )}
          {!hidden && general.length > 0 && (
            <section className="inbox-general" aria-labelledby="inbox-general-heading" ref={generalPart}>
              <h4 className="queue-heading" id="inbox-general-heading">
                {t("right.inbox.general")} <span className="queue-count">{live.value!.general.new}</span>
              </h4>
              <ul className="queue-list inbox-messages" data-list="general">
                {general.map((m) => <GeneralMessage key={m.id} message={m} />)}
              </ul>
            </section>
          )}
          {!hidden && readCount > 0 && <Read count={readCount} part={readPart} />}
        </section>
      )}
    </>
  );
}

/** The messages he has read or answered and is not done with: closed
 *  until asked for, like Work's History. */
function Read({ count, part }: { count: number; part: PartRef }) {
  const t = useT();
  const shown = useSyncExternalStore(readOpen.subscribe, readOpen.get);
  const live = useSyncExternalStore(readMessages.subscribe, readMessages.get);
  const rows = shown ? (live.value?.messages ?? []).filter((m) => m.state !== "new" && m.state !== "done") : [];
  return (
    <div className="queue-history inbox-read" ref={part}>
      <button type="button" className="queue-history-toggle" aria-expanded={shown}
              {...bindingProps(bind("inbox.read.shown", { shown: !shown }))}>
        {shown ? "▾" : "▸"} {t("right.inbox.readCount", { count })}
      </button>
      {shown && live.error && !live.value && (
        <p className="read-failure" role="status">{t("right.inbox.unreadable", { error: live.error })}</p>
      )}
      {shown && (
        <ul className="queue-list inbox-messages" data-list="read">
          {rows.map((m) => <GeneralMessage key={m.id} message={m} />)}
        </ul>
      )}
    </div>
  );
}

/** One Work item with new messages: opens its Messages tab. */
function WorkLine({ thread }: { thread: WorkThread }) {
  const t = useT();
  const title = thread.title || thread.work;
  const ui = useUiTarget({ ref: { region: "right", view: "right.queue", kind: "inboxWork", key: thread.work },
                           label: title, socket: RIGHT_SOCKET });
  return (
    <li className="queue-item" data-work-id={thread.work}>
      <button type="button" className="queue-row inbox-work-row" ref={row(ui)}
              {...bindingProps(bind("work.open", { itemId: thread.work, tab: "messages" }))}>
        <span className="row-subject">{title}</span>
        <span className="row-side" ref={ui.part("count")}>{t("right.inbox.new", { count: thread.new })}</span>
      </button>
    </li>
  );
}

/** One message that belongs to no Work item. */
function GeneralMessage({ message }: { message: ToHim }) {
  const ui = useUiTarget({ ref: { region: "right", view: "right.queue", kind: "inboxMessage", key: String(message.id) },
                           label: message.from.name, socket: RIGHT_SOCKET });
  return <MessageCard message={message} ui={ui} />;
}
