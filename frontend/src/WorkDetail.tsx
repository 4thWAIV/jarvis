/**
 * One work item, in full: the centre area's Work view, opened by clicking
 * its row in the right panel's Work tab (`work.open`), which stays in view.
 * In order:
 *
 *   - the head: its title, its state, and Close (`work.close`, back to
 *     Conversation);
 *   - its source: a ZIP work package or a folder on this Mac, its path, and
 *     whether it is there now (a stat; nothing is opened or run);
 *   - the latest update ("No recent update" once a working item has gone
 *     quiet);
 *   - its open decisions, each answered here (DecisionCard.tsx);
 *   - its pieces, in the order their dependencies give, each with exactly
 *     who is on it (an agent by its one name, a JARVIS run, a workflow, or
 *     him), the reporting agent's own worker label beside that, quoted and
 *     marked as its words, a mark while it is blocked or waiting on him,
 *     its latest note, the pieces it waits for, and its evidence (a URL as
 *     a link, a commit or path as text to copy);
 *   - its latest reports, newest last.
 *
 * Everything here was written by the agents reporting the item, and is
 * drawn as plain text.
 */

import { useState } from "react";
import { copyPath } from "./attachmentLocation";
import { CENTRE_SOCKET } from "./ConversationView";
import { DecisionCard } from "./DecisionCard";
import { useLiveRead } from "./liveRead";
import { formatDateTime, useT } from "./locale";
import { bind, bindingProps } from "./uiNavigation";
import { useUiTarget } from "./uiRegistry";
import { answerWorkDecision, getWorkItem, pieceMark, STATE_WORDS, whoWords, workChanged,
         type WorkDecision, type WorkEvidence, type WorkItem } from "./work";
import { WorkMarkDot } from "./WorkPanel";

function useWorkTarget(kind: "workItem" | "workPiece" | "workDecision", key: string, label: string) {
  return useUiTarget({ ref: { region: "main", view: "main.work", kind, key }, label, socket: CENTRE_SOCKET });
}

export function WorkDetail({ id }: { id: string }) {
  const t = useT();
  const live = useLiveRead(() => getWorkItem(id), [workChanged]);
  const item = live.value?.item;
  const events = live.value?.events ?? [];
  const ui = useWorkTarget("workItem", id, item?.title ?? t("tabs.main.work"));
  return (
    <div id="work-detail" ref={ui.whole}>
      <div className="work-detail-head" ref={ui.part("header")}>
        <span className="work-detail-name">{item?.title ?? t("tabs.main.work")}</span>
        {item && <span className={`work-state state-${item.state}`} ref={ui.part("state")}>
          {STATE_WORDS[item.state]}</span>}
        <button type="button" className="workflow-close" title={t("main.common.close")}
                aria-label={t("main.common.close")} ref={ui.part("close")}
                {...bindingProps(bind("work.close", {}))}>×</button>
      </div>
      {live.error && <p className="read-failure">{t("main.work.unreadable", { error: live.error })}</p>}
      {item && (
        <div className="work-detail-body">
          <p className="work-source" ref={ui.part("source")}>
            <span>{t(`main.work.source.${item.source_kind}`)}</span>{" "}
            <code className="work-path">{item.source_path}</code>{" "}
            <span className={`work-source-${item.source}`}>{t(`main.work.found.${item.source}`)}</span>
          </p>
          <p className="work-latest" ref={ui.part("update")}>
            {item.quiet && <span className="work-quiet">{t("right.work.quiet")} · </span>}
            {item.latest_update}
          </p>
          <section ref={ui.part("decisions")}>
            <h3 className="work-section-head">{t("main.work.decisions")}</h3>
            {item.decisions.length === 0
              ? <p className="workflow-empty">{t("main.work.noDecisions")}</p>
              : <ul className="workflow-decisions-list">
                  {item.decisions.map((d) => <Decision key={d.key} itemId={id} decision={d} />)}
                </ul>}
          </section>
          <section ref={ui.part("pieces")}>
            <h3 className="work-section-head">{t("main.work.pieces")}</h3>
            {item.pieces.length === 0
              ? <p className="workflow-empty">{t("main.work.noPieces")}</p>
              : <ol className="work-pieces">
                  {item.pieces.map((p, step) => <Piece key={p.key} item={item} pieceKey={p.key} step={step + 1} />)}
                </ol>}
          </section>
          <section>
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
        </div>
      )}
    </div>
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
                       ui={ui} dataKey={d.key} answer={(value) => answerWorkDecision(itemId, d.key, value)} />;
}
