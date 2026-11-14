/**
 * The settings Settings draws from their one list (settings_specs.py, read
 * at GET /api/settings/values): one row per setting of a section that has
 * no control of its own (`control: "generic"`), each a `settingsItem`
 * target by its registered key, its words the catalog's
 * (`left.field.<key>` and `.help`). A change is saved at once (a switch or
 * a choice) or when he leaves the field or presses Enter (text, numbers,
 * lists), through POST /api/settings/values, the route the brain's
 * set_jarvis_setting shares. A start-up setting says it takes effect when
 * JARVIS next starts, and that it is waiting for one once changed.
 *
 * A refusal comes back as a code and its values and is worded here
 * (`left.fields.refused.<code>`).
 */

import { useEffect, useState, useSyncExternalStore } from "react";
import { liveRead } from "./liveRead";
import { useT, type Translate } from "./locale";
import { SettingsRow, useSettingsItem, type SettingsItem } from "./SettingsRow";
import { createStore } from "./store";

export type SettingKind = "bool" | "int" | "float" | "text" | "choice" | "path" | "paths" | "origins" | "secret";

export interface SettingView {
  key: string;
  area: string;
  kind: SettingKind;
  value?: unknown;
  default: unknown;
  choices?: string[];
  min?: number;
  max?: number;
  optional: boolean;
  restart: boolean;
  page_only: boolean;
  pending_restart?: boolean;
  control: "generic" | "own";
  target: SettingsItem;
  set?: boolean;
}

export interface SettingsValues {
  settings: SettingView[];
  start: { problem: "tampered" | "unreadable" | null; imported_from_env: string | null;
           data_dir_from_env: string | null; file: string;
           /** A changed start setup waiting for a page to reach it (start_settings.status). */
           trial: { setup: StartSetup; fallback: StartSetup; began: number; deadline: number;
                    seconds_left: number } | null;
           /** The last time JARVIS went back to the last setup that worked. */
           reverted: { at: number; when: string; why: "unreached" | "unbindable";
                       from: StartSetup; to: StartSetup } | null;
           window_sec: number; overridden: boolean };
}

export interface StartSetup { host: string; port: number; https: boolean; allowed_origins?: string[] }

/** A start setup's address as a page would open it. */
export function startAddress(s: StartSetup): string {
  const host = s.host.includes(":") ? `[${s.host}]` : s.host;
  return `${s.https ? "https" : "http"}://${host}:${s.port}`;
}

interface Refusal { message: string; code: string; values: Record<string, string | number> }

class Refused extends Error {
  constructor(readonly refusal: Refusal) { super(refusal.message); }
}

/** Moves when a setting was saved, here or elsewhere (the `settings` hint
 *  on /ws/sessions, sessionsSocket.ts), so every section reads again. */
export const settingsChanged = createStore(0);

/** Every setting, read once for all sections and again after each change. */
export const settingsValues = liveRead(async (): Promise<SettingsValues> => {
  const res = await fetch("/api/settings/values");
  if (!res.ok) throw new Error(`${res.status}`);
  return res.json() as Promise<SettingsValues>;
}, [settingsChanged]);

/** Save one setting; Refused carries the server's code. */
export async function saveSetting(key: string, value: unknown): Promise<void> {
  const res = await fetch("/api/settings/values", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ key, value }),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => null) as { detail?: Refusal } | null;
    throw new Refused(body?.detail ?? { message: `${res.status}`, code: "failed", values: {} });
  }
  settingsChanged.set(settingsChanged.get() + 1);
}

/** A message named at run time (a setting's key, a refusal's code): every
 *  one is in both catalogs, checked against settings_specs by
 *  tests/test_env_registry.py. */
type Say = (key: string, values?: Record<string, string | number>) => string;
const sayWith = (t: Translate): Say => t as unknown as Say;

function refusalWords(t: Translate, e: unknown): string {
  if (e instanceof Refused) {
    const { code, values } = e.refusal;
    const key = `left.fields.refused.${code}`;
    const said = sayWith(t)(key, values);
    return said === key ? e.refusal.message : said;
  }
  return t("left.fields.refused.failed");
}

