"""The move kit (`jarvis/knowledge_move.py`): SQLite into the engine and
back, lossless table by table.

A hand-made database with every storage class, NULLs, blobs, REAL
precision, composite and WITHOUT ROWID keys, a table with no primary key,
an index, a trigger, a view and `user_version` goes into an in-process
engine, verifies, and comes back as a file equal to the source in schema
and in every table. Then the same on a database built by JARVIS's own
schema creators (`run_store.init_db`, the stores' migrations through
`run_store.migrate`). Verify names a changed and a missing row; copy
refuses an engine that holds a different move; the CLI runs the three
commands against an engine folder.
"""
from __future__ import annotations

import json
import sqlite3
import time
from contextlib import closing
from pathlib import Path

import pytest

from jarvis import knowledge_move as move
from jarvis.knowledge.uds import Transaction, in_process


def _source(path: Path) -> Path:
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE things (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            amount REAL,
            body BLOB,
            loose,
            flag INTEGER DEFAULT 0
        );
        CREATE INDEX idx_things_name ON things(name);
        CREATE TABLE pairs (
            project TEXT NOT NULL,
            seq INTEGER NOT NULL,
            note,
            PRIMARY KEY (project, seq)
        ) WITHOUT ROWID;
        CREATE TABLE named (slug TEXT PRIMARY KEY, value REAL);
        CREATE TABLE log (at REAL, what TEXT);
        CREATE TRIGGER things_logged AFTER INSERT ON things
            BEGIN INSERT INTO log VALUES (new.amount, 'added ' || new.name); END;
        CREATE VIEW cheap AS SELECT id, name FROM things WHERE amount < 1;
        PRAGMA user_version = 42;
    """)
    rows = [
        ("plain", 0.1, b"\x00\x01\xff", None, 1),
        ("/Users/tim/code", 1.0, None, 7, 0),
        ("", float("inf"), b"", -0.0, None),
        ("ünïcödé ✓", 1 / 3, bytes(range(256)), "text in a loose column", 2 ** 62),
        ("tiny", 5e-324, None, 1.7976931348623157e308, -2 ** 63),
        ("42", -float("inf"), b"\x00", b"blob in a loose column", 3),
    ]
    conn.executemany("INSERT INTO things (name, amount, body, loose, flag) VALUES (?, ?, ?, ?, ?)", rows)
    conn.execute("INSERT INTO things (name, amount) VALUES (CAST(X'FF00FE' AS TEXT), 2.5)")
    conn.execute("INSERT INTO things (name, amount) VALUES (?, 0.5)", ("nul\x00inside",))
    conn.executemany("INSERT INTO pairs VALUES (?, ?, ?)", [
        ("a/b", 1, "x"), ("a/b", 2, None), ("42", 42, 1.5), ("", 0, b"\x01"),
        ("comma,and'quote!", -1, "odd key"), ("x" * 400, 3, "long key")])
    conn.executemany("INSERT INTO named VALUES (?, ?)", [("one", 1.0), ("two", 2.000000000000001)])
    conn.execute("INSERT INTO named VALUES (NULL, 3.0)")      # rowid tables let a key be NULL
    conn.execute("INSERT INTO named VALUES (NULL, 4.0)")
    conn.execute("DELETE FROM log WHERE what = 'added tiny'")  # a gap in the rowids
    conn.commit()
    conn.close()
    return path


def _tables(path: Path) -> dict:
    """Each table's rows as the kit reads them, and the schema."""
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.text_factory = move._text_factory
    try:
        schema = move.schema_of(conn)
        out = {}
        for t in move.tables_of(schema):
            info = move.table_info(conn, t)
            out[t] = sorted(r.canonical() for r in move.read_rows(conn, info))
        return {"schema": schema, "tables": out}
    finally:
        conn.close()


