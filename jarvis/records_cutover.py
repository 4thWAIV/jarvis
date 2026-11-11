"""The cut-over: JARVIS's SQLite file into the engine, once, at the first
start after the records moved (round 3 of
docs/planning/knowledge-engine/plan-uds.md).

At startup, after the engine is up and before any store is touched,
`cut_over()` looks for `data_paths.db_path()` (jarvis.db). With none there
is nothing to move (a new install). With one, and no finished move in the
engine, it:

1. takes a snapshot of the engine (`/v1/snapshot`), so the state before the
   move is kept;
2. copies the file into the engine through every document type's mapping
   (`knowledge_move.copy`; the file is opened read-only and copied with
   SQLite's backup API, never written);
3. verifies both ways: every table read back from the engine equals the
   file's (`knowledge_move.verify`), and a file exported from the engine
   (`export_sqlite`, into a private temporary folder, then deleted) equals
   the file table by table, so the way back is proven before JARVIS runs on
   the engine;
4. records the cut-over at `CUTOVER_PATH` (the source's SHA-256, when, how
   long each step took), committed with a full flush.

jarvis.db is left exactly where it was, untouched, beside the engine's
snapshots; `python -m jarvis.knowledge export-sqlite --out <file>` writes
the records back to a SQLite file for the way back.

A move stopped part way is copied again in full at the next start (the
move kit's rule). A verification that fails raises `CutoverFailed`, and the
server does not start on records it could not prove: running on half of
Tim's records is worse than not starting, and jarvis.db is untouched.
"""
from __future__ import annotations

import logging
import shutil
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from jarvis import data_paths, records
from jarvis import knowledge_move as move
from jarvis.knowledge.uds import Transaction

log = logging.getLogger("jarvis.records_cutover")

CUTOVER_PATH = "/settings/cutover.settings"


class CutoverFailed(RuntimeError):
    """The move could not be proven; JARVIS does not start on it."""


@dataclass
class Outcome:
    state: str                       # "fresh", "done" (earlier), or "cut over"
    seconds: dict = field(default_factory=dict)
    rows: int = 0
    tables: int = 0
    source_sha256: Optional[str] = None
    snapshot: Optional[str] = None


def done(uds=None) -> Optional[dict]:
    """The cut-over's record, or None before it."""
    item = (uds or records.uds()).get(records.JARVIS, CUTOVER_PATH)
    return None if item is None else dict(item.data or {})


def _snapshot(uds) -> Optional[str]:
    try:
        status, payload = uds.transport.request("POST", "/v1/snapshot", {}, 120.0)
    except Exception:
        log.exception("cut-over: the snapshot before the move failed")
        raise
    if status >= 400:
        raise CutoverFailed(f"the engine refused the snapshot before the move ({status})")
    return (payload or {}).get("snapshot") or (payload or {}).get("path")


def cut_over(*, db_path: Optional[Path] = None, uds=None, files_root: Optional[Path] = None,
             snapshot: bool = True) -> Outcome:
    """Move jarvis.db into the engine once; see the module's words."""
    db_path = Path(db_path or data_paths.db_path())
    uds = uds or records.uds()
    if done(uds) is not None:
        return Outcome("done")
    if not db_path.exists():
        return Outcome("fresh")
    seconds: dict = {}
    started = time.monotonic()
    snap = _snapshot(uds) if snapshot else None
    seconds["snapshot"] = round(time.monotonic() - started, 3)
    log.info("cut-over: moving %s into the engine", db_path)
    t = time.monotonic()
    copied = move.copy(db_path, uds, files_root=files_root)
    seconds["copy"] = round(time.monotonic() - t, 3)
    t = time.monotonic()
    report = move.verify(db_path, uds, files_root=files_root)
    seconds["verify"] = round(time.monotonic() - t, 3)
    if not report.ok:
        raise CutoverFailed("the records copied into the engine differ from jarvis.db: "
                            + report.words()[:2000])
    t = time.monotonic()
    work = Path(tempfile.mkdtemp(prefix="jarvis-cutover-"))
    try:
        back = work / "back.db"
        move.export_sqlite(uds, None, back, files_root=files_root)
        report_back = _same_tables(db_path, back)
        if report_back:
            raise CutoverFailed("a file exported from the engine differs from jarvis.db: "
                                + "; ".join(report_back)[:2000])
    finally:
        shutil.rmtree(work, ignore_errors=True)
    seconds["verify_back"] = round(time.monotonic() - t, 3)
    seconds["total"] = round(time.monotonic() - started, 3)
    record = {"source": str(db_path.resolve()), "source_sha256": copied.source_sha256,
              "tables": copied.tables, "rows": copied.rows, "writes": copied.writes,
              "cut_over_at": time.time(), "seconds": seconds, "snapshot": snap,
              "kept": "jarvis.db is left untouched beside the engine's snapshots"}
    uds.transaction(records.JARVIS, Transaction().put(CUTOVER_PATH, record, absent=True),
                    change="cut-over", summary=f"moved {copied.rows} rows of {copied.tables} tables",
                    full_sync=True)
    log.info("cut-over: %d rows of %d tables moved and proven both ways in %.1f s",
             copied.rows, copied.tables, seconds["total"])
    return Outcome("cut over", seconds, copied.rows, copied.tables, copied.source_sha256, snap)


def _same_tables(a: Path, b: Path) -> list[str]:
    """The tables whose rows differ between two SQLite files (count and
    order-independent digest), named."""
    out: list[str] = []
    with move.snapshot(a) as ca, move.snapshot(b) as cb:
        ta = move.tables_of(move.schema_of(ca))
        tb = set(move.tables_of(move.schema_of(cb)))
        for t in ta:
            if t not in tb:
                out.append(f"{t} missing")
                continue
            da = move.table_digest(move.read_rows(ca, move.table_info(ca, t)))
            db_ = move.table_digest(move.read_rows(cb, move.table_info(cb, t)))
            if da != db_:
                out.append(f"{t}: {da[0]} rows here, {db_[0]} back")
    return out
