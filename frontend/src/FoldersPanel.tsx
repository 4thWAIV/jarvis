/**
 * The right panel's Folders tab: the registered folders as a compact list
 * to choose from, from the same store as the centre's Folders view
 * (folders.ts). A row is the role's mark, the name, the node and the
 * availability in words; the selected folder is marked as it is in the
 * centre. Choosing a row opens it in the centre's Folders view, where its
 * path, contents and sessions are: this list is never a file browser.
 */

import { useState, useSyncExternalStore } from "react";
import { AvailabilityMark, RoleMark } from "./FolderMarks";
import { candidates, catalog, matches, nodeWords, openFolder, selectedFolder } from "./folders";

export function FoldersPanel() {
  const live = useSyncExternalStore(catalog.subscribe, catalog.get);
  const found = useSyncExternalStore(candidates.subscribe, candidates.get);
  const selected = useSyncExternalStore(selectedFolder.subscribe, selectedFolder.get);
  const [search, setSearch] = useState("");
  const folders = (live.value?.folders ?? [])
    .filter((f) => matches(search, f.display_name, f.location.canonical_path));
  const unavailable = (live.value?.folders ?? []).filter((f) => f.location.availability !== "available").length;
  const discovered = found.value?.discovered.length ?? 0;
  return (
    <div className="right-view folders-panel">
      <div className="folders-panel-heading">
        <span>Folders</span>
        {live.value && (
          <span className="folders-panel-count">
            {live.value.folders.length} registered{unavailable ? ` · ${unavailable} unavailable` : ""}
          </span>
        )}
      </div>
      <label className="folder-field folder-search">
        <span className="visually-hidden">Filter folders</span>
        <input type="search" placeholder="Filter" value={search} onChange={(e) => setSearch(e.target.value)} />
      </label>
      {!live.value && (
        <p className="folder-note" role="status">
          {live.error ? `Couldn't read the folders (${live.error}); trying again.` : "Reading the folders…"}
        </p>
      )}
      {live.value && !live.value.folders.length && (
        <p className="folder-note">No folders registered. Open Folders to add one.</p>
      )}
      {live.value && live.value.folders.length > 0 && !folders.length && (
        <p className="folder-note" role="status">No folders match.</p>
      )}
      <ul className="folders-panel-list" aria-label="Registered folders">
        {folders.map((f) => (
          <li key={f.id}>
            <button type="button" className={`folders-panel-row${f.id === selected ? " selected" : ""}`}
                    aria-current={f.id === selected ? "true" : undefined}
                    title={f.location.canonical_path} onClick={() => openFolder(f.id)}>
              <RoleMark role={f.role} short />
              <span className="folders-panel-name">{f.display_name}</span>
              <span className="folders-panel-meta">
                <span>{nodeWords(f)}</span>
                <AvailabilityMark availability={f.location.availability} />
              </span>
            </button>
          </li>
        ))}
      </ul>
      <button type="button" className="folder-button quiet folders-panel-open" onClick={() => openFolder(selected)}>
        Open Folders{discovered ? ` · ${discovered} discovered` : ""}
      </button>
    </div>
  );
}
