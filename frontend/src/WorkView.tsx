/**
 * One piece of work (one intent across projects, project_store's `works`)
 * in the centre area (design section 14): its outcome, its projects (the
 * primary first, each opening its Project view), its own folders, Start an
 * agent here (only in the work's folders: its projects' and its own, the
 * project service's rule, `run_location`), its runs, what is bound to it,
 * and the context pack previewed exactly as a run started for it is given
 * (`knowledge_api.run_pack`). Opened by `workRecord.open` from a Project
 * view, or by the brain; closed by `workRecord.close`.
 *
 * Starting an agent is his click; the brain's is spawn_run with the work's
 * ID, placed by the same service. Folder names and the pack are shown as
 * text only.
 *
 * Registered for pointing at: the view (header, close, outcome, projects,
 * folders, start, runs, bound, context).
 */

import { useState, useSyncExternalStore } from "react";
import { CENTRE_SOCKET } from "./ConversationView";
import { AvailabilityMark } from "./FolderMarks";
import type { Availability } from "./folders";
import { useLiveRead } from "./liveRead";
import { formatDateTime, useT } from "./locale";
import { answerError, getWork, getWorkContext, projectsChanged, startWorkRun } from "./projects";
import { bind, bindingProps } from "./uiNavigation";
import { useUiTarget } from "./uiRegistry";

export function WorkView({ id }: { id: string }) {
  const t = useT();
  const live = useLiveRead(() => getWork(id), [projectsChanged]);
  const pack = useLiveRead(() => getWorkContext(id), [projectsChanged]);
  const failed = useSyncExternalStore(answerError.subscribe, answerError.get);
  const d = live.value;
  const ui = useUiTarget({ ref: { region: "main", view: "main.workRecord", kind: "workRecord", key: id },
                           label: d?.work.title ?? t("tabs.main.workRecord"), socket: CENTRE_SOCKET });
  return (
    <div id="work-record-view" ref={ui.whole}>
      <section className="subsystem-head" ref={ui.part("header")}>
        <div className="subsystem-head-words">
          <h2>{d?.work.title ?? t("tabs.main.workRecord")}</h2>
          {d && <p className="subsystem-head-state">{t(`main.workRecord.state.${state(d.work.state)}`)}</p>}
        </div>
        <button type="button" className="workflow-close" title={t("main.common.close")} aria-label={t("main.common.close")}
                ref={ui.part("close")} {...bindingProps(bind("workRecord.close", {}))}>×</button>
      </section>
      {live.error && <p className="read-failure">{t("main.workRecord.unreadable", { error: live.error })}</p>}
      {!d && !live.error && <p className="voice-empty">{t("main.workRecord.reading")}</p>}
      {failed && <p className="read-failure">{t("main.project.answerFailed", { error: failed })}</p>}
      {d && (
        <>
          <section ref={ui.part("outcome")}>
            <h3>{t("main.workRecord.outcome")}</h3>
            <p className="knowledge-text">{d.work.outcome || t("main.workRecord.noOutcome")}</p>
          </section>
          <section ref={ui.part("projects")}>
            <h3>{t("main.workRecord.projects")}</h3>
            <ul className="knowledge-topics">
              {d.projects.map((p) => (
                <li key={p.id}>
                  <button type="button" className="knowledge-open"
                          {...bindingProps(bind("project.open", { projectId: p.id }))}>{p.display_name}</button>
                  {p.role === "primary" && <span className="knowledge-pinned">{t("main.workRecord.primary")}</span>}
                </li>
              ))}
            </ul>
          </section>
          <section ref={ui.part("folders")}>
            <h3>{t("main.workRecord.folders")}</h3>
            {!d.folders.length && <p className="voice-empty">{t("main.workRecord.noFolders")}</p>}
            <ul className="knowledge-topics">
              {d.folders.map((f) => (
                <li key={f.folder_id}>
                  <button type="button" className="knowledge-open" title={f.path ?? undefined}
                          {...bindingProps(bind("folders.open", { folderId: f.folder_id }))}>
                    {f.name ?? t("main.project.folderGone")}</button>
                  <AvailabilityMark availability={f.availability as Availability} />
                </li>
              ))}
            </ul>
          </section>
          <StartAgent id={id} folders={d.run_folders} partRef={ui.part("start")} />
          <section ref={ui.part("runs")}>
            <h3>{t("main.workRecord.runs")}</h3>
            {!d.runs.length && <p className="voice-empty">{t("main.workRecord.noRuns")}</p>}
            <ul className="knowledge-topics">
              {d.runs.map((r) => (
                <li key={r.id}><span className="knowledge-label">{formatDateTime(r.created_at * 1000, { dateStyle: "medium", timeStyle: "short" })}</span>
                  <span className="knowledge-status">{r.status}</span></li>
              ))}
            </ul>
          </section>
          <section ref={ui.part("bound")}>
            <h3>{t("main.workRecord.bound")}</h3>
            {!d.bound.length && <p className="voice-empty">{t("main.workRecord.noBound")}</p>}
            <ul className="knowledge-topics">
              {d.bound.map((b) => (
                <li key={`${b.kind}:${b.id}`}><span className="knowledge-kind">{b.kind}</span>
                  <code>{b.id}</code></li>
              ))}
            </ul>
          </section>
          <section ref={ui.part("context")}>
            <h3>{t("main.workRecord.context")}</h3>
            <p className="knowledge-note">
              {pack.value && !pack.value.enabled ? t("main.workRecord.contextOff") : t("main.workRecord.contextNote")}
            </p>
            {pack.error && <p className="read-failure">{t("main.workRecord.unreadable", { error: pack.error })}</p>}
            {pack.value && (pack.value.text
              ? <pre className="knowledge-text work-record-pack">{pack.value.text}</pre>
              : <p className="voice-empty">{t("main.workRecord.contextEmpty")}</p>)}
          </section>
        </>
      )}
    </div>
  );
}

