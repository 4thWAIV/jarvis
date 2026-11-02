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
 */

import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from "react";
import { BrainsSection } from "./BrainsSection";
import { LanguageControl } from "./LanguageControl";
import { TeamSection } from "./TeamSection";
import { useLiveRead } from "./liveRead";
import { StatusDot, type Dot } from "./StatusDot";
import { createStore } from "./store";
import { notice } from "./voiceHealth";

export const settingsOpen = createStore(false);
// The section to scroll to once Settings is open, when one was asked for.
const settingsSection = createStore<SectionId | null>(null);

/** Open Settings, at `section` if one is given (the sign-in reminder opens
 *  it at Team, the menu's Check for updates at Updates). */
export function openSettings(section: SectionId | null = null): void {
  settingsSection.set(section);
  settingsOpen.set(true);
}

interface Status {
  claude_code_installed: boolean;
  server_port: number;
  uptime_seconds: number;
  env_keys_set: { fish_audio: boolean; fish_voice_id: boolean; user_name: string };
}

const SECTIONS = [
  { id: "brains", title: "Brains" },
  { id: "voice", title: "Voice" },
  { id: "you", title: "You" },
  { id: "team", title: "Team" },
  { id: "updates", title: "Updates" },
  { id: "system", title: "System" },
] as const;
export type SectionId = (typeof SECTIONS)[number]["id"];

const SETUP_STEPS = ["voice", "you"] as const;
const SETUP_DELAY_MS = 2000;

// How long a restart is given before the page reloads itself.
const RELOAD_AFTER_MS = 4000;

/** The server is restarting: say so, and reload the page once it is back.
 *  The menu's Restart Server and Updates' Update and restart both end here. */