def test_round_trip_keeps_every_storage_class_and_the_schema(tmp_path):
    src = _source(tmp_path / "source.db")
    before = src.read_bytes()
    with in_process(tmp_path / "engine") as (_, uds):
        done = move.copy(src, uds)
        assert done.state == "copied" and done.tables == 5
        report = move.verify(src, uds)
        assert report.ok, report.words()
        out = tmp_path / "back.db"
        move.export_sqlite(uds, None, out)
        assert src.read_bytes() == before                  # the source is never written
        a, b = _tables(src), _tables(out)
        assert a["schema"] == b["schema"]
        assert a["tables"] == b["tables"]
        assert set(a["tables"]) == {"things", "sqlite_sequence", "pairs", "named", "log"}

        # Exact values, read back with SQLite's own typeof.
        conn = sqlite3.connect(out)
        conn.text_factory = bytes
        got = conn.execute("SELECT typeof(amount), quote(amount), typeof(loose), typeof(name) "
                           "FROM things ORDER BY id").fetchall()
        orig = sqlite3.connect(src)
        orig.text_factory = bytes
        assert got == orig.execute("SELECT typeof(amount), quote(amount), typeof(loose), typeof(name) "
                                   "FROM things ORDER BY id").fetchall()
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 42
        assert conn.execute("SELECT seq FROM sqlite_sequence WHERE name='things'").fetchone()[0] == 8
        # The trigger is there and was not run by the export's inserts.
        assert conn.execute("SELECT count(*) FROM log").fetchone()[0] == 7
        conn.execute("INSERT INTO things (name) VALUES ('after')")
        assert conn.execute("SELECT count(*) FROM log").fetchone()[0] == 8
        assert conn.execute("SELECT count(*) FROM cheap").fetchone()[0] > 0
        conn.close()
        orig.close()

        # One item per row at its key, every column a cell.
        item = uds.get(move.MOVE_CALLER, "/sqlite/pairs/a%2Fb,1.row")
        assert item.data == {"table": "pairs", "cells": {"project": "a/b", "seq": 1, "note": "x"}}
        assert uds.get(move.MOVE_CALLER, "/sqlite/log/1.row").data["rowid"] == 1
        assert uds.get(move.MOVE_CALLER, "/sqlite/things/1.row").data["cells"]["body"] == {"blob": "AAH/"}
        assert uds.get(move.MOVE_CALLER, "/sqlite/pairs/'42,42.row") is not None
        assert uds.get(move.MOVE_CALLER, "/sqlite/pairs/',0.row") is not None
        entry = uds.get(move.MOVE_CALLER, "/sqlite/things/1.row").entry
        assert (entry["by"], entry["agent"], entry["change"]) == ("tim", "move-kit", "move")
        manifest = uds.get(move.MOVE_CALLER, move.MANIFEST_PATH).data
        assert manifest["state"] == "copied"
        assert {t["name"]: t["rows"] for t in manifest["tables"]}["things"] == 8

        with pytest.raises(move.MoveRefused):
            move.export_sqlite(uds, None, out)         # never overwrites


def test_key_segments_never_share_a_path():
    keys = [("key", (1,)), ("key", ("1",)), ("key", ("",)), ("key", ("'",)), ("key", (1.0,)),
            ("key", (b"\x01",)), ("key", (move.RawText(b"\x01"),)), ("key", (None,)),
            ("key", ("a", "b")), ("key", ("a,b",)), ("key", ("!n",)), ("rowid", (5,)),
            ("nullkey", (5,)), ("key", ("A",)), ("key", ("a",)), ("key", ("%41",))]
    segments = [move.key_segment(k) for k in keys]
    assert len(set(segments)) == len(segments)
    from jarvis.knowledge import paths
    for s in segments:
        assert paths.is_path(f"/sqlite/t/{s}.row") and paths.type_of(f"/sqlite/t/{s}.row") == "row"


def _saved_tables(conn: sqlite3.Connection, prefixes: tuple[str, ...]) -> None:
    """The tables (and their indexes and schema version rows) whose names
    start with `prefixes`, from jarvis/sqlite_schema.json."""
    import json
    saved = json.loads((Path(move.__file__).parent / "sqlite_schema.json").read_text())
    for o in saved["schema"]["objects"]:
        if o["sql"] and o["tbl_name"].startswith(prefixes) and not o["name"].startswith("sqlite_"):
            conn.execute(o["sql"].replace("CREATE TABLE ", "CREATE TABLE IF NOT EXISTS ", 1)
                         .replace("CREATE INDEX ", "CREATE INDEX IF NOT EXISTS ", 1))
    for name, rows in saved["schema_versions"].items():
        if name.startswith(prefixes):
            conn.executemany(f"INSERT INTO {name} VALUES ({', '.join('?' * len(rows[0]))})", rows)


