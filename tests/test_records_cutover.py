"""The cut-over at first start (`jarvis/records_cutover.py`): jarvis.db
into the engine once, proven both ways, the file never written."""
import hashlib
import sqlite3

import pytest

from jarvis import data_paths, records, records_cutover
from jarvis import knowledge_move as move


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def jarvis_db(monkeypatch):
    # Only the generic row mapping, so the test is about the cut-over itself.
    monkeypatch.setattr(move, "RECORD_MODULES", ())
    path = data_paths.db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE things (id TEXT PRIMARY KEY, n INTEGER, x REAL, note TEXT, raw BLOB);
        CREATE TABLE log (id INTEGER PRIMARY KEY AUTOINCREMENT, what TEXT NOT NULL);
        INSERT INTO things VALUES ('a', 1, 1.5, NULL, x'00ff'), ('b', NULL, 2.0, 'hé', NULL);
        INSERT INTO log (what) VALUES ('one'), ('two');
    """)
    conn.commit()
    conn.close()
    return path


def test_a_new_install_has_nothing_to_move():
    assert records_cutover.cut_over(snapshot=False).state == "fresh"
    assert records_cutover.done() is None


def test_jarvis_db_moves_once_proven_both_ways_and_is_never_written(jarvis_db):
    before = _sha(jarvis_db)
    out = records_cutover.cut_over()
    assert out.state == "cut over" and out.rows == 2 + 2 + 1      # sqlite_sequence too
    assert set(out.seconds) >= {"snapshot", "copy", "verify", "verify_back", "total"}
    assert out.snapshot
    assert _sha(jarvis_db) == before
    record = records_cutover.done()
    assert record["source_sha256"] == before and record["rows"] == out.rows
    # The next start finds it done and touches nothing.
    assert records_cutover.cut_over().state == "done"
    assert _sha(jarvis_db) == before


def test_a_move_that_cannot_be_proven_stops_startup(jarvis_db, monkeypatch):
    real = move.verify

    def differs(*a, **kw):
        report = real(*a, **kw)
        t = report.tables[0]
        bad = move.TableReport(t.name, t.source_rows, t.engine_rows + 1, t.source_digest, "x", "first")
        return move.Report((bad,) + report.tables[1:], report.problems, report.seconds)

    monkeypatch.setattr(move, "verify", differs)
    with pytest.raises(records_cutover.CutoverFailed):
        records_cutover.cut_over(snapshot=False)
    assert records_cutover.done() is None


def test_export_sqlite_on_an_install_that_never_had_jarvis_db(tmp_path):
    out = tmp_path / "back.db"
    done = move.export_sqlite(records.uds(), None, out)
    assert done["tables"] > 70
    conn = sqlite3.connect(out)
    try:
        names = {n for (n,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"runs", "work_items", "attention_items", "messages"} <= names
        # Each store's schema version comes back, so the code before the move
        # opens the file as its own.
        assert conn.execute("SELECT version FROM work_schema").fetchone() is not None
    finally:
        conn.close()


def _write_between_starts(path):
    conn = sqlite3.connect(path)
    conn.executescript("""
        UPDATE things SET n = 7 WHERE id = 'a';
        DELETE FROM things WHERE id = 'b';
        INSERT INTO log (what) VALUES ('written before the next start');
    """)
    conn.commit()
    conn.close()


def test_a_start_stopped_mid_copy_never_blocks_the_next(jarvis_db, monkeypatch):
    real = move._write_docs
    calls = {"n": 0}

    def stopped(*a, **kw):
        calls["n"] += 1
        if calls["n"] == 2:
            raise KeyboardInterrupt      # Ctrl-C, or the server gone
        return real(*a, **kw)

    monkeypatch.setattr(move, "_write_docs", stopped)
    with pytest.raises(KeyboardInterrupt):
        records_cutover.cut_over(snapshot=False)
    monkeypatch.setattr(move, "_write_docs", real)
    _write_between_starts(jarvis_db)
    before = _sha(jarvis_db)
    out = records_cutover.cut_over(snapshot=False)
    assert out.state == "cut over" and "cleared" in out.seconds
    assert move.verify(jarvis_db, records.uds()).ok
    assert records_cutover.done()["source_sha256"] == before == _sha(jarvis_db)


def test_a_start_stopped_during_the_checks_never_blocks_the_next(jarvis_db, monkeypatch):
    real = move.export_sqlite

    def stopped(*a, **kw):
        raise KeyboardInterrupt          # stopped while checking back

    monkeypatch.setattr(move, "export_sqlite", stopped)
    with pytest.raises(KeyboardInterrupt):
        records_cutover.cut_over(snapshot=False)
    monkeypatch.setattr(move, "export_sqlite", real)
    _write_between_starts(jarvis_db)
    out = records_cutover.cut_over(snapshot=False)
    assert out.state == "cut over" and "cleared" in out.seconds
    assert records.uds().get(move.MOVE_CALLER, "/sqlite/things/b.row") is None
