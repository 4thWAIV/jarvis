"""Rows of the folder catalog's and the project records' old tables, read
from SQLite into plain document fields and given back exactly (the move's
`documents` and `rows`, `knowledge_move`), shared by `folder_store` and
`project_store`.

A row keeps its rowid as the field `rowid` when its table has one apart
from its columns (a TEXT or composite primary key, or none), so it comes
back as the same row; a row written after the move has none, and gets the
next rowid after the largest of its table. Each cell comes back in the
storage class its column's affinity gives it (REAL as a float, INTEGER as
an int), as SQLite would have stored it.
"""
from __future__ import annotations

import json
from typing import Any, Iterable, Optional

from jarvis import knowledge_move


def read(conn, table: str, columns: tuple[str, ...]) -> list[dict]:
    """Every row of `table` as a dict of `columns` (and `rowid` when the
    table keeps one); none when the file has no such table."""
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                    (table,)).fetchone() is None:
        return []
    info = knowledge_move.table_info(conn, table)
    out = []
    for row in knowledge_move.read_rows(conn, info):
        d = {c: row.cells.get(c) for c in columns}
        if row.rowid is not None:
            d["rowid"] = row.rowid
        out.append(d)
    return out


def plain(row: dict, columns: Iterable[str], drop: Iterable[str] = ()) -> dict:
    """The row's fields for a document: `columns` it has, less `drop`."""
    gone = set(drop)
    return {c: row[c] for c in columns if c in row and c not in gone}


def raw(row: dict) -> dict:
    """A row kept whole (one with no record to belong to), every cell in
    its storage class."""
    out = {"cells": {c: knowledge_move.cell(v) for c, v in row.items() if c != "rowid"}}
    if row.get("rowid") is not None:
        out["rowid"] = row["rowid"]
    return out


def json_or_text(text: Any) -> Any:
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return text


def sequences(conn) -> dict[str, int]:
    """`sqlite_sequence`: the largest id each AUTOINCREMENT table gave."""
    if conn.execute("SELECT 1 FROM sqlite_master WHERE name='sqlite_sequence'").fetchone() is None:
        return {}
    return {name: int(seq) for name, seq in conn.execute("SELECT name, seq FROM sqlite_sequence")}


def parent(path: str) -> str:
    return path.rsplit("/", 1)[0] or "/"


def _affine(value: Any, column: str, real: set, integer: set) -> Any:
    if isinstance(value, bool):
        value = int(value)
    if value is None:
        return None
    if column in real and isinstance(value, int):
        return float(value)
    if column in integer and isinstance(value, float) and value.is_integer():
        return int(value)
    return value


class Tables:
    """Rows gathered back from documents, table by table."""

    def __init__(self, tables: dict[str, tuple[str, ...]], real: set, integer: set,
                 aliased: set) -> None:
        self.tables, self.real, self.integer, self.aliased = tables, real, integer, aliased
        self._rows: dict[str, list[tuple[dict, Optional[int]]]] = {t: [] for t in tables}

    def add(self, table: str, fields: dict) -> None:
        cells = {c: _affine(fields.get(c), c, self.real, self.integer) for c in self.tables[table]}
        rowid = None if table in self.aliased else fields.get("rowid")
        self._rows[table].append((cells, rowid))

    def add_raw(self, table: str, kept: dict) -> None:
        cells = {c: knowledge_move.uncell(v) for c, v in kept["cells"].items()}
        self._rows.setdefault(table, []).append((cells, kept.get("rowid")))

    def rows(self) -> dict[str, list]:
        out: dict[str, list] = {}
        for table, found in self._rows.items():
            if table in self.aliased:
                out[table] = [knowledge_move.Row(cells) for cells, _ in found]
                continue
            top = max((r for _, r in found if r is not None), default=0)
            rows = []
            for cells, rowid in found:
                if rowid is None:
                    top += 1
                    rowid = top
                rows.append(knowledge_move.Row(cells, rowid))
            out[table] = rows
        return out
