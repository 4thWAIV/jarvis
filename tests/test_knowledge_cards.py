"""Entity cards, importance, the resident tier and HotCards (phase 4)."""
from __future__ import annotations

import json

import pytest

from jarvis import knowledge_client as kc
from jarvis.knowledge import cards as cd
from jarvis.knowledge.engine import Engine


@pytest.fixture(autouse=True)
def _in_process(monkeypatch):
    monkeypatch.setattr(Engine, "compact_in_child", False)


@pytest.fixture
def engine(tmp_path):
    e = Engine.open(tmp_path / "k")
    yield e
    e.close()


def node(key, kind, **props):
    return {"_key": key, "_kind": kind, "_label": key.split(":", 1)[1], **props}


def world(e):
    e.transaction({"puts": [
        node("topic:denice", "topic", _status="planned", pinned=True,
             aliases={"exact": ["Denice"], "ambiguous": ["Denise"]}),
        node("topic:bridge", "topic", _status="planned"),
        node("topic:cake", "topic"),
        node("doc:d", "document", _source="jref:1:n:note:topics/denice.md"),
        node("chunk:1", "chunk", text="Denice is the successor direction.", document="doc:d"),
        node("chunk:2", "chunk", text="Their words.", document="doc:d", _trust="untrusted"),
    ], "edges": [
        {"from": "topic:bridge", "kind": "stage_of", "to": "topic:denice", "origin": "authored"},
        {"from": "chunk:1", "kind": "mentions", "to": "topic:denice", "origin": "extracted", "weight": 0.9},
        {"from": "doc:d", "kind": "about", "to": "topic:denice", "origin": "authored"},
    ]})


def test_a_card_is_bounded_and_puts_asserted_links_first(engine):
    world(engine)
    card = engine.cards.get("topic:denice")
    assert card["label"] == "denice" and card["status"] == "planned" and card["pinned"]
    assert [e["origin"] for e in card["edges"]][:2] == ["authored", "authored"]
    assert card["edges"][-1]["origin"] == "extracted"
    assert len(json.dumps(card, ensure_ascii=False)) <= cd.CARD_MAX
    doc = engine.cards.get("doc:d")
    assert doc["summary"] == "Denice is the successor direction."     # own words only
    many = [node(f"topic:t{i}", "topic") for i in range(40)]
    engine.transaction({"puts": many, "edges": [
        {"from": f"topic:t{i}", "kind": "relates_to", "to": "topic:denice", "origin": "authored"} for i in range(40)]})
    big = engine.cards.get("topic:denice")
    assert len(big["edges"]) <= cd.TOP_EDGES and len(json.dumps(big, ensure_ascii=False)) <= cd.CARD_MAX


def test_a_write_drops_the_cards_it_touches(engine):
    world(engine)
    assert engine.cards.get("topic:denice")["status"] == "planned"
    engine.transaction({"puts": [node("topic:denice", "topic", _status="active", pinned=True)]})
    assert engine.cards.get("topic:denice")["status"] == "active"
    engine.transaction({"edge_deletes": [{"from": "topic:bridge", "kind": "stage_of", "to": "topic:denice"}]})
    assert "topic:bridge" not in [e["key"] for e in engine.cards.get("topic:denice")["edges"]]


def test_importance_puts_the_pinned_and_their_neighbours_first(engine):
    world(engine)
    engine.cards.refresh()
    s = engine.cards.scores
    assert s["topic:denice"] > s["topic:bridge"] > s["topic:cake"]
    assert "topic:denice" in engine.cards.resident
    hot = engine.cards.hot()
    assert hot[0]["key"] == "topic:denice"


def test_use_raises_importance_and_decays():
    c = cd.Cards.__new__(cd.Cards)
    c._use = {}
    c.used("a", now=0)
    c.used("a", now=0)
    fresh = c._use_score("a", 0)
    later = c._use_score("a", cd.USE_HALF_LIFE_SEC * 3)
    assert 0 < later < fresh < 1


def test_focus_and_warm_start(tmp_path):
    e = Engine.open(tmp_path / "k")
    world(e)
    assert e.cards.focus("topic:denice")["nodes"] >= 3
    e.cards.refresh()
    e.close()
    e = Engine.open(tmp_path / "k")
    try:
        assert "topic:denice" in e.cards.resident          # warmed from cache.warm
    finally:
        e.close()
    other = Engine.open(tmp_path / "other")
    try:
        (tmp_path / "other" / "cache.warm").write_text((tmp_path / "k" / "cache.warm").read_text())
        assert other.cards.load_warm(tmp_path / "other" / "cache.warm") == 0   # another engine_id
    finally:
        other.close()


def test_routes(engine):
    world(engine)
    client = kc.KnowledgeClient(kc.InProcessTransport(engine))
    assert client.call("GET", "/v1/cards/topic:denice")["key"] == "topic:denice"
    hot = client.call("GET", "/v1/hot")
    assert hot["engine_id"] == engine.store.engine_id and hot["cards"]
    assert client.call("POST", "/v1/focus", {"key": "topic:denice"})["card"]
    with pytest.raises(kc.KnowledgeRefused):
        client.call("GET", "/v1/cards/topic:none")


def test_hot_cards_keep_within_budget_and_follow_the_engine(monkeypatch):
    monkeypatch.setattr(kc, "HOT_CARDS_MAX_BYTES", 300)
    hot = kc.HotCards()
    cards = [{"key": f"topic:{i}", "label": f"Name {i}", "aliases": {"exact": [f"Alias{i}"]},
              "summary": "x" * 50} for i in range(10)]
    hot.update("e1", cards)
    assert 0 < len(hot.cards) < 10 and hot.bytes <= 300
    assert [c["key"] for c in hot.find("alias0")] == ["topic:0"]
    assert [c["key"] for c in hot.find("NAME 1")] == ["topic:1"]
    hot.update("e2", cards[5:6])
    assert list(hot.cards) == ["topic:5"] and hot.engine_id == "e2"


def test_the_resident_tier_stays_columnar_and_small(engine):
    engine.transaction({"puts": [node(f"topic:t{i}", "topic") for i in range(500)]})
    engine.cards.refresh()
    per_card = sum(len(json.dumps(c)) for c in engine.cards.resident.values()) / max(1, len(engine.cards.resident))
    assert per_card < cd.CARD_MAX and engine.docs.resident_bytes() < 500 * 60
