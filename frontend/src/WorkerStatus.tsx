import { useSyncExternalStore } from "react";
import { brains, activeBrain } from "./brains";
import { useWorkers, type WorkerCounts, type Inventory, type Worker } from "./workers";

function words(c: WorkerCounts) {
  return `${c.model_work} model work${c.unknown ? ` · ${c.unknown} unconfirmed` : ""}${c.tool_wait ? ` · ${c.tool_wait} in tools` : ""}${c.queued ? ` · ${c.queued} queued` : ""}`;
}
function WorkerTree({ worker, inventory, ancestors = [] }: { worker: Worker; inventory: Inventory; ancestors?: string[] }) {
  if (ancestors.includes(worker.id)) return <li>Parent cycle: {worker.name}</li>;
  const children = inventory.workers.filter(w => w.parent_id === worker.id);
  return <li>
    <div><strong>{worker.name}</strong> <small>{worker.provider} · {worker.kind}</small></div>
    <div>{worker.activity.state.replace(/_/g, " ")} · {words(worker.counts)}</div>
    {worker.activity.evidence_age_seconds != null && <small>Last evidence {Math.round(worker.activity.evidence_age_seconds)} seconds ago.</small>}
    {worker.runtime?.status && <small>Runtime: {worker.runtime.status}. Slot: {worker.allocation}.</small>}
    {worker.native_hook_coverage === "not_observed" && <small>Native lifecycle hooks not observed yet.</small>}
    {worker.workflow_id && <small>Workflow: {worker.workflow_id}</small>}
    {!!children.length && <details><summary>{children.length} child records</summary><ul>{children.map(child => <WorkerTree key={child.id} worker={child} inventory={inventory} ancestors={[...ancestors, worker.id]} />)}</ul></details>}
  </li>;
}
export function WorkerTotal({ provider, compact = false }: { provider?: string; compact?: boolean } = {}) {
  const brainList = useSyncExternalStore(brains.subscribe, brains.get);
  const account = provider ?? activeBrain(brainList.value);
  const { value, error } = useWorkers();
  const roots = value?.workers.filter(w => !w.parent_id || !value.workers.some(p => p.id === w.parent_id)) ?? [];
  const projects = [...new Set(roots.map(w => w.project))].sort((a, b) => {
    const latest = (project: string) => Math.max(0, ...value!.workers.filter(w => w.project === project).map(w => w.activity.observed_at ?? 0));
    return latest(b) - latest(a);
  });
  const count = compact ? (account ? value?.providers[account] : undefined) : value?.total;
  const label = count ? words(count) : "Activity unavailable";
  return <details className={`worker-total${compact ? " weekly-avatar worker-counter" : ""}`} title={error ?? label}>
    <summary aria-label={`${account ?? "All"} agent model work: ${label}`}>
      {compact ? <><span>{count?.model_work ?? "—"}</span>{!!count?.unknown && <small>+?</small>}</> : label}
    </summary>
    <div className="worker-inventory">
      <h4>All workers</h4>
      {value && <p><strong>{value.total.runtime_active} runtime-active</strong> · {value.total.model_work} model work · {value.total.tool_wait} in tools</p>}
      <p>{error ?? value?.coverage}</p>
      {value?.capacity && <p>Managed run slots: {value.capacity.occupied} occupied, {value.capacity.queued} queued, {value.capacity.free} free. External CLI slot reservations are unknown.</p>}
      {value?.gaps.map(g => <p key={g}>{g}</p>)}
      {value && projects.map(project => <section key={project}>
        <h5>{project || "JARVIS"}</h5>
        {value.projects[project] && <p>{words(value.projects[project])}</p>}
        <ul>{roots.filter(w => w.project === project).map(w => <WorkerTree key={w.id} worker={w} inventory={value} />)}</ul>
      </section>)}
    </div>
  </details>;
}
export function WorkerFamily({ sessionId }: { sessionId: string }) {
  const { value, error } = useWorkers();
  const root = value?.workers.find(w => w.session_id === sessionId);
  return <span className="agent-subagents" title={error ?? value?.coverage}>
    {root ? words(root.counts) : "Activity unknown"}
  </span>;
}
