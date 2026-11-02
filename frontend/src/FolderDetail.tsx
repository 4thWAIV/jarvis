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
import { CENTRE_SOCKET } from "./ConversationView";
import { codeWords, formatDateTime, SaidText, t as tNow, useT, type Said } from "./locale";
import { createStore } from "./store";
import { bind, bindingProps } from "./uiNavigation";
import { registerCommand, useUiTarget, type Availability, type CommandOutcome } from "./uiRegistry";
import { UI_CONTRACT, type CommandArgs } from "./uiContract";
import { registerRevealer } from "./uiNavigationReveal";
import { kindDecl, splitKey } from "./uiValidate";
import { AvailabilityMark, RoleMark } from "./FolderMarks";
import {
  catalog, editFolder, foldersChanged, FolderError, folderRefusal, nodeWords, PROVIDER_WORDS, readDetail,
  readEntries, readerWords, readPreview, reconnectFolder, refreshFolder, ROLES, roleWords, selectedFolder,
  sizeWords, unregisterFolder,
 type Entries, type Entry, type Folder, type FolderDetail as Detail,
  type Preview, type Role,
} from "./folders";
import { shortAgo } from "./timeFormat";

/** A part's callback ref, from a registered target. */
type PartRef = (part: string) => (el: Element | null) => void;

function when(at: number | null): string {
  if (at === null) return tNow("main.folders.never");
  return `${formatDateTime(at * 1000, { dateStyle: "short", timeStyle: "medium" })} (${
    shortAgo(Date.now() - at * 1000, 10_000)})`;
}

/** An availability a location event recorded, in words. */
const availabilityOf = (value: unknown): string =>
  codeWords("main.folders.availability", String(value ?? "")) ?? String(value ?? "");

/** What a refusal means to the person looking at the folder. */
const refusalWords = (e: unknown): Said => folderRefusal(e, "detail");

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

// ── the selected folder's contents ──────────────────────────────────────
//
// Which directory of the shown folder is open, which file is previewed and
// how much of each is loaded are kept here, for one folder at its location
// revision, and changed only by `folder.browse`, `folder.preview` and
// `folder.more`: a click and the brain's request run the same handler. The
// folder's reader on the server decides what may be read (containment,
// links, hidden files and credentials stay its refusals), and a refusal is
// shown in its words. Opening the folder reads its top directory.

interface Listing { page: Entries | null; rows: Entry[]; error: Said | null; loading: boolean }
interface Showing { windows: Preview[]; error: Said | null; loading: boolean }
interface Contents {
  folderId: string;
  revision: number;
  /** The directory shown, relative to the folder ("" is its top). */
  path: string;
  dir: Listing;
  /** The file previewed, or null. */
  file: string | null;
  preview: Showing;
}

const NO_LISTING: Listing = { page: null, rows: [], error: null, loading: false };
const NO_PREVIEW: Showing = { windows: [], error: null, loading: false };

const contents = createStore<Contents | null>(null);

/** The read in flight for the directory and for the preview: a newer one
 *  aborts the older, and its answer, if it still arrives, is dropped. */
const reads: Record<"dir" | "preview", AbortController | null> = { dir: null, preview: null };

function fresh(which: "dir" | "preview"): AbortController {
  reads[which]?.abort();
  reads[which] = new AbortController();
  return reads[which]!;
}

/** Change the contents if they are still `of`'s folder at its revision. */
function update(of: Contents, change: (c: Contents) => Contents): void {
  const now = contents.get();
  if (now && now.folderId === of.folderId && now.revision === of.revision) contents.set(change(now));
}

const superseded: CommandOutcome = { status: "cancelled", effects: [], reason: "superseded" };
const unread: CommandOutcome = { status: "unavailable", effects: [], reason: "not-loaded" };

/** Show directory `path` of the folder: its first page, or with `cursor`
 *  the next one added below. */
