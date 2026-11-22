"""A search fuses its candidate lists first and reads only the documents of
the fused top hits, in one batched read (sorted by key, one pass over the
segments), with the candidates judged from the resident key column first
(construction-drafts.md "Knowledge search", the second part: made fast
enough that seconds is never normal)."""
from __future__ import annotations

import pytest

from jarvis import knowledge_client as kc
from jarvis.knowledge import embedder as emb
from jarvis.knowledge import items as items_mod
from jarvis.knowledge import vecmath
from jarvis.knowledge.engine import Engine
from jarvis.knowledge.store import DOCS
from jarvis.knowledge.uds import Caller, EngineUDS, Query
from tests.settings_fixtures import hold_setting

TIM = Caller("tim")
ANA = Caller("ana")


@pytest.fixture
def engine(tmp_path, monkeypatch):
    hold_setting("knowledge_enabled", "1")
    monkeypatch.setattr(Engine, "compact_in_child", False)
    monkeypatch.setattr(vecmath, "MATH", vecmath.Reference())
    monkeypatch.setattr(emb, "RUNNER", lambda: emb.FakeEmbedder(32))
    e = Engine.open(tmp_path / "k")
    yield e
    e.close()


def _uds(engine) -> EngineUDS:
    return EngineUDS(kc.InProcessTransport(engine))


def _notes(engine, n=30) -> EngineUDS:
    uds = _uds(engine)
    for i in range(n):
        uds.put(TIM, f"/notes/n{i:02d}.md", data={},
                content={"text": f"# Lighthouse {i}\nthe lighthouse keeper trims lamp {i}"})
    assert engine.vectors.wait_idle(20)
    return uds


def _counting_reads(engine, monkeypatch) -> dict:
    """Every document read: one by one (`by_ordinal`, `get`) and batched."""
    seen = {"one": 0, "batched": [], "batches": 0}
    docs = engine.items.docs
    real_get, real_many = docs.get, docs.get_many

    def one(*a, **k):
        seen["one"] += 1
        return real_get(*a, **k)

    def many(keys, *a, **k):
        keys = list(keys)
        seen["batches"] += 1
        seen["batched"].extend(keys)
        return real_many(keys, *a, **k)
    monkeypatch.setattr(docs, "get", one)
    monkeypatch.setattr(docs, "get_many", many)
    return seen


def test_only_the_fused_top_is_read_in_one_batched_read(engine, monkeypatch):
    uds = _notes(engine)
    seen = _counting_reads(engine, monkeypatch)
    found = uds.search_page(TIM, Query(meaning="lighthouse keeper", words="lighthouse keeper", limit=5))
    assert len(found["hits"]) == 5
    assert seen["one"] == 0                    # nothing read one by one
    assert seen["batches"] == 1                # one batched read, of the answer alone
    assert sorted(seen["batched"]) == sorted(h.path for h in found["hits"])
    assert all({"vector", "fulltext"} <= set(h.matched_by) for h in found["hits"])


def test_the_answer_is_the_one_a_check_of_every_candidate_gives(engine):
    """Candidates the key column cannot rule out but their documents do (a
    reader with the data right alone, records' type, content inside) leave
    their lists, which are fused again: the answer is every record, ranked
    among records only."""
    uds = _uds(engine)
    for i in range(8):        # items of a record type holding content: not readable to ana
        uds.put(TIM, f"/cv/a{i}.work", data={}, content={"text": f"lighthouse keeper {i}"})
    for i in range(4):        # records found by their own words: readable to ana
        uds.put(TIM, f"/cv/r{i}.work", data={"title": f"lighthouse keeper record {i}"})
    assert engine.vectors.wait_idle(20)
    uds.set_rights(TIM, "/cv", [{"person": "ana", "data": True, "content": False}])
    found = uds.search_page(ANA, Query(words="lighthouse keeper", limit=3))
    assert [h.path for h in found["hits"]] and all("/r" in h.path for h in found["hits"])
    assert len(found["hits"]) == 3
    every = uds.search_page(ANA, Query(words="lighthouse keeper", limit=20))
    assert sorted(h.path for h in every["hits"]) == [f"/cv/r{i}.work" for i in range(4)]
    assert [h.path for h in every["hits"]][:3] == [h.path for h in found["hits"]]


