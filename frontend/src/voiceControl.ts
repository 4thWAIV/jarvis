/**
 * The page's copy of the voice control contract's shared types
 * (jarvis/voice_control.py): the voice input settings, as the route and
 * the brain's tools give them.
 *
 *   stop    When I stop the microphone: keep the words already captured,
 *           or discard unfinished input.
 *   switch  When I switch away from JARVIS: continue a listening session
 *           already started, or stop listening.
 *   sounds  Capture sounds: the master switch for every cue (voice.ts
 *           `cue`): off plays none.
 *   cues    The five capture cues, each on or off with its tone: start and
 *           stop (listening started, stopped), and the pause cues in the
 *           order they play, tentative, deciding and likely, each `before`
 *           seconds before the end of his turn (it plays at patience -
 *           before seconds of quiet, and not at all when that is not after
 *           he stopped); each earlier one's `before` is the larger.
 *   volume  One cue volume, in percent of a quiet ceiling.
 */

export type StopMode = "keep" | "discard";
export type SwitchMode = "continue" | "stop";

export const STOP_MODES: readonly StopMode[] = ["keep", "discard"];
export const SWITCH_MODES: readonly SwitchMode[] = ["continue", "stop"];

/** The bounds the server accepts for his patience, in seconds
 *  (voice_control.PATIENCE_MIN, PATIENCE_MAX). */
export const PATIENCE_MIN = 0.5;
export const PATIENCE_MAX = 10;

/** The capture cues (voice_control.CUES), the pause cues among them in the
 *  order they play (voice_control.PAUSE_CUES), and the quiet presets each
 *  may play (voice_control.TONES; voice.ts holds their notes). */
export const PAUSE_CUES = ["tentative", "deciding", "likely"] as const;
export const CUE_NAMES = ["start", "stop", ...PAUSE_CUES] as const;
export type PauseCue = (typeof PAUSE_CUES)[number];
export type CueName = (typeof CUE_NAMES)[number];
/** Whether a cue is one of the pause cues. */
export function isPauseCue(name: unknown): name is PauseCue {
  return (PAUSE_CUES as readonly unknown[]).includes(name);
}
export type Tone = "rise" | "fall" | "pulse" | "low" | "double" | "resolve";
export const TONES: readonly Tone[] = ["rise", "fall", "pulse", "low", "double", "resolve"];
/** A pause cue's seconds before the end of his turn, and the cue volume in
 *  percent (voice_control.BEFORE_MIN/MAX, VOLUME_MIN/MAX). */
export const BEFORE_MIN = 0.25;
export const BEFORE_MAX = 10;
export const VOLUME_MIN = 5;
export const VOLUME_MAX = 100;

export interface CueSetting { on: boolean; tone: Tone; before?: number }
export type CueSettings = Record<CueName, CueSetting>;

/** When a pause cue plays at this patience: seconds of quiet, or null
 *  when it falls at or before the moment he stopped. */
export function pauseCueAt(patience: number | null, cue: CueSetting): number | null {
  if (patience == null || cue.before == null) return null;
  const at = Math.round((patience - cue.before) * 100) / 100;
  return at > 0 ? at : null;
}

/** The gaps around one pause cue at this patience, each tuned by that
 *  cue's own `before` and its neighbour's (there is no shared spacing):
 *  the seconds since the nearest earlier pause cue that is on and plays,
 *  and until the nearest later one, or until what he said is sent when
 *  none later plays (`next: null`). Null when this cue does not play. */
export interface PauseCueGaps {
  after: { cue: PauseCue; gap: number } | null;
  until: { next: PauseCue | null; gap: number };
}
export function pauseCueGaps(patience: number | null, cues: CueSettings, name: PauseCue): PauseCueGaps | null {
  const at = pauseCueAt(patience, cues[name]);
  if (at == null) return null;
  const sounding = (n: PauseCue) => cues[n].on && pauseCueAt(patience, cues[n]) != null;
  const round = (seconds: number) => Math.round(seconds * 100) / 100;
  const i = PAUSE_CUES.indexOf(name);
  const earlier = PAUSE_CUES.slice(0, i).filter(sounding).pop();
  const later = PAUSE_CUES.slice(i + 1).find(sounding) ?? null;
  return {
    after: earlier ? { cue: earlier, gap: round(at - (pauseCueAt(patience, cues[earlier]) as number)) } : null,
    until: { next: later, gap: round(later ? (pauseCueAt(patience, cues[later]) as number) - at
                                           : cues[name].before as number) },
  };
}

export const VOICE_INPUT_SETTINGS_PATH = "/api/settings/voice-input";
/** The hint on /ws/sessions that the settings changed. */
export const VOICE_INPUT_SETTINGS_FRAME = "voice_input_settings";

export interface VoiceInputSettings {
  stop: StopMode;
  switch: SwitchMode;
  /** His patience: seconds of quiet that end his turn, and before JARVIS
   *  may start talking (applies from the next time the page connects). */
  patience: number | null;
  /** Capture sounds: on (the default) or off; the master switch. */
  sounds: boolean;
  /** Each capture cue. */
  cues: CueSettings;
  /** The cue volume, in percent of the quiet ceiling. */
  volume: number;
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
  const cues = parseCues(v.cues);
  if (!cues || typeof v.volume !== "number" || !Number.isFinite(v.volume)) return null;
  return { stop: v.stop as StopMode, switch: v.switch as SwitchMode, patience,
           sounds: v.sounds !== false, cues, volume: v.volume, saved: v.saved === true,
           scope: v.scope === "member" ? "member" : "local" };
}

function parseCues(value: unknown): CueSettings | null {
  if (!value || typeof value !== "object") return null;
  const out: Partial<CueSettings> = {};
  for (const name of CUE_NAMES) {
    const c = (value as Record<string, unknown>)[name] as Record<string, unknown> | undefined;
    if (!c || typeof c.on !== "boolean" || !TONES.includes(c.tone as Tone)) return null;
    const pause = isPauseCue(name);
    if (pause && (typeof c.before !== "number" || !Number.isFinite(c.before))) return null;
    out[name] = { on: c.on, tone: c.tone as Tone, ...(pause ? { before: c.before as number } : {}) };
  }
  return out as CueSettings;
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
