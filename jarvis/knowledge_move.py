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
a move left `copying` (stopped part way) is no move: `clear` erases it and
the file is copied again in full, whatever was written to the file in
between; a different `copied` move, or items under `/sqlite/` with no
manifest, is refused (`MoveRefused`) and nothing is written.

`clear` erases a move from the engine as if it had never been written:
every item written since its manifest was begun, under the paths the move
writes (`/sqlite/` and each mapping's `under`), deleted and then forgotten
with every version (`EngineUDS.forget`), so no half-written history stays
behind the next copy; the files it wrote under the files root since then;
the manifest last, so a clear stopped part way is cleared again at the
next start. The file is never touched.

Every call a move makes (copy, verify, export, clear) has its own long
deadline, `MOVE_DEADLINE_SEC`, never the ordinary record deadline: a write
of thousands of items on a busy Mac may take longer than that, and is not
an engine gone. Every write of a copy names the version its path is at
(`expect_version`: 0 before the move writes it, then the version the move
last gave it), so a write tried again can never add a version twice. A
write that still times out or loses its connection is looked for (`_land`):
landed, it is done; not, it is tried again, at most `MOVE_ATTEMPTS` times
with `MOVE_BACKOFF_SEC` between, before the move fails with the reason.
"""
from __future__ import annotations

import base64
import contextlib
import hashlib
import inspect
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

from jarvis.knowledge.uds import KEEP, Caller, Conflict, Stream, Transaction
from jarvis.knowledge_client import EngineUnavailable

ROOT = "/sqlite"
MANIFEST_PATH = ROOT + "/manifest.move"
ROW_TYPE = "row"
MOVE_CALLER = Caller(person="tim", agent="move-kit")
MOVE_CHANGE = "move"
FORMAT = 1
TXN_WRITES_MAX = 1000          # the engine's limit (items.WRITES_MAX)
TXN_BYTES_MAX = 8 << 20        # well under the engine's 64 MB request body
SEGMENT_KEEP_BYTES = 200       # a longer key is named by its hash
MOVE_DEADLINE_SEC = 300.0      # every call a move makes; a forget compacts what held the items
MOVE_ATTEMPTS = 5              # tries of a write that timed out or lost its connection
MOVE_BACKOFF_SEC = (1.0, 2.0, 4.0, 8.0)   # waited before each try after the first
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
    """Tables to documents and back, lossless for the tables it owns.

    A mapping whose rows become Content files on disk (a run's output, the
    voice log) also names `files_under` (folders relative to the files root,
    JARVIS's data folder) and gives `files(conn)`: (relative path, bytes)
    for each file, written by `copy`; its `rows(docs, files)` then takes a
    `Files` to read them back."""
    name: str                       # recorded in the manifest
    tables: tuple[str, ...]         # the tables it owns
    under: tuple[str, ...]          # the path prefixes its documents live under
    history: bool                   # rows() needs each Doc's earlier versions

    def documents(self, conn: sqlite3.Connection) -> Iterator[Doc]: ...

    def rows(self, docs: Iterable[Doc]) -> dict[str, list[Row]]: ...
    # A mapping whose tables changed shape over JARVIS's versions takes
    # `shape` as well (`rows(docs, shape=...)`): the file's own tables and
    # columns, which it gives back (an older file has fewer of either).


@dataclass(frozen=True)
class Shape:
    """The tables a SQLite file has and each one's columns, in order: the
    shape `rows()` gives back for that file. With no `tables`, today's
    (an export with no moved file to match)."""
    tables: Optional[dict] = None          # table -> its columns

    @classmethod
    def of(cls, infos: dict) -> "Shape":
        return cls({t: tuple(i.columns) for t, i in infos.items()})

    def has(self, table: str) -> bool:
        return self.tables is None or table in self.tables

    def columns(self, table: str, today: Iterable[str]) -> tuple[str, ...]:
        """The file's columns of `table`, else `today`'s."""
        if self.tables is None or table not in self.tables:
            return tuple(today)
        return self.tables[table]

    def fit(self, out: dict) -> dict:
        """`out` (table -> rows) with only the tables the file has; one it
        lacks is kept only while it holds rows, so the checks still see
        them."""
        return {t: rows for t, rows in out.items() if self.has(t) or rows}


TODAY = Shape()


class Files:
    """The files a mapping writes under the files root (JARVIS's data
    folder): written whole (a temporary file renamed into place), read, and
    listed under a folder."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def _at(self, rel: str) -> Path:
        rel_path = Path(rel)
        if rel_path.is_absolute() or ".." in rel_path.parts or not rel_path.parts:
            raise MoveError(f"not a file under the files root: {rel!r}")
        return self.root / rel_path

    def write(self, rel: str, data: bytes) -> None:
        path = self._at(rel)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".moving", dir=path.parent)
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
            os.replace(tmp, path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    def read(self, rel: str) -> bytes:
        return self._at(rel).read_bytes()

    def list(self, folder: str) -> list[str]:
        """Every file below `folder`, as relative paths, sorted."""
        base = self._at(folder)
        if not base.is_dir():
            return []
        return sorted(str(p.relative_to(self.root)) for p in base.rglob("*")
                      if p.is_file() and not p.name.endswith(".moving"))


def _files_root(files_root: Optional[Path]) -> Path:
    if files_root is not None:
        return Path(files_root)
    from jarvis import data_paths
    return data_paths.data_dir()


def _rows_of(m, uds, files: "Files", shape: Shape = TODAY) -> dict:
    docs = _engine_docs(uds, m)
    args: tuple = (docs, files) if getattr(m, "files_under", None) else (docs,)
    if "shape" in inspect.signature(m.rows).parameters:
        return m.rows(*args, shape=shape)
    return m.rows(*args)


def _in_file_columns(info: "TableInfo", row: Row) -> Row:
    """A row read back from the engine in the file's columns, when every
    cell of a column the file lacks is NULL (a mapping that gives today's
    columns for an older file); a real value there is left, so it still
    differs."""
    extra = [c for c in row.cells if c not in info.columns]
    if not extra or any(row.cells[c] is not None for c in extra):
        return row
    return Row({c: v for c, v in row.cells.items() if c in info.columns}, row.rowid)


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


PACKAGED_SCHEMA = Path(__file__).with_name("sqlite_schema.json")
"""JARVIS's SQLite schema when its records moved, and each store's schema
version: what export-sqlite builds when the engine holds no move."""

REGISTERED: list[Mapping] = []
"""Mappings registered by hand (tests); the document types' own are in
`RECORD_MODULES`. Every table none owns is moved by `RowMapping`."""

RECORD_MODULES = (
    "jarvis.run_store", "jarvis.work_store", "jarvis.workflow_store", "jarvis.package_inbox",
    "jarvis.agent_key_store", "jarvis.messaging_store", "jarvis.conversation_log",
    "jarvis.messaging_files", "jarvis.attachment_locations", "jarvis.folder_store",
    "jarvis.project_store", "jarvis.attention_store", "jarvis.language_documents",
    "jarvis.usage_history", "jarvis.voice_diagnostics", "jarvis.voice_control",
)
"""The store modules; each gives the mappings of its document types as
`MAPPINGS` (a list), lossless for the tables they own."""


def registered() -> list[Mapping]:
    """Every document type's mapping: each record module's `MAPPINGS`,
    then `REGISTERED`."""
    import importlib
    found: list[Mapping] = []
    for name in RECORD_MODULES:
        found.extend(getattr(importlib.import_module(name), "MAPPINGS", ()))
    return found + list(REGISTERED)


def mappings_for(tables: Iterable[str]) -> list[Mapping]:
    """The registered mappings that own any of `tables`, then a
    `RowMapping` for each table none of them owns."""
    tables = list(tables)
    chosen: list[Mapping] = [m for m in registered() if set(m.tables) & set(tables)]
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


def count_rows(db_path: Path) -> tuple[int, int]:
    """The tables whose rows a move copies and their rows, read from the
    file opened read-only (nothing copied)."""
    conn = sqlite3.connect(f"file:{_url_quote(str(Path(db_path).resolve()))}?mode=ro", uri=True)
    try:
        tables = tables_of(schema_of(conn))
        return len(tables), sum(conn.execute(f"SELECT count(*) FROM {_q(t)}").fetchone()[0]
                                for t in tables)
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
    # A mapping may own a table an older file does not have yet; it reads
    # none of its rows from that file and gives none back.
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


def _patient(uds):
    """The engine as the move calls it: each call given `MOVE_DEADLINE_SEC`."""
    return uds.with_deadline(MOVE_DEADLINE_SEC)


_sleep = time.sleep


def _land(uds, caller: Caller, writes: list[tuple[str, dict, Any]], versions: dict[str, int], *,
          change: str, summary: str, full_sync: bool = False) -> None:
    """Put `writes` (path, data, content) in one transaction, each on the
    condition that its path is still at `versions[path]` (0: not written
    yet), and keep the versions it made in `versions`. A try that times out
    or loses its connection (`EngineUnavailable`) may have landed all the
    same: it is looked for, and tried again only when it is not there; the
    condition refuses a try that would land twice. After `MOVE_ATTEMPTS`
    tries the move fails with the last reason."""
    tx = Transaction()
    for path, data, content in writes:
        tx.put(path, data, content=content, expect_version=versions.get(path, 0))
    last: Optional[BaseException] = None
    for attempt in range(MOVE_ATTEMPTS):
        if attempt:
            _sleep(MOVE_BACKOFF_SEC[min(attempt, len(MOVE_BACKOFF_SEC)) - 1])
            try:
                if _landed(uds, writes, versions):
                    return
            except EngineUnavailable as e:
                last = e
                continue
        try:
            done = uds.transaction(caller, tx, change=change, summary=summary, full_sync=full_sync)
        except EngineUnavailable as e:
            last = e
            continue
        except Conflict:
            # A try that timed out landed after it was looked for.
            if last is not None and _landed(uds, writes, versions):
                return
            raise
        for item in (done or {}).get("items") or []:
            versions[item["path"]] = item["version"]
        return
    raise MoveError(f"a write of {len(writes)} items to the engine failed {MOVE_ATTEMPTS} times; "
                    f"last: {type(last).__name__}: {last}") from last


def _landed(uds, writes: list[tuple[str, dict, Any]], versions: dict[str, int]) -> bool:
    """Whether a transaction of `writes` is in the engine: every path moved
    past the version it was written on (and `versions` brought up to date),
    or none did. Nothing else writes where a move writes, and a transaction
    lands whole, so a part is a fault."""
    now = {}
    for path, _, _ in writes:
        item = uds.get(MOVE_CALLER, path, {Stream.ENTRY})
        now[path] = 0 if item is None else item.version
    moved = [p for p, v in now.items() if v != versions.get(p, 0)]
    if not moved:
        return False
    if len(moved) != len(now):
        raise MoveError(f"only part of a transaction is in the engine ({moved[0]} moved, "
                        f"{next(p for p in now if p not in moved)} did not)")
    versions.update(now)
    return True


def _write_docs(uds, docs: Iterable[Doc], *, batch: int, summary: str) -> int:
    """Write documents in transactions of at most `batch` writes and
    `TXN_BYTES_MAX` bytes; a document's history first, oldest first, each
    version in a transaction of its own writer and change. Returns the
    writes made."""
    written = 0
    pending: list[Doc] = []
    size = 0
    versions: dict[str, int] = {}

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
                    _land(uds, caller, chunk, versions, change=change, summary=words)
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
    # A mapping that shares a folder with another type's documents names its
    # own types, or narrows what it reads under a prefix (`select`: parent,
    # types, where), so the others' documents (and their histories) are
    # never read.
    types = getattr(mapping, "types", None)
    select = getattr(mapping, "select", None) or {}
    for under in mapping.under:
        narrow = dict(select.get(under, {}))
        if types is not None and "types" not in narrow:
            narrow["types"] = list(types)
        for item in uds.scan(MOVE_CALLER, under, limit=1000, streams=streams, **narrow):
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


# -- clearing a move that never finished -----------------------------------------

FORGET_BYTES_MAX = 32 << 20    # paths a forget names: as few compactions as fit a request


def _all_mappings(mappings: Optional[list[Mapping]]) -> list[Mapping]:
    return registered() + list(mappings or ())


def move_prefixes(mappings: Optional[list[Mapping]] = None) -> tuple[str, ...]:
    """Every path prefix a move writes under: `/sqlite/` and the `under` of
    each registered mapping and of `mappings`."""
    found = {ROOT}
    for m in _all_mappings(mappings):
        found.update(m.under)
    return tuple(sorted(found))


def _under_any(path: str, prefixes: Iterable[str]) -> bool:
    return any(path == p or path.startswith(p.rstrip("/") + "/") for p in prefixes)


def _written_since(uds, lsn: int) -> set[str]:
    """Every item path the change feed names after log number `lsn`."""
    out: set[str] = set()
    since = lsn
    while True:
        page = uds.changes(MOVE_CALLER, since, limit=1000)
        for record in page.get("changes") or []:
            out.update(i["path"] for i in record.get("items") or [])
        if page.get("next", since) == since:
            return out
        since = page["next"]


def _byte_chunks(paths: list[str], budget: int) -> Iterator[list[str]]:
    chunk: list[str] = []
    size = 0
    for p in paths:
        if chunk and size + len(p) + 4 > budget:
            yield chunk
            chunk, size = [], 0
        chunk.append(p)
        size += len(p) + 4
    if chunk:
        yield chunk


def held(uds) -> bool:
    """Whether the engine holds a move (its manifest), finished or not."""
    return _manifest(uds) is not None


@dataclass(frozen=True)
class Cleared:
    items: int
    files: int
    seconds: float


def clear(uds, mappings: Optional[list[Mapping]] = None, *,
          files_root: Optional[Path] = None) -> Optional[Cleared]:
    """Erase the move the engine holds (see the module's words); None when
    it holds none."""
    uds = _patient(uds)
    found = _manifest(uds)
    if found is None:
        return None
    started = time.monotonic()
    versions = uds.history(MOVE_CALLER, MANIFEST_PATH, limit=10000)
    begun_lsn = min(v.lsn for v in versions)
    begun_at = min((v.at for v in versions if v.at is not None), default=0.0)
    prefixes = move_prefixes(mappings)
    paths = sorted(p for p in _written_since(uds, begun_lsn - 1)
                   if p != MANIFEST_PATH and _under_any(p, prefixes))
    _erase(uds, paths)
    files = 0
    root = Files(_files_root(files_root))
    folders = {f for m in _all_mappings(mappings) for f in getattr(m, "files_under", ()) or ()}
    for rel in (rel for folder in sorted(folders) for rel in root.list(folder)):
        path = root._at(rel)
        if path.stat().st_mtime >= begun_at - 1:      # written by the move, not before it
            path.unlink(missing_ok=True)
            files += 1
    _erase(uds, [MANIFEST_PATH])
    return Cleared(len(paths), files, time.monotonic() - started)


def _erase(uds, paths: list[str]) -> None:
    for chunk in _chunks([(p, {}, None) for p in paths], TXN_WRITES_MAX):
        tx = Transaction()
        for p, _, _ in chunk:
            tx.delete(p)
        uds.transaction(MOVE_CALLER, tx, change=MOVE_CHANGE, summary="a move stopped part way, cleared")
    for chunk in _byte_chunks(paths, FORGET_BYTES_MAX):
        uds.forget(MOVE_CALLER, chunk)


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
         *, batch: int = TXN_WRITES_MAX, files_root: Optional[Path] = None) -> CopyReport:
    """Copy a SQLite file into the engine through `mappings` (every table
    owned by exactly one; `mappings_for` the file's tables when None). See
    the module's words for the rule on an engine already holding a move."""
    if caller != MOVE_CALLER:
        raise MoveError("the move writes as Tim through the agent move-kit")
    batch = max(1, min(int(batch), TXN_WRITES_MAX))
    uds = _patient(uds)
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
        if found is not None and found.data.get("state") != "copied":
            # Stopped part way: no move. Erased, then copied again in full.
            clear(uds, mappings, files_root=files_root)
            found = None
        if found is None:
            stray = uds.list(caller, ROOT, limit=1, streams={Stream.ENTRY}).items
            if stray:
                raise MoveRefused(f"the engine holds {stray[0].path} with no move manifest; "
                                  "nothing was written")
        else:
            held = {t["name"]: [t["rows"], t["digest"]] for t in found.data.get("tables", [])}
            if held != identity:
                raise MoveRefused("the engine already holds a different finished move (from "
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
        _land(uds, caller, [(MANIFEST_PATH, manifest, KEEP)], {}, change=MOVE_CHANGE,
              summary="move begun", full_sync=True)
        writes = 0
        for m in mappings:
            if getattr(m, "files_under", None):
                files = Files(_files_root(files_root))
                for rel, data in m.files(conn):
                    files.write(rel, data)
            writes += _write_docs(uds, m.documents(conn), batch=batch,
                                  summary=f"moved from {db_path.name}: {', '.join(m.tables)}"[:2000])
    current = _manifest(uds)
    manifest["state"] = "copied"
    manifest["copied_at"] = time.time()
    _land(uds, caller, [(MANIFEST_PATH, manifest, KEEP)], {MANIFEST_PATH: current.version},
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


def _key_order(sortable: str) -> list:
    """A key's place in order: numbers by value (so id 2 before id 1000),
    then everything else by its JSON text."""
    how, values = json.loads(sortable)
    out: list = [how]
    for _, v in values:
        number = isinstance(v, (int, float)) and not isinstance(v, bool)
        out.append((0, v, "") if number else (1, 0, json.dumps(v, sort_keys=True)))
    return out


def _first_difference(info: TableInfo, source: dict, engine: dict) -> Optional[str]:
    for k in sorted(set(source) | set(engine), key=_key_order):
        a, b = source.get(k), engine.get(k)
        if a is None:
            return f"{info.key_words(b[0][0])} (only in the engine)"
        if b is None:
            return f"{info.key_words(a[0][0])} (missing from the engine)"
        if sorted(h for _, h in a) != sorted(h for _, h in b):
            return f"{info.key_words(a[0][0])} (changed)"
    return None


def verify(db_path: Path, uds, mappings: Optional[list[Mapping]] = None, *,
           files_root: Optional[Path] = None) -> Report:
    """The file and the engine, table by table: each table's row count and
    digest read from the file and from the engine through its mapping;
    every table that differs named with its first differing key."""
    uds = _patient(uds)
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
        shape = Shape.of(infos)
        for m in mappings:
            for t, rows in _rows_of(m, uds, Files(_files_root(files_root)), shape).items():
                back.setdefault(t, []).extend(rows)
        for t in tables:
            info = infos[t]
            src_rows = list(read_rows(conn, info))
            eng_rows = [_in_file_columns(info, r) for r in back.get(t, [])]
            sn, sd = table_digest(src_rows)
            en, ed = table_digest(eng_rows)
            first = None
            if (sn, sd) != (en, ed):
                first = _first_difference(info, _keyed(info, src_rows), _keyed(info, eng_rows))
            reports.append(TableReport(t, sn, en, sd, ed, first))
        del owner
    return Report(tuple(reports), tuple(problems), time.monotonic() - started)


def export_sqlite(uds, mappings: Optional[list[Mapping]], out_path: Path, *,
                  schema_from: Optional[Path] = None, files_root: Optional[Path] = None) -> dict:
    """A new SQLite file at `out_path` with the moved file's schema (the
    manifest's, or `schema_from`'s; on an install that never had jarvis.db,
    the schema JARVIS had when its records moved, `PACKAGED_SCHEMA`, with
    each store's schema version) and its rows back from the engine. An
    existing file is never overwritten: the file is built beside it and
    linked into place only while the name is free."""
    uds = _patient(uds)
    started = time.monotonic()
    out_path = Path(out_path)
    if out_path.exists() or out_path.is_symlink():
        raise MoveRefused(f"{out_path} exists; export-sqlite never overwrites a file")
    manifest = _manifest(uds)
    versions: dict = {}
    if schema_from is None and manifest is None:
        packaged = json.loads(PACKAGED_SCHEMA.read_text(encoding="utf-8"))
        schema, infos, versions = packaged["schema"], {}, packaged["schema_versions"]
    elif schema_from is not None:
        with snapshot(Path(schema_from)) as conn:
            schema = schema_of(conn)
            infos = {t: table_info(conn, t) for t in tables_of(schema)}
    else:
        schema = manifest.data["schema"]
        infos = {t["name"]: TableInfo.from_json(t["info"]) for t in manifest.data["tables"]}
    tables = tables_of(schema)
    mappings = mappings if mappings is not None else mappings_for(tables)
    _covered(mappings, tables)
    shape = Shape.of(infos) if infos else TODAY
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
                for t, rows in _rows_of(m, uds, Files(_files_root(files_root)), shape).items():
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
                    if o["name"] not in infos:
                        infos[o["name"]] = table_info(conn, o["name"])
                    rows = back.pop(o["name"], [])
                    if not rows and o["name"] in versions:
                        rows = [Row(dict(zip(infos[o["name"]].columns, v))) for v in versions[o["name"]]]
                    rows_written += _insert(conn, infos[o["name"]], rows)
            internal = {t: back.pop(t) for t in list(back) if t.startswith("sqlite_")}
            # A table an older file never had comes back empty and is not
            # made; one holding rows is refused.
            back = {t: rows for t, rows in back.items() if rows}
            if back:
                raise MoveError("rows for tables the schema does not make: " + ", ".join(back))
            if "sqlite_sequence" in internal:
                if "sqlite_sequence" not in infos:
                    infos["sqlite_sequence"] = table_info(conn, "sqlite_sequence")
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


# -- what the record types share --------------------------------------------------

def row_data(row: Row) -> dict:
    """A row as a document's (or a file line's) fields: each cell under its
    column, kept by `cell`, and `rowid` when the row has one apart from its
    columns."""
    out = {c: cell(v) for c, v in row.cells.items()}
    if row.rowid is not None:
        out["rowid"] = row.rowid
    return out


def data_row(data: dict, columns: Iterable[str]) -> Row:
    """The row `row_data` made `data` from (a column it lacks is NULL)."""
    return Row({c: uncell(data.get(c)) for c in columns}, data.get("rowid"))


def table_rows(conn: sqlite3.Connection, table: str) -> list[Row]:
    """Every row of `table`, with its rowid where it has one apart from its
    columns; none when the file has no such table."""
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is None:
        return []
    return list(read_rows(conn, table_info(conn, table)))


def numbered(rows: list[Row], column: Optional[str] = None) -> list[Row]:
    """`rows` with each one written after the move, which has no rowid (or
    no `column`, its id), given the next after the largest, in the order
    given."""
    def got(r: Row) -> Any:
        return r.rowid if column is None else r.cells.get(column)
    taken = [v for v in map(got, rows) if isinstance(v, int)]
    following = max(taken, default=0) + 1
    out = []
    for r in rows:
        if got(r) is None:
            r = Row(r.cells, following) if column is None else Row({**r.cells, column: following}, r.rowid)
            following += 1
        out.append(r)
    return out