async function readDir(of: Contents, path: string, cursor: string | null): Promise<CommandOutcome> {
  const ctl = fresh("dir");
  update(of, (c) => ({ ...c, path, dir: { ...(cursor ? c.dir : NO_LISTING), loading: true, error: null } }));
  try {
    const got = await readEntries(of.folderId, path, of.revision, cursor, ctl.signal);
    if (ctl.signal.aborted) return superseded;
    update(of, (c) => ({ ...c, dir: { page: got, rows: cursor ? [...c.dir.rows, ...got.entries] : got.entries,
                                      error: null, loading: false } }));
    return { status: "completed", effects: ["list.paged"] };
  } catch (e) {
    if (ctl.signal.aborted) return superseded;
    // The location moved under the cursor: shown again from the top.
    if (e instanceof FolderError && e.code === "stale_cursor") return readDir(of, "", null);
    update(of, (c) => ({ ...c, dir: { ...NO_LISTING, error: refusalWords(e) } }));
    return unread;
  }
}

/** Preview file `path`: its first window, or with `cursor` the next one. */
async function readFile(of: Contents, path: string, cursor: string | null): Promise<CommandOutcome> {
  const ctl = fresh("preview");
  update(of, (c) => ({ ...c, file: path, preview: { ...(cursor ? c.preview : NO_PREVIEW), loading: true, error: null } }));
  try {
    const got = await readPreview(of.folderId, path, of.revision, cursor, ctl.signal);
    if (ctl.signal.aborted) return superseded;
    update(of, (c) => ({ ...c, preview: { windows: cursor ? [...c.preview.windows, got] : [got],
                                          error: null, loading: false } }));
    return { status: "completed", effects: cursor ? ["list.paged"] : [] };
  } catch (e) {
    if (ctl.signal.aborted) return superseded;
    update(of, (c) => ({ ...c, preview: { ...c.preview, error: refusalWords(e), loading: false } }));
    return unread;
  }
}

function closePreview(of: Contents): void {
  reads.preview?.abort();
  update(of, (c) => ({ ...c, file: null, preview: NO_PREVIEW }));
}

/** Offered for the folder shown, and refused at another location revision. */
function shownFolder(folderId: string): Availability {
  return contents.get()?.folderId === folderId ? { available: true } : { available: false, reason: "no-entity" };
}

const staleAt = (revision: number | undefined): CommandOutcome | null =>
  revision !== undefined && revision !== contents.get()!.revision
    ? { status: "stale", effects: [], reason: "entity-gone" } : null;

/** Where the next page of the list starts, if there is one. */
function nextOf(c: Contents, list: "entries" | "preview"): string | null {
  return list === "entries" ? c.dir.page?.next_cursor ?? null
    : c.preview.windows[c.preview.windows.length - 1]?.next_cursor ?? null;
}

registerCommand("folder.browse", {
  availability: ({ folderId }) => shownFolder(folderId),
  execute({ path, revision }) {
    const stale = staleAt(revision);
    if (stale) return stale;
    const c = contents.get()!;
    closePreview(c);
    return readDir(c, path ?? "", null);
  },
});

registerCommand("folder.preview", {
  availability: ({ folderId }) => shownFolder(folderId),
  execute({ path, revision }) {
    const stale = staleAt(revision);
    if (stale) return stale;
    const c = contents.get()!;
    if (!path) {
      closePreview(c);
      return { status: "completed", effects: [] };
    }
    return readFile(c, path, null);
  },
});

registerCommand("folder.more", {
  availability: ({ folderId, list }) => {
    const shown = shownFolder(folderId);
    if (!shown.available) return shown;
    return nextOf(contents.get()!, list) ? { available: true } : { available: false, reason: "no-entity" };
  },
  execute({ list }) {
    const c = contents.get()!;
    return list === "entries" ? readDir(c, c.path, nextOf(c, list)) : readFile(c, c.file!, nextOf(c, list));
  },
});

// ── the shown folder's form ─────────────────────────────────────────────
//
// Whether the folder is shown as it is or with its Edit, Reconnect or
// Remove form open: kept here, changed by folder.mode (a click and the
// brain alike), which never checks or saves anything. A folder shown
// afresh starts as it is; a removed one has no forms.

type Mode = CommandArgs["folder.mode"]["mode"];

