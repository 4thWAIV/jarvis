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
interface UpdateStatus {
  source: "github" | "installed" | "other";
  reason: string;
  error: string;
  branch: string;
  commit: string;
  behind: number;
  commits: { sha: string; subject: string }[];
  ci: "" | "passed" | "pending" | "failed" | "none" | "unknown";
  blockers: { code: string; text: string; detail: string[] }[];
  up_to_date: boolean;
  can_update: boolean;
}

interface UpdateOutcome {
  status: UpdateStatus;
  ok: boolean;
  updated: boolean;
  refused: string;
  failed: string;
  to_commit: string;
  notes: string[];
  restart: boolean;
}

const CI_WORDS: Record<UpdateStatus["ci"], string> = {
  "": "", passed: "CI passed", pending: "CI is still running", failed: "CI failed",
  none: "no CI result", unknown: "GitHub could not be asked about CI",
};

function UpdatesSection() {
  const [status, setStatus] = useState<UpdateStatus | null>(null);
  const [checking, setChecking] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [updating, setUpdating] = useState(false);
  const [result, setResult] = useState<string | null>(null);

  const check = useCallback(async () => {
    setChecking(true);
    setProblem(null);
    try {
      setStatus(await getJson<UpdateStatus>("/api/update"));
    } catch (e) {
      setProblem(`Could not ask the server (${(e as Error).message}).`);
    } finally {
      setChecking(false);
    }
  }, []);

  useEffect(() => { check(); }, [check]);

  const update = async () => {
    setUpdating(true);
    setResult(null);
    try {
      const res = await fetch("/api/update", { method: "POST" });
      const out = (await res.json()) as UpdateOutcome;
      setStatus(out.status);
      if (out.restart) {
        const said = `Updated to ${out.to_commit.slice(0, 7)}; restarting.`;
        setResult([said, ...out.notes].join(" "));
        reloadAfterRestart(said);
        return;
      }
      setResult(out.refused ? `Not updated: ${out.refused}`
        : out.failed ? `The update failed: ${out.failed}`
        : "Already up to date.");
    } catch {
      setResult("The update failed: the server did not answer.");
    }
    setUpdating(false);
  };

  let summary = "Checking for updates…";
  if (problem) summary = problem;
  else if (status && status.source !== "github") summary = status.reason;
  else if (status?.error) summary = `Could not check for updates: ${status.error}`;
  else if (status?.up_to_date) summary = `Up to date at ${status.commit.slice(0, 7)}.`;
  else if (status) {
    summary = `${status.behind} new commit${status.behind === 1 ? "" : "s"} on main`
      + (status.ci ? `; ${CI_WORDS[status.ci]}.` : ".");
  }
  const showCommits = status && status.source === "github" && !status.error && status.behind > 0;

  return (
    <>
      <p className="settings-hint">{checking && status ? "Checking again…" : summary}</p>
      {showCommits && (
        <ul className="update-commits">
          {status.commits.map((c) => (
            <li key={c.sha}><code>{c.sha.slice(0, 7)}</code> {c.subject}</li>
          ))}
        </ul>
      )}
      {showCommits && status.blockers.map((b) => (
        <p key={b.code} className="settings-hint read-failure">
          {b.text}{b.detail.length ? ` ${b.detail.join(", ")}` : ""}
        </p>
      ))}
      {result && <p className="settings-restart-notice">{result}</p>}
      <div className="settings-actions">
        <button className="settings-btn" onClick={check} disabled={checking || updating}>
          Check again</button>
        {status?.can_update && (
          <button className="settings-btn primary" onClick={update} disabled={updating || checking}>
            {updating ? "Updating…" : "Update and restart"}</button>
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
