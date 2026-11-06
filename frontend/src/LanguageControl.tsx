/**
 * Settings → Voice and language: the language JARVIS hears and speaks, which is also the
 * interface's language (locale.ts), and the Spanish speech model it needs
 * (language_setting.py, speech_models.py).
 *
 * Language: English | Español. Changing it reloads the speech model, which
 * takes a few seconds, and JARVIS confirms aloud in the new language; the
 * choice is kept by JARVIS, not in .env or the browser. The interface
 * changes once the server has put the language in force: from the change's
 * answer here, or the `language` frame every open page gets; a refused
 * change leaves it as it was. Español can be chosen only once its speech
 * model is installed: "Download (124 MB)" starts that, checked against the
 * release's SHA-256, and its progress is read again every second while it
 * runs (there is no push for it). Once installed, the model's row is not
 * shown: Español being on offer says it. The English model, which speech
 * input starts with, has the same row, shown only while it is not
 * installed, with Try again after a failed download. When the model speech
 * input has is installed but it still reports a failure, the hint says to
 * stop JARVIS and launch it again.
 *
 * Each language is named in its own words in either interface language.
 * The brain's `set_language` and `install_speech_model` call the same
 * functions as these routes.
 */

import { useEffect, useState } from "react";
import { useLiveRead } from "./liveRead";
import {
  formatNumber, guardedRead, language as interfaceLanguage, SaidText, useLanguage, useT, type Said, type Translate,
} from "./locale";
import { SettingsRow, useSettingsItem } from "./SettingsRow";
import { speechRefusal, type SpeechRefusal } from "./voiceInput";

interface Install {
  state: "downloading" | "verifying" | "installing" | "installed" | "failed";
  doneBytes: number;
  totalBytes: number;
  error: string | null;
  errorCode: string | null;
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

// What each language is called on the control: in its own words, whatever
// the interface's language.
const LANGUAGES = [
  { language: "en", name: "English" },
  { language: "es", name: "Español" },
] as const;
const ENGLISH = "en";
const SPANISH = "es";
const RUNNING = new Set(["downloading", "verifying", "installing"]);
const PROGRESS_EVERY_MS = 1000;

const megabytes = (t: Translate, bytes: number) =>
  t("units.megabytes", { amount: formatNumber(Math.round(bytes / 1_000_000)) });

/** A refusal's body (speech_models.Refused), thrown by `readJson`. */
class Refusal extends Error {
  constructor(readonly body: SpeechRefusal) { super(body.error); }
}

async function readJson<T>(res: Response): Promise<T> {
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    const refusal = body as SpeechRefusal;
    throw new Refusal({ ...refusal, error: refusal.error || `${res.status}` });
  }
  return body as T;
}

/** What a failed change or download says: the refusal's words, or what
 *  the browser said when the server could not be reached. */
const problemSaid = (e: unknown): Said =>
  e instanceof Refusal ? speechRefusal(e.body) : { original: (e as Error).message };

// What it reads is the language in force too, so the interface takes it
// (unless a newer pushed report came first).
const readStatus = async () => {
  const take = guardedRead();
  const status = await fetch("/api/settings/language").then(readJson<LanguageStatus>);
  take(status.language);
  return status;
};

/** How far a download under way has got. */
function progressWords(t: Translate, install: Install): string {
  if (install.state === "downloading")
    return t("language.downloading", { done: megabytes(t, install.doneBytes), total: megabytes(t, install.totalBytes) });
  return t(install.state === "verifying" ? "language.verifying" : "language.installing");
}

/** What a failed download said, or null when it has not failed. */
const installFailure = (install: Install | null | undefined): Said | null =>
  install?.state === "failed"
    ? speechRefusal({ code: install.errorCode ?? undefined, detail: install.error ?? undefined }) : null;

