/**
 * The Folders views' one source: the folder catalog's routes (/api/folders,
 * jarvis/folders_api.py), read into stores both the centre's Folders view
 * (FoldersView.tsx) and the right panel's Folders tab (FoldersPanel.tsx)
 * show, and the owner's commands on them. Which folder is selected is one
 * store too, so the two views always agree.
 *
 * The catalog and the discovered candidates are read at once, on every
 * "folders" hint and every (re)connect snapshot on /ws/sessions
 * (sessionsSocket.ts), and after every command here. A folder's detail, its
 * directory pages and its file windows are read by the view showing them;
 * a newer request for the same thing aborts the older one, and a page from
 * before a reconnect is refused by the server (stale_cursor) rather than
 * read from the wrong place.
 *
 * The types mirror jarvis/folder_service.py's JSON; the error codes are
 * jarvis/folder_contract.py's ERRORS.
 */

import { liveRead } from "./liveRead";
import { createStore } from "./store";

export type Role = "data" | "source" | "history";
export type Availability = "available" | "missing" | "access_denied" | "unavailable_node" | "unknown";
export type Reader = "files" | "notes" | "claude_history" | "codex_history";

export interface Access {
  reader: Reader;
  description: string;
  permitted_subtree: string;
  policy_version: number;
  enabled: boolean;
}

export interface Folder {
  id: string;
  display_name: string;
  role: Role;
  lifecycle: "active" | "removed";
  revision: number;
  owner_id: string | null;
  created_at: number;
  updated_at: number;
  removed_at: number | null;
  location: {
    id: string;
    node_id: string;
    local: boolean;
    supplied_path: string;
    canonical_path: string;
    revision: number;
    availability: Availability;
    last_checked_at: number | null;
    last_seen_at: number | null;
    error_code: string | null;
  };
  access: Access;
}

export interface Catalog {
  folders: Folder[];
  next_cursor: string | null;
  counts: Partial<Record<Availability, number>>;
  node_id: string;
}

export interface CoverageSource {
  source: string;
  listed: number;
  read: number;
  pending: number;
  complete: boolean;
  last_run_at: number | null;
  last_ok_at: number | null;
  error: string | null;
}

export interface Coverage {
  complete: boolean;
  sources: CoverageSource[];
}

export interface Candidate {
  id: string;
  path: string;
  proposed_role: Role;
  state: "discovered" | "ignored" | "registered";
  providers: string[];
  sessions: number;
  first_seen_at: number;
  last_observed_at: number;
  registered_folder_id: string | null;
  availability: Availability;
  access: Access | null;
  refusal: string | null;
}

export interface Candidates {
  candidates: Candidate[];
  next_cursor: string | null;
  coverage: Coverage;
}

export interface RelatedSession {
  provider: string;
  session_id: string;
  profile_root: string;
  cwd: string;
  history_file: string;
  evidence: string;
  relation: "cwd" | "inside" | "history";
  associated: { memory?: string; subagents?: number; subagent?: boolean };
  first_observed_at: number;
  observed_at: number;
  live: boolean;
  agent_name: string | null;
}

export interface LocationEvent {
  id: number;
  event: string;
  prior: Record<string, unknown>;
  new: Record<string, unknown>;
  at: number;
}

export interface FolderDetail {
  folder: Folder;
  sessions: RelatedSession[];
  events: LocationEvent[];
  coverage: Coverage;
}

export interface Entry {
  name: string;
  path: string;
  kind: "dir" | "file" | "link" | "other";
  size: number | null;
  modified: number | null;
  transcript: boolean;
}

export interface Provenance {
  folder_id: string;
  location_id: string;
  location_revision: number;
  canonical_path: string;
  path: string;
}

export interface Entries extends Provenance {
  reader: Reader;
  breadcrumbs: { name: string; path: string }[];
  entries: Entry[];
  withheld: number;
  scanned: number;
  partial: boolean;
  next_cursor: string | null;
}

