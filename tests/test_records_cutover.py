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


# -- an older jarvis.db: conversation_schema 2, no agent-key tables -------------

# conversation_items as conversation_schema 2 left it: 16 columns (the six
# of migration 4, sender to state_at, came later).
_ITEMS_AT_SCHEMA_2 = """
    CREATE TABLE conversation_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        at REAL NOT NULL,
        kind TEXT NOT NULL,
        text TEXT NOT NULL,
        agent_session_id TEXT,
        agent_name TEXT,
        message_id INTEGER,
        session_message_id INTEGER,
        capture_id INTEGER,
        detail TEXT NOT NULL DEFAULT '{}'
    , counterpart_kind TEXT, counterpart_ref TEXT, sender_evidence TEXT, sender_subagent TEXT,
      source_kind TEXT, source_id TEXT)
"""
_LATER_INDEXES = {"conversation_items_by_source", "conversation_items_by_about",
                  "conversation_items_by_state", "conversation_items_by_carries"}
_NO_AGENT_KEYS = ("agent_key", "agent_rights", "agent_author")


def _older_jarvis_db(path):
    """JARVIS's last SQLite schema as an older install had it: conversation
    items at conversation_schema 2 and none of the agent-key tables, with
    agents, steers and over a thousand conversation items."""
    import json
    saved = json.loads(move.PACKAGED_SCHEMA.read_text(encoding="utf-8"))
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    for o in saved["schema"]["objects"]:
        if not o["sql"] or o["name"].startswith("sqlite_") or o["tbl_name"].startswith(_NO_AGENT_KEYS):
            continue
        if o["name"] == "conversation_items":
            conn.execute(_ITEMS_AT_SCHEMA_2)
        elif o["name"] not in _LATER_INDEXES:
            conn.execute(o["sql"])
    for name, rows in saved["schema_versions"].items():
        if name.startswith(_NO_AGENT_KEYS):
            continue
        rows = [[2]] if name == "conversation_schema" else rows
        conn.executemany(f"INSERT INTO {name} VALUES ({', '.join('?' * len(rows[0]))})", rows)
    conn.executemany("INSERT INTO agents VALUES (?, ?, ?, ?, ?, ?, ?)",
                     [("s-1", "planning", 1, "idle", None, 1700000000.0, None),
                      ("s-2", "chitauri", 0, "", "Chitauri", 0, 1700000500.0)])
    conn.executemany("INSERT INTO steers (session_id, voice_name, project, prompt, outcome, created_at) "
                     "VALUES (?, ?, ?, ?, ?, ?)",
                     [("s-1", "planning", "jarvis", f"steer {n}", "sent", 1700000000.0 + n)
                      for n in range(3)])
    items = []
    for n in range(1, 1375):
        if n % 10:
            items.append((n, 1700000000.0 + n, "heard" if n % 2 else "jarvis", f"said {n}",
                          None, None, None, None, None, "{}", None, None, None, None, None, None))
        else:
            items.append((n, 1700000000.0 + n, "message_in", f"agent says {n}", "s-1", "planning",
                          None, None, None, '{"proof": "watcher"}', "agent", "s-1", "watcher", None,
                          "message", str(n)))
    conn.executemany(f"INSERT INTO conversation_items VALUES ({', '.join('?' * 16)})", items)
    conn.commit()
    conn.close()
    return path


def test_an_older_jarvis_db_moves_and_is_proven_both_ways():
    """An install whose jarvis.db is older than the last schema (the
    conversation's 16 columns, no agent-key tables) moves through the
    document types' own mappings and is proven both ways: the check reads
    the items back in the file's 16 columns, and the way back makes none of
    the tables the file never had."""
    path = _older_jarvis_db(data_paths.db_path())
    before = _sha(path)
    out = records_cutover.cut_over(snapshot=False)
    assert out.state == "cut over"
    assert _sha(path) == before
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT count(*) FROM conversation_items").fetchone()[0] == 1374
    assert records_cutover.done()["rows"] == out.rows
