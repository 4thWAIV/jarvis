"""Moving JARVIS's SQLite tables into the knowledge engine and back: the
move kit (round 2, `move-kit`, of docs/planning/knowledge-engine/plan-uds.md).

Round 3 moves every table of `jarvis.db` into the engine as redesigned
document types and cuts over once: `copy` the file into the engine,
`verify` it, run on the engine, keep `jarvis.db` untouched; `export_sqlite`
writes today's tables back from the engine through the same mappings, so
the move can be undone. This module is that kit; the types plug into it.

A `Mapping` owns some tables. `documents(conn)` reads them from a SQLite
connection and gives `Doc`s (a path, its data, optional content, and the
earlier versions it should carry, `Doc.history`, oldest first: a table
whose event rows become a record's versions fills it); `rows(docs)` turns
the documents read back from the engine into the same rows. Both must be
lossless for the tables it owns: `verify` checks exactly that. Round 3
registers its types in `REGISTERED`; every table no registered mapping
covers is moved by `RowMapping`.

`RowMapping(table)` is the generic, lossless mapping: each row one item at
`/sqlite/<table>/<key>.row` whose data is the table's name, the row's
rowid where the row has one apart from its columns, and its cells by
column, each kept in its SQLite storage class (INTEGER and TEXT as
themselves, REAL as a float, NULL as null; a BLOB as `{"blob": base64}`,
TEXT that is not UTF-8 as `{"text": base64}`, an infinite REAL as
`{"real": "inf"}`). The key is the primary key (several columns joined
with commas; WITHOUT ROWID tables alike), the rowid for a table with none
or a row whose key holds a NULL. Each key part is written so two keys
never share a path (`key_segment`).

`copy` never writes the source: it opens it read-only and takes a
consistent copy with SQLite's backup API (the WAL included) into a private
temporary folder, and reads that. It writes the manifest
(`MANIFEST_PATH`) first, as `copying`, then the documents in transactions
of at most `items.WRITES_MAX` writes and `TXN_BYTES_MAX` bytes, then the
manifest as `copied`: the source file's SHA-256, its schema as
`sqlite_master` holds it, `user_version`, `application_id` and its text
encoding, and per table its row count and an order-independent digest of
its canonical rows (`table_digest`). Every write is by `MOVE_CALLER` (Tim,
through the agent `move-kit`) with change kind `move`, unless a version in
a `Doc.history` names its own writer and change.

The rule for an engine that already holds a move (`copy`): the same move
(every table's count and digest equal) already `copied` is left as it is;
the same move left `copying` (stopped part way) is written again in full;
a different move, or items under `/sqlite/` with no manifest, is refused
(`MoveRefused`) and nothing is written.
"""
from __future__ import annotations

import base64
import contextlib
import hashlib
import json
import math
import os
import re
import sqlite3
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator, Optional, Protocol
from urllib.parse import quote as _url_quote

from jarvis.knowledge.uds import Caller, Stream, Transaction

ROOT = "/sqlite"
MANIFEST_PATH = ROOT + "/manifest.move"
ROW_TYPE = "row"
MOVE_CALLER = Caller(person="tim", agent="move-kit")
MOVE_CHANGE = "move"
FORMAT = 1
TXN_WRITES_MAX = 1000          # the engine's limit (items.WRITES_MAX)
TXN_BYTES_MAX = 8 << 20        # well under the engine's 64 MB request body
SEGMENT_KEEP_BYTES = 200       # a longer key is named by its hash
_ROWID_NAMES = ("rowid", "_rowid_", "oid")
_SAFE = re.compile(r"[A-Za-z0-9_-]")
_INTEGER = re.compile(r"-?[0-9]+\Z")
_STATS = ("sqlite_stat1", "sqlite_stat4")


class MoveError(RuntimeError):
    """The move cannot go on; the words say why."""


class MoveRefused(MoveError):
    """The engine already holds something this copy would overwrite."""


class RawText(bytes):
    """A TEXT value whose bytes are not UTF-8, kept as its bytes."""


# -- cells: one SQLite value in JSON, its storage class kept -----------------

def storage_class(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, RawText) or isinstance(value, str):
        return "text"
    if isinstance(value, bytes):
        return "blob"
    if isinstance(value, bool):
        raise TypeError("SQLite has no boolean")
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "real"
    raise TypeError(f"not a SQLite value: {type(value).__name__}")


