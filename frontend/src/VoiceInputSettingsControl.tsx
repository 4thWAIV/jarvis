/**
 * Settings → Voice and language: his two voice input settings
 * (voiceControl.ts; kept per user by jarvis/voice_control.py).
 *
 *   When I stop the microphone      Keep captured words | Discard unfinished input
 *   When I switch away from JARVIS  Continue listening  | Stop listening
 *
 * Each shows the value in force, read with the page (voiceSettings.ts) and
 * again whenever another page or the brain changes it. Changing one saves
 * it and applies from the next stop or the next time the page is left: the
 * change itself starts, stops and sends nothing. Until the settings have
 * been read the controls are unavailable, and so is Start listening. The
 * brain's set_voice_input_settings calls the same route.
 */

import { useState, useSyncExternalStore } from "react";
import { useT } from "./locale";
import { voiceInputSettings } from "./mic";
import { SettingsRow, useSettingsItem } from "./SettingsRow";
import { STOP_MODES, SWITCH_MODES, type StopMode, type SwitchMode } from "./voiceControl";
import { changeVoiceInputSettings } from "./voiceSettings";

export function VoiceInputSettingsControl() {
  const t = useT();
  const settings = useSyncExternalStore(voiceInputSettings.subscribe, voiceInputSettings.get);
  const [saving, setSaving] = useState(false);
  const [failed, setFailed] = useState<string | null>(null);
  const stopUi = useSettingsItem("voiceInputStop", t("left.voiceInput.stop"));
  const switchUi = useSettingsItem("voiceInputSwitch", t("left.voiceInput.switch"));

  const save = async (change: { stop?: StopMode; switch?: SwitchMode }) => {
    setSaving(true);
    setFailed(null);
    try {
      await changeVoiceInputSettings(change);
    } catch (e) {
      setFailed((e as Error).message);
    } finally {
      setSaving(false);
    }
  };

  const disabled = !settings || saving;
  return (
    <>
      <SettingsRow label={t("left.voiceInput.stop")} labelRef={stopUi.part("label")} htmlFor="settings-voice-stop">
        <select id="settings-voice-stop" data-ui-action="domain" ref={stopUi.whole}
                value={settings?.stop ?? ""} disabled={disabled}
                onChange={(e) => void save({ stop: e.target.value as StopMode })}>
          {!settings && <option value="" />}
          {STOP_MODES.map((mode) => <option key={mode} value={mode}>{t(`left.voiceInput.stop.${mode}`)}</option>)}
        </select>
      </SettingsRow>
      <p className="settings-hint">{t("left.voiceInput.stopHelp")}</p>
      <SettingsRow label={t("left.voiceInput.switch")} labelRef={switchUi.part("label")} htmlFor="settings-voice-switch">
        <select id="settings-voice-switch" data-ui-action="domain" ref={switchUi.whole}
                value={settings?.switch ?? ""} disabled={disabled}
                onChange={(e) => void save({ switch: e.target.value as SwitchMode })}>
          {!settings && <option value="" />}
          {SWITCH_MODES.map((mode) => <option key={mode} value={mode}>{t(`left.voiceInput.switch.${mode}`)}</option>)}
        </select>
      </SettingsRow>
      <p className="settings-hint">{t("left.voiceInput.switchHelp")}</p>
      {settings && !settings.saved && <p className="settings-hint">{t("left.voiceInput.defaults")}</p>}
      {!settings && <p className="settings-hint read-failure">{t("left.voiceInput.unread")}</p>}
      {failed && <p className="settings-hint read-failure">{t("left.voiceInput.notSaved", { error: failed })}</p>}
    </>
  );
}
