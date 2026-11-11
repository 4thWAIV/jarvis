"""The cross-engine query (build plan step 7)."""
from __future__ import annotations

import pytest

@pytest.fixture(autouse=True)
def indexing_enabled(monkeypatch):
    # These tests exercise indexing, with the legacy installation input.
    # Settings persistence and live enable/disable have their own tests.
    monkeypatch.setenv("JARVIS_KNOWLEDGE", "1")


from jarvis import knowledge_client as kc
from jarvis.knowledge import embedder as emb
from jarvis.knowledge import query as qy
from jarvis.knowledge import vecmath
from jarvis.knowledge.documents import DocumentError
from jarvis.knowledge.engine import Engine


@pytest.fixture(autouse=True)
def _seams(monkeypatch):
    monkeypatch.setattr(Engine, "compact_in_child", False)
    monkeypatch.setattr(vecmath, "MATH", vecmath.Reference())
    monkeypatch.setattr(emb, "RUNNER", lambda: emb.FakeEmbedder(32))


def node(key, kind, **props):
    return {"_key": key, "_kind": kind, "_label": key, **props}


CHUNKS = {
    # key: (document, text, language, trust)
    "chunk:a1": ("doc:about", "how the brain orbs are laid out in the centre", "en", "own"),
    "chunk:a2": ("doc:about", "the brain layout keeps every orb visible", "en", "own"),
    "chunk:a3": ("doc:about", "la disposición del cerebro y sus orbes", "es", "own"),
    "chunk:a4": ("doc:about", "brain layout notes a colleague sent", "en", "untrusted"),
    "chunk:b1": ("doc:other", "the brain layout of an unrelated project", "en", "own"),
    "chunk:b2": ("doc:other", "voice input and the microphone", "en", "own"),
}


@pytest.fixture
def engine(tmp_path):
    e = Engine.open(tmp_path / "k")
    e.transaction({
        "puts": [node("topic:denice", "topic", _status="planned"),
                 node("project:jarvis", "project"),
                 node("doc:about", "document"), node("doc:other", "document")]
                + [node(k, "chunk", text=t, language=lang, _trust=trust, document=d)
                   for k, (d, t, lang, trust) in CHUNKS.items()],
        "edges": [{"from": "doc:about", "kind": "about", "to": "topic:denice", "origin": "authored"},
                  {"from": "project:jarvis", "kind": "relates_to", "to": "topic:denice", "origin": "authored"}]})
    assert e.vectors.wait_idle(20)
    yield e
    e.close()


WALK = {"start": "topic:denice", "steps": [
    {"edge": "about", "dir": "in", "kinds": ["document"], "limit": 50},
    {"edge": "in_document", "dir": "in", "kinds": ["chunk"], "limit": 2000}]}


def keys(answer):
    return [h["key"] for h in answer["hits"]]


def test_one_request_crosses_documents_graph_keywords_and_vectors(engine):
    answer = qy.run(engine, {
        "from": {"kinds": ["chunk"], "where": [["_trust", "=", "own"], ["language", "=", "en"]]},
        "walk": WALK,
        "near": {"text": "how the brain orbs are laid out", "k": 50},
        "match": "brain layout",
        "return": {"properties": ["_key", "_label", "_source"], "limit": 20}})
    # Only own English chunks in documents about Denice: a1 and a2.
    assert sorted(keys(answer)) == ["chunk:a1", "chunk:a2"]
    top = answer["hits"][0]
    assert set(top["why"]) >= {"match", "walk"} and top["trust"] == "own"
    assert top["why"]["walk"]["edge"] == "in_document"
    assert not answer["partial"]


