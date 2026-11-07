"""The document store and its general index (build plan step 3)."""
from __future__ import annotations

import os
import random
from pathlib import Path

import pytest

from jarvis import knowledge_client as kc
from jarvis.knowledge import documents as dm
from jarvis.knowledge.engine import Engine
from jarvis.knowledge.store import DOCS


@pytest.fixture(autouse=True)
def _in_process_compaction(monkeypatch):
    monkeypatch.setattr(Engine, "compact_in_child", False)


@pytest.fixture
def engine(tmp_path):
    e = Engine.open(tmp_path / "k")
    yield e
    e.close()


def doc(key, kind="document", **props):
    return {"_key": key, "_kind": kind, "_label": key, "_source": f"jref:1:n:note:{key}", **props}


def keys(docs):
    return sorted(d["_key"] for d in docs)


def brute(engine, where, kinds_=None):
    preds = [dm.Predicate.parse(w) for w in where]
    if kinds_:
        preds.append(dm.Predicate("_kind", "in", kinds_))
    out = []
    for v in engine.store.scan(DOCS):
        o, d = dm.decode_doc(v.value)
        dates = {id(p): engine.docs._is_date(p, kinds_) for p in preds}
        if all(p.matches(d, dates[id(p)]) for p in preds):
            out.append(d["_key"])
    return sorted(out)


# --- the schema --------------------------------------------------------------

@pytest.mark.parametrize("bad, words", [
    ({"_key": "x", "_kind": "document", "colour": "red"}, "no property"),
    ({"_key": "x", "_kind": "document", "_lsn": 4}, "engine's own"),
    ({"_key": "x", "_kind": "document", "_secret": 1}, "reserved"),
    ({"_key": "x", "_kind": "document", "_trust": "maybe"}, "_trust"),
    ({"_key": "x", "_kind": "spaceship"}, "unknown kind"),
    ({"_key": "", "_kind": "document"}, "_key"),
])
def test_the_schema_refuses(engine, bad, words):
    with pytest.raises(dm.DocumentError, match=words):
        engine.docs.write([bad])


def test_system_properties_are_filled_and_references_are_not_kept(engine):
    engine.docs.write([{"_key": "folder:1", "_kind": "folder"}, doc("doc:a", path="a.md", folder="folder:1")])
    got = engine.docs.get("doc:a")
    assert got["_trust"] == "own" and got["_ns"] == "local" and got["_owner"] == "local"
    assert len(got["_hash"]) == 64 and got["_ord"] >= 1 and got["_lsn"] >= 1
    assert "folder" not in got


def test_ordinals_are_kept_across_updates_flush_and_reopen(tmp_path):
    e = Engine.open(tmp_path / "k")
    e.docs.write([doc("doc:a"), doc("doc:b")])
    a, b = e.docs.get("doc:a")["_ord"], e.docs.get("doc:b")["_ord"]
    assert a != b
    e.docs.write([doc("doc:a", path="changed")])
    assert e.docs.get("doc:a")["_ord"] == a
    e.store.flush()
    e.docs.write([doc("doc:c")])
    c = e.docs.get("doc:c")["_ord"]
    e.close()
    e = Engine.open(tmp_path / "k")
    try:
        assert [e.docs.get(k)["_ord"] for k in ("doc:a", "doc:b", "doc:c")] == [a, b, c]
        e.docs.write([doc("doc:d")])
        assert e.docs.get("doc:d")["_ord"] > c
        assert e.docs.key_of(a) == "doc:a"
    finally:
        e.close()


# --- the index answers what a scan answers -----------------------------------

def _corpus(rng: random.Random, n: int, start: int = 0) -> list[dict]:
    out = []
    for i in range(start, start + n):
        kind = rng.choice(["document", "chunk", "topic"])
        if kind == "document":
            out.append(doc(f"doc:{i}", "document", path=f"docs/{rng.choice(['a', 'b', 'c'])}/{i}.md",
                           language=rng.choice(["en", "es"]), bytes=rng.randint(0, 5000),
                           modified=f"2026-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}T10:00:00Z",
                           headings=rng.sample(["Intro", "Plan", "Café", "Notes", "Risks"], rng.randint(0, 3))))
        elif kind == "chunk":
            out.append(doc(f"chunk:{i}", "chunk", text=f"text {i}", language=rng.choice(["en", "es"]),
                           span=[rng.randint(0, 99), rng.randint(100, 999)],
                           heading_path=rng.sample(["Intro", "Plan", "Café"], rng.randint(0, 2)),
                           _trust=rng.choice(["own", "untrusted"])))
        else:
            out.append(doc(f"topic:{i}", "topic", pinned=rng.random() < 0.3,
                           aliases={"exact": [f"Name{i % 7}"], "ambiguous": [f"Other{i % 5}"]},
                           front={"status": rng.choice(["planned", "implemented", "active"])},
                           _status=rng.choice(["planned", "implemented"])))
    return out


