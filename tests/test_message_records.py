"""Messages to and from agents and the conversation's turns as documents
(message_records.py): one message, its row and its conversation items, is
one document whose versions are its steps; each turn is one document; ids
keep counting; and the move from SQLite and back is lossless."""
import json
import os
import sqlite3
import time
from pathlib import Path

import pytest

from jarvis import conversation_log as cl
from jarvis import knowledge_move as move
from jarvis import message_records as mr
from jarvis import records, run_store
from jarvis.conversation_log import ConversationItem, Counterpart, ItemKind

TABLES = ("messages", "session_messages", "conversation_items")


def _schema() -> list[dict]:
    raw = json.loads((Path(__file__).resolve().parent.parent / "jarvis" /
                      "sqlite_schema.json").read_text())
    return [o for o in raw["schema"]["objects"]
            if o["tbl_name"] in TABLES and o["sql"]]


def sqlite_file(path: Path, rows: dict) -> Path:
    """A SQLite file with this group's three tables as they stood, holding
    `rows` (table: [row dict, ...])."""
    conn = sqlite3.connect(path)
    for o in _schema():
        conn.execute(o["sql"])
    for table, found in rows.items():
        for row in found:
            names = ", ".join(row)
            conn.execute(f"INSERT INTO {table} ({names}) VALUES ({', '.join('?' * len(row))})",
                         list(row.values()))
    conn.commit()
    conn.close()
    return path


def _mappings(db: Path) -> list:
    conn = sqlite3.connect(db)
    tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    conn.close()
    return cl.MAPPINGS + [move.RowMapping(t) for t in tables if t not in TABLES]


def moved(tmp_path: Path, rows: dict) -> Path:
    """Rows as they stood in SQLite, moved into this test's engine."""
    db = sqlite_file(tmp_path / "moved.db", rows)
    move.copy(db, records.uds(), _mappings(db), files_root=tmp_path)
    return db


def _rows(db: Path, table: str) -> list[tuple]:
    conn = sqlite3.connect(db)
    try:
        return conn.execute(f"SELECT * FROM {table} ORDER BY id").fetchall()
    finally:
        conn.close()


# -- the store ------------------------------------------------------------------------

def test_a_message_and_its_items_are_one_document_whose_versions_are_its_steps():
    mid = run_store.create_message("sess-1", "anthropic", "hammer", "tools", "JARVIS: use Postgres",
                                   capture_id=4)
    to = cl.append(ConversationItem.new(ItemKind.TO_AGENT, "use Postgres", agent_session_id="sess-1",
                                        agent_name="hammer", message_id=mid, capture_id=4))
    run_store.update_message(mid, status="sent", sent_at=time.time())
    run_store.update_message(mid, status="answered", reply="Done.")
    answer = cl.inbound(ItemKind.ANSWER, Counterpart.AGENT, "sess-1", "hammer", "Done.", "watcher",
                        source_kind=cl.Source.MESSAGE, source_id=mid, message_id=mid)
    [doc] = mr.message_docs([["message.id", "=", mid]])
    assert doc.path == f"/agents/sess-1/messages/m{mid}.message"
    assert [i["id"] for i in doc.data["items"]] == [to.id, answer.id]
    assert doc.data["message"]["status"] == "answered"
    steps = [v.change for v in records.history(doc.path)]
    assert steps == ["staged", "to_agent", "sent", "answered", "answer"]
    assert [v.data["message"]["status"] for v in records.history(doc.path, fields=["message"])] == [
        "staged", "staged", "sent", "answered", "answered"]
    assert cl.item_for_message(mid).id == to.id
    assert [i.kind for i in cl.thread("sess-1")] == [ItemKind.TO_AGENT, ItemKind.ANSWER]


def test_turns_are_documents_by_day_and_ids_keep_counting():
    a = cl.append(ConversationItem.new(ItemKind.HEARD, "good morning"))
    b = cl.append(ConversationItem.new(ItemKind.JARVIS, "Morning, sir."))
    assert (a.id, b.id) == (1, 2)
    day = time.strftime("%Y-%m-%d", time.localtime(a.at))
    rec = records.get(f"/conversation/{day}/1.turn")
    assert rec.data["text"] == "good morning" and rec.data["detail"] == {}
    assert records.history(rec.path)[0].agent is None, "his own words are his"
    assert records.get(mr.counter_path("conversation_items")).data["next"] == 3


