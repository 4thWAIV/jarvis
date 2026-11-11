"""Runs as documents (UDS round 3): a run is `/runs/<id>.run`, its output
stream the file `data/runs/<id>.jsonl`, and the old `runs` and `run_events`
tables move into both and back without losing a cell."""
import json
import os
import sqlite3
import time
from pathlib import Path

import pytest

from jarvis import data_paths, knowledge_move as km, records, run_store

SCHEMA = json.loads((Path(__file__).parent.parent / "jarvis" / "sqlite_schema.json").read_text())


# -- the run document ------------------------------------------------------------

def test_a_run_is_one_document_with_every_field():
    run_id = run_store.create_run("build", "proj", "/tmp/proj", "voice", package_id="pkg-1")
    rec = records.get(f"/runs/{records.segment(run_id)}.run")
    assert rec is not None
    assert set(rec.data) == set(run_store.RUN_FIELDS)
    assert rec.data["status"] == "queued" and rec.data["package_id"] == "pkg-1"
    assert isinstance(rec.data["cost_usd"], float) and rec.data["is_error"] == 0
    assert list(run_store.get_run(run_id)) == list(run_store.RUN_FIELDS)


def test_each_write_is_tagged_with_its_change():
    run_id = run_store.create_run("build", "proj", "/tmp/proj", "voice")
    run_store.update_run(run_id, status=run_store.RunStatus.RUNNING, pid=7)
    run_store.update_run(run_id, model="opus")
    run_store.finish_run(run_id, run_store.RunStatus.SUCCEEDED)
    changes = [v.change for v in records.history(f"/runs/{records.segment(run_id)}.run")]
    assert changes == ["created", "status:running", "update", "status:succeeded"]
    assert all(v.agent == "jarvis" for v in records.history(f"/runs/{records.segment(run_id)}.run"))


def test_fields_keep_their_column_kinds():
    run_id = run_store.create_run("build", "proj", "/tmp/proj", "voice")
    run_store.update_run(run_id, cost_usd=1, is_error=True, exit_code=0.0, input_tokens=3)
    run = run_store.get_run(run_id)
    assert run["cost_usd"] == 1.0 and isinstance(run["cost_usd"], float)
    assert run["is_error"] == 1 and type(run["is_error"]) is int
    assert run["exit_code"] == 0 and type(run["exit_code"]) is int


def test_update_of_a_missing_run_writes_nothing():
    run_store.update_run("nope", status="running")
    assert run_store.get_run("nope") is None
    assert run_store.list_runs() == []


def test_finish_run_first_writer_wins():
    run_id = run_store.create_run("build", "proj", "/tmp/proj", "voice")
    first = run_store.finish_run(run_id, run_store.RunStatus.CANCELLED)
    assert first["status"] == "cancelled" and first["ended_at"] is not None
    assert run_store.finish_run(run_id, run_store.RunStatus.SUCCEEDED, exit_code=0) is None
    assert run_store.get_run(run_id)["status"] == "cancelled"
    assert run_store.finish_run("missing", run_store.RunStatus.FAILED) is None
    with pytest.raises(ValueError):
        run_store.finish_run(run_id, "failed", bogus=1)


def test_list_runs_filters_as_the_query_did():
    a = run_store.create_run("a", "Alpha-Site", "/tmp/a", "voice", package_id="p1")
    time.sleep(0.01)
    b = run_store.create_run("b", "beta", "/tmp/b", "voice", package_id="P1")
    time.sleep(0.01)
    c = run_store.create_run("c", "alphabet", "/tmp/c", "voice")
    run_store.update_run(b, status="running")
    # package_id is exact (the engine folds text; the store checks again)
    assert [r["id"] for r in run_store.list_runs(package_id="p1")] == [a]
    # project is LIKE '%x%': a substring, ASCII case ignored
    assert [r["id"] for r in run_store.list_runs(project="ALPHA")] == [c, a]
    assert [r["id"] for r in run_store.list_runs(status=["running"])] == [b]
    assert [r["id"] for r in run_store.list_runs(status=["RUNNING"])] == []
    created_b = run_store.get_run(b)["created_at"]
    assert [r["id"] for r in run_store.list_runs(before=created_b)] == [a]
    assert [r["id"] for r in run_store.list_runs(limit=2)] == [c, b]
    assert len(run_store.list_runs(limit=-1)) == 3
    assert run_store.list_runs(limit=0) == []
    assert run_store.all_run_ids() == {a, b, c}


