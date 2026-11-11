"""The folder catalog's and the project records' tables moved into their
documents and back (`folder_store.MAPPINGS`, `project_store.MAPPINGS`,
through `knowledge_move`): every column of every table, NULLs included, a
row with no record to belong to, then the stores reading the moved
documents, and a change made after the move coming back as new rows.

`JARVIS_MOVE_COPY_DB` names a copy of a live database for the same round
trip on real rows (skipped without it).
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import time
from pathlib import Path

import pytest

from jarvis import folder_store, knowledge_move as move, project_store, records, run_store
from jarvis.folder_contract import Lifecycle
from jarvis.folder_service import FolderService

SCHEMA = Path(__file__).resolve().parent.parent / "jarvis" / "sqlite_schema.json"
MINE = tuple(folder_store.TABLES) + tuple(folder_store.DISCOVERY_TABLES) + tuple(project_store.TABLES)
NODE = "node-a"


def _schema(conn: sqlite3.Connection) -> None:
    objects = json.loads(SCHEMA.read_text())["schema"]["objects"]
    for o in objects:
        if o["tbl_name"] in MINE and o["sql"]:
            conn.execute(o["sql"])


def _loc_json(**kw) -> str:
    return json.dumps(kw)


def _source(path: Path) -> Path:
    from jarvis import workspace_ref
    conn = sqlite3.connect(path)
    _schema(conn)
    x = "/Users/tim/code/x"

    def ref(kind, rid):
        return workspace_ref.format(workspace_ref.ref(kind, rid, node="n1"))
    ins = conn.execute
    ins("INSERT INTO folders VALUES ('f1', 'github:1', 'X', 'source', 'active', 3, 1.5, 2.5, NULL)")
    ins("INSERT INTO folders VALUES ('f2', NULL, 'Gone', 'data', 'removed', 2, 1.0, 4.0, 4.0)")
    ins("INSERT INTO folder_locations VALUES ('l0', 'f1', 'n1', '/old', '/old', NULL, 1, 'missing', "
        "NULL, NULL, NULL, 1.5, 2.0)")
    ins("INSERT INTO folder_locations VALUES ('l1', 'f1', 'n1', ?, ?, '1:2', 2, 'available', 3.0, 3.0, "
        "'none', 2.0, NULL)", (x, x))
    ins("INSERT INTO folder_locations VALUES ('l2', 'f2', 'n1', '/g', '/g', NULL, 1, 'missing', 1.0, "
        "NULL, 'missing', 1.0, NULL)")
    ins("INSERT INTO folder_locations VALUES ('lx', 'nobody', 'n1', '/o', '/o', NULL, 1, 'missing', "
        "NULL, NULL, NULL, 1.0, NULL)")
    ins("INSERT INTO folder_access_scopes VALUES ('s0', 'l0', 'files', '', 1, 0, 1.5, 2.0)")
    ins("INSERT INTO folder_access_scopes VALUES ('s1', 'l1', 'files', 'sub', 1, 1, 2.0, 2.0)")
    ins("INSERT INTO folder_access_scopes VALUES ('s2', 'l2', 'notes', '', 1, 0, 1.0, 4.0)")
    ins("INSERT INTO folder_location_events (folder_id, location_id, event, prior, new, at) VALUES "
        "('f1', 'l0', 'registered', '{}', ?, 1.5)",
        (_loc_json(location_id="l0", node_id="n1", supplied_path="/old", canonical_path="/old",
                   fs_identity=None, revision=1, availability="missing"),))
    ins("INSERT INTO folder_location_events (folder_id, location_id, event, prior, new, at) VALUES "
        "('f1', 'l1', 'availability', '{\"availability\": \"missing\"}', "
        "'{\"availability\": \"available\", \"error_code\": null}', 2.5)")
    ins("INSERT INTO folder_location_events (folder_id, location_id, event, prior, new, at) VALUES "
        "('f2', NULL, 'unregistered', '{\"lifecycle\": \"active\"}', '{\"lifecycle\": \"removed\"}', 4.0)")
    ins("INSERT INTO folder_location_events (folder_id, location_id, event, prior, new, at) VALUES "
        "('f1', 'l1', 'odd', '{\"z\": 1, \"a\": 2}', 'not json', 5.0)")         # kept whole
    ins("INSERT INTO folder_registrations VALUES ('k1', 'fp1', 'f1', 1.5)")
    ins("INSERT INTO folder_registrations VALUES ('k2', 'fp2', 'gone', 1.5)")       # kept whole
    ins("INSERT INTO folder_candidates VALUES ('c1', 'n1', ?, 'data', 'registered', 'claude:cwd', "
        "NULL, 1.0, 9.0, 'f1')", (x,))
    ins("INSERT INTO folder_candidates VALUES ('c2', 'n1', '/h', 'history', 'ignored', 'claude:history', "
        "'claude:history', 1.0, 8.0, NULL)")
    ins("INSERT INTO folder_observations (node_id, provider, profile_root, native_session_id, observed_cwd, "
        "cwd_key, history_dir, history_file, evidence, associated, cwd_candidate_id, history_candidate_id, "
        "first_observed_at, observed_at, last_live_at) VALUES ('n1', 'claude', '/r', 's1', ?, ?, '/h', "
        "'/h/s1.jsonl', 'transcript', '{\"a\": 1, \"b\": [2]}', 'c1', 'c2', 1.0, 9.0, 9.0)", (x, x))
    ins("INSERT INTO folder_observations (node_id, provider, profile_root, native_session_id, observed_cwd, "
        "cwd_key, evidence, cwd_candidate_id, first_observed_at, observed_at) VALUES ('n1', 'codex', "
        "'/c', 's2', ?, ?, 'codex_thread', 'c1', 2.0, 2.0)", (x, x))
    ins("INSERT INTO folder_observations (node_id, provider, profile_root, native_session_id, observed_cwd, "
        "cwd_key, evidence, associated, first_observed_at, observed_at) VALUES ('n1', 'codex', '/c', 's3', "
        "'/q', '/q', 'live', '{\"b\": 1, \"a\": 2}', 2.0, 2.0)")                # unsorted: kept whole
    ins("INSERT INTO folder_discovery VALUES ('live', NULL, 3, 3, 0, 1, 9.0, 9.0, NULL)")
    ins("INSERT INTO folder_discovery VALUES ('codex:/c', '1700', 4, 1, 2, 0, 9.0, NULL, 'OSError')")
    ins("INSERT INTO folder_history_files VALUES ('/h/s1.jsonl', -1, 9.0)")
    ins("INSERT INTO folder_history_files VALUES ('/h/s9.jsonl', 120, 9.5)")
    ins("INSERT INTO folder_history_files VALUES ('/h2/a.jsonl', 7, 1.0)")

    ins("INSERT INTO projects VALUES ('p1', 'local', 'n1', 'Orbs', 'orbs', 'orbs', 'the orbs', 'active', 4, "
        "1.0, 5.0, NULL)")
    ins("INSERT INTO projects VALUES ('p2', 'local', 'n1', 'Old', 'old', 'old', '', 'archived', 2, 1.0, "
        "2.0, 2.0)")
    ins("INSERT INTO project_aliases VALUES ('local', 'run_project', 'orbs', 'p1', 1.0)")
    ins("INSERT INTO project_aliases VALUES ('local', 'memory_slug', 'old', 'p2', 1.0)")
    ins("INSERT INTO project_folders VALUES ('p1', 'f1', 'source', 1, 'docs', 1, 1.0, NULL)")
    ins("INSERT INTO project_folders VALUES ('p1', 'f2', 'docs', 0, 'off', 2, 1.0, 3.0)")
    ins("INSERT INTO project_candidates VALUES ('k1', 'local', 'Orbs', ?, 'abc', 'accepted', 'p1', 1.0, 2.0)",
        (json.dumps({"folders": [{"folder_id": "f1", "part": "source"}], "legacy": []}, sort_keys=True),))
    ins("INSERT INTO project_candidates VALUES ('k2', 'local', 'Kestrel', '{}', 'def', 'ignored', NULL, "
        "1.0, 1.0)")
    ins("INSERT INTO works VALUES ('w1', 'local', 'Spike', 'done well', 'done', 'local', 2, 1.0, 3.0, 3.0)")
    ins("INSERT INTO works VALUES ('w2', 'local', 'Open', '', 'open', 'received', 1, 1.0, 1.0, NULL)")
    ins("INSERT INTO work_projects VALUES ('w1', 'p1', 'primary', 1.0)")
    ins("INSERT INTO work_projects VALUES ('w1', 'p2', 'involved', 1.5)")
    ins("INSERT INTO work_folders VALUES ('w1', 'f1', 1.0)")
    ins("INSERT INTO accomplishments VALUES ('a1', 'p1', 'w1', 'Shipped', 'detail', 'confirmed', 'page', "
        "2.0, 2.5, 2, 2.0)")
    ins("INSERT INTO accomplishments VALUES ('a2', 'p1', NULL, 'Maybe', '', 'proposed', 'run_finish', 3.0, "
        "NULL, 1, 3.0)")
    ins("INSERT INTO accomplishment_evidence VALUES ('a1', 'https://example/pr/1')")
    ins("INSERT INTO accomplishment_evidence VALUES ('a1', 'jref:1:n1:run:r1')")
    ins("INSERT INTO project_bindings VALUES ('jref:1:n1:run:r1', 'p1', NULL, 'f1', 'started_here', 1.0, NULL)")
    ins("INSERT INTO project_bindings VALUES ('jref:1:n1:run:r2', NULL, 'w2', NULL, 'owner', 1.0, 2.0)")
    ins("INSERT INTO project_bindings VALUES ('jref:1:n1:run:r3', 'p1', 'w1', NULL, 'adopted', 1.0, NULL)")
    events = [(ref("project", "p1"), "created", 1, "page", {"name": "Orbs"}),
              (ref("candidate", "k1"), "proposed", 1, "migration", {"name": "Orbs"}),
              (ref("candidate", "k1"), "accepted", 1, "voice", {"project_id": "p1"}),
              (ref("work", "w1"), "started", 1, "page", {"projects": ["p1"], "title": "Spike"}),
              (ref("accomplishment", "a1"), "recorded", 1, "agent", {"project_id": "p1", "title": "S"}),
              (ref("project", "p1"), "folder_linked", 2, "package", {"folder_id": "f1", "home": True}),
              (ref("project", "nobody"), "created", 1, "page", {})]                # kept whole
    for i, (subj, action, rev, actor, detail) in enumerate(events):
        ins("INSERT INTO project_events (at, subject, action, revision, actor, detail) VALUES (?,?,?,?,?,?)",
            (10.0 + i, subj, action, rev, actor, json.dumps(detail, sort_keys=True)))
    conn.commit()
    conn.close()
    return path


def _rows(path: Path) -> dict:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        out = {}
        for t in MINE:
            info = move.table_info(conn, t)
            out[t] = sorted(r.canonical() for r in move.read_rows(conn, info))
        return out
    finally:
        conn.close()


def _round_trip(src: Path, out: Path) -> tuple[float, float]:
    uds = records.uds()
    t0 = time.monotonic()
    done = move.copy(src, uds)
    copied = time.monotonic() - t0
    assert done.state == "copied"
    t0 = time.monotonic()
    report = move.verify(src, uds)
    verified = time.monotonic() - t0
    mine = [t for t in report.tables if t.name in MINE]
    assert report.ok, report.words()
    assert {t.name for t in mine} == {t for t in MINE if t in _rows(src)}
    move.export_sqlite(uds, None, out)
    assert _rows(out) == _rows(src)
    return copied, verified


def test_every_column_round_trips_and_the_stores_read_the_moved_documents(tmp_path):
    run_store.init_db()
    src = _source(tmp_path / "src.db")
    _round_trip(src, tmp_path / "back.db")

    store = folder_store.FolderStore()
    f1 = store.get("f1")
    assert f1.location.id == "l1" and f1.scope.permitted_subtree == "sub" and f1.scope.enabled
    assert [f.id for f in store.list(lifecycle=None)] == ["f2", "f1"]          # Gone, X by name
    assert [e.event for e in store.events("f1")] == ["availability", "registered"]
    assert store.events("f1")[0].new == {"availability": "available", "error_code": None}
    c1 = store.candidate("c1")
    assert (c1.providers, c1.sessions) == (["claude", "codex"], 2)
    assert store.history_files_read(["/h/s1.jsonl", "/h/s9.jsonl", "/nope"]) == {"/h/s1.jsonl": -1,
                                                                                  "/h/s9.jsonl": 120}
    assert {p.source: p.cursor for p in store.progress()} == {"codex:/c": "1700", "live": None}
    assert [s.native_session_id for s in store.related_sessions("n1", "/Users/tim/code/x")] == ["s1", "s2"]

    projects = project_store.ProjectStore()
    assert [p.id for p in projects.projects(["local"], include_archived=True)] == ["p2", "p1"]
    assert projects.project_for_legacy(["local"], "run_project", "Orbs").id == "p1"
    assert [f.folder_id for f in projects.folders_of("p1")] == ["f1"]
    assert projects.index_scopes() == {"f1": ["docs"]}
    assert [a.evidence for a in projects.accomplishments("p1")] == [["https://example/pr/1",
                                                                     "jref:1:n1:run:r1"]]
    w1 = projects.work("w1")
    assert w1.projects == [{"project_id": "p1", "role": "primary"}, {"project_id": "p2", "role": "involved"}]
    assert w1.folders == ["f1"]
    assert projects.binding("jref:1:n1:run:r1")["folder_id"] == "f1"
    assert projects.candidate("k1").evidence["folders"][0]["folder_id"] == "f1"


def test_changes_after_the_move_come_back_as_new_rows(tmp_path):
    src = _source(tmp_path / "src.db")
    _round_trip(src, tmp_path / "back.db")
    target = tmp_path / "new"
    target.mkdir()
    svc = FolderService(folder_store.FolderStore(), lambda: NODE)
    made = svc.register(request_key="k-new", path=str(target))["folder"]
    projects = project_store.ProjectStore()
    p = projects.create("local", "Fresh")
    projects.link_folder(p.id, made["id"], home=True)
    projects.bind("jref:1:n1:run:r9", project_id=p.id)
    folder_store.FolderStore().observe(NODE, [folder_store.Observation(
        "claude", "/r", "s-new", str(target), str(target))])
    out = tmp_path / "after.db"
    move.export_sqlite(records.uds(), None, out)
    conn = sqlite3.connect(out)
    try:
        assert conn.execute("SELECT id FROM folder_location_events WHERE folder_id=?",
                            (made["id"],)).fetchall() == [(5,)]           # after the four moved
        assert conn.execute("SELECT max(id) FROM folder_observations").fetchone() == (4,)
        assert conn.execute("SELECT max(seq) FROM project_events").fetchone() == (10,)
        assert conn.execute("SELECT is_home, rowid FROM project_folders WHERE folder_id=?",
                            (made["id"],)).fetchall() == [(1, 3)]
        assert conn.execute("SELECT count(*) FROM project_bindings").fetchone() == (4,)
        assert conn.execute("SELECT lifecycle FROM folders WHERE id=?", (made["id"],)).fetchone() == (
            Lifecycle.ACTIVE.value,)
    finally:
        conn.close()


@pytest.mark.skipif(not os.environ.get("JARVIS_MOVE_COPY_DB"), reason="no copy of a live database named")
def test_a_live_databases_tables_round_trip(tmp_path, capsys):
    """Only this group's tables of the copy (the rest dropped from a
    private copy of it), into the engine, verified, and back."""
    src = tmp_path / "copy.db"
    shutil.copyfile(os.environ["JARVIS_MOVE_COPY_DB"], src)
    for side in ("-wal", "-shm"):
        if Path(os.environ["JARVIS_MOVE_COPY_DB"] + side).exists():
            shutil.copyfile(os.environ["JARVIS_MOVE_COPY_DB"] + side, str(src) + side)
    conn = sqlite3.connect(src)
    for (name,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall():
        if name not in MINE and not name.startswith("sqlite_"):
            conn.execute(f'DROP TABLE "{name}"')
    conn.execute("DELETE FROM sqlite_sequence WHERE name NOT IN (%s)" % ",".join("?" * len(MINE)), MINE)
    conn.commit()
    conn.execute("VACUUM")
    conn.close()
    copied, verified = _round_trip(src, tmp_path / "back.db")
    counts = {t: len(rows) for t, rows in _rows(src).items()}
    with capsys.disabled():
        print(f"\nlive copy: {sum(counts.values())} rows in {len(counts)} tables; "
              f"copy {copied:.2f}s, verify {verified:.2f}s")
    folders = folder_store.FolderStore().list(limit=1000)
    conn = sqlite3.connect(src)
    try:
        assert len(folders) == conn.execute("SELECT count(*) FROM folders WHERE lifecycle='active'").fetchone()[0]
        for f in folders:
            loc = conn.execute("SELECT canonical_path, availability FROM folder_locations WHERE folder_id=? "
                               "AND retired_at IS NULL", (f.id,)).fetchone()
            assert (f.location.canonical_path, f.location.availability) == loc
            n = conn.execute("SELECT count(*) FROM folder_location_events WHERE folder_id=?",
                             (f.id,)).fetchone()[0]
            assert len(folder_store.FolderStore().events(f.id, limit=1000)) == n
        cands = folder_store.FolderStore().candidates(state=None, limit=100000)
        assert len(cands) == conn.execute("SELECT count(*) FROM folder_candidates").fetchone()[0]
        for c in cands[:20]:
            n = conn.execute("SELECT count(*) FROM folder_observations WHERE cwd_candidate_id=? "
                             "OR history_candidate_id=?", (c.id, c.id)).fetchone()[0]
            assert c.sessions == n
        projects = project_store.ProjectStore()
        owners = [r[0] for r in conn.execute("SELECT owner_id FROM projects UNION SELECT owner_id FROM works")]
        assert {p.id for p in projects.projects(owners, include_archived=True)} == {
            r[0] for r in conn.execute("SELECT id FROM projects")}
        for (pid,) in conn.execute("SELECT id FROM projects"):
            n = conn.execute("SELECT count(*) FROM project_events WHERE subject LIKE ?",
                             (f"%:project:{pid}",)).fetchone()[0]
            moved = [e for v in records.history(project_store.project_path(pid), fields=["events"])
                     for e in (v.data or {}).get("events") or [] if e["subject"].endswith(f":project:{pid}")]
            assert len(moved) == n
            assert [f.folder_id for f in projects.folders_of(pid)] == [r[0] for r in conn.execute(
                "SELECT folder_id FROM project_folders WHERE project_id=? AND removed_at IS NULL "
                "ORDER BY is_home DESC, added_at", (pid,))]
        assert {w.id for w in projects.works(owners, state=None)} == {
            r[0] for r in conn.execute("SELECT id FROM works")}
        assert len(projects.candidates(owners, state=None)) == conn.execute(
            "SELECT count(*) FROM project_candidates").fetchone()[0]
    finally:
        conn.close()
