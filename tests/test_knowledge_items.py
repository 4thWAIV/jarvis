"""Items at paths (jarvis/knowledge/items.py) through the UDS interface:
transactions and their conditions under concurrent writers, versions kept
through flush, compaction and restart, merges and content kept or removed,
ordered lists with cursors, counts and grouped counts, the change feed,
links of each origin, rebuild that never drops a document, and the daily
snapshot."""
from __future__ import annotations

import threading
import time

import pytest

from jarvis.knowledge import paths
from jarvis.knowledge.engine import Engine
from jarvis.knowledge.uds import Caller, Conflict, Stream, Transaction, in_process, Link

TIM = Caller("tim")
AI = Caller("tim", agent="planning", model="sonnet")


@pytest.fixture
def opened(tmp_path):
    with in_process(tmp_path / "engine") as both:
        yield both


@pytest.fixture
def uds(opened):
    return opened[1]


def test_path_rules():
    assert paths.type_of("/a/b.WORK") == "work"
    assert paths.type_of("/a/.env") == paths.FOLDER
    assert paths.type_of("/a/b") == paths.FOLDER
    assert paths.parent("/a") == "/" and paths.parent("/a/b/c.x") == "/a/b"
    assert paths.ancestors("/a/b/c.x") == ["/a", "/a/b"]
    assert paths.problem("/á") is not None          # not NFC
    assert paths.problem("/a\x01") is not None
    assert paths.problem("/" + "x" * 256) is not None
    assert paths.is_path("/work/w-1.work")


def test_each_write_is_a_tagged_version(uds):
    uds.put(TIM, "/work/w.work", data={"state": "ready"}, change="created", summary="new item")
    uds.transaction(AI, Transaction().merge("/work/w.work", {"state": "working"}),
                    change="state", summary="picked up")
    item = uds.get(TIM, "/work/w.work")
    assert item.version == 2 and item.data == {"state": "working"}
    assert item.entry["by"] == "tim" and item.entry["agent"] == "planning"
    assert item.entry["model"] == "sonnet" and item.entry["change"] == "state"
    newest, first = uds.history(TIM, "/work/w.work")
    assert (newest.version, newest.agent, newest.summary) == (2, "planning", "picked up")
    assert (first.version, first.agent, first.change) == (1, None, "created")
    assert newest.at >= first.at
    assert uds.get(TIM, "/work/w.work", version=1).data == {"state": "ready"}
    assert uds.diff(TIM, "/work/w.work", 1)["changes"] == [
        {"field": "state", "before": "ready", "after": "working"}]


def test_merge_sets_and_unsets_and_keeps_content(uds):
    uds.put(TIM, "/notes/a.md", data={"a": 1, "b": {"c": 2}}, content={"text": "hello"})
    uds.transaction(TIM, Transaction().merge("/notes/a.md", {"d": [1, 2]}, unset=("a",)))
    item = uds.get(TIM, "/notes/a.md", {Stream.DATA, Stream.CONTENT, Stream.ENTRY})
    assert item.data == {"b": {"c": 2}, "d": [1, 2]}
    assert item.content["text"] == "hello"
    uds.put(TIM, "/notes/a.md", data={"x": 1}, content=None)
    gone = uds.get(TIM, "/notes/a.md", {Stream.CONTENT, Stream.ENTRY})
    assert gone.content is None and gone.entry["content"] is None
    assert uds.get(TIM, "/notes/a.md", {Stream.CONTENT}, version=1).content["text"] == "hello"
    with pytest.raises(Conflict):
        uds.transaction(TIM, Transaction().merge("/notes/none.md", {"a": 1}))


def test_any_shape_and_every_field_indexed(uds):
    uds.put(TIM, "/work/a.work", data={"pieces": [{"key": "p1", "state": "blocked"}], "meta": {"n": 3}})
    uds.put(TIM, "/work/b.work", data={"pieces": [{"key": "p2", "state": "done"}], "meta": {"n": 5}})
    hit = uds.list(TIM, "/work", where=[["pieces[].state", "=", "blocked"]]).items
    assert [i.path for i in hit] == ["/work/a.work"]
    assert [i.path for i in uds.list(TIM, "/", where=[["meta.n", ">", 4]]).items] == ["/work/b.work"]
    assert [i.path for i in uds.list(TIM, "/", where=[["_version", "=", 1]]).items] == [
        "/work/a.work", "/work/b.work"]
    with pytest.raises(ValueError, match="engine's"):
        uds.put(TIM, "/work/c.work", data={"_key": "x"})


