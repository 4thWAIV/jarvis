/**
 * One of his projects in the centre area (projects.ts): its summary, the
 * registered folders it is made of (each opening in Folders), its open
 * pieces of work, its accomplishments (a proposed one waiting for his
 * Confirm or Withdraw), the topics it touches and the agents working in its
 * folders now. Opened by `project.open` from the Folders tab's Projects or
 * a Work row, or by the brain; every read is the same call the brain's
 * project tools make (project_service.py).
 *
 * Folder and agent names and proposed titles are shown as text only.
 *
 * Registered for pointing at: the view (header, close, summary, folders,
 * works, accomplishments, topics, agents) and each accomplishment (row,
 * confirm, withdraw).
 */

import { useSyncExternalStore } from "react";
import { CENTRE_SOCKET } from "./ConversationView";
import { AvailabilityMark } from "./FolderMarks";
import type { Availability } from "./folders";
import { useT } from "./locale";
import { useLiveRead } from "./liveRead";
import { answerError, getProject, projectsChanged, type Accomplishment } from "./projects";
import { bind, bindingProps } from "./uiNavigation";
import { useUiTarget } from "./uiRegistry";

export function ProjectView({ id }: { id: string }) {
  const t = useT();
  const live = useLiveRead(() => getProject(id), [projectsChanged]);
  const failed = useSyncExternalStore(answerError.subscribe, answerError.get);
  const d = live.value;
  const ui = useUiTarget({ ref: { region: "main", view: "main.project", kind: "project", key: id },
                           label: d?.project.display_name ?? t("tabs.main.project"), socket: CENTRE_SOCKET });
  return (
    <div id="project-view" ref={ui.whole}>
      <section className="subsystem-head" ref={ui.part("header")}>
        <div className="subsystem-head-words">
          <h2>{d?.project.display_name ?? t("tabs.main.project")}</h2>
          {d && <p className="subsystem-head-state">{t(`main.project.lifecycle.${lifecycle(d.project.lifecycle)}`)}</p>}
        </div>
        <button type="button" className="workflow-close" title={t("main.common.close")} aria-label={t("main.common.close")}
                ref={ui.part("close")} {...bindingProps(bind("project.close", {}))}>×</button>
      </section>
      {live.error && <p className="read-failure">{t("main.project.unreadable", { error: live.error })}</p>}
      {!d && !live.error && <p className="voice-empty">{t("main.project.reading")}</p>}
      {failed && <p className="read-failure">{t("main.project.answerFailed", { error: failed })}</p>}
      {d && (
        <>
          <section ref={ui.part("summary")}>
            <h3>{t("main.project.summary")}</h3>
            <p className="knowledge-text">{d.project.summary || t("main.project.noSummary")}</p>
          </section>
          <section ref={ui.part("folders")}>
            <h3>{t("main.project.folders")}</h3>
            {!d.folders.length && <p className="voice-empty">{t("main.project.noFolders")}</p>}
            <ul className="knowledge-topics">
              {d.folders.map((f) => (
                <li key={f.folder_id}>
                  <button type="button" className="knowledge-open" title={f.path ?? undefined}
                          {...bindingProps(bind("folders.open", { folderId: f.folder_id }))}>
                    {f.name ?? t("main.project.folderGone")}</button>
                  {f.is_home && <span className="knowledge-pinned">{t("main.project.home")}</span>}
                  <AvailabilityMark availability={f.availability as Availability} />
                </li>
              ))}
            </ul>
          </section>
          <section ref={ui.part("works")}>
            <h3>{t("main.project.works")}</h3>
            {!d.works.length && <p className="voice-empty">{t("main.project.noWorks")}</p>}
            <ul className="knowledge-topics">
              {d.works.map((w) => (
                <li key={w.id}><span className="knowledge-label">{w.title}</span>
                  {w.outcome && <span className="knowledge-note">{w.outcome}</span>}</li>
              ))}
            </ul>
          </section>
          <section ref={ui.part("accomplishments")}>
            <h3>{t("main.project.accomplishments")}</h3>
            <p className="knowledge-note">{t("main.project.accomplishmentsNote")}</p>
            {!d.accomplishments.length && <p className="voice-empty">{t("main.project.noAccomplishments")}</p>}
            <ul className="knowledge-proposals">
              {d.accomplishments.map((a) => <AccomplishmentRow key={a.id} a={a} />)}
            </ul>
          </section>
          <section ref={ui.part("topics")}>
            <h3>{t("main.project.topics")}</h3>
            {!d.topics.length && <p className="voice-empty">{t("main.project.noTopics")}</p>}
            <ul className="knowledge-topics">
              {d.topics.map((x) => <li key={x.slug}><span className="knowledge-label">{x.name}</span></li>)}
            </ul>
          </section>
          <section ref={ui.part("agents")}>
            <h3>{t("main.project.agents")}</h3>
            {!d.agents.length && <p className="voice-empty">{t("main.project.noAgents")}</p>}
            <ul className="knowledge-topics">
              {d.agents.map((a) => (
                <li key={a.session_id}>
                  <button type="button" className="knowledge-open"
                          {...bindingProps(bind("thread.open", { sessionId: a.session_id }))}>
                    {a.name ?? a.session_id}</button>
                </li>
              ))}
            </ul>
          </section>
        </>
      )}
    </div>
  );
}

const LIFECYCLES = ["active", "paused", "done", "archived"] as const;
type Lifecycle = typeof LIFECYCLES[number];
const lifecycle = (l: string): Lifecycle => (LIFECYCLES as readonly string[]).includes(l) ? l as Lifecycle : "active";

function AccomplishmentRow({ a }: { a: Accomplishment }) {
  const t = useT();
  const ui = useUiTarget({ ref: { region: "main", view: "main.project", kind: "accomplishment", key: a.id },
                           label: a.title, socket: CENTRE_SOCKET });
  const proposed = a.state === "proposed";
  return (
    <li className="knowledge-proposal" data-accomplishment={a.id} data-state={a.state}
        ref={(el) => { ui.whole(el); ui.part("row")(el); }}>
      <span className="knowledge-proposal-words">
        <span className="knowledge-label">{a.title}</span>
        {proposed && <span className="knowledge-note"> · {t("main.project.proposed")}</span>}
      </span>
      {proposed && (
        <>
          <button type="button" ref={ui.part("confirm")}
                  {...bindingProps(bind("project.accomplishment.answer", { accomplishmentId: a.id, answer: "confirm" }))}>
            {t("main.project.confirm")}</button>
          <button type="button" ref={ui.part("withdraw")}
                  {...bindingProps(bind("project.accomplishment.answer", { accomplishmentId: a.id, answer: "withdraw" }))}>
            {t("main.project.withdraw")}</button>
        </>
      )}
    </li>
  );
}
