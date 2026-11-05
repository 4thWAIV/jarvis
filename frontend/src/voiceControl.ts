/**
 * The page's copy of the voice control contract's shared types
 * (jarvis/voice_control.py): the voice input settings, as the route and
 * the brain's tools give them.
 *
 *   stop    When I stop the microphone: keep the words already captured,
 *           or discard unfinished input.
 *   switch  When I switch away from JARVIS: continue a listening session
 *           already started, or stop listening.
 *   sounds  Capture sounds: a quiet tone when listening starts and when it
 *           stops (voice.ts `cue`), or none.
 */

export type StopMode = "keep" | "discard";
export type SwitchMode = "continue" | "stop";

export const STOP_MODES: readonly StopMode[] = ["keep", "discard"];
export const SWITCH_MODES: readonly SwitchMode[] = ["continue", "stop"];

/** The bounds the server accepts for his patience, in seconds
 *  (voice_control.PATIENCE_MIN, PATIENCE_MAX). */
export const PATIENCE_MIN = 0.5;
export const PATIENCE_MAX = 10;

export const VOICE_INPUT_SETTINGS_PATH = "/api/settings/voice-input";
/** The hint on /ws/sessions that the settings changed. */
export const VOICE_INPUT_SETTINGS_FRAME = "voice_input_settings";

export interface VoiceInputSettings {
  stop: StopMode;
  switch: SwitchMode;
  /** His patience: seconds of quiet that end his turn, and before JARVIS
   *  may start talking (applies from the next time the page connects). */
  patience: number | null;
  /** Capture sounds: on (the default) or off. */
  sounds: boolean;
  /** Whether he chose them (false: the defaults, nothing saved yet). */
  saved: boolean;
  /** Whose they are: the team member signed in, or this JARVIS's owner. */
  scope: "member" | "local";
}

/** A settings answer, or null when it is not one. */
export function parseVoiceInputSettings(value: unknown): VoiceInputSettings | null {
  if (!value || typeof value !== "object") return null;
  const v = value as Record<string, unknown>;
  if (!STOP_MODES.includes(v.stop as StopMode) || !SWITCH_MODES.includes(v.switch as SwitchMode)) return null;
  const patience = typeof v.patience === "number" && Number.isFinite(v.patience) ? v.patience : null;
  return { stop: v.stop as StopMode, switch: v.switch as SwitchMode, patience,
           sounds: v.sounds !== false, saved: v.saved === true,
           scope: v.scope === "member" ? "member" : "local" };
}

// ── the voice socket's shared words ──────────────────────────────────────

/** How a reply's text ended (`outcome` on its last `reply` frame). */
export type ReplyOutcome = "succeeded" | "failed" | "cancelled";
export const REPLY_OUTCOMES: readonly ReplyOutcome[] = ["succeeded", "failed", "cancelled"];

/** Why a reply failed (`reason` beside `outcome: failed`; `resetsAt` for a
 *  usage limit): voice_control.ReplyFailure. */
export type ReplyFailure = "not_ready" | "down" | "sign_in" | "rate_limited" | "error" | "lost";
export const REPLY_FAILURES: readonly ReplyFailure[] = ["not_ready", "down", "sign_in", "rate_limited", "error", "lost"];

/** The `playback` frame: what only the page can know about one chunk. */
export const PLAYBACK_FRAME = "playback";
export type PlaybackReport = "started" | "failed";
