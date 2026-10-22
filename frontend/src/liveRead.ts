/**
 * Reading the server's state so the page never stays wrong.
 *
 * A read made once, with its failure thrown away, left the page showing the
 * wrong brain after every restart: the page reloads while the server is still
 * coming back, the one read fails, and nothing asks again. So every read of
 * server state goes through `liveRead`:
 *
 *   - it reads at once, and again whenever the voice socket (re)connects
 *     (`reconnected`) and whenever one of its `when` stores moves;
 *   - a read that fails is retried, sooner at first and then every
 *     RETRY_MAX_MS, until one succeeds;
 *   - the failure is not hidden: it lands in the returned store's `error`
 *     until a read succeeds, for the view to say.
 */

import { useEffect, useState, useSyncExternalStore } from "react";
import { createStore, type Store } from "./store";

// Moves on every (re)connect of the voice socket (jarvis.ts).
export const reconnected = createStore(0);

const RETRY_FIRST_MS = 1000;
const RETRY_MAX_MS = 15000;

export interface Live<T> {
  value: T | null;
  error: string | null;
}

export interface LiveStore<T> extends Store<Live<T>> {
  /** Read again now. */
  refresh(): void;
  /** Stop reading (for a view that is closing). */
  stop(): void;
}

export function liveRead<T>(read: () => Promise<T>, when: Store<unknown>[] = []): LiveStore<T> {
  const store = createStore<Live<T>>({ value: null, error: null });
  let retry: ReturnType<typeof setTimeout> | null = null;
  let wait = RETRY_FIRST_MS;
  let asked = 0;
  let stopped = false;

  const refresh = () => {
    if (stopped) return;
    if (retry) { clearTimeout(retry); retry = null; }
    const mine = ++asked;
    read().then((value) => {
      if (mine !== asked) return;               // a newer read is on its way
      wait = RETRY_FIRST_MS;
      store.set({ value, error: null });
    }).catch((e: Error) => {
      if (mine !== asked) return;
      store.set({ value: store.get().value, error: e.message || "the server did not answer" });
      retry = setTimeout(refresh, wait);
      wait = Math.min(wait * 2, RETRY_MAX_MS);
    });
  };

  const unsubscribe = [reconnected, ...when].map((trigger) => trigger.subscribe(refresh));
  const stop = () => {
    stopped = true;
    if (retry) clearTimeout(retry);
    unsubscribe.forEach((u) => u());
  };
  refresh();
  return Object.assign(store, { refresh, stop });
}

/** `liveRead` for one component's lifetime. */
export function useLiveRead<T>(read: () => Promise<T>,
                               when: Store<unknown>[] = []): Live<T> & { refresh(): void } {
  const [live] = useState(() => liveRead(read, when));
  useEffect(() => live.stop, [live]);
  const state = useSyncExternalStore(live.subscribe, live.get);
  return { ...state, refresh: live.refresh };
}
