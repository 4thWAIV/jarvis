import { WorkOwnerActivity } from "./WorkerStatus";
/**
 * The right panel's Work tab (the tab that was Workflows, in its place):
 * the items under their groups, each group headed by its area or, for items
 * with none, its source folder's name. A row per work item with its title,
 * its state and one useful update, an open decision's question taking
 * priority over the latest report. A working or verifying item that has
 * reported nothing for a while keeps its state and says "No recent update"
 * as its update. An item blocked or waiting on him, or with a piece that is,
 * carries a mark: a red or amber edge and dot. An item with decisions open
 * says how many beside its state, and its third line is the first thing
 * holding it up besides those decisions, with how many more. Clicking a row
 * opens the item in the centre area (WorkDetail.tsx) through `work.open`,
 * and the list stays in view. Each row shows its live activity
 * (nodeActivity.tsx): a travelling glow while it is working and reporting,
 * "Paused" once it has gone quiet, a check once done, and one flash for
 * each report that arrives.
 *
 * Under the open items, History (`work.showHistory`): the items closed, or
 * done a while, the most recently finished first, each still opening in
 * full.
 *
 * Below it, under its own heading, the Workflows view as it was
 * (WorkflowsView.tsx), so the workflow monitor stays reachable while the
 * Work tracker grows.
 */

import { useEffect, useSyncExternalStore } from "react";
import { RIGHT_SOCKET, row } from "./uiRight";
import { useT } from "./locale";
import { bind, bindingProps } from "./uiNavigation";
import { useUiTarget } from "./uiRegistry";
import { WorkflowsView } from "./WorkflowsView";
import { activityOf, ActivityMark, ShownSince, useArrivalFlash, useShownSince,
         workStanding } from "./nodeActivity";
import { blockerWords, followUpWords, itemMark, liveWords, needWords, packageReviewWords,
         rowState, rowStateWords, openWork, showHistory, STATE_WORDS, workList, type WorkGroup, type WorkItem,
         type WorkMark } from "./work";

export function WorkPanel() {
  const t = useT();
  const live = useSyncExternalStore(workList.subscribe, workList.get);
  const open = useSyncExternalStore(openWork.subscribe, openWork.get);
  const ui = useUiTarget({ ref: { region: "right", view: "right.workflows", kind: "workList" },
                           label: t("right.work.title"), socket: RIGHT_SOCKET });
  const items = new Map((live.value?.items ?? []).map((i) => [i.id, i]));
  const groups = live.value?.groups ?? [];
  const since = useShownSince();
  const history = live.value?.history ?? [];
  const historyShown = useSyncExternalStore(showHistory.subscribe, showHistory.get);
  useEffect(() => () => showHistory.set(false), []);
  return (
    <ShownSince.Provider value={since}>
    <div className="right-view work-panel">
      <section className="work-section" aria-label={t("right.work.title")}>
        {live.error && <p className="read-failure">{t("right.work.readFailed", { error: live.error })}</p>}
        {live.value && items.size === 0 && <p className="workflows-empty">{t("right.work.none")}</p>}
        <div className="work-groups" ref={(el) => { ui.whole(el); ui.part("list")(el); }}>
          {groups.map((g) => (
            <Group key={`${g.grouped_by}:${g.name}`} group={g} open={open}
                   items={g.ids.flatMap((id) => items.get(id) ?? [])} />
          ))}
        </div>
        {history.length > 0 && (
          <div className="work-history">
            <button type="button" className="workflows-done-toggle" aria-expanded={historyShown}
                    ref={ui.part("showHistory")} {...bindingProps(bind("work.showHistory", { shown: !historyShown }))}>
              {historyShown ? "▾" : "▸"} {t("right.work.history", { count: history.length })}
            </button>
            {historyShown && (
              <ul className="work-list history" ref={ui.part("history")}>
                {history.map((item) => <WorkRow key={item.id} item={item} selected={item.id === open} />)}
              </ul>
            )}
          </div>
        )}
      </section>
      <h3 className="work-section-head">{t("right.work.workflows")}</h3>
      <WorkflowsView />
    </div>
    </ShownSince.Provider>
  );
}

