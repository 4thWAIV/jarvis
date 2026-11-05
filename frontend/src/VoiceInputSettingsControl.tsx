/**
 * Settings → Voice and language: his voice input settings
 * (voiceControl.ts; kept per user by jarvis/voice_control.py).
 *
 *   When I stop the microphone      Keep captured words | Discard unfinished input
 *   When I switch away from JARVIS  Continue listening  | Stop listening
 *   Patience                        seconds of quiet that end his turn
 *   Capture sounds                  On | Off (the master switch)
 *   Each capture cue                Off | one of the tones, and Test
 *   Each pause cue's timing         seconds before what he said is sent
 *   Sound volume                    percent of a quiet ceiling
 *
 * Each cue says, under it, when it plays; a pause cue's timing says when
 * it plays at his patience (patience minus its seconds), or that it does
 * not. Test plays the cue once as it is set (voiceStatus.ts `cueTest`, the
 * same as the brain's play_cue), whether it is on or not.
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
import { SettingsRow, useSettingsItem, type SettingsItem } from "./SettingsRow";
import { BEFORE_MAX, BEFORE_MIN, CUE_NAMES, PATIENCE_MAX, PATIENCE_MIN, pauseCueAt, STOP_MODES,
         SWITCH_MODES, TONES, VOLUME_MAX, VOLUME_MIN, type CueName, type StopMode, type SwitchMode,
         type Tone, type VoiceInputSettings } from "./voiceControl";
import { changeVoiceInputSettings, type VoiceInputChange } from "./voiceSettings";
import { cueTest } from "./voiceStatus";

type Save = (change: VoiceInputChange) => Promise<void>;

/** Each cue's registered keys: its choice, its Test button and, for a
 *  pause cue, its timing. */
const CUE_ITEMS: Record<CueName, { choice: SettingsItem; test: SettingsItem; before?: SettingsItem }> = {
  start: { choice: "cueStart", test: "cueStartTest" },
  stop: { choice: "cueStop", test: "cueStopTest" },
  tentative: { choice: "cueTentative", test: "cueTentativeTest", before: "cueTentativeBefore" },
  likely: { choice: "cueLikely", test: "cueLikelyTest", before: "cueLikelyBefore" },
};

/** A number typed and saved when he leaves the field or presses Enter,
 *  only when it changed. */
function NumberField({ id, uiRef, min, max, step, value, disabled, onSave }: {
  id: string; uiRef: (el: Element | null) => void; min: number; max: number; step: number;
  value: number | null; disabled: boolean; onSave: (value: number) => void;
}) {
  const [typed, setTyped] = useState<string | null>(null);
  return <input id={id} type="number" data-ui-action="input" ref={uiRef}
                min={min} max={max} step={step} disabled={disabled}
                value={typed ?? (value != null ? String(value) : "")}
                onChange={(e) => setTyped(e.target.value)}
                onKeyDown={(e) => { if (e.key === "Enter") (e.target as HTMLInputElement).blur(); }}
                onBlur={() => {
                  if (typed === null) return;
                  const number = Number(typed);
                  setTyped(null);
                  if (typed.trim() && Number.isFinite(number) && number !== value) onSave(number);
                }} />;
}

/** One capture cue: Off or its tone, Test, when it plays, and a pause
 *  cue's timing. */
function CueRows({ name, settings, disabled, save }: {
  name: CueName; settings: VoiceInputSettings | null; disabled: boolean; save: Save;
}) {
  const t = useT();
  const items = CUE_ITEMS[name];
  const label = t(`left.voiceInput.cue.${name}`);
  const choiceUi = useSettingsItem(items.choice, label);
  const testUi = useSettingsItem(items.test, `${label}: ${t("left.voiceInput.cueTest")}`);
  const beforeLabel = name === "tentative" || name === "likely" ? t(`left.voiceInput.cueBefore.${name}`) : "";
  const beforeUi = useSettingsItem(items.before ?? items.choice, beforeLabel, items.before !== undefined);
  const cue = settings?.cues[name];
  const at = settings && cue ? pauseCueAt(settings.patience, cue) : null;
  return <>
    <SettingsRow label={label} labelRef={choiceUi.part("label")} htmlFor={`settings-cue-${name}`}>
      <select id={`settings-cue-${name}`} data-ui-action="domain" ref={choiceUi.whole}
              value={cue ? (cue.on ? cue.tone : "off") : ""} disabled={disabled}
              onChange={(e) => void save({ cues: { [name]: e.target.value === "off"
                ? { on: false } : { on: true, tone: e.target.value as Tone } } })}>
        {!cue && <option value="" />}
        <option value="off">{t("left.voiceInput.cue.off")}</option>
        {TONES.map((tone) => <option key={tone} value={tone}>{t(`left.voiceInput.tone.${tone}`)}</option>)}
      </select>
      <button type="button" id={`settings-cue-${name}-test`} data-ui-action="domain" ref={testUi.whole}
              disabled={!settings} title={t("left.voiceInput.cueTestTitle")}
              onClick={() => cueTest.current(name)}>
        {t("left.voiceInput.cueTest")}
      </button>
    </SettingsRow>
    <p className="settings-hint">{t(`left.voiceInput.cueHelp.${name}`)}</p>
    {items.before && <>
      <SettingsRow label={beforeLabel} labelRef={beforeUi.part("label")} htmlFor={`settings-cue-${name}-before`}>
        <NumberField id={`settings-cue-${name}-before`} uiRef={beforeUi.whole} min={BEFORE_MIN} max={BEFORE_MAX}
                     step={0.25} value={cue?.before ?? null} disabled={disabled}
                     onSave={(before) => void save({ cues: { [name]: { before } } })} />
      </SettingsRow>
      {settings && <p className="settings-hint" id={`settings-cue-${name}-at`}>
        {at != null ? t("left.voiceInput.cueAt", { patience: settings.patience ?? "", at })
                    : t("left.voiceInput.cueNever", { patience: settings.patience ?? "" })}
      </p>}
    </>}
  </>;
}