/** The words a list setting is edited in: one entry a line. */
const asLines = (v: unknown) => (Array.isArray(v) ? v.join("\n") : "");
const asText = (v: unknown) => (v === null || v === undefined ? "" : String(v));

function Field({ setting }: { setting: SettingView }) {
  const t = useT();
  const say = sayWith(t);
  const label = say(`left.field.${setting.key}`);
  const ui = useSettingsItem(setting.target, label);
  const list = setting.kind === "paths" || setting.kind === "origins";
  const shown = list ? asLines(setting.value) : asText(setting.value);
  const [draft, setDraft] = useState(shown);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => setDraft(shown), [shown]);

  const save = async (value: unknown) => {
    setBusy(true); setError(null);
    try { await saveSetting(setting.key, value); }
    catch (e) { setError(refusalWords(t, e)); setDraft(shown); }
    finally { setBusy(false); }
  };
  const commit = () => {
    if (draft === shown) return;
    if (list) void save(draft.split("\n").map((s) => s.trim()).filter(Boolean));
    else if (setting.kind === "int" || setting.kind === "float")
      void save(draft.trim() === "" ? null : Number(draft));
    else void save(draft);
  };
  const id = `setting-${setting.key}`;
  const placeholder = t("left.fields.automatic");

  let control;
  if (setting.kind === "bool") {
    control = <input data-ui-action="input" id={id} type="checkbox" ref={ui.whole} disabled={busy}
                     checked={setting.value === true} onChange={(e) => void save(e.target.checked)} />;
  } else if (setting.kind === "choice") {
    control = (
      <select data-ui-action="input" id={id} ref={ui.whole} disabled={busy} value={asText(setting.value)}
              onChange={(e) => void save(e.target.value)}>
        {(setting.choices ?? []).map((c) => (
          <option key={c} value={c}>{say(`left.fields.choice.${c}`)}</option>
        ))}
      </select>
    );
  } else if (list) {
    control = <textarea data-ui-action="input" id={id} ref={ui.whole} rows={2} disabled={busy}
                        placeholder={placeholder} value={draft}
                        onChange={(e) => setDraft(e.target.value)} onBlur={commit} />;
  } else {
    const number = setting.kind === "int" || setting.kind === "float";
    control = <input data-ui-action="input" id={id} ref={ui.whole} disabled={busy}
                     type={number ? "number" : "text"} min={setting.min} max={setting.max}
                     step={setting.kind === "int" ? 1 : "any"}
                     placeholder={setting.optional || !number ? placeholder : undefined} value={draft}
                     onChange={(e) => setDraft(e.target.value)} onBlur={commit}
                     onKeyDown={(e) => { if (e.key === "Enter") commit(); }} />;
  }

  return (
    <>
      <SettingsRow label={label} labelRef={ui.part("label")} htmlFor={id}>{control}</SettingsRow>
      <p className="settings-hint">
        {say(`left.field.${setting.key}.help`)}
        {setting.restart && <> {t(setting.pending_restart ? "left.fields.pending" : "left.fields.restart")}</>}
        {setting.page_only && <> {t("left.fields.pageOnly")}</>}
      </p>
      {error && <p role="alert" className="read-failure">{error}</p>}
    </>
  );
}

/** One section's settings that have no control of their own. */
export function SettingsFields({ area }: { area: string }) {
  const t = useT();
  const live = useSyncExternalStore(settingsValues.subscribe, settingsValues.get);
  useEffect(() => settingsValues.refresh(), []);
  const fields = (live.value?.settings ?? []).filter((s) => s.area === area && s.control === "generic");
  return (
    <>
      {!live.value && (
        <p className="settings-hint read-failure">
          {live.error ? t("left.fields.unreadable", { error: live.error }) : t("left.fields.reading")}
        </p>
      )}
      {fields.map((s) => <Field key={s.key} setting={s} />)}
    </>
  );
}
