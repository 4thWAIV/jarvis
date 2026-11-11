import { WorkOwnerActivity } from "./WorkerStatus";
/**
 * One work item, in full: the centre area's Work view, opened by clicking
 * its row in the right panel's Work tab (`work.open`), which stays in view;
 * an item in History opens the same way.
 *
 *   - the head: its title, its state, "In History" when it is, and Close
 *     (`work.close`, back to Conversation);
 *   - while a decision waits on him, a "Decision needed" banner above every
 *     tab, which opens the Messages tab (`work.tab.select`);
 *   - the tabs (`work.tab.select`, `WORK_TABS`), Messages and Blocking
 *     counted and lit while they have something:
 *       Messages (first): the item's conversation, oldest first, his
 *         replies in it; a decision is a message carrying its package,
 *         opened from there, his answer recorded under it
 *         (WorkMessages.tsx); counted by its new messages and open
 *         decisions;
 *       Overview: its latest update ("No recent update" once it has gone
 *         quiet), its source (a stat; nothing is opened or run), its area,
 *         and who is on it;
 *       Blocking: everything holding it up, in the server's order;
 *       Pieces: in dependency order, each with exactly who is on it, the
 *         reporting agent's own worker label beside that (quoted, marked as
 *         its words), a mark while blocked or waiting on him, its latest
 *         note, what it waits for and its evidence (a URL as a link, a
 *         commit or path as text to copy);
 *       Reports: its latest reports, newest last.
 *
 * Each piece shows its live activity (nodeActivity.tsx): a travelling glow
 * along its edge while it is working and reporting, "Paused" once quiet, a
 * check once done, one flash for each report on it, and its "waits for"
 * line flowing while a piece it waits for is working. The latest update
 * flashes for each report on the item.
 *
 * Everything here was written by the agents reporting the item, and is
 * drawn as plain text.
 */

import { useEffect, useLayoutEffect, useRef, useState, useSyncExternalStore, type ReactNode } from "react";
import { copyPath } from "./attachmentLocation";
import { CENTRE_SOCKET } from "./ConversationView";
import { inboxMessages, threadOf } from "./messagesToHim";
import { useLiveRead } from "./liveRead";
import { formatDateTime, SaidText, useT, type Said } from "./locale";
import { revokeAgentKey } from "./agents";
import { openInbox } from "./packageInbox";
import { TabRow, type Tab } from "./Tabs";
import { bind, bindingProps } from "./uiNavigation";
import { useUiTarget } from "./uiRegistry";
import { TakeoverButton } from "./TakeoverBar";
import { WorkMessages } from "./WorkMessages";
import { blockerWords, followUpWords, liveWords, needWords, packageReviewWords,
         rowState, rowStateWords, reviewWork, type ReviewAction, type WorkPackageReview, getWorkItem, pieceMark, STATE_WORDS, whoWords, WORK_TABS,
         workChanged, workItemFrame, workTab, type WorkBlocker, type WorkEvent,
         type WorkEvidence, type WorkItem, type WorkPiece, type WorkTab } from "./work";
import { activityOf, ActivityMark, ShownSince, useArrivalFlash, useShownSince,
         workStanding, type Activity } from "./nodeActivity";
import { WorkMarkDot } from "./WorkPanel";

type WorkKind = "workItem" | "workPiece" | "workBlocker";

function useWorkTarget(kind: WorkKind, key: string, label: string) {
  return useUiTarget({ ref: { region: "main", view: "main.work", kind, key }, label, socket: CENTRE_SOCKET });
}

type Part = (name: string) => (el: Element | null) => void;

