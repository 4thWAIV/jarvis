/**
 * What JARVIS is doing, shared between jarvis.ts (which knows) and the
 * status control (StatusControl.tsx, which shows it).
 */

import type { OrbState } from "./orb";
import { createStore } from "./store";

export const voiceState = createStore<OrbState>("idle");
// The status line: "listening...", "thinking...", or a notice the server sent.
export const statusText = createStore("");
// Stopping him mid-sentence; jarvis.ts puts its handler here.
export const stopSpeaking = { current: () => {} };
