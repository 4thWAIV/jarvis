"""The query shape log, the advisor and the extra indexes (phase 4)."""
from __future__ import annotations

import itertools
import json
import random

import pytest

from jarvis.knowledge import advisor as adv
from jarvis.knowledge.encoding import canonical
from jarvis.knowledge.engine import Engine
from jarvis.knowledge.store import META
from jarvis.knowledge.wal import Op


@pytest.fixture(autouse=True)
def _in_process(monkeypatch):
    monkeypatch.setattr(Engine, "compact_in_child", False)


@pytest.fixture
def engine(tmp_path):
    e = Engine.open(tmp_path / "k")
    yield e
    e.close()


def corpus(e, n=400, seed=1, start=0):
    rng = random.Random(seed)
    e.transaction({"puts": [{"_key": f"doc:{i}", "_kind": "document", "language": rng.choice(["en", "es"]),
                             "media_type": rng.choice(["text/markdown", "text/plain", "application/pdf"]),
                             "bytes": rng.randint(0, 9), "path": f"p/{i}.md"}
                            for i in range(start, start + n)]})


QUERIES = [
    [["language", "=", "en"], ["media_type", "=", "text/plain"]],
    [["language", "=", "es"], ["media_type", "=", "application/pdf"], ["bytes", "=", 3]],
    [["media_type", "=", "text/markdown"], ["bytes", "=", 7]],
    [["language", "=", "en"], ["bytes", ">", 4]],
    [["language", "=", "fr"], ["media_type", "=", "text/plain"]],
]
DEFS = [{"kinds": ["document"], "paths": ["language", "media_type"]},
        {"kinds": ["document"], "paths": ["bytes", "media_type"]},
        {"kinds": ["document"], "paths": ["bytes", "language", "media_type"]}]


def install(e, defs):
    full = [{"id": i + 1, **d, "created": e.advisor.clock(), "last_used": None, "uses": 0} for i, d in enumerate(defs)]
    e.store.commit([Op(META, adv.DEFS_KEY, canonical(full))])
    e.advisor.defs = full
    e.rebuild_indexes()
    return full


def answers(e):
    return [sorted(d["_key"] for d in e.docs.find(kinds_=["document"], where=q, limit=1 << 20)) for q in QUERIES]


def test_extra_indexes_change_speed_never_answers(engine):
    corpus(engine)
    engine.store.flush()
    corpus(engine, 100, seed=2, start=400)             # some unflushed documents too
    engine.docs.extra_lookup = None
    plain = answers(engine)
    engine.docs.extra_lookup = engine.advisor.lookup
    full = install(engine, DEFS)
    assert answers(engine) == plain                    # with every one
    assert sum(d["uses"] for d in full) > 0
    for n in range(len(full) + 1):
        for subset in itertools.combinations(full, n):
            engine.advisor.defs = list(subset)
            assert answers(engine) == plain, [d["id"] for d in subset]


def test_an_index_is_used_only_once_every_generation_holds_it(engine):
    corpus(engine)
    engine.store.flush()
    full = [{"id": 1, **DEFS[0], "created": 0, "last_used": None, "uses": 0}]
    engine.store.commit([Op(META, adv.DEFS_KEY, canonical(full))])
    engine.advisor.defs = full
    preds = [adv.Predicate("language", "=", "en"), adv.Predicate("media_type", "=", "text/plain")]
    assert engine.advisor.lookup(preds, ["document"]) is None      # the old generation lacks it
    engine.rebuild_indexes()
    assert engine.advisor.lookup(preds, ["document"]) is not None


def test_shapes_hold_no_values_and_survive_a_restart(tmp_path):
    e = Engine.open(tmp_path / "k")
    corpus(e, 50)
    e.docs.find(kinds_=["document"], where=[["path", "=", "SECRET-VALUE"], ["language", "=", "en"]])
    assert "SECRET" not in json.dumps(e.advisor.shapes)
    shapes = json.loads(json.dumps(e.advisor.shapes))
    e.close()
    e = Engine.open(tmp_path / "k")
    try:
        assert e.advisor.shapes == shapes
    finally:
        e.close()


def test_the_advisor_adds_an_index_for_a_hot_slow_wasteful_shape_and_builds_it(engine):
    corpus(engine)
    engine.store.flush()
    preds = [adv.Predicate("language", "=", "en"), adv.Predicate("media_type", "=", "text/plain"),
             adv.Predicate("_kind", "in", ["document"])]
    for _ in range(adv.MIN_RUNS):
        engine.advisor.record(["document"], preds, None, 30.0, 400, 2)
    for _ in range(adv.MIN_RUNS):                       # fast: never proposed
        engine.advisor.record(["topic"], [adv.Predicate("a", "="), adv.Predicate("b", "=")], None, 1.0, 400, 1)
    cands = engine.advisor.candidates()
    assert [c["paths"] for c in cands] == [["language", "media_type"]]
    advice = engine.advisor.advise(rebuild=engine.rebuild_indexes)
    assert advice["added"] == [1]
    assert engine.advisor.lookup(preds, ["document"]) is not None
    assert engine.advisor.advise(rebuild=engine.rebuild_indexes)["added"] == []      # not twice


def test_guardrails_and_dropping_the_unused(engine, monkeypatch):
    monkeypatch.setattr(adv, "PER_KIND_MAX", 1)
    corpus(engine, 50)
    for paths in (["language", "media_type"], ["bytes", "language"]):
        preds = [adv.Predicate(p, "=", 1) for p in paths]
        for _ in range(adv.MIN_RUNS):
            engine.advisor.record(["document"], preds, None, 50.0, 1000, 1)
    added = engine.advisor.advise(rebuild=engine.rebuild_indexes)["added"]
    assert len(added) == 1 and len(engine.advisor.defs) == 1
    clock = [engine.advisor.clock() + (adv.UNUSED_DAYS + 1) * 86400]
    monkeypatch.setattr(engine.advisor, "clock", lambda: clock[0])
    engine.advisor.shapes = {}
    advice = engine.advisor.advise(rebuild=engine.rebuild_indexes)
    assert advice["dropped"] == added and engine.advisor.defs == []


@pytest.mark.knowledge_engine
def test_the_compaction_child_builds_the_extra_indexes(tmp_path, monkeypatch):
    monkeypatch.setattr(Engine, "compact_in_child", True)
    e = Engine.open(tmp_path / "k")
    try:
        corpus(e)
        e.store.flush()
        e.docs.extra_lookup = None
        plain = answers(e)
        e.docs.extra_lookup = e.advisor.lookup
        install(e, DEFS)
        preds = [adv.Predicate("language", "=", "en"), adv.Predicate("media_type", "=", "text/plain")]
        assert e.advisor.lookup(preds, ["document"]) is not None
        assert answers(e) == plain
    finally:
        e.close()
