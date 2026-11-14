/**
 * The Settings view: a centre view like Conversation, offered as a centre
 * tab while it is open, one scrolling page of sections in the order a
 * person comes to them: You, Voice and language, Brains, Agents, Projects,
 * Team, Knowledge, Safety, Updates, Server. Every choice a person makes is
 * here: no one ever leaves the dashboard to change JARVIS. The settings
 * with no control of their own are drawn from their one list
 * (SettingsFields.tsx, settings_specs.py). Its title, × and a row of section names that jumps to each stay
 * above the scrolling sections.
 *
 * With no Fish Audio key set, two seconds after the page loads, it opens
 * as first-time setup (useFirstRunSetup): a welcome line, Voice and
 * language alone, then You alone, then Finish closes it.
 *
 * Everything is read from and written to the same endpoints as before
 * (/api/settings/*, /api/brains for Brains, /api/update for Updates,
 * self_update.py, which the menu's Check for updates opens). A secret is
 * typed into a masked field and never read back.
 *
 * Opening, closing and going to a section are the registered commands
 * settings.open, settings.close and settings.section, run the same by his
 * clicks (the menu, Check for updates, the sign-in reminder, the section
 * row, ×, Finish), by first-time setup and by the brain; Settings is the
 * centre's `settings` detail (uiNavigation.ts). Saving, testing keys,
 * brains, sign-in, membership and updates stay on their own authorised
 * paths. Settings, each section and every control and reading in it are
 * targets on the main rail (SettingsRow.tsx).
 *
 * Its words are the catalog's (left.settings.* and each section's), in the
 * interface's language; a section is named by its registered id. Updates
 * are worded from self_update's codes; a commit's subject, a branch, a path
 * or a failing step's output is shown as it came. A state is shown only
 * when it is not the ordinary one.
 */

import { motion, setMotion } from "./motion";
import { useCallback, useEffect, useState, useSyncExternalStore, type ReactNode } from "react";
import { KnowledgeSettings } from "./KnowledgeSettings";
import { SettingsFields, settingsValues } from "./SettingsFields";
import { BrainsSection } from "./BrainsSection";
import { CENTRE_SOCKET } from "./ConversationView";
import { closeMenu, reloadAfterRestart } from "./MenuControl";
import { LanguageControl } from "./LanguageControl";
import { VoiceInputSettingsControl } from "./VoiceInputSettingsControl";
import { TeamSection } from "./TeamSection";
import { useLiveRead } from "./liveRead";
import { formatDateTime, placed, useT, type Translate } from "./locale";
import { SettingsRow, useSettingsItem, useSettingsRow } from "./SettingsRow";
import { StatusDot, type Dot } from "./StatusDot";
import { theme, setTheme, type Theme } from "./theme";
import { createStore } from "./store";
import type { CommandArgs, SideEffect } from "./uiContract";
import { bind, bindingProps, detailSlot } from "./uiNavigation";
import { dispatch, registerCommand, useUiTarget } from "./uiRegistry";

// In order; each is named by its id (`left.settings.section.<id>`).
const SECTIONS = ["you", "voice", "brains", "agents", "projects", "team", "knowledge", "safety", "updates",
                  "server"] as const satisfies
  readonly CommandArgs["settings.section"]["section"][];
export type SectionId = (typeof SECTIONS)[number];

const sectionKey = (id: SectionId) => `left.settings.section.${id}` as const;

const SETUP_STEPS = ["voice", "you"] as const satisfies readonly SectionId[];
const SETUP_DELAY_MS = 2000;

// ── Settings' route state and its commands ───────────────────────────────
// Whether Settings is open (the centre's `settings` detail), the section
// asked for, and the first-time setup step: changed only here, by the
// registered commands every opener runs.

const settingsSlot = detailSlot<true>("settings");
/** @routeState Whether Settings is open: the centre offers its tab while it is. */
export const settingsOpen = settingsSlot.value;
/** @routeState The section to bring to the top once Settings is drawn, when one was asked for. */
const asked = createStore<SectionId | null>(null);
/** @routeState The first-time setup step being shown, or null when it is not setup. */
const setup = createStore<number | null>(null);

/** Whether a section is on show: all are, except during first-time setup,
 *  which shows its own step's alone. */
function sectionShown(id: SectionId, step = setup.get()): boolean {
  return step === null || SETUP_STEPS[step] === id;
}

// How long a section brought to the top is kept there while the sections
// above it are still being read (the Brains section draws its cards and the
// engine settings once they arrive): long enough for those reads, ended at
// once by his own scrolling.
const HOLD_SECTION_MS = 3000;

/** Bring a section to the top of the view (at once: the pointer measures
 *  it straight after), and keep it there while what is above it grows. */
function goTo(id: SectionId): void {
  const section = document.getElementById(`settings-${id}`);
  section?.scrollIntoView({ block: "start" });
  const body = section?.closest(".settings-body");
  if (!section || !body) return;
  const again = new ResizeObserver(() => section.scrollIntoView({ block: "start" }));
  for (const child of Array.from(body.children)) again.observe(child);
  const stop = () => {
    again.disconnect();
    for (const kind of ["wheel", "touchstart", "keydown", "pointerdown"]) body.removeEventListener(kind, stop);
  };
  for (const kind of ["wheel", "touchstart", "keydown", "pointerdown"]) body.addEventListener(kind, stop, { once: true });
  window.setTimeout(stop, HOLD_SECTION_MS);
}