export function WorkDetail({ id }: { id: string }) {
  const t = useT();
  // The item, its events and its packages, read once and again only when
  // its record or its packages change; the item itself from every frame
  // the server sends for it (work.ts), with no read.
  const live = useLiveRead(() => getWorkItem(id), [workChanged]);
  const framed = useFramedItem(id, live.value, live.refresh);
  // The keys are the detail's alone (not in the list's item): kept from the read.
  const item = framed && (!live.value || framed.revision > live.value.revision)
    ? { ...framed.item, work_keys: live.value?.item.work_keys } : live.value?.item;
  const events = live.value?.events ?? [];
  const ui = useWorkTarget("workItem", id, item?.title ?? t("tabs.main.work"));
  const shownTab = useSyncExternalStore(workTab.subscribe, workTab.get);
  const tab = shownTab.itemId === id ? shownTab.tab : "messages";
  const packages = live.value?.packages ?? [];
  const asked = packages.flatMap((p) => p.questions.filter((q) => q.applies && !q.answer)
    .map((q) => ({ question: q.question })));
  const deciding = item ? item.decisions.length + asked.length : 0;
  // The Messages tab counts its new messages and the decisions still open.
  const inbox = useSyncExternalStore(inboxMessages.subscribe, inboxMessages.get);
  const waiting = (threadOf(inbox.value, id)?.new ?? 0) + deciding;
  const views: Record<WorkTab, (item: WorkItem) => ReactNode> = {
    messages: (i) => <WorkMessages key={i.id} item={i} packages={packages} part={ui.part} />,
    overview: (i) => <Overview item={i} part={ui.part} />,
    blocking: (i) => <Blocking item={i} part={ui.part} />,
    pieces: (i) => <Pieces item={i} part={ui.part} />,
    reports: () => <Reports events={events} part={ui.part} />,
  };
  const tabs: Tab[] = item ? WORK_TABS.map((name) => ({
    id: name, icon: null, view: () => views[name](item),
    title: name === "messages" && waiting ? t("main.work.tab.messagesCount", { count: waiting })
      : name === "blocking" && item.blockers.length ? t("main.work.tab.blockingCount", { count: item.blockers.length })
      : t(`main.work.tab.${name}`),
  })) : [];
  const selected = tabs.find((x) => x.id === tab) ?? tabs[0];
  const row = useRef<HTMLDivElement | null>(null);
  useTabParts(row, id, ui.part, tabs.length > 0);
  const since = useShownSince(id);
  return (
    <ShownSince.Provider value={since}>
    <div id="work-detail" ref={ui.whole}>
      <div className="work-detail-head" ref={ui.part("header")}>
        <span className="work-detail-name">{item?.title ?? t("tabs.main.work")}</span>
        {item && <span className={`work-state state-${rowState(item)}`} ref={ui.part("state")}>
          {rowStateWords(item, t)}</span>}
          {item?.live
            ? <small className="work-activity-reason work-live-reason">{liveWords(item.live)}</small>
            : item?.state === "activity_unknown" && <small className="work-activity-reason">{t("right.work.activityUnknownReason")}</small>}
        {item?.history && <span className="work-history-pill">{t("main.work.inHistory")}</span>}
        <TakeoverButton partRef={ui.part("takeover")} />
        <button type="button" className="workflow-close" title={t("main.common.close")}
                aria-label={t("main.common.close")} ref={ui.part("close")}
                {...bindingProps(bind("work.close", {}))}>×</button>
      </div>
      {item && <NeedsYou item={item} target={ui.part("needs")} />}
      {item && !item.closed_at && <Review item={item} target={ui.part("review")} />}
      {live.error && <p className="read-failure">{t("main.work.unreadable", { error: live.error })}</p>}
      {item && deciding > 0 && (
        <button type="button" className="work-decision-banner" ref={ui.part("banner")}
                {...bindingProps(bind("work.tab.select", { itemId: id, tab: "messages" }))}>
          <strong>{t("main.work.banner.title", { count: deciding })}</strong>
          <span className="work-decision-banner-question">
            {(item.decisions[0] ?? asked[0]).question}</span>
          <span className="work-decision-banner-go">{t("main.work.banner.go")}</span>
        </button>
      )}
      {item && selected && <>
        <div ref={row} style={{ flex: "none" }}>
          <TabRow tabs={tabs} selected={selected.id} className="workflow-tabs work-tabs" words box="main.work.tabs"
                  alert={(x) => (x.id === "messages" && waiting > 0) || (x.id === "blocking" && item.blockers.length > 0)}
                  binding={(x) => bind("work.tab.select", { itemId: id, tab: x.id as WorkTab })} />
        </div>
        <div className="work-detail-body" role="tabpanel" aria-label={selected.title}>
          {selected.view()}
        </div>
      </>}
    </div>
    </ShownSince.Provider>
  );
}

/** The latest frame for this item, while it is newer than what was read.
 *  A frame that moves its record (its revision) or its packages' questions
 *  reads its events and packages again; a change of its live owners alone
 *  reads nothing. */
