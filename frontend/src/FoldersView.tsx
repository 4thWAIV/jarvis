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
  availabilityWords, candidateAction, candidates, catalog, folderRefusal, matches, nodeWords,
  PROVIDER_WORDS, readerWords, registerFolder, resolvePath, ROLES, roleWords, selectedFolder,
  type Availability, type Candidate, type Folder, type Resolved, type Role,
} from "./folders";
import { SaidText, t as tNow, useT, type Said } from "./locale";
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
  return at === null ? tNow("main.folders.never") : shortAgo(Date.now() - at * 1000, 10_000);
}

function CatalogRow({ f, selected }: { f: Folder; selected: boolean }) {
  const t = useT();
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
        <span className="folder-row-seen">{t("main.folders.checked", { when: ago(f.location.last_checked_at) })}</span>
      </button>
    </li>
  );
}

function CandidateRow({ c }: { c: Candidate }) {
  const t = useT();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<Said | null>(null);
  const act = async (work: () => Promise<unknown>) => {
    setBusy(true);
    setError(null);
    try {
      await work();
    } catch (e) {
      setError(folderRefusal(e));
    } finally {
      setBusy(false);
    }
  };
  const who = c.providers.map((p) => PROVIDER_WORDS[p] ?? p).join(", ");
  const ui = useUiTarget({ ref: { region: "main", view: "main.folders", kind: "folderCandidate", key: c.id },
                           label: t("main.folders.discoveredFolder"), socket: CENTRE_SOCKET });
  return (
    <li className="candidate-row" ref={(el) => { ui.whole(el); ui.part("row")(el); }}>
      <div className="candidate-head">
        <RoleMark role={c.proposed_role} />
        <span className="candidate-path" title={c.path} ref={ui.part("path")}>{c.path}</span>
      </div>
      <div className="candidate-meta">
        <AvailabilityMark availability={c.availability} />
        <span>{t("main.folders.sessions", { count: c.sessions })}{who ? ` · ${who}` : ""}</span>
        <span>{t("main.folders.seen", { when: ago(c.last_observed_at) })}</span>
      </div>
      {c.refusal && <p className="folder-note">{t("main.folders.notReadable")}</p>}
      {c.access && !c.refusal && <p className="folder-note">{t("main.folders.wouldReadAs", { reader: readerWords(c.access) })}</p>}
      <div className="candidate-actions" ref={ui.part("actions")}>
        {c.state === "discovered" && (
          <>
            <button type="button" className="folder-button" disabled={busy || !!c.refusal || c.availability !== "available"}
                    onClick={() => act(async () => {
                      const done = await registerFolder(c.path, c.access!.reader);
                      runBinding(bind("folders.open", { folderId: done.folder.id }));
                    })}>
              {t("main.folders.register")}
            </button>
            <button data-ui-action="domain" type="button" className="folder-button quiet" disabled={busy}
                    onClick={() => act(() => candidateAction(c, "ignore"))}>{t("main.folders.ignore")}</button>
          </>
        )}
        {c.state === "ignored" && (
          <button data-ui-action="domain" type="button" className="folder-button quiet" disabled={busy}
                  onClick={() => act(() => candidateAction(c, "restore"))}>{t("main.folders.restore")}</button>
        )}
      </div>
      {error && <SaidText said={error} as="p" className="folder-error" role="alert" />}
    </li>
  );
}

/** Add existing folder: a path, checked with the server first, then saved
 *  as shown, or refused with the server's own words. */
