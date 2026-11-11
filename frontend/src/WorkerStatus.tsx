/**
 * Agent activity as the page shows it. Every word, number and orb here reads
 * the server's one answer to "is it working?" (`activity.working`, decided by
 * worker_inventory.working: an open turn, waiting on the model or running a
 * tool). Nothing is "unknown" and nothing is counted as unconfirmed.
 */
import { ParticleOrb, WORKFLOW_PALETTE } from "./WorkflowVisuals";
import { useSyncExternalStore } from "react";
import { useT, type Translate } from "./locale";
import { brains, activeBrain } from "./brains";
import { useWorkers, findWorker, isWorking, workingOnItem, type OwnedPiece, type WorkerCounts,
         type Inventory, type Worker } from "./workers";

function words(t: Translate, c: WorkerCounts) {
  return [t("left.workers.active", {count: c.active ?? 0}),
    c.tool_wait ? t("left.workers.tools", {count: c.tool_wait}) : "",
    c.queued ? t("left.workers.queued", {count: c.queued}) : ""].filter(Boolean).join(" · ");
}
function phaseWords(t: Translate, worker: Worker | undefined) {
  if (!isWorking(worker)) return t("left.workers.idle");
  return worker!.activity.phase === "tool" ? t("left.workers.inTool") : t("left.workers.working");
}
function WorkerTree({ worker, inventory, ancestors = [] }: { worker: Worker; inventory: Inventory; ancestors?: string[] }) {
  const t = useT();
  if (ancestors.includes(worker.id)) return <li>{t("left.workers.parentCycle", {name: worker.name})}</li>;
  // Only what works now, and what it works under: finished subagents stay in
  // the record and out of this list.
  const children = inventory.workers.filter(w => w.parent_id === worker.id && (isWorking(w) || w.counts.active > 0));
  return <li>
    <div><strong>{worker.name}</strong> <small>{worker.provider} · {worker.kind}</small></div>
    <div>{phaseWords(t, worker)}{worker.counts.active > (isWorking(worker) ? 1 : 0) ? ` · ${words(t, worker.counts)}` : ""}</div>
    {worker.workflow_id && <small>{t("left.workers.workflow", {id: worker.workflow_id})}</small>}
    {!!children.length && <ul>{children.map(child => <WorkerTree key={child.id} worker={child} inventory={inventory} ancestors={[...ancestors, worker.id]} />)}</ul>}
  </li>;
}
export function WorkerTotal({ provider, compact = false }: { provider?: string; compact?: boolean } = {}) {
  const t = useT();
  const brainList = useSyncExternalStore(brains.subscribe, brains.get);
  const account = provider ?? activeBrain(brainList.value);
  const { value, error } = useWorkers();
  const roots = value?.workers.filter(w => (!w.parent_id || !value.workers.some(p => p.id === w.parent_id))
                                           && w.counts.active > 0) ?? [];
  const projects = [...new Set(roots.map(w => w.project))].sort();
  // The brain count: every worker on this account working now.
  const count = compact ? (account ? value?.providers[account] : undefined) : value?.total;
  const label = value ? words(t, count ?? {active: 0} as WorkerCounts) : t("left.workers.unavailable");
  return <details className={`worker-total${compact ? " weekly-avatar worker-counter" : ""}`} title={error ?? label}>
    <summary aria-label={t("left.workers.label", {account: account ?? t("left.workers.all"), activity: label})}>
      {compact ? <span>{value ? count?.active ?? 0 : "—"}</span> : label}
    </summary>
    <div className="worker-inventory">
      <h4>{t("left.workers.allWorkers")}</h4>
      {value && <p>{words(t, value.total)}</p>}
      <p>{error ?? value?.coverage}</p>
      {value?.capacity && <p>{t("left.workers.capacity", {occupied: value.capacity.occupied, queued: value.capacity.queued, free: value.capacity.free})}</p>}
      {value && projects.map(project => <section key={project}>
        <h5>{project || "JARVIS"}</h5>
        <ul>{roots.filter(w => w.project === project).map(w => <WorkerTree key={w.id} worker={w} inventory={value} />)}</ul>
      </section>)}
    </div>
  </details>;
}

/** The line under an agent's name: how many of its subagents work now, and
 *  nothing while none do. */
export function WorkerFamily({ sessionId, provider }: { sessionId: string; provider?: string | null }) {
  const t = useT();
  const { value } = useWorkers();
  const root = findWorker(value, sessionId, provider);
  const working = root?.descendant_counts?.active ?? 0;
  if (!working) return null;
  return <span className="agent-subagents" title={value?.coverage}>
    {t("agents.working", {count: working})}
  </span>;
}

/** A piece owner's orb in the Work item's detail: thinking while it works
 *  on this piece (in progress and in a turn), never for work elsewhere. */
export function WorkOwnerActivity({piece}: {piece: OwnedPiece}) {
  const {value} = useWorkers();
  const t = useT();
  const owner = piece.who;
  if (!owner.ref || owner.kind === "tim") return null;
  const working = workingOnItem(value, [piece]) > 0;
  const words = working ? t("left.workers.working") : t("left.workers.idle");
  return <span data-worker-snapshot={value?.snapshot_id} title={words} aria-label={words}>
    <ParticleOrb palette={WORKFLOW_PALETTE} look={{state: working ? "thinking" : "idle", gray: false}}
      dataState={working ? "working" : "idle"} className="workflow-orb" size={20} particles={120} />
  </span>;
}

/** The one orb on the right of a Work row: still and empty when nobody works
 *  on the item, else thinking with how many of its agents work now. */
export function WorkRowActivity({pieces}: {pieces: OwnedPiece[]}) {
  const {value} = useWorkers();
  const t = useT();
  const count = workingOnItem(value, pieces);
  const words = t("right.work.agentsWorking", {count});
  return <span className="work-row-orb" data-working={count} data-worker-snapshot={value?.snapshot_id}
               title={words} aria-label={words}>
    <ParticleOrb palette={WORKFLOW_PALETTE} look={{state: count ? "thinking" : "idle", gray: false}}
      dataState={count ? "working" : "idle"} className="workflow-orb" size={18} particles={100} />
    {count > 0 && <span className="work-row-orb-count">{count}</span>}
  </span>;
}

export function WorkerPhase({sessionId}: {sessionId: string}) {
  const {value} = useWorkers();
  const t = useT();
  const worker = findWorker(value, sessionId);
  return <span data-worker-snapshot={value?.snapshot_id} data-working={isWorking(worker)}>{phaseWords(t, worker)}</span>;
}
