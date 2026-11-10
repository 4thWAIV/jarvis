/**
 * The page's side of the usage history (usage_history.py): the centre view
 * opened from a brain's box in the left column (UsageHistoryView.tsx), and
 * the range it shows. Every read is `GET /api/usage/history`, the same call
 * the brain's usage_history makes.
 *
 * `usage.open` and `usage.close` show and close the view; `usage.range`
 * picks the last day, week or month, or all of it.
 */

import { createStore } from "./store";
import { detailSlot, registerClose } from "./uiNavigation";
import { registerCommand } from "./uiRegistry";
import type { CommandArgs } from "./uiContract";

export type UsageRange = CommandArgs["usage.range"]["range"];
export const RANGES: readonly UsageRange[] = ["day", "week", "month", "all"];

/** How far back each range reaches, in seconds; "all" reaches the start. */
export const RANGE_SEC: Record<UsageRange, number | null> = {
  day: 86_400, week: 7 * 86_400, month: 30 * 86_400, all: null,
};

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

export interface UsageLatest {
  window: string;
  used: number | null;
  resets_at: number | null;
  observed_at: number | null;
  status: string;
}

export interface UsageSeries {
  provider: string;
  window: string;
  label: string;
  points: UsagePoint[];
  resets: number[];
  latest: UsageLatest;
}

export interface UsageHistory {
  since: number;
  until: number;
  resolution: "raw" | "hour";
  gap_sec: number;
  raw_keep_sec: number;
  series: UsageSeries[];
}

export const usageRange = createStore<UsageRange>("day");

export async function readUsageHistory(range: UsageRange, nowMs: number = Date.now()): Promise<UsageHistory> {
  const until = Math.floor(nowMs / 1000);
  const back = RANGE_SEC[range];
  const since = back === null ? 0 : until - back;
  const res = await fetch(`/api/usage/history?since=${since}&until=${until}`);
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error((body as { error?: string }).error ?? `/api/usage/history: ${res.status}`);
  return body as UsageHistory;
}

const usageSlot = detailSlot<true>("usage");
/** Whether the usage history is open in the centre. Read-only: controls run the commands. */
export const openUsage = usageSlot.value;

registerCommand("usage.open", {
  availability: () => ({ available: true }),
  execute: () => ({ status: "completed", effects: usageSlot.show(true) }),
});

registerClose("usage.close", usageSlot);

registerCommand("usage.range", {
  availability: () => ({ available: true }),
  execute: ({ range }) => {
    const effects = usageSlot.show(true);
    usageRange.set(range);
    return { status: "completed", effects: [...effects, "list.filtered"] };
  },
});
