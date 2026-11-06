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
 *           order they play, tentative, deciding and likely.
 *   intervals  The pause, four stages in order (PAUSE_STAGES: the pause
 *           cues, then the send), each its interval in seconds after the
 *           stage before; `schedule` is each stage's time after he stops,
 *           worked out by the server (voice_control.schedule_of, the one
 *           function), and `patience` the send's, their total.
 *   volume  One cue volume, in percent of a quiet ceiling.
 */

export type StopMode = "keep" | "discard";
export type SwitchMode = "continue" | "stop";

export const STOP_MODES: readonly StopMode[] = ["keep", "discard"];
export const SWITCH_MODES: readonly SwitchMode[] = ["continue", "stop"];

/** The capture cues (voice_control.CUES), the pause cues among them in the
 *  order they play (voice_control.PAUSE_CUES), and the quiet presets each
 *  may play (voice_control.TONES; voice.ts holds their notes). */
export const PAUSE_CUES = ["tentative", "deciding", "likely"] as const;
export const LISTEN_CUES = ["start", "stop"] as const;
export const CUE_NAMES = [...LISTEN_CUES, ...PAUSE_CUES] as const;
export type PauseCue = (typeof PAUSE_CUES)[number];
/** The pause stages in order: the pause cues, then the send
 *  (turn_controller.PAUSE_STAGES). */
export const PAUSE_STAGES = [...PAUSE_CUES, "send"] as const;
export type PauseStage = (typeof PAUSE_STAGES)[number];
export type CueName = (typeof CUE_NAMES)[number];
/** Whether a cue is one of the pause cues. */
export function isPauseCue(name: unknown): name is PauseCue {
  return (PAUSE_CUES as readonly unknown[]).includes(name);
}
export type Tone = "rise" | "fall" | "pulse" | "low" | "double" | "resolve";
export const TONES: readonly Tone[] = ["rise", "fall", "pulse", "low", "double", "resolve"];
/** A pause interval's bounds in seconds, and the cue volume in percent
 *  (voice_control.INTERVAL_MIN/MAX, VOLUME_MIN/MAX). */
export const INTERVAL_MIN = 0.25;
export const INTERVAL_MAX = 5;
export const VOLUME_MIN = 5;
export const VOLUME_MAX = 100;

export interface CueSetting { on: boolean; tone: Tone }
export type CueSettings = Record<CueName, CueSetting>;
/** Seconds for each pause stage. */
export type PauseSeconds = Record<PauseStage, number>;

export const VOICE_INPUT_SETTINGS_PATH = "/api/settings/voice-input";
/** The hint on /ws/sessions that the settings changed. */
export const VOICE_INPUT_SETTINGS_FRAME = "voice_input_settings";

export interface VoiceInputSettings {
  stop: StopMode;
  switch: SwitchMode;
  /** Each pause stage's interval after the stage before. */
  intervals: PauseSeconds;
  /** Each pause stage's time after he stops (the server's sum). */
  schedule: PauseSeconds;
  /** His patience: the send's time, the intervals' total; also how long
   *  JARVIS waits after he speaks before it may start talking. */
  patience: number;
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
  const intervals = parseSeconds(v.intervals);
  const schedule = parseSeconds(v.schedule);
  const cues = parseCues(v.cues);
  if (!cues || !intervals || !schedule || typeof v.volume !== "number" || !Number.isFinite(v.volume)) return null;
  return { stop: v.stop as StopMode, switch: v.switch as SwitchMode, intervals, schedule,
           patience: schedule.send,
           sounds: v.sounds !== false, cues, volume: v.volume, saved: v.saved === true,
           scope: v.scope === "member" ? "member" : "local" };
}

function parseCues(value: unknown): CueSettings | null {
  if (!value || typeof value !== "object") return null;
  const out: Partial<CueSettings> = {};
  for (const name of CUE_NAMES) {
    const c = (value as Record<string, unknown>)[name] as Record<string, unknown> | undefined;
    if (!c || typeof c.on !== "boolean" || !TONES.includes(c.tone as Tone)) return null;
    out[name] = { on: c.on, tone: c.tone as Tone };
  }
  return out as CueSettings;
}

function parseSeconds(value: unknown): PauseSeconds | null {
  if (!value || typeof value !== "object") return null;
  const out: Partial<PauseSeconds> = {};
  for (const stage of PAUSE_STAGES) {
    const seconds = (value as Record<string, unknown>)[stage];
    if (typeof seconds !== "number" || !Number.isFinite(seconds)) return null;
    out[stage] = seconds;
  }
  return out as PauseSeconds;
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
