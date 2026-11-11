"""Project candidates from what JARVIS already has (design section 5.4).

It makes candidates, never projects (I9): one per registered SOURCE folder,
named by the folder, with evidence counted from the legacy project strings
(a run's `project_name`, matched through its `project_path` to a folder's
canonical location first, then by key; a workflow's `project`; a message
to a session's `project`; and the memory `projects/*.md` slugs), each read
through its own store (`run_store.list_runs`, `workflow_store.recent`,
`run_store.list_messages`). The old session messages (dropped files, before
the signed route) are not read: no store lists them now. Folders whose names fold to the same key become ONE
candidate listing every such folder, so he says which belong and which is
home. Legacy keys that match no folder become candidates with no folder. A
key already resolved to one of his projects (by alias or name) is not
offered again, and a folder already in a project is left out.

Reading only: the store records the candidates (`offer_candidate`), and an
ignored one stays ignored until its evidence changes.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable, Optional

from jarvis import data_paths, run_store
from jarvis.agent_directory import spoken_key
from jarvis.project_store import Candidate, ProjectStore

EVERY = 1_000_000_000      # every record a store lists


def _legacy_strings() -> Iterable[tuple[str, str, Optional[str]]]:
    """(source, name, path or None) of every record that names a project by
    a string, read through each record's own store."""
    from jarvis import workflow_store
    for r in run_store.list_runs(limit=EVERY):
        yield "run_project", r.get("project_name"), r.get("project_path")
    for w in workflow_store.recent(limit=EVERY):
        yield "workflow_project", w.project, None
    # Messages and session messages, counted by their own store (one read).
    counted = run_store.message_projects()
    for source, table in (("message_project", "messages"), ("session_project", "session_messages")):
        for name, count in counted[table].items():
            for _ in range(int(count)):
                yield source, name, None


def _legacy_rows() -> list[tuple[str, str, Optional[str], int]]:
    """(source, name, path or None, count) from every legacy record there is."""
    out = []
    counted: dict[tuple[str, str, Optional[str]], int] = {}
    for source, name, path in _legacy_strings():
        if name:
            key = (source, str(name), path)
            counted[key] = counted.get(key, 0) + 1
    for (source, name, path), count in counted.items():
        out.append((source, name, path, count))
    notes = data_paths.projects_dir()
    if notes.is_dir():
        for p in sorted(notes.glob("*.md")):
            if not p.is_symlink():
                out.append(("memory_slug", p.stem, None, 1))
    return out


def _canonical(path: Optional[str]) -> Optional[str]:
    if not path:
        return None
    try:
        return os.path.realpath(path)
    except OSError:
        return None


def propose(store: ProjectStore, folders, owner: str, owners: Iterable[str]) -> list[Candidate]:
    """Offer every candidate the folders and legacy strings suggest; returns
    the ones now proposed. `folders` is the folder service (its catalog)."""
    owners = list(owners)
    in_projects = store.linked_folder_ids()
    groups: dict[str, dict] = {}

    listing, cursor = [], None
    while True:
        page = folders.list_folders(role="source", cursor=cursor)
        listing += page["folders"]
        cursor = page.get("next_cursor")
        if not cursor:
            break
    by_path: dict[str, str] = {}
    for f in listing:
        if f["id"] in in_projects or not f["location"].get("local"):
            continue
        key = spoken_key(f["display_name"])
        if not key:
            continue
        g = groups.setdefault(key, {"name": f["display_name"], "folders": [], "legacy": {}})
        g["folders"].append({"folder_id": f["id"], "label": f["display_name"], "part": "source",
                             "path": f["location"].get("canonical_path")})
        if f["location"].get("canonical_path"):
            by_path[f["location"]["canonical_path"]] = key

    for source, name, path, count in _legacy_rows():
        key = spoken_key(name)
        if not key or store.project_for_legacy(owners, source, name) is not None:
            continue
        at = by_path.get(_canonical(path) or "")
        target = at or key
        g = groups.setdefault(target, {"name": name, "folders": [], "legacy": {}})
        legacy = g["legacy"].setdefault((source, key), {"source": source, "key": key, "count": 0})
        legacy["count"] += count

    proposed = []
    for key, g in sorted(groups.items()):
        if store.find_by_key(owners, key):
            continue
        evidence = {"folders": sorted(g["folders"], key=lambda f: f["folder_id"]),
                    "legacy": sorted(g["legacy"].values(), key=lambda x: (x["source"], x["key"]))}
        found = store.offer_candidate(owner, g["name"], evidence)
        if found is not None and found.state == "proposed":
            proposed.append(found)
    return proposed
