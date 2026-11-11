"""The attention record as documents (attention_store.py over records.py):
items, their events as versions, presentations, digests and settings, and
the move of their five SQLite tables into the engine and back, lossless.

Checked: a synthetic file filling every column of every table (NULLs, an
event and a presentation naming no item, a setting that is not JSON, the
row numbers SQLite keeps apart) copies, verifies and exports back equal;
the store reads the moved documents as the SQL read the rows; what the
store writes after the move exports with fresh ids after the largest; the
settings are held in memory; and messaging's announcements under
/attention are never counted or listed as items.
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import time
from contextlib import closing
from pathlib import Path

import pytest

from jarvis import attention_store, records
from jarvis import knowledge_move as move
from jarvis.attention_store import AttentionStore
from jarvis.knowledge.uds import in_process

TABLES = ("attention_items", "attention_events", "attention_digests", "attention_presentations",
          "attention_settings")
COPY = Path(os.environ.get(
    "JARVIS_ATTENTION_COPY",
    "/private/tmp/claude-501/-Users-timothystockstill-code-macos-4thWAIV-jarvis/"
    "a5b4be76-7b3d-4a0c-9220-817d38b9e5f3/scratchpad/r3/jarvis-copy.db"))


# The schema version table is no document type: the kit's own row mapping.
MAPPINGS = attention_store.MAPPINGS + [move.RowMapping("attention_schema")]


def _schema(conn: sqlite3.Connection) -> None:
    saved = json.loads((Path(move.__file__).parent / "sqlite_schema.json").read_text())
    for o in saved["schema"]["objects"]:
        if o["sql"] and o["tbl_name"].startswith("attention_"):
            conn.execute(o["sql"])
    conn.executemany("INSERT INTO attention_schema VALUES (?)",
                     saved["schema_versions"]["attention_schema"])


def _synthetic(path: Path) -> Path:
    with closing(sqlite3.connect(path)) as conn, conn:
        _schema(conn)
        conn.executemany("INSERT INTO attention_items VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", [
            (1, "node-a", "agent_message", "987", 2, "answered", '{"message_id": 987}',
             '{"agent": "Diagnostics"}', 1, "seen", 1, 1793900078.85392, 1793900139.5662,
             1793900191.56467),
            (2, "node-a", "package", "inbox/7 x", 1, "arrived", "{}", "{}", 0, "pending", 0,
             1793900000.0, 1793900000.0, None),
            (5, "node-b", "digests", "a:b", 3, "x", "not json", '{"b": 1, "a": "é"}', 1,
             "resolved", 2, 10.5, 11.5, None),
        ])
        conn.executemany("INSERT INTO attention_events VALUES (?,?,?,?,?,?,?)", [
            (1, 1, 1793900078.85392, 1, "delivered", '{"message_id": 987}', '{"agent": "Diagnostics"}'),
            (4, 1, 1793900139.5662, 2, "answered", '{"message_id": 987}', '{"agent": "Diagnostics"}'),
            (2, 2, 1793900000.0, 1, "arrived", "{}", "{}"),
            (3, 99, 5.0, 1, "orphan", "{}", "{}"),
            (7, 5, 11.5, 3, "x", "not json", '{"a":"é"}'),
        ])
        conn.executemany("INSERT INTO attention_digests VALUES (?,?,?,?,?,?)", [
            (1, 1793900150.0, 1793900160.0, "played", None, "Diagnostics has answered, sir."),
            (2, 1793900170.0, 1793900171.0, "failed", "brain", None),
        ])
        conn.executemany("INSERT INTO attention_presentations VALUES (?,?,?)",
                         [(1, 1, 2), (2, 5, 2), (2, 1, 2), (1, 42, 1)])
        conn.executemany("INSERT INTO attention_settings (rowid, key, value) VALUES (?,?,?)",
                         [(3, "away", "false"), (5, "oldest_event_sec", "90.0"),
                          (6, "engagement_sec", "1e2"), (9, "mystery", "not json")])
    return path


def _rows(path: Path) -> dict:
    with closing(sqlite3.connect(path)) as conn:
        return {t: sorted(map(repr, conn.execute(f"SELECT rowid, * FROM {t}"))) for t in TABLES}


def test_synthetic_tables_round_trip_through_documents(tmp_path):
    src = _synthetic(tmp_path / "src.db")
    with in_process(tmp_path / "engine") as (_, uds):
        move.copy(src, uds, MAPPINGS)
        report = move.verify(src, uds, MAPPINGS)
        assert report.ok, report.words()
        # The documents are the store's own shape.
        item = uds.get(move.MOVE_CALLER, attention_store.item_path("agent_message", "987")).data
        assert item["facts"] == {"message_id": 987} and item["events"] == []
        assert item["presentations"] == [{"digest_id": 1, "revision": 2, "rowid": 1},
                                         {"digest_id": 2, "revision": 2, "rowid": 3}]
        versions = uds.history(move.MOVE_CALLER, attention_store.item_path("agent_message", "987"))
        assert sorted((v.version, v.change) for v in versions) == [
            (1, "delivered"), (2, "answered"), (3, "move")]
        odd = attention_store.item_path("digests", "a:b")
        assert odd.startswith("/attention/%64igests/")
        assert uds.get(move.MOVE_CALLER, odd).data["facts_text"] == "not json"
        out = tmp_path / "back.db"
        move.export_sqlite(uds, MAPPINGS, out)
    assert _rows(out) == _rows(src)


def test_the_store_reads_moved_documents_and_numbers_on_after_them(tmp_path):
    src = _synthetic(tmp_path / "src.db")
    with in_process(tmp_path / "engine") as (_, uds):
        move.copy(src, uds, MAPPINGS)
        records.bind(uds)
        try:
            store = AttentionStore(clock=lambda: 1793900200.0, node_id=lambda: "node-a")
            assert store.pending_count() == 1
            assert [i.id for i in store.items((attention_store.PENDING, attention_store.SEEN,
                                               attention_store.RESOLVED))] == [1, 2, 5]
            assert [h["event"] for h in store.history(1)] == ["delivered", "answered"]
            assert store.get("digests", "a:b").facts == {}
            assert store.get("digests", "a:b").untrusted == {"a": "é", "b": 1}
            assert [d["items"] for d in store.digests()] == [[1, 5], [1]]
            assert store.last_digest_started() == 1793900170.0
            s = attention_store.settings()
            assert (s.away, s.oldest_event_sec, s.engagement_sec) == (False, 90.0, 100.0)
            # Written since the move: next ids after the largest.
            new = store.record("run", "r-1", "finished", facts={"ok": True})
            assert new.id == 6
            store.record("agent_message", "987", "failed")
            digest = store.begin_digest([new])
            assert digest == 3
            store.finish_digest(digest, attention_store.PLAYED, text="Done, sir.")
            assert store.get("run", "r-1").digested_revision == 1
            attention_store.change_settings({"digest_cooldown_sec": 300})
            out = tmp_path / "back.db"
            move.export_sqlite(uds, MAPPINGS, out)
        finally:
            records.bind(None)
    with closing(sqlite3.connect(out)) as conn:
        assert conn.execute("SELECT id, item_id, event FROM attention_events WHERE id > 7 "
                            "ORDER BY id").fetchall() == [(8, 1, "failed"), (9, 6, "finished")]
        assert conn.execute("SELECT rowid, * FROM attention_presentations WHERE digest_id = 3"
                            ).fetchall() == [(5, 3, 6, 1)]
        assert conn.execute("SELECT outcome, text FROM attention_digests WHERE id = 3"
                            ).fetchall() == [("played", "Done, sir.")]
        assert conn.execute("SELECT rowid, key, value FROM attention_settings ORDER BY rowid"
                            ).fetchall() == [(3, "away", "false"), (5, "oldest_event_sec", "90.0"),
                                             (6, "engagement_sec", "1e2"), (9, "mystery", "not json"),
                                             (10, "digest_cooldown_sec", "300.0")]


def test_announcements_and_digests_under_attention_are_never_items():
    store = AttentionStore(clock=lambda: 100.0)
    records.write("/attention/announcements/m-1.attention",
                  {"server_id": "s", "owner": 1, "message_id": "m-1", "state": "pending",
                   "digested_revision": 0})
    item = store.record("digests", "x", "news")
    assert store.pending_count() == 1
    assert [i.id for i in store.items()] == [item.id]
    store.mark_seen()
    assert records.get("/attention/announcements/m-1.attention").data["state"] == "pending"
    assert store.pending_count() == 0


def test_settings_are_held_in_memory_and_refreshed_by_their_writes():
    assert attention_store.settings().away is False
    records.write(attention_store.SETTINGS_PATH, {"values": {"away": True}})
    assert attention_store.settings().away is False          # held: the engine is not read again
    attention_store.change_settings({"oldest_event_sec": 30})
    s = attention_store.settings()
    assert (s.away, s.oldest_event_sec) == (True, 30.0)      # the write read it whole


def test_each_change_is_a_version_with_its_event_and_writer():
    store = AttentionStore(clock=lambda: 100.0)
    item = store.record("session_message", "s-1", "arrived", untrusted={"agent": "Planning"})
    store.record("session_message", "s-1", "arrived", untrusted={"agent": "Planning"})  # nothing new
    store.mark_seen([item.id])
    store.record("session_message", "s-1", "read")
    path = attention_store.item_path("session_message", "s-1")
    versions = records.history(path, fields=["events"])
    assert [(v.change, v.agent, [e["event"] for e in v.data["events"]]) for v in versions] == [
        ("arrived", "jarvis", ["arrived"]), ("seen", None, []), ("read", "jarvis", ["read"])]
    assert store.get("session_message", "s-1").revision == 2


@pytest.mark.skipif(not COPY.is_file(), reason="no copy of the live database here")
def test_the_live_copy_round_trips(tmp_path, capsys):
    db = tmp_path / "copy.db"
    shutil.copyfile(COPY, db)
    with closing(sqlite3.connect(db)) as conn:
        names = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        for name in names:
            if not name.startswith("attention_") and not name.startswith("sqlite_"):
                conn.execute(f'DROP TABLE "{name}"')
        conn.commit()
        conn.execute("VACUUM")
        sql = {t: conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in TABLES}
        pending = conn.execute("SELECT count(*) FROM attention_items WHERE state='pending'").fetchone()[0]
        newest = [r[0] for r in conn.execute(
            "SELECT id FROM attention_items ORDER BY updated_at DESC, id DESC LIMIT 200")]
        some = conn.execute("SELECT id, kind, source_id FROM attention_items ORDER BY id DESC LIMIT 1").fetchone()
        events = [r[0] for r in conn.execute(
            "SELECT event FROM attention_events WHERE item_id = ? ORDER BY id", (some[0],))]
    with in_process(tmp_path / "engine") as (_, uds):
        t0 = time.monotonic()
        move.copy(db, uds, MAPPINGS)
        copied = time.monotonic() - t0
        t0 = time.monotonic()
        report = move.verify(db, uds, MAPPINGS)
        verified = time.monotonic() - t0
        assert report.ok, report.words()
        out = tmp_path / "back.db"
        move.export_sqlite(uds, MAPPINGS, out)
        records.bind(uds)
        try:
            store = AttentionStore()
            assert store.pending_count() == pending
            assert [i.id for i in store.items((attention_store.PENDING, attention_store.SEEN,
                                               attention_store.RESOLVED))] == newest
            assert [h["event"] for h in store.history(some[0])] == events
            assert store.get(some[1], some[2]).id == some[0]
        finally:
            records.bind(None)
    assert _rows(out) == _rows(db)
    with capsys.disabled():
        print(f"\n[attention] live copy {sql}: copy {copied:.2f} s, verify {verified:.2f} s")