export function LanguageControl() {
  const t = useT();
  const inForce = useLanguage();
  // Read again whenever the language in force moves (another page, the
  // brain or the spoken phrase changed it): the models' state goes with it.
  const live = useLiveRead(readStatus, [interfaceLanguage]);
  const [busy, setBusy] = useState<string | null>(null);
  const [problem, setProblem] = useState<Said | null>(null);
  const status = live.value;
  const spanish = status?.speech.models.find((m) => m.language === SPANISH) ?? null;
  const downloading = Boolean(spanish?.install && RUNNING.has(spanish.install.state));
  const english = status?.speech.models.find((m) => m.language === ENGLISH) ?? null;
  const englishDownloading = Boolean(english?.install && RUNNING.has(english.install.state));

  // While a download runs, read its progress again every second.
  const anyDownloading = downloading || englishDownloading;
  useEffect(() => {
    if (!anyDownloading) return;
    const timer = setInterval(live.refresh, PROGRESS_EVERY_MS);
    return () => clearInterval(timer);
  }, [anyDownloading, live.refresh]);

  const choose = async (language: string) => {
    setBusy(language);
    setProblem(null);
    const take = guardedRead();
    try {
      const done = await fetch("/api/settings/language", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ language }),
      }).then(readJson<LanguageStatus>);
      take(done.language);
    } catch (e) {
      setProblem(problemSaid(e));
    } finally {
      setBusy(null);
      live.refresh();
    }
  };

  const download = async (language: string) => {
    setProblem(null);
    try {
      await fetch(`/api/speech/models/${language}/install`, { method: "POST" }).then(readJson);
    } catch (e) {
      setProblem(problemSaid(e));
    }
    live.refresh();
  };

  const install = spanish?.install;
  const failed = installFailure(install);
  const missing = spanish !== null && !spanish.installed;
  const englishInstall = english?.install;
  const englishFailed = installFailure(englishInstall);
  const englishMissing = english !== null && !english.installed;
  // The model speech input has is installed, yet it still reports a failure.
  const loaded = status?.speech.models.find((m) => m.language === status?.speech.language) ?? null;
  const relaunch = status?.speech.state === "failed" && Boolean(loaded?.installed);
  const languageUi = useSettingsItem("language", t("language.label"));
  const englishUi = useSettingsItem("englishModel", t("language.englishModel"), englishMissing);
  const modelUi = useSettingsItem("spanishModel", t("language.spanishModel"), missing);
  return (
    <>
      <SettingsRow label={t("language.label")} labelRef={languageUi.part("label")} htmlFor="settings-language">
        <select id="settings-language" data-ui-action="domain" value={status ? inForce : ""}
                disabled={!status || busy !== null} ref={languageUi.whole}
                onChange={(e) => choose(e.target.value)}>
          {LANGUAGES.map((l) => (
            <option key={l.language} value={l.language} lang={l.language}
                    disabled={l.language === SPANISH && !spanish?.installed}>{l.name}</option>
          ))}
        </select>
      </SettingsRow>
      {busy && <p className="settings-hint">{t("language.loading")}</p>}
      {englishMissing && (
        <SettingsRow label={t("language.englishModel")} labelRef={englishUi.part("label")}>
          {englishDownloading && englishInstall
            ? <span className="settings-value" ref={englishUi.whole}>{progressWords(t, englishInstall)}</span>
            : <button className="settings-btn" data-ui-action="domain" onClick={() => download(ENGLISH)}
                      ref={englishUi.whole}>
                {t(englishFailed ? "language.retry" : "language.download",
                   { size: megabytes(t, english.archiveBytes) })}</button>}
        </SettingsRow>
      )}
      {englishFailed && <SaidText said={englishFailed} as="p" className="settings-hint read-failure" />}
      {missing && (
        <SettingsRow label={t("language.spanishModel")} labelRef={modelUi.part("label")}>
          {downloading && install
            ? <span className="settings-value" ref={modelUi.whole}>{progressWords(t, install)}</span>
            : <button className="settings-btn" data-ui-action="domain" onClick={() => download(SPANISH)}
                      ref={modelUi.whole}>
                {t("language.download", { size: megabytes(t, spanish.archiveBytes) })}</button>}
        </SettingsRow>
      )}
      {failed && <SaidText said={failed} as="p" className="settings-hint read-failure" />}
      {relaunch && <p className="settings-hint read-failure">{t("language.relaunch")}</p>}
      {problem && <SaidText said={problem} as="p" className="settings-hint read-failure" />}
      {!status && (
        <p className="settings-hint read-failure">
          {live.error ? t("language.unreadable", { error: live.error }) : t("language.reading")}
        </p>
      )}
    </>
  );
}