def test_a_phrase_with_nothing_ranking_stops_at_the_first_hits_in_path_order(engine, monkeypatch):
    uds = _uds(engine)
    for i in range(600):
        uds.put(TIM, f"/notes/p{i:03d}.md", data={}, content={"text": f"the keeper of lamp {i}"})
    seen = _counting_reads(engine, monkeypatch)
    found = uds.search_page(TIM, Query(phrase="keeper of lamp", limit=5))
    assert [h.path for h in found["hits"]] == [f"/notes/p{i:03d}.md" for i in range(5)]
    assert seen["one"] == 0
    assert len(seen["batched"]) == items_mod.SEARCH_READ_BATCH     # one batch, not all 600


def test_a_phrase_beside_a_ranking_is_checked_in_full(engine, monkeypatch):
    uds = _uds(engine)
    for i in range(300):
        text = f"the keeper of lamp {i}" if i % 2 else f"lamp {i} has a keeper"
        uds.put(TIM, f"/notes/q{i:03d}.md", data={}, content={"text": text})
    seen = _counting_reads(engine, monkeypatch)
    found = uds.search_page(TIM, Query(phrase="keeper of lamp", words="keeper lamp", limit=200))
    assert sorted(h.path for h in found["hits"]) == [f"/notes/q{i:03d}.md" for i in range(1, 300, 2)]
    assert seen["one"] == 0


def test_each_stage_s_seconds_come_with_the_answer(engine):
    uds = _notes(engine, n=4)
    found = engine.items.search({"caller": {"person": "tim"}, "words": "lighthouse",
                                 "meaning": "lighthouse", "limit": 3})
    assert found["hits"]
    assert {"conditions", "words", "embed", "meaning", "fuse_and_read", "sections"} <= set(found["stages"])
    assert all(isinstance(s, float) and s >= 0 for s in found["stages"].values())
    del uds


def test_newest_many_is_newest_of_each_key_in_one_pass(engine):
    """Across the memtable and flushed segments, tombstones and an earlier
    LSN included, the batched read gives exactly what each read alone gives."""
    uds = _uds(engine)
    for i in range(40):
        uds.put(TIM, f"/m/k{i:02d}.md", data={"v": 1})
    engine.store.flush()
    marks = engine.store.lsn
    for i in range(0, 40, 3):
        uds.put(TIM, f"/m/k{i:02d}.md", data={"v": 2})
    engine.store.flush()
    for i in range(0, 40, 5):
        uds.delete(TIM, f"/m/k{i:02d}.md")
    for i in range(0, 40, 7):
        uds.put(TIM, f"/m/k{i:02d}.md", data={"v": 3})           # left in the memtable
    store = engine.store
    keys = [f"/m/k{i:02d}.md".encode() for i in range(40)] + [b"/m/none.md"]
    for at in (None, marks):
        for live in (False, True):
            many = store.newest_many(DOCS, list(reversed(keys)), at, live=live)
            for k in keys:
                one = store.newest(DOCS, k, at, live=live)
                assert (many.get(k) is None) == (one is None), k
                if one is not None:
                    assert (many[k].lsn, many[k].value) == (one.lsn, one.value)
    got = engine.docs.get_many([k.decode() for k in keys])
    for k in keys:
        assert got.get(k.decode()) == engine.docs.get(k.decode())


def test_a_phrase_beside_meaning_finds_the_nearest_holding_it_however_far(engine):
    """The nearest items holding the phrase, though many nearer ones lack
    it: more are asked for until enough hold it or none are left."""
    uds = _uds(engine)
    for i in range(200):
        uds.put(TIM, f"/notes/x{i:03d}.md", data={}, content={"text": f"keeper lamp {i}"})
    for i in range(3):
        uds.put(TIM, f"/notes/y{i}.md", data={}, content={"text": f"a keeper of lamp number {i}"})
    assert engine.vectors.wait_idle(20)
    found = uds.search_page(TIM, Query(meaning="keeper lamp", phrase="keeper of lamp", limit=10))
    assert sorted(h.path for h in found["hits"]) == [f"/notes/y{i}.md" for i in range(3)]
    assert all({"vector", "fulltext"} <= set(h.matched_by) for h in found["hits"])