def test_conditions_hold_under_concurrent_writers(uds):
    """Forty writers race to move one item on from the version each read;
    exactly one wins each version, and no update is lost."""
    uds.put(TIM, "/c/counter.count", data={"n": 0})
    wins, conflicts = [], []

    def bump() -> None:
        for _ in range(10):
            while True:
                item = uds.get(TIM, "/c/counter.count")
                try:
                    uds.transaction(TIM, Transaction().put("/c/counter.count", {"n": item.data["n"] + 1},
                                                           expect_version=item.version))
                    wins.append(item.version)
                    break
                except Conflict:
                    conflicts.append(item.version)
    threads = [threading.Thread(target=bump) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    final = uds.get(TIM, "/c/counter.count")
    assert final.data["n"] == 40 and final.version == 41
    assert sorted(wins) == list(range(1, 41))


def test_absent_and_expect_zero_take_a_path_once(uds):
    results = []

    def take(who: str) -> None:
        try:
            uds.transaction(TIM, Transaction().put("/locks/a.lock", {"who": who}, absent=True))
            results.append(who)
        except Conflict:
            pass
    threads = [threading.Thread(target=take, args=(str(i),)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(results) == 1
    with pytest.raises(Conflict) as found:
        uds.transaction(TIM, Transaction().put("/locks/a.lock", {"who": "late"}, expect_version=0))
    assert found.value.found == 1
    uds.delete(TIM, "/locks/a.lock")
    uds.transaction(TIM, Transaction().put("/locks/a.lock", {"who": "again"}, expect_version=0))
    assert uds.get(TIM, "/locks/a.lock").version == 3          # numbering goes on past a delete


def test_history_survives_flush_compaction_and_restart(tmp_path):
    directory = tmp_path / "engine"
    with in_process(directory) as (engine, uds):
        for n in range(30):
            uds.put(TIM, "/work/busy.work", data={"n": n})
            if n % 7 == 0:
                engine.store.flush()
        uds.delete(TIM, "/work/busy.work")
        engine.store.flush()
        engine.store.compact(engine.store.manifest.generations())
        assert len(uds.history(TIM, "/work/busy.work")) == 31
    with in_process(directory) as (engine, uds):
        versions = uds.history(TIM, "/work/busy.work")
        assert [v.version for v in versions] == list(range(31, 0, -1))
        assert uds.get(TIM, "/work/busy.work", version=12).data == {"n": 11}
        assert uds.get(TIM, "/work/busy.work") is None
        uds.put(TIM, "/work/busy.work", data={"n": "back"})
        assert uds.get(TIM, "/work/busy.work").version == 32


def test_log_recovery_without_a_flush(tmp_path):
    """Writes only in the log (no generation written) are all there after a
    restart, with their versions and the change feed."""
    directory = tmp_path / "engine"
    with in_process(directory) as (engine, uds):
        for n in range(50):
            uds.put(TIM, f"/r/{n:03d}.rec", data={"n": n})
        uds.put(TIM, "/r/000.rec", data={"n": "again"})
        engine.store.flush = lambda: None      # as after a crash: nothing flushed at close
    assert not list((directory / "seg").glob("*.docs"))
    engine = Engine.open(directory)
    engine.close()
    with in_process(directory) as (_engine, uds):
        assert uds.count(TIM, "/r").items == 50
        assert uds.get(TIM, "/r/000.rec").version == 2
        assert len(uds.changes(TIM, 0, limit=1000)["changes"]) == 51


def test_list_pages_in_path_order_and_by_field(uds):
    for n in range(25):
        uds.put(TIM, f"/w/{n:02d}.work", data={"rank": (n * 7) % 25, "team": "a" if n % 2 else "b"})
    uds.put(TIM, "/other/x.work", data={"rank": 1})
    seen, cursor = [], None
    while True:
        page = uds.list(TIM, "/w", limit=10, cursor=cursor)
        seen += [i.path for i in page.items]
        if page.cursor is None:
            break
        cursor = page.cursor
    assert seen == [f"/w/{n:02d}.work" for n in range(25)]
    ranked = list(uds.scan(TIM, "/w", order=("rank", "desc"), limit=4))
    assert [i.data["rank"] for i in ranked] == list(range(24, -1, -1))
    team = list(uds.scan(TIM, "/w", where=[["team", "=", "a"]], order=("rank", "asc"), limit=3))
    assert len(team) == 12 and [i.data["rank"] for i in team] == sorted(i.data["rank"] for i in team)
    assert [i.path for i in uds.list(TIM, "/", parent="/other").items] == ["/other/x.work"]
    assert uds.list(TIM, "/", types=["WORK"]).items[0].kind == "work"


def test_counts_and_grouped_counts(uds):
    uds.put(TIM, "/w/a.work", data={"state": "done", "tags": ["x", "y", "x"]}, content={"text": "a"})
    uds.put(TIM, "/w/b.work", data={"state": "done", "tags": ["y"]})
    uds.put(TIM, "/w/c.work", data={"state": "ready"})
    uds.put(TIM, "/p/a.project", data={"state": "done"})
    total = uds.count(TIM, "/")
    assert (total.items, total.embedded, total.waiting) == (4, 0, 1)
    assert uds.count(TIM, "/w", where=[["state", "=", "done"]]).items == 2
    assert dict(uds.count(TIM, "/w", group_by="state").groups) == {"done": 2, "ready": 1}
    assert dict(uds.count(TIM, "/", group_by="tags[]").groups) == {"y": 2, "x": 1}
    assert dict(uds.count(TIM, "/", group_by="_ancestors[]").groups) == {"/w": 3, "/p": 1}


def test_change_feed_follows_every_write(uds):
    start = uds.changes(TIM, 0)["lsn"]
    uds.put(TIM, "/w/a.work", data={"n": 1})
    uds.transaction(AI, Transaction().put("/w/b.work", {"n": 2}).put("/p/x.project", {"n": 3}),
                    change="made", summary="two at once")
    uds.delete(TIM, "/w/a.work")
    found = uds.changes(TIM, start)
    assert [[(i["path"], i["op"], i["version"]) for i in c["items"]] for c in found["changes"]] == [
        [("/w/a.work", "put", 1)],
        [("/w/b.work", "put", 1), ("/p/x.project", "put", 1)],
        [("/w/a.work", "delete", 2)]]
    assert found["changes"][1]["agent"] == "planning" and found["changes"][1]["summary"] == "two at once"
    under = uds.changes(TIM, start, under="/p")
    assert [[i["path"] for i in c["items"]] for c in under["changes"]] == [["/p/x.project"]]
    page = uds.changes(TIM, start, limit=1)
    assert len(page["changes"]) == 1
    assert len(uds.changes(TIM, page["next"])["changes"]) == 2


def test_links_of_each_origin(uds):
    uds.put(TIM, "/projects/j.project", data={"name": "J"})
    uds.put(TIM, "/projects/j.project/notes.md", data={}, content={"text": "n"})
    uds.put(TIM, "/work/w.work", data={"project": "/projects/j.project",
                                         "pieces": [{"owner": "/agents/a.agent"}]})
    refs = uds.links(TIM, "/work/w.work", origins=["reference"])
    assert [lk.target for lk in refs] == ["/projects/j.project"]   # the agent is not an item yet
    uds.put(TIM, "/agents/a.agent", data={})
    found = {(lk.source, lk.kind, lk.target, lk.origin) for lk in uds.links(TIM, "/work/w.work")}
    assert found == {("/work/w.work", "project", "/projects/j.project", "reference"),
                     ("/work/w.work", "pieces[].owner", "/agents/a.agent", "reference")}
    project = {(lk.source, lk.kind, lk.target, lk.origin) for lk in uds.links(TIM, "/projects/j.project")}
    assert ("/projects/j.project/notes.md", "parent", "/projects/j.project", "hierarchy") in project
    assert ("/work/w.work", "project", "/projects/j.project", "reference") in project
    with pytest.raises(ValueError, match="model"):
        uds.transaction(TIM, Transaction().link("/work/w.work", "mentions", "/agents/a.agent", model=""))
    with pytest.raises(ValueError, match="proposal"):     # an AI's link waits for Tim's yes
        uds.transaction(AI, Transaction().link("/projects/j.project/notes.md", "mentions", "/agents/a.agent",
                                               model="e5", weight=0.4))
    proposed = uds.propose_link(AI, Link("/projects/j.project/notes.md", "mentions", "/agents/a.agent",
                                         "derived", model="e5", weight=0.4))
    assert uds.links(TIM, "/agents/a.agent", origins=["derived"]) == []
    uds.answer_proposal(TIM, proposed.path, "accept")
    derived = uds.links(TIM, "/agents/a.agent", origins=["derived"])
    assert len(derived) == 1 and derived[0].model == "e5" and derived[0].at is not None
    uds.put(TIM, "/work/w.work", data={"project": "/projects/j.project"})
    assert {lk.kind for lk in uds.links(TIM, "/work/w.work", direction="out")} == {"project"}
    uds.transaction(TIM, Transaction().unlink("/projects/j.project/notes.md", "mentions", "/agents/a.agent"))
    assert uds.links(TIM, "/agents/a.agent", origins=["derived"]) == []


def test_full_sync_and_feed_lsn(uds, opened):
    engine = opened[0]
    done = uds.transaction(TIM, Transaction().put("/answers/a.answer", {"yes": True}), full_sync=True)
    assert engine.store.wal.durable_lsn >= done["lsn"]


def test_legacy_documents_and_items_keep_apart(opened):
    engine, uds = opened
    engine.transaction({"puts": [{"_key": "topic:x", "_kind": "topic", "_label": "X"}]})
    uds.put(TIM, "/topics/x.topic", data={"label": "X"})
    assert [d["_key"] for d in engine.docs.find(kinds_=["topic"])] == ["topic:x"]
    assert uds.count(TIM).items == 1
    from jarvis.knowledge.documents import DocumentError
    with pytest.raises(DocumentError):
        engine.transaction({"puts": [{"_key": "/not/legacy", "_kind": "topic"}]})


def test_rebuild_keeps_every_document(tmp_path):
    from jarvis.knowledge.__main__ import rebuild
    directory = tmp_path / "engine"
    with in_process(directory) as (engine, uds):
        uds.put(TIM, "/w/a.work", data={"state": "x"})
        uds.put(TIM, "/w/a.work", data={"state": "y"})
        engine.transaction({"puts": [{"_key": "topic:x", "_kind": "topic", "_label": "X"}]})
    report = rebuild(directory)
    assert report["snapshot"]
    with in_process(directory) as (engine, uds):
        assert uds.get(TIM, "/w/a.work").data == {"state": "y"}
        assert len(uds.history(TIM, "/w/a.work")) == 2
        assert [i.path for i in uds.list(TIM, "/", where=[["state", "=", "y"]]).items] == ["/w/a.work"]
        assert engine.docs.get("topic:x") is not None
        assert engine.store.snapshots()


def test_daily_snapshot(opened, monkeypatch):
    engine, uds = opened
    uds.put(TIM, "/w/a.work", data={})
    engine.maintain()
    assert len(engine.store.snapshots()) == 1
    engine.maintain()
    assert len(engine.store.snapshots()) == 1           # not again within the day
    import jarvis.knowledge.engine as engine_mod
    monkeypatch.setattr(engine_mod, "SNAPSHOT_EVERY_SEC", 0.0)
    uds.put(TIM, "/w/b.work", data={})
    engine.maintain()
    assert len(engine.store.snapshots()) == 2


def test_a_full_field_dictionary_never_refuses_a_write(opened, monkeypatch):
    engine, uds = opened
    import jarvis.knowledge.documents as documents
    monkeypatch.setattr(documents, "FIELDS_MAX", len(engine.docs.fields) + 3)
    uds.put(TIM, "/f/a.x", data={f"field{n}": n for n in range(10)})
    assert engine.docs.fields_full
    assert [i.path for i in uds.list(TIM, "/", where=[["field9", "=", 9]]).items] == ["/f/a.x"]