export function VoiceInputSettingsControl() {
  const t = useT();
  const settings = useSyncExternalStore(voiceInputSettings.subscribe, voiceInputSettings.get);
  const [saving, setSaving] = useState(false);
  const [failed, setFailed] = useState<string | null>(null);
  const stopUi = useSettingsItem("voiceInputStop", t("left.voiceInput.stop"));
  const switchUi = useSettingsItem("voiceInputSwitch", t("left.voiceInput.switch"));
  const patienceUi = useSettingsItem("voicePatience", t("left.voiceInput.patience"));
  const soundsUi = useSettingsItem("captureSounds", t("left.voiceInput.sounds"));
  const volumeUi = useSettingsItem("cueVolume", t("left.voiceInput.volume"));

  const save: Save = async (change) => {
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
      <SettingsRow label={t("left.voiceInput.patience")} labelRef={patienceUi.part("label")} htmlFor="settings-voice-patience">
        <NumberField id="settings-voice-patience" uiRef={patienceUi.whole} min={PATIENCE_MIN} max={PATIENCE_MAX}
                     step={0.5} value={settings?.patience ?? null} disabled={disabled}
                     onSave={(patience) => void save({ patience })} />
      </SettingsRow>
      <p className="settings-hint">{t("left.voiceInput.patienceHelp", { min: PATIENCE_MIN, max: PATIENCE_MAX })}</p>
      <SettingsRow label={t("left.voiceInput.sounds")} labelRef={soundsUi.part("label")} htmlFor="settings-voice-sounds">
        <select id="settings-voice-sounds" data-ui-action="domain" ref={soundsUi.whole}
                value={settings ? (settings.sounds ? "on" : "off") : ""} disabled={disabled}
                onChange={(e) => void save({ sounds: e.target.value === "on" })}>
          {!settings && <option value="" />}
          <option value="on">{t("left.voiceInput.sounds.on")}</option>
          <option value="off">{t("left.voiceInput.sounds.off")}</option>
        </select>
      </SettingsRow>
      <p className="settings-hint">{t("left.voiceInput.soundsHelp")}</p>
      {CUE_NAMES.map((name) => <CueRows key={name} name={name} settings={settings} disabled={disabled} save={save} />)}
      <p className="settings-hint">{t("left.voiceInput.cueBeforeHelp", { min: BEFORE_MIN, max: BEFORE_MAX })}</p>
      <SettingsRow label={t("left.voiceInput.volume")} labelRef={volumeUi.part("label")} htmlFor="settings-cue-volume">
        <NumberField id="settings-cue-volume" uiRef={volumeUi.whole} min={VOLUME_MIN} max={VOLUME_MAX} step={5}
                     value={settings?.volume ?? null} disabled={disabled}
                     onSave={(volume) => void save({ volume })} />
      </SettingsRow>
      <p className="settings-hint">{t("left.voiceInput.volumeHelp", { min: VOLUME_MIN, max: VOLUME_MAX })}</p>
      {settings && !settings.saved && <p className="settings-hint">{t("left.voiceInput.defaults")}</p>}
      {!settings && <p className="settings-hint read-failure">{t("left.voiceInput.unread")}</p>}
      {failed && <p className="settings-hint read-failure">{t("left.voiceInput.notSaved", { error: failed })}</p>}
    </>
  );
}
