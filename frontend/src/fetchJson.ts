/**
 * The page's one plain read of a JSON route, and its one way to post his
 * answer to an open decision (a workflow's, workflows.ts, and a work
 * item's, work.ts, go to their own routes the same way).
 */

import { t } from "./locale";
import type { Store } from "./store";

/** GET a route's JSON; a status that is not ok throws. */
export async function getJson<T>(path: string): Promise<T> {
  const res = await fetch(path);
  if (!res.ok) throw new Error(`GET ${path}: ${res.status}`);
  return (await res.json()) as T;
}

/** Post his answer to `url`; once recorded, move `changed` so every view
 *  reads the record again. Resolves with the refusal's reason, or null. */
export async function postAnswer(url: string, answer: string,
                                 changed: Store<number>): Promise<string | null> {
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ answer }),
  }).catch(() => null);
  if (res?.ok) {
    changed.set(changed.get() + 1);
    return null;
  }
  if (!res) return t("right.workflows.noAnswer");
  const body = (await res.json().catch(() => ({}))) as { error?: string; detail?: string };
  return body.error || body.detail || t("right.workflows.refused", { status: String(res.status) });
}