def test_sweep_counts_only_runs_still_active():
    a = run_store.create_run("a", "p", "/tmp/p", "voice")
    b = run_store.create_run("b", "p", "/tmp/p", "voice")
    run_store.update_run(b, status="succeeded")
    assert run_store.sweep_stale_runs() == 1
    assert run_store.get_run(a)["status"] == "failed"
    assert run_store.sweep_stale_runs() == 0


# -- the events file ------------------------------------------------------------

def _file(run_id):
    return data_paths.data_dir() / "runs" / f"{records.segment(run_id)}.jsonl"


def test_events_are_json_lines_in_the_run_s_file():
    run_id = run_store.create_run("a", "p", "/tmp/p", "voice")
    assert not _file(run_id).exists()
    run_store.append_events(run_id, [(1, "system", '{"a":1}'), (2, "assistant", '{"b":"é"}')])
    lines = _file(run_id).read_text(encoding="utf-8").splitlines()
    assert [json.loads(x) for x in lines] == [
        {"seq": 1, "ts": json.loads(lines[0])["ts"], "kind": "system", "payload": '{"a":1}'},
        {"seq": 2, "ts": json.loads(lines[1])["ts"], "kind": "assistant", "payload": '{"b":"é"}'}]
    got = run_store.get_events(run_id)
    assert [(e["id"], e["run_id"], e["seq"], e["kind"], e["payload"]) for e in got] == [
        (None, run_id, 1, "system", '{"a":1}'), (None, run_id, 2, "assistant", '{"b":"é"}')]


def test_a_seq_already_there_is_passed_over():
    run_id = run_store.create_run("a", "p", "/tmp/p", "voice")
    run_store.append_events(run_id, [(1, "system", "first"), (1, "system", "again")])
    run_store.append_event(run_id, 1, "system", "later")
    run_store.append_event(run_id, 2, "result", "{}")
    assert [(e["seq"], e["payload"]) for e in run_store.get_events(run_id)] == [
        (1, "first"), (2, "{}")]
    assert run_store.next_seq(run_id) == 3


def test_events_come_back_in_seq_order_and_a_negative_limit_is_none():
    run_id = run_store.create_run("a", "p", "/tmp/p", "voice")
    for seq in (3, 1, 2):
        run_store.append_event(run_id, seq, "assistant", "{}")
    assert [e["seq"] for e in run_store.get_events(run_id)] == [1, 2, 3]
    assert [e["seq"] for e in run_store.get_events(run_id, limit=-1)] == [1, 2, 3]
    assert [e["seq"] for e in run_store.get_events(run_id, limit=2)] == [1, 2]
    assert run_store.get_events("no-such-run") == [] and run_store.count_events("no-such-run") == 0


def test_a_torn_last_line_is_passed_over_and_the_next_append_starts_clean():
    run_id = run_store.create_run("a", "p", "/tmp/p", "voice")
    run_store.append_event(run_id, 1, "system", "{}")
    with open(_file(run_id), "ab") as f:
        f.write(b'{"seq":2,"ts":1.0,"ki')               # a crash mid-append
    assert run_store.count_events(run_id) == 1
    run_store.append_event(run_id, 2, "result", "{}")
    assert [e["seq"] for e in run_store.get_events(run_id)] == [1, 2]


def test_a_change_made_to_the_file_elsewhere_is_read():
    run_id = run_store.create_run("a", "p", "/tmp/p", "voice")
    run_store.append_event(run_id, 1, "system", "{}")
    assert run_store.count_events(run_id) == 1
    time.sleep(0.01)
    with open(_file(run_id), "ab") as f:
        f.write(b'{"seq":2,"ts":1.0,"kind":"result","payload":"{}"}\n')
    assert run_store.count_events(run_id) == 2


def test_the_runs_folder_is_watched_as_content():
    from jarvis import knowledge_content
    run_id = run_store.create_run("a", "p", "/tmp/p", "voice")
    run_store.append_event(run_id, 1, "system", "{}")
    roots = {r.prefix: r for r in knowledge_content.jarvis_roots()}
    root = roots["/jarvis/runs"]
    assert root.path == os.path.realpath(data_paths.runs_dir())
    assert knowledge_content.wanted(root, _file(run_id).name)
    assert knowledge_content.item_path(root, _file(run_id).name) == f"/jarvis/runs/{_file(run_id).name}"
    assert root.closed_only                     # read once the run has finished, never while it grows


