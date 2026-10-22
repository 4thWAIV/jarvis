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
