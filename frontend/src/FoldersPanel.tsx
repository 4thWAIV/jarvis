/**
 * The right panel's Folders tab: the registered folders as a compact list
 * to choose from, from the same store as the centre's Folders view
 * (folders.ts). A row is the role's mark, the name, the node and the
 * availability in words; the selected folder is marked as it is in the
 * centre. Choosing a row opens it in the centre's Folders view, where its
 * path, contents and sessions are: this list is never a file browser.
 *
 * A row runs `folders.open` and Open Folders runs `folders.open` for the
 * selected folder or `main.tab.select` for the catalog, the same commands
 * the brain's requests run. The list (`foldersList`: its filter and Open
 * Folders) and each row (`folder`: row, name, availability) are targets the
 * brain can point at. Under them, his projects and the projects proposed
 * from these folders (ProjectsSection.tsx).
 */

import { useLayoutEffect, useSyncExternalStore } from "react";
import { RIGHT_SOCKET, row } from "./uiRight";
import { AvailabilityMark, RoleMark } from "./FolderMarks";
import { ProjectsSection } from "./ProjectsSection";
import { candidates, catalog, matches, nodeWords, selectedFolder, type Folder } from "./folders";
import { useT } from "./locale";
import { createStore } from "./store";
import { bind, bindingProps, runBinding } from "./uiNavigation";
import { registerRevealer } from "./uiNavigationReveal";
import { registerCommand, useUiTarget } from "./uiRegistry";

/** The filter's words: emptied each time the tab leaves the page, and
 *  changed only by `folders.panel.search`, the box and the brain alike. */
const search = createStore("");

registerCommand("folders.panel.search", {
  availability: () => ({ available: true }),
  execute({ text }) { search.set(text ?? ""); return { status: "completed", effects: ["list.filtered"] }; },
});

const shownBy = (text: string, f: Folder) => matches(text, f.display_name, f.location.canonical_path);

// Pointing at a folder whose row is not on the page once the tab shows:
// why not. The filter he typed is not cleared to show it.
registerRevealer("right.folders", "folder", async (ref) => {
  const now = catalog.get().value;
  if (!now) return "not-loaded";
  const folder = now.folders.find((f) => f.id === ref.key);
  if (!folder) return "entity-gone";
  return shownBy(search.get(), folder) ? true : "hidden";
});

export function FoldersPanel() {
  const t = useT();
  const live = useSyncExternalStore(catalog.subscribe, catalog.get);
  const found = useSyncExternalStore(candidates.subscribe, candidates.get);
  const selected = useSyncExternalStore(selectedFolder.subscribe, selectedFolder.get);
  const text = useSyncExternalStore(search.subscribe, search.get);
  const ui = useUiTarget({ ref: { region: "right", view: "right.folders", kind: "foldersList" },
                           label: t("targets.registeredFolders"), socket: RIGHT_SOCKET });
  useLayoutEffect(() => () => search.set(""), []);
  const folders = (live.value?.folders ?? []).filter((f) => shownBy(text, f));
  const unavailable = (live.value?.folders ?? []).filter((f) => f.location.availability !== "available").length;
  const discovered = found.value?.discovered.length ?? 0;
  return (
    <div className="right-view folders-panel" ref={ui.whole}>
      <div className="folders-panel-heading">
        <span>{t("tabs.right.folders")}</span>
        {live.value && (
          <span className="folders-panel-count">
            {t("right.folders.count", { count: live.value.folders.length })}
            {unavailable ? <> · {t("right.folders.unavailable", { count: unavailable })}</> : null}
          </span>
        )}
      </div>
      <label className="folder-field folder-search" ref={ui.part("search")}>
        <span className="visually-hidden">{t("right.folders.filterLabel")}</span>
        <input type="search" placeholder={t("right.folders.filterPlaceholder")} value={text} data-ui-command="folders.panel.search"
               onChange={(e) => runBinding(e.target.value ? bind("folders.panel.search", { text: e.target.value })
                                                          : bind("folders.panel.search", {}))} />
      </label>
      {!live.value && (
        <p className="folder-note" role="status">
          {live.error ? t("right.folders.readFailed", { error: live.error }) : t("right.folders.reading")}
        </p>
      )}
      {live.value && !live.value.folders.length && (
        <p className="folder-note">{t("right.folders.none")}</p>
      )}
      {live.value && live.value.folders.length > 0 && !folders.length && (
        <p className="folder-note" role="status">{t("right.folders.noMatch")}</p>
      )}
      <ul className="folders-panel-list" aria-label={t("right.folders.listLabel")}>
        {folders.map((f) => <FolderRow key={f.id} folder={f} selected={f.id === selected} />)}
      </ul>
      <button type="button" className="folder-button quiet folders-panel-open" ref={ui.part("open")}
              {...bindingProps(selected !== null ? bind("folders.open", { folderId: selected })
                                                 : bind("main.tab.select", { tab: "folders" }))}>
        {discovered ? t("right.folders.openDiscovered", { count: discovered }) : t("right.folders.open")}
      </button>
      <ProjectsSection />
    </div>
  );
}

/** One registered folder's row, and its presentation for the brain. */
function FolderRow({ folder: f, selected }: { folder: Folder; selected: boolean }) {
  const ui = useUiTarget({ ref: { region: "right", view: "right.folders", kind: "folder", key: f.id },
                           label: f.display_name, socket: RIGHT_SOCKET });
  return (
    <li>
      <button type="button" className={`folders-panel-row${selected ? " selected" : ""}`}
              aria-current={selected ? "true" : undefined} ref={row(ui)}
              title={f.location.canonical_path} {...bindingProps(bind("folders.open", { folderId: f.id }))}>
        <RoleMark role={f.role} short />
        <span className="folders-panel-name" ref={ui.part("name")}>{f.display_name}</span>
        <span className="folders-panel-meta">
          <span>{nodeWords(f)}</span>
          <span className="folders-panel-availability" ref={ui.part("availability")}>
            <AvailabilityMark availability={f.location.availability} />
          </span>
        </span>
      </button>
    </li>
  );
}
