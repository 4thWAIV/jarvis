"""Every Work item in its own folder (work_folders.py): moved from
/work/<id>.work to /work/<id>/<id>.work with its whole history
(records.move), taken up where a stopped move left off, checked, and run
back; project works at /work/works are never touched."""
from __future__ import annotations

import pytest

from jarvis import records, work_folders, work_store
from jarvis.knowledge.uds import Caller, Transaction

AGENT = Caller(person="tim", agent="agent:s-1", model="m-1")


def _write(path, data, *, by=records.JARVIS, change=None, summary=None, delete=False):
    tx = Transaction().delete(path) if delete else Transaction().put(path, data)
    records.uds().transaction(by, tx, change=change, summary=summary)


def _shape(path):
    return [(v.op, v.by, v.agent, v.model, v.change, v.summary) for v in records.history(path)]


def _datas(path):
    return [None if v.op == "delete" else records.get(path, version=v.version).data
            for v in records.history(path)]


def test_a_record_moves_with_every_version_in_order():
    _write("/t/a.work", {"n": 1}, change="created", summary="new")
    _write("/t/a.work", {"n": 2}, by=AGENT, change="piece", summary="a piece")
    _write("/t/a.work", None, delete=True, change="gone")
    _write("/t/a.work", {"n": 3}, change="again")
    before_shape, before_data = _shape("/t/a.work"), _datas("/t/a.work")
    assert records.move("/t/a.work", "/t/a/a.work") == 4
    assert _shape("/t/a/a.work") == before_shape
    assert _datas("/t/a/a.work") == before_data
    assert records.get("/t/a.work") is None
    assert _shape("/t/a.work")[:-1] == before_shape          # kept, then deleted
    assert _shape("/t/a.work")[-1][0] == "delete"
    assert records.move("/t/a.work", "/t/a/a.work") == 0      # already moved


def test_a_history_longer_than_one_read_moves_whole(monkeypatch):
    monkeypatch.setattr(records, "HISTORY_PAGE", 3)            # many pages of history
    for n in range(1, 12):
        _write("/t/h.work", {"n": n}, change=f"c{n}")
    _write("/t/h.work", None, delete=True, change="gone")
    _write("/t/h.work", {"n": 12}, change="again")
    assert len(records.versions("/t/h.work")) == 13
    assert [v.version for v in records.versions("/t/h.work")] == list(range(1, 14))
    before_shape, before_data = _shape("/t/h.work"), _datas("/t/h.work")
    assert records.move("/t/h.work", "/t/h/h.work") == 13
    assert _shape("/t/h/h.work") == before_shape
    assert _datas("/t/h/h.work") == before_data


def test_a_stopped_move_is_taken_up_where_it_stopped():
    for n in range(1, 5):
        _write("/t/b.work", {"n": n}, change=f"c{n}")
    _write("/t/b/b.work", {"n": 1}, change="c1")               # the first version copied
    _write("/t/b/b.work", {"n": 2}, change="c2")
    assert records.move("/t/b.work", "/t/b/b.work") == 4
    assert [d["n"] for d in _datas("/t/b/b.work")] == [1, 2, 3, 4]


def test_another_record_where_it_goes_is_refused():
    _write("/t/c.work", {"n": 1})
    _write("/t/c/c.work", {"other": True})
    with pytest.raises(records.MoveFailed):
        records.move("/t/c.work", "/t/c/c.work")
    assert records.get("/t/c.work").data == {"n": 1}


def test_derived_links_go_with_it():
    _write("/t/d.work", {"n": 1})
    _write("/t/other.note", {"x": 1})
    records.uds().transaction(records.TIM, Transaction().link("/t/other.note", "mentions", "/t/d.work",
                                                                 model="linker"))
    records.move("/t/d.work", "/t/d/d.work")
    found = records.uds().links(records.JARVIS, "/t/d/d.work", origins=["derived"])
    assert [(l.source, l.kind, l.target, l.model) for l in found] == [
        ("/t/other.note", "mentions", "/t/d/d.work", "linker")]


