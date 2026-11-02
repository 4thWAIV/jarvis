/**
 * The Workflows view of the right panel: a summary of every long workflow,
 * one compact two-line row each at the panel's fixed width, read at a
 * glance. A row has the workflow's light red orb (WorkflowVisuals.tsx),
 * which moves with its state and is ringed in the state's colour; its name
 * on one line (the whole of it on hover) and when it was last heard from; a
 * slim track with a segment per phase, done, current or to come; tasks done
 * out of all; and its open decisions, blockers and waits as coloured icons
 * with counts. Everything else is in the detail, in the centre area.
 * Running workflows come first. A finished one stays in the list for a
 * couple of minutes (the server's LINGER_SEC) so its finish can be seen: its
 * orb ringed in its outcome's colour, a one-time glow, its outcome named,
 * and an × that dismisses it at once. Then it leaves the list by itself,
 * on time, without waiting for a hint. Dismissed ones are listed under a
 * collapsed "Done", dimmed and marked Dismissed. Clicking any row opens its
 * detail in the centre area (WorkflowDetail.tsx).
 *
 * Below the workflows, every subagent a session has dispatched that belongs
 * to no running workflow (one that does is in that workflow's detail): a
 * row in the same style, its orb ringed by its state, what it was asked to
 * do as its name, when it last wrote, and the session that dispatched it.
 * One that has finished lingers as a finished workflow does and leaves on
 * time; there is nothing to dismiss, as nothing was recorded. Clicking it
 * opens its transcript in the centre area the same way.
 */

import { useEffect, useState, useSyncExternalStore } from "react";
import { RIGHT_SOCKET, row } from "./uiRight";
import { useT } from "./locale";
import { createStore } from "./store";
import { bind, bindingProps } from "./uiNavigation";
import { registerRevealer } from "./uiNavigationReveal";
import { registerCommand, useUiTarget } from "./uiRegistry";
import { ago, dismissals, dismissWorkflow, FINISHED, isGone, openWorkflow, STATE_WORDS,
         SUBAGENT_LOOK, SUBAGENT_WORDS, workflows, type SubagentRow as Subagent,
         type Workflow } from "./workflows";
import { OpenIcons, PhaseTrack, WorkflowOrb } from "./WorkflowVisuals";

/** Whether the dismissed workflows under "Done" are shown; collapsed each
 *  time the tab leaves the page. Changed only by `workflows.showDone`, its
 *  toggle and the brain alike. */
const showDone = createStore(false);

registerCommand("workflows.showDone", {
  availability: () => ({ available: true }),
  execute({ shown }) { showDone.set(shown); return { status: "completed", effects: ["list.filtered"] }; },
});

// Pointing at a workflow whose row is not on the page once the tab shows:
// a finished one under a collapsed "Done" is shown by opening Done, as
// workflows.showDone does.
registerRevealer("right.workflows", "workflow", async (ref) => {
  const now = workflows.get().value;
  if (!now) return "not-loaded";
  const w = now.workflows.find((x) => x.id === ref.key);
  if (!w) return "entity-gone";
  if (isGone(w, Date.now() / 1000)) showDone.set(true);
  return true;
});

/** A row's part drawn by a component of its own (the orb), found in that
 *  row only. */
const within = (part: (el: Element | null) => void, selector: string) =>
  (row: Element | null) => part(row?.querySelector(selector) ?? null);

/** Re-renders every `ms`, so ages stay true between reads. */
export function useTick(ms: number): void {
  const [, setTick] = useState(0);
  useEffect(() => {
    const timer = setInterval(() => setTick((t) => t + 1), ms);
    return () => clearInterval(timer);
  }, [ms]);
}

