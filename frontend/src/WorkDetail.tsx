/**
 * One work item, in full: the centre area's Work view, opened by clicking
 * its row in the right panel's Work tab (`work.open`), which stays in view;
 * an item in History opens the same way.
 *
 *   - the head: its title, its state, "In History" when it is, and Close
 *     (`work.close`, back to Conversation);
 *   - while a decision waits on him, a "Decision needed" banner above every
 *     tab, which opens the Decisions tab (`work.tab.select`);
 *   - the tabs (`work.tab.select`, `WORK_TABS`), Decisions and Blocking
 *     counted and lit while they have something:
 *       Overview: its latest update ("No recent update" once it has gone
 *         quiet), its source (a stat; nothing is opened or run), its area,
 *         and who is on it;
 *       Decisions: each open decision, answered here (DecisionCard.tsx),
 *         with "Open the package" when it names one (the server's one way
 *         to open a page package, the brain's open_package_page too); then
 *         the questions of the packages sent for it (`make_package --work`,
 *         package_inbox.py), answered here into the package's own record;
 *         then the decided ones: his answers, and those withdrawn by their
 *         asker;
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

import { useLayoutEffect, useRef, useState, useSyncExternalStore, type ReactNode } from "react";
import { copyPath } from "./attachmentLocation";
import { CENTRE_SOCKET } from "./ConversationView";
import { DecisionCard } from "./DecisionCard";
import { useLiveRead } from "./liveRead";
import { formatDateTime, SaidText, useT, type Said } from "./locale";
import { answerInbox, inboxProgress, openInbox, optionFor, type InboxDetail,
         type InboxQuestion } from "./packageInbox";
import { openPackagePage } from "./packages";
import { PackageMark } from "./QueuePackages";
import { TabRow, type Tab } from "./Tabs";
import { bind, bindingProps } from "./uiNavigation";
import { useUiTarget } from "./uiRegistry";
import { answerWorkDecision, blockerWords, followUpWords, packageReviewWords, reviewWork, type ReviewAction, type WorkPackageReview, getWorkItem, pieceMark, STATE_WORDS, whoWords, WORK_TABS,
         workChanged, workTab, type WorkAnswered, type WorkBlocker, type WorkDecision, type WorkEvent,
         type WorkEvidence, type WorkItem, type WorkPiece, type WorkTab } from "./work";
import { activityOf, ActivityMark, ShownSince, useActivityClock, useArrivalFlash, useShownSince,
         workStanding, type Activity } from "./nodeActivity";
import { WorkMarkDot } from "./WorkPanel";

type WorkKind = "workItem" | "workPiece" | "workDecision" | "workBlocker";

function useWorkTarget(kind: WorkKind, key: string, label: string) {
  return useUiTarget({ ref: { region: "main", view: "main.work", kind, key }, label, socket: CENTRE_SOCKET });
}

type Part = (name: string) => (el: Element | null) => void;

export function WorkDetail({ id }: { id: string }) {
  const t = useT();
  const live = useLiveRead(() => getWorkItem(id), [workChanged]);
  const item = live.value?.item;
  const events = live.value?.events ?? [];
  const ui = useWorkTarget("workItem", id, item?.title ?? t("tabs.main.work"));
  const shownTab = useSyncExternalStore(workTab.subscribe, workTab.get);
  const tab = shownTab.itemId === id ? shownTab.tab : "overview";
  const packages = live.value?.packages ?? [];
  const asked = packages.flatMap((p) => p.questions.filter((q) => q.applies && !q.answer)
    .map((q) => ({ question: q.question })));
  const deciding = item ? item.decisions.length + asked.length : 0;
  const views: Record<WorkTab, (item: WorkItem) => ReactNode> = {
    overview: (i) => <Overview item={i} part={ui.part} />,
    decisions: (i) => <Decisions item={i} packages={packages} part={ui.part} />,
    blocking: (i) => <Blocking item={i} part={ui.part} />,
    pieces: (i) => <Pieces item={i} part={ui.part} />,
    reports: () => <Reports events={events} part={ui.part} />,
  };
  const tabs: Tab[] = item ? WORK_TABS.map((name) => ({
    id: name, icon: null, view: () => views[name](item),
    title: name === "decisions" && deciding ? t("main.work.tab.decisionsCount", { count: deciding })
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
        {item && <span className={`work-state state-${item.state}`} ref={ui.part("state")}>
          {STATE_WORDS[item.state]}</span>}
        {item?.history && <span className="work-history-pill">{t("main.work.inHistory")}</span>}
        <button type="button" className="workflow-close" title={t("main.common.close")}
                aria-label={t("main.common.close")} ref={ui.part("close")}
                {...bindingProps(bind("work.close", {}))}>×</button>
      </div>
      {item && !item.closed_at && <Review item={item} target={ui.part("review")} />}
      {live.error && <p className="read-failure">{t("main.work.unreadable", { error: live.error })}</p>}
      {item && deciding > 0 && (
        <button type="button" className="work-decision-banner" ref={ui.part("banner")}
                {...bindingProps(bind("work.tab.select", { itemId: id, tab: "decisions" }))}>
          <strong>{t("main.work.banner.title", { count: deciding })}</strong>
          <span className="work-decision-banner-question">
            {(item.decisions[0] ?? asked[0]).question}</span>
          <span className="work-decision-banner-go">{t("main.work.banner.go")}</span>
        </button>
      )}
      {item && selected && <>
        <div ref={row} style={{ flex: "none" }}>
          <TabRow tabs={tabs} selected={selected.id} className="workflow-tabs work-tabs" words box="main.work.tabs"
                  alert={(x) => (x.id === "decisions" && deciding > 0) || (x.id === "blocking" && item.blockers.length > 0)}
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
      </> : <form data-ui-action="domain" onSubmit={(e) => { e.preventDefault(); if (words.trim()) act(asking, words); }}>
        <input data-ui-action="input" type="text" value={words} autoFocus aria-label={t(`main.work.review.${asking}`)}
               placeholder={t(`main.work.review.${asking}`)} onChange={(e) => setWords(e.target.value)} />
        <button type="submit" className="settings-btn" data-ui-action="domain"
                disabled={busy || !words.trim()}>{t("main.work.review.send")}</button>
        <button type="button" className="settings-btn" data-ui-action="domain"
                onClick={() => { setAsking(null); setWords(""); setProblem(null); }}>{t("main.common.cancel")}</button>
      </form>}
      {problem && <span className="file-state">{problem}</span>}
    </div>
  );
}

/** What his act in a review package did, with when: on the Overview and
 *  the Decisions tab alike, so every act of his shows a change. */
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
      {item.projects.length > 0 && (
        <p className="work-projects" ref={part("project")}>
          {item.projects.map((p) => (
            <button key={p.id} type="button" className="knowledge-open"
                    {...bindingProps(bind("project.open", { projectId: p.id }))}>
              {t("main.work.project", { name: p.name })}</button>
          ))}
        </p>
      )}
      <section ref={part("who")}>
        <h3 className="work-section-head">{t("main.work.who")}</h3>
        {who.length === 0 ? <p className="workflow-empty">{t("main.work.noPieces")}</p>
          : <ul className="work-who">{who.map((w) => <li key={w}>{w}</li>)}</ul>}
      </section>
    </>
  );
}