function AddFolder({ onDone }: { onDone: () => void }) {
  const t = useT();
  const [path, setPath] = useState("");
  const [name, setName] = useState("");
  const [checked, setChecked] = useState<Resolved | null>(null);
  const [error, setError] = useState<Said | null>(null);
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
      setError(folderRefusal(err));
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
      setError(folderRefusal(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <form data-ui-action="domain" className="folder-add" onSubmit={check} aria-label={t("main.folders.addExisting")}>
      <label className="folder-field">
        <span>{t("main.folders.pathField")}</span>
        <input data-ui-action="input" type="text" value={path} spellCheck={false} autoFocus
               onChange={(e) => { setPath(e.target.value); setChecked(null); }} />
      </label>
      <div className="folder-add-actions">
        <button type="submit" className="folder-button" disabled={busy || !path.trim()}>{t("main.folders.check")}</button>
        <button type="button" className="folder-button quiet"
                {...bindingProps(bind("folders.add.open", { open: false }))}>{t("main.common.cancel")}</button>
      </div>
      {checked && (
        <div className="folder-add-checked" role="status">
          <dl className="folder-facts">
            <dt>{t("main.folders.resolvesTo")}</dt><dd className="folder-path">{checked.canonical_path}</dd>
            <dt>{t("main.folders.readAs")}</dt><dd>{readerWords(checked.access)}</dd>
            <dt>{t("main.folders.roleLabel")}</dt><dd><RoleMark role={checked.proposed_role} /></dd>
          </dl>
          {checked.existing?.lifecycle === "active" && (
            <p className="folder-note">{t("main.folders.alreadyRegistered", { name: checked.existing.display_name })}</p>
          )}
          {checked.existing?.lifecycle === "removed" && (
            <p className="folder-note">{t("main.folders.removedBefore")}</p>
          )}
          {checked.existing?.lifecycle !== "active" && (
            <>
              <label className="folder-field">
                <span>{t("main.folders.nameLabel")}</span>
                <input data-ui-action="input" type="text" value={name} onChange={(e) => setName(e.target.value)} />
              </label>
              <p className="folder-note">{t("main.folders.registering")}</p>
              <button data-ui-action="domain" type="button" className="folder-button" disabled={busy} onClick={save}>
                {checked.existing ? t("main.folders.registerAgain") : t("main.folders.register")}
              </button>
            </>
          )}
        </div>
      )}
      {error && <SaidText said={error} as="p" className="folder-error" role="alert" />}
    </form>
  );
}

const AVAILABILITIES: Availability[] = ["available", "missing", "access_denied", "unavailable_node"];

export function FoldersView() {
  useTick(30000);
  const t = useT();
  const live = useSyncExternalStore(catalog.subscribe, catalog.get);
  const found = useSyncExternalStore(candidates.subscribe, candidates.get);
  const selected = useSyncExternalStore(selectedFolder.subscribe, selectedFolder.get);
  const { view, search, role, availability, removed: showRemoved, ignored: showIgnored, adding } =
    useSyncExternalStore(listState.subscribe, listState.get);
  const ui = useUiTarget({ ref: { region: "main", view: "main.folders", kind: "foldersList" },
                           label: t("main.folders.catalog"), socket: CENTRE_SOCKET });

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
      <section className="folders-catalog" aria-label={t("main.folders.catalog")} ref={ui.whole}>
        <div className="folders-toolbar">
          <label className="folder-field folder-search" ref={ui.part("search")}>
            <span className="visually-hidden">{t("main.folders.searchLabel")}</span>
            <input type="search" placeholder={t("main.folders.searchPlaceholder")} value={search} maxLength={200}
                   data-ui-command="folders.search"
                   onChange={(e) => runBinding(e.target.value ? bind("folders.search", { text: e.target.value })
                                                              : bind("folders.search", {}))} />
          </label>
          <button type="button" className="folder-button" aria-expanded={adding} ref={ui.part("add")}
                  {...bindingProps(bind("folders.add.open", { open: !adding }))}>{t("main.folders.addExisting")}</button>
        </div>
        <div className="folders-tabs" role="tablist" aria-label={t("main.folders.whichFolders")}>
          {(["registered", "discovered"] as ListView[]).map((v) => (
            <button key={v} type="button" role="tab" aria-selected={view === v} ref={ui.part(v)}
                    className={`folder-tab${view === v ? " selected" : ""}`}
                    {...bindingProps(bind("folders.list", { view: v }))}>
              {v === "registered" ? t("main.folders.registeredCount", { n: live.value?.folders.length ?? 0 })
                                  : t("main.folders.discoveredCount", { n: found.value?.discovered.length ?? 0 })}
            </button>
          ))}
        </div>
        <div className="folders-filters">
          <label className="folder-field" ref={ui.part("role")}>
            <span>{t("main.folders.roleLabel")}</span>
            <select value={role} data-ui-command="folders.filter"
                    onChange={(e) => filter({ role: (e.target.value || "any") as Role | "any" })}>
              <option value="">{t("main.folders.allRoles")}</option>
              {ROLES.map((r) => <option key={r} value={r}>{roleWords(r)}</option>)}
            </select>
          </label>
          <label className="folder-field" ref={ui.part("availability")}>
            <span>{t("main.folders.availabilityLabel")}</span>
            <select value={availability} data-ui-command="folders.filter"
                    onChange={(e) => filter({ availability: (e.target.value || "any") as CommandArgs["folders.filter"]["availability"] })}>
              <option value="">{t("main.folders.anyAvailability")}</option>
              {AVAILABILITIES.map((a) => <option key={a} value={a}>{availabilityWords(a)}</option>)}
            </select>
          </label>
          {view === "registered" ? (
            <label className="folder-check" ref={ui.part("removed")}>
              <input type="checkbox" checked={showRemoved} data-ui-command="folders.filter"
                     onChange={(e) => filter({ removed: e.target.checked })} />
              {t("main.folders.showRemoved")}
            </label>
          ) : (
            <label className="folder-check" ref={ui.part("ignored")}>
              <input type="checkbox" checked={showIgnored} data-ui-command="folders.filter"
                     onChange={(e) => filter({ ignored: e.target.checked })} />
              {t("main.folders.showIgnored")}
            </label>
          )}
        </div>
        {adding && <AddFolder onDone={() => {
          runBinding(bind("folders.add.open", { open: false }));
          runBinding(bind("folders.list", { view: "registered" }));
        }} />}
        <div className="folders-list" role="tabpanel" ref={ui.part("list")} aria-label={view === "registered" ? t("main.folders.registered") : t("main.folders.discovered")}>
          {view === "registered" && (
            <>
              {!live.value && <p className="folder-note" role="status">{live.error ? t("main.folders.catalogUnreadable", { error: live.error }) : t("main.folders.readingCatalog")}</p>}
              {live.value && !live.value.folders.length && !showRemoved && (
                <p className="folder-note">{t("main.folders.noneRegistered")}</p>
              )}
              {live.value && all.length > 0 && !folders.length && <p className="folder-note" role="status">{t("main.folders.noneMatch")}</p>}
              {live.value && !live.value.complete && <p className="folder-note">{t("main.folders.showingFirst", { count: live.value.folders.length })}</p>}
              <ul className="folder-rows">
                {folders.map((f) => <CatalogRow key={f.id} f={f} selected={f.id === selected} />)}
              </ul>
            </>
          )}
          {view === "discovered" && (
            <>
              <p className="folder-note">{t("main.folders.discoveredNote")}</p>
              {!found.value && <p className="folder-note" role="status">{found.error ? t("main.folders.discoveryUnreadable", { error: found.error }) : t("main.folders.readingDiscovery")}</p>}
              {found.value && (
                <p className="folder-note" role="status">
                  {failedSource ? t("main.folders.discoveryFailed", { source: failedSource.source, error: failedSource.error ?? "" })
                    : pending ? t("main.folders.discoveryPartial", { count: pending })
                    : found.value.coverage.sources.length ? t("main.folders.discoveryCurrent") : t("main.folders.discoveryNotRun")}
                </p>
              )}
              {found.value && !discovered.length && <p className="folder-note">{t("main.folders.noneDiscovered")}</p>}
              <ul className="folder-rows">
                {discovered.map((c) => <CandidateRow key={c.id} c={c} />)}
              </ul>
            </>
          )}
        </div>
      </section>
      <section className="folders-detail" aria-label={t("main.folders.selected")}>
        {selected ? <FolderDetail key={selected} id={selected} />
                  : <p className="folder-note folder-empty">{t("main.folders.selectOne")}</p>}
      </section>
    </div>
  );
}
