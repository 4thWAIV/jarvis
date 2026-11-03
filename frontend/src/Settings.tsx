/**
 * The Settings view, opened from the ⋮ menu (MenuControl.tsx): a large view
 * over the page with its sections listed down the left and stacked in one
 * scrolling body — Brains, Voice, You, Team, Updates, System.
 *
 * With no Fish Audio key set it opens by itself, two seconds after the page
 * loads, as first-time setup: Voice first, then You, then it closes.
 *
 * Everything is read from and written to the same endpoints the dialog
 * before it used (/api/settings/*), plus /api/brains for the Brains section
 * and /api/update for Updates (self_update.py), which the menu's Check for
 * updates opens.
 *
 * Opening, closing and going to a section are registered commands
 * (settings.open, settings.close, settings.section), run the same by his
 * clicks and by the brain. Saving, testing keys, brains, sign-in, membership
 * and updates stay on their own authorised paths. Settings and each section
 * on show are targets hung from the overlay rail; while Settings covers the
 * orb, pointing inside it is refused as blocked (there is never a second
 * orb).
 *
 * Its words are the catalog's (left.settings.* and each section's), in the
 * interface's language; a section is named by its registered id, and the
 * brain's discovery labels stay in English. Updates are worded from
 * self_update's codes; a commit's subject, a branch, a path or a failing
 * step's output is shown as it came.
 */

import { useCallback, useEffect, useState, useSyncExternalStore, type ReactNode } from "react";
import { BrainsSection } from "./BrainsSection";
import { closeMenu, reloadAfterRestart } from "./MenuControl";
import { LanguageControl } from "./LanguageControl";
import { TeamSection } from "./TeamSection";
import { useLiveRead } from "./liveRead";
import { formatDateTime, placed, useT, type Translate } from "./locale";
import { StatusDot, type Dot } from "./StatusDot";
import { createStore } from "./store";
import type { CommandArgs, SideEffect } from "./uiContract";
import { bind, bindingProps, type ReadStore } from "./uiNavigation";
import { registerCommand, useUiTarget } from "./uiRegistry";

// In order; each is named by its id (`left.settings.section.<id>`).
const SECTIONS = ["brains", "voice", "you", "team", "updates", "system"] as const satisfies
  readonly CommandArgs["settings.section"]["section"][];
export type SectionId = (typeof SECTIONS)[number];

const sectionKey = (id: SectionId) => `left.settings.section.${id}` as const;

const SETUP_STEPS = ["voice", "you"] as const;
const SETUP_DELAY_MS = 2000;

// ── Settings' route state and its commands ───────────────────────────────
// Whether Settings is open, the section asked for, and the first-time
// setup step: changed only here, by the registered commands his clicks and
// the brain's requests both run (settings.open, settings.section,
// settings.close), and by first-time setup opening it by itself.

/** @routeState Whether Settings is open. */
const open = createStore(false);
export const settingsOpen: ReadStore<boolean> = open;
/** @routeState The section to go to once Settings is drawn, when one was asked for. */
const asked = createStore<SectionId | null>(null);
/** @routeState The first-time setup step being shown, or null when it is not setup. */
const setup = createStore<number | null>(null);

/** Whether a section is on show: all are, except during first-time setup,
 *  which shows its own step's alone. */
function sectionShown(id: SectionId): boolean {
  const step = setup.get();
  return step === null || SETUP_STEPS[step] === id;
}

/** Bring a section to the top of Settings' body. */
function goTo(id: SectionId): void {
  document.getElementById(`settings-${id}`)?.scrollIntoView({ behavior: "smooth", block: "start" });
}

registerCommand("settings.open", {
  availability: ({ section }) => open.get() && section && !sectionShown(section)
    ? { available: false, reason: "hidden" } : { available: true },
  execute({ section }) {
    closeMenu();
    const effects: SideEffect[] = open.get() ? [] : ["settings.opened"];
    asked.set(section ?? null);
    open.set(true);
    return { status: "completed", effects };
  },
});

registerCommand("settings.section", {
  availability: ({ section }) => !open.get() ? { available: false, reason: "not-mounted" }
    : sectionShown(section) ? { available: true } : { available: false, reason: "hidden" },
  execute({ section }) {
    goTo(section);
    return { status: "completed", effects: [] };
  },
});