function useFramedItem(id: string, read: { item: WorkItem; revision: number } | null,
                       readAgain: () => void) {
  const [framed, setFramed] = useState<{ item: WorkItem; revision: number } | null>(null);
  const shown = useRef<WorkItem | null>(null);
  shown.current = framed && (!read || framed.revision > read.revision) ? framed.item : read?.item ?? null;
  useEffect(() => {
    setFramed(null);
    return workItemFrame.subscribe(() => {
      const frame = workItemFrame.get();
      if (!frame || frame.item.id !== id) return;
      const before = shown.current;
      setFramed(frame);
      if (!before || before.revision !== frame.item.revision
          || before.package_questions !== frame.item.package_questions) readAgain();
    });
  }, [id, readAgain]);
  return framed?.item.id === id ? framed : null;
}

/** Each tab's button as the item's `tab.<id>` part, found by the command
 *  and arguments it runs, never by its words or its place. */
function useTabParts(row: { current: HTMLDivElement | null }, itemId: string, part: Part, drawn: boolean) {
  useLayoutEffect(() => {
    const buttons = [...(row.current?.querySelectorAll<HTMLElement>('[data-ui-command="work.tab.select"]') ?? [])];
    for (const tab of WORK_TABS) {
      part(`tab.${tab}`)(buttons.find((b) => {
        const args = JSON.parse(b.dataset.uiArgs ?? "{}") as { itemId?: string; tab?: string };
        return args.itemId === itemId && args.tab === tab;
      }) ?? null);
    }
    return () => WORK_TABS.forEach((tab) => part(`tab.${tab}`)(null));
  }, [row, itemId, part, drawn]);
}

/** His review of the item, above every tab: Approve (done is his alone to
 *  give), Not done yet (back to working, with his note for its agents) and
 *  Add work (a new piece in his words). The same call as review_work. */