/** Show Settings, at `section` if one is asked for: the body of open and
 *  section alike. */
function show(section: SectionId | undefined): SideEffect[] {
  closeMenu();
  asked.set(section ?? null);
  return settingsSlot.show(true);
}

registerCommand("settings.open", {
  availability: ({ section }) => settingsSlot.value.get() && section && !sectionShown(section)
    ? { available: false, reason: "hidden" } : { available: true },
  execute({ section, setup: first }) {
    const opened = settingsSlot.value.get() === null;
    if (first) setup.set(0);
    const effects = show(section);
    return { status: "completed", effects: opened ? ["settings.opened", ...effects] : effects };
  },
});

registerCommand("settings.section", {
  availability: ({ section }) => !settingsSlot.value.get() ? { available: false, reason: "not-mounted" }
    : sectionShown(section) ? { available: true } : { available: false, reason: "hidden" },
  execute: ({ section }) => ({ status: "completed", effects: show(section) }),
});

registerCommand("settings.close", {
  availability: () => settingsSlot.value.get() ? { available: true } : { available: false, reason: "no-entity" },
  execute() {
    setup.set(null);
    asked.set(null);
    return { status: "completed", effects: settingsSlot.close() };
  },
});

interface Status {
  voice_output?: { provider: string; voice: string; installed: boolean; state: string;
    error: string | null; download_bytes: number; metrics: { rtf: number } | null };
  claude_code_installed: boolean;
  server_port: number;
  uptime_seconds: number;
  env_keys_set: { fish_audio: boolean; fish_voice_id: boolean; user_name: string };
  imported: { at: string; copied: string[] } | null;
}

async function getJson<T>(url: string): Promise<T> {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`${res.status}`);
  return res.json() as Promise<T>;
}

async function postJson<T>(url: string, body: unknown): Promise<T> {
  const res = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" },
                                 body: JSON.stringify(body) });
  if (!res.ok) throw new Error(`${res.status}`);
  return res.json() as Promise<T>;
}

const readStatus = () => getJson<Status>("/api/settings/status");

/** First-time setup: no Fish key means JARVIS cannot speak at all, so two
 *  seconds after the page loads Settings opens as setup, through
 *  `settings.open` like every other opener. The page does it for him; it
 *  counts as his navigation. */
export function useFirstRunSetup(): void {
  useEffect(() => {
    const timer = setTimeout(async () => {
      const s = await readStatus().catch(() => null);
      if (s && !(s.voice_output?.provider === "local" ? s.voice_output.installed : s.env_keys_set.fish_audio)) void dispatch("settings.open", { setup: true }, { actor: "human" });
    }, SETUP_DELAY_MS);
    return () => clearTimeout(timer);
  }, []);
}

function formatUptime(t: Translate, seconds: number): string {
  if (seconds < 60) return t("time.short.seconds", { n: Math.floor(seconds) });
  if (seconds < 3600) return t("time.short.minutes", { n: Math.floor(seconds / 60) });
  return t("left.server.hoursMinutes", { hours: Math.floor(seconds / 3600),
                                         minutes: Math.floor((seconds % 3600) / 60) });
}

/** Each section's target, registered while it is on show. SECTIONS is
 *  fixed, so the hooks run in the same order on every render. */
function useSectionTargets(step: number | null) {
  const t = useT();
  return SECTIONS.map((id) => ({
    id,
    ui: useUiTarget(sectionShown(id, step)  // eslint-disable-line react-hooks/rules-of-hooks
      ? { ref: { region: "main", view: "main.settings", kind: "settingsSection", key: id },
          label: t("targets.settingsSection", { section: t(sectionKey(id)) }), socket: CENTRE_SOCKET }
      : null),
  }));
}