const STATES = ["open", "done", "dropped"] as const;
type State = typeof STATES[number];
const state = (s: string): State => (STATES as readonly string[]).includes(s) ? s as State : "open";

function StartAgent({ id, folders, partRef }: { id: string; folders: { folder_id: string; name: string | null }[];
                                               partRef: (el: Element | null) => void }) {
  const t = useT();
  const [prompt, setPrompt] = useState("");
  const [folder, setFolder] = useState<string>("");
  const [busy, setBusy] = useState(false);
  const [said, setSaid] = useState<string | null>(null);
  return (
    <section ref={partRef}>
      <h3>{t("main.workRecord.start")}</h3>
      <p className="knowledge-note">{t("main.workRecord.startNote")}</p>
      <form data-ui-action="domain" className="work-record-start" onSubmit={async (e) => {
        e.preventDefault();
        setBusy(true);
        const refused = await startWorkRun(id, prompt, folder || null);
        setBusy(false);
        setSaid(refused === null ? t("main.workRecord.started") : null);
        if (refused === null) setPrompt("");
      }}>
        <select data-ui-action="input" value={folder} onChange={(e) => setFolder(e.target.value)}
                aria-label={t("main.workRecord.startFolder")}>
          <option value="">{t("main.workRecord.startHome")}</option>
          {folders.map((f) => <option key={f.folder_id} value={f.folder_id}>{f.name ?? f.folder_id}</option>)}
        </select>
        <textarea data-ui-action="input" value={prompt} rows={3} onChange={(e) => setPrompt(e.target.value)}
                  placeholder={t("main.workRecord.startPlaceholder")} aria-label={t("main.workRecord.start")} />
        <button data-ui-action="domain" type="submit" disabled={busy || !prompt.trim()}>
          {t("main.workRecord.startButton")}</button>
      </form>
      {said && <p className="knowledge-note" role="status">{said}</p>}
    </section>
  );
}
