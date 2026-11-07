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
 *         to open a page package, the brain's open_package_page too), then
 *         the decided ones: his answers, and those withdrawn by their asker;
 *       Blocking: everything holding it up, in the server's order;
 *       Pieces: in dependency order, each with exactly who is on it, the
 *         reporting agent's own worker label beside that (quoted, marked as
 *         its words), a mark while blocked or waiting on him, its latest
 *         note, what it waits for and its evidence (a URL as a link, a
 *         commit or path as text to copy);
 *       Reports: its latest reports, newest last.
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
import { openPackagePage } from "./packages";
import { TabRow, type Tab } from "./Tabs";
import { bind, bindingProps } from "./uiNavigation";
import { useUiTarget } from "./uiRegistry";
import { answerWorkDecision, blockerWords, getWorkItem, pieceMark, STATE_WORDS, whoWords, WORK_TABS,
         workChanged, workTab, type WorkAnswered, type WorkBlocker, type WorkDecision, type WorkEvent,
         type WorkEvidence, type WorkItem, type WorkTab } from "./work";
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
  const deciding = item ? item.decisions.length : 0;
  const views: Record<WorkTab, (item: WorkItem) => ReactNode> = {
    overview: (i) => <Overview item={i} part={ui.part} />,
    decisions: (i) => <Decisions item={i} part={ui.part} />,
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
  return (
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
      {live.error && <p className="read-failure">{t("main.work.unreadable", { error: live.error })}</p>}
      {item && deciding > 0 && (
        <button type="button" className="work-decision-banner" ref={ui.part("banner")}
                {...bindingProps(bind("work.tab.select", { itemId: id, tab: "decisions" }))}>
          <strong>{t("main.work.banner.title", { count: deciding })}</strong>
          <span className="work-decision-banner-question">{item.decisions[0].question}</span>
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

/** Its state, latest update, source and area, and who is on it. */
function Overview({ item, part }: { item: WorkItem; part: Part }) {
  const t = useT();
  const who = [...new Map(item.pieces.map((p) => {
    const words = whoWords(p.who) + (p.worker ? ` ${t("main.work.worker", { label: p.worker })}` : "");
    return [words, words];
  })).values()];
  return (
    <>
      <p className="work-latest" ref={part("update")}>
        {item.quiet && <span className="work-quiet">{t("right.work.quiet")} · </span>}
        {item.latest_update}
      </p>
      <p className="work-source" ref={part("source")}>
        <span>{t(`main.work.source.${item.source_kind}`)}</span>{" "}
        <code className="work-path">{item.source_path}</code>{" "}
        <span className={`work-source-${item.source}`}>{t(`main.work.found.${item.source}`)}</span>
        {item.area && <> · <span className="work-area">{t("main.work.area", { area: item.area })}</span></>}
      </p>
      <section ref={part("who")}>
        <h3 className="work-section-head">{t("main.work.who")}</h3>
        {who.length === 0 ? <p className="workflow-empty">{t("main.work.noPieces")}</p>
          : <ul className="work-who">{who.map((w) => <li key={w}>{w}</li>)}</ul>}
      </section>
    </>
  );
}

/** Each open decision, answered here, then the answered ones. */
function Decisions({ item, part }: { item: WorkItem; part: Part }) {
  const t = useT();
  return (
    <>
      <section ref={part("decisions")}>
        <h3 className="work-section-head">{t("main.work.decisions")}</h3>
        {item.decisions.length === 0
          ? <p className="workflow-empty">{t("main.work.noDecisions")}</p>
          : <ul className="workflow-decisions-list">
              {item.decisions.map((d) => <Decision key={d.key} itemId={item.id} decision={d} />)}
            </ul>}
      </section>
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
  return (
    <section ref={part("pieces")}>
      <h3 className="work-section-head">{t("main.work.pieces")}</h3>
      {item.pieces.length === 0
        ? <p className="workflow-empty">{t("main.work.noPieces")}</p>
        : <ol className="work-pieces">
            {item.pieces.map((p, step) => <Piece key={p.key} item={item} pieceKey={p.key} step={step + 1} />)}
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

function Piece({ item, pieceKey, step }: { item: WorkItem; pieceKey: string; step: number }) {
  const t = useT();
  const piece = item.pieces.find((p) => p.key === pieceKey)!;
  const ui = useWorkTarget("workPiece", `${item.id}/${piece.key}`, piece.title);
  const mark = pieceMark(piece);
  const titleOf = (key: string) => item.pieces.find((p) => p.key === key)?.title ?? key;
  return (
    <li className="work-piece" data-key={piece.key} data-mark={mark ?? undefined}
        ref={(el) => { ui.whole(el); ui.part("row")(el); }}>
      <div className="work-piece-head">
        <span className="work-piece-step">{step}.</span>
        <span className="work-piece-title">
          {mark && <WorkMarkDot mark={mark} target={ui.part("mark")} />}
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
        <p className={`work-piece-after${piece.waits_for.length ? " waiting" : ""}`} ref={ui.part("after")}>
          {piece.waits_for.length
            ? t("main.work.waitsFor", { pieces: piece.waits_for.map(titleOf).join(", ") })
            : t("main.work.afterDone", { pieces: piece.after.map(titleOf).join(", ") })}
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