def cell(value: Any) -> Any:
    """A SQLite value as JSON, its storage class told apart: an int, a
    float, a str or null as themselves; everything JSON cannot say plainly
    as a one-field object."""
    if isinstance(value, RawText):
        return {"text": base64.b64encode(bytes(value)).decode("ascii")}
    if isinstance(value, bytes):
        return {"blob": base64.b64encode(value).decode("ascii")}
    if isinstance(value, float) and not math.isfinite(value):
        return {"real": "inf" if value > 0 else "-inf"}
    storage_class(value)
    return value


def uncell(raw: Any) -> Any:
    if isinstance(raw, dict):
        if len(raw) == 1 and "blob" in raw:
            return base64.b64decode(raw["blob"])
        if len(raw) == 1 and "text" in raw:
            return RawText(base64.b64decode(raw["text"]))
        if len(raw) == 1 and "real" in raw and raw["real"] in ("inf", "-inf"):
            return math.inf if raw["real"] == "inf" else -math.inf
        raise MoveError(f"not a cell: {raw!r}"[:200])
    if isinstance(raw, bool) or not (raw is None or isinstance(raw, (int, float, str))):
        raise MoveError(f"not a cell: {raw!r}"[:200])
    return raw


def _text_factory(raw: bytes) -> Any:
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return RawText(raw)


# -- rows ---------------------------------------------------------------------

@dataclass(frozen=True)
class Row:
    """One row: its cells by column (generated columns left out: SQLite
    makes them again) and its rowid when it has one apart from its
    columns."""
    cells: dict
    rowid: Optional[int] = None

    def canonical(self) -> bytes:
        body = {"cells": {k: cell(v) for k, v in self.cells.items()}}
        if self.rowid is not None:
            body["rowid"] = self.rowid
        return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                          allow_nan=False).encode("utf-8")


def _q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


@dataclass(frozen=True)
class TableInfo:
    """What the kit needs to know of one table: its columns in order, its
    primary key, and how its rows are told apart."""
    name: str
    columns: tuple[str, ...]
    key: tuple[str, ...]              # primary key columns, in key order
    without_rowid: bool
    rowid_alias: bool                 # an INTEGER PRIMARY KEY: the key is the rowid
    rowid_name: Optional[str]         # how to name the rowid, when it is kept apart

    @property
    def keeps_rowid(self) -> bool:
        return self.rowid_name is not None

    def as_json(self) -> dict:
        return {"name": self.name, "columns": list(self.columns), "key": list(self.key),
                "without_rowid": self.without_rowid, "rowid_alias": self.rowid_alias,
                "rowid_name": self.rowid_name}

    @classmethod
    def from_json(cls, raw: dict) -> "TableInfo":
        return cls(raw["name"], tuple(raw["columns"]), tuple(raw["key"]), bool(raw["without_rowid"]),
                   bool(raw["rowid_alias"]), raw.get("rowid_name"))

    def row_key(self, row: Row) -> tuple[str, tuple]:
        """("key", values) by the primary key; ("rowid", (n,)) for a table
        with none; ("nullkey", (rowid,)) for a row of a rowid table whose
        key holds a NULL (SQLite lets those repeat)."""
        if self.key:
            values = tuple(row.cells[c] for c in self.key)
            if self.without_rowid or all(v is not None for v in values):
                return "key", values
            return "nullkey", (row.rowid,)
        return "rowid", (row.rowid,)

    def key_words(self, key: tuple[str, tuple]) -> str:
        how, values = key
        if how == "rowid":
            return f"rowid={values[0]}"
        if how == "nullkey":
            return f"rowid={values[0]} (its key holds a NULL)"
        return ", ".join(f"{c}={v!r}" for c, v in zip(self.key, values))


