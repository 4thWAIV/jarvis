/**
 * The Inbox's messages from agents, at the top of the Inbox tab (the tab
 * that was the Queue; QueuePanel.tsx), shown signed in to a team server or
 * not: they come from agents on this Mac, not from people.
 *
 *   - each Work item with new messages, one line ("Work tracker: 2 new"),
 *     which opens that item's Messages tab (`work.open`, the brain's
 *     request too);
 *   - the messages that belong to no item, newest first, each a
 *     MessageCard (who, when, NEW, what it says, Read, Reply, and Open when
 *     it carries a package); the ones he is done with are left out.
 *
 * Nothing here opens, scrolls or moves focus when a message arrives. Each
 * line and each message is a target the brain can point at; pointing at one
 * never opens or reads it.
 */

import { useSyncExternalStore } from "react";
import { inboxMessages, type ToHim, type WorkThread } from "./messagesToHim";
import { useT } from "./locale";
import { MessageCard } from "./MessageCard";
import { bind, bindingProps } from "./uiNavigation";
import { useUiTarget } from "./uiRegistry";
import { RIGHT_SOCKET, row } from "./uiRight";

type PartRef = (el: Element | null) => void;

export function InboxMessages({ workPart, generalPart }: { workPart: PartRef; generalPart: PartRef }) {
  const t = useT();
  const live = useSyncExternalStore(inboxMessages.subscribe, inboxMessages.get);
  const items = (live.value?.work.items ?? []).filter((i) => i.new > 0);
  const general = (live.value?.general.messages ?? []).filter((m) => m.state !== "done");
  return (
    <>
      {live.error && !live.value && (
        <p className="read-failure" role="status">{t("right.inbox.unreadable", { error: live.error })}</p>
      )}
      {items.length > 0 && (
        <section className="queue-section inbox-work" aria-labelledby="inbox-work-heading" ref={workPart}>
          <h3 className="queue-heading" id="inbox-work-heading">
            {t("right.inbox.byWork")} <span className="queue-count">{items.length}</span>
          </h3>
          <ul className="queue-list" data-list="work">
            {items.map((i) => <WorkLine key={i.work} thread={i} />)}
          </ul>
        </section>
      )}
      {general.length > 0 && (
        <section className="queue-section inbox-general" aria-labelledby="inbox-general-heading" ref={generalPart}>
          <h3 className="queue-heading" id="inbox-general-heading">
            {t("right.inbox.general")} <span className="queue-count">{live.value!.general.new}</span>
          </h3>
          <ul className="queue-list inbox-messages" data-list="general">
            {general.map((m) => <GeneralMessage key={m.id} message={m} />)}
          </ul>
        </section>
      )}
    </>
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