export interface Preview extends Provenance {
  reader: Reader;
  kind: "text" | "transcript" | "binary";
  size: number;
  modified: number;
  lines: string[];
  first_line: number | null;
  offset: number;
  next_cursor: string | null;
  partial: boolean;
  clipped_lines: number;
  skipped_records: number;
  sessions: { provider: string; session_id: string | null; transcript: string }[];
}

export interface Resolved {
  supplied_path: string;
  canonical_path: string;
  access: Access;
  proposed_role: Role;
  display_name: string;
  existing: Folder | null;
}

/** A refusal from the catalog, with its stable code and fixed words. */
export class FolderError extends Error {
  constructor(readonly code: string, message: string, readonly extra: Record<string, unknown> = {},
              readonly status = 0) {
    super(message);
  }
}

// ── words ────────────────────────────────────────────────────────────────

export const AVAILABILITY_WORDS: Record<Availability, string> = {
  available: "Available",
  missing: "Missing",
  access_denied: "Access denied",
  unavailable_node: "Unavailable node",
  unknown: "Not checked",
};

export const ROLE_WORDS: Record<Role, string> = { data: "Data", source: "Source", history: "History" };

/** A role's icon: a glyph beside the role's word, never instead of it. */
export const ROLE_ICONS: Record<Role, string> = { data: "▱", source: "⌘", history: "◷" };

export const PROVIDER_WORDS: Record<string, string> = { anthropic: "Claude Code", openai: "Codex" };

// ── reading ──────────────────────────────────────────────────────────────