def table_info(conn: sqlite3.Connection, name: str) -> TableInfo:
    sql = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone()
    if sql and sql[0] and re.match(r"\s*CREATE\s+VIRTUAL\s", sql[0], re.I):
        raise MoveError(f"{name} is a virtual table; the kit moves ordinary tables only")
    cols = conn.execute(f"PRAGMA table_xinfo({_q(name)})").fetchall()
    columns = tuple(c[1] for c in cols if c[6] == 0)
    key = tuple(c[1] for c in sorted((c for c in cols if c[5]), key=lambda c: c[5]))
    without_rowid = False
    try:
        listed = conn.execute(f"PRAGMA table_list({_q(name)})").fetchall()
        without_rowid = any(r[1] == name and r[4] for r in listed)
    except sqlite3.OperationalError:
        without_rowid = bool(sql and sql[0] and re.search(r"WITHOUT\s+ROWID\s*;?\s*\Z", sql[0], re.I))
    decl = {c[1]: (c[2] or "") for c in cols}
    alias = not without_rowid and len(key) == 1 and decl[key[0]].strip().upper() == "INTEGER"
    rowid_name = None
    if not without_rowid and not alias:
        lowered = {c.lower() for c in (c[1] for c in cols)}
        rowid_name = next((n for n in _ROWID_NAMES if n not in lowered), None)
        if rowid_name is None and not key:
            raise MoveError(f"{name} has no primary key and its rowid is hidden by its columns")
    return TableInfo(name, columns, key, without_rowid, alias, rowid_name)


def read_rows(conn: sqlite3.Connection, info: TableInfo) -> Iterator[Row]:
    """Every row of a table, in key order."""
    cols = ", ".join(_q(c) for c in info.columns)
    head = f"{info.rowid_name}, " if info.keeps_rowid else ""
    order = ", ".join(_q(c) for c in info.key) if info.without_rowid else (
        info.rowid_name or "rowid")
    for r in conn.execute(f"SELECT {head}{cols} FROM {_q(info.name)} ORDER BY {order}"):
        if info.keeps_rowid:
            yield Row(dict(zip(info.columns, r[1:])), r[0])
        else:
            yield Row(dict(zip(info.columns, r)))


def table_digest(rows: Iterable[Row]) -> tuple[int, str]:
    """(count, digest): the SHA-256 of the sorted hashes of each row's
    canonical form, so it does not depend on the order rows come in."""
    hashes = sorted(hashlib.sha256(r.canonical()).digest() for r in rows)
    h = hashlib.sha256()
    for one in hashes:
        h.update(one)
    return len(hashes), h.hexdigest()


# -- paths ----------------------------------------------------------------------

def _escape(text: str) -> str:
    return "".join(c if _SAFE.match(c) else _url_quote(c, safe="") if ord(c) > 0x7F
                   else "%%%02X" % ord(c) for c in text)


def _part(value: Any) -> str:
    if isinstance(value, RawText):
        return "!t" + bytes(value).hex()
    if isinstance(value, bytes):
        return "!b" + value.hex()
    if value is None:
        return "!n"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return "!r" + _escape(repr(value))
    text = _escape(value)
    # An empty text, or one that reads as a number, is marked so it never
    # names the same item as an INTEGER key ('%' and '!' are escaped in text).
    return "'" + text if text == "" or _INTEGER.match(text) else text


def key_segment(key: tuple[str, tuple]) -> str:
    """A key as one path segment, never shared by two keys: INTEGER parts
    as numbers, TEXT escaped to [A-Za-z0-9_-] and %XX (with a leading `'`
    when it would read as a number or be empty), other classes marked with
    `!`, parts joined with `,` (escaped in text); a table with no
    primary key is keyed by its rowid, and a row whose key holds a NULL by
    `!rowid=<n>`. Past
    `SEGMENT_KEEP_BYTES` the segment is `!sha256=<hash of the parts>`."""
    how, values = key
    if how == "rowid":
        seg = str(values[0])
    elif how == "nullkey":
        seg = f"!rowid={values[0]}"
    else:
        seg = ",".join(_part(v) for v in values)
    if len(seg.encode("ascii")) > SEGMENT_KEEP_BYTES:
        seg = "!sha256=" + hashlib.sha256(seg.encode("ascii")).hexdigest()
    return seg


def table_segment(table: str) -> str:
    seg = _escape(table)
    if len(seg) > SEGMENT_KEEP_BYTES:
        seg = "!sha256=" + hashlib.sha256(seg.encode("ascii")).hexdigest()
    return seg


def table_root(table: str) -> str:
    return f"{ROOT}/{table_segment(table)}"


# -- documents and mappings -----------------------------------------------------