def _old_layout():
    """Two Work items and a project work as they were before folders."""
    a = work_store.create("Speech", "folder", "/")
    work_store.record(work_store.WorkEvent(a.id, "piece", "Tests run.", {"key": "tests", "who": {"kind": "tim"}}))
    b = work_store.create("Voice", "folder", "/")
    records.write("/work/works/w-1.work", {"name": "a project work"})
    work_folders.move(back=True, snapshot=False)
    return a, b


def test_the_work_items_move_into_their_folders_and_back(tmp_path):
    a, b = _old_layout()
    assert records.get(work_store.old_path_of(a.id)) is not None
    assert records.get(work_store.path_of(a.id)) is None
    old_versions = {i: len(records.history(work_store.old_path_of(i))) for i in (a.id, b.id)}
    old_events = [e["kind"] for e in records.get(work_store.old_path_of(a.id)).data["events"]]

    done = work_folders.move(snapshot=False)
    assert (done.items, sorted(done.ids)) == (2, sorted([a.id, b.id]))
    assert done.versions == sum(old_versions.values())
    now = work_folders.where()
    assert now.at_old_paths == () and set(now.in_folders) == {a.id, b.id}
    for item_id, n in old_versions.items():
        # the old path's versions are kept, then its delete
        assert len(records.history(work_store.path_of(item_id))) == n
    assert [p.key for p in work_store.get(a.id).pieces] == ["tests"]
    assert [e["kind"] for e in records.get(work_store.path_of(a.id)).data["events"]] == old_events
    assert {i.id for i in work_store.listing()} >= {a.id, b.id}
    assert records.get("/work/works/w-1.work").data == {"name": "a project work"}
    assert work_folders.move(snapshot=False).items == 0          # nothing left to move

    back = work_folders.move(back=True, snapshot=False)
    assert back.items == 2 and work_folders.where().in_folders == ()
    assert work_folders.move(snapshot=False).items == 2            # and forward again
    assert work_store.get(b.id).title == "Voice"


def test_the_move_takes_a_snapshot_first(monkeypatch):
    from jarvis import records_cutover
    _old_layout()
    taken = []
    monkeypatch.setattr(records_cutover, "take_snapshot", lambda uds: taken.append(uds) or "snap-1")
    assert work_folders.move().snapshot == "snap-1"
    assert len(taken) == 1
    assert work_folders.move().snapshot is None                  # nothing to move: none taken


def test_the_command_moves_its_own_data_folder_never_an_address(tmp_path, monkeypatch, capsys):
    """The command opens the engine in its own data folder in this process:
    a copied folder whose address file names another engine is moved itself,
    and an engine running there refuses it."""
    from jarvis import data_paths, records_cutover
    from jarvis.knowledge.engine import Engine
    folder = tmp_path / "copied-data" / "knowledge"
    records.uds()                                                    # this test's own engine
    with records.opened_at(folder):
        _write("/work/work-cmd.work", {"id": "work-cmd", "title": "t"})
        _write("/work/work-cmd.work", {"id": "work-cmd", "title": "t2"})
    monkeypatch.setattr(work_store, "_is_item", lambda data: bool(data and data.get("id")))
    monkeypatch.setattr(data_paths, "knowledge_dir", lambda: folder)
    monkeypatch.setattr(records_cutover, "take_snapshot", lambda uds: "snap-1")
    assert work_folders.main(["forward"]) == 0
    assert "in their folders: 1; at the old paths: 0" in capsys.readouterr().out
    assert records._bound is None                                    # the binding put back
    with records.opened_at(folder):
        assert len(records.versions(work_store.path_of("work-cmd"))) == 2
        assert records.get(work_store.old_path_of("work-cmd")) is None
    engine = Engine.open(folder)
    try:
        assert work_folders.main(["check"]) == 1
        assert "stop JARVIS first" in capsys.readouterr().err
    finally:
        engine.close()
