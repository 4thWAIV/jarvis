"""Changing a Work item's, workflow's or package-inbox entry's document
directly, as a test sets a record up the store would never write that way
(a time moved back, a state from before a rule): one write of the
document, as the store's own writers do."""
from __future__ import annotations

from typing import Callable

from jarvis import package_inbox, records, work_store, workflow_store


def _edit(path: str, change: Callable[[dict], None]) -> dict:
    def apply(data):
        assert data is not None, f"no record at {path}"
        data["events"] = []
        change(data)
        if path.startswith(work_store.ROOT + "/"):
            data["held"] = any(True for _ in work_store._held(data))
        if path.startswith(workflow_store.ROOT + "/"):
            data["linked"] = any(True for _ in workflow_store._linked_waits(data))
        return data
    return records.update(path, apply)


def item(item_id: str, **fields) -> dict:
    """Set fields of a Work item's own."""
    return _edit(work_store.path_of(item_id), lambda d: d.update(fields))


def piece(item_id: str, key: str, **fields) -> dict:
    """Set fields of one of a Work item's pieces."""
    def change(d):
        [found] = [p for p in d["pieces"] if p["key"] == key]
        found.update(fields)
    return _edit(work_store.path_of(item_id), change)


def pieces(item_id: str, **fields) -> dict:
    """Set fields of every piece of a Work item."""
    return _edit(work_store.path_of(item_id), lambda d: [p.update(fields) for p in d["pieces"]])


def decision(item_id: str, key: str, **fields) -> dict:
    """Set fields of one of a Work item's decisions."""
    def change(d):
        [found] = [x for x in d["decisions"] if x["key"] == key]
        found.update(fields)
    return _edit(work_store.path_of(item_id), change)


def workflow(workflow_id: str, change: Callable[[dict], None]) -> dict:
    """Change a workflow's document."""
    return _edit(workflow_store.path_of(workflow_id), change)


def entry(inbox_id: str, **fields) -> dict:
    """Set fields of a package-inbox entry."""
    return _edit(package_inbox.path_of(inbox_id), lambda d: d.update(fields))
