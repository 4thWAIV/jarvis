/**
 * What JARVIS is doing, shared between jarvis.ts (which knows) and the
 * status control (StatusControl.tsx, which shows it). The store holds the
 * state alone; the status line's words are the control's, in the
 * interface's language, so a language change rewords it at once.
 */

import type { OrbState } from "./orb";
import { createStore } from "./store";

export const voiceState = createStore<OrbState>("idle");
// Stop speaking: all of his audio, now (the status control's Stop).
// jarvis.ts puts its handler here.
export const stopSpeaking = { current: () => {} };
// Skip the rest: only the reading sounding now (the playback bar's Skip and
// Escape), never another reading, never the request. jarvis.ts puts its
// handler here.
export const skipReading = { current: () => {} };
// Sending what Tim typed into the white box instead of saying it; jarvis.ts
// puts its handler here.
export const sendTyped = { current: (_text: string): boolean => false };

// Connection and explicit user navigation, shared by the composer/playback UI.
export const voiceConnected = createStore(false);
export const conversationJump = createStore(0);
