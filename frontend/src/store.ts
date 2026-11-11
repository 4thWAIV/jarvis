/**
 * A tiny shared value React components read with useSyncExternalStore and
 * jarvis.ts writes. One per piece of state the page's behaviour and its
 * controls both need.
 */

type Listener = () => void;

export interface Store<T> {
  get(): T;
  set(value: T): void;
  subscribe(listener: Listener): () => void;
}

/** A yes-or-no this browser remembers (localStorage, under `key`): a
 *  viewer's own convenience, such as a list he minimised. Storage that
 *  cannot be read is no; one that cannot be written keeps it for this page
 *  load only. */
export function rememberedFlag(key: string): Store<boolean> {
  let saved = false;
  try {
    saved = window.localStorage.getItem(key) === "1";
  } catch {
    // No storage: not set.
  }
  const store = createStore(saved);
  return {
    ...store,
    set(next: boolean) {
      store.set(next);
      try {
        if (next) window.localStorage.setItem(key, "1");
        else window.localStorage.removeItem(key);
      } catch {
        // Kept for this page load only.
      }
    },
  };
}

export function createStore<T>(initial: T): Store<T> {
  let value = initial;
  const listeners = new Set<Listener>();
  return {
    get: () => value,
    set(next: T) {
      if (next === value) return;
      value = next;
      listeners.forEach((listener) => listener());
    },
    subscribe(listener: Listener) {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
  };
}
