"""The exact vector scan and the embedder interface (build plan step 6).

Every test but the one marked `accelerate` uses the pure-Python maths and
the fake embedder."""
from __future__ import annotations

import ctypes
import random
import sys
import threading
import time
from ctypes import c_float

import pytest

from jarvis.knowledge import embedder as emb
from jarvis.knowledge import vecmath
from jarvis.knowledge import vectors as vx
from jarvis.knowledge.engine import Engine


@pytest.fixture(autouse=True)
def _seams(monkeypatch):
    monkeypatch.setattr(Engine, "compact_in_child", False)
    monkeypatch.setattr(vecmath, "MATH", vecmath.Reference())
    monkeypatch.setattr(emb, "RUNNER", lambda: emb.FakeEmbedder(32))


def open_engine(path):
    return Engine.open(path)


@pytest.fixture
def engine(tmp_path):
    e = open_engine(tmp_path / "k")
    yield e
    e.close()


TEXTS = ["the bridge engine follows the prototype", "denice is the successor direction",
         "bake a chocolate cake", "voice input and the microphone", "the roadmap names four stages",
         "agents work in project folders", "a package carries its references", "recall at ten"]


def chunk(i, text):
    return {"_key": f"chunk:{i}", "_kind": "chunk", "text": text}


def write(engine, chunks):
    engine.transaction({"puts": chunks})
    assert engine.vectors.wait_idle(20)


def nearest(engine, text, k=3, within=None):
    q = engine.vectors.embed_query(text, timeout=5)
    found = engine.vectors.search(q, k=k, within=within)
    return [engine.docs.key_of(o) for o, _ in found["hits"]]


# --- the maths ------------------------------------------------------------------

def _rows(rng, n, d):
    out = (c_float * (n * d))()
    for i in range(n):
        v = emb.normalise([rng.uniform(-1, 1) for _ in range(d)])
        for j in range(d):
            out[i * d + j] = v[j]
    return out


def test_top_k_is_exact_and_never_returns_a_refused_row():
    rng = random.Random(1)
    n = 1000
    scores = (c_float * n)(*[rng.uniform(-1, 1) for _ in range(n)])
    penalty = (c_float * n)(*[vecmath.REFUSED if i % 3 == 0 else 0.0 for i in range(n)])
    m = vecmath.Reference()
    m.add(scores, penalty, n)
    got = vecmath.top_k(scores, n, 25, m)
    want = sorted(((scores[i], i) for i in range(n) if i % 3), key=lambda h: (-h[0], h[1]))[:25]
    assert got == want
    few = (c_float * 4)(0.5, vecmath.REFUSED, vecmath.REFUSED, 0.1)
    assert [r for _, r in vecmath.top_k(few, 4, 10, m)] == [0, 3]


@pytest.mark.accelerate
def test_accelerate_gives_the_reference_answer():
    if sys.platform != "darwin":
        pytest.skip("Accelerate is macOS only")
    fast, slow = vecmath.Accelerate(), vecmath.Reference()
    rng = random.Random(2)
    n, d = 700, 64
    rows = _rows(rng, n, d)
    q = (c_float * d)(*emb.normalise([rng.uniform(-1, 1) for _ in range(d)]))
    a, b = (c_float * n)(), (c_float * n)()
    fast.gemv(rows, n, d, q, a)
    slow.gemv(rows, n, d, q, b)
    assert max(abs(a[i] - b[i]) for i in range(n)) < 1e-5
    penalty = (c_float * n)(*[vecmath.REFUSED if i % 5 == 0 else 0.0 for i in range(n)])
    fast.add(a, penalty, n)
    slow.add(b, penalty, n)
    ka = [r for _, r in vecmath.top_k(a, n, 20, fast)]
    kb = [r for _, r in vecmath.top_k(b, n, 20, slow)]
    assert ka == kb and not any(r % 5 == 0 for r in ka)


# --- the embedder interface -----------------------------------------------------

def test_the_fake_embedder_is_deterministic_and_normalised():
    a, b = emb.FakeEmbedder(32), emb.FakeEmbedder(32)
    va, = a.embed(["Denice is the successor"], "passage")
    vb, = b.embed(["Denice is the successor"], "query")
    assert list(va) == list(vb)
    assert abs(sum(x * x for x in va) - 1.0) < 1e-5


def test_with_no_embedder_nothing_is_embedded_and_search_says_so(tmp_path, monkeypatch):
    monkeypatch.setattr(emb, "RUNNER", None)
    e = open_engine(tmp_path / "k")
    try:
        e.transaction({"puts": [chunk(1, "text")]})
        assert e.vectors.embed_query("text") is None
        assert e.vectors.search([1.0] * 32)["hits"] == []
        assert e.health()["vectors"]["embedder"] is None
    finally:
        e.close()


def test_a_query_waits_behind_at_most_one_ingest_batch():
    started = []

    class Slow(emb.FakeEmbedder):
        def embed(self, texts, purpose):
            started.append(purpose)
            time.sleep(0.05)
            return super().embed(texts, purpose)
    queue = emb.EmbeddingQueue(Slow(8))
    try:
        done = threading.Event()
        for i in range(10):
            queue.submit(["x"] * emb.INGEST_BATCH, emb.INGEST, "passage",
                         (lambda v, e: done.set()) if i == 9 else (lambda v, e: None))
        time.sleep(0.01)
        assert queue.embed_now(["question"], timeout=5) is not None
        assert started.index("query") <= 1
        with pytest.raises(ValueError):
            queue.submit(["x"] * (emb.INGEST_BATCH + 1), emb.INGEST, "passage", lambda v, e: None)
        assert done.wait(5)
    finally:
        queue.close()


