/**
 * Settings → Voice: the language JARVIS hears and speaks, and the Spanish
 * speech model it needs (language_setting.py, speech_models.py).
 *
 * Language: English | Español. Changing it reloads the speech model, which
 * takes a few seconds, and JARVIS confirms aloud in the new language; the
 * choice is kept by JARVIS, not in .env. Español can be chosen only once its
 * speech model is installed: "Download (124 MB)" starts that, checked against
 * the release's SHA-256, and its progress is read again every second while it
 * runs (there is no push for it).
 *
 * The brain's `set_language` and `install_speech_model` call the same
 * functions as these routes.
 */

import { useEffect, useState } from "react";
import { useLiveRead } from "./liveRead";

interface Install {
  state: "downloading" | "verifying" | "installing" | "installed" | "failed";
  doneBytes: number;
  totalBytes: number;
  error: string | null;
}

interface Model {
  language: string;
  label: string;
  archiveBytes: number;
  installed: boolean;
  install: Install | null;
}

interface LanguageStatus {
  language: string;
  speech: { language: string | null; state: string; failure: string | null; models: Model[] };
}

// What each language is called on the control: in its own words.
const LANGUAGES = [
  { language: "en", name: "English" },
  { language: "es", name: "Español" },
] as const;
const SPANISH = "es";
const RUNNING = new Set(["downloading", "verifying", "installing"]);
const PROGRESS_EVERY_MS = 1000;

const megabytes = (bytes: number) => `${Math.round(bytes / 1_000_000)} MB`;

async function readJson<T>(res: Response): Promise<T> {
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error((body as { error?: string }).error || `${res.status}`);
  return body as T;
}

const readStatus = () => fetch("/api/settings/language").then(readJson<LanguageStatus>);

function installWords(model: Model): string {
  const install = model.install;
  if (model.installed) return "Installed";
  if (install?.state === "downloading")
    return `Downloading, ${megabytes(install.doneBytes)} of ${megabytes(install.totalBytes)}`;
  if (install?.state === "verifying") return "Checking the download";
  if (install?.state === "installing") return "Installing";
  return "Not installed";
}

export function LanguageControl() {
  const live = useLiveRead(readStatus);
  const [busy, setBusy] = useState<string | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const status = live.value;
  const spanish = status?.speech.models.find((m) => m.language === SPANISH) ?? null;
  const downloading = Boolean(spanish?.install && RUNNING.has(spanish.install.state));

  // While the download runs, read its progress again every second.
  useEffect(() => {
    if (!downloading) return;
    const timer = setInterval(live.refresh, PROGRESS_EVERY_MS);
    return () => clearInterval(timer);
  }, [downloading, live.refresh]);

  const choose = async (language: string) => {
    setBusy(language);
    setProblem(null);
    try {
      await fetch("/api/settings/language", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ language }),
      }).then(readJson<LanguageStatus>);
    } catch (e) {
      setProblem((e as Error).message);
    } finally {
      setBusy(null);
      live.refresh();
    }
  };

  const download = async () => {
    setProblem(null);
    try {
      await fetch(`/api/speech/models/${SPANISH}/install`, { method: "POST" }).then(readJson);
    } catch (e) {
      setProblem((e as Error).message);
    }
    live.refresh();
  };

  const failed = spanish?.install?.state === "failed" ? spanish.install.error : null;
  return (
    <>
      <div className="settings-field">
        <label htmlFor="settings-language">Language</label>
        <div className="settings-input-row">
          <select id="settings-language" value={status?.language ?? ""}
                  disabled={!status || busy !== null}
                  onChange={(e) => choose(e.target.value)}>
            {LANGUAGES.map((l) => (
              <option key={l.language} value={l.language}
                      disabled={l.language === SPANISH && !spanish?.installed}>{l.name}</option>
            ))}
          </select>
        </div>
        {busy && <p className="settings-hint">Loading the speech model; this takes a few seconds.</p>}
      </div>
      {spanish && (
        <div className="settings-field">
          <label>Spanish speech model</label>
          <div className="settings-input-row">
            <span className="settings-model-state">{installWords(spanish)}</span>
            {!spanish.installed && !downloading && (
              <button className="settings-btn" onClick={download}>
                Download ({megabytes(spanish.archiveBytes)})
              </button>
            )}
          </div>
          {failed && <p className="settings-hint read-failure">{failed}</p>}
        </div>
      )}
      {problem && <p className="settings-hint read-failure">{problem}</p>}
      {!status && (
        <p className="settings-hint read-failure">
          {live.error ? `The language could not be read (${live.error}); trying again.`
                      : "Reading the language…"}
        </p>
      )}
    </>
  );
}