/** The Settings view, while it is the centre's selected view. */
export function SettingsView() {
  const t = useT();
  const step = useSyncExternalStore(setup.subscribe, setup.get);
  const status = useLiveRead(readStatus);
  const sections = useSectionTargets(step);
  const ui = useUiTarget({ ref: { region: "main", view: "main.settings", kind: "settings" },
                           label: t("menu.settings"), socket: CENTRE_SOCKET });

  // Opened or moved to a section: bring it to the top once it is drawn.
  const section = useSyncExternalStore(asked.subscribe, asked.get);
  useEffect(() => {
    if (!section) return;
    goTo(section);
    asked.set(null);
  }, [section]);

  const close = bindingProps(bind("settings.close", {}));
  const body: Record<SectionId, () => ReactNode> = {
    you: () => <><YouSection /><SettingsFields area="you" /></>,
    voice: () => <><VoiceSection status={status.value} reload={status.refresh} /><SettingsFields area="voice" /></>,
    brains: () => <><BrainsSection /><SettingsFields area="brains" /></>,
    agents: () => <SettingsFields area="agents" />,
    projects: () => <SettingsFields area="projects" />,
    team: () => <><TeamSection /><SettingsFields area="team" /></>,
    knowledge: () => <><KnowledgeSettings /><SettingsFields area="knowledge" /></>,
    safety: () => <SettingsFields area="safety" />,
    updates: () => <UpdatesSection />,
    server: () => <ServerSection status={status.value} failed={status.error !== null} />,
  };
  const last = step !== null && step + 1 >= SETUP_STEPS.length;

  return (
    <div id="settings-view" ref={ui.whole}>
      <header className="settings-head">
        <h2>{t("menu.settings")}</h2>
        <button type="button" className="workflow-close" title={t("left.settings.close")}
                aria-label={t("left.settings.close")} ref={ui.part("close")} {...close}>×</button>
        {step === null
          ? <nav className="settings-nav" aria-label={t("left.settings.sections")} ref={ui.part("nav")}>
              {SECTIONS.map((id) => (
                <button key={id} type="button" ref={ui.part(id)}
                        {...bindingProps(bind("settings.section", { section: id }))}>{t(sectionKey(id))}</button>
              ))}
            </nav>
          : <p className="settings-welcome">{t("left.settings.welcome")}</p>}
      </header>
      <div className="settings-body">
        {sections.filter(({ id }) => sectionShown(id, step)).map(({ id, ui: s }) => (
          <section key={id} className="settings-section" id={`settings-${id}`} ref={s.whole}>
            <h3 ref={s.part("heading")}>{t(sectionKey(id))}</h3>
            <div className="settings-section-body" ref={s.part("body")}>{body[id]()}</div>
          </section>
        ))}
        {step !== null && (
          <div className="settings-actions">
            {last
              ? <button className="settings-btn primary" ref={ui.part("setup")} {...close}>
                  {t("left.settings.finishSetup")}</button>
              : <button className="settings-btn primary" data-ui-action="setup" ref={ui.part("setup")}
                        onClick={() => setup.set(step + 1)}>{t("left.settings.nextName")}</button>}
          </div>
        )}
      </div>
    </div>
  );
}

// How he may be addressed (spoken_words.HONORIFICS): the stored value, and
// its words on the control.
const HONORIFICS = [
  ["sir", "left.you.honorific.sir"],
  ["ma'am", "left.you.honorific.maam"],
  ["señor", "left.you.honorific.senor"],
  ["none", "left.you.honorific.none"],
] as const;

function YouSection() {
  const t = useT();
  // Saving is held until what is stored has been read: saving the empty
  // fields of a read that failed would write a blank name over the real one.
  const stored = useLiveRead(() =>
    getJson<{ user_name: string; honorific: string }>("/api/settings/preferences"));
  const [name, setName] = useState("");
  const [honorific, setHonorific] = useState("sir");
  const [filled, setFilled] = useState(false);
  const nameUi = useSettingsItem("name", t("left.you.name"));
  const honorificUi = useSettingsItem("honorific", t("left.you.honorific"));
  const saveUi = useSettingsItem("saveYou", t("left.settings.save"));
  const steadyUi = useSettingsItem("steady", t("left.you.steady"));
  const steady = useSyncExternalStore(motion.subscribe, motion.get) === "steady";

  useEffect(() => {
    if (stored.value && !filled) {
      setName(stored.value.user_name || "");
      setHonorific(stored.value.honorific || "sir");
      setFilled(true);
    }
  }, [stored.value, filled]);

  const save = async () => {
    if (!filled) return;
    await postJson("/api/settings/preferences", { user_name: name.trim(), honorific });
  };

  return (
    <>
      <SettingsRow label={t("left.you.name")} labelRef={nameUi.part("label")} htmlFor="settings-name">
        <input data-ui-action="input" id="settings-name" type="text" placeholder={t("left.you.namePlaceholder")}
               value={name} ref={nameUi.whole} onChange={(e) => setName(e.target.value)} />
      </SettingsRow>
      <SettingsRow label={t("left.you.honorific")} labelRef={honorificUi.part("label")} htmlFor="settings-honorific">
        <select data-ui-action="input" id="settings-honorific" value={honorific} ref={honorificUi.whole}
                onChange={(e) => setHonorific(e.target.value)}>
          {HONORIFICS.map(([value, words]) => <option key={value} value={value}>{t(words)}</option>)}
        </select>
      </SettingsRow>
      {!filled && (
        <p className="settings-hint read-failure">
          {stored.error ? t("left.you.unreadable", { error: stored.error }) : t("left.you.reading")}
        </p>
      )}
      <div className="settings-actions">
        <button data-ui-action="domain" className="settings-btn primary" onClick={save} disabled={!filled}
                ref={saveUi.whole}>{t("left.settings.save")}</button>
      </div>
      <SettingsRow label={t("left.you.steady")} labelRef={steadyUi.part("label")} htmlFor="settings-steady">
        <input data-ui-action="input" id="settings-steady" type="checkbox" checked={steady} ref={steadyUi.whole}
               onChange={(e) => setMotion(e.target.checked ? "steady" : "full")} />
      </SettingsRow>
      <p className="settings-hint">{t("left.you.steadyHint")}</p>
    </>
  );
}