QUERIES = [
    ([["language", "=", "en"]], None),
    ([["language", "=", "en"]], ["chunk"]),
    ([["bytes", ">=", 2500]], ["document"]),
    ([["bytes", "<", 1000], ["language", "=", "es"]], ["document"]),
    ([["modified", ">=", "2026-10-01T00:00:00Z"]], ["document"]),
    ([["modified", "<", "2026-03-01"]], ["document"]),
    ([["headings[]", "=", "Cafe"]], ["document"]),
    ([["headings[]", "=", "Café"]], None),
    ([["heading_path[]", "in", ["Plan", "Intro"]]], ["chunk"]),
    ([["front.status", "=", "planned"]], ["topic"]),
    ([["aliases.exact[]", "=", "Name3"]], ["topic"]),
    ([["pinned", "=", True]], ["topic"]),
    ([["path", "prefix", "docs/b/"]], ["document"]),
    ([["_trust", "=", "untrusted"]], ["chunk"]),
    ([["span[]", ">", 500]], ["chunk"]),             # chunks do not index span: a scan
    ([["_status", "!=", "planned"]], ["topic"]),
    ([["headings[]", "exists"]], ["document"]),
    ([["nothing.ever", "=", 1]], None),
    ([], ["topic"]),
]


def _check_all(engine):
    for where, kinds_ in QUERIES:
        got = keys(engine.docs.find(kinds_=kinds_, where=where, limit=1 << 20))
        assert got == brute(engine, where, kinds_), (where, kinds_)


def test_the_index_answers_what_a_scan_answers_at_every_stage(tmp_path):
    rng = random.Random(42)
    e = Engine.open(tmp_path / "k")
    corpus = _corpus(rng, 300)
    e.docs.write(corpus[:150])
    _check_all(e)                                   # memtable only
    e.store.flush()
    e.docs.write(corpus[150:])
    _check_all(e)                                   # a generation and the memtable
    e.store.flush()
    changed = _corpus(random.Random(7), 60)         # the same keys, other values
    e.docs.write(changed)
    e.docs.write([], deletes=[d["_key"] for d in corpus[::9]])
    _check_all(e)                                   # stale entries in old generations
    e.store.flush()
    _check_all(e)
    e.store.compact(e.store.manifest.generations())
    _check_all(e)                                   # merged
    e.docs.write(_corpus(random.Random(9), 40, start=1000))
    e.close()
    e = Engine.open(tmp_path / "k")                 # replayed and loaded
    try:
        _check_all(e)
    finally:
        e.close()


def test_semantics_by_hand(engine):
    engine.docs.write([
        doc("doc:1", path="docs/a.md", bytes=10, modified="2026-11-01T00:00:00Z", headings=["Plan", "Café"]),
        doc("doc:2", path="docs/b.md", bytes=2000, modified="2026-01-01T00:00:00Z", headings=[]),
        doc("doc:3", path="other/c.md", bytes=-5, modified="2026-06-15T12:00:00+02:00"),
    ])
    f = lambda w, k=("document",): keys(engine.docs.find(kinds_=list(k), where=w))  # noqa: E731
    assert f([["bytes", ">", 0]]) == ["doc:1", "doc:2"]
    assert f([["bytes", "<", 0]]) == ["doc:3"]
    assert f([["modified", ">=", "2026-06-01"]]) == ["doc:1", "doc:3"]
    assert f([["headings[]", "=", "Cafe"]]) == ["doc:1"]          # accents fold, case counts
    assert f([["headings[]", "=", "cafe"]]) == []
    assert f([["path", "prefix", "docs/"]]) == ["doc:1", "doc:2"]
    assert f([["bytes", "in", [10, -5]]]) == ["doc:1", "doc:3"]
    assert f([["headings[]", "exists"]]) == ["doc:1"]
    assert f([["bytes", "=", "10"]]) == []           # types never compare equal
    ordered = engine.docs.find(kinds_=["document"], order=("bytes", "desc"))
    assert [d["_key"] for d in ordered] == ["doc:2", "doc:1", "doc:3"]


