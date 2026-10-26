/**
 * The microphone's shared state: its live level, whether it is muted, and
 * what voice input is doing. jarvis.ts measures the level, acts on mute and
 * reports the input's status; the mic control (MicControl.tsx) draws the
 * level, toggles mute and shows the status until it is resolved.
 */

import { createStore } from "./store";
import type { InputStatus } from "./voiceInput";

export const micLevel = createStore(0);
export const micMuted = createStore(false);
export const inputStatus = createStore<InputStatus>({ state: "starting", message: "" });
