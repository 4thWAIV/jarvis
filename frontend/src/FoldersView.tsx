/**
 * The centre area's Folders view: the folder catalog on the left half, the
 * selected folder in full on the right (FolderDetail.tsx).
 *
 * Above the list: a search by name or path, Registered and Discovered as
 * two tabs, role and availability filters, and Add existing folder, which
 * checks a path with the server and shows where it resolves and how it
 * would be read before anything is saved. A registered row shows the role's
 * icon and word, the name, the node, the availability in words and when it
 * was last checked; a discovered row shows the path, which sessions named
 * it and Register and Ignore. Discovered folders are never mixed into the
 * catalog, and seeing one grants nothing. No project counts: projects come
 * in a later release.
 *
 * Everything comes from folders.ts, the same store the right panel's
 * Folders tab reads, and the selection is shared with it. The view stays
 * mounted while another centre view is shown (CentreArea.tsx), so its
 * filters, its selection and its scroll are where they were left.
 */

import { useState, useSyncExternalStore, type FormEvent } from "react";
import { FolderDetail } from "./FolderDetail";
import { AvailabilityMark, RoleMark } from "./FolderMarks";
import {
  AVAILABILITY_WORDS, candidateAction, candidates, catalog, FolderError, matches, nodeWords,
  PROVIDER_WORDS, registerFolder, resolvePath, ROLE_WORDS, selectedFolder,
  type Availability, type Candidate, type Folder, type Resolved, type Role,
} from "./folders";
import { shortAgo } from "./timeFormat";
import { useTick } from "./WorkflowsView";

type ListView = "registered" | "discovered";

function ago(at: number | null): string {
  return at === null ? "never" : shortAgo(Date.now() - at * 1000, 10_000);
}

function CatalogRow({ f, selected }: { f: Folder; selected: boolean }) {
  return (
    <li>
      <button type="button" className={`folder-row${selected ? " selected" : ""}`}
              aria-current={selected ? "true" : undefined}
              onClick={() => selectedFolder.set(f.id)}>
        <RoleMark role={f.role} />
        <span className="folder-row-name" title={f.location.canonical_path}>{f.display_name}</span>
        <span className="folder-row-node">{nodeWords(f)}</span>
        <AvailabilityMark availability={f.location.availability} />
        <span className="folder-row-seen">checked {ago(f.location.last_checked_at)}</span>
      </button>
    </li>
  );
}