def test_a_run_s_file_is_closed_when_the_run_finishes():
    from jarvis import closed_files
    run_id = run_store.create_run("a", "p", "/tmp/p", "voice")
    run_store.update_run(run_id, status=run_store.RunStatus.RUNNING)
    run_store.append_event(run_id, 1, "system", "{}")
    assert not closed_files.closed(_file(run_id))
    run_store.finish_run(run_id, run_store.RunStatus.SUCCEEDED)
    assert closed_files.closed(_file(run_id))
    # A last event racing the finish still goes in, and the file is closed again.
    run_store.append_event(run_id, 2, "result", "{}")
    assert closed_files.closed(_file(run_id))
    assert [e["seq"] for e in run_store.get_events(run_id)] == [1, 2]


def test_a_run_swept_after_a_restart_has_its_file_closed():
    from jarvis import closed_files
    run_id = run_store.create_run("a", "p", "/tmp/p", "voice")
    run_store.update_run(run_id, status=run_store.RunStatus.RUNNING)
    run_store.append_event(run_id, 1, "system", "{}")
    assert run_store.sweep_stale_runs() == 1
    assert closed_files.closed(_file(run_id))


def test_finished_runs_files_left_open_are_closed_at_start():
    """Files from before runs closed them, and a crash between a finish and
    its close: closed at start, every one but a run still going's."""
    from jarvis import closed_files
    going = run_store.create_run("a", "p", "/tmp/p", "voice")
    done = run_store.create_run("b", "p", "/tmp/p", "voice")
    for run_id in (going, done):
        run_store.append_event(run_id, 1, "system", "{}")
    records.update(f"/runs/{records.segment(done)}.run",
                   lambda d: {**d, "status": run_store.RunStatus.FAILED}, by=records.JARVIS)
    orphan = data_paths.runs_dir() / "gone-run.jsonl"
    orphan.write_text('{"seq":1,"ts":1.0,"kind":"system","payload":"{}"}\n')
    assert run_store.close_finished_outputs() == 2
    assert [closed_files.closed(p) for p in (_file(going), _file(done), orphan)] == [False, True, True]
    assert run_store.close_finished_outputs() == 0


# -- the move: runs and run_events to documents and files, and back -----------

def _table_sql(name):
    return next(o["sql"] for o in SCHEMA["schema"]["objects"] if o["type"] == "table" and o["name"] == name)


def _synthetic_db(path: Path) -> None:
    """Today's `runs` and `run_events` tables, every column filled in one
    row and NULL where it may be in another, an id that is no uuid, and a
    payload that is not plain ASCII."""
    conn = sqlite3.connect(path)
    conn.execute(_table_sql("runs"))
    conn.execute(_table_sql("run_events"))
    cols = [r[1] for r in conn.execute("PRAGMA table_info(runs)")]
    full = {"id": "run-a", "project_name": "proj", "project_path": "/tmp/proj", "prompt": "p\nq",
            "origin": "voice", "status": "succeeded", "resume_from": "run-0", "result_text": "done",
            "summary": "s", "error": "", "exit_code": 0, "pid": 4242, "cost_usd": 0.125,
            "input_tokens": 10, "output_tokens": 20, "cache_read_tokens": 30,
            "cache_creation_tokens": 40, "num_turns": 2, "model": "opus", "requested_model": "opus",
            "is_error": 1, "created_at": 1700000000.5, "started_at": 1700000001.25,
            "ended_at": 1700000002.0, "folder_id": "f-1", "package_id": "pkg", "context_sha256": "ab" * 32,
            "project_id": "proj-1", "work_id": "work-1"}
    assert set(full) == set(cols)
    sparse = {"id": "odd/id. with spaces", "project_name": "x", "project_path": "", "prompt": "",
              "origin": "api", "status": "running", "created_at": 1700000003.0}
    for row in (full, sparse):
        conn.execute(f"INSERT INTO runs ({', '.join(row)}) VALUES ({', '.join('?' * len(row))})",
                     list(row.values()))
    conn.execute("UPDATE runs SET resume_from=NULL, exit_code=NULL, pid=NULL, cost_usd=NULL, "
                 "input_tokens=NULL, model=NULL, is_error=NULL, started_at=NULL WHERE id=?",
                 ("odd/id. with spaces",))
    events = [("run-a", 1, 1700000001.5, "system", '{"type":"system"}'),
              ("run-a", 2, 1700000001.75, "assistant", '{"t":"héllo ✓"}'),
              ("odd/id. with spaces", 1, 1700000003.5, "result", "{}"),
              ("orphan", 5, 1.0, "x", "")]
    conn.executemany("INSERT INTO run_events (run_id, seq, ts, kind, payload) VALUES (?,?,?,?,?)", events)
    conn.execute("DELETE FROM run_events WHERE seq = 5")      # a gap in the ids
    conn.execute("INSERT INTO run_events (run_id, seq, ts, kind, payload) VALUES (?,?,?,?,?)",
                 ("orphan", 6, 2.0, "x", "y"))
    conn.commit()
    conn.close()