const form = createStore<{ folderId: string | null; removed: boolean; mode: Mode }>(
  { folderId: null, removed: false, mode: "view" });

/** The folder `folderId` is shown, read and (`removed`) removed or not. */
function formShows(folderId: string | null, removed = false): void {
  const now = form.get();
  form.set({ folderId, removed, mode: now.folderId === folderId && !removed ? now.mode : "view" });
}

/** Back to the folder as it is, once a form's work is done. */
const formDone = () => form.set({ ...form.get(), mode: "view" });

registerCommand("folder.mode", {
  availability: ({ folderId, mode }) => {
    const now = form.get();
    if (now.folderId !== folderId) return { available: false, reason: "no-entity" };
    return mode !== "view" && now.removed ? { available: false, reason: "no-entity" } : { available: true };
  },
  execute({ mode }) {
    form.set({ ...form.get(), mode });
    return { status: "completed", effects: [] };
  },
});

/** The folder's form as a binding: `mode` for `folderId`. */
const formBinding = (folderId: string, mode: Mode) => bind("folder.mode", { folderId, mode });

/** A directory or file of the folder, as a binding: the folder's top when
 *  `path` is "". */
const browseTo = (folderId: string, path: string) =>
  bind("folder.browse", path ? { folderId, path } : { folderId });

/** Resolves true once folder `folderId`'s contents are shown, false if that
 *  does not happen within the reveal's limit or the reveal ends first. */
function contentsShown(folderId: string, signal: AbortSignal): Promise<boolean> {
  return new Promise((resolve) => {
    const ready = () => contents.get()?.folderId === folderId;
    if (ready()) return resolve(true);
    const finish = (ok: boolean) => { stop(); clearTimeout(timer); signal.removeEventListener("abort", end); resolve(ok); };
    const end = () => finish(false);
    const stop = contents.subscribe(() => { if (ready()) finish(true); });
    const timer = setTimeout(end, UI_CONTRACT.limits.revealWaitMs);
    signal.addEventListener("abort", end, { once: true });
  });
}

// An entry not drawn: its folder (the key's first part) is opened and the
// directory that lists it browsed to, both by their registered commands,
// run as the caller. Nothing else; an entry past the first page is not
// paged to.
registerRevealer("main.folders", "folderEntry", async (ref, { navigate, signal }) => {
  const [folderId, path] = splitKey(kindDecl("folderEntry")!, ref.key!);
  if (selectedFolder.get() !== folderId && !await navigate("folders.open", { folderId })) return "no-entity";
  if (!await contentsShown(folderId, signal)) return "not-loaded";
  const dir = path.includes("/") ? path.slice(0, path.lastIndexOf("/")) : "";
  if (contents.get()!.path === dir) return true;
  return await navigate("folder.browse", dir ? { folderId, path: dir } : { folderId }) ? true : "no-entity";
});

/** One entry of the folder or one crumb of the path, by its relative path. */
function useEntryTarget(folderId: string, path: string, label: string) {
  return useUiTarget(path ? { ref: { region: "main", view: "main.folders", kind: "folderEntry", key: `${folderId}/${path}` },
                              label, socket: CENTRE_SOCKET } : null);
}

