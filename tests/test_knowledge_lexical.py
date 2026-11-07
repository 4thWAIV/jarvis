"""The keyword index (build plan step 5)."""
from __future__ import annotations

import math
import random
from collections import Counter

import pytest

from jarvis.knowledge import lexical as lx
from jarvis.knowledge.engine import Engine


@pytest.fixture(autouse=True)
def _in_process_compaction(monkeypatch):
    monkeypatch.setattr(Engine, "compact_in_child", False)


@pytest.fixture
def engine(tmp_path):
    e = Engine.open(tmp_path / "k")
    yield e
    e.close()


WORDS = ("denice bridge engine prototype proteus parris island roadmap folder agent run workflow "
         "package queue voice orb brain memory journal topic project canción señor café niño "
         "error E1234 build_plan.md tokenizer recall vector graph").split()


def chunk(i, text, **extra):
    return {"_key": f"chunk:{i}", "_kind": "chunk", "text": text, **extra}


def put(engine, chunks):
    engine.transaction({"puts": chunks})


def corpus(rng, n, start=0):
    return [chunk(i, " ".join(rng.choice(WORDS) for _ in range(rng.randint(3, 40))))
            for i in range(start, start + n)]


def brute(engine, text, k=lx.CANDIDATES):
    """BM25 over every live chunk, with exact document frequencies."""
    chunks = {d["_ord"]: Counter(lx.terms(d["text"])) for d in engine.docs.find(kinds_=["chunk"], limit=1 << 30)}
    n = len(chunks)
    avgdl = sum(sum(c.values()) for c in chunks.values()) / n
    scores = {}
    for term in dict.fromkeys(lx.terms(text)):
        df = sum(1 for c in chunks.values() if term in c)
        if not df:
            continue
        idf = math.log(1 + (n - df + 0.5) / (df + 0.5))
        for o, c in chunks.items():
            tf = c.get(term)
            if tf:
                dl = sum(c.values())
                scores[o] = scores.get(o, 0) + idf * tf * (lx.K1 + 1) / (tf + lx.K1 * (1 - lx.B + lx.B * dl / avgdl))
    return sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))[:k]


def same(engine, text, k=lx.CANDIDATES):
    got = [(o, round(s, 6)) for o, s, _ in engine.lexical.search(text, k)]
    want = [(o, round(s, 6)) for o, s in brute(engine, text, k)]
    assert got == want, text


QUERIES = ["denice bridge", "the prototype engine", "canción", "cancion senor", "E1234",
           "build_plan.md", "vector graph recall tokenizer", "nothingmatches", "a of the"]


def test_ranking_equals_brute_force_bm25(engine):
    put(engine, corpus(random.Random(5), 400))
    for q in QUERIES:
        same(engine, q)
    engine.store.flush()
    put(engine, corpus(random.Random(6), 200, start=400))
    for q in QUERIES:
        same(engine, q)                       # a generation and the memtable, no superseded versions


def test_after_updates_and_deletes_old_terms_stop_matching_and_compaction_makes_it_exact(tmp_path):
    e = Engine.open(tmp_path / "k")
    rng = random.Random(8)
    put(e, corpus(rng, 300))
    e.store.flush()
    put(e, [chunk(i, "zebra unique words") for i in range(0, 300, 3)])
    deleted = [i for i in range(1, 300, 10) if i % 3]
    e.transaction({"deletes": [f"chunk:{i}" for i in deleted]})
    hits = {o for o, _, _ in e.lexical.search("zebra", 1000)}
    assert hits == {e.docs.get(f"chunk:{i}")["_ord"] for i in range(0, 300, 3)}
    for o, _, _ in e.lexical.search("denice bridge engine", 1000):
        key = e.docs.key_of(o)
        assert key not in {f"chunk:{i}" for i in range(0, 300, 3)} | {f"chunk:{i}" for i in deleted}
    e.store.flush()
    e.store.compact(e.store.manifest.generations())
    for q in QUERIES + ["zebra"]:
        same(e, q)
    e.close()
    e = Engine.open(tmp_path / "k")
    try:
        for q in QUERIES + ["zebra"]:
            same(e, q)
        assert e.lexical.live == 300 - len(deleted)
    finally:
        e.close()


def test_accents_fold(engine):
    put(engine, [chunk(1, "La canción del señor"), chunk(2, "nothing here")])
    assert [engine.docs.key_of(o) for o, _, _ in engine.lexical.search("cancion senor")] == ["chunk:1"]
    assert [engine.docs.key_of(o) for o, _, _ in engine.lexical.search("CANCIÓN")] == ["chunk:1"]


def test_why_names_each_term(engine):
    put(engine, [chunk(1, "denice denice bridge"), chunk(2, "bridge")])
    (o, score, why), *_ = engine.lexical.search("denice bridge")
    assert engine.docs.key_of(o) == "chunk:1" and set(why) == {"denice", "bridge"}
    assert abs(sum(why.values()) - score) < 1e-3


def test_within_limits_the_candidates(engine):
    put(engine, [chunk(i, "denice") for i in range(10)])
    allowed = {engine.docs.get("chunk:3")["_ord"], engine.docs.get("chunk:7")["_ord"]}
    assert {o for o, _, _ in engine.lexical.search("denice", within=allowed)} == allowed


def test_only_chunks_are_indexed(engine):
    engine.transaction({"puts": [{"_key": "doc:1", "_kind": "document", "path": "denice.md"},
                                 chunk(1, "denice")]})
    assert [engine.docs.key_of(o) for o, _, _ in engine.lexical.search("denice")] == ["chunk:1"]


def test_a_common_term_does_not_scan_its_whole_list(engine, monkeypatch):
    # "common" is in every chunk; "rare" in few. Once the top k is full of
    # rare matches, the common term only scores those, block by block.
    put(engine, [chunk(i, "common " + ("rare rare rare" if i < 5 else "filler")) for i in range(2000)])
    engine.store.flush()
    read = []
    real = lx.postings.Postings.block

    def counting(self, b):
        read.append(b)
        return real(self, b)
    monkeypatch.setattr(lx.postings.Postings, "block", counting)
    hits = engine.lexical.search("rare common", k=5)
    assert len(hits) == 5
    assert len(read) < 2000 // lx.postings.BLOCK      # far fewer than every block