const fishTestErrors: Partial<Record<number, "invalidKey" | "noCredit" | "badVoiceId">> = {
  400: "badVoiceId",
  401: "invalidKey",
  402: "noCredit",
};

function VoiceSection({ status, reload }: { status: Status | null; reload: () => void }) {
  const t = useT();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<"saveFailed" | "installFailed" | "previewFailed" | "keySaveFailed" | "voiceIdSaveFailed" | null>(null);
  const [sample, setSample] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const output = status?.voice_output;
  const provider = output?.provider ?? "fish";
  const voice = output?.voice ?? "george";
  const change = async (provider: string, voice: string) => {
    setSaving(true); setError(null); setSample(null);
    try { await postJson("/api/settings/voice-output", { provider, voice }); await reload(); }
    catch { setError("saveFailed"); }
    finally { setSaving(false); }
  };
  const installLocal = async () => {
    setBusy(true); setError(null);
    try { await postJson("/api/settings/voice-output/install", {}); await reload(); }
    catch { setError("installFailed"); }
    finally { setBusy(false); }
  };
  const preview = async () => {
    setBusy(true); setError(null); setSample(null);
    try {
      const result = await postJson<{audio: string | null}>("/api/tts-test", {});
      if (!result.audio) throw new Error("No audio");
      setSample(`data:audio/${provider === "local" ? "wav" : "mpeg"};base64,${result.audio}`);
      await reload();
    } catch { setError("previewFailed"); }
    finally { setBusy(false); }
  };
  const providerUi = useSettingsItem("voiceOutputProvider", t("left.voice.outputProvider"));
  const localVoiceUi = useSettingsItem("localVoice", t("left.voice.localVoice"), provider === "local");
  const localInstallUi = useSettingsItem("installLocalVoice", t("left.voice.installLocal"), provider === "local" && !output?.installed);
  const previewUi = useSettingsItem("previewVoice", t("left.voice.preview"));
  const [key, setKey] = useState("");
  const [voiceId, setVoiceId] = useState("");
  const [tested, setTested] = useState<"testing" | "testPassed" | "invalidKey" | "noCredit" | "badVoiceId" | "testFailed" | null>(null);
  const voiceUi = useSettingsItem("voiceId", t("left.voice.voiceId"), provider === "fish");
  const saveVoiceUi = useSettingsItem("saveVoiceId", t("left.settings.save"), provider === "fish");
  const keyUi = useSettingsItem("fishKey", t("left.voice.fishKey"), provider === "fish");
  const testUi = useSettingsItem("testFishKey", t("left.voice.test"), provider === "fish");
  const saveKeyUi = useSettingsItem("saveFishKey", t("left.settings.save"), provider === "fish");
  const hasSavedKey = status?.env_keys_set.fish_audio;
  const testing = tested === "testing";
  const dot: Dot | null = tested ? (testing ? "yellow" : tested === "testPassed" ? "green" : "red") : null;

  const test = async () => {
    setError(null); setTested("testing");
    try {
      const r = await postJson<{ valid: boolean; status_code?: number }>("/api/settings/test-fish",
                                                                        { key_value: key.trim() || undefined });
      setTested(r.valid ? "testPassed" : fishTestErrors[r.status_code ?? 0] ?? "testFailed");
    } catch {
      setTested("testFailed");
    }
  };
  const saveFishSetting = async (name: "fish_api_key" | "fish_voice_id", value: string) => {
    setSaving(true); setError(null);
    try {
      await postJson("/api/settings/keys", { key_name: name, key_value: value.trim() });
      if (name === "fish_api_key") setKey("");
      setTested(null);
      reload();
    } catch {
      setError(name === "fish_api_key" ? "keySaveFailed" : "voiceIdSaveFailed");
    } finally {
      setSaving(false);
    }
  };

  return (
    <>
      <LanguageControl />
      <VoiceInputSettingsControl />
      <SettingsRow label={t("left.voice.outputProvider")} labelRef={providerUi.part("label")} htmlFor="voice-provider">
        <select data-ui-action="input" id="voice-provider" ref={providerUi.whole}
                value={provider} disabled={!status || saving || busy}
                onChange={e => void change(e.target.value, voice)}>
          <option value="fish">{t("left.voice.fishCloud")}</option>
          <option value="local">{t("left.voice.kokoroCpu")}</option>
        </select>
      </SettingsRow>
      {provider === "local" && <>
        <SettingsRow label={t("left.voice.localVoice")} labelRef={localVoiceUi.part("label")} htmlFor="local-voice">
          <select data-ui-action="input" id="local-voice" ref={localVoiceUi.whole}
                  value={voice} disabled={saving || busy} onChange={e => void change("local", e.target.value)}>
            {(["george", "daniel", "lewis", "emma"] as const).map(id =>
              <option key={id} value={id}>{t(`left.voice.preset.${id}`)}</option>)}
          </select>
        </SettingsRow>
        <p role="status">{busy ? t("left.voice.working") : output?.error ?? t(
          output?.installed ? "left.voice.localInstalled" : "left.voice.localMissing")}</p>
        {!output?.installed && <button data-ui-action="domain" className="settings-btn"
          ref={localInstallUi.whole} disabled={busy || saving} onClick={() => void installLocal()}>
          {t("left.voice.installLocal")}</button>}
        {output?.metrics && <p>{t("left.voice.computeTime", { rtf: output.metrics.rtf.toFixed(2) })}</p>}
      </>}
      {provider === "fish" && <>
      <SettingsRow label={t("left.voice.voiceId")} labelRef={voiceUi.part("label")} htmlFor="settings-voice-id">
        <input data-ui-action="input" id="settings-voice-id" type="text" placeholder="612b878b113047d9a770c069c8b4fdfe"
               value={voiceId} ref={voiceUi.whole} disabled={saving} onChange={(e) => setVoiceId(e.target.value)} />
        <button data-ui-action="domain" className="settings-btn" disabled={!voiceId.trim() || testing || saving}
                onClick={() => void saveFishSetting("fish_voice_id", voiceId)} ref={saveVoiceUi.whole}>
          {t("left.settings.save")}</button>
      </SettingsRow>
      <SettingsRow label={t("left.voice.fishKey")} labelRef={keyUi.part("label")} htmlFor="settings-fish-key">
        <input data-ui-action="input" id="settings-fish-key" type="password" autoComplete="off"
               placeholder={t("left.voice.fishKeyPlaceholder")} value={key} ref={keyUi.whole}
               disabled={testing || saving} onChange={(e) => { setKey(e.target.value); setTested(null); }} />
        <button data-ui-action="domain" className="settings-btn" disabled={testing || saving}
                onClick={test} ref={testUi.whole}>
          {t("left.voice.test")}</button>
        {dot && <StatusDot status={dot} />}
        <button data-ui-action="domain" className="settings-btn primary" disabled={!key.trim() || testing || saving}
                onClick={() => void saveFishSetting("fish_api_key", key)} ref={saveKeyUi.whole}>
          {t("left.settings.save")}</button>
      </SettingsRow>
      {hasSavedKey !== undefined && <p role="status">{t(hasSavedKey ? "left.voice.keySaved" : "left.voice.keyMissing")}</p>}
      {tested && <p role="status">{t(`left.voice.${tested}`)}</p>}
      </>}
      <button data-ui-action="domain" className="settings-btn" ref={previewUi.whole}
              disabled={busy || saving || (provider === "local" && !output?.installed)}
              onClick={() => void preview()}>{t("left.voice.preview")}</button>
      {sample && <audio controls autoPlay src={sample} aria-label={t("left.voice.previewLabel")} />}
      {error && <p role="alert">{t(`left.voice.${error}`)}</p>}
    </>
  );
}

