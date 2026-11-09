import { useSyncExternalStore } from "react";
import { createStore } from "./store";

export interface WorkerCounts { workers: number; generating: number; unknown: number; tool_wait: number; queued: number; runtime_active: number }
export interface Worker {
  id: string; session_id: string; provider: string; name: string; project: string;
  parent_id: string | null; kind: string; workflow_id: string | null;
  activity: { state: string; generating: boolean; observed_at?: number; source?: string };
  counts: WorkerCounts; runtime?: { status?: string }; allocation?: string;
}
export interface Inventory {
  workers: Worker[]; total: WorkerCounts; providers: Record<string, WorkerCounts>;
  projects: Record<string, WorkerCounts>; capacity?: { total: number; occupied: number; queued: number; free: number }; taken_at: number; coverage: string; gaps: string[];
}
const store = createStore<{ value: Inventory | null; error: string | null }>({ value: null, error: null });
let users = 0;
let timer: ReturnType<typeof setTimeout> | undefined;
let epoch = 0;
async function refresh(generation: number) {
  const controller = new AbortController();
  const deadline = setTimeout(() => controller.abort(), 2500);
  try {
    const res = await fetch("/api/workers", { signal: controller.signal });
    if (!res.ok) throw new Error(`Worker discovery: HTTP ${res.status}`);
    const value = await res.json() as Inventory;
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
