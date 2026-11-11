"""The Work tracker's, the workflow monitor's and the package inbox's
records as documents (round 3, records-as-documents): their ten tables move
into the engine through their mappings and back, every row exactly as it
was (NULLs, rowids, JSON text as written), and the stores read the moved
documents and write on from them."""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

import pytest

from jarvis import knowledge_move, package_inbox, records, work_store, workflow_store

SCHEMA = json.loads((Path(__file__).parent.parent / "jarvis" / "sqlite_schema.json").read_text())
MINE = work_store.TABLES + workflow_store.TABLES + package_inbox.TABLES


def _make(db: Path) -> None:
    """A file with the ten tables as JARVIS made them, their indexes too."""
    conn = sqlite3.connect(db)
    for o in SCHEMA["schema"]["objects"]:
        if o["sql"] and o["tbl_name"] in MINE:
            conn.execute(o["sql"])
    conn.commit()
    conn.close()


def _insert(conn, table: str, rowid, **cells) -> None:
    names = (["rowid"] if rowid is not None else []) + list(cells)
    values = ([rowid] if rowid is not None else []) + list(cells.values())
    conn.execute(f"INSERT INTO {table} ({', '.join(names)}) VALUES "
                 f"({', '.join('?' * len(values))})", values)


def _fill(db: Path) -> None:
    """Every column of every table filled, and every nullable one NULL in
    another row; rowids out of order; JSON text as the old code and the
    migrations wrote it (keys in their own order, no spaces)."""
    conn = sqlite3.connect(db)
    item = dict(title="Speech", source_kind="folder", source_path="/tmp/speech",
                state="verifying", latest_update="In.", revision=4, created_at=1.5,
                updated_at=2.5, area="voice", closed_at=3.0, until='["decision:merge"]',
                then_state="ready", waiting_since=1.75, reported_at=2.0, approved_at=2.25,
                review_note="Again.", review_note_at=2.5, error="Held.", error_at=2.75)
    _insert(conn, "work_items", 9, id="work-a", **item)
    _insert(conn, "work_items", 4, id="work-b", **{**item, "closed_at": None, "until": "",
                                                   "waiting_since": None, "reported_at": None,
                                                   "approved_at": None, "review_note_at": None,
                                                   "error_at": None, "state": "waiting_on_tim"})
    piece = dict(title="Land", who_kind="agent", who_ref="sess-1", state="waiting_on_tim",
                 position=0, note="Waits.", note_at=1.5, worker="Feynman",
                 after_keys='["a"]', evidence='[{"kind": "commit", "value": "abcdef1"}]',
                 until='["decision:open","words here"]', then_state="working",
                 waiting_since=1.5, reporter="key:planning")
    _insert(conn, "work_pieces", 12, item_id="work-b", key="land", **piece)
    _insert(conn, "work_pieces", 3, item_id="work-b", key="a",
            **{**piece, "who_kind": "tim", "who_ref": None, "note_at": None, "position": 1,
               "waiting_since": None, "after_keys": "[]", "evidence": "[]", "until": ""})
    decision = dict(question="Merge?", options='["Yes", "No"]', recommendation="Yes",
                    answer="Yes", answered_where="voice", opened_at=1.0, answered_at=1.25,
                    package="tea-deck", asked_by="agent:sess-1", withdrawn_at=1.3,
                    withdrawn_reason="Moot.", signed_off_at=1.4)
    _insert(conn, "work_decisions", 5, item_id="work-b", key="merge", **decision)
    _insert(conn, "work_decisions", 2, item_id="work-b", key="open",
            **{**decision, "answer": None, "answered_where": None, "answered_at": None,
               "withdrawn_at": None, "withdrawn_reason": None, "signed_off_at": None})
    for seq, (item_id, kind, detail) in enumerate((
            ("work-a", "created", '{"source_kind": "folder", "source_path": "/tmp/speech"}'),
            ("work-b", "created", '{"state":"planned","area":""}'),
            ("work-b", "approved", '{"by": "tim", "where": "package", '
                                   '"source": {"package": "pkg-1", "answered_at": "7"}}'),
            ("work-a", "piece", '{"key": "land", "z": 1, "a": [2, {"y": 1, "b": 2}]}')), start=3):
        _insert(conn, "work_events", None, seq=seq * 2, item_id=item_id, kind=kind,
                summary=f"{kind}.", detail=detail, at=float(seq), reporter="key:x" if seq % 2 else "")
    flow = dict(name="Build", project="jarvis", state="needs_you", phase="plan",
                agents='["sess-1", "sess-2"]', started_at=1.0, last_event_at=2.0,
                last_summary="Asked.", finished_at=3.0, dismissed_at=3.5,
                reporter="agent:sess-1", work="work-a")
    _insert(conn, "workflows", 7, id="wf-a", **flow)
    _insert(conn, "workflows", 2, id="wf-b", **{**flow, "phase": None, "finished_at": None,
                                                "dismissed_at": None, "agents": "[]"})
    _insert(conn, "workflow_items", 8, workflow_id="wf-b", slug="plan", kind="phase", phase=None,
            title="Plan", state="running", done=None, total=None, position=0)
    _insert(conn, "workflow_items", 1, workflow_id="wf-b", slug="spec", kind="task", phase="plan",
            title="Spec", state="running", done=1, total=3, position=1)
    _insert(conn, "workflow_open", 6, workflow_id="wf-b", kind="wait", key="go",
            summary="Waiting.", detail='{"key": "go", "until": "decision:go", "link": "decision:go"}',
            opened_at=1.5)
    _insert(conn, "workflow_open", 3, workflow_id="wf-b", kind="decision", key="go",
            summary="Go?", detail="{}", opened_at=1.25)
    for seq, (wid, kind, item_slug, detail) in enumerate((
            ("wf-b", "workflow_started", None, '{"phases": ["plan"]}'),
            ("wf-b", "task_progress", "spec", '{"total": 3, "done": 1}'),
            ("wf-a", "decision_made", None, '{"key": "go", "answer": "yes", "by": "user", '
                                            '"how": "voice"}')), start=1):
        _insert(conn, "workflow_events", None, seq=seq * 3, workflow_id=wid, kind=kind,
                item=item_slug, summary=f"{kind}.", detail=detail, at=float(seq),
                reporter="agent:sess-1" if seq == 1 else "")
    entry = dict(package_id="tea-deck-1.0.0-abc", sha256="ab" * 32, name="tea-deck",
                 version="1.0.0", title="Tea", purpose="Pick a tea.", runs="page",
                 decisions_total=2, decisions_answered=1, from_project="tea",
                 from_session="sess-1", from_agent="chitauri", workflow_id="wf-a",
                 work_id="work-a", project_id="proj-1", state="closed", revision=5,
                 capture_id=11, return_asked_at=2.0, message_id=12, arrived_at=1.0,
                 opened_at=1.1, answered_at=1.2, returned_at=2.1, closed_at=2.2,
                 closed_why="signed_off", signed_off_at=2.2, claimed_session="sess-1",
                 sender_evidence="agent_signature", reprove=1)
    _insert(conn, "package_inbox", 30, id="pkg-a", **entry)
    _insert(conn, "package_inbox", 10, id="pkg-b", **{
        **entry, "workflow_id": None, "work_id": None, "project_id": None, "capture_id": None,
        "return_asked_at": None, "message_id": None, "opened_at": None, "answered_at": None,
        "returned_at": None, "closed_at": None, "closed_why": None, "signed_off_at": None,
        "state": "arrived", "reprove": 0})
    for seq, (inbox_id, kind, revision) in enumerate((("pkg-a", "arrived", 1),
                                                      ("pkg-b", "arrived", 1),
                                                      ("pkg-a", "signed_off", 2)), start=1):
        _insert(conn, "package_inbox_events", None, seq=seq * 5, inbox_id=inbox_id, kind=kind,
                revision=revision, at=float(seq), how="on the page")
    conn.commit()
    conn.close()


