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

A move stopped part way (the server stopped, the Mac shut down, Ctrl-C)
never blocks a start: whatever move the engine holds without the cut-over's
record never became JARVIS's records, so it is erased (`knowledge_move.clear`)
before the snapshot and the file is copied again in full, whatever was
written to it in between. That covers a stop during the copy and during
either check. A verification that fails raises `CutoverFailed`, and JARVIS
does not run on records it could not prove: running on half of the records
is worse than not running, and jarvis.db is untouched.

Before anything is written the move is planned (`plan`): the file's tables
and rows, how long each step should take (`STEP_SEC_PER_ROW`, measured on a
real database of 238,208 rows), and the free disk it needs
(`space_needed`). Too little free disk refuses the move (`NotEnoughSpace`)
with how much it needs and how much is free. `cut_over` tells `on_step`
each step as it begins (`STEPS`), which is how the page shows where the
move is and the time left while the server answers (records_move.py).
"""
from __future__ import annotations

import logging
import shutil
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from jarvis import data_paths, records
from jarvis import knowledge_move as move
from jarvis.knowledge.uds import Transaction

log = logging.getLogger("jarvis.records_cutover")

CUTOVER_PATH = "/settings/cutover.settings"


# The steps a move takes, in order, as `on_step` names them.
SPACE, CLEAR, COPY, CHECK, CHECK_BACK = "space", "clear", "copy", "check", "check_back"
STEPS = (SPACE, CLEAR, COPY, CHECK, CHECK_BACK)

# Seconds per row of each step, measured on a real move (238,208 rows of
# 89 tables: copy 57.6 s, check 39.6 s, check back 48.8 s). A clear writes
# and forgets about what a copy wrote.
STEP_SEC_PER_ROW = {CLEAR: 57.6 / 238208, COPY: 57.6 / 238208, CHECK: 39.6 / 238208,
                    CHECK_BACK: 48.8 / 238208}

# The engine holds a moved file in at most this many times its size; the
# move also writes two private copies of the file at once (a snapshot read,
# and the file exported back), and keeps this much spare.
ENGINE_TIMES_FILE = 3
SPARE_BYTES = 512 << 20


class CutoverFailed(RuntimeError):
    """The move could not be proven; JARVIS does not start on it."""


class NotEnoughSpace(CutoverFailed):
    """Too little free disk for the move; nothing was written."""


@dataclass(frozen=True)
class Plan:
    tables: int
    rows: int
    file_bytes: int
    needed_bytes: int
    free_bytes: int
    expected: dict                   # step -> seconds
    clearing: bool                   # a move stopped part way is erased first

    @property
    def expected_total(self) -> float:
        return sum(self.expected.values())

    def as_json(self) -> dict:
        return {"tables": self.tables, "rows": self.rows, "file_bytes": self.file_bytes,
                "needed_bytes": self.needed_bytes, "free_bytes": self.free_bytes,
                "expected": {k: round(v, 1) for k, v in self.expected.items()},
                "expected_total": round(self.expected_total, 1), "clearing": self.clearing}


def space_needed(file_bytes: int) -> int:
    """The free disk a move of a file of `file_bytes` needs."""
    return file_bytes * (ENGINE_TIMES_FILE + 2) + SPARE_BYTES


def free_space(folder: Path) -> int:
    """The free disk where the engine and the temporary copies are written
    (the less of the two folders' free space)."""
    folder = Path(folder)
    while not folder.exists() and folder != folder.parent:
        folder = folder.parent
    return min(shutil.disk_usage(folder).free, shutil.disk_usage(tempfile.gettempdir()).free)


def expected_seconds(rows: int, clearing: bool) -> dict:
    """How long each step should take for `rows` rows."""
    return {step: rows * rate for step, rate in STEP_SEC_PER_ROW.items()
            if step != CLEAR or clearing}


def plan(db_path: Optional[Path] = None, *, uds=None, engine_dir: Optional[Path] = None) -> Plan:
    """The move of `db_path` planned: its size, rows, the time each step
    should take, and the disk it needs against what is free. Reads the file
    read-only and writes nothing."""
    db_path = Path(db_path or data_paths.db_path())
    tables, rows = move.count_rows(db_path)
    size = db_path.stat().st_size + sum(
        p.stat().st_size for p in (Path(str(db_path) + "-wal"),) if p.exists())
    clearing = uds is not None and move.held(uds)
    folder = Path(engine_dir) if engine_dir is not None else data_paths.data_dir()
    return Plan(tables, rows, size, space_needed(size), free_space(folder),
                expected_seconds(rows, clearing), clearing)


def needed(db_path: Optional[Path] = None, uds=None) -> bool:
    """Whether a start must move jarvis.db into the engine first."""
    db_path = Path(db_path or data_paths.db_path())
    return db_path.exists() and done(uds) is None


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


def _quiet(step: str, facts: dict) -> None:
    pass


def cut_over(*, db_path: Optional[Path] = None, uds=None, files_root: Optional[Path] = None,
             snapshot: bool = True, on_step: Callable[[str, dict], None] = _quiet) -> Outcome:
    """Move jarvis.db into the engine once; see the module's words.
    `on_step(step, facts)` is told each step as it begins; the first,
    `SPACE`, carries the plan (`Plan.as_json`)."""
    db_path = Path(db_path or data_paths.db_path())
    uds = uds or records.uds()
    if done(uds) is not None:
        return Outcome("done")
    if not db_path.exists():
        return Outcome("fresh")
    seconds: dict = {}
    started = time.monotonic()
    planned = plan(db_path, uds=uds)
    on_step(SPACE, planned.as_json())
    if planned.free_bytes < planned.needed_bytes:
        raise NotEnoughSpace(
            f"the move needs about {_gb(planned.needed_bytes)} of free disk and "
            f"{_gb(planned.free_bytes)} is free; nothing was written and jarvis.db is untouched")
    if planned.clearing:
        on_step(CLEAR, {})
    cleared = move.clear(uds, files_root=files_root)
    if cleared is not None:
        log.warning("cut-over: a move stopped part way was cleared (%d items, %d files, %.1f s); "
                    "copying again in full", cleared.items, cleared.files, cleared.seconds)
        seconds["cleared"] = round(cleared.seconds, 3)
    t = time.monotonic()
    snap = _snapshot(uds) if snapshot else None
    seconds["snapshot"] = round(time.monotonic() - t, 3)
    log.info("cut-over: moving %s into the engine", db_path)
    on_step(COPY, {})
    t = time.monotonic()
    copied = move.copy(db_path, uds, files_root=files_root)
    seconds["copy"] = round(time.monotonic() - t, 3)
    on_step(CHECK, {})
    t = time.monotonic()
    report = move.verify(db_path, uds, files_root=files_root)
    seconds["verify"] = round(time.monotonic() - t, 3)
    if not report.ok:
        raise CutoverFailed("the records copied into the engine differ from jarvis.db: "
                            + report.words()[:2000])
    on_step(CHECK_BACK, {})
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


def _gb(n: int) -> str:
    return f"{n / (1 << 30):.1f} GB"


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
