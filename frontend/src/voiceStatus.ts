/**
 * What JARVIS is doing, shared between jarvis.ts (which knows) and the
 * status control (StatusControl.tsx, which shows it). The store holds the
 * state alone; the status line's words are the control's, in the
 * interface's language, so a language change rewords it at once.
 */

import type { OrbState } from "./orb";
import { createStore } from "./store";
import type { CueName, PauseCue } from "./voiceControl";

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

// What he has said aloud and the server has gathered but not yet sent
// (the `turn` frames on /ws/voice): only words the server accepted as his,
// never echo. `stalled` is a turn the recogniser could not finish, waiting
// for his Retry, Send now or Keep as draft (VoiceTurnBar.tsx). `pause` is
// the pause cue the server last gave while he is quiet in a gathered turn
// (tentative: he may be done; deciding: still deciding; likely: about to be
// sent), shown whether or not its sound plays; any accepted speech clears
// it.
export type VoiceTurnState = "gathering" | "commit-pending" | "stalled";
export interface VoiceTurn { state: VoiceTurnState; turnId: number | null; text: string; pause?: PauseCue }
export const voiceTurn = createStore<VoiceTurn | null>(null);
// Send now, Retry and Keep as draft: the same calls as the brain's
// voice_turn. jarvis.ts puts its handler here.
export type VoiceTurnAction = "send_now" | "retry" | "keep_as_draft";
export const turnAction = { current: (_action: VoiceTurnAction): boolean => false };
// A capture cue played once, as Settings' Test buttons and the brain's
// play_cue play it: its tone at the cue volume, on or off. jarvis.ts puts
// its handler here.
export const cueTest = { current: (_cue: CueName): void => {} };

// Connection and explicit user navigation, shared by the composer/playback UI.
export const voiceConnected = createStore(false);
export const conversationJump = createStore(0);