/** Each open decision, answered here, then the questions of the packages
 *  sent for it, then the answered ones. */
function Decisions({ item, packages, part }: { item: WorkItem; packages: InboxDetail[]; part: Part }) {
  const t = useT();
  return (
    <>
      {item.package_review && <PackageReviewLine review={item.package_review} />}
      <section ref={part("decisions")}>
        <h3 className="work-section-head">{t("main.work.decisions")}</h3>
        {item.decisions.length === 0 && packages.length === 0
          ? <p className="workflow-empty">{t("main.work.noDecisions")}</p>
          : <ul className="workflow-decisions-list">
              {item.decisions.map((d) => <Decision key={d.key} itemId={item.id} decision={d} />)}
            </ul>}
      </section>
      {packages.length > 0 && (
        <section ref={part("packages")}>
          <h3 className="work-section-head">{t("main.work.packages")}</h3>
          {packages.map((p) => <PackageQuestions key={p.inbox.id} detail={p} />)}
        </section>
      )}
      <section ref={part("answered")}>
        <h3 className="work-section-head">{t("main.work.answered")}</h3>
        {item.answered.length === 0
          ? <p className="workflow-empty">{t("main.work.noneAnswered")}</p>
          : <ul className="work-answered">
              {item.answered.map((a) => <Answered key={a.key} itemId={item.id} answered={a} />)}
            </ul>}
      </section>
    </>
  );
}