def _jarvis_db(tmp_path: Path, monkeypatch) -> Path:
    """A database with JARVIS's last SQLite schema, a few rows a table."""
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path / "data"))
    from jarvis import data_paths
    # Every store is over documents now; the database JARVIS had when its
    # records moved is the schema saved with it (jarvis/sqlite_schema.json).
    data_paths.db_path().parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(data_paths.db_path())) as conn, conn:
        _saved_tables(conn, ("",))
    db = data_paths.db_path()
    conn = sqlite3.connect(db)
    conn.execute("PRAGMA foreign_keys=OFF")
    # Generic rows cannot meet every table's CHECK (states, kinds); the kit
    # moves whatever rows a table holds, so the filler does not try.
    conn.execute("PRAGMA ignore_check_constraints=ON")
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
    for t in tables:
        cols = [c for c in conn.execute(f"PRAGMA table_xinfo({move._q(t)})") if c[6] == 0]
        for i in range(3):
            values = []
            for c in cols:
                decl = (c[2] or "").upper()
                nullable = not c[3] and not c[5]
                if i == 2 and nullable:
                    values.append(None)
                elif "INT" in decl:
                    values.append(1000 * i + c[0] + 1)
                elif any(x in decl for x in ("REAL", "FLOA", "DOUB")):
                    values.append(i + c[0] / 7)
                elif "BLOB" in decl:
                    values.append(bytes([i, c[0], 255]))
                else:
                    values.append(f"{t}.{c[1]}.{i} /Users/tim/{i}")
            names = ", ".join(move._q(c[1]) for c in cols)
            marks = ", ".join("?" for _ in cols)
            conn.execute(f"INSERT OR IGNORE INTO {move._q(t)} ({names}) VALUES ({marks})", values)
    conn.commit()
    empty = [t for t in tables if not conn.execute(f"SELECT count(*) FROM {move._q(t)}").fetchone()[0]]
    conn.close()
    assert not empty, f"tables left empty: {empty}"
    assert len(tables) > 60
    return db


def test_jarvis_schema_round_trips_every_table(tmp_path, monkeypatch, capsys):
    # Whatever a table holds moves through the row mapping, every cell kept.
    # (The document types' own mappings are proven on rows their stores
    # write, each in its store's records test, and together on a copy of
    # the live database by the cut-over rehearsal.)
    monkeypatch.setattr(move, "RECORD_MODULES", ())
    db = _jarvis_db(tmp_path, monkeypatch)
    with in_process(tmp_path / "engine") as (_, uds):
        t0 = time.monotonic()
        done = move.copy(db, uds)
        copied = time.monotonic() - t0
        t0 = time.monotonic()
        report = move.verify(db, uds)
        verified = time.monotonic() - t0
        assert report.ok, report.words()
        out = tmp_path / "back.db"
        t0 = time.monotonic()
        move.export_sqlite(uds, None, out)
        exported = time.monotonic() - t0
    a, b = _tables(db), _tables(out)
    assert a["schema"] == b["schema"]
    assert a["tables"] == b["tables"]
    with capsys.disabled():
        print(f"\n[move] jarvis schema: {done.tables} tables, {done.rows} rows; copy {copied:.2f} s "
              f"({done.rows / copied:.0f} rows/s), verify {verified:.2f} s, export {exported:.2f} s")


def test_verify_names_a_changed_and_a_missing_row(tmp_path):
    src = _source(tmp_path / "source.db")
    with in_process(tmp_path / "engine") as (_, uds):
        move.copy(src, uds)
        cells = dict(uds.get(move.MOVE_CALLER, "/sqlite/pairs/a%2Fb,2.row").data["cells"])
        cells["note"] = "changed"
        uds.transaction(move.MOVE_CALLER, Transaction().put(
            "/sqlite/pairs/a%2Fb,2.row", {"table": "pairs", "cells": cells}), change="test")
        uds.transaction(move.MOVE_CALLER, Transaction().delete("/sqlite/things/3.row"), change="test")
        report = move.verify(src, uds)
        assert not report.ok
        assert report.differing == ["things", "pairs"]
        by = {t.name: t for t in report.tables}
        assert by["things"].first_difference == "id=3 (missing from the engine)"
        assert by["things"].engine_rows == by["things"].source_rows - 1
        assert by["pairs"].first_difference == "project='a/b', seq=2 (changed)"
        assert "pairs" in report.words() and "things" in report.words()


