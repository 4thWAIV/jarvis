/**
 * One folder in full, the right half of the centre's Folders view
 * (FoldersView.tsx): its name and role (editable), where it actually is,
 * which node has it, whether it is there now and when it was last checked
 * and last seen, how JARVIS reads it, and Refresh, Reconnect and Remove
 * from catalog (which never deletes a file, and says so). Below, its
 * contents: breadcrumbs, one bounded page of a directory at a time with
 * Load more, and a file's preview, a bounded window at a time; then the
 * sessions whose records named it, and its location's history.
 *
 * Every state is said in words: loading, empty, partial, missing, access
 * denied, removed, unsupported preview. File content is shown as text,
 * escaped by React; an HTML file is its source, never a page.
 */

import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from "react";
import { AvailabilityMark, RoleMark } from "./FolderMarks";
import {
  catalog, editFolder, foldersChanged, FolderError, nodeWords, PROVIDER_WORDS, readDetail,
  readEntries, readPreview, reconnectFolder, refreshFolder, ROLE_WORDS, selectedFolder, sizeWords,
  unregisterFolder, type Entries, type Entry, type Folder, type FolderDetail as Detail,
  type Preview, type Role,
} from "./folders";
import { shortAgo } from "./timeFormat";

function when(at: number | null): string {
  if (at === null) return "never";
  return `${new Date(at * 1000).toLocaleString()} (${shortAgo(Date.now() - at * 1000, 10_000)})`;
}

/** What a refusal means to the person looking at the folder. */
function refusalWords(e: unknown): string {
  if (!(e instanceof FolderError)) return (e as Error)?.message ?? "Something went wrong.";
  switch (e.code) {
    case "missing": return "Nothing is at this location now.";
    case "access_denied": return "macOS does not let JARVIS read this location.";
    case "forbidden": return "JARVIS does not read this: it is hidden, a credential, a link, or outside the folder.";
    case "unsupported": return "This cannot be previewed.";
    case "removed": return "This folder was removed from the catalog; it is not read.";
    case "unavailable": return "This folder is on another node; it is not read from here.";
    case "stale_cursor": return "The folder's location changed; showing it again from the top.";
    default: return e.message;
  }
}

/** A read that a newer one for the same view replaces: the older is
 *  aborted, and its answer, if it still arrives, is dropped. */
function useLatest() {
  const current = useRef<AbortController | null>(null);
  useEffect(() => () => current.current?.abort(), []);
  return useCallback(() => {
    current.current?.abort();
    current.current = new AbortController();
    return current.current;
  }, []);
}

