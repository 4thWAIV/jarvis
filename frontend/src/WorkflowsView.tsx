/**
 * The Workflows view of the right panel: every long workflow, one compact
 * row each, like the agents list. A row has its state as a coloured dot (a
 * workflow that has gone quiet has its own), its name, the phase it is on,
 * a small bar of the tasks done, how many decisions and blockers are open,
 * and when it was last heard from. Running workflows come first; finished
 * ones follow, dimmed. Clicking a row opens its detail in the centre area
 * (WorkflowDetail.tsx).
 */

import { useEffect, useState, useSyncExternalStore } from "react";
import { ago, FINISHED, openWorkflow, phaseText, STATE_WORDS, workflows,
         type Workflow } from "./workflows";

/** Re-renders every `ms`, so ages stay true between reads. */
export function useTick(ms: number): void {
  const [, setTick] = useState(0);
  useEffect(() => {
    const timer = setInterval(() => setTick((t) => t + 1), ms);
    return () => clearInterval(timer);
  }, [ms]);
}

export function WorkflowsView() {
  const live = useSyncExternalStore(workflows.subscribe, workflows.get);
  const open = useSyncExternalStore(openWorkflow.subscribe, openWorkflow.get);
  useTick(30000);
  const list = live.value ?? [];
  return (
    <div className="right-view workflows-view">
      {live.error && <p className="read-failure">Couldn't read the workflows ({live.error}); trying again.</p>}
      {live.value && list.length === 0 && <p className="workflows-empty">No workflows</p>}
      <ul className="workflows-list">
        {list.map((w) => (
          <li key={w.id}>
            <WorkflowRow workflow={w} selected={w.id === open} />
          </li>
        ))}
      </ul>
    </div>
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

function WorkflowRow({ workflow: w, selected }: { workflow: Workflow; selected: boolean }) {
  const decisions = w.open_decisions.length;
  const blockers = w.open_blockers.length;
  return (
    <button type="button" data-workflow-id={w.id} data-state={w.state}
            className={`workflow-row${FINISHED.has(w.state) ? " finished" : ""}${
              selected ? " selected" : ""}`}
            onClick={() => openWorkflow.set(w.id)}>
      <span className={`workflow-dot state-${w.state}`} title={STATE_WORDS[w.state]} />
      <span className="workflow-name">{w.name}</span>
      <span className="workflow-age">{ago(w.last_event_at)}</span>
      <span className="workflow-phase">{phaseText(w)}</span>
      <span className="workflow-tasks">
        <TaskBar done={w.tasks_done} total={w.tasks_total} />
        <span className="workflow-task-count">{w.tasks_done}/{w.tasks_total} tasks</span>
      </span>
      {(decisions > 0 || blockers > 0) && (
        <span className="workflow-open">
          {decisions > 0 && <span className="workflow-decisions">
            {decisions} decision{decisions !== 1 ? "s" : ""}</span>}
          {blockers > 0 && <span className="workflow-blockers">
            {blockers} blocker{blockers !== 1 ? "s" : ""}</span>}
        </span>
      )}
    </button>
  );
}