function Review({ item, target }: { item: WorkItem; target: (el: Element | null) => void }) {
  const t = useT();
  const [asking, setAsking] = useState<ReviewAction | null>(null);
  const [words, setWords] = useState("");
  const [problem, setProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const act = (action: ReviewAction, said = "") => {
    setBusy(true);
    void reviewWork(item.id, action, said).then((why) => {
      setBusy(false);
      setProblem(why);
      if (!why) { setAsking(null); setWords(""); }
    });
  };
  return (
    <div className="work-review" ref={target}>
      {asking === null ? <>
        {item.approvable && <button type="button" className="settings-btn work-approve" data-ui-action="domain"
                disabled={busy} onClick={() => act("approve")}>{t("main.work.review.approve")}</button>}
        <button type="button" className="settings-btn" data-ui-action="domain" disabled={busy}
                onClick={() => setAsking("not_done")}>{t("main.work.review.notDone")}</button>
        <button type="button" className="settings-btn" data-ui-action="domain" disabled={busy}
                onClick={() => setAsking("add_work")}>{t("main.work.review.addWork")}</button>
      </> : <form data-ui-action="domain" onSubmit={(e) => { e.preventDefault(); if (words.trim() || asking === "not_done") act(asking, words); }}>
        <input data-ui-action="input" type="text" value={words} autoFocus aria-label={t(`main.work.review.${asking}`)}
               placeholder={t(`main.work.review.${asking}`)} onChange={(e) => setWords(e.target.value)} />
        <button type="submit" className="settings-btn" data-ui-action="domain"
                disabled={busy || (!words.trim() && asking !== "not_done")}>{t("main.work.review.send")}</button>
        <button type="button" className="settings-btn" data-ui-action="domain"
                onClick={() => { setAsking(null); setWords(""); setProblem(null); }}>{t("main.common.cancel")}</button>
      </form>}
      {problem && <span className="file-state">{problem}</span>}
    </div>
  );
}

/** Every act he is needed for, the first first, each with the way to do
 *  it: a decision opens Messages, a package opens it (his click), his
 *  review is the header's own buttons. Or that nobody is working on it. */
function NeedsYou({ item, target }: { item: WorkItem; target: (el: Element | null) => void }) {
  const t = useT();
  const [problem, setProblem] = useState<Said | null>(null);
  const needs = item.needs_you ?? [];
  if (item.stalled) {
    return <p className="work-needs work-stalled" ref={target}>{t("right.work.stalled")}</p>;
  }
  if (needs.length === 0) return null;
  return (
    <div className="work-needs" ref={target}>
      <strong>{t("main.work.needsYouTitle")}</strong>
      <ol>
        {needs.map((need, n) => (
          <li key={`${need.kind}:${need.key ?? need.inbox ?? n}`} data-need={need.kind}>
            {needWords(need)}{" "}
            {need.kind === "decision" && (
              <button type="button" className="file-action"
                      {...bindingProps(bind("work.tab.select", { itemId: item.id, tab: "messages" }))}>
                {t("main.work.need.go")}</button>
            )}
            {(need.kind === "package" || need.kind === "review_package") && need.inbox && (
              <button type="button" className="file-action" data-ui-action="domain"
                      onClick={() => void openInbox(need.inbox!).then(setProblem)}>
                {t("main.work.openPackage")}</button>
            )}
          </li>
        ))}
      </ol>
      {problem && <SaidText said={problem} className="file-state" />}
    </div>
  );
}

/** What his act in a review package did, with when: on the Overview, so
 *  every act of his shows a change. */
function PackageReviewLine({ review }: { review: WorkPackageReview }) {
  return <p className="work-package-review" data-outcome={review.outcome}>{packageReviewWords(review)}</p>;
}

/** Its state, latest update, source and area, and who is on it. */
function Overview({ item, part }: { item: WorkItem; part: Part }) {
  const t = useT();
  const who = [...new Map(item.pieces.map((p) => {
    const words = whoWords(p.who) + (p.worker ? ` ${t("main.work.worker", { label: p.worker })}` : "");
    return [words, words];
  })).values()];
  const arrived = useArrivalFlash(item.updated_at);
  return (
    <>
      {item.approved_at ? <p className="work-approved">
        {t("main.work.review.approved", { when: formatDateTime(item.approved_at * 1000, { dateStyle: "medium", timeStyle: "short" }) })}</p> : null}
      {item.package_review && <PackageReviewLine review={item.package_review} />}
      {item.review_note && <p className="work-review-note">
        {t("main.work.review.note", { note: item.review_note })}</p>}
      <p className="work-latest" ref={(el) => { part("update")(el); arrived(el); }}>
        {(item.also_waiting ?? 0) > 0 && <span className="work-follow-up">{t("right.work.alsoWaiting", { count: item.also_waiting })} · </span>}
        {item.follow_up && <span className="work-follow-up">{followUpWords(item.follow_up)} · </span>}
        {item.quiet && <span className="work-quiet">{t("right.work.quiet")} · </span>}
        {item.latest_update}
      </p>
      <p className="work-source" ref={part("source")}>
        <span>{t(`main.work.source.${item.source_kind}`)}</span>{" "}
        <code className="work-path">{item.source_path}</code>{" "}
        <span className={`work-source-${item.source}`}>{t(`main.work.found.${item.source}`)}</span>
        {item.area && <> · <span className="work-area">{t("main.work.area", { area: item.area })}</span></>}
      </p>
      {(item.projects ?? []).length > 0 && (
        <p className="work-projects" ref={part("project")}>
          {item.projects.map((p) => (
            <button key={p.id} type="button" className="knowledge-open"
                    {...bindingProps(bind("project.open", { projectId: p.id }))}>
              {t("main.work.project", { name: p.name })}</button>
          ))}
        </p>
      )}
      {(item.work_keys ?? []).length > 0 && (
        <ul className="work-item-keys" ref={part("itemKeys")}>
          {item.work_keys!.map((k) => <ItemKey key={k.fingerprint} itemKey={k} />)}
        </ul>
      )}
      <section ref={part("who")}>
        <h3 className="work-section-head">{t("main.work.who")}</h3>
        {who.length === 0 ? <p className="workflow-empty">{t("main.work.noPieces")}</p>
          : <ul className="work-who">{who.map((w) => <li key={w}>{w}</li>)}</ul>}
      </section>
    </>
  );
}

/** One work-item key (made by `make_package --work` or `agent_key grant`):
 *  who issued it, when it ends, and his Revoke. The detail is read again
 *  on the "work" hint every key change sends. */
function ItemKey({ itemKey }: { itemKey: NonNullable<WorkItem["work_keys"]>[number] }) {
  const t = useT();
  const [failed, setFailed] = useState<string | null>(null);
  return (
    <li>
      {t("main.work.itemKey", { name: itemKey.issued_by || t("main.work.itemKeyNobody"),
                                 when: formatDateTime(itemKey.expires_at * 1000, { dateStyle: "medium" }) })}{" "}
      <code className="work-path">{itemKey.fingerprint}</code>{" "}
      <button data-ui-action="domain" type="button" className="thread-key-revoke"
              onClick={() => { setFailed(null); revokeAgentKey(itemKey.fingerprint).catch((e: Error) => setFailed(e.message)); }}>
        {t("main.thread.keyRevoke")}</button>
      {failed && <span className="read-failure"> {t("main.thread.keyFailed", { error: failed })}</span>}
    </li>
  );
}

/** Everything holding the item up, in the server's order. */
function Blocking({ item, part }: { item: WorkItem; part: Part }) {
  const t = useT();
  return (
    <section ref={part("blocking")}>
      <h3 className="work-section-head">{t("main.work.tab.blocking")}</h3>
      {item.blockers.length === 0
        ? <p className="workflow-empty">{t("main.work.nothingBlocking")}</p>
        : <ul className="work-blockers">
            {item.blockers.map((b) => <BlockerRow key={`${b.kind}:${b.key}`} item={item} blocker={b} />)}
          </ul>}
    </section>
  );
}

function BlockerRow({ item, blocker: b }: { item: WorkItem; blocker: WorkBlocker }) {
  const ui = useWorkTarget("workBlocker", `${item.id}/${b.kind}:${b.key}`, b.title);
  return (
    <li className="work-blocker" data-kind={b.kind} ref={(el) => { ui.whole(el); ui.part("row")(el); }}>
      {blockerWords(item, b)}
    </li>
  );
}

function Pieces({ item, part }: { item: WorkItem; part: Part }) {
  const t = useT();
  // Every piece's look from one clock, so a line knows whether the piece it
  // waits for is working.
  const now = 0; // Work activity is already computed by the worker authority.
  const looks = new Map(item.pieces.map((p) => [p.key, pieceActivity(p, now)]));
  return (
    <section ref={part("pieces")}>
      <h3 className="work-section-head">{t("main.work.pieces")}</h3>
      {item.pieces.length === 0
        ? <p className="workflow-empty">{t("main.work.noPieces")}</p>
        : <ol className="work-pieces">
            {item.pieces.map((p, step) => <Piece key={p.key} item={item} pieceKey={p.key} step={step + 1}
                                                  looks={looks} />)}
          </ol>}
    </section>
  );
}

function Reports({ events, part }: { events: WorkEvent[]; part: Part }) {
  const t = useT();
  return (
    <section ref={part("reports")}>
      <h3 className="work-section-head">{t("main.work.reports")}</h3>
      <ol className="work-events">
        {events.map((e) => (
          <li key={e.seq}>
            <span className="work-event-at">
              {formatDateTime(e.at * 1000, { hour: "2-digit", minute: "2-digit" })}</span>{" "}
            {e.summary}
            {e.reporter_name && <small className="work-event-reporter" title={t("main.work.reporterTitle")}>
              {" "}{t("main.work.reportedBy", { name: e.reporter_name })}</small>}
          </li>
        ))}
      </ol>
    </section>
  );
}

const pieceActivity = (p: WorkPiece, now: number) => activityOf(workStanding(p.state), p.quiet_at, now);

function Piece({ item, pieceKey, step, looks }: { item: WorkItem; pieceKey: string; step: number;
                                                 looks: Map<string, Activity | null> }) {
  const t = useT();
  const piece = item.pieces.find((p) => p.key === pieceKey)!;
  const ui = useWorkTarget("workPiece", `${item.id}/${piece.key}`, piece.title);
  const mark = pieceMark(piece);
  const activity = looks.get(piece.key) ?? null;
  const arrived = useArrivalFlash(piece.note_at);
  // The line to the pieces it waits for flows while one of them is working.
  const flowing = piece.waits_for.some((k) => looks.get(k) === "working");
  const titleOf = (key: string) => item.pieces.find((p) => p.key === key)?.title ?? key;
  return (
    <li className="work-piece" data-key={piece.key} data-mark={mark ?? undefined}
        data-activity={activity ?? undefined}
        ref={(el) => { ui.whole(el); ui.part("row")(el); arrived(el); }}>
      <div className="work-piece-head">
        <span className="work-piece-step">{step}.</span>
        <span className="work-piece-title">
          {mark && <WorkMarkDot mark={mark} target={ui.part("mark")} />}
          <ActivityMark activity={activity} target={ui.part("activity")} />
          {piece.title}</span>
        <span className="work-piece-who" data-who={piece.who.kind} ref={ui.part("who")}>
          <WorkOwnerActivity piece={piece} />{whoWords(piece.who)}
          {piece.worker && " "}
          {piece.worker && <span className="work-piece-worker" title={t("main.work.workerTitle")}
                                 ref={ui.part("worker")}>{t("main.work.worker", { label: piece.worker })}</span>}
          {piece.reporter_name && " "}
          {piece.reporter_name && <span className="work-piece-reporter" title={t("main.work.reporterTitle")}
                                        ref={ui.part("reporter")}>{t("main.work.reportedBy", { name: piece.reporter_name })}</span>}
        </span>
        <span className={`work-state state-${piece.stalled ? "stalled" : piece.state}`} ref={ui.part("state")}>
          {piece.stalled ? t("right.work.state.stalled") : STATE_WORDS[piece.state]}</span>
        {piece.state === "activity_unknown" && !piece.live && <small className="work-activity-reason">{t("right.work.activityUnknownReason")}</small>}
        {piece.live && (
          <p className="work-piece-live" data-live={piece.live.state}>
            {t(`main.work.live.${piece.live.state}`, { name: piece.live.name })}
            {piece.live.doing && <> · <span className="work-piece-doing">{piece.live.doing}</span></>}
          </p>
        )}
      </div>
      {piece.note && (
        <p className="work-piece-note" ref={ui.part("note")}>
          {piece.note_at !== null && <span className="work-event-at">
            {formatDateTime(piece.note_at * 1000, { hour: "2-digit", minute: "2-digit" })}</span>}{" "}
          {piece.note}
        </p>
      )}
      {piece.after.length > 0 && (
        <p className={`work-piece-after${piece.waits_for.length ? " waiting" : ""}`}
           data-flow={flowing ? "on" : undefined} ref={ui.part("after")}>
          {piece.waits_for.length
            ? t("main.work.waitsFor", { pieces: piece.waits_for.map(titleOf).join(", ") })
            : t("main.work.afterDone", { pieces: piece.after.map(titleOf).join(", ") })}
        </p>
      )}
      {(piece.state === "waiting_on_tim" || piece.state === "blocked") && ((piece.until ?? []).length > 0 || piece.unlinked) && (
        <p className="work-piece-until">
          {(piece.until ?? []).length ? t("main.work.waitsForAnswer", { link: piece.until.join(", ") }) : t("main.work.unlinked")}
        </p>
      )}
      {piece.evidence.length > 0 && (
        <ul className="work-evidence" ref={ui.part("evidence")}>
          {piece.evidence.map((e) => <Evidence key={`${e.kind}:${e.value}`} evidence={e} />)}
        </ul>
      )}
    </li>
  );
}

/** One piece of evidence: a URL as a link, a commit or a path as text with Copy. */
function Evidence({ evidence: e }: { evidence: WorkEvidence }) {
  const t = useT();
  const [copied, setCopied] = useState(false);
  const label = copied ? t("main.work.copied") : t("main.work.copy");
  return (
    <li className="work-evidence-item" data-kind={e.kind}>
      <span className="work-evidence-kind">{t(`main.work.evidence.${e.kind}`)}</span>
      {e.kind === "url" && /^https?:\/\//i.test(e.value)
        ? <a className="work-evidence-value" href={e.value} target="_blank" rel="noopener noreferrer">{e.value}</a>
        : <>
            <code className="work-evidence-value">{e.value}</code>
            <button data-ui-action="file" type="button" className="work-evidence-copy" title={label}
                    aria-label={label} onClick={() => void copyPath(e.value).then(setCopied)}>{label}</button>
          </>}
    </li>
  );
}