function Answered({ itemId, answered: a }: { itemId: string; answered: WorkAnswered }) {
  const t = useT();
  const ui = useWorkTarget("workDecision", `${itemId}/${a.key}`, a.question);
  return (
    <li className="work-answered-item" data-key={a.key} ref={(el) => { ui.whole(el); ui.part("row")(el); }}>
      <p className="work-answered-question">{a.question}</p>
      <p className="work-answered-answer">
        {t(`main.work.answeredBy.${a.where}`, { answer: a.answer })}{" "}
        <span className="work-event-at">{formatDateTime(a.answered_at * 1000,
          { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" })}</span>
      </p>
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
  const now = useActivityClock(item.pieces.map((p) => p.quiet_at));
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
          {whoWords(piece.who)}
          {piece.worker && " "}
          {piece.worker && <span className="work-piece-worker" title={t("main.work.workerTitle")}
                                 ref={ui.part("worker")}>{t("main.work.worker", { label: piece.worker })}</span>}
        </span>
        <span className={`work-state state-${piece.state}`} ref={ui.part("state")}>{STATE_WORDS[piece.state]}</span>
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

function Decision({ itemId, decision: d }: { itemId: string; decision: WorkDecision }) {
  const t = useT();
  const ui = useWorkTarget("workDecision", `${itemId}/${d.key}`, t("main.work.decision"));
  return <DecisionCard question={d.question} options={d.options} recommendation={d.recommendation || null}
                       ui={ui} dataKey={d.key} answer={(value) => answerWorkDecision(itemId, d.key, value)}
                       more={d.package ? <OpenPackage reference={d.package} target={ui.part("package")} /> : null} />;
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

/** One package sent for the item: its title and sender, Open the package
 *  (the inbox's one Open, his click), and each question it asks, answered
 *  here through the package's own record (the brain's
 *  answer_package_decision too). The words are the sender's. */
function PackageQuestions({ detail }: { detail: InboxDetail }) {
  const t = useT();
  const [problem, setProblem] = useState<Said | null>(null);
  const entry = detail.inbox;
  return (
    <div className="work-package" data-inbox-id={entry.id}>
      <p className="work-decision-package">
        <PackageMark />{" "}
        <span>{t("main.work.packageFrom", { title: entry.title, sender: entry.sender })}</span>{" "}
        <span className="package-progress">{inboxProgress(entry)}</span>{" "}
        <button type="button" className="file-action" data-ui-action="domain"
                onClick={() => void openInbox(entry.id).then(setProblem)}>
          {t("main.work.openPackage")}</button>
        {problem && <SaidText said={problem} className="file-state" />}
      </p>
      <ul className="workflow-decisions-list">
        {detail.questions.map((q) => <PackageQuestion key={q.id} inboxId={entry.id} question={q} />)}
      </ul>
    </div>
  );
}

function PackageQuestion({ inboxId, question: q }: { inboxId: string; question: InboxQuestion }) {
  const t = useT();
  const ui = useUiTarget({ ref: { region: "main", view: "main.work", kind: "workPackageQuestion",
                                  key: `${inboxId}/${q.id}` }, label: q.question, socket: CENTRE_SOCKET });
  if (!q.applies || q.answer) {
    return (
      <li className="work-answered-item" data-key={q.id} ref={(el) => { ui.whole(el); ui.part("row")(el); }}>
        <p className="work-answered-question">{q.question}</p>
        <p className="work-answered-answer">
          {q.answer ? t("main.work.packageAnswered", { answer: answerWords(q, t) }) : t("main.work.packageNotAsked")}
        </p>
      </li>
    );
  }
  const recommended = q.options.find((o) => o.id === q.recommended);
  return <DecisionCard question={q.question} options={q.options.map((o) => o.name)}
                       recommendation={recommended ? recommended.name : null} ui={ui} dataKey={q.id}
                       answer={(value) => {
                         const { option, words } = optionFor(q, value);
                         return answerInbox(inboxId, q.id, option, words);
                       }} />;
}

/** A recorded answer in words: the option's name, or Decide later / None of these. */
function answerWords(q: InboxQuestion, t: ReturnType<typeof useT>): string {
  const a = q.answer!;
  const name = a.choice || q.options.find((o) => o.id === a.answer)?.name
    || (a.answer === "later" || a.answer === "none" ? t(`main.work.packageSpecial.${a.answer}`) : a.answer);
  return a.note ? `${name} (${a.note})` : name;
}
