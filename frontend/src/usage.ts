/**
 * The page's side of the weekly usage history (usage_history.py): one
 * brain's history in the centre, opened from that brain's own box in the
 * left column (UsageHistoryView.tsx). Only the seven-day window is kept;
 * each week is drawn as its own burn-down, this week first, and earlier
 * weeks are reached one at a time. Every read is
 * `GET /api/usage/history?provider=`, the same call the brain's
 * usage_history makes, read when a brain's view opens and every minute
 * while it is open.
 *
 * `usage.open {provider}` shows that brain's view (never two brains
 * together) and `usage.close` closes it; `usage.week {to}` moves to the
 * earlier or later week, or back to this week, while a view is open.
 */

import { createStore } from "./store";
import { detailSlot, registerClose } from "./uiNavigation";
import { registerCommand } from "./uiRegistry";

export interface UsagePoint {
  at: number;
  /** Percentage used, or null: the reading did not measure it. */
  used: number | null;
  low: number | null;
  high: number | null;
  resets_at: number | null;
  status: string;
  account: string | null;
  samples: number;
}

export interface UsageWeek {
  /** When the week resets, and when it began (a week before). */
  resets_at: number;
  start: number;
  points: UsagePoint[];
  measured: number;
}

export interface UsageLatest {
  used: number | null;
  resets_at: number | null;
  observed_at: number | null;
  status: string;
}

export interface UsageHistory {
  provider: string;
  window: "seven_day";
  week_sec: number;
  since: number;
  until: number;
  resolution: "raw" | "hour";
  raw_keep_sec: number;
  latest: UsageLatest;
  /** Newest first. */
  weeks: UsageWeek[];
}

export interface Loaded { value: UsageHistory | null; error: string | null }

export const REFRESH_MS = 60_000;

const usageSlot = detailSlot<string>("usage");
/** The brain whose usage history is open in the centre, or null. Read-only:
 *  controls run the commands. */
export const openUsage = usageSlot.value;

/** The open brain's history as last read, and why the last read failed. */
export const usageHistory = createStore<Loaded>({ value: null, error: null });
/** Which week is shown: 0 is the newest, 1 the week before it, and so on. */
export const usageWeek = createStore(0);

export async function readUsageHistory(provider: string): Promise<UsageHistory> {
  const res = await fetch(`/api/usage/history?provider=${encodeURIComponent(provider)}`);
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error((body as { error?: string }).error ?? `/api/usage/history: ${res.status}`);
  return body as UsageHistory;
}

let timer: ReturnType<typeof setInterval> | null = null;

function load(provider: string): void {
  readUsageHistory(provider)
    .then((value) => { if (openUsage.get() === provider) usageHistory.set({ value, error: null }); })
    .catch((e: Error) => {
      if (openUsage.get() === provider) usageHistory.set({ value: usageHistory.get().value, error: e.message });
    });
}

// A brain's view opening reads its history from this week; closing stops.
let shown: string | null = null;
openUsage.subscribe(() => {
  const provider = openUsage.get();
  if (provider === shown) return;
  shown = provider;
  if (timer) { clearInterval(timer); timer = null; }
  usageHistory.set({ value: null, error: null });
  usageWeek.set(0);
  if (provider === null) return;
  load(provider);
  timer = setInterval(() => load(provider), REFRESH_MS);
});

const weekCount = () => usageHistory.get().value?.weeks.length ?? 0;

registerCommand("usage.open", {
  availability: () => ({ available: true }),
  execute: ({ provider }) => ({ status: "completed", effects: usageSlot.show(provider) }),
});

registerClose("usage.close", usageSlot);

registerCommand("usage.week", {
  availability: ({ to }) => {
    if (usageSlot.value.get() === null) return { available: false, reason: "no-entity" };
    const at = usageWeek.get();
    if (to === "earlier" && at + 1 >= weekCount()) return { available: false, reason: "no-entity" };
    if (to === "later" && at === 0) return { available: false, reason: "no-entity" };
    return { available: true };
  },
  execute: ({ to }) => {
    const at = usageWeek.get();
    usageWeek.set(to === "current" ? 0 : to === "earlier" ? Math.min(at + 1, Math.max(0, weekCount() - 1))
      : Math.max(0, at - 1));
    return { status: "completed", effects: ["list.paged"] };
  },
});