@dataclass(frozen=True)
class Revision:
    """One earlier version of a document. Written by the move's caller with
    change `move` unless it names its own; read back, `version` and `at`
    say which version it was and when the engine wrote it (the engine
    stamps the time of a write; a version's own time belongs in its data)."""
    data: dict
    content: Optional[dict] = None
    by: Optional[Caller] = None
    change: Optional[str] = None
    summary: Optional[str] = None
    version: Optional[int] = None
    at: Optional[float] = None


@dataclass(frozen=True)
class Doc:
    """One document a mapping makes: written as its `history`, oldest
    first, then itself as the current version."""
    path: str
    data: dict
    content: Optional[dict] = None
    history: tuple[Revision, ...] = ()
    version: Optional[int] = None


class Mapping(Protocol):
    """Tables to documents and back, lossless for the tables it owns."""
    name: str                       # recorded in the manifest
    tables: tuple[str, ...]         # the tables it owns
    under: tuple[str, ...]          # the path prefixes its documents live under
    history: bool                   # rows() needs each Doc's earlier versions

    def documents(self, conn: sqlite3.Connection) -> Iterator[Doc]: ...

    def rows(self, docs: Iterable[Doc]) -> dict[str, list[Row]]: ...


class RowMapping:
    """One row, one item at `/sqlite/<table>/<key>.row`, every cell kept."""

    history = False

    def __init__(self, table: str, info: Optional[TableInfo] = None) -> None:
        self.table = table
        self.name = "row"
        self.tables = (table,)
        self.under = (table_root(table),)
        self._info = info

    def info(self, conn: sqlite3.Connection) -> TableInfo:
        if self._info is None:
            self._info = table_info(conn, self.table)
        return self._info

    def documents(self, conn: sqlite3.Connection) -> Iterator[Doc]:
        info = self.info(conn)
        root = table_root(self.table)
        for row in read_rows(conn, info):
            data: dict = {"table": self.table, "cells": {c: cell(v) for c, v in row.cells.items()}}
            if row.rowid is not None:
                data["rowid"] = row.rowid
            yield Doc(f"{root}/{key_segment(info.row_key(row))}.{ROW_TYPE}", data)

    def rows(self, docs: Iterable[Doc]) -> dict[str, list[Row]]:
        out: list[Row] = []
        for doc in docs:
            if doc.data.get("table") != self.table:
                raise MoveError(f"{doc.path} is not a row of {self.table}")
            out.append(Row({c: uncell(v) for c, v in doc.data["cells"].items()}, doc.data.get("rowid")))
        return {self.table: out}


REGISTERED: list[Mapping] = []
"""Round 3's document types, each owning its tables; every other table is
moved by `RowMapping`."""


def mappings_for(tables: Iterable[str]) -> list[Mapping]:
    """The registered mappings that own any of `tables`, then a
    `RowMapping` for each table none of them owns."""
    tables = list(tables)
    chosen: list[Mapping] = [m for m in REGISTERED if set(m.tables) & set(tables)]
    owned = {t for m in chosen for t in m.tables}
    return chosen + [RowMapping(t) for t in tables if t not in owned]


# -- the source ---------------------------------------------------------------

def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


@contextlib.contextmanager
def snapshot(db_path: Path) -> Iterator[sqlite3.Connection]:
    """A consistent read-only copy of a SQLite file: opened read-only,
    copied with the backup API (its WAL included) into a private temporary
    folder, and that copy opened. The source is never written."""
    db_path = Path(db_path)
    if not db_path.is_file():
        raise MoveError(f"no SQLite file at {db_path}")
    with tempfile.TemporaryDirectory(prefix="jarvis-move-") as tmp:
        os.chmod(tmp, 0o700)
        copy_path = Path(tmp) / "snapshot.db"
        src = sqlite3.connect(f"file:{_url_quote(str(db_path.resolve()))}?mode=ro", uri=True)
        try:
            dst = sqlite3.connect(copy_path)
            try:
                src.backup(dst)
            finally:
                dst.close()
        finally:
            src.close()
        conn = sqlite3.connect(f"file:{_url_quote(str(copy_path))}?mode=ro", uri=True)
        conn.text_factory = _text_factory
        try:
            yield conn
        finally:
            conn.close()


def schema_of(conn: sqlite3.Connection) -> dict:
    """The CREATE statements in `sqlite_master` order, and the header values
    an export sets again."""
    objects = [{"type": t, "name": n, "tbl_name": tn, "sql": s} for t, n, tn, s in conn.execute(
        "SELECT type, name, tbl_name, sql FROM sqlite_master ORDER BY rowid")]
    return {"objects": objects,
            "user_version": conn.execute("PRAGMA user_version").fetchone()[0],
            "application_id": conn.execute("PRAGMA application_id").fetchone()[0],
            "encoding": conn.execute("PRAGMA encoding").fetchone()[0]}