def _rows(db: Path) -> dict:
    conn = sqlite3.connect(db)
    try:
        return {t: sorted(r.canonical() for r in knowledge_move.read_rows(
            conn, knowledge_move.table_info(conn, t))) for t in MINE}
    finally:
        conn.close()


@pytest.fixture
def moved(tmp_path):
    db = tmp_path / "jarvis.db"
    _make(db)
    _fill(db)
    knowledge_move.copy(db, records.uds())
    return db


def test_every_column_of_the_ten_tables_moves_and_comes_back_exactly(moved, tmp_path):
    report = knowledge_move.verify(moved, records.uds())
    assert report.ok, report.words()
    assert {t.name for t in report.tables} >= set(MINE)
    out = tmp_path / "back.db"
    knowledge_move.export_sqlite(records.uds(), None, out)
    assert _rows(out) == _rows(moved)


def test_the_stores_read_what_was_moved(moved):
    item = work_store.get("work-b")
    assert [p.key for p in item.pieces] == ["a", "land"], "land comes after a"
    assert item.pieces[1].evidence == [work_store.Evidence("commit", "abcdef1")]
    assert item.pieces[1].until == ["decision:open", "words here"]
    assert [d.key for d in item.decisions] == ["open"] and item.answered[0].key == "merge"
    assert [e.seq for e in work_store.events("work-b")] == [8, 10]
    assert [e.seq for e in work_store.latest("work-b", 1)] == [10]
    assert item.package_review is not None and item.package_review.package == "pkg-1"
    assert work_store.events("work-a")[1].detail == {"key": "land", "z": 1,
                                                     "a": [2, {"y": 1, "b": 2}]}
    snap = workflow_store.snapshot("wf-b")
    assert [i.slug for i in snap.items] == ["plan", "spec"] and snap.open_decisions == ["go"]
    assert snap.items[1].heard_at == 2.0
    assert [e.seq for e in workflow_store.events("wf-b")] == [3, 6]
    entry = package_inbox.get("pkg-a")
    assert (entry.capture_id, entry.reprove, entry.closed_why) == (11, 1, "signed_off")
    assert [e["seq"] for e in package_inbox.events("pkg-a")] == [5, 15]
    assert [e.id for e in package_inbox.listing()] == ["pkg-b"]