export function reloadAfterRestart(said: string): void {
  notice("Status", said);
  setTimeout(() => window.location.reload(), RELOAD_AFTER_MS);
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

function formatUptime(seconds: number): string {
  if (seconds < 60) return `${Math.floor(seconds)}s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m`;
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  return `${h}h ${m}m`;
}

export function Settings() {
  const open = useSyncExternalStore(settingsOpen.subscribe, settingsOpen.get);
  const [status, setStatus] = useState<Status | null>(null);
  const [statusFailed, setStatusFailed] = useState(false);
  // The first-time setup step being shown, or null when it is not setup.
  const [setup, setSetup] = useState<number | null>(null);
  const body = useRef<HTMLDivElement>(null);

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
    if (open) loadStatus();
  }, [open, loadStatus]);

  // First-time setup: no Fish key means JARVIS cannot speak at all.
  useEffect(() => {
    const timer = setTimeout(async () => {
      const s = await loadStatus();
      if (s && !s.env_keys_set.fish_audio) {
        setSetup(0);
        settingsOpen.set(true);
      }
    }, SETUP_DELAY_MS);
    return () => clearTimeout(timer);
  }, [loadStatus]);

  const close = () => {
    settingsOpen.set(false);
    setSetup(null);
  };

  const goTo = (id: string) =>
    body.current?.querySelector(`#settings-${id}`)?.scrollIntoView({ behavior: "smooth", block: "start" });

  // Opened at a section: go to it once the sections are drawn.
  const asked = useSyncExternalStore(settingsSection.subscribe, settingsSection.get);
  useEffect(() => {
    if (!open || !asked) return;
    goTo(asked);
    settingsSection.set(null);
  }, [open, asked]);

  if (!open) return null;
  const shown = (id: string) => setup === null || SETUP_STEPS[setup] === id;

  return (
    <div id="settings-container" className="open">
      <div className="settings-backdrop" onClick={close} />
      <div className="settings-panel settings-view" role="dialog" aria-label="Settings">
        <div className="settings-header">
          <h2>Settings</h2>
          <button className="settings-close" onClick={close} aria-label="Close">&times;</button>
        </div>
        {setup !== null && (
          <div className="settings-welcome"><p>Welcome to JARVIS. Let's get you set up.</p></div>
        )}
        <div className="settings-columns">
          {setup === null && (
            <nav className="settings-nav" aria-label="Settings sections">
              {SECTIONS.map((s) => (
                <button key={s.id} type="button" onClick={() => goTo(s.id)}>{s.title}</button>
              ))}
            </nav>
          )}
          <div className="settings-body" ref={body}>
            {shown("brains") && <section className="settings-section" id="settings-brains">
              <h3>Brains</h3><BrainsSection /></section>}
            {shown("voice") && <VoiceSection status={status} statusFailed={statusFailed}
                                             reload={loadStatus} />}
            {shown("you") && <YouSection reload={loadStatus} />}
            {shown("team") && <section className="settings-section" id="settings-team">
              <h3>Team</h3><TeamSection /></section>}
            {shown("updates") && <section className="settings-section" id="settings-updates">
              <h3>Updates</h3><UpdatesSection /></section>}
            {shown("system") && <SystemSection status={status} statusFailed={statusFailed} />}
            {setup !== null && (
              <div className="setup-nav">
                <button className="settings-btn primary" onClick={() =>
                  setup + 1 >= SETUP_STEPS.length ? close() : setSetup(setup + 1)}>
                  {setup === 0 ? "Next: Set Your Name" : "Finish Setup"}
                </button>
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
    <section className="settings-section" id="settings-voice">
      <h3>Voice</h3>
      <div className="settings-field">
        <label>Fish Audio API Key</label>
        <div className="settings-input-row">
          <input type="password" placeholder="Fish Audio key..." value={key}
                 onChange={(e) => setKey(e.target.value)} />
          <button className="settings-btn" onClick={test}>Test</button>
          <StatusDot status={tested ?? known} />
        </div>
      </div>
      <div className="settings-field">
        <label>Fish Voice ID</label>
        <div className="settings-input-row">
          <input type="text" placeholder="612b878b113047d9a770c069c8b4fdfe" value={voiceId}
                 onChange={(e) => setVoiceId(e.target.value)} />
          <button className="settings-btn" onClick={saveVoice}>Save</button>
        </div>
      </div>
      <div className="settings-actions">
        <button className="settings-btn primary" onClick={saveKeys}>Save Keys</button>
      </div>
      {savedKey && (
        <p className="settings-restart-notice">
          Saved. Restart the server (menu &rarr; Restart Server) for this to take effect.
        </p>
      )}
      <LanguageControl />
    </section>
  );
}

function YouSection({ reload }: { reload: () => Promise<Status | null> }) {
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
    <section className="settings-section" id="settings-you">
      <h3>You</h3>
      <div className="settings-field">
        <label>Your Name</label>
        <input type="text" placeholder="Your name" value={name} onChange={(e) => setName(e.target.value)} />
      </div>
      <div className="settings-field">
        <label>Honorific</label>
        <select value={honorific} onChange={(e) => setHonorific(e.target.value)}>
          <option value="sir">Sir</option>
          <option value="ma'am">Ma'am</option>
          <option value="señor">Señor</option>
          <option value="none">None</option>
        </select>
      </div>
      {!filled && (
        <p className="settings-hint read-failure">
          {stored.error ? `Your preferences could not be read (${stored.error}); trying again.`
                        : "Reading your preferences…"}
        </p>
      )}
      <div className="settings-actions">
        <button className="settings-btn primary" onClick={save} disabled={!filled}>Save Preferences</button>
      </div>
    </section>
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
  blockers: { code: string; text: string; detail: string[] }[];
  up_to_date: boolean;
  can_update: boolean;
}

interface UpdateOutcome {
  status: UpdateStatus;
  ok: boolean;
  updated: boolean;
  version: UpdateVersion | null;
  refused: string;
  failed: string;
  to_commit: string;
  notes: string[];
  restart: boolean;
}

// Each version's tests, as its row says them, and the colour they take.
const CI_WORDS: Record<CiState, [string, string]> = {
  "": ["", ""], passed: ["Tests passed", "pass"], pending: ["Tests running", "wait"],
  failed: ["Tests failed", "fail"], none: ["No test result", "fail"],
  unknown: ["Tests unknown", "wait"],
};

// The latest commit's CI is said on each version's row instead.
const CI_BLOCKERS = new Set(["ci_not_passed", "version_ci_not_passed"]);

function shortDate(iso: string): string {
  const when = new Date(iso);
  return Number.isNaN(when.getTime()) ? ""
    : when.toLocaleDateString("en-GB", { day: "numeric", month: "short" });
}

function VersionTitle({ version }: { version: UpdateVersion }) {
  return version.pr !== null
    ? <><b>{version.name}</b>: {version.title} <code>{version.sha.slice(0, 7)}</code></>
    : <><code>{version.sha.slice(0, 7)}</code> {version.title}</>;
}

function UpdatesSection() {
  const [status, setStatus] = useState<UpdateStatus | null>(null);
  const [checking, setChecking] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [updating, setUpdating] = useState(false);
  const [result, setResult] = useState<string | null>(null);
  const [refusal, setRefusal] = useState<string | null>(null);
  const [chosen, setChosen] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);

  const check = useCallback(async () => {
    setChecking(true);
    setProblem(null);
    setRefusal(null);
    setConfirming(false);
    try {
      setStatus(await getJson<UpdateStatus>("/api/update"));
    } catch (e) {
      setProblem(`Could not ask the server (${(e as Error).message}).`);
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
      setRefusal(null);
      return;
    }
    setConfirming(false);
    setUpdating(true);
    setResult(`Installing ${choice.name}…`);
    try {
      const res = await fetch("/api/update", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ version: choice.sha }) });
      const out = (await res.json()) as UpdateOutcome;
      setStatus(out.status);
      if (out.restart) {
        const said = `Updated to ${out.version?.name ?? out.to_commit.slice(0, 7)}; restarting.`;
        setResult([said, "The page reloads when JARVIS is back.", ...out.notes].join(" "));
        reloadAfterRestart(said);
        return;
      }
      setResult(null);
      if (out.refused) setRefusal(`Not updated: ${out.refused}`);
      else if (out.failed) setRefusal(`The update failed: ${out.failed}`);
      else setResult("Already up to date.");
    } catch {
      setResult(null);
      setRefusal("The update failed: the server did not answer.");
    }
    setUpdating(false);
  };

  const ready = status !== null && status.source === "github" && !status.error;
  let summary = "Checking for updates…";
  if (problem) summary = problem;
  else if (status && status.source !== "github") summary = status.reason;
  else if (status?.error) summary = `Could not check for updates: ${status.error}`;
  else if (status?.up_to_date) summary = "Up to date.";
  else if (status) {
    summary = `${versions.length} newer version${versions.length === 1 ? "" : "s"} on main. `
      + "Choose one to install.";
  }
  const stopping = ready ? status.blockers.filter((b) => !CI_BLOCKERS.has(b.code)) : [];
  const brings = choice ? versions.length - versions.indexOf(choice) : 0;

  return (
    <>
      {ready && status.current && (
        <p className="update-current">Running <VersionTitle version={status.current} /></p>
      )}
      <p className="settings-hint">{checking && status ? "Checking again…" : summary}</p>
      {ready && versions.length > 0 && (
        <ul className="update-versions">
          {versions.map((v, i) => {
            const [ciText, ciClass] = CI_WORDS[v.ci];
            return (
              <li key={v.sha} className={"update-version" + (v.can_choose ? "" : " off")
                                         + (v.sha === chosen ? " sel" : "")}>
                <label>
                  <input type="radio" name="update-version" disabled={!v.can_choose || updating}
                         checked={v.sha === chosen}
                         onChange={() => { setChosen(v.sha); setConfirming(false); }} />
                  <span className="update-version-body">
                    <span className="update-version-title">
                      {v.pr !== null ? `${v.name}: ${v.title}` : `${v.name} ${v.title}`}
                      {i === 0 && <span className="update-tag">LATEST</span>}
                    </span>
                    <span className="update-version-meta">
                      {shortDate(v.date)} · <code>{v.sha.slice(0, 7)}</code>
                      {v.can_choose ? "" : ` · ${v.unchosen}`}
                    </span>
                  </span>
                  <span className={`update-ci ${ciClass}`}>{ciText}</span>
                </label>
              </li>
            );
          })}
        </ul>
      )}
      {confirming && choice && (
        <div className="update-confirm">
          Install {choice.name} and restart JARVIS? This brings in {brings} pull
          request{brings === 1 ? "" : "s"}.
          <ul>
            <li>The page reloads by itself when JARVIS is back.</li>
            <li>If a step fails, JARVIS moves back to {status?.current?.name ?? "where it was"} and
              nothing changes.</li>
            <li>Going back to an older version is not offered.</li>
          </ul>
        </div>
      )}
      {refusal ? <p className="update-refused">{refusal}</p>
        : stopping.map((b) => (
          <p key={b.code} className="update-refused">
            {b.text}{b.detail.length ? ` ${b.detail.join(", ")}` : ""}
          </p>
        ))}
      {result && <p className="settings-restart-notice">{result}</p>}
      <div className="settings-actions">
        <button className="settings-btn" onClick={check} disabled={checking || updating}>
          Check again</button>
        {ready && versions.length > 0 && (
          <button className="settings-btn primary" onClick={install}
                  disabled={!choice || stopping.length > 0 || updating || checking}>
            {updating && choice ? `Installing ${choice.name}…`
              : confirming && choice ? `Yes, install ${choice.name}` : "Install and restart"}
          </button>
        )}
      </div>
    </>
  );
}

function SystemSection({ status, statusFailed }: { status: Status | null; statusFailed: boolean }) {
  const detail = statusFailed ? "no answer from the server"
    : status ? `port ${status.server_port} | up ${formatUptime(status.uptime_seconds)}` : "";
  return (
    <section className="settings-section" id="settings-system">
      <h3>System</h3>
      <div className="status-grid">
        <div className="status-row">
          <StatusDot status={statusFailed ? "red" : status ? "green" : "off"} />
          <span>Server</span><span className="status-detail">{detail}</span>
        </div>
      </div>
      <div className="sysinfo-grid">
        <div className="sysinfo-row"><span className="sysinfo-label">Server port</span>
          <span>{status && !statusFailed ? status.server_port : "—"}</span></div>
        <div className="sysinfo-row"><span className="sysinfo-label">Uptime</span>
          <span>{status && !statusFailed ? formatUptime(status.uptime_seconds) : "—"}</span></div>
      </div>
    </section>
  );
}
