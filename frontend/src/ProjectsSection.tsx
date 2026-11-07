/**
 * The Folders tab's Projects: his projects, each opening in the centre
 * (`project.open`), and under them the projects JARVIS proposes from his
 * registered folders and the project names it already holds, each waiting
 * for his Accept or Ignore (`project.candidate.answer`). A proposal is only
 * ever made a project by his own hand here, or his word to the brain.
 *
 * Registered for pointing at: the list (`projectsList`: list, candidates),
 * each project (`project`: row) and each proposal (`projectCandidate`:
 * row, accept, ignore).
 */

import { useSyncExternalStore } from "react";
import { useT } from "./locale";
import { answerError, openProject, projectCandidates, projectList, type Candidate, type ProjectRow } from "./projects";
import { RIGHT_SOCKET, row } from "./uiRight";
import { bind, bindingProps } from "./uiNavigation";
import { useUiTarget } from "./uiRegistry";

export function ProjectsSection() {
  const t = useT();
  const live = useSyncExternalStore(projectList.subscribe, projectList.get);
  const found = useSyncExternalStore(projectCandidates.subscribe, projectCandidates.get);
  const failed = useSyncExternalStore(answerError.subscribe, answerError.get);
  const open = useSyncExternalStore(openProject.subscribe, openProject.get);
  const ui = useUiTarget({ ref: { region: "right", view: "right.folders", kind: "projectsList" },
                           label: t("right.projects.title"), socket: RIGHT_SOCKET });
  const list = live.value ?? [];
  const proposed = found.value ?? [];
  return (
    <section className="projects-section" aria-label={t("right.projects.title")} ref={ui.whole}>
      <div className="folders-panel-heading"><span>{t("right.projects.title")}</span></div>
      {live.error && <p className="folder-note" role="status">{t("right.projects.readFailed", { error: live.error })}</p>}
      {live.value && !list.length && <p className="folder-note">{t("right.projects.none")}</p>}
      <ul className="folders-panel-list" aria-label={t("right.projects.title")} ref={ui.part("list")}>
        {list.map((p) => <ProjectItem key={p.id} p={p} selected={p.id === open} />)}
      </ul>
      {proposed.length > 0 && (
        <div ref={ui.part("candidates")}>
          <p className="folder-note">{t("right.projects.proposed", { count: proposed.length })}</p>
          {failed && <p className="folder-note" role="status">{t("right.projects.answerFailed", { error: failed })}</p>}
          <ul className="folders-panel-list">
            {proposed.map((c) => <CandidateItem key={c.id} c={c} />)}
          </ul>
        </div>
      )}
    </section>
  );
}

function ProjectItem({ p, selected }: { p: ProjectRow; selected: boolean }) {
  const t = useT();
  const ui = useUiTarget({ ref: { region: "right", view: "right.folders", kind: "project", key: p.id },
                           label: p.display_name, socket: RIGHT_SOCKET });
  return (
    <li>
      <button type="button" className={`folders-panel-row${selected ? " selected" : ""}`}
              aria-current={selected ? "true" : undefined} ref={row(ui)}
              {...bindingProps(bind("project.open", { projectId: p.id }))}>
        <span className="folders-panel-name">{p.display_name}</span>
        <span className="folders-panel-meta">
          <span>{t("right.projects.folders", { count: p.folders })}</span>
          {p.open_works > 0 && <span>{t("right.projects.works", { count: p.open_works })}</span>}
        </span>
      </button>
    </li>
  );
}

function CandidateItem({ c }: { c: Candidate }) {
  const t = useT();
  const ui = useUiTarget({ ref: { region: "right", view: "right.folders", kind: "projectCandidate", key: c.id },
                           label: c.proposed_name, socket: RIGHT_SOCKET });
  const folders = c.evidence.folders.map((f) => f.label).join(", ");
  return (
    <li className="project-candidate" data-candidate={c.id} ref={(el) => { ui.whole(el); ui.part("row")(el); }}>
      <span className="folders-panel-name">{c.proposed_name}</span>
      <span className="folders-panel-meta">
        {folders ? t("right.projects.fromFolders", { folders }) : t("right.projects.fromNames")}
      </span>
      <span className="project-candidate-answers">
        <button type="button" className="folder-button quiet" ref={ui.part("accept")}
                {...bindingProps(bind("project.candidate.answer", { candidateId: c.id, answer: "accept" }))}>
          {t("right.projects.accept")}</button>
        <button type="button" className="folder-button quiet" ref={ui.part("ignore")}
                {...bindingProps(bind("project.candidate.answer", { candidateId: c.id, answer: "ignore" }))}>
          {t("right.projects.ignore")}</button>
      </span>
    </li>
  );
}
