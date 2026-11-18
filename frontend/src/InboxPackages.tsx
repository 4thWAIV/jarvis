/**
 * His open packages, in the Inbox tab under the messages from agents
 * (QueuePanel.tsx): every package an agent sent him that is not closed yet,
 * newest first, from the package inbox's record (GET /api/packages/inbox,
 * packageInbox.ts `openPackages`, read again on every `packages` hint). The
 * brain reads the same list with `package_inbox`.
 *
 * Each is one row: its title and who sent it (the sender's words, drawn as
 * text), the Work item it is for, how many of its questions are still
 * unanswered and where it stands, and Open, his click, which goes through
 * the inbox's one Open (`_open_inbox_package`, as the brain's
 * `open_inbox_package` does): a page package shows in the centre, any
 * other is in JARVIS's hands and the row says so. A package closed
 * (signed off, set aside, replaced or withdrawn by its sender) leaves the
 * list.
 *
 * Nothing here opens, scrolls or moves focus when a package arrives. Each
 * row is a target the brain can point at; pointing never opens it.
 */

import { useState, useSyncExternalStore } from "react";
import { SaidText, useT, type Said } from "./locale";
import { openInbox, openPackages, type InboxEntry } from "./packageInbox";
import { useUiTarget } from "./uiRegistry";
import { RIGHT_SOCKET, row } from "./uiRight";

type PartRef = (el: Element | null) => void;

export function InboxPackages({ part }: { part: PartRef }) {
  const t = useT();
  const live = useSyncExternalStore(openPackages.subscribe, openPackages.get);
  const rows = live.value ?? [];
  if (!rows.length && !(live.error && !live.value)) return null;
  return (
    <section className="queue-section inbox-packages" aria-labelledby="inbox-packages-heading" ref={part}>
      <h3 className="queue-heading" id="inbox-packages-heading">
        {t("right.inbox.packages")} <span className="queue-count">{rows.length}</span>
      </h3>
      {live.error && !live.value && (
        <p className="read-failure" role="status">{t("right.inbox.packagesUnreadable", { error: live.error })}</p>
      )}
      <ul className="queue-list" data-list="packages">
        {rows.map((e) => <PackageLine key={e.id} entry={e} />)}
      </ul>
    </section>
  );
}

/** One open package: what it is, from whom, for what, and Open. */
function PackageLine({ entry }: { entry: InboxEntry }) {
  const t = useT();
  const [busy, setBusy] = useState(false);
  const [said, setSaid] = useState<Said | null>(null);
  const ui = useUiTarget({ ref: { region: "right", view: "right.queue", kind: "inboxPackage", key: entry.id },
                           label: entry.title || entry.name, socket: RIGHT_SOCKET });
  const where = entry.decisions_total
    ? t("right.inbox.package.unanswered", { count: entry.waiting, total: entry.decisions_total })
    : "";
  return (
    <li className="queue-item" data-inbox-id={entry.id}>
      <div className="queue-row inbox-package-row" ref={row(ui)}>
        <span className="row-who"><span className="row-person">{entry.sender || t("right.inbox.package.unknownSender")}</span></span>
        <span className="row-subject">{entry.title || entry.name}</span>
        <span className="row-side">
          {[t(`right.inbox.package.state.${entry.state}`), where,
            entry.work_id ? t("right.inbox.package.forWork", { work: entry.work_id }) : ""]
            .filter(Boolean).join(" · ")}
        </span>
        <span className="row-strip">
          <button type="button" className="settings-btn" data-ui-action="domain" disabled={busy}
                  ref={ui.part("open")}
                  onClick={() => {
                    setBusy(true);
                    void openInbox(entry.id).then((why) => { setBusy(false); setSaid(why); });
                  }}>{t("right.inbox.package.open")}</button>
        </span>
        {said && <span className="row-delivery"><SaidText said={said} className="inbox-package-said" /></span>}
      </div>
    </li>
  );
}
