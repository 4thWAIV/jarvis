/**
 * The page's side of his project records (project_store.py): one store for
 * the Folders tab's Projects list and the centre's Project view
 * (ProjectView.tsx), read from /api/project-records — the same calls the
 * brain's project tools run (project_service.py) — again on every
 * "projects" hint on /ws/sessions.
 *
 * A project opens in the centre's `project` detail slot through
 * `project.open` and closes through `project.close`, a click and the
 * brain's request alike. Answering a proposed project
 * (`project.candidate.answer`) or a proposed accomplishment
 * (`project.accomplishment.answer`) is his own hand's: the brain answers
 * them with its acting tools, on his word.
 */

import { getJson } from "./fetchJson";
import { liveRead } from "./liveRead";
import { createStore } from "./store";
import { detailSlot, readForOpen, registerClose } from "./uiNavigation";
import { registerCommand } from "./uiRegistry";

export interface ProjectRecord {
  id: string;
  display_name: string;
  summary: string;
  lifecycle: string;
  revision: number;
  created_at: number;
  updated_at: number;
  archived_at: number | null;
}

export interface FolderFacts {
  folder_id: string;
  name: string | null;
  availability: string;
  path?: string | null;
  part?: string;
  is_home?: boolean;
}

export interface ProjectRow extends ProjectRecord {
  folders: number;
  home: FolderFacts | null;
  open_works: number;
}

export interface Work { id: string; title: string; outcome: string; state: string; updated_at: number }

export interface Accomplishment {
  id: string;
  project_id: string;
  title: string;
  detail: string;
  state: "proposed" | "confirmed" | "withdrawn" | string;
  achieved_at: number;
}

export interface ProjectDetail {
  project: ProjectRecord;
  folders: FolderFacts[];
  works: Work[];
  accomplishments: Accomplishment[];
  runs: { id: string; status: string; created_at: number }[];
  topics: { slug: string; name: string; status: string }[];
  agents: { session_id: string; name: string | null }[];
}

export interface Candidate {
  id: string;
  proposed_name: string;
  state: string;
  evidence: {
    folders: { folder_id: string; label: string; path: string | null }[];
    legacy: { source: string; key: string; count: number }[];
  };
}

/** Moves on every "projects" hint on /ws/sessions, and on every answer here. */
export const projectsChanged = createStore(0);
const changed = () => projectsChanged.set(projectsChanged.get() + 1);

export const projectList = liveRead(
  () => getJson<{ projects: ProjectRow[] }>("/api/project-records").then((b) => b.projects), [projectsChanged]);
export const projectCandidates = liveRead(
  () => getJson<{ candidates: Candidate[] }>("/api/project-records/candidates").then((b) => b.candidates),
  [projectsChanged]);

export const getProject = (id: string) =>
  getJson<ProjectDetail>(`/api/project-records/${encodeURIComponent(id)}`);

/** The last answer he gave here that was refused, in the server's words. */
export const answerError = createStore<string | null>(null);

async function send(path: string, body: unknown): Promise<string | null> {
  try {
    const res = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" },
                                    body: JSON.stringify(body) });
    if (!res.ok) {
      const said = (await res.json().catch(() => ({}))) as { error?: { message?: string } };
      throw new Error(said.error?.message ?? `${path}: ${res.status}`);
    }
    answerError.set(null);
    changed();
    return null;
  } catch (e) {
    answerError.set((e as Error).message);
    return (e as Error).message;
  }
}

// ── the registered commands ─────────────────────────────────────────────

const projectSlot = detailSlot<string>("project");
/** The project open in the centre, or null. Read-only: controls run the commands. */
export const openProject = projectSlot.value;

registerCommand("project.open", {
  availability: () => ({ available: true }),
  async execute({ projectId }, { signal }) {
    if (!(projectList.get().value ?? []).some((p) => p.id === projectId)) {
      const read = await readForOpen(`/api/project-records/${encodeURIComponent(projectId)}`, signal);
      if ("refused" in read) return read.refused;
    }
    return { status: "completed", effects: projectSlot.show(projectId) };
  },
});

registerClose("project.close", projectSlot);

registerCommand("project.candidate.answer", {
  availability: ({ candidateId }) =>
    (projectCandidates.get().value ?? []).some((c) => c.id === candidateId)
      ? { available: true } : { available: false, reason: "no-entity" },
  async execute({ candidateId, answer }) {
    const refused = await send(`/api/project-records/candidates/${encodeURIComponent(candidateId)}`,
                               { action: answer });
    return refused === null ? { status: "completed", effects: [] }
      : { status: "unavailable", effects: [], reason: refused };
  },
});

registerCommand("project.accomplishment.answer", {
  availability: () => (projectSlot.value.get() !== null ? { available: true }
                                                         : { available: false, reason: "no-entity" }),
  async execute({ accomplishmentId, answer }) {
    const project = projectSlot.value.get();
    if (project === null) return { status: "unavailable", effects: [], reason: "no-entity" };
    const refused = await send(`/api/project-records/${encodeURIComponent(project)}/accomplishments`,
                               { action: answer, accomplishment: accomplishmentId });
    return refused === null ? { status: "completed", effects: [] }
      : { status: "unavailable", effects: [], reason: refused };
  },
});
