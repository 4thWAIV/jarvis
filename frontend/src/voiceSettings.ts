/**
 * His two voice input settings on the page (voiceControl.ts has their
 * types; jarvis/voice_control.py keeps them, per user, on this JARVIS):
 * read when the page starts, again whenever the voice socket reconnects or
 * the server says they changed (`voice_input_settings` on /ws/sessions),
 * and changed from Settings, Voice and language
 * (VoiceInputSettingsControl.tsx).
 *
 * Until a read has answered, `voiceInputSettings` (mic.ts) is null and
 * Start listening stays unavailable: a failed read is never taken as
 * permission to listen. A failed read after a good one keeps the values
 * already read. Changing a setting changes nothing that is happening now;
 * it applies at the next stop or the next time the page is left.
 */

import { reconnected } from "./liveRead";
import { voiceInputSettings } from "./mic";
import {
  parseVoiceInputSettings, VOICE_INPUT_SETTINGS_PATH, type StopMode, type SwitchMode,
  type VoiceInputSettings,
} from "./voiceControl";

let reads = 0;

/** Read the settings in force. A read overtaken by a newer one, or by a
 *  change, is not applied. */
export async function loadVoiceInputSettings(): Promise<VoiceInputSettings | null> {
  const mine = ++reads;
  try {
    const res = await fetch(VOICE_INPUT_SETTINGS_PATH);
    const value = res.ok ? parseVoiceInputSettings(await res.json()) : null;
    if (value && mine === reads) voiceInputSettings.set(value);
    return value;
  } catch {
    return null;
  }
}

/** A refusal of a change: the server's code and its English words. */
export class SettingsRefused extends Error {
  constructor(readonly code: string, message: string) { super(message); }
}

/** Change one or both settings: the same call as the brain's
 *  set_voice_input_settings. Answers the settings now saved. */
export async function changeVoiceInputSettings(
    change: { stop?: StopMode; switch?: SwitchMode }): Promise<VoiceInputSettings> {
  ++reads;
  const res = await fetch(VOICE_INPUT_SETTINGS_PATH, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(change),
  });
  const body = await res.json().catch(() => ({}));
  const value = res.ok ? parseVoiceInputSettings(body) : null;
  if (!value) {
    throw new SettingsRefused(String(body?.code ?? res.status), String(body?.error ?? `${res.status}`));
  }
  voiceInputSettings.set(value);
  return value;
}

/** The server said they changed (another page, or the brain). */
export function voiceInputSettingsChanged(): void {
  void loadVoiceInputSettings();
}

reconnected.subscribe(() => { void loadVoiceInputSettings(); });