function Contents({ folder, partRef }: { folder: Folder; partRef: PartRef }) {
  const revision = folder.location.revision;
  // Opening the folder, or its location moving, shows its top directory.
  useEffect(() => {
    const opened: Contents = { folderId: folder.id, revision, path: "", dir: NO_LISTING, file: null, preview: NO_PREVIEW };
    contents.set(opened);
    void readDir(opened, "", null);
    return () => {
      reads.dir?.abort();
      reads.preview?.abort();
      if (contents.get()?.folderId === opened.folderId) contents.set(null);
    };
  }, [folder.id, revision]);
  const now = useSyncExternalStore(contents.subscribe, contents.get);
  const c = now && now.folderId === folder.id && now.revision === revision ? now : null;
  const { page, rows, error, loading } = c?.dir ?? NO_LISTING;
  const file = c?.file ?? null;
  const t = useT();

  return (
    <section className="folder-contents" aria-label={t("main.folder.contents")} ref={partRef("contents")}>
      <h3>{t("main.folder.contents")}</h3>
      <nav className="folder-crumbs" aria-label={t("main.folder.pathInFolder")}>
        {(page?.breadcrumbs ?? [{ name: folder.display_name, path: "" }]).map((b, i, all) => (
          <Crumb key={b.path} folderId={folder.id} crumb={b} last={i === all.length - 1} />
        ))}
      </nav>
      {loading && !rows.length && <p className="folder-note" role="status">{t("main.folder.reading")}</p>}
      {error && <SaidText said={error} as="p" className="folder-error" role="alert" />}
      {page && !rows.length && !loading && <p className="folder-note">{page.withheld ? t("main.folder.emptyShown") : t("main.folder.empty")}</p>}
      {page && (page.withheld > 0 || page.partial) && (
        <p className="folder-note" role="status">
          {page.withheld > 0 && t("main.folder.withheld", { count: page.withheld })}
          {page.withheld > 0 && page.partial && " "}
          {page.partial && t("main.folder.partial", { count: page.scanned })}
        </p>
      )}
      <div className="folder-contents-body">
        <ul className="folder-entries">
          {rows.map((e) => <EntryRow key={e.path} folderId={folder.id} entry={e} selected={file === e.path} />)}
        </ul>
        {page?.next_cursor && (
          <button type="button" className="folder-button quiet" disabled={loading}
                  {...bindingProps(bind("folder.more", { folderId: folder.id, list: "entries" }))}>{t("main.folder.loadMore")}</button>
        )}
      </div>
      {c && file && <FilePreview folder={folder} path={file} preview={c.preview} previewRef={partRef("preview")} />}
    </section>
  );
}

function Crumb({ folderId, crumb: b, last }: { folderId: string; crumb: { name: string; path: string }; last: boolean }) {
  const ui = useEntryTarget(folderId, b.path, b.name);
  const crumb = (el: Element | null) => { ui.whole(el); ui.part("crumb")(el); };
  return (
    <span>
      {!last
        ? <button type="button" className="folder-crumb" ref={crumb}
                  {...bindingProps(browseTo(folderId, b.path))}>{b.name}</button>
        : <span aria-current="location" ref={crumb}>{b.name}</span>}
      {!last && <span aria-hidden="true"> / </span>}
    </span>
  );
}

/** What an entry is, in a word. */
function entryKind(e: Entry): string {
  if (e.kind === "file") return tNow(e.transcript ? "main.folder.entry.transcript" : "main.folder.entry.file");
  return tNow(`main.folder.entry.${e.kind}`);
}

function EntryRow({ folderId, entry: e, selected }: { folderId: string; entry: Entry; selected: boolean }) {
  const t = useT();
  const ui = useEntryTarget(folderId, e.path, e.name);
  const opens = e.kind === "dir" ? browseTo(folderId, e.path)
    : e.kind === "file" ? bind("folder.preview", { folderId, path: e.path }) : null;
  return (
    <li>
      <button type="button" className={`folder-entry kind-${e.kind}${selected ? " selected" : ""}`}
              ref={(el) => { ui.whole(el); ui.part("row")(el); }}
              disabled={!opens}
              title={e.kind === "link" ? t("main.folder.linkTitle") : e.name}
              {...(opens ? bindingProps(opens) : {})}>
        <span className="folder-entry-kind">{entryKind(e)}</span>
        <span className="folder-entry-name">{e.name}{e.kind === "dir" ? "/" : ""}</span>
        <span className="folder-entry-size">{sizeWords(e.size)}</span>
      </button>
    </li>
  );
}

