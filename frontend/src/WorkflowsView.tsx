/**
 * The Workflows view of the right panel: every long workflow, one row each,
 * read at a glance rather than read. A row has the workflow's light red orb
 * (WorkflowVisuals.tsx), which moves with its state and is ringed in the
 * state's colour; its name and when it was last heard from; a track with a
 * segment per phase, done, current or to come; a ring of tasks done; and
 * its open decisions, blockers and waits as coloured icons with counts.
 * Running workflows come first; finished ones follow, dimmed. Clicking a
 * row opens its detail in the centre area (WorkflowDetail.tsx).
 */

import { useEffect, useState, useSyncExternalStore } from "react";
import { ago, FINISHED, openWorkflow, STATE_WORDS, workflows,
         type Workflow } from "./workflows";
import { OpenIcons, PhaseTrack, Ring, WorkflowOrb } from "./WorkflowVisuals";

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
  return (
    <button type="button" data-workflow-id={w.id} data-state={w.state}
            className={`workflow-row${FINISHED.has(w.state) ? " finished" : ""}${
              selected ? " selected" : ""}`}
            title={STATE_WORDS[w.state]}
            onClick={() => openWorkflow.set(w.id)}>
      <WorkflowOrb state={w.state} size={44} />
      <span className="workflow-name">{w.name}</span>
      <span className="workflow-age">{ago(w.last_event_at)}</span>
      <span className="workflow-track"><PhaseTrack w={w} /></span>
      <span className="workflow-counts">
        <Ring done={w.tasks_done} total={w.tasks_total} size={34} label="Tasks done" />
        <OpenIcons w={w} />
      </span>
    </button>
  );
}
