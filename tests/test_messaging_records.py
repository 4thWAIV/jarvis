"""The messaging records as documents and back (round 3 of
docs/planning/knowledge-engine/plan-uds.md): the `human_*` tables, the
`messaging_files`, `attachment_locations` and `attachment_leases` tables
into `message`, `thread`, `file`, announcement and sync documents through
the move kit, verified table by table, and exported back as the same rows;
then what the stores write after the cut-over exported as rows of the same
tables. With JARVIS_ROUND_TRIP_DB naming a copy of a real jarvis.db, the
same on its rows."""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import sqlite3
from pathlib import Path
from uuid import UUID

import pytest

from jarvis import knowledge_move as move
from jarvis import messaging_files, messaging_ports as ports, messaging_store, records

MINE = ("human_messages", "human_drafts", "human_approved", "human_sends", "human_preflight",
        "human_owed", "human_receipts", "human_openability", "human_review", "human_work",
        "human_commands", "human_announcements", "human_sync", "human_switch",
        "messaging_files", "attachment_locations", "attachment_leases")
SCHEMA = json.loads((Path(messaging_store.__file__).parent / "sqlite_schema.json").read_text())


def _mappings() -> list:
    return messaging_store.MAPPINGS + messaging_files.MAPPINGS + [move.RowMapping("human_switch")]


def _schema_db(path: Path, tables=MINE) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    for o in SCHEMA["schema"]["objects"]:
        if o["sql"] and o["tbl_name"] in tables:
            conn.execute(o["sql"])
    return conn


_JSON = {"body", "facts", "report", "reported"}


def _value(table: str, column: str, decl: str, i: int, nullable: bool):
    """Row i of a table: 0 real JSON in JSON columns, 1 text that is not
    JSON (one of them not UTF-8), 2 every nullable column NULL."""
    if i == 2 and nullable:
        return None
    if column in ("server_id", "message_id", "thread_id", "attachment_id", "node_id"):
        return f"{column}-{i}@x"            # shared, so one row of each table meets in one document
    if "INT" in decl:
        return [7, 2 ** 62, -3][i]
    if "REAL" in decl:
        return [1793514092.1504672, 0.1, 1.0][i]
    if column in _JSON or column == "continuation":
        return ['{"z": 1, "a": [1, "é", null], "m": {"y": true, "b": 1.5}}',
                f"{table}.{column} /Users/tim/x", '{"a":1}'][i]
    if column == "extraction_members":
        return "a.txt\nb/c.md"
    return [f"{table}-{column}-0", f"{table}.{column} /Users/tim/1 ünï", f"{table}-{column}-2"][i]


def _fill(conn: sqlite3.Connection) -> None:
    for t in MINE:
        cols = list(conn.execute(f"PRAGMA table_info({t})"))
        for i in range(3):
            values = [_value(t, c[1], (c[2] or "").upper(), i, not c[3] and not c[5])
                      for c in cols]
            if t == "human_switch":
                if i:
                    continue
                values = [1, 1]
            conn.execute(f"INSERT INTO {t} VALUES ({', '.join('?' * len(cols))})", values)
    # TEXT whose bytes are not UTF-8, and a JSON body pydantic would render the same
    conn.execute("UPDATE human_drafts SET body = CAST(x'ff00fe' AS TEXT) "
                 "WHERE rowid = (SELECT MIN(rowid) FROM human_drafts)")
    send = ports.LocalSend(message_id=UUID(int=5), content_revision=1, state="draft")
    conn.execute("UPDATE human_sends SET body = ? "
                 "WHERE rowid = (SELECT MAX(rowid) FROM human_sends)", (send.model_dump_json(),))
    conn.commit()


def _rows(path: Path) -> dict:
    conn = sqlite3.connect(path)
    conn.text_factory = bytes
    try:
        return {t: sorted(map(repr, conn.execute(f"SELECT rowid, * FROM {t}"))) for t in MINE}
    finally:
        conn.close()


def _round_trip(src: Path, tmp_path: Path) -> move.Report:
    uds = records.uds()
    move.copy(src, uds, _mappings())
    report = move.verify(src, uds, _mappings())
    out = tmp_path / "back.db"
    move.export_sqlite(uds, _mappings(), out)
    assert _rows(out) == _rows(src)
    return report


def test_every_column_of_every_table_comes_back(tmp_path):
    src = tmp_path / "src.db"
    conn = _schema_db(src)
    _fill(conn)
    conn.close()
    report = _round_trip(src, tmp_path)
    assert report.ok, report.words()
    assert {t.name for t in report.tables} == set(MINE)
    assert all(t.source_rows >= 1 for t in report.tables)