def _rows(path: Path, table: str) -> list[tuple]:
    conn = sqlite3.connect(path)
    try:
        order = "rowid" if table == "runs" else "id"
        return [tuple((type(v).__name__, v) for v in r) for r in
                conn.execute(f"SELECT rowid, * FROM {table} ORDER BY {order}")]
    finally:
        conn.close()


def _mappings():
    return [run_store.RunMapping(), km.RowMapping("sqlite_sequence")]


def test_the_tables_move_into_documents_and_files_and_back(tmp_path):
    db = tmp_path / "source.db"
    _synthetic_db(db)
    files_root = data_paths.data_dir()
    uds = records.uds()
    report = km.copy(db, uds, _mappings(), files_root=files_root)
    assert report.state == "copied"
    check = km.verify(db, uds, _mappings(), files_root=files_root)
    assert check.ok, check.words()

    out = tmp_path / "back.db"
    km.export_sqlite(uds, _mappings(), out, files_root=files_root)
    for table in ("runs", "run_events"):
        assert _rows(out, table) == _rows(db, table), table

    # The store reads what was moved.
    full = run_store.get_run("run-a")
    assert full["cost_usd"] == 0.125 and full["work_id"] == "work-1" and "rowid" not in full
    odd = run_store.get_run("odd/id. with spaces")
    assert odd["pid"] is None and odd["is_error"] is None and odd["result_text"] == ""
    assert [e["payload"] for e in run_store.get_events("run-a")] == ['{"type":"system"}', '{"t":"héllo ✓"}']
    assert [e["id"] for e in run_store.get_events("run-a")] == [1, 2]
    assert run_store.count_events("orphan") == 1
    assert run_store.all_run_ids() == {"run-a", "odd/id. with spaces"}

    # A run and events written after the cut-over export too: new events
    # take the ids after the largest.
    new = run_store.create_run("n", "proj", "/tmp/proj", "voice")
    run_store.append_events(new, [(1, "system", "{}"), (2, "result", "{}")])
    run_store.append_event("run-a", 3, "result", "{}")
    later = tmp_path / "later.db"
    km.export_sqlite(uds, _mappings(), later, files_root=files_root)
    conn = sqlite3.connect(later)
    try:
        ids = conn.execute("SELECT id, run_id, seq FROM run_events WHERE id > 5 ORDER BY id").fetchall()
        assert [(r, s) for _, r, s in ids] == [(new, 1), (new, 2), ("run-a", 3)]
        assert [i for i, _, _ in ids] == [6, 7, 8]
        assert conn.execute("SELECT status, cost_usd FROM runs WHERE id=?", (new,)).fetchone() == ("queued", 0.0)
    finally:
        conn.close()


@pytest.mark.skipif(not os.environ.get("JARVIS_RUNS_COPY_DB"),
                    reason="set JARVIS_RUNS_COPY_DB to a private copy of jarvis.db")
def test_the_round_trip_on_a_copy_of_the_live_database(tmp_path):
    import shutil
    db = tmp_path / "copy.db"
    shutil.copy(os.environ["JARVIS_RUNS_COPY_DB"], db)
    conn = sqlite3.connect(db)
    for (name,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT IN "
                                "('runs', 'run_events', 'sqlite_sequence') AND name NOT LIKE 'sqlite_%'").fetchall():
        conn.execute(f'DROP TABLE "{name}"')
    conn.commit()
    conn.execute("VACUUM")
    conn.close()
    uds = records.uds()
    files_root = data_paths.data_dir()
    km.copy(db, uds, _mappings(), files_root=files_root)
    assert km.verify(db, uds, _mappings(), files_root=files_root).ok
    out = tmp_path / "back.db"
    km.export_sqlite(uds, _mappings(), out, files_root=files_root)
    for table in ("runs", "run_events"):
        assert _rows(out, table) == _rows(db, table), table
