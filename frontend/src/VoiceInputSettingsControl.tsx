/**
 * Settings → Voice and language: his voice input settings
 * (voiceControl.ts; kept per user by jarvis/voice_control.py).
 *
 *   When I stop the microphone      Keep captured words | Discard unfinished input
 *   When I switch away from JARVIS  Continue listening  | Stop listening
 *   Capture sounds                  On | Off (the master switch)
 *   Listening started, stopped,     Off | one of the tones, and Test
 *   speech heard
 *   The pause, stage by stage       each interval (seconds after the stage
 *                                   before) and its time after he stops;
 *                                   a pause cue's Off | tone, and Test
 *   Your patience                   the total (a reading, not set)
 *   Sound volume                    percent of a quiet ceiling
 *
 * The pause shows its four stages in their order (voiceControl.PAUSE_STAGES):
 * each interval's field, then when that stage comes after he stops (the
 * server's `schedule`, never worked out here), then, for a pause cue, its
 * sound. Since each interval counts from the stage before, the cues can
 * never cross or come after the send. A cue that is off keeps its
 * interval: the time passes in silence. Test plays the cue once as it is
 * set (voiceStatus.ts `cueTest`, the same as the brain's play_cue), whether
 * it is on or not.
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
import { HEARD, INTERVAL_MAX, INTERVAL_MIN, isPauseCue, LISTEN_CUES, PAUSE_STAGES, STOP_MODES, SWITCH_MODES, TONES, VOLUME_MAX,
         VOLUME_MIN, type CueName, type PauseStage, type StopMode, type SwitchMode, type Tone,
         type VoiceInputSettings } from "./voiceControl";
import { changeVoiceInputSettings, type VoiceInputChange } from "./voiceSettings";
import { cueTest } from "./voiceStatus";

type Save = (change: VoiceInputChange) => Promise<void>;

/** Each cue's registered keys: its choice and its Test button. */
const CUE_ITEMS: Record<CueName, { choice: SettingsItem; test: SettingsItem }> = {
  start: { choice: "cueStart", test: "cueStartTest" },
  stop: { choice: "cueStop", test: "cueStopTest" },
  heard: { choice: "cueHeard", test: "cueHeardTest" },
  tentative: { choice: "cueTentative", test: "cueTentativeTest" },
  deciding: { choice: "cueDeciding", test: "cueDecidingTest" },
  likely: { choice: "cueLikely", test: "cueLikelyTest" },
};
/** Each pause stage's interval field, by its registered key. */
const INTERVAL_ITEMS: Record<PauseStage, SettingsItem> = {
  tentative: "intervalTentative", deciding: "intervalDeciding", likely: "intervalLikely", send: "intervalSend",
};

/** A number typed and saved when he leaves the field or presses Enter,
 *  only when it changed; with `clamp`, a value outside min to max is
 *  saved as the nearest of them. */
function NumberField({ id, uiRef, min, max, step, value, disabled, onSave, clamp = false }: {
  id: string; uiRef: (el: Element | null) => void; min: number; max: number; step: number;
  value: number | null; disabled: boolean; onSave: (value: number) => void; clamp?: boolean;
}) {
  const [typed, setTyped] = useState<string | null>(null);
  return <input id={id} type="number" data-ui-action="input" ref={uiRef}
                min={min} max={max} step={step} disabled={disabled}
                value={typed ?? (value != null ? String(value) : "")}
                onChange={(e) => setTyped(e.target.value)}
                onKeyDown={(e) => { if (e.key === "Enter") (e.target as HTMLInputElement).blur(); }}
                onBlur={() => {
                  if (typed === null) return;
                  const number = clamp ? Math.min(max, Math.max(min, Number(typed))) : Number(typed);
                  setTyped(null);
                  if (typed.trim() && Number.isFinite(number) && number !== value) onSave(number);
                }} />;
}

/** One capture cue: Off or its tone, Test, and when it plays. */
function CueRows({ name, settings, disabled, save }: {
  name: CueName; settings: VoiceInputSettings | null; disabled: boolean; save: Save;
}) {
  const t = useT();
  const items = CUE_ITEMS[name];
  const label = t(`left.voiceInput.cue.${name}`);
  const choiceUi = useSettingsItem(items.choice, label);
  const testUi = useSettingsItem(items.test, `${label}: ${t("left.voiceInput.cueTest")}`);
  const cue = settings?.cues[name];
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
    {isPauseCue(name) && cue?.on === false && <p className="settings-hint">{t("left.voiceInput.cueOffKeepsTime")}</p>}
  </>;
}

/** One stage of the pause: its interval after the stage before, when it
 *  comes after he stops, and a pause cue's sound. */
function PauseStageRows({ stage, settings, disabled, save }: {
  stage: PauseStage; settings: VoiceInputSettings | null; disabled: boolean; save: Save;
}) {
  const t = useT();
  const label = t(`left.voiceInput.interval.${stage}`);
  const ui = useSettingsItem(INTERVAL_ITEMS[stage], label);
  return <>
    <SettingsRow label={label} labelRef={ui.part("label")} htmlFor={`settings-interval-${stage}`}>
      <NumberField id={`settings-interval-${stage}`} uiRef={ui.whole} min={INTERVAL_MIN} max={INTERVAL_MAX}
                   step={0.05} value={settings?.intervals[stage] ?? null} disabled={disabled} clamp
                   onSave={(seconds) => void save({ intervals: { [stage]: Math.round(seconds * 100) / 100 } })} />
    </SettingsRow>
    {settings && <p className="settings-hint" id={`settings-interval-${stage}-at`}>
      {t(`left.voiceInput.intervalAt.${stage}`, { at: settings.schedule[stage] })}
    </p>}
    {isPauseCue(stage) && <CueRows name={stage} settings={settings} disabled={disabled} save={save} />}
  </>;
}

export function VoiceInputSettingsControl() {
  const t = useT();
  const settings = useSyncExternalStore(voiceInputSettings.subscribe, voiceInputSettings.get);
  const [saving, setSaving] = useState(false);
  const [failed, setFailed] = useState<string | null>(null);
  const stopUi = useSettingsItem("voiceInputStop", t("left.voiceInput.stop"));
  const switchUi = useSettingsItem("voiceInputSwitch", t("left.voiceInput.switch"));
  const patienceUi = useSettingsItem("voicePatience", t("left.voiceInput.patienceTotal"));
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
      {[...LISTEN_CUES, HEARD].map((name) => <CueRows key={name} name={name} settings={settings} disabled={disabled} save={save} />)}
      <p className="settings-hint">{t("left.voiceInput.pauseHelp", { min: INTERVAL_MIN, max: INTERVAL_MAX })}</p>
      {PAUSE_STAGES.map((stage) => <PauseStageRows key={stage} stage={stage} settings={settings}
                                                   disabled={disabled} save={save} />)}
      <SettingsRow label={t("left.voiceInput.patienceTotal")} labelRef={patienceUi.part("label")}>
        <output id="settings-voice-patience" ref={patienceUi.whole}>
          {settings ? t("left.voiceInput.seconds", { seconds: settings.patience }) : ""}
        </output>
      </SettingsRow>
      <p className="settings-hint">{t("left.voiceInput.patienceTotalHelp")}</p>
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
