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
 */

import { useEffect, useState, useSyncExternalStore } from "react";
import { ago, dismissals, dismissWorkflow, FINISHED, isGone, openWorkflow, STATE_WORDS,
         workflows, type Workflow } from "./workflows";
import { OpenIcons, PhaseTrack, WorkflowOrb } from "./WorkflowVisuals";

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
  useSyncExternalStore(dismissals.subscribe, dismissals.get);
  const [showDone, setShowDone] = useState(false);
  const [, setNow] = useState(0);
  useTick(30000);
  const list = live.value ?? [];
  const now = Date.now() / 1000;
  const current = list.filter((w) => !isGone(w, now));
  const done = list.filter((w) => isGone(w, now));

  // A finished workflow leaves the list at its dismiss_at: on time, by a
  // timer of its own, and the record is read again then.
  const next = Math.min(...current.map((w) => w.dismiss_at ?? Infinity));
  useEffect(() => {
    if (!Number.isFinite(next)) return;
    const timer = setTimeout(() => { setNow(Date.now()); workflows.refresh(); },
                             Math.max(0, (next - Date.now() / 1000) * 1000) + 50);
    return () => clearTimeout(timer);
  }, [next]);

  return (
    <div className="right-view workflows-view">
      {live.error && <p className="read-failure">Couldn't read the workflows ({live.error}); trying again.</p>}
      {live.value && current.length === 0 && <p className="workflows-empty">No workflows</p>}
      <ul className="workflows-list">
        {current.map((w) => (
          <li key={w.id} className={`workflow-item${FINISHED.has(w.state) ? " dismissible" : ""}`}>
            <WorkflowRow workflow={w} selected={w.id === open} />
            {FINISHED.has(w.state) && (
              <button type="button" className="workflow-dismiss" aria-label="Dismiss"
                      title="Dismiss" onClick={() => void dismissWorkflow(w.id)}>×</button>
            )}
          </li>
        ))}
      </ul>
      {done.length > 0 && (
        <div className="workflows-done">
          <button type="button" className="workflows-done-toggle" aria-expanded={showDone}
                  onClick={() => setShowDone(!showDone)}>
            {showDone ? "▾" : "▸"} Done ({done.length})
          </button>
          {showDone && (
            <ul className="workflows-list done">
              {done.map((w) => (
                <li key={w.id}><DoneRow workflow={w} selected={w.id === open} /></li>
              ))}
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
  return (
    <button type="button" data-workflow-id={w.id} data-state={w.state}
            className={`workflow-done-row${selected ? " selected" : ""}`}
            onClick={() => openWorkflow.set(w.id)}>
      <span className={`workflow-outcome-dot outcome-${w.state}`} />
      <span className="workflow-done-name">{w.name}</span>
      <span className="workflow-dismissed-mark">Dismissed</span>
    </button>
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
  const finished = FINISHED.has(w.state);
  return (
    <button type="button" data-workflow-id={w.id} data-state={w.state}
            className={`workflow-row${finished ? ` finished outcome-${w.state}` : ""}${
              selected ? " selected" : ""}`}
            title={`${w.name}: ${STATE_WORDS[w.state]}`}
            onClick={() => openWorkflow.set(w.id)}>
      <WorkflowOrb state={w.state} size={30} />
      <span className="workflow-name">{w.name}</span>
      {finished
        ? <span className={`workflow-outcome outcome-${w.state}`}>{STATE_WORDS[w.state]}</span>
        : <span className="workflow-age">{ago(w.last_event_at)}</span>}
      <span className="workflow-strip">
        <span className="workflow-track"><PhaseTrack w={w} /></span>
        <span className="workflow-counts">
          {w.tasks_total > 0 && (
            <span className="workflow-tasks" aria-label="Tasks done">{w.tasks_done}/{w.tasks_total}</span>
          )}
          {!finished && <OpenIcons w={w} />}
        </span>
      </span>
    </button>
  );
}