// GET /api/update (self_update.Status.to_json) and POST /api/update
// (self_update.Outcome.to_json).
type CiState = "" | "passed" | "pending" | "failed" | "cancelled" | "none" | "unknown";

// A version: a pull request merged to main (self_update.Version.to_json).
interface UpdateVersion {
  sha: string;
  pr: number | null;
  title: string;
  date: string;
  ci: CiState;
  name: string;
  can_choose: boolean;
  unchosen: string;
}

interface UpdateStatus {
  source: "github" | "installed" | "other";
  reason: string;
  error: string;
  branch: string;
  commit: string;
  behind: number;
  current: UpdateVersion | null;
  versions: UpdateVersion[];
  ci: CiState;
  blockers: { code: string; text: string; detail: string[]; values?: { count?: number } }[];
  up_to_date: boolean;
  can_update: boolean;
}

interface UpdateOutcome {
  status: UpdateStatus;
  ok: boolean;
  updated: boolean;
  version: UpdateVersion | null;
  refused: string;
  /** Why it was refused (self_update.REFUSED_*): its words are the page's. */
  refused_code?: "busy" | "source" | "cannot_check" | "blocked" | "";
  failed: string;
  to_commit: string;
  notes: string[];
  note_codes?: string[];
  restart: boolean;
}

// Each version's tests, as its row says them, and the colour they take.
const CI_WORDS = {
  passed: ["left.updates.ci.passed", "pass"], pending: ["left.updates.ci.pending", "wait"],
  failed: ["left.updates.ci.failed", "fail"], cancelled: ["left.updates.ci.cancelled", "fail"],
  none: ["left.updates.ci.none", "fail"],
  unknown: ["left.updates.ci.unknown", "wait"],
} as const;

// Why a version whose tests have not passed cannot be chosen, by its CI.
const UNCHOSEN_WORDS = {
  pending: "left.updates.unchosen.pending", failed: "left.updates.unchosen.failed",
  cancelled: "left.updates.unchosen.cancelled",
  none: "left.updates.unchosen.none", unknown: "left.updates.unchosen.unknown",
} as const;

// The latest commit's CI is said on each version's row instead.
const CI_BLOCKERS = new Set(["ci_not_passed", "version_ci_not_passed"]);

const SOURCE_WORDS = { installed: "left.updates.installed", other: "left.updates.notGithub" } as const;

const NOTE_WORDS: Record<string, "left.updates.note.frontendPackages"> = {
  frontend_packages: "left.updates.note.frontendPackages",
};