def test_a_message_from_another_counterpart_lives_under_its_thread():
    item = cl.inbound(ItemKind.MESSAGE_IN, Counterpart.WORK, "work-1", "Speech", "Decide?",
                      "access_key", source_kind=cl.Source.WORK_DECISION, source_id="work-1:model",
                      sender="key:planning", about="work-1")
    [doc] = mr.message_docs([["items[].id", "=", item.id]])
    assert doc.path == f"/messages/work/work-1/c{item.id}.message"
    assert records.history(doc.path)[0].agent == "key:planning"
    moved = cl.mark(item.id, cl.MessageState.READ)
    assert moved.state == "read" and cl.get(item.id).state == "read"
    assert [v.change for v in records.history(doc.path)] == ["message_in", "read"]


def test_the_queue_survives_and_staged_ones_are_swept():
    q = run_store.create_message("s", "anthropic", "a", "p", "x", status=run_store.MessageStatus.QUEUED)
    s = run_store.create_message("s", "anthropic", "a", "p", "y")
    assert [m.id for m in run_store.queued_messages()] == [q]
    assert run_store.sweep_staged_messages() == 1
    assert run_store.get_message(s).status == "failed"
    assert run_store.get_message(s).failure == "the server restarted before it was sent"
    assert [m.id for m in run_store.list_messages(agent_name="A")] == [s, q]


def test_reads_page_back_through_a_long_conversation():
    for n in range(300):
        cl.append(ConversationItem.new(ItemKind.HEARD, f"line {n}"))
    shown = cl.page(limit=100)
    assert [i.text for i in shown] == [f"line {n}" for n in range(200, 300)]
    earlier = cl.page(before=shown[0].id, limit=150)
    assert [i.text for i in earlier] == [f"line {n}" for n in range(50, 200)]
    assert [i.text for i in cl.after(290)] == [f"line {n}" for n in range(290, 300)]


# -- the move ------------------------------------------------------------------------

def _every_column(i: int) -> dict:
    """Rows filling every column of the three tables (i == 2: every column
    that may be NULL is NULL)."""
    null = i == 2
    message = dict(id=10 + i, session_id=f"sess-{i}", provider="anthropic", agent_name=f"Agent {i}",
                   project="tools", capture_id=None if null else 3 + i, text=f"JARVIS: do {i}",
                   status="answered", staged_at=1700000000.0 + i,
                   sent_at=None if null else 1700000001.5, delivered_at=None if null else 1700000002.0,
                   started_at=None if null else 1700000003.0,
                   answered_at=None if null else 1700000004.0, reply=None if null else "Done.",
                   failure=None if null else "late")
    session_message = dict(id=20 + i, project="tools", agent_name=f"Agent {i}", subject="Report",
                           body="Body /Users/tim", received_at=1700000005.0 + i,
                           read_at=None if null else 1700000006.0,
                           sender_session=None if null else f"sess-{i}",
                           sender_evidence=None if null else "transcript",
                           sender_subagent=None if null else "Feynman")
    details = ['{"b": 1, "a": [1.5, "x"]}', '{}', 'not json', '{"_hidden": 1}', '{"a": 1}']
    items = []
    kinds = ("to_agent", "answer", "from_agent", "message_in", "heard", "notice", "odd kind")
    for k, kind in enumerate(kinds):
        n = 100 * (i + 1) + k
        items.append(dict(
            id=n, at=1700000000.0 + n, kind=kind, text=f"{kind} {n} /agents/x",
            agent_session_id=None if null else f"sess-{i}", agent_name=None if null else "Agent",
            message_id=(10 + i) if kind in ("to_agent", "answer") else (None if null else 999),
            session_message_id=(20 + i) if kind == "from_agent" else None,
            capture_id=None if null else 7, detail=details[k % len(details)],
            counterpart_kind=None if null else ("work" if kind == "message_in" else "agent"),
            counterpart_ref=None if null else ("work-1" if kind == "message_in" else f"sess-{i}"),
            sender_evidence=None if null else "watcher",
            sender_subagent=None if null else "Feynman", source_kind=None if null else "message",
            source_id=None if null else f"src-{n}", sender=None if null else "agent:sess",
            sender_name=None if null else "Agent", about=None if null else "work-1",
            carries=None if null else "inbox-1", state=None if null else "new",
            state_at=None if null else 1700000009.0))
    return {"messages": [message], "session_messages": [session_message],
            "conversation_items": items}