async function call<T>(method: string, url: string, body?: unknown, signal?: AbortSignal): Promise<T> {
  const res = await fetch(url, {
    method,
    signal,
    headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const payload = await res.json().catch(() => ({}));
  if (!res.ok) {
    const err = payload?.error ?? {};
    throw new FolderError(err.code ?? "unreachable", err.message ?? `${method} ${url}: ${res.status}`,
                          err, res.status);
  }
  return payload as T;
}

function query(params: Record<string, string | number | null | undefined>): string {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== null && v !== undefined && v !== "") q.set(k, String(v));
  const s = q.toString();
  return s ? `?${s}` : "";
}

/** Moves on every "folders" hint on /ws/sessions, and after every command. */
export const foldersChanged = createStore(0);
const changed = () => foldersChanged.set(foldersChanged.get() + 1);

/** The pages to read for the whole catalog before saying it is partial. */
const CATALOG_PAGES = 10;

async function readAll<T, P extends { next_cursor: string | null }>(
  first: string, pick: (page: P) => T[]): Promise<{ items: T[]; complete: boolean; last: P }> {
  let page = await call<P>("GET", first);
  const items = [...pick(page)];
  for (let n = 1; page.next_cursor && n < CATALOG_PAGES; n++) {
    page = await call<P>("GET", `${first}${first.includes("?") ? "&" : "?"}cursor=${encodeURIComponent(page.next_cursor)}`);
    items.push(...pick(page));
  }
  return { items, complete: !page.next_cursor, last: page };
}

export interface CatalogState {
  folders: Folder[];
  removed: Folder[];
  counts: Catalog["counts"];
  nodeId: string;
  complete: boolean;
}

export const catalog = liveRead<CatalogState>(async () => {
  const active = await readAll<Folder, Catalog>("/api/folders", (p) => p.folders);
  const removed = await readAll<Folder, Catalog>("/api/folders?lifecycle=removed", (p) => p.folders);
  return { folders: active.items, removed: removed.items, counts: active.last.counts,
           nodeId: active.last.node_id, complete: active.complete };
}, [foldersChanged]);

export interface CandidateState {
  discovered: Candidate[];
  ignored: Candidate[];
  coverage: Coverage;
  complete: boolean;
}

export const candidates = liveRead<CandidateState>(async () => {
  const found = await readAll<Candidate, Candidates>("/api/folders/candidates", (p) => p.candidates);
  const ignored = await readAll<Candidate, Candidates>("/api/folders/candidates?state=ignored", (p) => p.candidates);
  return { discovered: found.items, ignored: ignored.items, coverage: found.last.coverage,
           complete: found.complete };
}, [foldersChanged]);

export const readDetail = (id: string, signal?: AbortSignal) =>
  call<FolderDetail>("GET", `/api/folders/${encodeURIComponent(id)}`, undefined, signal);

export const readEntries = (id: string, path: string, revision: number, cursor: string | null,
                            signal?: AbortSignal) =>
  call<Entries>("GET", `/api/folders/${encodeURIComponent(id)}/entries${query({ path, location_revision: revision, cursor })}`,
                undefined, signal);

export const readPreview = (id: string, path: string, revision: number, cursor: string | null,
                            signal?: AbortSignal) =>
  call<Preview>("GET", `/api/folders/${encodeURIComponent(id)}/preview${query({ path, location_revision: revision, cursor })}`,
                undefined, signal);

export const resolvePath = (path: string, signal?: AbortSignal) =>
  call<Resolved>("GET", `/api/folders/resolve${query({ path })}`, undefined, signal);

// ── the owner's commands ─────────────────────────────────────────────────

function requestKey(): string {
  return typeof crypto !== "undefined" && "randomUUID" in crypto
    ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

async function command<T>(method: string, url: string, body?: unknown): Promise<T> {
  try {
    return await call<T>(method, url, body);
  } finally {
    changed();
  }
}

export const registerFolder = (path: string, reader: Reader, displayName?: string, reactivate = false) =>
  command<{ folder: Folder; outcome: string }>("POST", "/api/folders", {
    request_key: requestKey(), path, reader, reactivate,
    ...(displayName ? { display_name: displayName } : {}),
  });

export const editFolder = (f: Folder, changes: { display_name?: string; role?: Role }) =>
  command<{ folder: Folder }>("PATCH", `/api/folders/${encodeURIComponent(f.id)}`,
                              { expected_revision: f.revision, ...changes });

export const unregisterFolder = (f: Folder) =>
  command<{ folder: Folder }>("DELETE", `/api/folders/${encodeURIComponent(f.id)}${query({ expected_revision: f.revision })}`);

export const refreshFolder = (f: Folder) =>
  command<FolderDetail>("POST", `/api/folders/${encodeURIComponent(f.id)}/refresh`);

export const reconnectFolder = (f: Folder, path: string, replace: boolean) =>
  command<{ folder: Folder }>("POST", `/api/folders/${encodeURIComponent(f.id)}/reconnect`,
                              { expected_revision: f.revision, path, replace });

export const candidateAction = (c: Candidate, action: "ignore" | "restore") =>
  command<unknown>("POST", `/api/folders/candidates/${encodeURIComponent(c.id)}`, { action });

// ── what is selected ─────────────────────────────────────────────────────

/** The selected folder, the same in the centre and the right panel. */
export const selectedFolder = createStore<string | null>(null);

/** Moves when something asks the centre to show the Folders view. */
export const showFolders = createStore(0);

/** Select a folder (or none) and show it in the centre. */
export function openFolder(id: string | null): void {
  selectedFolder.set(id);
  showFolders.set(showFolders.get() + 1);
}

/** A folder or candidate matches a search when its name or path holds it. */
export function matches(text: string, ...fields: string[]): boolean {
  const q = text.trim().toLowerCase();
  return !q || fields.some((f) => f.toLowerCase().includes(q));
}

/** "This Mac", or another node's ID, shortened. */
export function nodeWords(f: Folder): string {
  return f.location.local ? "This Mac" : `Node ${f.location.node_id.slice(0, 8)}`;
}

/** A size in bytes, as a person reads it. */
export function sizeWords(bytes: number | null): string {
  if (bytes === null) return "";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(bytes < 10 * 1024 ? 1 : 0)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}