/** A version's name in words: "PR 42", or its short sha. */
function versionName(t: Translate, v: UpdateVersion): string {
  return v.pr !== null ? t("left.updates.prName", { pr: v.pr }) : v.sha.slice(0, 7);
}

function shortDate(iso: string): string {
  const when = new Date(iso);
  return Number.isNaN(when.getTime()) ? "" : formatDateTime(when, { day: "numeric", month: "short" });
}

/** A version as a line: its name, its pull request's title (as written)
 *  and its short sha. */
function VersionTitle({ version }: { version: UpdateVersion }) {
  const t = useT();
  return version.pr !== null
    ? <><b>{versionName(t, version)}</b>: {version.title} <code>{version.sha.slice(0, 7)}</code></>
    : <><code>{version.sha.slice(0, 7)}</code> {version.title}</>;
}

/** What stops an update (self_update's blocker codes), with what it names
 *  (a branch, files, a version) as it came. A code the page does not know
 *  keeps the server's sentence. */
function blockerWords(t: Translate, b: UpdateStatus["blockers"][number]): { text: string; detail: string } {
  const listed = b.detail.join(", ");
  switch (b.code) {
    case "not_on_main": return { text: t("left.updates.blocker.notOnMain"), detail: listed };
    case "modified": return { text: t("left.updates.blocker.modified"), detail: listed };
    case "would_overwrite": return { text: t("left.updates.blocker.wouldOverwrite"), detail: listed };
    case "ci_not_passed": return { text: t("left.updates.blocker.ciNotPassed"), detail: listed };
    case "not_newer": return { text: t("left.updates.blocker.notNewer"), detail: listed };
    case "not_offered": return { text: t("left.updates.blocker.notOffered"), detail: listed };
    case "version_ci_not_passed": return { text: t("left.updates.blocker.versionCiNotPassed"), detail: listed };
    case "local_commits":
      return b.values?.count !== undefined
        ? { text: t("left.updates.blocker.localCommits", { count: b.values.count }), detail: "" }
        : { text: b.text, detail: listed };
    default: return { text: b.text, detail: listed };
  }
}

/** Why an update was refused, from its code; the server's words if the
 *  page does not know it. */
function refusedWords(t: Translate, out: UpdateOutcome): string {
  const s = out.status;
  switch (out.refused_code) {
    case "busy": return t("left.updates.busy");
    case "source": return s.source === "github" ? out.refused : t(SOURCE_WORDS[s.source]);
    case "cannot_check": return t("left.updates.cannotCheck");
    case "blocked": return s.blockers.map((b) => blockerWords(t, b).text).join(" ");
    default: return out.refused;
  }
}

/** What an update did, kept as what happened and worded where shown. */
type UpdateResult =
  | { kind: "installing"; version: UpdateVersion }
  | { kind: "restarting"; version: UpdateVersion | null; commit: string; notes: string[] }
  | { kind: "refused"; out: UpdateOutcome }
  | { kind: "failed"; detail: string }
  | { kind: "current" }
  | { kind: "no-answer" };

/** One version on offer: its radio, words and tests, a target by its commit. */
function VersionRow({ version: v, latest, chosen, updating, choose }: {
  version: UpdateVersion; latest: boolean; chosen: boolean; updating: boolean; choose: () => void;
}) {
  const t = useT();
  const ui = useSettingsRow("updateVersion", v.sha, versionName(t, v));
  const [ciKey, ciClass] = v.ci ? CI_WORDS[v.ci] : [null, ""];
  const why = v.can_choose ? "" : v.ci && v.ci !== "passed" ? t(UNCHOSEN_WORDS[v.ci]) : v.unchosen;
  return (
    <li className={"update-version" + (v.can_choose ? "" : " off") + (chosen ? " sel" : "")} ref={ui.whole}>
      <label>
        <input data-ui-action="input" type="radio" name="update-version" ref={ui.part("choose")}
               disabled={!v.can_choose || updating} checked={chosen} onChange={choose} />
        <span className="update-version-body">
          <span className="update-version-title">
            {v.pr !== null ? `${versionName(t, v)}: ${v.title}` : `${versionName(t, v)} ${v.title}`}
            {latest && <span className="update-tag">{t("left.updates.latest")}</span>}
          </span>
          <span className="update-version-meta">
            {shortDate(v.date)} · <code>{v.sha.slice(0, 7)}</code>
            {why ? ` · ${why}` : ""}
          </span>
        </span>
        <span className={`update-ci ${ciClass}`} ref={ui.part("ci")}>{ciKey ? t(ciKey) : ""}</span>
      </label>
    </li>
  );
}