def test_the_answer_is_the_clauses_intersected_and_fused_by_hand(engine):
    body = {"from": {"kinds": ["chunk"], "where": [["language", "=", "en"]]}, "walk": WALK,
            "near": {"text": "brain layout", "k": 50}, "match": "brain layout"}
    got = qy.run(engine, body)
    allowed = {d["_ord"] for d in engine.docs.find(kinds_=["chunk"], where=[["language", "=", "en"]])}
    walked = {r["node"]["_ord"] for r in engine.graph.walk(WALK)["steps"][-1]}
    both = allowed & walked
    lex = engine.lexical.search("brain layout", within=both)
    vec = engine.vectors.search(engine.vectors.embed_query("brain layout"), 50, within=both)["hits"]
    fused = {}
    for ranking in ([o for o, _, _ in lex], [o for o, _ in vec]):
        for rank, o in enumerate(ranking, start=1):
            fused[o] = fused.get(o, 0) + 1 / (qy.RRF_K + rank)
    for o in fused:
        fused[o] += 1 / (qy.RRF_K + 2)        # every one was reached at the walk's second step
    want = [engine.docs.key_of(o) for o in sorted(fused, key=lambda o: (-fused[o], o))]
    assert keys(got) == want


def test_a_refused_document_never_comes_through_the_vector_mask(engine):
    answer = qy.run(engine, {"near": {"text": "brain layout notes a colleague sent"},
                             "visible": {"trust": ["own"]}, "return": {"limit": 50}})
    assert "chunk:a4" not in keys(answer) and keys(answer)
    assert all(h["trust"] == "own" for h in answer["hits"])
    everything = qy.run(engine, {"near": {"text": "brain layout notes a colleague sent"}})
    assert keys(everything)[0] == "chunk:a4"


def test_a_large_where_is_checked_on_the_candidates_with_the_same_answer(engine, monkeypatch):
    body = {"from": {"kinds": ["chunk"], "where": [["_trust", "=", "own"]]},
            "match": "brain layout", "near": {"text": "brain layout"}}
    small = qy.run(engine, body)
    monkeypatch.setattr(qy, "SMALL_WHERE", 0)
    large = qy.run(engine, body)
    assert set(keys(small)) == set(keys(large))
    assert "chunk:a4" not in keys(large)


def test_without_an_embedder_near_is_skipped_and_said(tmp_path, monkeypatch):
    monkeypatch.setattr(emb, "RUNNER", None)
    e = Engine.open(tmp_path / "k2")
    try:
        e.transaction({"puts": [node("chunk:1", "chunk", text="brain layout")]})
        answer = qy.run(e, {"match": "brain layout", "near": {"text": "brain layout"}})
        assert keys(answer) == ["chunk:1"]
        assert any("no embedder" in n for n in answer["notes"])
        assert "near" not in answer["hits"][0]["why"]
    finally:
        e.close()


def test_from_alone_is_ordered_newest_first_or_as_asked(engine):
    answer = qy.run(engine, {"from": {"kinds": ["chunk"], "where": [["language", "=", "en"]]},
                             "return": {"limit": 3}})
    assert len(answer["hits"]) == 3
    lsns = [h["properties"]["_lsn"] for h in answer["hits"]]
    assert lsns == sorted(lsns, reverse=True)


def test_an_older_read_takes_from_and_walk_only(engine):
    before = engine.store.lsn
    engine.transaction({"edge_deletes": [{"from": "doc:about", "kind": "about", "to": "topic:denice"}]})
    assert keys(qy.run(engine, {"walk": WALK})) == []
    old = qy.run(engine, {"walk": WALK, "at": before, "return": {"limit": 50}})
    assert sorted(keys(old)) == ["chunk:a1", "chunk:a2", "chunk:a3", "chunk:a4"]
    with pytest.raises(DocumentError):
        qy.run(engine, {"match": "brain", "at": before})


@pytest.mark.parametrize("body", [
    {},
    {"match": 3},
    {"near": "text"},
    {"match": "x", "return": {"limit": 500}},
    {"match": "x", "colour": "red"},
    {"from": {"where": [["a", "=", 1]] * 9}},
])
def test_bounds_and_shapes_are_refused(engine, body):
    with pytest.raises(DocumentError):
        qy.run(engine, body)


def test_routes_through_the_client(engine):
    client = kc.KnowledgeClient(kc.InProcessTransport(engine))
    found = client.call("POST", "/v1/search", {"text": "brain layout", "kinds": ["chunk"],
                                               "visible": {"trust": ["own"]}})
    assert found["hits"] and "chunk:a4" not in keys(found)
    assert client.call("POST", "/v1/query", {"walk": WALK})["hits"]
    with pytest.raises(kc.KnowledgeRefused):
        client.call("POST", "/v1/query", {})