function Group({ group, items, open }: { group: WorkGroup; items: WorkItem[]; open: string | null }) {
  const t = useT();
  const ui = useUiTarget({ ref: { region: "right", view: "right.workflows", kind: "workGroup",
                                  key: `${group.grouped_by}:${group.name}` },
                           label: group.label, socket: RIGHT_SOCKET });
  return (
    <section className="work-group" data-grouped-by={group.grouped_by} ref={ui.whole}>
      <h4 className="work-group-head" ref={ui.part("head")}
          title={group.grouped_by === "folder" ? group.name : t("right.work.area")}>
        {group.label}</h4>
      <ul className="work-list">
        {items.map((item) => <WorkRow key={item.id} item={item} selected={item.id === open} />)}
      </ul>
    </section>
  );
}

function WorkRow({ item, selected }: { item: WorkItem; selected: boolean }) {
  const t = useT();
  const ui = useUiTarget({ ref: { region: "right", view: "right.workflows", kind: "workItem", key: item.id },
                           label: item.title, socket: RIGHT_SOCKET });
  const deciding = item.decisions.length > 0 && item.state === "waiting_on_tim";
  const mark = itemMark(item);
  const activity = activityOf(workStanding(item.state), null, 0);
  const arrived = useArrivalFlash(item.updated_at);
  // The decisions are the update line already; the third line is the rest.
  const holding = item.blockers.filter((b) => b.kind !== "decision");
  const decisionCount = t("right.work.decisionCount", { count: item.decisions.length });
  return (
    <li>
      <button type="button" className={`work-row${selected ? " selected" : ""}`} data-work-id={item.id}
              data-state={item.state} data-mark={mark ?? undefined} data-activity={activity ?? undefined}
              title={item.title} ref={(el) => { row(ui)(el); arrived(el); }}
              {...bindingProps(bind("work.open", { itemId: item.id }))}>
        <span className="work-row-title">
          {mark && <WorkMarkDot mark={mark} target={ui.part("mark")} />}
          {item.title}</span>
        <span className="work-row-pills">
          {[...new Map(item.pieces.filter(p => p.state !== "done" && p.who.ref).map(p => [p.who.kind + ":" + p.who.ref, p.who])).entries()].map(([key, owner]) => <WorkOwnerActivity key={key} owner={owner} />)}
          {/* Beside the state, never in the title's room: a Paused mark there
              once squeezed a long title down to its ellipsis. */}
          <ActivityMark activity={activity} target={ui.part("activity")} />
          {deciding && <span className="work-decision-count" title={decisionCount} aria-label={decisionCount}
                             ref={ui.part("decisionCount")}>{item.decisions.length}</span>}
          <span className={`work-state state-${rowState(item)}`} ref={ui.part("state")}
                title={item.state === "activity_unknown" && !item.live ? t("right.work.activityUnknownReason") : undefined}>
            {rowStateWords(item, t)}</span>
        </span>
        {(item.projects ?? []).length > 0 && (
          <span className="work-row-project" ref={ui.part("project")}>
            {t("right.work.project", { names: item.projects.map((p) => p.name).join(", ") })}</span>
        )}
        <span className={`work-update${deciding ? " deciding" : ""}`} ref={ui.part("update")}>
          {(item.also_waiting ?? 0) > 0 && <>{t("right.work.alsoWaiting", { count: item.also_waiting })} · </>}
          {deciding ? t("right.work.decision", { question: item.headline })
            : item.stalled ? t("right.work.stalled")
            : item.live ? liveWords(item.live)
            : (item.needs_you ?? []).length > 0 ? t("right.work.needsYou", { act: needWords(item.needs_you![0]) })
            : item.package_review && item.package_review.at >= item.updated_at
              ? packageReviewWords(item.package_review)
            : item.follow_up ? followUpWords(item.follow_up)
            : item.quiet ? t("right.work.quiet") : item.headline}</span>
        {holding.length > 0 && (
          <span className="work-blocker-line" data-kind={holding[0].kind} ref={ui.part("blocker")}>
            {blockerWords(item, holding[0])}
            {holding.length > 1 && ` · ${t("right.work.more", { count: holding.length - 1 })}`}
          </span>
        )}
      </button>
    </li>
  );
}

/** The mark an item or a piece carries while it is blocked (red) or waiting
 *  on him (amber): a dot before its title, worded for a screen reader and on
 *  hover. The Work detail's pieces draw the same one. */
export function WorkMarkDot({ mark, target }: { mark: Exclude<WorkMark, null>;
                                                 target?: (el: HTMLElement | null) => void }) {
  const t = useT();
  const words = t(`right.work.mark.${mark}`);
  return <span className={`work-mark mark-${mark}`} role="img" aria-label={words} title={words}
               ref={target} />;
}
