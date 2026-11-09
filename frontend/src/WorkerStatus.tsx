import { ParticleOrb, WORKFLOW_PALETTE } from "./WorkflowVisuals";
import { useSyncExternalStore } from "react";
import { useT, type Translate } from "./locale";
import { brains, activeBrain } from "./brains";
import { useWorkers, findWorker, type WorkerCounts, type Inventory, type Worker } from "./workers";

function words(t: Translate, c: WorkerCounts) {
  return [t("left.workers.modelWork", {count: c.model_work}),
    c.unknown ? t("left.workers.unconfirmed", {count: c.unknown}) : "",
    c.tool_wait ? t("left.workers.tools", {count: c.tool_wait}) : "",
    c.queued ? t("left.workers.queued", {count: c.queued}) : ""].filter(Boolean).join(" · ");
}
function WorkerTree({ worker, inventory, ancestors = [] }: { worker: Worker; inventory: Inventory; ancestors?: string[] }) {
  const t = useT();
  if (ancestors.includes(worker.id)) return <li>{t("left.workers.parentCycle", {name: worker.name})}</li>;
  const children = inventory.workers.filter(w => w.parent_id === worker.id);
  return <li>
    <div><strong>{worker.name}</strong> <small>{worker.provider} · {worker.kind}</small></div>
    <div>{worker.activity.state.replace(/_/g, " ")} · {words(t, worker.counts)}</div>
    {worker.activity.state === "unknown" && worker.activity.last_phase && worker.activity.last_phase !== "unknown" && <small>{t("left.workers.lastPhase", {phase: worker.activity.last_phase.replace(/_/g, " ")})}</small>}
    {worker.activity.evidence_age_seconds != null && <small>{t("left.workers.lastEvidence", {count: Math.round(worker.activity.evidence_age_seconds)})}</small>}
    {worker.runtime?.status && <small>{t("left.workers.runtime", {status: worker.runtime.status, allocation: worker.allocation ?? t("left.workers.unknown")})}</small>}
    {worker.native_hook_coverage === "not_observed" && <small>{t("left.workers.hooksMissing")}</small>}
    {worker.workflow_id && <small>{t("left.workers.workflow", {id: worker.workflow_id})}</small>}
    {!!children.length && <details><summary>{t("left.workers.children", {count: children.length})}</summary><ul>{children.map(child => <WorkerTree key={child.id} worker={child} inventory={inventory} ancestors={[...ancestors, worker.id]} />)}</ul></details>}
  </li>;
}
export function WorkerTotal({ provider, compact = false }: { provider?: string; compact?: boolean } = {}) {
  const t = useT();
  const brainList = useSyncExternalStore(brains.subscribe, brains.get);
  const account = provider ?? activeBrain(brainList.value);
  const { value, error } = useWorkers();
  const roots = value?.workers.filter(w => !w.parent_id || !value.workers.some(p => p.id === w.parent_id)) ?? [];
  const projects = [...new Set(roots.map(w => w.project))].sort((a, b) => {
    const latest = (project: string) => Math.max(0, ...value!.workers.filter(w => w.project === project).map(w => w.activity.observed_at ?? 0));
    return latest(b) - latest(a);
  });
  const count = compact ? (account ? value?.providers[account] : undefined) : value?.total;
  const label = count ? words(t, count) : t("left.workers.unavailable");
  return <details className={`worker-total${compact ? " weekly-avatar worker-counter" : ""}`} title={error ?? label}>
    <summary aria-label={t("left.workers.label", {account: account ?? t("left.workers.all"), activity: label})}>
      {compact ? <><span>{count?.model_work ?? "—"}</span>{!!count?.unknown && <small>+?</small>}</> : label}
    </summary>
    <div className="worker-inventory">
      <h4>{t("left.workers.allWorkers")}</h4>
      {value && <p><strong>{t("left.workers.runtimeActive", {count: value.total.runtime_active})}</strong> · {words(t, value.total)}</p>}
      <p>{error ?? value?.coverage}</p>
      {value?.capacity && <p>{t("left.workers.capacity", {occupied: value.capacity.occupied, queued: value.capacity.queued, free: value.capacity.free})}</p>}
      {value?.gaps.map(g => <p key={g}>{g}</p>)}
      {value && projects.map(project => <section key={project}>
        <h5>{project || "JARVIS"}</h5>
        {value.projects[project] && <p>{words(t, value.projects[project])}</p>}
        <ul>{roots.filter(w => w.project === project).map(w => <WorkerTree key={w.id} worker={w} inventory={value} />)}</ul>
      </section>)}
    </div>
  </details>;
}
export function WorkerFamily({ sessionId, provider }: { sessionId: string; provider?: string | null }) {
  const t = useT();
  const { value, error } = useWorkers();
  const root = findWorker(value, sessionId, provider);
  return <span className="agent-subagents" title={error ?? value?.coverage}>
    {root ? words(t, root.counts) : t("left.workers.unknown")}
  </span>;
}

/** Work's owner activity is separate from its assignment/completion state. */
export function WorkOwnerActivity({owner}: {owner: {kind: string; ref: string | null}}) {
  const {value} = useWorkers();
  const t = useT();
  if (!owner.ref || owner.kind === "tim") return null;
  const child = owner.ref.split("/sub:");
  const worker = findWorker(value, child[1] ?? owner.ref);
  const activity = owner.kind === "workflow" ? value?.workflow_counts?.[owner.ref] : worker?.activity;
  return <span data-worker-snapshot={value?.snapshot_id} title={value?.coverage}>
    <ParticleOrb palette={WORKFLOW_PALETTE} look={{state: activity?.orb_state ?? "idle", gray: !activity}}
      dataState={activity?.orb_state ?? "unknown"} className="workflow-orb" size={20} particles={120} />
    <small>{owner.kind === "workflow" && value?.workflow_counts?.[owner.ref]
      ? words(t, value.workflow_counts[owner.ref]) : worker?.activity.state.replace(/_/g, " ") ?? t("left.workers.unknown")}</small>
  </span>;
}
