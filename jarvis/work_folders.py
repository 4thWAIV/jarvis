"""Every Work item moved into its own folder: from `/work/<id>.work` to
`/work/<id>/<id>.work` (work_store.path_of), with its whole history.

A Work item's history is its document's versions and the engine has no
move, so each item is moved by `records.move`: every version copied in
order to the new path, the old path deleted, the counts and the newest
version checked. It runs at every start, before anything reads a Work item
(`server._start_on_records`); once every item is in its folder there is
nothing to move and it reads one short list. A start stopped part way
takes each item up where it stopped. Before the first item moves the
engine takes a snapshot (`records_cutover.take_snapshot`), the copy to go back
to; the move can also be run back (`python -m jarvis.work_folders back`,
with JARVIS stopped and the code from before the move), and `check` says
where every item is. Project works at `/work/works/` are not Work items and
are never moved: a Work item's id starts `work-`, so no item's folder is
`/work/works`.
"""
from __future__ import annotations

import logging
import sys
import time
from dataclasses import dataclass, field
from typing import Optional

from jarvis import records, work_store

log = logging.getLogger("jarvis.work_folders")


@dataclass
class Moved:
    """What a move did: how many items and versions, and the snapshot."""
    items: int = 0
    versions: int = 0
    snapshot: Optional[str] = None
    seconds: float = 0.0
    ids: list = field(default_factory=list)


@dataclass(frozen=True)
class Where:
    """Every Work item by where it is: in its folder, at its old path."""
    in_folders: tuple
    at_old_paths: tuple


def where() -> Where:
    """Each live Work item's id, by where it is now."""
    new, old = [], []
    for rec in records.scan(work_store.ROOT, types=[work_store.TYPE]):
        if not work_store._is_item(rec.data):
            continue
        item_id = str(rec.data["id"])
        if rec.path == work_store.path_of(item_id):
            new.append(item_id)
        elif rec.path == work_store.old_path_of(item_id):
            old.append(item_id)
    return Where(tuple(sorted(new)), tuple(sorted(old)))


def move(*, back: bool = False, snapshot: bool = True) -> Moved:
    """Every Work item at its old path into its folder (`back`: every item
    in its folder back to its old path), then checked: none left where it
    was, each readable where it went."""
    started = time.monotonic()
    before = where()
    todo = before.in_folders if back else before.at_old_paths
    out = Moved()
    if not todo:
        return out
    if snapshot:
        from jarvis import records_cutover
        out.snapshot = records_cutover.take_snapshot(records.uds())
    pairs = [(work_store.old_path_of(i), work_store.path_of(i)) for i in todo]
    if back:
        pairs = [(dst, src) for src, dst in pairs]
    # Paths gone to before (a move back's, or forward again after one) are
    # forgotten together: one forget compacts what held them all, where
    # one a path would compact the engine once for each.
    gone = [dst for _, dst in pairs if records.get(dst) is None and records.history(dst, limit=1)]
    if gone:
        records.uds().forget(records.JARVIS, gone, deadline=records.FORGET_DEADLINE_SEC)
    for item_id, (src, dst) in zip(todo, pairs):
        out.versions += records.move(src, dst)
        out.items += 1
        out.ids.append(item_id)
    after = where()
    left = after.at_old_paths if not back else after.in_folders
    there = set(after.in_folders if not back else after.at_old_paths)
    if left or not set(todo) <= there:
        raise records.MoveFailed(
            f"after the move {len(left)} Work item(s) are still where they were and "
            f"{len(set(todo) - there)} are not where they went")
    out.seconds = round(time.monotonic() - started, 3)
    log.info("work folders: %d Work item(s) moved %s with %d versions in %.1f s (snapshot %s)",
             out.items, "back" if back else "into their folders", out.versions, out.seconds,
             out.snapshot)
    return out


def main(argv: Optional[list[str]] = None) -> int:
    """`python -m jarvis.work_folders [check|forward|back]` (JARVIS stopped).
    It opens the engine of its own data folder in this process
    (`records.opened_at`), never one an address file names, so a copy of a
    data folder is moved, never the engine a copied address points at; the
    engine's lock refuses while JARVIS runs there."""
    args = list(sys.argv[1:] if argv is None else argv) or ["check"]
    if args[0] not in ("check", "forward", "back"):
        print("usage: python -m jarvis.work_folders [check|forward|back]", file=sys.stderr)
        return 2
    from jarvis import data_paths
    from jarvis.knowledge.engine import EngineLocked
    try:
        with records.opened_at(data_paths.knowledge_dir().resolve()):
            if args[0] != "check":
                done = move(back=args[0] == "back")
                print(f"moved {done.items} Work item(s), {done.versions} versions; snapshot {done.snapshot}")
            now = where()
            print(f"in their folders: {len(now.in_folders)}; at the old paths: {len(now.at_old_paths)}")
    except EngineLocked:
        print("the knowledge engine is running there: stop JARVIS first", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