def tables_of(schema: dict) -> list[str]:
    """Every table whose rows move: the ordinary ones and `sqlite_sequence`
    and the statistics tables, which SQLite makes itself."""
    return [o["name"] for o in schema["objects"] if o["type"] == "table"
            and not (o["sql"] or "").lstrip().upper().startswith("CREATE VIRTUAL")]


def _covered(mappings: list[Mapping], tables: list[str]) -> dict[str, Mapping]:
    owner: dict[str, Mapping] = {}
    for m in mappings:
        for t in m.tables:
            if t in owner:
                raise MoveError(f"{t} is owned by two mappings ({owner[t].name}, {m.name})")
            owner[t] = m
    missing = [t for t in tables if t not in owner]
    if missing:
        raise MoveError("no mapping owns " + ", ".join(missing))
    extra = [t for t in owner if t not in tables]
    if extra:
        raise MoveError("the mappings name tables the file does not have: " + ", ".join(extra))
    return owner


def source_tables(conn: sqlite3.Connection, tables: list[str]) -> dict[str, dict]:
    """Per table: its info, row count and digest, read from the source."""
    out = {}
    for t in tables:
        info = table_info(conn, t)
        rows, digest = table_digest(read_rows(conn, info))
        out[t] = {"info": info, "rows": rows, "digest": digest}
    return out


# -- the engine ---------------------------------------------------------------

def _manifest(uds) -> Optional[Any]:
    return uds.get(MOVE_CALLER, MANIFEST_PATH, {Stream.ENTRY, Stream.DATA})


def _write_docs(uds, docs: Iterable[Doc], *, batch: int, summary: str) -> int:
    """Write documents in transactions of at most `batch` writes and
    `TXN_BYTES_MAX` bytes; a document's history first, oldest first, each
    version in a transaction of its own writer and change. Returns the
    writes made."""
    written = 0
    pending: list[Doc] = []
    size = 0

    def flush() -> None:
        nonlocal written, pending, size
        if not pending:
            return
        depth = max(len(d.history) for d in pending)
        for wave in range(depth + 1):
            groups: dict[tuple, list[tuple[str, dict, Any]]] = {}
            for d in pending:
                back = depth - wave           # the current version lands in the last wave
                if back > len(d.history):
                    continue
                if back == 0:
                    key = (MOVE_CALLER, MOVE_CHANGE, summary)
                    groups.setdefault(key, []).append((d.path, d.data, d.content))
                else:
                    r = d.history[len(d.history) - back]
                    key = (r.by or MOVE_CALLER, r.change or MOVE_CHANGE, r.summary or summary)
                    groups.setdefault(key, []).append((d.path, r.data, r.content))
            for (caller, change, words), writes in groups.items():
                for chunk in _chunks(writes, batch):
                    tx = Transaction()
                    for path, data, content in chunk:
                        tx.put(path, data, content=content)
                    uds.transaction(caller, tx, change=change, summary=words)
                    written += len(chunk)
        pending, size = [], 0

    for d in docs:
        weight = len(json.dumps(d.data, separators=(",", ":"))) + (
            len(json.dumps(d.content)) if d.content else 0)
        weight *= 1 + len(d.history)
        if pending and (len(pending) >= batch or size + weight > TXN_BYTES_MAX):
            flush()
        pending.append(d)
        size += weight
    flush()
    return written


def _chunks(writes: list, batch: int) -> Iterator[list]:
    chunk: list = []
    size = 0
    for w in writes:
        weight = len(json.dumps(w[1], separators=(",", ":"))) + (len(json.dumps(w[2])) if w[2] else 0)
        if chunk and (len(chunk) >= batch or size + weight > TXN_BYTES_MAX):
            yield chunk
            chunk, size = [], 0
        chunk.append(w)
        size += weight
    if chunk:
        yield chunk