function Contents({ folder }: { folder: Folder }) {
  const [path, setPath] = useState("");
  const [page, setPage] = useState<Entries | null>(null);
  const [rows, setRows] = useState<Entry[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [file, setFile] = useState<string | null>(null);
  const latest = useLatest();
  const revision = folder.location.revision;

  const load = useCallback(async (at: string, cursor: string | null) => {
    const ctl = latest();
    setLoading(true);
    setError(null);
    try {
      const got = await readEntries(folder.id, at, revision, cursor, ctl.signal);
      if (ctl.signal.aborted) return;
      setPage(got);
      setRows((prev) => (cursor ? [...prev, ...got.entries] : got.entries));
    } catch (e) {
      if (ctl.signal.aborted) return;
      if (e instanceof FolderError && e.code === "stale_cursor") {
        setPath("");
        return;
      }
      setPage(null);
      setRows([]);
      setError(refusalWords(e));
    } finally {
      if (!ctl.signal.aborted) setLoading(false);
    }
  }, [folder.id, revision, latest]);

  useEffect(() => { void load(path, null); }, [path, load]);
  useEffect(() => { setPath(""); setFile(null); }, [folder.id, revision]);

  const open = (e: Entry) => {
    if (e.kind === "dir") {
      setFile(null);
      setPath(e.path);
    } else if (e.kind === "file") {
      setFile(e.path);
    }
  };

  return (
    <section className="folder-contents" aria-label="Contents">
      <h3>Contents</h3>
      <nav className="folder-crumbs" aria-label="Path in folder">
        {(page?.breadcrumbs ?? [{ name: folder.display_name, path: "" }]).map((b, i, all) => (
          <span key={b.path}>
            {i < all.length - 1
              ? <button type="button" className="folder-crumb" onClick={() => { setFile(null); setPath(b.path); }}>{b.name}</button>
              : <span aria-current="location">{b.name}</span>}
            {i < all.length - 1 && <span aria-hidden="true"> / </span>}
          </span>
        ))}
      </nav>
      {loading && !rows.length && <p className="folder-note" role="status">Reading…</p>}
      {error && <p className="folder-error" role="alert">{error}</p>}
      {page && !rows.length && !loading && <p className="folder-note">This folder is empty{page.withheld ? " of anything JARVIS shows" : ""}.</p>}
      {page && (page.withheld > 0 || page.partial) && (
        <p className="folder-note" role="status">
          {page.withheld > 0 && `${page.withheld} item${page.withheld === 1 ? " is" : "s are"} not shown (hidden, credentials, or not this reader's). `}
          {page.partial && `This directory is too large to look at in full; only the first ${page.scanned} entries were sorted.`}
        </p>
      )}
      <div className="folder-contents-body">
        <ul className="folder-entries">
          {rows.map((e) => (
            <li key={e.path}>
              <button type="button" className={`folder-entry kind-${e.kind}${file === e.path ? " selected" : ""}`}
                      disabled={e.kind === "link" || e.kind === "other"}
                      title={e.kind === "link" ? "A link: JARVIS does not follow links" : e.name}
                      onClick={() => open(e)}>
                <span className="folder-entry-kind">{e.kind === "dir" ? "Folder" : e.kind === "file" ? (e.transcript ? "Transcript" : "File") : e.kind === "link" ? "Link" : "Other"}</span>
                <span className="folder-entry-name">{e.name}{e.kind === "dir" ? "/" : ""}</span>
                <span className="folder-entry-size">{sizeWords(e.size)}</span>
              </button>
            </li>
          ))}
        </ul>
        {page?.next_cursor && (
          <button type="button" className="folder-button quiet" disabled={loading}
                  onClick={() => void load(path, page.next_cursor)}>Load more</button>
        )}
      </div>
      {file && <FilePreview key={`${folder.id}:${revision}:${file}`} folder={folder} path={file} />}
    </section>
  );
}

function FilePreview({ folder, path }: { folder: Folder; path: string }) {
  const [windows, setWindows] = useState<Preview[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const latest = useLatest();

  const load = useCallback(async (cursor: string | null) => {
    const ctl = latest();
    setLoading(true);
    setError(null);
    try {
      const got = await readPreview(folder.id, path, folder.location.revision, cursor, ctl.signal);
      if (!ctl.signal.aborted) setWindows((prev) => (cursor ? [...prev, got] : [got]));
    } catch (e) {
      if (!ctl.signal.aborted) setError(refusalWords(e));
    } finally {
      if (!ctl.signal.aborted) setLoading(false);
    }
  }, [folder.id, folder.location.revision, path, latest]);

  useEffect(() => { void load(null); }, [load]);
  const first = windows[0];
  const last = windows[windows.length - 1];
  return (
    <section className="folder-preview" aria-label={`Preview of ${path}`}>
      <h4 className="folder-path">{path}</h4>
      {loading && !first && <p className="folder-note" role="status">Reading…</p>}
      {error && <p className="folder-error" role="alert">{error}</p>}
      {first && (
        <p className="folder-note">
          {first.kind === "transcript" ? "A session transcript, shown as what was said" : first.kind === "binary" ? "A binary file" : "Text"}
          {" · "}{sizeWords(first.size)} · modified {when(first.modified)}
          {first.sessions[0]?.session_id && ` · session ${first.sessions[0].session_id}`}
        </p>
      )}
      {first?.kind === "binary" && <p className="folder-note">Binary and media files are shown as their details only.</p>}
      {first && first.kind !== "binary" && (
        <pre className="folder-text" tabIndex={0}>
          {windows.flatMap((w) => w.lines).join("\n") || "(nothing to show)"}
        </pre>
      )}
      {last && (last.clipped_lines > 0 || last.skipped_records > 0) && (
        <p className="folder-note">
          {last.clipped_lines > 0 && "Very long lines are cut short. "}
          {last.skipped_records > 0 && `${last.skipped_records} record${last.skipped_records === 1 ? "" : "s"} could not be read.`}
        </p>
      )}
      {last?.next_cursor && (
        <button type="button" className="folder-button quiet" disabled={loading}
                onClick={() => void load(last.next_cursor)}>Show more</button>
      )}
      {last && !last.next_cursor && windows.length > 0 && first?.kind !== "binary" && <p className="folder-note">End of file.</p>}
    </section>
  );
}

function Reconnect({ folder, onDone }: { folder: Folder; onDone: () => void }) {
  const [path, setPath] = useState("");
  const [mismatch, setMismatch] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const go = async (replace: boolean) => {
    setBusy(true);
    setError(null);
    try {
      await reconnectFolder(folder, path.trim(), replace);
      onDone();
    } catch (e) {
      if (e instanceof FolderError && e.code === "identity_mismatch") {
        setMismatch(String(e.extra.new_path ?? path));
      } else {
        setError(refusalWords(e));
      }
    } finally {
      setBusy(false);
    }
  };
  return (
    <form className="folder-add" onSubmit={(e) => { e.preventDefault(); void go(false); }}
          aria-label="Reconnect location">
      <label className="folder-field">
        <span>New location (absolute, or starting with ~/)</span>
        <input type="text" value={path} spellCheck={false} autoFocus
               onChange={(e) => { setPath(e.target.value); setMismatch(null); }} />
      </label>
      {mismatch ? (
        <div role="alert">
          <p className="folder-note">{mismatch} is a different directory from the one recorded. Use it
            as this folder's location from now on? The folder keeps its ID; no file moves.</p>
          <button type="button" className="folder-button" disabled={busy} onClick={() => void go(true)}>Replace location</button>
        </div>
      ) : (
        <button type="submit" className="folder-button" disabled={busy || !path.trim()}>Reconnect</button>
      )}
      <button type="button" className="folder-button quiet" onClick={onDone}>Cancel</button>
      {error && <p className="folder-error" role="alert">{error}</p>}
    </form>
  );
}

export function FolderDetail({ id }: { id: string }) {
  const [detail, setDetail] = useState<Detail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [mode, setMode] = useState<"view" | "edit" | "reconnect" | "remove">("view");
  const [name, setName] = useState("");
  const [role, setRole] = useState<Role>("data");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const hint = useSyncExternalStore(foldersChanged.subscribe, foldersChanged.get);
  const reconnects = useSyncExternalStore(catalog.subscribe, catalog.get).value;
  const latest = useLatest();

  useEffect(() => {
    const ctl = latest();
    readDetail(id, ctl.signal).then((d) => { if (!ctl.signal.aborted) { setDetail(d); setError(null); } })
      .catch((e) => { if (!ctl.signal.aborted) setError(refusalWords(e)); });
  }, [id, hint, reconnects, latest]);

  if (error && !detail) return <p className="folder-error" role="alert">{error}</p>;
  if (!detail) return <p className="folder-note" role="status">Reading the folder…</p>;
  const f = detail.folder;
  const removed = f.lifecycle === "removed";

  const act = async (work: () => Promise<unknown>, done?: string) => {
    setBusy(true);
    setNotice(null);
    try {
      await work();
      setMode("view");
      if (done) setNotice(done);
    } catch (e) {
      setNotice(refusalWords(e) + (e instanceof FolderError && e.code === "stale_revision"
        ? " It changed elsewhere; the latest is shown." : ""));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="folder-detail" data-folder={f.id}>
      <header className="folder-detail-head">
        {mode === "edit" ? (
          <form className="folder-edit" aria-label="Edit label"
                onSubmit={(e) => { e.preventDefault(); void act(() => editFolder(f, { display_name: name, role }), "Saved."); }}>
            <label className="folder-field"><span>Name</span>
              <input type="text" value={name} onChange={(e) => setName(e.target.value)} autoFocus /></label>
            <label className="folder-field"><span>Role</span>
              <select value={role} onChange={(e) => setRole(e.target.value as Role)}>
                {(Object.keys(ROLE_WORDS) as Role[]).map((r) => <option key={r} value={r}>{ROLE_WORDS[r]}</option>)}
              </select></label>
            <button type="submit" className="folder-button" disabled={busy || !name.trim()}>Save</button>
            <button type="button" className="folder-button quiet" onClick={() => setMode("view")}>Cancel</button>
          </form>
        ) : (
          <>
            <h2>{f.display_name}</h2>
            <div className="folder-detail-marks">
              <RoleMark role={f.role} />
              <AvailabilityMark availability={f.location.availability} />
              {removed && <span className="folder-removed">Removed from catalog</span>}
            </div>
          </>
        )}
        <button type="button" className="workflow-close" title="Close" aria-label="Close folder"
                onClick={() => selectedFolder.set(null)}>×</button>
      </header>

      {mode === "view" && !removed && (
        <div className="folder-actions">
          <button type="button" className="folder-button" disabled={busy}
                  onClick={() => void act(() => refreshFolder(f), "Checked now; discovery will look again shortly.")}>Refresh</button>
          <button type="button" className="folder-button quiet" disabled={busy}
                  onClick={() => { setName(f.display_name); setRole(f.role); setMode("edit"); }}>Edit label</button>
          <button type="button" className="folder-button quiet" disabled={busy} onClick={() => setMode("reconnect")}>Reconnect location</button>
          <button type="button" className="folder-button danger" disabled={busy} onClick={() => setMode("remove")}>Remove from catalog</button>
        </div>
      )}
      {mode === "remove" && (
        <div className="folder-confirm" role="alertdialog" aria-label="Remove from catalog">
          <p>Remove “{f.display_name}” from JARVIS's catalog? JARVIS stops reading it. <strong>No file is
            deleted</strong>: the folder, its files and its sessions' histories stay exactly where they are.</p>
          <button type="button" className="folder-button danger" disabled={busy}
                  onClick={() => void act(() => unregisterFolder(f), "Removed from the catalog. No files were touched.")}>Remove from catalog</button>
          <button type="button" className="folder-button quiet" onClick={() => setMode("view")}>Keep it</button>
        </div>
      )}
      {mode === "reconnect" && <Reconnect folder={f} onDone={() => setMode("view")} />}
      {notice && <p className="folder-note" role="status">{notice}</p>}

      <dl className="folder-facts">
        <dt>Location</dt><dd className="folder-path">{f.location.canonical_path}</dd>
        {f.location.supplied_path !== f.location.canonical_path && (
          <><dt>Registered as</dt><dd className="folder-path">{f.location.supplied_path}</dd></>
        )}
        <dt>Node</dt><dd>{nodeWords(f)}</dd>
        <dt>Last checked</dt><dd>{when(f.location.last_checked_at)}</dd>
        <dt>Last seen there</dt><dd>{when(f.location.last_seen_at)}</dd>
        <dt>Read as</dt><dd>{f.access.enabled ? f.access.description : "Not read (removed from the catalog)"}</dd>
        <dt>Location revision</dt><dd>{f.location.revision}</dd>
      </dl>

      {!removed && f.location.availability === "available" && <Contents folder={f} />}
      {!removed && f.location.availability !== "available" && (
        <p className="folder-note" role="status">
          {f.location.availability === "missing" && "Nothing is at this location now. Its record and sessions are kept; reconnect it if it moved."}
          {f.location.availability === "access_denied" && "macOS does not let JARVIS read this location. Grant access, then Refresh."}
          {f.location.availability === "unavailable_node" && "This folder is on another node and is not read from here."}
          {f.location.availability === "unknown" && "Not checked yet. Refresh to check it now."}
        </p>
      )}

      <section className="folder-sessions" aria-label="Related sessions">
        <h3>Related sessions</h3>
        {!detail.sessions.length && <p className="folder-note">No session's records name this folder yet.</p>}
        <ul>
          {detail.sessions.map((s) => (
            <li key={`${s.provider}:${s.profile_root}:${s.session_id}`} className="folder-session">
              <span className="folder-session-name">{s.agent_name ?? s.session_id}</span>
              <span>{PROVIDER_WORDS[s.provider] ?? s.provider}</span>
              <span>{s.live ? "Live" : "Not running"}</span>
              <span>{s.relation === "history" ? "history kept here" : s.relation === "cwd" ? "worked here" : `worked in ${s.cwd}`}</span>
              {s.associated.subagents ? <span>{s.associated.subagents} subagent transcript{s.associated.subagents === 1 ? "" : "s"}</span> : null}
              {s.associated.memory ? <span>memory notes</span> : null}
              <span>seen {shortAgo(Date.now() - s.observed_at * 1000, 10_000)}</span>
            </li>
          ))}
        </ul>
      </section>

      <section className="folder-events" aria-label="Location history">
        <h3>Location history</h3>
        <ul>
          {detail.events.map((e) => (
            <li key={e.id}>
              <span>{when(e.at)}</span>{" "}
              <span>{e.event === "availability"
                ? `${String(e.prior.availability ?? "").replace("_", " ")} → ${String(e.new.availability ?? "").replace("_", " ")}`
                : e.event === "reconnected" ? `reconnected from ${String(e.prior.canonical_path ?? "")}` : e.event}</span>
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}