def test_copy_refuses_an_engine_holding_a_different_move(tmp_path):
    src = _source(tmp_path / "source.db")
    other = tmp_path / "other.db"
    with closing(sqlite3.connect(other)) as conn:
        conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY)")
        conn.commit()
    with in_process(tmp_path / "engine") as (_, uds):
        move.copy(src, uds)
        assert move.copy(src, uds).state == "already"     # the same move: nothing written
        with pytest.raises(move.MoveRefused):
            move.copy(other, uds)
        with closing(sqlite3.connect(src)) as conn:       # the same file, changed since
            conn.execute("UPDATE named SET value = 9 WHERE slug = 'one'")
            conn.commit()
        with pytest.raises(move.MoveRefused):
            move.copy(src, uds)
        assert uds.get(move.MOVE_CALLER, "/sqlite/named/one.row").data["cells"]["value"] == 1.0

    with in_process(tmp_path / "engine2") as (_, uds):
        uds.transaction(move.MOVE_CALLER, Transaction().put("/sqlite/stray.row", {"x": 1}), change="test")
        with pytest.raises(move.MoveRefused):
            move.copy(other, uds)


def test_a_move_stopped_part_way_is_written_again(tmp_path):
    src = _source(tmp_path / "source.db")
    with in_process(tmp_path / "engine") as (_, uds):
        real = move._write_docs
        calls = {"n": 0}

        def failing(*a, **kw):
            calls["n"] += 1
            if calls["n"] == 3:
                raise RuntimeError("stopped")
            return real(*a, **kw)

        move._write_docs = failing
        try:
            with pytest.raises(RuntimeError):
                move.copy(src, uds)
        finally:
            move._write_docs = real
        assert uds.get(move.MOVE_CALLER, move.MANIFEST_PATH).data["state"] == "copying"
        assert not move.verify(src, uds).ok
        assert move.copy(src, uds).state == "copied"
        assert move.verify(src, uds).ok


class _History:
    """A mapping whose event rows become one record's versions: the hook
    round 3 fills. `events` rows (record, seq, state) are the versions of
    `/records/<record>.rec`; the newest is its current state."""
    name = "history-test"
    tables = ("events",)
    under = ("/records",)
    history = True

    def documents(self, conn):
        by: dict = {}
        for record, seq, state in conn.execute("SELECT record, seq, state FROM events ORDER BY record, seq"):
            by.setdefault(record, []).append({"seq": seq, "state": state})
        for record, versions in by.items():
            yield move.Doc(f"/records/{record}.rec", {"record": record, **versions[-1]},
                           history=tuple(move.Revision({"record": record, **v}, change="event")
                                         for v in versions[:-1]))

    def rows(self, docs):
        out = []
        for d in docs:
            for v in [r.data for r in d.history] + [d.data]:
                out.append(move.Row({"record": v["record"], "seq": v["seq"], "state": v["state"]}))
        return {"events": out}


def test_event_rows_become_versions_and_come_back(tmp_path):
    src = tmp_path / "events.db"
    with closing(sqlite3.connect(src)) as conn:
        conn.execute("CREATE TABLE events (record TEXT, seq INTEGER, state TEXT, "
                     "PRIMARY KEY (record, seq)) WITHOUT ROWID")
        conn.executemany("INSERT INTO events VALUES (?, ?, ?)",
                         [("w1", 1, "planned"), ("w1", 2, "working"), ("w1", 3, "done"), ("w2", 1, "ready")])
        conn.commit()
    mappings = [_History()]
    with in_process(tmp_path / "engine") as (_, uds):
        move.copy(src, uds, mappings)
        history = uds.history(move.MOVE_CALLER, "/records/w1.rec")
        assert sorted((v.version, v.change) for v in history) == [(1, "event"), (2, "event"), (3, "move")]
        assert move.verify(src, uds, mappings).ok
        out = tmp_path / "back.db"
        move.export_sqlite(uds, mappings, out)
    assert _tables(src)["tables"] == _tables(out)["tables"]


def test_cli_copy_verify_export(tmp_path, capsys):
    from jarvis.knowledge.__main__ import main
    src = _source(tmp_path / "source.db")
    engine = tmp_path / "engine"
    assert main(["copy", "--db", str(src), "--engine-dir", str(engine)]) == 0
    assert main(["verify", "--db", str(src), "--engine-dir", str(engine)]) == 0
    out = tmp_path / "back.db"
    assert main(["export-sqlite", "--out", str(out), "--engine-dir", str(engine)]) == 0
    assert _tables(src) == _tables(out)
    assert main(["export-sqlite", "--out", str(out), "--engine-dir", str(engine)]) == 3
    words = capsys.readouterr()
    assert "copied 26 rows of 5 tables" in words.out
    assert "never overwrites" in words.err
