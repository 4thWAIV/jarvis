"""The typed graph (build plan step 4)."""
from __future__ import annotations

import os
import random
import statistics
import time
from pathlib import Path

import pytest

from jarvis import knowledge_client as kc
from jarvis.knowledge.documents import DocumentError
from jarvis.knowledge.engine import Engine
from jarvis.knowledge.store import EDGES_IN, EDGES_OUT


@pytest.fixture(autouse=True)
def _in_process_compaction(monkeypatch):
    monkeypatch.setattr(Engine, "compact_in_child", False)


@pytest.fixture
def engine(tmp_path):
    e = Engine.open(tmp_path / "k")
    yield e
    e.close()


def node(key, kind, **props):
    return {"_key": key, "_kind": kind, "_label": key, **props}


def txn(engine, puts=(), edges=(), deletes=(), edge_deletes=()):
    return engine.transaction({"puts": list(puts), "edges": list(edges), "deletes": list(deletes),
                               "edge_deletes": list(edge_deletes)})


def out(engine, key, kind=None):
    return sorted((e["kind"], e["to"]) for e in engine.graph.edges(key, "out", kind))


def inn(engine, key, kind=None):
    return sorted((e["kind"], e["from"]) for e in engine.graph.edges(key, "in", kind))


def denice(engine):
    txn(engine, puts=[
        node("project:jarvis", "project"),
        node("topic:denice", "topic", _status="planned"),
        node("topic:prototype-engine", "topic", _status="implemented"),
        node("topic:bridge-engine", "topic", _status="planned"),
        node("folder:src", "folder"),
        node("doc:roadmap", "document", folder="folder:src"),
        node("chunk:1", "chunk", text="Denice is the successor", document="doc:roadmap"),
    ], edges=[
        {"from": "project:jarvis", "kind": "relates_to", "to": "topic:denice", "origin": "authored"},
        {"from": "topic:prototype-engine", "kind": "stage_of", "to": "topic:denice", "origin": "authored"},
        {"from": "topic:bridge-engine", "kind": "stage_of", "to": "topic:denice", "origin": "authored"},
        {"from": "topic:prototype-engine", "kind": "precedes", "to": "topic:bridge-engine", "origin": "authored"},
        {"from": "project:jarvis", "kind": "has_folder", "to": "folder:src", "origin": "record"},
        {"from": "chunk:1", "kind": "mentions", "to": "topic:denice", "origin": "extracted", "weight": 0.9},
    ])


# --- writing ------------------------------------------------------------------

@pytest.mark.parametrize("edge, words", [
    ({"from": "chunk:1", "kind": "has_folder", "to": "folder:src"}, "does not go from a chunk"),
    ({"from": "project:jarvis", "kind": "teleports", "to": "folder:src"}, "unknown edge kind"),
    ({"from": "project:jarvis", "kind": "has_folder", "to": "folder:none"}, "no document"),
    ({"from": "project:jarvis", "kind": "same_as", "to": "topic:denice", "origin": "authored"}, "two of one kind"),
    ({"from": "topic:denice", "kind": "possibly_same", "to": "topic:bridge-engine", "origin": "authored"}, "extracted"),
    ({"from": "project:jarvis", "kind": "has_folder", "to": "folder:src", "origin": "gossip"}, "origin"),
    ({"from": "project:jarvis", "kind": "has_folder", "to": "folder:src", "weight": 3}, "weight"),
])
def test_edges_are_checked_on_write(engine, edge, words):
    denice(engine)
    with pytest.raises(DocumentError, match=words):
        txn(engine, edges=[edge])


def test_edges_are_reachable_both_ways_and_carry_origin_weight_and_lsn(engine):
    denice(engine)
    assert ("stage_of", "topic:prototype-engine") in inn(engine, "topic:denice")
    assert out(engine, "topic:prototype-engine") == [("precedes", "topic:bridge-engine"),
                                                     ("stage_of", "topic:denice")]
    e = next(engine.graph.edges("chunk:1", "out", "mentions"))
    assert e["origin"] == "extracted" and e["weight"] == 0.9 and e["lsn"] >= 1


