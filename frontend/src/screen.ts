/**
 * What is shown and never said. The server sends a `display` message for each
 * thing the brain puts on the screen (sources, links, a short list); the
 * Screen tab (ScreenView.tsx) draws them, newest first, and lights up while
 * there is one Tim has not looked at.
 */

import { liveRead } from "./liveRead";
import { createStore } from "./store";

export interface DisplayItem { label: string; url: string; host: string; note: string }
export interface Display {
  id: number;
  kind: "sources" | "links" | "list";
  title: string;
  items: DisplayItem[];
}

const KEEP = 20;

export const displays = createStore<Display[]>([]);
/** A display arrived since the Screen tab was last open. */
export const unseenDisplay = createStore(false);

export function addDisplay(display: Display): void {
  const known = displays.get();
  if (known.some((d) => d.id === display.id)) return;
  displays.set([...known, display].slice(-KEEP));
  unseenDisplay.set(true);
}

/** The ones kept on the server: read at load and on every reconnect, and
 *  retried until read (liveRead.ts). The Screen tab says so while they
 *  cannot be read. */
export const keptDisplays = liveRead(async () => {
  const res = await fetch("/api/displays");
  if (!res.ok) throw new Error(`the server answered ${res.status}`);
  return (((await res.json()) as { displays?: Display[] }).displays ?? []);
});

keptDisplays.subscribe(() => {
  const kept = keptDisplays.get().value;
  if (!kept) return;
  const seen = unseenDisplay.get();
  for (const d of kept) addDisplay(d);
  unseenDisplay.set(seen);              // what was already there is not news
});