function UpdatesSection() {
  const t = useT();
  const [status, setStatus] = useState<UpdateStatus | null>(null);
  const [checking, setChecking] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [updating, setUpdating] = useState(false);
  const [result, setResult] = useState<UpdateResult | null>(null);
  const [chosen, setChosen] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);

  const check = useCallback(async () => {
    setChecking(true);
    setProblem(null);
    setResult((was) => (was?.kind === "refused" || was?.kind === "failed" || was?.kind === "no-answer" ? null : was));
    setConfirming(false);
    try {
      setStatus(await getJson<UpdateStatus>("/api/update"));
    } catch (e) {
      setProblem((e as Error).message);
    } finally {
      setChecking(false);
    }
  }, []);

  useEffect(() => { check(); }, [check]);

  // The newest version that can be chosen starts chosen, and stays chosen
  // across a check unless it can no longer be.
  const versions = status?.versions ?? [];
  useEffect(() => {
    setChosen((was) => versions.some((v) => v.sha === was && v.can_choose) ? was
      : versions.find((v) => v.can_choose)?.sha ?? null);
  }, [status]);  // eslint-disable-line react-hooks/exhaustive-deps

  const choice = versions.find((v) => v.sha === chosen) ?? null;

  const install = async () => {
    if (!choice) return;
    if (!confirming) {
      setConfirming(true);
      setResult(null);
      return;
    }
    setConfirming(false);
    setUpdating(true);
    setResult({ kind: "installing", version: choice });
    try {
      const res = await fetch("/api/update", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ version: choice.sha }) });
      const out = (await res.json()) as UpdateOutcome;
      setStatus(out.status);
      if (out.restart) {
        const commit = out.to_commit.slice(0, 7);
        setResult({ kind: "restarting", version: out.version, commit, notes: out.note_codes ?? [] });
        reloadAfterRestart(t("left.updates.updatedRestarting",
                             { version: out.version ? versionName(t, out.version) : commit }));
        return;
      }
      setResult(out.refused ? { kind: "refused", out }
        : out.failed ? { kind: "failed", detail: out.failed }
        : { kind: "current" });
    } catch {
      setResult({ kind: "no-answer" });
    }
    setUpdating(false);
  };

  const ready = status !== null && status.source === "github" && !status.error;
  const current = ready ? status.current : null;
  const offered = ready && versions.length > 0;
  const currentUi = useSettingsItem("currentVersion",
    current ? t("left.updates.running", { version: versionName(t, current) }) : "", current !== null);
  const checkUi = useSettingsItem("checkUpdates", t("left.updates.checkAgain"));
  const installUi = useSettingsItem("installUpdate", t("left.updates.install"), offered);
  let summary: ReactNode = t("left.updates.checking");
  if (problem) summary = t("left.updates.unasked", { error: problem });
  else if (status && status.source !== "github") summary = t(SOURCE_WORDS[status.source]);
  else if (status?.error) {
    summary = <>{t("left.updates.cannotCheck")} <span data-original-detail="">{status.error}</span></>;
  } else if (status?.up_to_date) summary = t("left.updates.upToDate");
  else if (status) summary = t("left.updates.newer", { count: versions.length });
  const stopping = ready ? status.blockers.filter((b) => !CI_BLOCKERS.has(b.code)) : [];
  const brings = choice ? versions.length - versions.indexOf(choice) : 0;

  let said: ReactNode = null;
  let refusal: ReactNode = null;
  if (result?.kind === "installing") said = t("left.updates.installing", { version: versionName(t, result.version) });
  else if (result?.kind === "restarting") {
    said = [t("left.updates.updatedRestarting",
              { version: result.version ? versionName(t, result.version) : result.commit }),
            t("left.updates.reloads"),
            ...result.notes.map((n) => NOTE_WORDS[n] ? t(NOTE_WORDS[n]) : n)].join(" ");
  } else if (result?.kind === "refused") {
    refusal = t("left.updates.notUpdated", { reason: refusedWords(t, result.out) });
  } else if (result?.kind === "failed") {
    refusal = <>{t("left.updates.failed")} <span data-original-detail="">{result.detail}</span></>;
  } else if (result?.kind === "current") said = t("left.updates.alreadyUpToDate");
  else if (result?.kind === "no-answer") refusal = t("left.updates.noAnswer");

  return (
    <>
      <LanguageKitStatus />
      {current && (
        <p className="update-current" ref={currentUi.whole}>
          {placed((mark) => t("left.updates.running", { version: mark }), <VersionTitle version={current} />)}
        </p>
      )}
      <p className="settings-hint">{checking && status ? t("left.updates.checkingAgain") : summary}</p>
      {offered && (
        <ul className="update-versions">
          {versions.map((v) => (
            <VersionRow key={v.sha} version={v} latest={v === versions[0]} chosen={v.sha === chosen}
                        updating={updating} choose={() => { setChosen(v.sha); setConfirming(false); }} />
          ))}
        </ul>
      )}
      {confirming && choice && (
        <div className="update-confirm">
          {t("left.updates.confirm", { version: versionName(t, choice), count: brings })}
          <ul>
            <li>{t("left.updates.confirm.reload")}</li>
            <li>{status?.current
              ? t("left.updates.confirm.moveBack", { version: versionName(t, status.current) })
              : t("left.updates.confirm.moveBackHere")}</li>
            <li>{t("left.updates.confirm.noOlder")}</li>
          </ul>
        </div>
      )}
      {refusal ? <p className="update-refused">{refusal}</p>
        : stopping.map((b) => {
          const { text, detail } = blockerWords(t, b);
          return (
            <p key={b.code} className="update-refused">
              {text}{detail && <> <span data-original-detail="">{detail}</span></>}
            </p>
          );
        })}
      {said && <p className="settings-restart-notice">{said}</p>}
      <div className="settings-actions">
        <button data-ui-action="refresh" className="settings-btn" onClick={check} disabled={checking || updating}
                ref={checkUi.whole}>
          {t("left.updates.checkAgain")}</button>
        {offered && (
          <button data-ui-action="restart" className="settings-btn primary" onClick={install} ref={installUi.whole}
                  disabled={!choice || stopping.length > 0 || updating || checking}>
            {updating && choice ? t("left.updates.installing", { version: versionName(t, choice) })
              : confirming && choice ? t("left.updates.yesInstall", { version: versionName(t, choice) })
              : t("left.updates.install")}
          </button>
        )}
      </div>
    </>
  );
}

