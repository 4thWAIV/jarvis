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
 *           order they play, tentative, deciding and likely, each at its
 *           `percent` of his patience from when his speech was last heard
 *           (the send is at 100); the ones that are on each at least
 *           PERCENT_SPACING after the one before, so they keep their order
 *           at any patience.
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
/** A pause cue's share of his patience in whole percent, the least spacing
 *  between two that are on, and the cue volume in percent
 *  (voice_control.PERCENT_SPACING, PERCENT_MIN/MAX, VOLUME_MIN/MAX). */
export const PERCENT_SPACING = 5;
export const PERCENT_MIN = PERCENT_SPACING;
export const PERCENT_MAX = 100 - PERCENT_SPACING;
export const VOLUME_MIN = 5;
export const VOLUME_MAX = 100;

export interface CueSetting { on: boolean; tone: Tone; percent?: number }
export type CueSettings = Record<CueName, CueSetting>;

/** Seconds of quiet after which a pause cue plays at this patience, to the
 *  hundredth, halves up (voice_control.pause_cue_at); null while his
 *  patience is not known. */
export function pauseCueAt(patience: number | null, cue: CueSetting): number | null {
  if (patience == null || cue.percent == null) return null;
  return Math.floor(patience * cue.percent + 0.5) / 100;
}

/** The pause cues that are on, in the order they play. */
function pauseCuesOn(cues: CueSettings): PauseCue[] {
  return PAUSE_CUES.filter((n) => cues[n].on);
}

/** The shares a pause cue may take without crossing a neighbour that is on
 *  (voice_control.pause_cue_bounds): PERCENT_SPACING after the nearest
 *  earlier one (or PERCENT_MIN) to PERCENT_SPACING before the nearest
 *  later one (or PERCENT_MAX), whether this cue is on or not. */
export function pauseCueBounds(cues: CueSettings, name: PauseCue): { min: number; max: number } {
  const i = PAUSE_CUES.indexOf(name);
  const on = pauseCuesOn(cues).filter((n) => n !== name);
  const earlier = on.filter((n) => PAUSE_CUES.indexOf(n) < i).pop();
  const later = on.find((n) => PAUSE_CUES.indexOf(n) > i);
  return { min: earlier ? (cues[earlier].percent as number) + PERCENT_SPACING : PERCENT_MIN,
           max: later ? (cues[later].percent as number) - PERCENT_SPACING : PERCENT_MAX };
}

/** The gaps around one pause cue at this patience, in seconds
 *  (voice_control.pause_cue_gaps): since the nearest earlier pause cue
 *  that is on, and until the nearest later one, or until what he said is
 *  sent when none later is on (`next: null`). Null while his patience is
 *  not known. */
export interface PauseCueGaps {
  after: { cue: PauseCue; gap: number } | null;
  until: { next: PauseCue | null; gap: number };
}
export function pauseCueGaps(patience: number | null, cues: CueSettings, name: PauseCue): PauseCueGaps | null {
  const at = pauseCueAt(patience, cues[name]);
  if (patience == null || at == null) return null;
  const seconds = (n: PauseCue) => pauseCueAt(patience, cues[n]) as number;
  const round = (value: number) => Math.round(value * 100) / 100;
  const i = PAUSE_CUES.indexOf(name);
  const on = pauseCuesOn(cues);
  const earlier = on.filter((n) => PAUSE_CUES.indexOf(n) < i).pop();
  const later = on.find((n) => PAUSE_CUES.indexOf(n) > i) ?? null;
  return {
    after: earlier ? { cue: earlier, gap: round(at - seconds(earlier)) } : null,
    until: { next: later, gap: round((later ? seconds(later) : patience) - at) },
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
    if (pause && (typeof c.percent !== "number" || !Number.isFinite(c.percent))) return null;
    out[name] = { on: c.on, tone: c.tone as Tone, ...(pause ? { percent: c.percent as number } : {}) };
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
