/**
 * The Queue's Packages section, at the top of the Queue tab: the package
 * inbox (packageInbox.ts), one row per open package, newest first. A row is
 * a small package mark, who sent it, its title, where it stands ("2 of 3
 * answered") and its buttons: Open, Done (sign it off: packageInbox.ts
 * `signOff`, the same as the view's Done and the brain's sign_off_package)
 * and Dismiss (set it aside, nothing sent back). The closed
 * ones are behind a collapsed Closed, read when it is opened.
 *
 * It is shown whether or not this JARVIS is signed in to a team server:
 * packages come from agents on this Mac, not from people. Nothing here
 * opens, scrolls or moves focus when a package arrives; Open is his click.
 * Each row is a target the brain can point at (`inboxPackage`); pointing at
 * one never opens it.
 *
 * The title and the sender's name are the sender's words, drawn as text.
 */

import { useState, useSyncExternalStore } from "react";
import { SaidText, useT, type Said } from "./locale";
import {
  closedInbox, closeInbox, inbox, inboxProgress, openInbox, signOff, type InboxEntry,
} from "./packageInbox";
import { createStore } from "./store";
import { RIGHT_SOCKET } from "./uiRight";
import { useUiTarget } from "./uiRegistry";

type PartRef = (el: Element | null) => void;

/** Whether Closed is open; kept while the tab is switched. */
const closedOpen = createStore(false);

export function PackageMark() {
  return (
    <svg className="package-mark" viewBox="0 0 16 16" width="14" height="14" aria-hidden="true">
      <path d="M8 1.5 14 4.5v7L8 14.5 2 11.5v-7Z M2 4.5 8 7.5l6-3 M8 7.5v7" fill="none"
            stroke="currentColor" strokeWidth="1.3" strokeLinejoin="round" />
    </svg>
  );
}

export function QueuePackages({ part, closedPart }: { part: PartRef; closedPart: PartRef }) {
  const t = useT();
  const live = useSyncExternalStore(inbox.subscribe, inbox.get);
  const rows = live.value?.packages ?? [];
  const closedCount = live.value?.closed_count ?? 0;
  if (!live.value && !live.error) return null;
  return (
    <section className="queue-section queue-packages" aria-labelledby="queue-packages-heading" ref={part}>
      <h3 className="queue-heading" id="queue-packages-heading">
        <PackageMark /> {t("right.packages.heading")} <span className="queue-count">{rows.length}</span>
      </h3>
      {live.error && <p className="read-failure" role="status">{t("right.packages.unreadable", { error: live.error })}</p>}
      {rows.length > 0 && (
        <ul className="queue-list" data-list="packages">
          {rows.map((e) => <PackageRow key={e.id} entry={e} />)}
        </ul>
      )}
      {closedCount > 0 && <Closed count={closedCount} part={closedPart} />}
    </section>
  );
}

function PackageRow({ entry }: { entry: InboxEntry }) {
  const t = useT();
  const [said, setSaid] = useState<Said | null>(null);
  const [busy, setBusy] = useState(false);
  const ui = useUiTarget({ ref: { region: "right", view: "right.queue", kind: "inboxPackage", key: entry.id },
                           label: `${entry.sender}: ${entry.title}`, socket: RIGHT_SOCKET });
  const act = (work: () => Promise<Said | null>) => {
    if (busy) return;
    setBusy(true);
    setSaid(null);
    void work().then((why) => { setSaid(why); setBusy(false); });
  };
  return (
    <li className={`queue-item package-row state-${entry.state}`} data-inbox-id={entry.id}
        ref={(el) => { ui.whole(el); ui.part("row")(el); }}>
      <div className="package-row-head">
        <PackageMark />
        <span className="row-person" ref={ui.part("sender")}>{entry.sender || t("right.packages.unknownSender")}</span>
        <span className="package-progress" data-waiting={entry.waiting > 0 || undefined}
              ref={ui.part("progress")}>{inboxProgress(entry)}</span>
      </div>
      <p className="package-title" ref={ui.part("title")}>{entry.title}</p>
      {entry.unproven_claim && <p className="file-state package-unproven" ref={ui.part("unproven")}>
        {t("right.packages.unprovenClaim")}</p>}
      <div className="package-actions">
        <button type="button" className="file-action" data-ui-action="domain" disabled={busy}
                ref={ui.part("open")} onClick={() => act(() => openInbox(entry.id))}>
          {t("right.packages.open")}</button>
        <button type="button" className="file-action primary" data-ui-action="domain" disabled={busy}
                ref={ui.part("done")} title={t("right.packages.doneTitle")}
                onClick={() => act(() => signOff(entry.package_id))}>
          {t("right.packages.done")}</button>
        <button type="button" className="file-action" data-ui-action="domain" disabled={busy}
                ref={ui.part("close")} onClick={() => act(() => closeInbox(entry.id))}>
          {t("right.packages.dismiss")}</button>
      </div>
      {said && <SaidText said={said} className="file-state package-said" />}
    </li>
  );
}

function Closed({ count, part }: { count: number; part: PartRef }) {
  const t = useT();
  const shown = useSyncExternalStore(closedOpen.subscribe, closedOpen.get);
  const live = useSyncExternalStore(closedInbox.subscribe, closedInbox.get);
  const rows = shown ? live.value?.packages ?? [] : [];
  return (
    <div className="queue-history" ref={part}>
      <button type="button" className="queue-history-toggle" aria-expanded={shown} data-ui-action="domain"
              onClick={() => closedOpen.set(!shown)}>
        {shown ? "▾" : "▸"} {t("right.packages.closedCount", { count })}
      </button>
      {shown && (
        <ul className="queue-list done" data-list="closed-packages">
          {rows.map((e) => (
            <li key={e.id} className="queue-item package-closed-row" data-inbox-id={e.id}>
              <span className="row-person">{e.sender}</span>
              <span className="row-subject">{e.title}</span>
              <span className="package-progress">{inboxProgress(e)}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