def _engine_docs(uds, mapping: Mapping) -> Iterator[Doc]:
    streams = {Stream.ENTRY, Stream.DATA, Stream.CONTENT, Stream.SECTIONS}
    for under in mapping.under:
        for item in uds.scan(MOVE_CALLER, under, limit=1000, streams=streams):
            content = _content_of(item)
            history: tuple[Revision, ...] = ()
            if mapping.history:
                history = tuple(_revisions(uds, item.path, item.version))
            yield Doc(item.path, dict(item.data or {}), content, history, item.version)


def _content_of(item) -> Optional[dict]:
    if item.content is None:
        return None
    out = dict(item.content)
    if item.sections is not None:
        out["sections"] = [{"heading": s.heading, "text": s.text} for s in item.sections]
    return out


def _revisions(uds, path: str, current: int) -> Iterator[Revision]:
    streams = {Stream.ENTRY, Stream.DATA, Stream.CONTENT, Stream.SECTIONS}
    for v in sorted(uds.history(MOVE_CALLER, path, limit=10000), key=lambda v: v.version or 0):
        if v.version is None or v.version >= current or v.op == "delete":
            continue
        item = uds.get(MOVE_CALLER, path, streams, version=v.version)
        if item is None:
            continue
        by = Caller(v.by, v.agent, v.model) if v.by else None
        yield Revision(dict(item.data or {}), _content_of(item), by, v.change, v.summary, v.version, v.at)


# -- copy, verify, export -------------------------------------------------------

@dataclass(frozen=True)
class CopyReport:
    state: str                  # "copied", or "already" when the same move was there
    tables: int
    rows: int
    writes: int
    seconds: float
    source_sha256: str


def copy(db_path: Path, uds, mappings: Optional[list[Mapping]] = None, caller: Caller = MOVE_CALLER,
         *, batch: int = TXN_WRITES_MAX) -> CopyReport:
    """Copy a SQLite file into the engine through `mappings` (every table
    owned by exactly one; `mappings_for` the file's tables when None). See
    the module's words for the rule on an engine already holding a move."""
    if caller != MOVE_CALLER:
        raise MoveError("the move writes as Tim through the agent move-kit")
    batch = max(1, min(int(batch), TXN_WRITES_MAX))
    started = time.monotonic()
    db_path = Path(db_path)
    with snapshot(db_path) as conn:
        source_sha = file_sha256(db_path)
        schema = schema_of(conn)
        tables = tables_of(schema)
        mappings = mappings if mappings is not None else mappings_for(tables)
        owner = _covered(mappings, tables)
        source = source_tables(conn, tables)
        identity = {t: [s["rows"], s["digest"]] for t, s in source.items()}
        found = _manifest(uds)
        if found is None:
            stray = uds.list(caller, ROOT, limit=1, streams={Stream.ENTRY}).items
            if stray:
                raise MoveRefused(f"the engine holds {stray[0].path} with no move manifest; "
                                  "nothing was written")
        else:
            held = {t["name"]: [t["rows"], t["digest"]] for t in found.data.get("tables", [])}
            if held != identity:
                raise MoveRefused("the engine already holds a different move (from "
                                  f"{found.data.get('source', {}).get('path')}); nothing was written")
            if found.data.get("state") == "copied":
                return CopyReport("already", len(tables), sum(s["rows"] for s in source.values()), 0,
                                  time.monotonic() - started, found.data["source"]["sha256"])
        manifest = {
            "format": FORMAT, "state": "copying",
            "source": {"path": str(db_path.resolve()), "sha256": source_sha,
                       "bytes": db_path.stat().st_size},
            "schema": schema,
            "tables": [{"name": t, "mapping": owner[t].name, "rows": source[t]["rows"],
                        "digest": source[t]["digest"], "info": source[t]["info"].as_json()}
                       for t in tables],
            "copied_at": None,
        }
        tx = Transaction()
        if found is None:
            tx.put(MANIFEST_PATH, manifest, absent=True)
        else:
            tx.put(MANIFEST_PATH, manifest, expect_version=found.version)
        uds.transaction(caller, tx, change=MOVE_CHANGE, summary="move begun", full_sync=True)
        writes = 0
        for m in mappings:
            writes += _write_docs(uds, m.documents(conn), batch=batch,
                                  summary=f"moved from {db_path.name}: {', '.join(m.tables)}"[:2000])
    current = _manifest(uds)
    manifest["state"] = "copied"
    manifest["copied_at"] = time.time()
    uds.transaction(caller, Transaction().put(MANIFEST_PATH, manifest, expect_version=current.version),
                    change=MOVE_CHANGE, summary="move copied", full_sync=True)
    return CopyReport("copied", len(tables), sum(s["rows"] for s in source.values()), writes,
                      time.monotonic() - started, source_sha)


