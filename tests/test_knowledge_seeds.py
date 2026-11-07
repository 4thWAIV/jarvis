"""The Denice seed: offered, never applied without his yes (I9)."""
from __future__ import annotations

import json

import pytest

from jarvis import knowledge_links as kl
from jarvis import knowledge_seeds as ks
from tests.test_brain_page_parity import _call, wired  # noqa: F401
from tests.test_knowledge_api import ORIGIN


def test_the_denice_seed_quotes_the_readme_and_roadmap_word_for_word():
    seed = ks.denice()
    assert seed["id"] == "denice"
    by = {t["slug"]: t for t in seed["topics"]}
    assert list(by) == ["denice", "prototype-engine", "parris-island-engine", "bridge-engine", "proteus-engine"]
    assert by["denice"]["status"] == "planned" and by["denice"]["ambiguous"] == ["Denise"] and by["denice"]["pinned"]
    assert by["prototype-engine"]["status"] == "implemented"
    assert all(by[s]["status"] == "planned" for s in ("parris-island-engine", "bridge-engine", "proteus-engine"))
    assert by["bridge-engine"]["ambiguous"] == ["Bridge"]
    assert by["parris-island-engine"]["links"] == {"stage_of": ["denice"], "precedes": ["bridge-engine"]}
    readme = (ks.data_paths.SOURCE_ROOT / "README.md").read_text()
    roadmap = (ks.data_paths.SOURCE_ROOT / "docs" / "roadmap.md").read_text()
    for t in seed["topics"]:
        for line in t["body"].splitlines():
            if line.startswith('> "'):
                quoted = line[3:line.index('" (')]
                assert quoted in " ".join(readme.split()) or quoted in roadmap, quoted


def test_nothing_is_created_until_he_says_yes():
    assert ks.open_seeds()[0]["id"] == "denice"
    assert kl.topics() == []
    done = ks.answer("denice", "accept")
    assert done["created"] == ["denice", "prototype-engine", "parris-island-engine", "bridge-engine", "proteus-engine"]
    assert kl.read("bridge-engine").links("stage_of") == ["denice"]
    assert kl.read("bridge-engine").links("precedes") == ["proteus-engine"]
    assert ks.open_seeds() == []
    with pytest.raises(ks.SeedError, match="already accepted"):
        ks.answer("denice", "accept")


def test_his_own_topic_is_never_overwritten():
    kl.create_topic("Denice", status="active", body="# Denice\n\nMy own words.\n")
    done = ks.answer("denice", "accept")
    assert "denice" not in done["created"]
    t = kl.read("denice")
    assert t.status == "active" and "My own words." in t.body
    assert kl.read("parris-island-engine").links("stage_of") == ["denice"]


def test_a_declined_seed_is_never_offered_again():
    ks.answer("denice", "decline")
    assert kl.declined_seeds() == ["denice"] and ks.open_seeds() == []
    assert kl.topics() == []
    with pytest.raises(ks.SeedError):
        ks.answer("denice", "accept")


def test_seeds_are_the_same_on_the_page_and_by_voice(wired):  # noqa: F811
    page = wired.client.get("/api/knowledge/seeds", headers=ORIGIN).json()
    joined, n = "", 1
    while True:
        text = _call(wired, "knowledge_seeds", page=n)["text"]
        head, _, chunk = text.partition("\n")
        joined += chunk
        if "again with page" not in head:
            break
        n += 1
    assert n > 1 and json.loads(joined) == page
    assert page["seeds"][0]["status"] == "open"
    refused = _call(wired, "change_knowledge_seed", origin="event", seed="denice", action="accept")
    assert not refused["ok"] and kl.topics() == []
    said = json.loads(_call(wired, "change_knowledge_seed", seed="denice", action="accept")["text"])
    assert said["created"][0] == "denice"
    again = wired.client.post("/api/knowledge/seeds/denice", json={"action": "decline"}, headers=ORIGIN)
    assert again.status_code == 400
    assert {"type": "knowledge"} in wired.hints


def test_an_open_seed_is_a_silent_attention_item(wired, monkeypatch):  # noqa: F811
    seen = []
    monkeypatch.setattr(wired.server, "_attend", lambda *a, **k: seen.append((a, k)))
    wired.server._offer_knowledge_seeds()
    (args, kw), = seen
    assert args[:3] == ("knowledge_seed", "denice", "proposed") and kw["audible"] is False