function FilePreview({ folder, path, preview, previewRef }: {
  folder: Folder; path: string; preview: Showing; previewRef: (el: Element | null) => void }) {
  const { windows, error, loading } = preview;
  const first = windows[0];
  const last = windows[windows.length - 1];
  const t = useT();
  return (
    <section className="folder-preview" aria-label={t("main.folder.previewOf", { path })} ref={previewRef}>
      <h4 className="folder-path">{path}</h4>
      {loading && !first && <p className="folder-note" role="status">{t("main.folder.reading")}</p>}
      {error && <SaidText said={error} as="p" className="folder-error" role="alert" />}
      {first && (
        <p className="folder-note">
          {t(`main.folder.preview.${first.kind}`)}
          {" · "}{sizeWords(first.size)} · {t("main.folder.modified", { when: when(first.modified) })}
          {first.sessions[0]?.session_id && <> · {t("main.folder.session", { id: first.sessions[0].session_id })}</>}
        </p>
      )}
      {first?.kind === "binary" && <p className="folder-note">{t("main.folder.binaryNote")}</p>}
      {first && first.kind !== "binary" && (
        <pre className="folder-text" tabIndex={0}>
          {windows.flatMap((w) => w.lines).join("\n") || t("main.folder.nothingToShow")}
        </pre>
      )}
      {last && (last.clipped_lines > 0 || last.skipped_records > 0) && (
        <p className="folder-note">
          {last.clipped_lines > 0 && t("main.folder.clipped")}
          {last.clipped_lines > 0 && last.skipped_records > 0 && " "}
          {last.skipped_records > 0 && t("main.folder.skipped", { count: last.skipped_records })}
        </p>
      )}
      {last?.next_cursor && (
        <button type="button" className="folder-button quiet" disabled={loading}
                {...bindingProps(bind("folder.more", { folderId: folder.id, list: "preview" }))}>{t("main.folder.showMore")}</button>
      )}
      {last && !last.next_cursor && windows.length > 0 && first?.kind !== "binary" && <p className="folder-note">{t("main.folder.endOfFile")}</p>}
    </section>
  );
}

