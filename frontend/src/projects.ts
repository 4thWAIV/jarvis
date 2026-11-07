/**
 * The page's side of his project records (project_store.py): one store for
 * the Folders tab's Projects list and the centre's Project view
 * (ProjectView.tsx), read from /api/project-records — the same calls the
 * brain's project tools run (project_service.py) — again on every
 * "projects" hint on /ws/sessions.
 *
 * A project opens in the centre's `project` detail slot through
 * `project.open` and closes through `project.close`, and a piece of work
 * in the `workRecord` slot through `workRecord.open` / `workRecord.close`
 * (WorkView.tsx), a click and the brain's request alike. Starting an agent
 * for a piece of work is his click (`startWorkRun`); the brain's is
 * spawn_run with its work_id, placed by the same project service. Answering a proposed project
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
  /** Agents working in its folders now. */
  agents_now: number;
  /** Accomplishments proposed and waiting for his Confirm. */
  to_confirm: number;
}

export interface ProjectListing {
  projects: ProjectRow[];
  /** Registered folders that are in a project; the rest are independent. */
  linked_folders: string[];
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

export interface WorkDetail {
  work: Work & { projects: { project_id: string; role: string }[]; folders: string[] };
  projects: (ProjectRecord & { role: string })[];
  folders: FolderFacts[];
  /** Where an agent may start for it: its projects' folders and its own. */
  run_folders: FolderFacts[];
  runs: { id: string; status: string; created_at: number; folder_id: string | null }[];
  bound: { kind: string; id: string; how: string; bound_at: number }[];
}

export interface RunContext { text: string; tier: number; partial: boolean; sources: string[]; enabled: boolean }

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

export const projectList = liveRead(() => getJson<ProjectListing>("/api/project-records"), [projectsChanged]);
export const projectCandidates = liveRead(
  () => getJson<{ candidates: Candidate[] }>("/api/project-records/candidates").then((b) => b.candidates),
  [projectsChanged]);

export const getProject = (id: string) =>
  getJson<ProjectDetail>(`/api/project-records/${encodeURIComponent(id)}`);

export const getWork = (id: string) => getJson<WorkDetail>(`/api/works/${encodeURIComponent(id)}`);
export const getWorkContext = (id: string) =>
  getJson<RunContext>(`/api/works/${encodeURIComponent(id)}/context`);

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

/** Start an agent for a piece of work, in one of its folders: his click
 *  (the brain's is spawn_run with work_id). Resolves with the refusal's
 *  words, or null once started. */
export async function startWorkRun(workId: string, prompt: string, folderId: string | null): Promise<string | null> {
  return send(`/api/works/${encodeURIComponent(workId)}/runs`, { prompt, folder_id: folderId });
}

// ── the registered commands ─────────────────────────────────────────────

const projectSlot = detailSlot<string>("project");
/** The project open in the centre, or null. Read-only: controls run the commands. */
export const openProject = projectSlot.value;

registerCommand("project.open", {
  availability: () => ({ available: true }),
  async execute({ projectId }, { signal }) {
    if (!(projectList.get().value?.projects ?? []).some((p) => p.id === projectId)) {
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

const workRecordSlot = detailSlot<string>("workRecord");
/** The piece of work open in the centre, or null. */
export const openWorkRecord = workRecordSlot.value;

registerCommand("workRecord.open", {
  availability: () => ({ available: true }),
  async execute({ workRecordId }, { signal }) {
    const read = await readForOpen(`/api/works/${encodeURIComponent(workRecordId)}`, signal);
    if ("refused" in read) return read.refused;
    return { status: "completed", effects: workRecordSlot.show(workRecordId) };
  },
});

registerClose("workRecord.close", workRecordSlot);

/** How the Projects and folders tab shows: projects (with the independent
 *  folders and what waits for him) or the plain folder list. Changed only
 *  by `folders.panel.show`, its switch and the brain alike. */
export type PanelView = "projects" | "folders";
export const panelView = createStore<PanelView>("projects");

registerCommand("folders.panel.show", {
  availability: () => ({ available: true }),
  execute({ view }) { panelView.set(view); return { status: "completed", effects: ["list.filtered"] }; },
});
