import { useSyncExternalStore } from "react";
import { createStore } from "./store";

export interface WorkerCounts { orb_state?: "thinking" | "idle" | "unknown"; active: number; workers: number; model_work: number; generating: number; unknown: number; tool_wait: number; queued: number; runtime_active: number }
export interface Worker {
  id: string; session_id: string; provider: string; name: string; project: string;
  native_hook_coverage?: string; parent_id: string | null; kind: string; workflow_id: string | null;
  activity: { working: boolean; phase?: "model" | "tool" | null; orb_state?: "thinking" | "idle" | "unknown"; active?: boolean; turn_open?: boolean | null; work_state?: string; state: string; last_phase?: string; generating: boolean; observed_at?: number; source?: string; evidence_age_seconds?: number; model_work?: boolean };
  counts: WorkerCounts; descendant_counts?: WorkerCounts; runtime?: { status?: string }; allocation?: string;
}
export interface Inventory {
  snapshot_id?: string;
  workflow_counts?: Record<string, WorkerCounts>; workers: Worker[]; total: WorkerCounts; providers: Record<string, WorkerCounts>;
  projects: Record<string, WorkerCounts>; capacity?: { total: number; occupied: number; queued: number; free: number }; taken_at: number; coverage: string; gaps: string[];
}
const store = createStore<{ value: Inventory | null; error: string | null }>({ value: null, error: null });
function record(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
function counts(value: unknown): value is WorkerCounts {
  return record(value) && ["workers", "active", "model_work", "generating", "unknown", "tool_wait", "queued", "runtime_active"]
    .every(key => typeof value[key] === "number" && Number.isFinite(value[key]) && Number(value[key]) >= 0);
}
function inventory(value: unknown): value is Inventory {
  if (!record(value) || typeof value.snapshot_id !== "string" || !Array.isArray(value.workers) || !counts(value.total)
      || !record(value.providers) || !Object.values(value.providers).every(counts)
      || !record(value.projects) || !Object.values(value.projects).every(counts)
      || !Array.isArray(value.gaps) || !value.gaps.every(gap => typeof gap === "string")
      || typeof value.coverage !== "string") return false;
  return value.workers.every(worker => record(worker) && counts(worker.counts)
    && ["id", "session_id", "provider", "name", "project", "kind"].every(key => typeof worker[key] === "string")
    && record(worker.activity) && typeof worker.activity.state === "string");
}
let users = 0;
let timer: ReturnType<typeof setTimeout> | undefined;
let epoch = 0;
async function refresh(generation: number) {
  const controller = new AbortController();
  const deadline = setTimeout(() => controller.abort(), 2500);
  try {
    const res = await fetch("/api/workers", { signal: controller.signal });
    if (!res.ok) throw new Error(`Worker discovery: HTTP ${res.status}`);
    const value: unknown = await res.json();
    if (!inventory(value)) throw new Error("Worker discovery: invalid inventory response");
    if (generation === epoch) store.set({ value, error: null });
  } catch (e) {
    if (generation === epoch) store.set({ value: null, error: String(e) });
  } finally { clearTimeout(deadline); }
  if (users && generation === epoch) timer = setTimeout(() => refresh(generation), 1000);
}
function subscribe(callback: () => void) {
  const unsubscribe = store.subscribe(callback);
  if (++users === 1) { ++epoch; void refresh(epoch); }
  return () => {
    unsubscribe();
    if (--users === 0) { ++epoch; clearTimeout(timer); }
  };
}
export function useWorkers() { return useSyncExternalStore(subscribe, store.get); }

/** Identity lookup only; activity is computed by the server. */
export function findWorker(value: Inventory | null, sessionId: string, provider?: string | null): Worker | undefined {
  const matches = value?.workers.filter(w => w.session_id === sessionId && (!provider || w.provider === provider)) ?? [];
  return matches.length === 1 ? matches[0] : undefined;
}
/** Whether a worker is working: the server's one answer
 *  (worker_inventory.working, an open turn: waiting on the model, running a
 *  tool, or JARVIS's brain thinking). Nothing here works it out again. */
export function isWorking(worker: Worker | undefined): boolean {
  return worker?.activity.working === true;
}

export interface WorkOwner { kind: string; ref: string | null }
export interface OwnedPiece { state: string; who: WorkOwner }

/** Whether a piece ties its owner's activity to its item: the server's rule
 *  (work_store.in_progress), read from the state the piece is shown in. A
 *  piece reported working (shown working, or not working now) is in
 *  progress; so is a planned or ready piece a subagent was put on. A piece
 *  in review, done, blocked or waiting on him never is. */
export function pieceInProgress(piece: OwnedPiece): boolean {
  if (piece.state === "working" || piece.state === "activity_unknown") return true;
  return (piece.state === "planned" || piece.state === "ready") && !!piece.who.ref?.includes("/sub:");
}

/** How many agents work on a Work item now: each distinct owner of a piece
 *  in progress (`pieceInProgress`) that is working by the one answer; a
 *  workflow counts its working members. An agent working on something else
 *  never counts here. */
export function workingOnItem(value: Inventory | null, pieces: OwnedPiece[]): number {
  const seen = new Set<string>();
  let count = 0;
  for (const { who: owner } of pieces.filter(pieceInProgress)) {
    if (!owner.ref || owner.kind === "tim") continue;
    const key = `${owner.kind}:${owner.ref}`;
    if (seen.has(key)) continue;
    seen.add(key);
    if (owner.kind === "workflow") {
      count += value?.workflow_counts?.[owner.ref]?.active ?? 0;
      continue;
    }
    const sub = owner.ref.split("/sub:");
    if (isWorking(findWorker(value, sub[1] ?? sub[0]))) count += 1;
  }
  return count;
}

export function useWorker(sessionId: string, provider?: string | null): Worker | undefined {
  return findWorker(useWorkers().value, sessionId, provider);
}
export const workerSnapshot = store.get;
export const subscribeWorkers = subscribe;

export const workerChanges = { subscribe, get: store.get };
