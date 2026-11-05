/**
 * The microphone's shared state: its live level, the quiet preference
 * (none, muted or off), what voice input is doing, and the two voice input
 * settings. jarvis.ts measures the level, acts on the quiet preference and
 * reports the input's status; the mic control (MicControl.tsx) draws the
 * level, toggles mute and off, offers Start listening and shows the status
 * until it is resolved.
 *
 * The quiet preference is this browser's, kept in localStorage and read
 * here, synchronously, when the module loads: before jarvis.ts starts and
 * so before any getUserMedia. A reload keeps Muted and Mic off; it never
 * keeps listening, which needs a deliberate Start in every page load (the
 * listening grant is voiceInput.ts's, in memory only). Storage that cannot
 * be read, or holds anything but a known value, reads as no preference,
 * which is not listening either: nothing is inferred from a failure.
 *
 * Another tab of this browser changing the preference is heard through the
 * `storage` event, so Mute and Mic off reach every tab (`quietBy` says the
 * change came from a sibling, which never arms this tab).
 */

import { createStore } from "./store";
import type { InputStatus } from "./voiceInput";
import type { VoiceInputSettings } from "./voiceControl";

export type Quiet = "none" | "muted" | "off";
const QUIETS: readonly Quiet[] = ["none", "muted", "off"];
export const QUIET_KEY = "jarvis.voice.quiet";

/** The saved quiet preference, or "none" when there is none, it cannot be
 *  read, or it is not one of the three. */
export function readQuiet(): Quiet {
  try {
    const value = window.localStorage.getItem(QUIET_KEY);
    return QUIETS.includes(value as Quiet) ? value as Quiet : "none";
  } catch {
    return "none";
  }
}

function saveQuiet(value: Quiet): void {
  try {
    if (value === "none") window.localStorage.removeItem(QUIET_KEY);
    else window.localStorage.setItem(QUIET_KEY, value);
  } catch { /* not kept: this page still applies it */ }
}

export const micLevel = createStore(0);
export const quiet = createStore<Quiet>(readQuiet());
/** Who made the last change to `quiet`: this page's own controls (his
 *  click, or the brain asking for quiet), or another tab of this browser. */
export let quietBy: "here" | "sibling" = "here";
export const inputStatus = createStore<InputStatus>(
  { state: quiet.get() === "none" ? "disarmed" : quiet.get() === "off" ? "off" : "muted-inactive",
    message: "", words: null });
/** The two voice input settings in force for this user, or null until
 *  they have been read: Start listening waits for them. */
export const voiceInputSettings = createStore<VoiceInputSettings | null>(null);

/** The quiet preference chosen on this page: saved first, then applied. */
export function chooseQuiet(value: Quiet): void {
  saveQuiet(value);
  quietBy = "here";
  quiet.set(value);
}

/** Start listening, on his click: jarvis.ts puts its handler here. */
export const startListening = { current: () => {} };

if (typeof window !== "undefined") {
  window.addEventListener("storage", (event) => {
    if (event.key !== QUIET_KEY && event.key !== null) return;
    quietBy = "sibling";
    quiet.set(readQuiet());
  });
}