def test_the_planner_starts_from_the_smallest_list(engine, monkeypatch):
    docs = [doc(f"doc:{i}", language="en" if i else "es", bytes=i) for i in range(400)]
    engine.docs.write(docs)
    engine.store.flush()
    built = []
    real = engine.docs._range_sources

    def spy(ranges):
        estimate, build = real(ranges)

        def tracked():
            out = build()
            built.append(len(out))
            return out
        return estimate, tracked
    monkeypatch.setattr(engine.docs, "_range_sources", spy)
    got = engine.docs.find(kinds_=["document"], where=[["language", "=", "es"], ["bytes", "<", 1000]])
    assert keys(got) == ["doc:0"]
    assert built == [1]          # only the one-document list was decoded


def test_older_reads_see_the_documents_as_they_were(engine):
    first = engine.docs.write([doc("doc:a", language="en")])
    engine.docs.write([doc("doc:a", language="es")])
    engine.store.flush()
    assert keys(engine.docs.find(kinds_=["document"], where=[["language", "=", "en"]])) == []
    old = engine.docs.find(kinds_=["document"], where=[["language", "=", "en"]], at=first)
    assert keys(old) == ["doc:a"] and old[0]["language"] == "en"
    assert engine.docs.get("doc:a", at=first)["language"] == "en"


def _all_bytes(directory: Path) -> bytes:
    out = bytearray()
    for root, _, files in os.walk(directory):
        for name in files:
            out += Path(root, name).read_bytes()
    return bytes(out)


def test_forgetting_a_source_removes_the_document_and_every_index_entry(engine):
    secret = "SECRETPATH" + os.urandom(8).hex()
    engine.docs.write([doc("doc:keep", path="public.md"),
                       {**doc("doc:gone", path=secret), "_source": "jref:1:n:folder_file:f/x.md"}])
    engine.store.flush()
    engine.store.snapshot()
    engine.docs.write([{**doc("doc:gone", path=secret + "v2"), "_source": "jref:1:n:folder_file:f/x.md"}])
    assert secret.encode() in _all_bytes(engine.directory)
    report = engine.docs.forget_sources(["jref:1:n:folder_file:f/x.md"])
    assert report["documents"] == 1
    assert secret.encode() not in _all_bytes(engine.directory)
    assert engine.docs.get("doc:gone") is None
    assert keys(engine.docs.find(where=[["path", "prefix", "SECRETPATH"]], kinds_=["document"])) == []
    assert keys(engine.docs.find(kinds_=["document"])) == ["doc:keep"]


# --- through the client -----------------------------------------------------

def test_routes_through_the_client(engine):
    client = kc.KnowledgeClient(kc.InProcessTransport(engine))
    lsn = client.call("POST", "/v1/txn", {"puts": [doc("doc:x", language="en")]})["lsn"]
    assert client.call("GET", "/v1/nodes/doc:x")["_lsn"] == lsn
    found = client.call("POST", "/v1/find", {"kinds": ["document"], "where": [["language", "=", "en"]]})
    assert keys(found["documents"]) == ["doc:x"]
    with pytest.raises(kc.KnowledgeRefused) as refused:
        client.call("POST", "/v1/txn", {"puts": [{"_key": "y", "_kind": "document", "colour": 1}]})
    assert refused.value.status == 400
    with pytest.raises(kc.KnowledgeRefused):
        client.call("GET", "/v1/nodes/doc:none")
    assert client.call("POST", "/v1/forget", {"refs": ["jref:1:n:note:doc:x"]})["documents"] == 1


def test_resident_columns_are_columnar(engine):
    engine.docs.write([doc(f"doc:{i}") for i in range(2000)])
    assert engine.docs.resident_bytes() < 2000 * 40


@pytest.mark.knowledge_engine
def test_the_compaction_child_builds_the_same_index(tmp_path, monkeypatch):
    monkeypatch.setattr(Engine, "compact_in_child", True)
    e = Engine.open(tmp_path / "k")
    try:
        corpus = _corpus(random.Random(3), 200)
        for i in range(4):
            e.docs.write(corpus[i * 50:(i + 1) * 50])
            e.store.flush()
        e.store.compact()
        assert len(e.store.manifest.generations()) == 1
        _check_all(e)
    finally:
        e.close()
