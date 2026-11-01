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
import { CENTRE_SOCKET } from "./ConversationView";
import { FolderDetail } from "./FolderDetail";
import { AvailabilityMark, RoleMark } from "./FolderMarks";
import {
  AVAILABILITY_WORDS, candidateAction, candidates, catalog, FolderError, matches, nodeWords,
  PROVIDER_WORDS, registerFolder, resolvePath, ROLE_WORDS, selectedFolder,
  type Availability, type Candidate, type Folder, type Resolved, type Role,
} from "./folders";
import { createStore } from "./store";
import { shortAgo } from "./timeFormat";
import type { CommandArgs } from "./uiContract";
import { bind, bindingProps, runBinding } from "./uiNavigation";
import { registerCommand, useUiTarget } from "./uiRegistry";
import { useTick } from "./WorkflowsView";

type ListView = CommandArgs["folders.list"]["view"];

// ── what the catalog list shows ─────────────────────────────────────────
//
// Registered or Discovered, the search, the role and availability filters,
// whether removed and ignored folders show, and whether Add existing
// folder is open: kept here (so they stay as left while another view is
// shown) and changed only by the folders.list, folders.search,
// folders.filter and folders.add.open commands, a click and the brain
// alike. None of them reads, saves or changes a folder.

interface ListState {
  view: ListView;
  search: string;
  role: Role | "";
  availability: Availability | "";
  removed: boolean;
  ignored: boolean;
  adding: boolean;
}

const listState = createStore<ListState>({
  view: "registered", search: "", role: "", availability: "", removed: false, ignored: false, adding: false,
});

const always = () => ({ available: true });
const setList = (change: Partial<ListState>) => listState.set({ ...listState.get(), ...change });

registerCommand("folders.list", {
  availability: always,
  execute({ view }) { setList({ view }); return { status: "completed", effects: ["list.filtered"] }; },
});

registerCommand("folders.search", {
  availability: always,
  execute({ text }) { setList({ search: text ?? "" }); return { status: "completed", effects: ["list.filtered"] }; },
});

registerCommand("folders.filter", {
  availability: always,
  execute({ role, availability, removed, ignored }) {
    setList({
      ...(role === undefined ? {} : { role: role === "any" ? "" : role }),
      ...(availability === undefined ? {} : { availability: availability === "any" ? "" : availability }),
      ...(removed === undefined ? {} : { removed }),
      ...(ignored === undefined ? {} : { ignored }),
    });
    return { status: "completed", effects: ["list.filtered"] };
  },
});

registerCommand("folders.add.open", {
  availability: always,
  execute({ open }) { setList({ adding: open }); return { status: "completed", effects: [] }; },
});

/** A filter change, as a person's control runs it. */
const filter = (args: CommandArgs["folders.filter"]) => runBinding(bind("folders.filter", args));

function ago(at: number | null): string {
  return at === null ? "never" : shortAgo(Date.now() - at * 1000, 10_000);
}

