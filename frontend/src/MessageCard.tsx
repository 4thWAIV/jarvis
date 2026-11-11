/**
 * One message to him, drawn the same wherever it is shown: in its Work
 * item's Messages tab (WorkMessages.tsx) and, when it belongs to no item, in
 * the Inbox (InboxMessages.tsx).
 *
 *   - who sent it (its proven sender's one name; a subagent as "name
 *     (parent)"), or "You" for what he sent, when, and NEW while it is new;
 *   - what it says, as text;
 *   - the package it carries, as a card: its title, where it stands ("1 of
 *     2 answered") and Open (the inbox's one Open, his click only: a page
 *     package is shown in the centre, packageInbox.ts `openInbox`);
 *   - anything the caller adds under it (his recorded answers, an open
 *     Work decision);
 *   - Read (JARVIS reads it aloud; it is read once heard in full), Reply (a
 *     capture to its proven sender, read back and sent on his yes) and Mark
 *     read while it is new; none on what he sent.
 *
 * Every word in it is its sender's, drawn as text.
 */

import { useState, type ReactNode } from "react";
import { carriedProgress, markMessage, messageAct, type ToHim } from "./messagesToHim";
import { formatDateTime, SaidText, useT, type Said } from "./locale";
import { openInbox } from "./packageInbox";
import type { bindingProps } from "./uiNavigation";

type ElementRef = (el: Element | null) => void;
const none: ElementRef = () => {};

export function PackageMark() {
  return (
    <svg className="package-mark" viewBox="0 0 16 16" width="14" height="14" aria-hidden="true">
      <path d="M8 1.5 14 4.5v7L8 14.5 2 11.5v-7Z M2 4.5 8 7.5l6-3 M8 7.5v7" fill="none"
            stroke="currentColor" strokeWidth="1.3" strokeLinejoin="round" />
    </svg>
  );
}

/** Minimise or bring back a list of messages: the page's disclosure mark
 *  (▾ shown, ▸ minimised, as Work's History, Workflows' Done and the
 *  Queue's Dismissed), alone, its words its accessible name and title. */
export function MinimiseToggle({ shown, hideLabel, showLabel, binding, part }: {
  shown: boolean; hideLabel: string; showLabel: string;
  binding: ReturnType<typeof bindingProps>; part?: ElementRef;
}) {
  const label = shown ? hideLabel : showLabel;
  return (
    <button type="button" className="minimise-toggle" aria-expanded={shown} aria-label={label}
            title={label} ref={part} {...binding}>
      <span aria-hidden="true">{shown ? "▾" : "▸"}</span>
    </button>
  );
}

/** Who a message is from, in words. */
export function senderWords(m: ToHim, say: ReturnType<typeof useT>): string {
  if (m.from.sender === "tim") return say("main.messages.you");
  const name = m.from.name || say("main.updates.someone");
  return m.from.subagent ? say("main.messages.subagent", { name: m.from.subagent, parent: name }) : name;
}

export function MessageCard({ message: m, children, ui }: {
  message: ToHim;
  /** Under what it says: his answers, an open decision. */
  children?: ReactNode;
  /** Its registered target's parts, where it is one (the Inbox's rows). */
  ui?: { whole: ElementRef; part: (name: string) => ElementRef };
}) {
  const t = useT();
  const [said, setSaid] = useState<Said | null>(null);
  const part = ui?.part ?? (() => none);
  const mine = m.from.sender === "tim";
  const act = (action: "read" | "reply") => void messageAct(m.id, action).then((w) => setSaid({ original: w }));
  return (
    <li className={`message-card${mine ? " mine" : ""}`} data-message-id={m.id} data-state={m.state ?? undefined}
        ref={(el) => { ui?.whole(el); part("row")(el); }}>
      <p className="message-head">
        <span className="message-who" ref={part("sender")}>{senderWords(m, t)}</span>
        <span className="message-at">{formatDateTime(m.at * 1000,
          { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" })}</span>
        {m.state === "new" && <span className="message-new">{t("main.messages.new")}</span>}
      </p>
      <p className="message-says" ref={part("says")}>{m.says}</p>
      {m.package && (
        <div className="message-package" data-inbox-id={m.package.inbox} ref={part("package")}>
          <PackageMark />
          <span className="message-package-title">{m.package.title}</span>
          <span className="package-progress">{carriedProgress(m.package)}</span>
          <button type="button" className="file-action" data-ui-action="domain" ref={part("open")}
                  onClick={() => void openInbox(m.package!.inbox).then(setSaid)}>
            {t("main.messages.open")}</button>
        </div>
      )}
      {children}
      {!mine && m.message && (
        <p className="message-actions">
          <button type="button" className="settings-btn" data-ui-action="domain" ref={part("read")}
                  onClick={() => act("read")}>{t("main.updates.read")}</button>
          <button type="button" className="settings-btn" data-ui-action="domain" ref={part("reply")}
                  onClick={() => act("reply")}>{t("main.updates.reply")}</button>
          {m.state === "new" && (
            <button type="button" className="settings-btn" data-ui-action="domain"
                    onClick={() => void markMessage(m.id, "read").then((why) => setSaid(why ? { original: why } : null))}>
              {t("main.messages.markRead")}</button>
          )}
        </p>
      )}
      {said && <SaidText said={said} className="file-state" />}
    </li>
  );
}