def test_the_rows_become_one_document_per_message_thread_and_file(tmp_path):
    src = tmp_path / "src.db"
    conn = _schema_db(src)
    _fill(conn)
    conn.close()
    move.copy(src, records.uds(), _mappings())
    messages = records.all_of("/people", types=["message"])
    assert messages and all(d.data["events"] == [] for d in messages)
    draft_row = next(d for d in messages if d.data["send"] and d.data["send"]["body"] ==
                     ports.LocalSend(message_id=UUID(int=5), content_revision=1,
                                     state="draft").model_dump(mode="json"))
    assert "raw" not in draft_row.data["send"], "a body pydantic renders the same keeps no text"
    assert records.all_of("/people", types=["thread"])
    assert records.all_of("/people", types=["file"])
    assert records.all_of("/attention/announcements", types=["attention"])
    assert records.all_of("/people", types=["settings"])


def test_what_the_stores_write_after_the_move_exports_as_their_rows(tmp_path):
    from tests.fixtures import messaging_fakes as fakes
    from jarvis import messaging_wire as wire
    src = tmp_path / "empty.db"
    _schema_db(src).close()
    uds = records.uds()
    move.copy(src, uds, _mappings())
    store = messaging_store.SqliteMessagingStore()
    serve = fakes.FakeServe(server_id="server-1", members={1, 2}, clock=fakes.FixedClock())
    ids = fakes.SeqIds(start=900)
    mid = ids.new_id()
    draft = store.create_draft("server-1", 1, ports.Draft(
        message_id=mid, thread_id=mid, recipient_github_id=2, draft_revision=1, text="Hi.",
        send=ports.LocalSend(message_id=mid, content_revision=1, state="draft")))
    guard = fakes.TableGuard(store, ids=ids, clock=fakes.FixedClock())
    reviewed = guard.request_review("server-1", 1, mid, 1)
    approved = guard.approve("server-1", 1, mid, 1, reviewed.send.content_digest, UUID(int=1),
                             "gui")
    result = asyncio.run(serve.pair(1, UUID(int=1)).send(approved.request))
    store.record_accepted("server-1", 1, result)
    store.owe_receipt("server-1", 1, mid, "read")
    store.queue_command("server-1", 1, mid, wire.WorkCommandRequest(
        change=wire.OpenItem(expected_revision=1)))
    store.save_review("server-1", 1, mid, None, 1, json.dumps({"state": {"x": 1}, "source": None}))
    store.set_announcement("server-1", 1, mid, "due")
    files = messaging_files.FileSnapshots(store, lambda: None)
    ref = wire.AttachmentRef(attachment_id=UUID(int=8), filename="a.txt", media_type="text/plain",
                             size_bytes=1, sha256="0" * 64)
    files._insert("server-1", 1, ref, mid, messaging_files.SNAPSHOT, "staged")
    out = tmp_path / "back.db"
    move.export_sqlite(uds, _mappings(), out)
    conn = sqlite3.connect(out)
    conn.row_factory = sqlite3.Row
    drafts = conn.execute("SELECT * FROM human_drafts").fetchall()
    assert [ports.Draft.model_validate_json(r["body"]).message_id for r in drafts] == [mid]
    assert ports.Draft.model_validate_json(drafts[0]["body"]).send.state == "accepted"
    assert [r["state"] for r in conn.execute("SELECT state FROM human_sends")] == ["accepted"]
    assert conn.execute("SELECT COUNT(*) FROM human_approved").fetchone()[0] == 1
    assert conn.execute("SELECT incoming FROM human_messages").fetchone()[0] == 0
    assert sorted(r["kind"] for r in conn.execute("SELECT kind FROM human_owed")) == ["read"]
    assert conn.execute("SELECT COUNT(*) FROM human_commands").fetchone()[0] == 1
    assert json.loads(conn.execute("SELECT body FROM human_review").fetchone()[0]) == \
        {"state": {"x": 1}, "source": None}
    assert [r["state"] for r in conn.execute("SELECT state FROM human_announcements")] == ["due"]
    assert [r["filename"] for r in conn.execute("SELECT filename FROM messaging_files")] == \
        ["a.txt"]
    assert draft.message_id == mid


@pytest.mark.skipif(not os.environ.get("JARVIS_ROUND_TRIP_DB"),
                    reason="JARVIS_ROUND_TRIP_DB names no copy of a real jarvis.db")
def test_a_real_database_s_messaging_tables_come_back(tmp_path):
    full = tmp_path / "full.db"
    shutil.copy(os.environ["JARVIS_ROUND_TRIP_DB"], full)
    src = tmp_path / "mine.db"
    conn = _schema_db(src)
    with sqlite3.connect(full) as f:
        for t in MINE:
            cols = [c[1] for c in f.execute(f"PRAGMA table_info({t})")]
            conn.executemany(f"INSERT INTO {t} (rowid, {', '.join(cols)}) VALUES "
                             f"({', '.join('?' * (len(cols) + 1))})",
                             f.execute(f"SELECT rowid, * FROM {t}").fetchall())
    conn.commit()
    conn.close()
    report = _round_trip(src, tmp_path)
    assert report.ok, report.words()
