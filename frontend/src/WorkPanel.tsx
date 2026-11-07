/**
 * The right panel's Work tab (the tab that was Workflows, in its place): a
 * row per work item with its title, its state and one useful update, an
 * open decision's question taking priority over the latest report. A
 * working item that has reported nothing for a while keeps its state and
 * says "No recent update" as its update. Clicking a row opens the item in the centre area (WorkDetail.tsx)
 * through `work.open`, and the list stays in view.
 *
 * Below it, under its own heading, the Workflows view as it was
 * (WorkflowsView.tsx), so the workflow monitor stays reachable while the
 * Work tracker grows.
 */

import { useSyncExternalStore } from "react";
import { RIGHT_SOCKET, row } from "./uiRight";
import { useT } from "./locale";
import { bind, bindingProps } from "./uiNavigation";
import { useUiTarget } from "./uiRegistry";
import { WorkflowsView } from "./WorkflowsView";
import { openWork, STATE_WORDS, workItems, type WorkItem } from "./work";

export function WorkPanel() {
  const t = useT();
  const live = useSyncExternalStore(workItems.subscribe, workItems.get);
  const open = useSyncExternalStore(openWork.subscribe, openWork.get);
  const ui = useUiTarget({ ref: { region: "right", view: "right.workflows", kind: "workList" },
                           label: t("right.work.title"), socket: RIGHT_SOCKET });
  const items = live.value ?? [];
  return (
    <div className="right-view work-panel">
      <section className="work-section" aria-label={t("right.work.title")}>
        {live.error && <p className="read-failure">{t("right.work.readFailed", { error: live.error })}</p>}
        {live.value && items.length === 0 && <p className="workflows-empty">{t("right.work.none")}</p>}
        <ul className="work-list" ref={(el) => { ui.whole(el); ui.part("list")(el); }}>
          {items.map((item) => <WorkRow key={item.id} item={item} selected={item.id === open} />)}
        </ul>
      </section>
      <h3 className="work-section-head">{t("right.work.workflows")}</h3>
      <WorkflowsView />
    </div>
  );
}

function WorkRow({ item, selected }: { item: WorkItem; selected: boolean }) {
  const t = useT();
  const ui = useUiTarget({ ref: { region: "right", view: "right.workflows", kind: "workItem", key: item.id },
                           label: item.title, socket: RIGHT_SOCKET });
  const deciding = item.decisions.length > 0 && item.state === "waiting_on_tim";
  return (
    <li>
      <button type="button" className={`work-row${selected ? " selected" : ""}`} data-work-id={item.id}
              data-state={item.state} title={item.title} ref={row(ui)}
              {...bindingProps(bind("work.open", { itemId: item.id }))}>
        <span className="work-row-title">{item.title}</span>
        <span className={`work-state state-${item.state}`} ref={ui.part("state")}>
          {STATE_WORDS[item.state]}</span>
        <span className={`work-update${deciding ? " deciding" : ""}`} ref={ui.part("update")}>
          {deciding ? t("right.work.decision", { question: item.headline })
            : item.quiet ? t("right.work.quiet") : item.headline}</span>
      </button>
    </li>
  );
}
