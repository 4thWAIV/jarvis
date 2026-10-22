/**
 * The microphone's shared state: its live level and whether it is muted.
 * jarvis.ts measures the level and acts on mute; the mic control (MicControl.tsx)
 * draws the level and toggles mute.
 */

import { createStore } from "./store";

export const micLevel = createStore(0);
export const micMuted = createStore(false);