def test_reference_properties_become_record_edges_and_are_replaced(engine):
    denice(engine)
    assert out(engine, "chunk:1", "in_document") == [("in_document", "doc:roadmap")]
    assert inn(engine, "folder:src", "in_folder") == [("in_folder", "doc:roadmap")]
    assert "document" not in engine.docs.get("chunk:1")
    txn(engine, puts=[node("doc:other", "document"),
                      node("chunk:1", "chunk", text="moved", document="doc:other")])
    assert out(engine, "chunk:1", "in_document") == [("in_document", "doc:other")]
    assert inn(engine, "doc:roadmap", "in_document") == []
    with pytest.raises(DocumentError, match="in_document does not go"):
        txn(engine, puts=[node("chunk:2", "chunk", text="x", document="folder:src")])


def test_a_document_and_its_edges_can_be_made_in_one_write(engine):
    txn(engine, puts=[node("topic:a", "topic"), node("topic:b", "topic")],
        edges=[{"from": "topic:a", "kind": "precedes", "to": "topic:b", "origin": "authored"}])
    assert out(engine, "topic:a") == [("precedes", "topic:b")]


def test_deleting_a_document_deletes_every_edge_on_it(engine):
    denice(engine)
    txn(engine, deletes=["topic:denice"])
    assert inn(engine, "topic:denice") == []
    assert ("stage_of", "topic:denice") not in out(engine, "topic:bridge-engine")
    assert out(engine, "chunk:1", "mentions") == []
    assert out(engine, "project:jarvis") == [("has_folder", "folder:src")]


def test_an_edge_can_be_deleted(engine):
    denice(engine)
    txn(engine, edge_deletes=[{"from": "topic:prototype-engine", "kind": "precedes", "to": "topic:bridge-engine"}])
    assert out(engine, "topic:prototype-engine") == [("stage_of", "topic:denice")]
    assert inn(engine, "topic:bridge-engine") == []


def test_edges_survive_flush_compaction_and_reopen(tmp_path):
    e = Engine.open(tmp_path / "k")
    denice(e)
    e.store.flush()
    txn(e, edge_deletes=[{"from": "project:jarvis", "kind": "has_folder", "to": "folder:src"}])
    e.store.flush()
    e.store.compact(e.store.manifest.generations())
    expect = (out(e, "project:jarvis"), inn(e, "topic:denice"))
    e.close()
    e = Engine.open(tmp_path / "k")
    try:
        assert (out(e, "project:jarvis"), inn(e, "topic:denice")) == expect
        assert expect[0] == [("relates_to", "topic:denice")]
    finally:
        e.close()


# --- the walk -------------------------------------------------------------------

def test_walk_from_a_project_reaches_the_stages_of_its_topic(engine):
    denice(engine)
    found = engine.graph.walk({"start": "project:jarvis", "steps": [
        {"edge": "relates_to", "dir": "out", "kinds": ["topic"]},
        {"edge": "stage_of", "dir": "in", "kinds": ["topic"]}]})
    assert [n["node"]["_key"] for n in found["steps"][0]] == ["topic:denice"]
    assert sorted(n["node"]["_key"] for n in found["steps"][1]) == ["topic:bridge-engine",
                                                                    "topic:prototype-engine"]
    assert not found["partial"]


def test_walk_filters_nodes_and_edges(engine):
    denice(engine)
    planned = engine.graph.walk({"start": "topic:denice", "steps": [
        {"edge": "stage_of", "dir": "in", "where": [["_status", "=", "planned"]]}]})
    assert [n["node"]["_key"] for n in planned["steps"][0]] == ["topic:bridge-engine"]
    strong = engine.graph.walk({"start": "topic:denice", "steps": [
        {"dir": "in", "min_weight": 0.95}]})
    assert "chunk:1" not in [n["node"]["_key"] for n in strong["steps"][0]]
    authored = engine.graph.walk({"start": "topic:denice", "steps": [
        {"dir": "in", "origins": ["extracted"]}]})
    assert [n["node"]["_key"] for n in authored["steps"][0]] == ["chunk:1"]


def test_walk_bounds(engine):
    txn(engine, puts=[node("topic:hub", "topic")] + [node(f"topic:{i}", "topic") for i in range(30)])
    txn(engine, edges=[{"from": f"topic:{i}", "kind": "stage_of", "to": "topic:hub", "origin": "authored"}
                       for i in range(30)])
    limited = engine.graph.walk({"start": "topic:hub", "steps": [{"edge": "stage_of", "dir": "in", "limit": 5}]})
    assert len(limited["steps"][0]) == 5 and limited["partial"]
    capped = engine.graph.walk({"start": "topic:hub", "max_nodes": 10,
                                "steps": [{"edge": "stage_of", "dir": "in", "limit": 100}]})
    assert len(capped["steps"][0]) == 9 and capped["partial"]
    with pytest.raises(DocumentError):
        engine.graph.walk({"start": "topic:hub", "steps": [{"dir": "in"}] * 5})
    with pytest.raises(DocumentError):
        engine.graph.walk({"start": "topic:hub", "steps": [{"dir": "sideways"}]})
    assert engine.graph.walk({"start": "topic:none", "steps": [{"dir": "in"}]})["start"] is None


