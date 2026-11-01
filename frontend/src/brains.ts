/**
 * The brains JARVIS has (/api/brains): for the Settings view's Brains section
 * (every card, with its account and setup checks) and for the Diagnostics
 * view (the brief list: which are added, which one is running). `brainsChanged`
 * moves on whenever something about them changes, such as a sign-in
 * finishing, so both read them again.
 */

import { liveRead } from "./liveRead";
import { t } from "./locale";
import { createStore } from "./store";

export interface BrainCheck {
  name: string;
  status: "ok" | "warn" | "fail";
  message: string;
  remedy: string | null;
}

export interface BrainCard {
  name: string;
  title: string;
  added: boolean;
  default: boolean;
  active: boolean;
  model: string;
  models?: string[];
  account?: { signed_in: boolean; plan: string; detail: string };
  checks?: BrainCheck[];
  signs_in_here?: boolean;
}

export const brainsChanged = createStore(0);

export function brainsMayHaveChanged(): void {
  brainsChanged.set(brainsChanged.get() + 1);
}

export async function listBrains(brief = false): Promise<BrainCard[]> {
  const res = await fetch(brief ? "/api/brains?brief=1" : "/api/brains");
  if (!res.ok) throw new Error(t("left.common.serverAnswered", { status: String(res.status) }));
  return ((await res.json()) as { brains: BrainCard[] }).brains;
}

/** A refusal of a brains change: brains_config's `code` and `values`
 *  (brains_config.Refused) worded in the language in force, else the
 *  server's own words as they came. */
const REFUSALS: Record<string, (values: Record<string, string>) => string> = {
  "unknown-brain": (v) => t("left.brains.refused.unknownBrain", { name: v.name ?? "" }),
  "always-there": () => t("left.brains.refused.alwaysThere"),
  "default-first": () => t("left.brains.refused.defaultFirst"),
  "not-added": (v) => t("left.brains.refused.notAdded", { name: v.name ?? "" }),
};

async function refusal(res: Response): Promise<Error> {
  const detail = await res.json().then((b) => b?.detail).catch(() => null) as
    string | { code?: string; values?: Record<string, string>; message?: string } | null;
  if (detail && typeof detail === "object") {
    const words = detail.code ? REFUSALS[detail.code] : undefined;
    if (words) return new Error(words(detail.values ?? {}));
    if (detail.message) return new Error(detail.message);
  }
  if (typeof detail === "string" && detail) return new Error(detail);
  return new Error(t("left.common.serverAnswered", { status: String(res.status) }));
}

async function send(method: string, url: string, body: unknown): Promise<void> {
  const res = await fetch(url, { method, headers: { "Content-Type": "application/json" },
                                 body: JSON.stringify(body) });
  if (!res.ok) throw await refusal(res);
}

export const addBrain = (name: string) => send("POST", `/api/brains/${name}`, { action: "add" });
export const removeBrain = (name: string) => send("POST", `/api/brains/${name}`, { action: "remove" });
export const setModel = (name: string, model: string) => send("PUT", `/api/brains/${name}`, { model });
export const setDefault = (name: string) => send("POST", "/api/brains/default", { name });

/** Start the brain's own sign-in; returns the address to open. */
export async function startSignIn(name: string): Promise<string> {
  const res = await fetch(`/api/brains/${name}/sign-in`, { method: "POST" });
  if (!res.ok) throw await refusal(res);
  return ((await res.json()) as { url: string }).url;
}

/** The brief list (which brains are added, which one runs), read at load,
 *  on every reconnect, when a brain becomes ready and after a Settings
 *  change, and retried until it is read. The Diagnostics view and the orb's
 *  logo both draw from it. */
export const brains = liveRead(() => listBrains(true), [brainsChanged]);

/** The brain running now, by provider name, or null when none is known. */
export function activeBrain(cards: BrainCard[] | null): string | null {
  return cards?.find((b) => b.active)?.name ?? null;
}
