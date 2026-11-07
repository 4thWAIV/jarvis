/**
 * The audio subsystem's state as the speaker and the headset show it, and
 * the two changes they make, in one place.
 *
 * What JARVIS hears comes from voice input's status and his quiet
 * preference (mic.ts); what it says from voiceState and speechStopped
 * (voiceStatus.ts) and his speech mute, a voice input setting the server
 * keeps per user (voice_control.py, `speech_muted`), so every page and the
 * brain agree on it.
 *
 * The speaker mutes and unmutes speech (`setSpeechMuted`); the headset turns
 * all audio off (Mic off and speech muted) or back on (`setAllAudio`). Each
 * is the same call as the brain's: `changeVoiceInputSettings` is the
 * Settings route that `mute_speech` and `turn_audio_off` call, and
 * `chooseQuiet` is what the brain's quiet frame applies. The brain may only
 * make them quieter; turning them back on is his click.
 */

import { useSyncExternalStore } from "react";
import { chooseQuiet, inputStatus, quiet, voiceInputSettings } from "./mic";
import { changeVoiceInputSettings } from "./voiceSettings";
import { speechStopped, voiceState } from "./voiceStatus";
import type { InputState } from "./voiceInput";

/** What the headset's boom shows. */
export type Hearing = "listening" | "starting" | "idle" | "muted" | "off";
/** What the speaker and the headset's ear cups show. */
export type Saying = "speaking" | "silent" | "stopped" | "muted";

const HEARING: Partial<Record<InputState, Hearing>> = {
  listening: "listening",
  finishing: "listening",
  starting: "starting",
  permission: "starting",
  loading: "starting",
  muted: "muted",
  "muted-inactive": "muted",
  off: "off",
};

/** The boom's look for an input state and his quiet preference: his Mute
 *  or Mic off shows at once, as the meter's words do (MicControl.tsx). */
export function hearing(state: InputState, preference: string): Hearing {
  if (preference === "off") return "off";
  if (preference === "muted") return "muted";
  return HEARING[state] ?? "idle";
}

/** The speaker's look: his speech mute first, then what JARVIS is doing. */
export function saying(state: string, stopped: boolean, muted: boolean): Saying {
  if (muted) return "muted";
  if (state === "speaking") return "speaking";
  return stopped ? "stopped" : "silent";
}

export function useHearing(): { look: Hearing; state: InputState } {
  const status = useSyncExternalStore(inputStatus.subscribe, inputStatus.get);
  const preference = useSyncExternalStore(quiet.subscribe, quiet.get);
  return { look: hearing(status.state, preference), state: status.state };
}

export function useSpeechMuted(): boolean {
  const settings = useSyncExternalStore(voiceInputSettings.subscribe, voiceInputSettings.get);
  return settings?.speech_muted === true;
}

export function useSaying(): Saying {
  const state = useSyncExternalStore(voiceState.subscribe, voiceState.get);
  const stopped = useSyncExternalStore(speechStopped.subscribe, speechStopped.get);
  return saying(state, stopped, useSpeechMuted());
}

/** Mute or unmute JARVIS's speech: the speaker's click. A refusal leaves
 *  it as it was; the settings read again say so. */
export function setSpeechMuted(muted: boolean): void {
  changeVoiceInputSettings({ speech_muted: muted }).catch((e) => {
    console.warn("the speech mute was not changed", e);
  });
}

/** All audio off (Mic off and speech muted) or back on (the microphone on
 *  again and speech unmuted): the headset's click. */
export function setAllAudio(on: boolean): void {
  chooseQuiet(on ? "none" : "off");
  setSpeechMuted(!on);
}