function Reconnect({ folder }: { folder: Folder }) {
  const t = useT();
  const [path, setPath] = useState("");
  const [mismatch, setMismatch] = useState<string | null>(null);
  const [error, setError] = useState<Said | null>(null);
  const [busy, setBusy] = useState(false);
  const go = async (replace: boolean) => {
    setBusy(true);
    setError(null);
    try {
      await reconnectFolder(folder, path.trim(), replace);
      formDone();
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
    <form data-ui-action="domain" className="folder-add" onSubmit={(e) => { e.preventDefault(); void go(false); }}
          aria-label={t("main.folder.reconnectLocation")}>
      <label className="folder-field">
        <span>{t("main.folder.newLocation")}</span>
        <input data-ui-action="input" type="text" value={path} spellCheck={false} autoFocus
               onChange={(e) => { setPath(e.target.value); setMismatch(null); }} />
      </label>
      {mismatch ? (
        <div role="alert">
          <p className="folder-note">{t("main.folder.mismatch", { path: mismatch })}</p>
          <button data-ui-action="domain" type="button" className="folder-button" disabled={busy} onClick={() => void go(true)}>{t("main.folder.replaceLocation")}</button>
        </div>
      ) : (
        <button type="submit" className="folder-button" disabled={busy || !path.trim()}>{t("main.folder.reconnect")}</button>
      )}
      <button type="button" className="folder-button quiet"
              {...bindingProps(formBinding(folder.id, "view"))}>{t("main.common.cancel")}</button>
      {error && <SaidText said={error} as="p" className="folder-error" role="alert" />}
    </form>
  );
}

export function FolderDetail({ id }: { id: string }) {
  const t = useT();
  const [detail, setDetail] = useState<Detail | null>(null);
  const [error, setError] = useState<Said | null>(null);
  const shownForm = useSyncExternalStore(form.subscribe, form.get);
  const mode: Mode = shownForm.folderId === id ? shownForm.mode : "view";
  const [name, setName] = useState("");
  const [role, setRole] = useState<Role>("data");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<Said | null>(null);
  const hint = useSyncExternalStore(foldersChanged.subscribe, foldersChanged.get);
  const reconnects = useSyncExternalStore(catalog.subscribe, catalog.get).value;
  const latest = useLatest();

  useEffect(() => {
    const ctl = latest();
    readDetail(id, ctl.signal).then((d) => { if (!ctl.signal.aborted) { setDetail(d); setError(null); } })
      .catch((e) => { if (!ctl.signal.aborted) setError(refusalWords(e)); });
  }, [id, hint, reconnects, latest]);
  const removedNow = detail?.folder.lifecycle === "removed";
  const read = detail?.folder.id === id;
  useEffect(() => { formShows(read ? id : null, removedNow); }, [id, read, removedNow]);
  useEffect(() => () => formShows(null), [id]);
  // Edit starts from the folder's name and role as they are.
  useEffect(() => {
    if (mode === "edit" && detail) { setName(detail.folder.display_name); setRole(detail.folder.role); }
  }, [mode]);                                          // eslint-disable-line react-hooks/exhaustive-deps
  const ui = useUiTarget({ ref: { region: "main", view: "main.folders", kind: "folder", key: id },
                           label: detail?.folder.display_name ?? t("main.folder.folder"), socket: CENTRE_SOCKET });

  if (error && !detail) return <SaidText said={error} as="p" className="folder-error" role="alert" />;
  if (!detail) return <p className="folder-note" role="status">{t("main.folder.readingFolder")}</p>;
  const f = detail.folder;
  const removed = f.lifecycle === "removed";

  const act = async (work: () => Promise<unknown>, done?: () => string) => {
    setBusy(true);
    setNotice(null);
    try {
      await work();
      formDone();
      if (done) setNotice({ words: done });
    } catch (e) {
      setNotice(e instanceof FolderError && e.code === "stale_revision"
        ? { words: () => tNow("main.folder.changedElsewhere") } : refusalWords(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="folder-detail" data-folder={f.id} ref={(el) => { ui.whole(el); ui.part("detail")(el); }}>
      <header className="folder-detail-head">
        {mode === "edit" ? (
          <form data-ui-action="domain" className="folder-edit" aria-label={t("main.folder.editLabel")}
                onSubmit={(e) => { e.preventDefault(); void act(() => editFolder(f, { display_name: name, role }), () => tNow("main.folder.saved")); }}>
            <label className="folder-field"><span>{t("main.folders.nameLabel")}</span>
              <input data-ui-action="input" type="text" value={name} onChange={(e) => setName(e.target.value)} autoFocus /></label>
            <label className="folder-field"><span>{t("main.folders.roleLabel")}</span>
              <select data-ui-action="input" value={role} onChange={(e) => setRole(e.target.value as Role)}>
                {ROLES.map((r) => <option key={r} value={r}>{roleWords(r)}</option>)}
              </select></label>
            <button type="submit" className="folder-button" disabled={busy || !name.trim()}>{t("main.folder.save")}</button>
            <button type="button" className="folder-button quiet"
                    {...bindingProps(formBinding(f.id, "view"))}>{t("main.common.cancel")}</button>
          </form>
        ) : (
          <>
            <h2 ref={ui.part("name")}>{f.display_name}</h2>
            <div className="folder-detail-marks">
              <RoleMark role={f.role} />
              <AvailabilityMark availability={f.location.availability} ref={ui.part("availability")} />
              {removed && <span className="folder-removed">{t("main.folder.removedMark")}</span>}
            </div>
          </>
        )}
        <button type="button" className="workflow-close" title={t("main.common.close")} aria-label={t("main.folder.close")}
                ref={ui.part("close")} {...bindingProps(bind("folders.close", {}))}>×</button>
      </header>

      {mode === "view" && !removed && (
        <div className="folder-actions">
          <button data-ui-action="refresh" type="button" className="folder-button" disabled={busy}
                  onClick={() => void act(() => refreshFolder(f), () => tNow("main.folder.refreshed"))}>{t("main.folder.refresh")}</button>
          <button type="button" className="folder-button quiet" disabled={busy}
                  {...bindingProps(formBinding(f.id, "edit"))}>{t("main.folder.editLabel")}</button>
          <button type="button" className="folder-button quiet" disabled={busy}
                  {...bindingProps(formBinding(f.id, "reconnect"))}>{t("main.folder.reconnectLocation")}</button>
          <button type="button" className="folder-button danger" disabled={busy}
                  {...bindingProps(formBinding(f.id, "remove"))}>{t("main.folder.remove")}</button>
        </div>
      )}
      {mode === "remove" && (
        <div className="folder-confirm" role="alertdialog" aria-label={t("main.folder.remove")}>
          <p>{t("main.folder.removeQuestion", { name: f.display_name })} <strong>{t("main.folder.noFileDeleted")}</strong>{" "}
            {t("main.folder.filesStay")}</p>
          <button data-ui-action="domain" type="button" className="folder-button danger" disabled={busy}
                  onClick={() => void act(() => unregisterFolder(f), () => tNow("main.folder.removed"))}>{t("main.folder.remove")}</button>
          <button type="button" className="folder-button quiet"
                  {...bindingProps(formBinding(f.id, "view"))}>{t("main.common.keepIt")}</button>
        </div>
      )}
      {mode === "reconnect" && <Reconnect folder={f} />}

      {notice && <SaidText said={notice} as="p" className="folder-note" role="status" />}

      <dl className="folder-facts">
        <dt>{t("main.folder.location")}</dt><dd className="folder-path" ref={ui.part("location")}>{f.location.canonical_path}</dd>
        {f.location.supplied_path !== f.location.canonical_path && (
          <><dt>{t("main.folder.registeredAs")}</dt><dd className="folder-path">{f.location.supplied_path}</dd></>
        )}
        <dt>{t("main.folder.node")}</dt><dd>{nodeWords(f)}</dd>
        <dt>{t("main.folder.lastChecked")}</dt><dd>{when(f.location.last_checked_at)}</dd>
        <dt>{t("main.folder.lastSeen")}</dt><dd>{when(f.location.last_seen_at)}</dd>
        <dt>{t("main.folders.readAs")}</dt><dd>{f.access.enabled ? readerWords(f.access) : t("main.folder.notRead")}</dd>
        <dt>{t("main.folder.locationRevision")}</dt><dd>{f.location.revision}</dd>
      </dl>

      {!removed && f.location.availability === "available" && <Contents folder={f} partRef={ui.part} />}
      {!removed && f.location.availability !== "available" && (
        <p className="folder-note" role="status">
          {t(`main.folder.unavailable.${f.location.availability}`)}
        </p>
      )}

      <section className="folder-sessions" aria-label={t("main.folder.relatedSessions")}>
        <h3>{t("main.folder.relatedSessions")}</h3>
        {!detail.sessions.length && <p className="folder-note">{t("main.folder.noSessions")}</p>}
        <ul>
          {detail.sessions.map((s) => (
            <li key={`${s.provider}:${s.profile_root}:${s.session_id}`} className="folder-session">
              <span className="folder-session-name">{s.agent_name ?? s.session_id}</span>
              <span>{PROVIDER_WORDS[s.provider] ?? s.provider}</span>
              <span>{s.live ? t("main.folder.live") : t("main.folder.notRunning")}</span>
              <span>{s.relation === "inside" ? t("main.folder.relation.inside", { path: s.cwd }) : t(`main.folder.relation.${s.relation}`)}</span>
              {s.associated.subagents ? <span>{t("main.folder.subagentTranscripts", { count: s.associated.subagents })}</span> : null}
              {s.associated.memory ? <span>{t("main.folder.memoryNotes")}</span> : null}
              <span>{t("main.folders.seen", { when: shortAgo(Date.now() - s.observed_at * 1000, 10_000) })}</span>
            </li>
          ))}
        </ul>
      </section>

      <section className="folder-events" aria-label={t("main.folder.locationHistory")}>
        <h3>{t("main.folder.locationHistory")}</h3>
        <ul>
          {detail.events.map((e) => (
            <li key={e.id}>
              <span>{when(e.at)}</span>{" "}
              <span>{e.event === "availability"
                ? `${availabilityOf(e.prior.availability)} → ${availabilityOf(e.new.availability)}`
                : e.event === "reconnected" ? t("main.folder.reconnectedFrom", { path: String(e.prior.canonical_path ?? "") })
                : codeWords("main.folder.event", e.event) ?? e.event}</span>
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}