function CandidateRow({ c }: { c: Candidate }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const act = async (work: () => Promise<unknown>) => {
    setBusy(true);
    setError(null);
    try {
      await work();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const who = c.providers.map((p) => PROVIDER_WORDS[p] ?? p).join(", ");
  return (
    <li className="candidate-row">
      <div className="candidate-head">
        <RoleMark role={c.proposed_role} />
        <span className="candidate-path" title={c.path}>{c.path}</span>
      </div>
      <div className="candidate-meta">
        <AvailabilityMark availability={c.availability} />
        <span>{c.sessions} session{c.sessions === 1 ? "" : "s"}{who ? ` · ${who}` : ""}</span>
        <span>seen {ago(c.last_observed_at)}</span>
      </div>
      {c.refusal && <p className="folder-note">JARVIS does not read this location, so it cannot be registered.</p>}
      {c.access && !c.refusal && <p className="folder-note">Would be read as: {c.access.description}.</p>}
      <div className="candidate-actions">
        {c.state === "discovered" && (
          <>
            <button type="button" className="folder-button" disabled={busy || !!c.refusal || c.availability !== "available"}
                    onClick={() => act(async () => {
                      const done = await registerFolder(c.path, c.access!.reader);
                      selectedFolder.set(done.folder.id);
                    })}>
              Register
            </button>
            <button type="button" className="folder-button quiet" disabled={busy}
                    onClick={() => act(() => candidateAction(c, "ignore"))}>Ignore</button>
          </>
        )}
        {c.state === "ignored" && (
          <button type="button" className="folder-button quiet" disabled={busy}
                  onClick={() => act(() => candidateAction(c, "restore"))}>Restore</button>
        )}
      </div>
      {error && <p className="folder-error" role="alert">{error}</p>}
    </li>
  );
}

/** Add existing folder: a path, checked with the server first, then saved
 *  as shown, or refused with the server's own words. */
function AddFolder({ onDone }: { onDone: () => void }) {
  const [path, setPath] = useState("");
  const [name, setName] = useState("");
  const [checked, setChecked] = useState<Resolved | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const check = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    setChecked(null);
    try {
      const found = await resolvePath(path.trim());
      setChecked(found);
      setName(found.existing?.display_name ?? found.display_name);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const save = async () => {
    if (!checked) return;
    setBusy(true);
    setError(null);
    try {
      const removed = checked.existing?.lifecycle === "removed";
      const done = await registerFolder(checked.supplied_path, checked.access.reader,
                                        name.trim() || undefined, removed);
      selectedFolder.set(done.folder.id);
      onDone();
    } catch (err) {
      const e = err as FolderError;
      setError(e.code === "reader_changed" ? "How this location is read changed; check it again." : e.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <form className="folder-add" onSubmit={check} aria-label="Add existing folder">
      <label className="folder-field">
        <span>Folder path (absolute, or starting with ~/)</span>
        <input type="text" value={path} spellCheck={false} autoFocus
               onChange={(e) => { setPath(e.target.value); setChecked(null); }} />
      </label>
      <div className="folder-add-actions">
        <button type="submit" className="folder-button" disabled={busy || !path.trim()}>Check</button>
        <button type="button" className="folder-button quiet" onClick={onDone}>Cancel</button>
      </div>
      {checked && (
        <div className="folder-add-checked" role="status">
          <dl className="folder-facts">
            <dt>Resolves to</dt><dd className="folder-path">{checked.canonical_path}</dd>
            <dt>Read as</dt><dd>{checked.access.description}</dd>
            <dt>Role</dt><dd><RoleMark role={checked.proposed_role} /></dd>
          </dl>
          {checked.existing?.lifecycle === "active" && (
            <p className="folder-note">Already registered as “{checked.existing.display_name}”.</p>
          )}
          {checked.existing?.lifecycle === "removed" && (
            <p className="folder-note">This location was removed from the catalog before; saving
              registers that folder again with its old ID.</p>
          )}
          {checked.existing?.lifecycle !== "active" && (
            <>
              <label className="folder-field">
                <span>Name</span>
                <input type="text" value={name} onChange={(e) => setName(e.target.value)} />
              </label>
              <p className="folder-note">Registering records the folder and lets JARVIS read it as shown.
                No file is created, moved or changed.</p>
              <button type="button" className="folder-button" disabled={busy} onClick={save}>
                {checked.existing ? "Register again" : "Register"}
              </button>
            </>
          )}
        </div>
      )}
      {error && <p className="folder-error" role="alert">{error}</p>}
    </form>
  );
}

const ROLES: Role[] = ["data", "source", "history"];
const AVAILABILITIES: Availability[] = ["available", "missing", "access_denied", "unavailable_node"];

export function FoldersView() {
  useTick(30000);
  const live = useSyncExternalStore(catalog.subscribe, catalog.get);
  const found = useSyncExternalStore(candidates.subscribe, candidates.get);
  const selected = useSyncExternalStore(selectedFolder.subscribe, selectedFolder.get);
  const [view, setView] = useState<ListView>("registered");
  const [search, setSearch] = useState("");
  const [role, setRole] = useState<Role | "">("");
  const [availability, setAvailability] = useState<Availability | "">("");
  const [showIgnored, setShowIgnored] = useState(false);
  const [showRemoved, setShowRemoved] = useState(false);
  const [adding, setAdding] = useState(false);

  const all = live.value ? [...live.value.folders, ...(showRemoved ? live.value.removed : [])] : [];
  const folders = all.filter((f) => matches(search, f.display_name, f.location.canonical_path)
                                    && (!role || f.role === role)
                                    && (!availability || f.location.availability === availability));
  const discovered = found.value
    ? [...found.value.discovered, ...(showIgnored ? found.value.ignored : [])]
        .filter((c) => matches(search, c.path) && (!role || c.proposed_role === role)
                       && (!availability || c.availability === availability))
    : [];
  const pending = found.value?.coverage.sources.reduce((n, s) => n + s.pending, 0) ?? 0;
  const failedSource = found.value?.coverage.sources.find((s) => s.error);

  return (
    <div id="folders-view" className="folders-view">
      <section className="folders-catalog" aria-label="Folder catalog">
        <div className="folders-toolbar">
          <label className="folder-field folder-search">
            <span className="visually-hidden">Search folders by name or path</span>
            <input type="search" placeholder="Search name or path" value={search}
                   onChange={(e) => setSearch(e.target.value)} />
          </label>
          <button type="button" className="folder-button" aria-expanded={adding}
                  onClick={() => setAdding((a) => !a)}>Add existing folder</button>
        </div>
        <div className="folders-tabs" role="tablist" aria-label="Which folders">
          {(["registered", "discovered"] as ListView[]).map((v) => (
            <button key={v} type="button" role="tab" aria-selected={view === v}
                    className={`folder-tab${view === v ? " selected" : ""}`} onClick={() => setView(v)}>
              {v === "registered" ? `Registered (${live.value?.folders.length ?? 0})`
                                  : `Discovered (${found.value?.discovered.length ?? 0})`}
            </button>
          ))}
        </div>
        <div className="folders-filters">
          <label className="folder-field">
            <span>Role</span>
            <select value={role} onChange={(e) => setRole(e.target.value as Role | "")}>
              <option value="">All roles</option>
              {ROLES.map((r) => <option key={r} value={r}>{ROLE_WORDS[r]}</option>)}
            </select>
          </label>
          <label className="folder-field">
            <span>Availability</span>
            <select value={availability} onChange={(e) => setAvailability(e.target.value as Availability | "")}>
              <option value="">Any</option>
              {AVAILABILITIES.map((a) => <option key={a} value={a}>{AVAILABILITY_WORDS[a]}</option>)}
            </select>
          </label>
          {view === "registered" ? (
            <label className="folder-check">
              <input type="checkbox" checked={showRemoved} onChange={(e) => setShowRemoved(e.target.checked)} />
              Show removed
            </label>
          ) : (
            <label className="folder-check">
              <input type="checkbox" checked={showIgnored} onChange={(e) => setShowIgnored(e.target.checked)} />
              Show ignored
            </label>
          )}
        </div>
        {adding && <AddFolder onDone={() => { setAdding(false); setView("registered"); }} />}
        <div className="folders-list" role="tabpanel" aria-label={view === "registered" ? "Registered" : "Discovered"}>
          {view === "registered" && (
            <>
              {!live.value && <p className="folder-note" role="status">{live.error ? `Couldn't read the catalog (${live.error}); trying again.` : "Reading the catalog…"}</p>}
              {live.value && !live.value.folders.length && !showRemoved && (
                <p className="folder-note">No folders registered yet. Add an existing folder, or register one JARVIS discovered.</p>
              )}
              {live.value && all.length > 0 && !folders.length && <p className="folder-note" role="status">No folders match these filters.</p>}
              {live.value && !live.value.complete && <p className="folder-note">Showing the first {live.value.folders.length} folders.</p>}
              <ul className="folder-rows">
                {folders.map((f) => <CatalogRow key={f.id} f={f} selected={f.id === selected} />)}
              </ul>
            </>
          )}
          {view === "discovered" && (
            <>
              <p className="folder-note">Found in Claude Code and Codex sessions' own records. Nothing here
                is readable until you register it.</p>
              {!found.value && <p className="folder-note" role="status">{found.error ? `Couldn't read what discovery found (${found.error}); trying again.` : "Reading what discovery found…"}</p>}
              {found.value && (
                <p className="folder-note" role="status">
                  {failedSource ? `Discovery could not read ${failedSource.source} (${failedSource.error}). `
                    : pending ? `Discovery is partial: ${pending} history files still to read. `
                    : found.value.coverage.sources.length ? "Discovery is up to date. " : "Discovery has not run yet. "}
                </p>
              )}
              {found.value && !discovered.length && <p className="folder-note">Nothing discovered matches.</p>}
              <ul className="folder-rows">
                {discovered.map((c) => <CandidateRow key={c.id} c={c} />)}
              </ul>
            </>
          )}
        </div>
      </section>
      <section className="folders-detail" aria-label="Selected folder">
        {selected ? <FolderDetail key={selected} id={selected} />
                  : <p className="folder-note folder-empty">Select a folder to see its location, contents and sessions.</p>}
      </section>
    </div>
  );
}