registerCommand("settings.close", {
  availability: () => open.get() ? { available: true } : { available: false, reason: "no-entity" },
  execute() {
    open.set(false);
    setup.set(null);
    return { status: "completed", effects: [] };
  },
});

// Pointed at from the overlay rail, down the left of the open view.
const SOCKET = { rail: "rail.overlay", side: "left" } as const;

interface Status {
  claude_code_installed: boolean;
  server_port: number;
  uptime_seconds: number;
  env_keys_set: { fish_audio: boolean; fish_voice_id: boolean; user_name: string };
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

function formatUptime(t: Translate, seconds: number): string {
  if (seconds < 60) return t("time.short.seconds", { n: Math.floor(seconds) });
  if (seconds < 3600) return t("time.short.minutes", { n: Math.floor(seconds / 60) });
  return t("left.system.hoursMinutes", { hours: Math.floor(seconds / 3600),
                                         minutes: Math.floor((seconds % 3600) / 60) });
}

/** One section of Settings: its heading and body, registered as the
 *  section's target (keyed by its ID) while it is on show. */
function SettingsSection({ id, children }: { id: SectionId; children: ReactNode }) {
  const t = useT();
  const ui = useUiTarget({ ref: { region: "overlay", view: "overlay.settings", kind: "settingsSection", key: id },
                           label: t("targets.settingsSection", { section: t(sectionKey(id)) }), socket: SOCKET });
  return (
    <section className="settings-section" id={`settings-${id}`} ref={ui.whole}>
      <h3 ref={ui.part("heading")}>{t(sectionKey(id))}</h3>
      <div className="settings-section-body" ref={ui.part("body")}>{children}</div>
    </section>
  );
}

export function Settings() {
  const t = useT();
  const shown = useSyncExternalStore(open.subscribe, open.get);
  const step = useSyncExternalStore(setup.subscribe, setup.get);
  const [status, setStatus] = useState<Status | null>(null);
  const [statusFailed, setStatusFailed] = useState(false);

  const loadStatus = useCallback(async () => {
    try {
      const s = await getJson<Status>("/api/settings/status");
      setStatus(s);
      setStatusFailed(false);
      return s;
    } catch {
      setStatus(null);
      setStatusFailed(true);
      return null;
    }
  }, []);

  useEffect(() => {
    if (shown) loadStatus();
  }, [shown, loadStatus]);

  // First-time setup: no Fish key means JARVIS cannot speak at all. The
  // page opens Settings by itself here; no one navigated.
  useEffect(() => {
    const timer = setTimeout(async () => {
      const s = await loadStatus();
      if (s && !s.env_keys_set.fish_audio) {
        setup.set(0);
        open.set(true);
      }
    }, SETUP_DELAY_MS);
    return () => clearTimeout(timer);
  }, [loadStatus]);

  // Opened at a section: go to it once the sections are drawn.
  const section = useSyncExternalStore(asked.subscribe, asked.get);
  useEffect(() => {
    if (!shown || !section) return;
    goTo(section);
    asked.set(null);
  }, [shown, section]);

  const ui = useUiTarget(shown ? { ref: { region: "overlay", view: "overlay.settings", kind: "settings" },
                                   label: t("menu.settings"), socket: SOCKET } : null);

  if (!shown) return null;
  const close = bindingProps(bind("settings.close", {}));
  const visible = (id: SectionId) => step === null || SETUP_STEPS[step] === id;

  return (
    <div id="settings-container" className="open">
      <div className="settings-backdrop" {...close} />
      <div className="settings-panel settings-view" role="dialog" aria-label={t("menu.settings")} ref={ui.whole}>
        <div className="settings-header">
          <h2>{t("menu.settings")}</h2>
          <button className="settings-close" aria-label={t("left.settings.close")} ref={ui.part("close")}
                  {...close}>×</button>
        </div>
        {step !== null && (
          <div className="settings-welcome"><p>{t("left.settings.welcome")}</p></div>
        )}
        <div className="settings-columns">
          {step === null && (
            <nav className="settings-nav" aria-label={t("left.settings.sections")} ref={ui.part("nav")}>
              {SECTIONS.map((id) => (
                <button key={id} type="button" {...bindingProps(bind("settings.section", { section: id }))}>
                  {t(sectionKey(id))}</button>
              ))}
            </nav>
          )}
          <div className="settings-body">
            {visible("brains") && <SettingsSection id="brains"><BrainsSection /></SettingsSection>}
            {visible("voice") && <SettingsSection id="voice">
              <VoiceSection status={status} statusFailed={statusFailed} reload={loadStatus} /></SettingsSection>}
            {visible("you") && <SettingsSection id="you"><YouSection reload={loadStatus} /></SettingsSection>}
            {visible("team") && <SettingsSection id="team"><TeamSection /></SettingsSection>}
            {visible("updates") && <SettingsSection id="updates"><UpdatesSection /></SettingsSection>}
            {visible("system") && <SettingsSection id="system">
              <SystemSection status={status} statusFailed={statusFailed} /></SettingsSection>}
            {step !== null && (
              <div className="setup-nav">
                {step + 1 >= SETUP_STEPS.length
                  ? <button className="settings-btn primary" {...close}>{t("left.settings.finishSetup")}</button>
                  : <button className="settings-btn primary" data-ui-action="setup"
                            onClick={() => setup.set(step + 1)}>{t("left.settings.nextName")}</button>}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

function VoiceSection({ status, statusFailed, reload }: {
  status: Status | null; statusFailed: boolean; reload: () => Promise<Status | null>;
}) {
  const t = useT();
  const [key, setKey] = useState("");
  const [voiceId, setVoiceId] = useState("");
  const [tested, setTested] = useState<Dot | null>(null);
  const [savedKey, setSavedKey] = useState(false);
  const known: Dot = statusFailed ? "off" : status ? (status.env_keys_set.fish_audio ? "green" : "red") : "off";

  const test = async () => {
    setTested("yellow");
    try {
      const r = await postJson<{ valid: boolean }>("/api/settings/test-fish",
                                                    { key_value: key.trim() || undefined });
      setTested(r.valid ? "green" : "red");
    } catch {
      setTested("red");
    }
  };
  const saveKeys = async () => {
    const value = key.trim();
    if (value) await postJson("/api/settings/keys", { key_name: "FISH_API_KEY", key_value: value });
    setTested(null);
    await reload();
    setSavedKey(Boolean(value));
  };
  const saveVoice = async () => {
    const value = voiceId.trim();
    if (value) await postJson("/api/settings/keys", { key_name: "FISH_VOICE_ID", key_value: value });
  };

  return (
    <>
      <div className="settings-field">
        <label>{t("left.voice.fishKey")}</label>
        <div className="settings-input-row">
          <input data-ui-action="input" type="password" placeholder={t("left.voice.fishKeyPlaceholder")} value={key}
                 onChange={(e) => setKey(e.target.value)} />
          <button data-ui-action="domain" className="settings-btn" onClick={test}>{t("left.voice.test")}</button>
          <StatusDot status={tested ?? known} />
        </div>
      </div>
      <div className="settings-field">
        <label>{t("left.voice.voiceId")}</label>
        <div className="settings-input-row">
          <input data-ui-action="input" type="text" placeholder="612b878b113047d9a770c069c8b4fdfe" value={voiceId}
                 onChange={(e) => setVoiceId(e.target.value)} />
          <button data-ui-action="domain" className="settings-btn" onClick={saveVoice}>{t("left.voice.save")}</button>
        </div>
      </div>
      <div className="settings-actions">
        <button data-ui-action="domain" className="settings-btn primary" onClick={saveKeys}>
          {t("left.voice.saveKeys")}</button>
      </div>
      {savedKey && (
        <p className="settings-restart-notice">{t("left.voice.restartNotice", { restart: t("menu.restart") })}</p>
      )}
      <LanguageControl />
    </>
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

function YouSection({ reload }: { reload: () => Promise<Status | null> }) {
  const t = useT();
  // Saving is held until what is stored has been read: saving the empty
  // fields of a read that failed would write a blank name over the real one.
  const stored = useLiveRead(() =>
    getJson<{ user_name: string; honorific: string }>("/api/settings/preferences"));
  const [name, setName] = useState("");
  const [honorific, setHonorific] = useState("sir");
  const [filled, setFilled] = useState(false);

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
    await reload();
  };

  return (
    <>
      <div className="settings-field">
        <label>{t("left.you.name")}</label>
        <input data-ui-action="input" type="text" placeholder={t("left.you.namePlaceholder")} value={name}
               onChange={(e) => setName(e.target.value)} />
      </div>
      <div className="settings-field">
        <label>{t("left.you.honorific")}</label>
        <select data-ui-action="input" value={honorific} onChange={(e) => setHonorific(e.target.value)}>
          {HONORIFICS.map(([value, words]) => <option key={value} value={value}>{t(words)}</option>)}
        </select>
      </div>
      {!filled && (
        <p className="settings-hint read-failure">
          {stored.error ? t("left.you.unreadable", { error: stored.error }) : t("left.you.reading")}
        </p>
      )}
      <div className="settings-actions">
        <button data-ui-action="domain" className="settings-btn primary" onClick={save} disabled={!filled}>
          {t("left.you.save")}</button>
      </div>
    </>
  );
}

// GET /api/update (self_update.Status.to_json) and POST /api/update
// (self_update.Outcome.to_json).
type CiState = "" | "passed" | "pending" | "failed" | "none" | "unknown";

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
  failed: ["left.updates.ci.failed", "fail"], none: ["left.updates.ci.none", "fail"],
  unknown: ["left.updates.ci.unknown", "wait"],
} as const;

// Why a version whose tests have not passed cannot be chosen, by its CI.
const UNCHOSEN_WORDS = {
  pending: "left.updates.unchosen.pending", failed: "left.updates.unchosen.failed",
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
      {ready && status.current && (
        <p className="update-current">
          {placed((mark) => t("left.updates.running", { version: mark }), <VersionTitle version={status.current} />)}
        </p>
      )}
      <p className="settings-hint">{checking && status ? t("left.updates.checkingAgain") : summary}</p>
      {ready && versions.length > 0 && (
        <ul className="update-versions">
          {versions.map((v, i) => {
            const [ciKey, ciClass] = v.ci ? CI_WORDS[v.ci] : [null, ""];
            const why = v.can_choose ? "" : v.ci && v.ci !== "passed" ? t(UNCHOSEN_WORDS[v.ci]) : v.unchosen;
            return (
              <li key={v.sha} className={"update-version" + (v.can_choose ? "" : " off")
                                         + (v.sha === chosen ? " sel" : "")}>
                <label>
                  <input data-ui-action="input" type="radio" name="update-version"
                         disabled={!v.can_choose || updating} checked={v.sha === chosen}
                         onChange={() => { setChosen(v.sha); setConfirming(false); }} />
                  <span className="update-version-body">
                    <span className="update-version-title">
                      {v.pr !== null ? `${versionName(t, v)}: ${v.title}` : `${versionName(t, v)} ${v.title}`}
                      {i === 0 && <span className="update-tag">{t("left.updates.latest")}</span>}
                    </span>
                    <span className="update-version-meta">
                      {shortDate(v.date)} · <code>{v.sha.slice(0, 7)}</code>
                      {why ? ` · ${why}` : ""}
                    </span>
                  </span>
                  <span className={`update-ci ${ciClass}`}>{ciKey ? t(ciKey) : ""}</span>
                </label>
              </li>
            );
          })}
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
        <button data-ui-action="refresh" className="settings-btn" onClick={check} disabled={checking || updating}>
          {t("left.updates.checkAgain")}</button>
        {ready && versions.length > 0 && (
          <button data-ui-action="restart" className="settings-btn primary" onClick={install}
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

function SystemSection({ status, statusFailed }: { status: Status | null; statusFailed: boolean }) {
  const t = useT();
  const detail = statusFailed ? t("left.system.noAnswer")
    : status ? t("left.system.detail", { port: String(status.server_port),
                                         uptime: formatUptime(t, status.uptime_seconds) }) : "";
  return (
    <>
      <div className="status-grid">
        <div className="status-row">
          <StatusDot status={statusFailed ? "red" : status ? "green" : "off"} />
          <span>{t("left.common.server")}</span><span className="status-detail">{detail}</span>
        </div>
      </div>
      <div className="sysinfo-grid">
        <div className="sysinfo-row"><span className="sysinfo-label">{t("left.system.port")}</span>
          <span>{status && !statusFailed ? status.server_port : "—"}</span></div>
        <div className="sysinfo-row"><span className="sysinfo-label">{t("left.system.uptime")}</span>
          <span>{status && !statusFailed ? formatUptime(t, status.uptime_seconds) : "—"}</span></div>
      </div>
    </>
  );
}