function CatalogRow({ f, selected }: { f: Folder; selected: boolean }) {
  const ui = useUiTarget({ ref: { region: "main", view: "main.folders", kind: "folderRow", key: f.id },
                           label: f.display_name, socket: CENTRE_SOCKET });
  return (
    <li>
      <button type="button" className={`folder-row${selected ? " selected" : ""}`}
              aria-current={selected ? "true" : undefined} ref={(el) => { ui.whole(el); ui.part("row")(el); }}
              {...bindingProps(bind("folders.open", { folderId: f.id }))}>
        <RoleMark role={f.role} />
        <span className="folder-row-name" title={f.location.canonical_path} ref={ui.part("name")}>{f.display_name}</span>
        <span className="folder-row-node">{nodeWords(f)}</span>
        <AvailabilityMark availability={f.location.availability} ref={ui.part("availability")} />
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
  const ui = useUiTarget({ ref: { region: "main", view: "main.folders", kind: "folderCandidate", key: c.id },
                           label: "Discovered folder", socket: CENTRE_SOCKET });
  return (
    <li className="candidate-row" ref={(el) => { ui.whole(el); ui.part("row")(el); }}>
      <div className="candidate-head">
        <RoleMark role={c.proposed_role} />
        <span className="candidate-path" title={c.path} ref={ui.part("path")}>{c.path}</span>
      </div>
      <div className="candidate-meta">
        <AvailabilityMark availability={c.availability} />
        <span>{c.sessions} session{c.sessions === 1 ? "" : "s"}{who ? ` · ${who}` : ""}</span>
        <span>seen {ago(c.last_observed_at)}</span>
      </div>
      {c.refusal && <p className="folder-note">JARVIS does not read this location, so it cannot be registered.</p>}
      {c.access && !c.refusal && <p className="folder-note">Would be read as: {c.access.description}.</p>}
      <div className="candidate-actions" ref={ui.part("actions")}>
        {c.state === "discovered" && (
          <>
            <button type="button" className="folder-button" disabled={busy || !!c.refusal || c.availability !== "available"}
                    onClick={() => act(async () => {
                      const done = await registerFolder(c.path, c.access!.reader);
                      runBinding(bind("folders.open", { folderId: done.folder.id }));
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
      runBinding(bind("folders.open", { folderId: done.folder.id }));
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
  const { view, search, role, availability, removed: showRemoved, ignored: showIgnored, adding } =
    useSyncExternalStore(listState.subscribe, listState.get);
  const ui = useUiTarget({ ref: { region: "main", view: "main.folders", kind: "foldersList" },
                           label: "Folder catalog", socket: CENTRE_SOCKET });

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
      <section className="folders-catalog" aria-label="Folder catalog" ref={ui.whole}>
        <div className="folders-toolbar">
          <label className="folder-field folder-search" ref={ui.part("search")}>
            <span className="visually-hidden">Search folders by name or path</span>
            <input type="search" placeholder="Search name or path" value={search} maxLength={200}
                   data-ui-command="folders.search"
                   onChange={(e) => runBinding(e.target.value ? bind("folders.search", { text: e.target.value })
                                                              : bind("folders.search", {}))} />
          </label>
          <button type="button" className="folder-button" aria-expanded={adding} ref={ui.part("add")}
                  {...bindingProps(bind("folders.add.open", { open: !adding }))}>Add existing folder</button>
        </div>
        <div className="folders-tabs" role="tablist" aria-label="Which folders">
          {(["registered", "discovered"] as ListView[]).map((v) => (
            <button key={v} type="button" role="tab" aria-selected={view === v} ref={ui.part(v)}
                    className={`folder-tab${view === v ? " selected" : ""}`}
                    {...bindingProps(bind("folders.list", { view: v }))}>
              {v === "registered" ? `Registered (${live.value?.folders.length ?? 0})`
                                  : `Discovered (${found.value?.discovered.length ?? 0})`}
            </button>
          ))}
        </div>
        <div className="folders-filters">
          <label className="folder-field" ref={ui.part("role")}>
            <span>Role</span>
            <select value={role} data-ui-command="folders.filter"
                    onChange={(e) => filter({ role: (e.target.value || "any") as Role | "any" })}>
              <option value="">All roles</option>
              {ROLES.map((r) => <option key={r} value={r}>{ROLE_WORDS[r]}</option>)}
            </select>
          </label>
          <label className="folder-field" ref={ui.part("availability")}>
            <span>Availability</span>
            <select value={availability} data-ui-command="folders.filter"
                    onChange={(e) => filter({ availability: (e.target.value || "any") as CommandArgs["folders.filter"]["availability"] })}>
              <option value="">Any</option>
              {AVAILABILITIES.map((a) => <option key={a} value={a}>{AVAILABILITY_WORDS[a]}</option>)}
            </select>
          </label>
          {view === "registered" ? (
            <label className="folder-check" ref={ui.part("removed")}>
              <input type="checkbox" checked={showRemoved} data-ui-command="folders.filter"
                     onChange={(e) => filter({ removed: e.target.checked })} />
              Show removed
            </label>
          ) : (
            <label className="folder-check" ref={ui.part("ignored")}>
              <input type="checkbox" checked={showIgnored} data-ui-command="folders.filter"
                     onChange={(e) => filter({ ignored: e.target.checked })} />
              Show ignored
            </label>
          )}
        </div>
        {adding && <AddFolder onDone={() => {
          runBinding(bind("folders.add.open", { open: false }));
          runBinding(bind("folders.list", { view: "registered" }));
        }} />}
        <div className="folders-list" role="tabpanel" ref={ui.part("list")} aria-label={view === "registered" ? "Registered" : "Discovered"}>
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