# --- the index -------------------------------------------------------------------

def test_search_finds_the_same_text_first_at_every_stage(tmp_path):
    e = open_engine(tmp_path / "k")
    write(e, [chunk(i, t) for i, t in enumerate(TEXTS[:4])])
    assert nearest(e, TEXTS[1])[0] == "chunk:1"           # unflushed rows
    e.store.flush()
    write(e, [chunk(i, t) for i, t in enumerate(TEXTS[4:], start=4)])
    assert nearest(e, TEXTS[1])[0] == "chunk:1"           # a file and the memtable
    assert nearest(e, TEXTS[6])[0] == "chunk:6"
    e.store.flush()
    e.store.compact(e.store.manifest.generations())
    assert nearest(e, TEXTS[2])[0] == "chunk:2"           # merged
    rows = e.vectors.health()["spaces"]["fake-32"]["rows"]
    assert rows == len(TEXTS)
    e.close()
    e = open_engine(tmp_path / "k")
    try:
        assert e.vectors.pending() == 0                   # nothing to embed again
        assert nearest(e, TEXTS[5])[0] == "chunk:5"
    finally:
        e.close()


def test_a_changed_text_never_answers_with_its_old_vector(engine):
    write(engine, [chunk(i, t) for i, t in enumerate(TEXTS)])
    engine.store.flush()
    engine.vectors.set_paused(True)                       # hold re-embedding
    engine.transaction({"puts": [chunk(2, "a completely different sentence about orbs")]})
    assert "chunk:2" not in nearest(engine, TEXTS[2], k=len(TEXTS))
    engine.vectors.set_paused(False)
    assert engine.vectors.wait_idle(20)
    assert nearest(engine, "a completely different sentence about orbs")[0] == "chunk:2"


def test_a_deleted_chunk_never_answers(engine):
    write(engine, [chunk(i, t) for i, t in enumerate(TEXTS)])
    engine.store.flush()
    engine.transaction({"deletes": ["chunk:3"]})
    assert "chunk:3" not in nearest(engine, TEXTS[3], k=len(TEXTS))
    engine.store.flush()
    engine.store.compact(engine.store.manifest.generations())
    assert "chunk:3" not in nearest(engine, TEXTS[3], k=len(TEXTS))


def test_within_limits_the_rows_by_gathering_and_by_mask(engine, monkeypatch):
    write(engine, [chunk(i, t) for i, t in enumerate(TEXTS)])
    engine.store.flush()
    allowed = {engine.docs.get(f"chunk:{i}")["_ord"] for i in (0, 4, 7)}
    gathered = nearest(engine, TEXTS[1], k=10, within=allowed)
    monkeypatch.setattr(vx, "GATHER_MAX", 0)
    masked = nearest(engine, TEXTS[1], k=10, within=allowed)
    assert sorted(gathered) == sorted(masked) == ["chunk:0", "chunk:4", "chunk:7"]
    assert gathered == masked


def test_past_the_deadline_the_answer_is_partial(engine):
    write(engine, [chunk(i, t) for i, t in enumerate(TEXTS)])
    engine.store.flush()
    q = engine.vectors.embed_query(TEXTS[0])
    found = engine.vectors.search(q, deadline_s=-1)
    assert found["partial"] and found["hits"] == []


def test_forget_removes_every_vector_row(engine):
    write(engine, [chunk(i, t) for i, t in enumerate(TEXTS)])
    engine.store.flush()
    engine.transaction({"puts": [{**chunk(9, "private words never to be found"),
                                  "_source": "jref:1:n:folder_file:secret"}]})
    assert engine.vectors.wait_idle(20)
    engine.store.flush()
    o = engine.docs.get("chunk:9")["_ord"]
    engine.docs.forget_sources(["jref:1:n:folder_file:secret"])
    for entry in engine.store.external_entries(vx.VECS):
        rows = vx._read_rows(engine.store.directory, entry)
        assert o not in [r.ordinal for r in rows]
    assert "chunk:9" not in nearest(engine, "private words never to be found", k=20)


def test_the_vector_files_are_mapped_and_hashed(engine):
    write(engine, [chunk(i, t) for i, t in enumerate(TEXTS)])
    engine.store.flush()
    (entry,) = engine.store.external_entries(vx.VECS)
    f32 = engine.store.directory / entry["files"][0]
    assert f32.stat().st_size == len(TEXTS) * 32 * 4
    import hashlib
    assert hashlib.sha256(f32.read_bytes()).hexdigest() == entry["sha256"]
    snap = engine.store.snapshot()
    assert not any(p.suffix in (".f32", ".ids") for p in snap.iterdir())   # snapshots leave out vectors


@pytest.mark.knowledge_engine
def test_the_compaction_child_merges_vector_files(tmp_path, monkeypatch):
    monkeypatch.setattr(Engine, "compact_in_child", True)
    e = open_engine(tmp_path / "k")
    try:
        for start in (0, 4):
            write(e, [chunk(i, t) for i, t in enumerate(TEXTS[start:start + 4], start=start)])
            e.store.flush()
        e.transaction({"deletes": ["chunk:6"]})
        e.store.flush()
        e.store.compact(e.store.manifest.generations())
        (entry,) = e.store.external_entries(vx.VECS)
        assert entry["rows"] == len(TEXTS) - 1
        assert nearest(e, TEXTS[4])[0] == "chunk:4"
        assert "chunk:6" not in nearest(e, TEXTS[6], k=20)
    finally:
        e.close()