function LanguageKitStatus() {
  const t = useT();
  const label = t("left.updates.languageKit");
  const target = useSettingsItem("languageKit", label);
  const live = useLiveRead(async () => {
    const response = await fetch("/api/language-kits/status");
    if (!response.ok) throw new Error(t("left.updates.kitUnavailable"));
    return response.json() as Promise<{ active: { version: string } | null; adopted?: boolean; active_error?: string; last_check: number | null; error?: string; compatibility_review_required?: boolean }>;
  });
  useEffect(() => { const timer = setInterval(live.refresh, 60000); return () => clearInterval(timer); }, [live.refresh]);
  const state = live.value;
  return <SettingsRow label={label} labelRef={target.part("label")}>
    <div ref={target.whole}>
      <p>{state?.active ? t("left.updates.kitActive", { version: state.active.version }) : state ? t("left.updates.kitNotAdopted") : t("left.updates.kitReading")}</p>
      {state?.last_check ? <p>{t("left.updates.kitLastCheck", { when: formatDateTime(state.last_check * 1000, { dateStyle: "short", timeStyle: "short" }) })}</p> : null}
      {state?.compatibility_review_required && <p>{t("left.updates.kitCompatibility")}</p>}
      {(live.error || state?.error) && <p role="status">{live.error || state?.error}</p>}
      {state?.adopted && state.active_error && <p role="status">{state.active_error}</p>}
    </div>
  </SettingsRow>;
}

function ServerSection({ status, failed }: { status: Status | null; failed: boolean }) {
  const t = useT();
  const port = useSettingsItem("serverPort", t("left.server.port"));
  const uptime = useSettingsItem("serverUptime", t("left.server.uptime"));
  const appearance = useSettingsItem("appearance", t("left.server.appearance"));
  const restartUi = useSettingsItem("restartNow", t("left.server.restartNow"));
  const selectedTheme = useSyncExternalStore(theme.subscribe, theme.get);
  const values = useSyncExternalStore(settingsValues.subscribe, settingsValues.get).value;
  const known = status !== null && !failed;
  const pending = (values?.settings ?? []).some((s) => s.pending_restart);
  const restart = async () => {
    try {
      await fetch("/api/restart", { method: "POST" });
      reloadAfterRestart(t("menu.restarting"));
    } catch { /* the server did not take it; the button stays to try again */ }
  };
  return (
    <>
      {failed && (
        <p className="settings-state"><StatusDot status="red" />{t("left.server.noAnswer")}</p>
      )}
      {values?.start.problem && (
        <p role="alert" className="settings-state"><StatusDot status="red" />
          {t(values.start.problem === "tampered" ? "left.server.startTampered" : "left.server.startUnreadable")}</p>
      )}
      {values?.start.data_dir_from_env && (
        <p role="alert" className="settings-state">
          {t("left.server.dataDirFromEnv", { path: values.start.data_dir_from_env })}</p>
      )}
      {status?.imported && (
        <p className="settings-hint">{t("left.server.imported", {
          when: formatDateTime(new Date(status.imported.at), { dateStyle: "medium" }) })}</p>
      )}
      <SettingsFields area="server" />
      {pending && <p className="settings-restart-notice">{t("left.server.pending")}</p>}
      <button data-ui-action="restart" className="settings-btn" ref={restartUi.whole} onClick={() => void restart()}>
        {t("left.server.restartNow")}</button>
      <SettingsRow label={t("left.server.port")} labelRef={port.part("label")}>
        <span className="settings-value" ref={port.whole}>{known ? status.server_port : "—"}</span>
      </SettingsRow>
      <SettingsRow label={t("left.server.uptime")} labelRef={uptime.part("label")}>
        <span className="settings-value" ref={uptime.whole}>{known ? formatUptime(t, status.uptime_seconds) : "—"}</span>
      </SettingsRow>
      <SettingsRow label={t("left.server.appearance")} labelRef={appearance.part("label")} htmlFor="workspace-theme">
        <select data-ui-action="input" id="workspace-theme" ref={appearance.whole} value={selectedTheme}
                onChange={e => setTheme(e.target.value as Theme)}>
          <option value="classic">{t("left.server.classic")}</option>
          <option value="graphite">{t("left.server.graphite")}</option>
        </select>
      </SettingsRow>
      <p className="settings-hint">{t("left.server.appearanceHint")}</p>
    </>
  );
}