@dataclass(frozen=True)
class TableReport:
    name: str
    source_rows: int
    engine_rows: int
    source_digest: str
    engine_digest: str
    first_difference: Optional[str] = None   # the first key that differs, and how

    @property
    def equal(self) -> bool:
        return self.source_rows == self.engine_rows and self.source_digest == self.engine_digest


@dataclass(frozen=True)
class Report:
    tables: tuple[TableReport, ...]
    problems: tuple[str, ...] = ()
    seconds: float = 0.0

    @property
    def ok(self) -> bool:
        return not self.problems and all(t.equal for t in self.tables)

    @property
    def differing(self) -> list[str]:
        return [t.name for t in self.tables if not t.equal]

    def words(self) -> str:
        if self.ok:
            return f"equal: {len(self.tables)} tables, {sum(t.source_rows for t in self.tables)} rows"
        lines = list(self.problems)
        for t in self.tables:
            if not t.equal:
                lines.append(f"{t.name}: {t.source_rows} rows in the file, {t.engine_rows} in the engine; "
                             f"first difference {t.first_difference}")
        return "\n".join(lines)


def _keyed(info: TableInfo, rows: Iterable[Row]) -> dict:
    out: dict = {}
    for r in rows:
        k = info.row_key(r)
        out.setdefault(_sortable(k), []).append((k, hashlib.sha256(r.canonical()).digest()))
    return out


def _sortable(key: tuple[str, tuple]) -> str:
    how, values = key
    return json.dumps([how, [[storage_class(v), cell(v)] for v in values]], sort_keys=True)


def _first_difference(info: TableInfo, source: dict, engine: dict) -> Optional[str]:
    for k in sorted(set(source) | set(engine)):
        a, b = source.get(k), engine.get(k)
        if a is None:
            return f"{info.key_words(b[0][0])} (only in the engine)"
        if b is None:
            return f"{info.key_words(a[0][0])} (missing from the engine)"
        if sorted(h for _, h in a) != sorted(h for _, h in b):
            return f"{info.key_words(a[0][0])} (changed)"
    return None


def verify(db_path: Path, uds, mappings: Optional[list[Mapping]] = None) -> Report:
    """The file and the engine, table by table: each table's row count and
    digest read from the file and from the engine through its mapping;
    every table that differs named with its first differing key."""
    started = time.monotonic()
    problems: list[str] = []
    reports: list[TableReport] = []
    with snapshot(Path(db_path)) as conn:
        schema = schema_of(conn)
        tables = tables_of(schema)
        mappings = mappings if mappings is not None else mappings_for(tables)
        owner = _covered(mappings, tables)
        manifest = _manifest(uds)
        if manifest is None:
            problems.append("the engine holds no move manifest")
        else:
            if manifest.data.get("state") != "copied":
                problems.append(f"the move is {manifest.data.get('state')}, not copied")
            if manifest.data.get("schema", {}).get("objects") != schema["objects"]:
                problems.append("the file's schema is not the one the move recorded")
        infos = {t: table_info(conn, t) for t in tables}
        back: dict[str, list[Row]] = {}
        for m in mappings:
            for t, rows in m.rows(_engine_docs(uds, m)).items():
                back.setdefault(t, []).extend(rows)
        for t in tables:
            info = infos[t]
            src_rows = list(read_rows(conn, info))
            eng_rows = back.get(t, [])
            sn, sd = table_digest(src_rows)
            en, ed = table_digest(eng_rows)
            first = None
            if (sn, sd) != (en, ed):
                first = _first_difference(info, _keyed(info, src_rows), _keyed(info, eng_rows))
            reports.append(TableReport(t, sn, en, sd, ed, first))
        del owner
    return Report(tuple(reports), tuple(problems), time.monotonic() - started)


