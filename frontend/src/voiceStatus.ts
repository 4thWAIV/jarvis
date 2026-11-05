/**
 * What JARVIS is doing, shared between jarvis.ts (which knows) and the
 * status control (StatusControl.tsx, which shows it). The store holds the
 * state alone; the status line's words are the control's, in the
 * interface's language, so a language change rewords it at once.
 */

import type { OrbState } from "./orb";
import { createStore } from "./store";

export const voiceState = createStore<OrbState>("idle");
// Stopping him mid-sentence; jarvis.ts puts its handler here.
export const stopSpeaking = { current: () => {} };
// Sending what Tim typed into the white box instead of saying it; jarvis.ts
// puts its handler here.
export const sendTyped = { current: (_text: string): boolean => false };

// Connection and explicit user navigation, shared by the composer/playback UI.
export const voiceConnected = createStore(false);
export const conversationJump = createStore(0);
