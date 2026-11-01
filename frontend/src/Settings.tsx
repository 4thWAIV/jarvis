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
 */

import { useCallback, useEffect, useState, useSyncExternalStore, type ReactNode } from "react";
import { BrainsSection } from "./BrainsSection";
import { closeMenu, reloadAfterRestart } from "./MenuControl";
import { TeamSection } from "./TeamSection";
import { useLiveRead } from "./liveRead";
import { StatusDot, type Dot } from "./StatusDot";
import { createStore } from "./store";
import type { CommandArgs, SideEffect } from "./uiContract";
import { bind, bindingProps, type ReadStore } from "./uiNavigation";
import { registerCommand, useUiTarget } from "./uiRegistry";

const SECTIONS = [
  { id: "brains", title: "Brains" },
  { id: "voice", title: "Voice" },
  { id: "you", title: "You" },
  { id: "team", title: "Team" },
  { id: "updates", title: "Updates" },
  { id: "system", title: "System" },
] as const satisfies readonly { id: CommandArgs["settings.section"]["section"]; title: string }[];
export type SectionId = (typeof SECTIONS)[number]["id"];

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

function formatUptime(seconds: number): string {
  if (seconds < 60) return `${Math.floor(seconds)}s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m`;
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  return `${h}h ${m}m`;
}

/** One section of Settings: its heading and body, registered as the
 *  section's target (keyed by its ID) while it is on show. */
function SettingsSection({ id, children }: { id: SectionId; children: ReactNode }) {
  const title = SECTIONS.find((s) => s.id === id)!.title;
  const ui = useUiTarget({ ref: { region: "overlay", view: "overlay.settings", kind: "settingsSection", key: id },
                           label: `Settings, ${title}`, socket: SOCKET });
  return (
    <section className="settings-section" id={`settings-${id}`} ref={ui.whole}>
      <h3 ref={ui.part("heading")}>{title}</h3>
      <div className="settings-section-body" ref={ui.part("body")}>{children}</div>
    </section>
  );
}

export function Settings() {
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
                                   label: "Settings", socket: SOCKET } : null);

  if (!shown) return null;
  const close = bindingProps(bind("settings.close", {}));
  const visible = (id: SectionId) => step === null || SETUP_STEPS[step] === id;

  return (
    <div id="settings-container" className="open">
      <div className="settings-backdrop" {...close} />
      <div className="settings-panel settings-view" role="dialog" aria-label="Settings" ref={ui.whole}>
        <div className="settings-header">
          <h2>Settings</h2>
          <button className="settings-close" aria-label="Close" ref={ui.part("close")} {...close}>&times;</button>
        </div>
        {step !== null && (
          <div className="settings-welcome"><p>Welcome to JARVIS. Let's get you set up.</p></div>
        )}
        <div className="settings-columns">
          {step === null && (
            <nav className="settings-nav" aria-label="Settings sections" ref={ui.part("nav")}>
              {SECTIONS.map((s) => (
                <button key={s.id} type="button" {...bindingProps(bind("settings.section", { section: s.id }))}>
                  {s.title}</button>
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
                  ? <button className="settings-btn primary" {...close}>Finish Setup</button>
                  : <button className="settings-btn primary" data-ui-action="setup"
                            onClick={() => setup.set(step + 1)}>Next: Set Your Name</button>}
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
    <>
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
    </>
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
    <>
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
    </>
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
    <>
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
    </>
  );
}
