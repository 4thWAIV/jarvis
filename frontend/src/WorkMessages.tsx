/**
 * A Work item's Messages tab, its first: every message about the item, in
 * time order, as one conversation (messagesToHim.ts `readConversation`),
 * his own replies in it. Each message is a MessageCard (who, when, NEW,
 * what it says, the package it carries with Open, Read, Reply).
 *
 * A decision is a message carrying its package: Open shows its background
 * and the vote in the centre, and what he chose is recorded under it ("You
 * chose: ..."), read from the package's own record (the item's packages,
 * GET /api/work/{id}). A Work decision asked the old way (`work_report.py
 * decision`, until every agent sends decisions as packages) is answered
 * where its message is, with the same card and call as before
 * (`answerWorkDecision`, the brain's answer_work_decision); one asked
 * before it had a message is answered above the conversation.
 *
 * Read again on every `inbox` hint (a message arrived or moved, or he
 * answered one) and whenever the item's record changes.
 */

import { useState } from "react";
import { DecisionCard } from "./DecisionCard";
import { inboxChanged, markMessage, readConversation, type ToHim } from "./messagesToHim";
import { useLiveRead } from "./liveRead";
import { SaidText, useT, type Said } from "./locale";
import { MessageCard } from "./MessageCard";
import { openPackagePage } from "./packages";
import type { InboxDetail, InboxQuestion } from "./packageInbox";
import { useUiTarget } from "./uiRegistry";
import { CENTRE_SOCKET } from "./ConversationView";
import { answerWorkDecision, workChanged, type WorkAnswered, type WorkDecision, type WorkItem } from "./work";

type Part = (name: string) => (el: Element | null) => void;

/** The Work decision a message is, by its source ("<item>:<key>"), or null. */
const decisionKey = (m: ToHim) =>
  m.source?.kind === "work_decision" && m.source.id ? m.source.id.split(":").slice(1).join(":") : null;

export function WorkMessages({ item, packages, part }: { item: WorkItem; packages: InboxDetail[]; part: Part }) {
  const t = useT();
  const live = useLiveRead(() => readConversation(item.id), [inboxChanged, workChanged]);
  const messages = live.value?.messages ?? [];
  const fresh = messages.filter((m) => m.state === "new");
  const [problem, setProblem] = useState<string | null>(null);
  const markAll = () => void Promise.all(fresh.map((m) => markMessage(m.id, "read")))
    .then((why) => setProblem(why.find((w) => w) ?? null));
  // An open decision with no message on record (asked before messages had
  // one) is still his to answer here, above the conversation.
  const asked = new Set(messages.map(decisionKey));
  const earlier = item.decisions.filter((d) => !asked.has(d.key));
  return (
    <section className="work-messages" ref={part("messages")}>
      {live.error && !live.value && <p className="read-failure">{t("main.messages.unreadable", { error: live.error })}</p>}
      {fresh.length > 0 && (
        <p className="work-messages-head">
          <button type="button" className="settings-btn" data-ui-action="domain" onClick={markAll}>
            {t("main.messages.markAllRead")}</button>
          {problem && <span className="file-state">{problem}</span>}
        </p>
      )}
      {earlier.length > 0 && (
        <div className="work-messages-earlier">
          <h3 className="work-section-head">{t("main.messages.earlier")}</h3>
          {earlier.map((d) => <Decision key={d.key} itemId={item.id} decision={d} />)}
        </div>
      )}
      {live.value && messages.length === 0
        ? earlier.length === 0 && <p className="workflow-empty">{t("main.messages.none")}</p>
        : <ol className="work-thread">
            {messages.map((m) => (
              <MessageCard key={m.id} message={m}>
                <Recorded item={item} message={m} packages={packages} />
              </MessageCard>
            ))}
          </ol>}
    </section>
  );
}

/** What is recorded under a message: his answers to the package it
 *  carries, or the Work decision it is (open: answered here). */
function Recorded({ item, message: m, packages }: { item: WorkItem; message: ToHim; packages: InboxDetail[] }) {
  const t = useT();
  const key = decisionKey(m);
  if (key !== null) {
    const open = item.decisions.find((d) => d.key === key);
    if (open) return <Decision itemId={item.id} decision={open} />;
    const answered = item.answered.find((a) => a.key === key);
    return answered ? <p className="message-chose">{answeredWords(answered, t)}</p> : null;
  }
  const detail = m.carries ? packages.find((p) => p.inbox.id === m.carries) : undefined;
  const chosen = (detail?.questions ?? []).filter((q) => q.answer);
  if (chosen.length === 0) return null;
  return (
    <ul className="message-chose-list">
      {chosen.map((q) => (
        <li key={q.id} className="message-chose" data-key={q.id}>
          {chosen.length > 1 && <span className="message-chose-question">{q.question} </span>}
          {t("main.messages.chose", { answer: answerWords(q, t) })}
        </li>
      ))}
    </ul>
  );
}

/** A Work decision's recorded end, in words: what he chose, or how it closed. */
function answeredWords(a: WorkAnswered, say: ReturnType<typeof useT>): string {
  return a.where !== "page" && a.where !== "voice"
    ? say(`main.work.answeredBy.${a.where}`, { answer: a.answer })
    : say("main.messages.chose", { answer: a.answer });
}

/** A package's recorded answer in words: the option's name, or Decide later / None of these. */
function answerWords(q: InboxQuestion, say: ReturnType<typeof useT>): string {
  const a = q.answer!;
  const name = a.choice || q.options.find((o) => o.id === a.answer)?.name
    || (a.answer === "later" || a.answer === "none" ? say(`main.work.packageSpecial.${a.answer}`) : a.answer);
  return a.note ? `${name} (${a.note})` : name;
}

function Decision({ itemId, decision: d }: { itemId: string; decision: WorkDecision }) {
  const t = useT();
  const ui = useUiTarget({ ref: { region: "main", view: "main.work", kind: "workDecision", key: `${itemId}/${d.key}` },
                           label: t("main.work.decision"), socket: CENTRE_SOCKET });
  return (
    <ul className="workflow-decisions-list">
      <DecisionCard question={d.question} options={d.options} recommendation={d.recommendation || null}
                    ui={ui} dataKey={d.key} answer={(value) => answerWorkDecision(itemId, d.key, value)}
                    more={d.package ? <OpenPackage reference={d.package} target={ui.part("package")} /> : null} />
    </ul>
  );
}

/** The decision's package, opened on his click through the server's one
 *  way to open a page package (the brain's open_package_page too). */
function OpenPackage({ reference, target }: { reference: string; target: (el: Element | null) => void }) {
  const t = useT();
  const [problem, setProblem] = useState<Said | null>(null);
  const [busy, setBusy] = useState(false);
  return (
    <p className="work-decision-package">
      <button type="button" className="file-action" disabled={busy} ref={target}
              data-ui-command="package.open" data-ui-args={JSON.stringify({ packageId: reference })}
              onClick={() => { setBusy(true); void openPackagePage(reference).then((why) => { setProblem(why); setBusy(false); }); }}>
        {t("main.work.openPackage")}
      </button>{" "}
      <code>{reference}</code>
      {problem && <SaidText said={problem} className="file-state" />}
    </p>
  );
}