def test_walk_at_an_older_lsn(engine):
    denice(engine)
    before = engine.store.lsn
    txn(engine, edge_deletes=[{"from": "project:jarvis", "kind": "relates_to", "to": "topic:denice"}])
    spec = {"start": "project:jarvis", "steps": [{"edge": "relates_to"}]}
    assert engine.graph.walk(spec)["steps"][0] == []
    assert [n["node"]["_key"] for n in engine.graph.walk({**spec, "at": before})["steps"][0]] == ["topic:denice"]


def test_edges_route_pages(engine):
    txn(engine, puts=[node("topic:hub", "topic")] + [node(f"topic:{i:02d}", "topic") for i in range(25)])
    txn(engine, edges=[{"from": f"topic:{i:02d}", "kind": "stage_of", "to": "topic:hub", "origin": "authored"}
                       for i in range(25)])
    client = kc.KnowledgeClient(kc.InProcessTransport(engine))
    seen, after = [], None
    while True:
        q = f"/v1/edges/topic:hub?dir=in&limit=10" + (f"&after={after}" if after else "")
        page = client.call("GET", q)
        seen += [e["from"] for e in page["edges"]]
        after = page["after"]
        if after is None:
            break
    assert seen == [f"topic:{i:02d}" for i in range(25)]
    walked = client.call("POST", "/v1/walk", {"start": "topic:hub", "steps": [{"dir": "in", "limit": 3}]})
    assert len(walked["steps"][0]) == 3


def test_forget_removes_the_edges_on_a_forgotten_document(engine):
    secret = "SECRET" + os.urandom(6).hex()
    denice(engine)
    txn(engine, puts=[{**node("doc:x", "document", folder="folder:src"), "_source": "jref:1:n:folder_file:x"}],
        edges=[{"from": "doc:x", "kind": "about", "to": "topic:denice", "origin": "extracted",
                "props": {"why": secret}}])
    engine.store.flush()
    engine.docs.forget_sources(["jref:1:n:folder_file:x"])
    assert inn(engine, "topic:denice", "about") == [] and inn(engine, "folder:src", "in_folder") == [
        ("in_folder", "doc:roadmap")]
    blob = b"".join(Path(r, f).read_bytes() for r, _, fs in os.walk(engine.directory) for f in fs)
    assert secret.encode() not in blob
    assert not list(engine.store.prefix(EDGES_OUT, b"doc:x\x00"))
    assert not list(engine.store.prefix(EDGES_IN, b"doc:x\x00"))


# --- the budget ---------------------------------------------------------------

@pytest.mark.knowledge_engine
def test_two_hop_walk_meets_ten_ms_p95_on_the_synthetic_corpus(tmp_path):
    rng = random.Random(1)
    e = Engine.open(tmp_path / "k")
    try:
        projects, folders = 300, 5
        for p in range(projects):
            puts = [node(f"project:{p}", "project")]
            puts += [node(f"folder:{p}.{f}", "folder") for f in range(folders)]
            puts += [node(f"doc:{p}.{f}.{d}", "document", folder=f"folder:{p}.{f}")
                     for f in range(folders) for d in range(20)]
            edges = [{"from": f"project:{p}", "kind": "has_folder", "to": f"folder:{p}.{f}", "origin": "record"}
                     for f in range(folders)]
            txn(e, puts=puts, edges=edges)
        e.store.flush()
        spec = lambda p: {"start": f"project:{p}", "steps": [  # noqa: E731
            {"edge": "has_folder", "dir": "out", "limit": 20},
            {"edge": "in_folder", "dir": "in", "limit": 50}]}
        for p in range(projects):
            e.graph.walk(spec(p))                  # warm the block pool
        times = []
        for _ in range(300):
            t0 = time.perf_counter()
            found = e.graph.walk(spec(rng.randrange(projects)))
            times.append(time.perf_counter() - t0)
            assert len(found["steps"][1]) == 100
        p95 = statistics.quantiles(times, n=20)[18] * 1000
        assert p95 <= 10.0 * max(1.0, os.getloadavg()[0] / 8), f"p95 {p95:.1f} ms"
    finally:
        e.close()