def test_the_move_is_lossless_for_every_column_nulls_included(tmp_path):
    rows: dict = {t: [] for t in TABLES}
    for i in range(3):
        for t, found in _every_column(i).items():
            rows[t] += found
    rows["conversation_items"].append(dict(id=900, at=5.0, kind="answer", text="orphan answer",
                                           message_id=77, detail="{}"))
    db = moved(tmp_path, rows)
    report = move.verify(db, records.uds(), _mappings(db), files_root=tmp_path)
    assert report.ok, report.words()
    out = tmp_path / "back.db"
    move.export_sqlite(records.uds(), _mappings(db), out, files_root=tmp_path)
    for t in TABLES:
        assert _rows(out, t) == _rows(db, t), t
    # the store reads the moved documents
    assert run_store.get_message(10).reply == "Done." and run_store.get_message(12).reply is None
    assert run_store.get_session_message(22).read_at is None
    assert cl.get(100).detail == {"b": 1, "a": [1.5, "x"]}
    assert list(cl.get(100).detail) == ["b", "a"], "the detail's own order"
    assert cl.statuses([cl.get(100)])[10].status == "answered"
    assert cl.item_for_message(10).id == 100


def test_writes_after_the_move_export_back(tmp_path):
    db = moved(tmp_path, _every_column(0))
    mid = run_store.create_message("sess-9", "openai", "Anvil", "p", "hello")
    item = cl.append(ConversationItem.new(ItemKind.TO_AGENT, "hello", agent_session_id="sess-9",
                                          message_id=mid))
    assert mid == 11 and item.id == 107
    out = tmp_path / "back.db"
    move.export_sqlite(records.uds(), _mappings(db), out, files_root=tmp_path)
    assert [r[0] for r in _rows(out, "messages")] == [10, 11]
    assert _rows(out, "conversation_items")[-1][:4] == (107, item.at, "to_agent", "hello")


COPY = os.environ.get("JARVIS_G3A_COPY_DB")


@pytest.mark.skipif(not COPY, reason="the round trip on a copy of the live database runs by hand: "
                    "JARVIS_G3A_COPY_DB names a private copy")
def test_the_round_trip_on_a_copy_of_the_live_database(tmp_path):
    src = sqlite3.connect(f"file:{COPY}?mode=ro", uri=True)
    db = tmp_path / "three.db"
    dst = sqlite3.connect(db)
    for (sql,) in src.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name IN "
                              "(?, ?, ?)", TABLES):
        dst.execute(sql)
    for t in TABLES:
        found = src.execute(f"SELECT * FROM {t}").fetchall()
        if found:
            dst.executemany(f"INSERT INTO {t} VALUES ({','.join('?' * len(found[0]))})", found)
    dst.commit()
    dst.close()
    started = time.monotonic()
    move.copy(db, records.uds(), _mappings(db), files_root=tmp_path)
    copied = time.monotonic() - started
    report = move.verify(db, records.uds(), _mappings(db), files_root=tmp_path)
    print(f"\ncopy {copied:.1f}s, verify {report.seconds:.1f}s: {report.words()}")
    assert report.ok, report.words()
    out = tmp_path / "back.db"
    move.export_sqlite(records.uds(), _mappings(db), out, files_root=tmp_path)
    for t in TABLES:
        assert _rows(out, t) == _rows(db, t), t
    # the store reads them right, spot-checked against SQL on the copy
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    newest = [r["text"] for r in conn.execute(
        "SELECT text FROM conversation_items WHERE kind <> 'message_in' ORDER BY id DESC LIMIT 100")]
    started = time.monotonic()
    assert [i.text for i in cl.page()] == list(reversed(newest))
    print(f"page {time.monotonic() - started:.2f}s")
    last = conn.execute("SELECT * FROM messages ORDER BY id DESC LIMIT 1").fetchone()
    assert run_store.get_message(last["id"]).text == last["text"]
    sid = conn.execute("SELECT agent_session_id FROM conversation_items WHERE kind='answer' "
                       "GROUP BY agent_session_id ORDER BY COUNT(*) DESC LIMIT 1").fetchone()[0]
    want = [r["id"] for r in conn.execute(
        "SELECT id FROM conversation_items WHERE agent_session_id=? AND kind <> 'message_in' "
        "ORDER BY id DESC LIMIT 100", (sid,))]
    started = time.monotonic()
    assert [i.id for i in cl.thread(sid)] == list(reversed(want))
    print(f"thread {time.monotonic() - started:.2f}s")
    assert [m.id for m in run_store.list_messages(limit=20)] == [r["id"] for r in conn.execute(
        "SELECT id FROM messages ORDER BY staged_at DESC, id DESC LIMIT 20")]
    started = time.monotonic()
    by_agent = cl.new_by_agent()
    print(f"new_by_agent {time.monotonic() - started:.3f}s")
    assert by_agent == {r[0][len("agent:"):]: r[1] for r in conn.execute(
        "SELECT sender, COUNT(*) FROM conversation_items WHERE state='new' AND sender LIKE "
        "'agent:%' GROUP BY sender")}
    assert len(cl.after(0, 500)) == 500
    conn.close()
