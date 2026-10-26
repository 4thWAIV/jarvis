/**
 * The brains JARVIS has (/api/brains): for the Settings view's Brains section
 * (every card, with its account and setup checks) and for the Diagnostics
 * view (the brief list: which are added, which one is running). `brainsChanged`
 * moves on whenever something about them changes, such as a sign-in
 * finishing, so both read them again.
 */

import { liveRead } from "./liveRead";
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
  if (!res.ok) throw new Error(`the server answered ${res.status}`);
  return ((await res.json()) as { brains: BrainCard[] }).brains;
}

async function send(method: string, url: string, body: unknown): Promise<void> {
  const res = await fetch(url, { method, headers: { "Content-Type": "application/json" },
                                 body: JSON.stringify(body) });
  if (!res.ok) {
    const detail = await res.json().then((b) => b?.detail).catch(() => null);
    throw new Error(detail || `the server answered ${res.status}`);
  }
}

export const addBrain = (name: string) => send("POST", `/api/brains/${name}`, { action: "add" });
export const removeBrain = (name: string) => send("POST", `/api/brains/${name}`, { action: "remove" });
export const setModel = (name: string, model: string) => send("PUT", `/api/brains/${name}`, { model });
export const setDefault = (name: string) => send("POST", "/api/brains/default", { name });

/** Start the brain's own sign-in; returns the address to open. */
export async function startSignIn(name: string): Promise<string> {
  const res = await fetch(`/api/brains/${name}/sign-in`, { method: "POST" });
  if (!res.ok) {
    const detail = await res.json().then((b) => b?.detail).catch(() => null);
    throw new Error(detail || `the server answered ${res.status}`);
  }
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