export function WorkflowsView() {
  const t = useT();
  const live = useSyncExternalStore(workflows.subscribe, workflows.get);
  const open = useSyncExternalStore(openWorkflow.subscribe, openWorkflow.get);
  useSyncExternalStore(dismissals.subscribe, dismissals.get);
  const doneShown = useSyncExternalStore(showDone.subscribe, showDone.get);
  useEffect(() => () => showDone.set(false), []);
  const ui = useUiTarget({ ref: { region: "right", view: "right.workflows", kind: "workflowsList" },
                           label: "Workflows", socket: RIGHT_SOCKET });
  const [, setNow] = useState(0);
  useTick(30000);
  const list = live.value?.workflows ?? [];
  const now = Date.now() / 1000;
  const current = list.filter((w) => !isGone(w, now));
  const done = list.filter((w) => isGone(w, now));
  const subagents = (live.value?.subagents ?? []).filter((a) => a.dismiss_at == null || now < a.dismiss_at);

  // A finished workflow or subagent leaves the list at its dismiss_at: on
  // time, by a timer of its own, and the record is read again then.
  const next = Math.min(...[...current, ...subagents].map((w) => w.dismiss_at ?? Infinity));
  useEffect(() => {
    if (!Number.isFinite(next)) return;
    const timer = setTimeout(() => { setNow(Date.now()); workflows.refresh(); },
                             Math.max(0, (next - Date.now() / 1000) * 1000) + 50);
    return () => clearTimeout(timer);
  }, [next]);

  return (
    <div className="right-view workflows-view" ref={ui.whole}>
      {live.error && <p className="read-failure">{t("right.workflows.readFailed", { error: live.error })}</p>}
      {live.value && current.length === 0 && subagents.length === 0 &&
        <p className="workflows-empty">{t("right.workflows.none")}</p>}
      <ul className="workflows-list">
        {current.map((w) => <WorkflowItem key={w.id} workflow={w} selected={w.id === open} />)}
        {subagents.map((a) => (
          <li key={a.key} className="workflow-item">
            <SubagentRow agent={a} selected={a.key === open} />
          </li>
        ))}
      </ul>
      {done.length > 0 && (
        <div className="workflows-done">
          <button type="button" className="workflows-done-toggle" aria-expanded={doneShown}
                  ref={ui.part("showDone")} {...bindingProps(bind("workflows.showDone", { shown: !doneShown }))}>
            {doneShown ? "▾" : "▸"} {t("right.workflows.done", { count: done.length })}
          </button>
          {doneShown && (
            <ul className="workflows-list done">
              {done.map((w) => <DoneRow key={w.id} workflow={w} selected={w.id === open} />)}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}

/** A dismissed workflow: small, dimmed, its outcome's colour and the mark
 *  that it is done with. */
function DoneRow({ workflow: w, selected }: { workflow: Workflow; selected: boolean }) {
  const t = useT();
  const ui = useWorkflowTarget(w);
  return (
    <li>
      <button type="button" data-workflow-id={w.id} data-state={w.state} ref={row(ui)}
              className={`workflow-done-row${selected ? " selected" : ""}`}
              {...bindingProps(bind("workflow.open", { workflowId: w.id }))}>
        <span className={`workflow-outcome-dot outcome-${w.state}`} ref={ui.part("state")} />
        <span className="workflow-done-name">{w.name}</span>
        <span className="workflow-dismissed-mark">{t("right.workflows.dismissed")}</span>
      </button>
    </li>
  );
}

/** One workflow's presentation in this list, for the brain to point at. */
function useWorkflowTarget(w: Workflow) {
  return useUiTarget({ ref: { region: "right", view: "right.workflows", kind: "workflow", key: w.id },
                       label: w.name, socket: RIGHT_SOCKET });
}

/** A listed workflow: its row and, once it has finished, its Dismiss ×
 *  (recording a dismissal: not navigation), registered as the `dismiss`
 *  part. The × sits over the row's edge, beside the whole the branch
 *  enters; its leaf comes down the row's inner margin into its top edge
 *  (uiPipeline.ts leafEdge). */
function WorkflowItem({ workflow: w, selected }: { workflow: Workflow; selected: boolean }) {
  const t = useT();
  const finished = FINISHED.has(w.state);
  const ui = useWorkflowTarget(w);
  return (
    <li className={`workflow-item${finished ? " dismissible" : ""}`}>
      <WorkflowRow workflow={w} selected={selected} ui={ui} />
      {finished && (
        <button data-ui-action="domain" type="button" className="workflow-dismiss" aria-label={t("right.workflows.dismiss")}
                title={t("right.workflows.dismiss")} ref={ui.part("dismiss")}
                onClick={() => void dismissWorkflow(w.id)}>×</button>
      )}
    </li>
  );
}

export function TaskBar({ done, total }: { done: number; total: number }) {
  const share = total > 0 ? Math.min(1, done / total) : 0;
  return (
    <span className="workflow-bar" role="progressbar" aria-valuemin={0}
          aria-valuemax={total} aria-valuenow={done}>
      <span className="workflow-bar-fill" style={{ width: `${share * 100}%` }} />
    </span>
  );
}

function WorkflowRow({ workflow: w, selected, ui }: {
  workflow: Workflow; selected: boolean; ui: ReturnType<typeof useUiTarget>;
}) {
  const t = useT();
  const finished = FINISHED.has(w.state);
  const state = within(ui.part("state"), ".workflow-orb");
  return (
    <button type="button" data-workflow-id={w.id} data-state={w.state}
            className={`workflow-row${finished ? ` finished outcome-${w.state}` : ""}${
              selected ? " selected" : ""}`}
            title={`${w.name}: ${STATE_WORDS[w.state]}`}
            ref={(el) => { row(ui)(el); state(el); }}
            {...bindingProps(bind("workflow.open", { workflowId: w.id }))}>
      <WorkflowOrb state={w.state} size={30} />
      <span className="workflow-name">{w.name}</span>
      {finished
        ? <span className={`workflow-outcome outcome-${w.state}`}>{STATE_WORDS[w.state]}</span>
        : <span className="workflow-age">{ago(w.last_event_at)}</span>}
      <span className="workflow-strip">
        <span className="workflow-track"><PhaseTrack w={w} /></span>
        <span className="workflow-counts">
          {w.tasks_total > 0 && (
            <span className="workflow-tasks" aria-label={t("right.workflows.tasksDone")} ref={ui.part("tasks")}>
              {w.tasks_done}/{w.tasks_total}</span>
          )}
          {!finished && <OpenIcons w={w} />}
        </span>
      </span>
    </button>
  );
}

/** A subagent that belongs to no running workflow, drawn as a workflow is:
 *  its orb ringed by its state, what it was asked to do, when it last
 *  wrote, and below, the session that dispatched it. */
function SubagentRow({ agent: a, selected }: { agent: Subagent; selected: boolean }) {
  const t = useT();
  const look = SUBAGENT_LOOK[a.state];
  const ended = a.state === "finished";
  const name = a.description || a.type || t("right.workflows.subagent");
  return (
    <button type="button" data-subagent-key={a.key} data-state={a.state}
            className={`workflow-row subagent-row${ended ? " finished outcome-succeeded" : ""}${
              selected ? " selected" : ""}`}
            title={`${name}: ${SUBAGENT_WORDS[a.state]}`}
            // Not yet a target: the subagent kind's key does not take the
            // real `agent:<session>/<id>` (coverage.md).
            {...bindingProps(bind("subagent.open", { sessionId: a.session_id, agentId: a.id }))}>
      <WorkflowOrb state={look} size={30} />
      <span className="workflow-name">{name}</span>
      {ended
        ? <span className="workflow-outcome outcome-succeeded">{SUBAGENT_WORDS[a.state]}</span>
        : <span className="workflow-age">{ago(a.written)}</span>}
      <span className="workflow-strip subagent-parent">
        {t(a.background ? "right.workflows.backgroundSubagentOf" : "right.workflows.subagentOf",
           { session: a.session_name })}
      </span>
    </button>
  );
}