def export_sqlite(uds, mappings: Optional[list[Mapping]], out_path: Path, *,
                  schema_from: Optional[Path] = None) -> dict:
    """A new SQLite file at `out_path` with the moved file's schema (the
    manifest's, or `schema_from`'s) and its rows back from the engine. An
    existing file is never overwritten: the file is built beside it and
    linked into place only while the name is free."""
    started = time.monotonic()
    out_path = Path(out_path)
    if out_path.exists() or out_path.is_symlink():
        raise MoveRefused(f"{out_path} exists; export-sqlite never overwrites a file")
    manifest = _manifest(uds)
    if manifest is None:
        raise MoveError("the engine holds no move manifest")
    if schema_from is not None:
        with snapshot(Path(schema_from)) as conn:
            schema = schema_of(conn)
            infos = {t: table_info(conn, t) for t in tables_of(schema)}
    else:
        schema = manifest.data["schema"]
        infos = {t["name"]: TableInfo.from_json(t["info"]) for t in manifest.data["tables"]}
    tables = tables_of(schema)
    mappings = mappings if mappings is not None else mappings_for(tables)
    _covered(mappings, tables)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=out_path.name + ".", suffix=".building", dir=out_path.parent)
    os.close(fd)
    tmp = Path(tmp_name)
    tmp.unlink()
    rows_written = 0
    try:
        conn = sqlite3.connect(tmp, isolation_level=None)
        try:
            conn.execute(f"PRAGMA encoding = '{schema['encoding']}'")
            # CHECK constraints held when each row was first written; the
            # rows come back exactly as they are.
            conn.execute("PRAGMA ignore_check_constraints = ON")
            conn.execute("BEGIN")
            back: dict[str, list[Row]] = {}
            for m in mappings:
                for t, rows in m.rows(_engine_docs(uds, m)).items():
                    back.setdefault(t, []).extend(rows)
            # The objects in the source's order, so `sqlite_master` reads the
            # same. Each table's rows go in as soon as it is made, before any
            # trigger on it exists (a trigger comes after its table), so no
            # trigger runs on the rows coming back.
            for o in schema["objects"]:
                if not o["sql"] or o["name"].startswith("sqlite_"):
                    continue
                conn.execute(o["sql"])
                if o["type"] == "table":
                    rows_written += _insert(conn, infos[o["name"]], back.pop(o["name"], []))
            internal = {t: back.pop(t) for t in list(back) if t.startswith("sqlite_")}
            if back:
                raise MoveError("rows for tables the schema does not make: " + ", ".join(back))
            if "sqlite_sequence" in internal:
                conn.execute("DELETE FROM sqlite_sequence")
                rows_written += _insert(conn, infos["sqlite_sequence"], internal["sqlite_sequence"])
            stats = [t for t in _STATS if t in internal]
            if stats:
                conn.execute("ANALYZE")
                for t in _STATS:
                    if conn.execute("SELECT 1 FROM sqlite_master WHERE name=?", (t,)).fetchone():
                        conn.execute(f"DELETE FROM {t}")
                for t in stats:
                    rows_written += _insert(conn, infos[t], internal[t])
            conn.execute(f"PRAGMA user_version = {int(schema['user_version'])}")
            conn.execute(f"PRAGMA application_id = {int(schema['application_id'])}")
            conn.execute("COMMIT")
        finally:
            conn.close()
        os.link(tmp, out_path)      # refuses a name taken meanwhile
    except FileExistsError:
        raise MoveRefused(f"{out_path} exists; export-sqlite never overwrites a file")
    finally:
        tmp.unlink(missing_ok=True)
    return {"path": str(out_path), "tables": len(tables), "rows": rows_written,
            "seconds": time.monotonic() - started}


def _insert(conn: sqlite3.Connection, info: TableInfo, rows: list[Row]) -> int:
    by_shape: dict[tuple, list[tuple]] = {}
    for r in rows:
        raw = tuple(c for c in info.columns if isinstance(r.cells.get(c), RawText))
        by_shape.setdefault((r.rowid is not None, raw), []).append(
            ((r.rowid,) if r.rowid is not None else ()) + tuple(
                bytes(v) if isinstance(v, RawText) else v for v in (r.cells[c] for c in info.columns)))
    for (with_rowid, raw), values in by_shape.items():
        names = ([info.rowid_name] if with_rowid else []) + [_q(c) for c in info.columns]
        marks = (["?"] if with_rowid else []) + ["CAST(? AS TEXT)" if c in raw else "?" for c in info.columns]
        conn.executemany(f"INSERT INTO {_q(info.name)} ({', '.join(names)}) VALUES ({', '.join(marks)})",
                         values)
    return len(rows)