def test_what_is_written_after_the_move_comes_back_as_rows_too(moved, tmp_path):
    work_store.record(work_store.WorkEvent("work-b", "update", "Moving on.", {"area": "speech"}))
    workflow_store.record(workflow_store.WorkflowEvent("wf-b", "note", None, "A note.", {}))
    assert work_store.events("work-b")[-1].seq == 11, "the item's own next number"
    out = tmp_path / "back.db"
    knowledge_move.export_sqlite(records.uds(), None, out)
    conn = sqlite3.connect(out)
    seqs = [r[0] for r in conn.execute("SELECT seq FROM work_events ORDER BY seq")]
    assert seqs == [6, 8, 10, 11, 12]
    assert conn.execute("SELECT area FROM work_items WHERE id='work-b'").fetchone()[0] == "speech"
    # The workflow's next is 7; work_events has no 7, but the numbers are each
    # table's own: workflow_events gives it 7.
    assert [r[0] for r in conn.execute("SELECT seq FROM workflow_events ORDER BY seq")] == [
        3, 6, 7, 9]
    conn.close()


def test_two_records_numbering_the_same_event_alike_come_back_apart(tmp_path):
    """Each record numbers its own events; a number two of them used is
    given to the older one, the newer one numbered after the largest."""
    from jarvis import record_cells
    events = [{"seq": 4, "at": 2.0}, {"seq": 4, "at": 1.0}, {"seq": 9, "at": 0.5},
              {"seq": None, "at": 0.1}]
    record_cells.resolve_seqs(events)
    assert [e["seq"] for e in events] == [11, 4, 9, 10]


def test_an_answer_and_the_waits_it_ends_land_in_one_write(tmp_path):
    """His answer on a Work item and the workflow wait linked to it: one
    unit, so both documents move together or neither does."""
    folder = tmp_path / "f"
    folder.mkdir()
    item = work_store.create("Speech", "folder", str(folder))
    work_store.record(work_store.WorkEvent(item.id, "decision_needed", "Merge?", {"key": "merge"}))
    wid = workflow_store.start("Build", "jarvis", [])
    workflow_store.record(workflow_store.WorkflowEvent(
        wid, "wait_started", None, "Waiting.", {"key": "m", "until": f"work:{item.id}:merge"}))
    before = records.get(workflow_store.path_of(wid)).version
    work_store.record(work_store.WorkEvent(item.id, "decision_made", "Yes.",
                                           {"key": "merge", "answer": "yes"}))
    [answered] = [v for v in records.history(work_store.path_of(item.id))
                  if v.change == "decision_made"]
    [ended] = [v for v in records.history(workflow_store.path_of(wid))
               if v.version > before]
    assert ended.lsn == answered.lsn, "one transaction"
    assert workflow_store.snapshot(wid).open_waits == []
