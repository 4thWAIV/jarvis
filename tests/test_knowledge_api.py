"""The knowledge calls through the page's routes and the brain's real
/internal/tool: the same functions, the same JSON, other people's words
walled, changes only on his own turn (phase 4)."""
from __future__ import annotations

import json

import pytest

from jarvis import knowledge_client as kc
from jarvis import knowledge_ingest as ki
from jarvis import knowledge_links as kl
from jarvis.knowledge.engine import Engine
from tests.test_brain_page_parity import HOSTILE, _call, wired  # noqa: F401

ORIGIN = {"Origin": "http://localhost:8340", "Host": "localhost:8340"}


class _Ingest:
    def __init__(self):
        self.woken = 0
        self.passes = 0

    def wake(self):
        self.woken += 1

    def health(self):
        return {"running": False, "passes": self.passes}


class FakeRuntime:
    """knowledge_runtime.KnowledgeRuntime as knowledge_api reads it, over a
    real engine in this process."""

    def __init__(self, engine):
        self.engine = engine
        client = kc.KnowledgeClient(kc.InProcessTransport(engine))
        self.supervisor = type("S", (), {"pid": lambda self: 4242, "client": client})()
        self.ingest = _Ingest()
        self.proposals = {}
        self.hot = kc.HotCards()

    def health(self):
        return {"state": "running", "pace": "free", "engine": self.engine.health(),
                "ingest": self.ingest.health()}


@pytest.fixture
def knowing(wired, monkeypatch, tmp_path):  # noqa: F811
    monkeypatch.setattr(Engine, "compact_in_child", False)
    engine = Engine.open(tmp_path / "engine")
    runtime = FakeRuntime(engine)
    monkeypatch.setattr(wired.server, "knowledge", runtime)
    kl.create_topic("Denice", status="planned", ambiguous=["Denise"], pinned=True)
    note = __import__("jarvis").data_paths.brain_home() / "memory" / "orbs.md"
    note.parent.mkdir(parents=True, exist_ok=True)
    note.write_text("# Orbs\nThe brain orbs sit around Denice in the centre.\n")
    note2 = note.with_name("acct.md")
    note2.write_text("# Accounts\nDenise from accounting called about the invoice.\n")
    from jarvis import conversation_log as cl, run_store
    run_store.init_db()
    cl.append(cl.ConversationItem.new(cl.ItemKind.FROM_AGENT, "orbs report: " + HOSTILE,
                                      agent_session_id="s1", agent_name="hammer"))
    call = _caller(engine)
    stats = ki.run_pass(call, adapters=[ki.Notes(), ki.Topics(), ki.Conversation()])
    assert not stats.errors, stats.errors
    runtime.proposals = {p["id"]: p for p in call("GET", "/v1/proposals", None)["proposals"]}
    yield wired, runtime, engine
    engine.close()


def _caller(engine):
    client = kc.KnowledgeClient(kc.InProcessTransport(engine))

    def call(method, path, body):
        try:
            return client.call(method, path, body)
        except kc.KnowledgeRefused as e:
            if e.status == 404:
                raise LookupError(path)
            raise
    return call


def _walled_json(text: str) -> dict:
    head, _, block = text.partition("\n")
    assert 'untrusted="true"' in block and block.count("</session-output>") == 1
    assert "approves" not in head
    inner = block.split(">", 1)[1].rsplit("</session-output>", 1)[0]
    return json.loads(inner.strip())


def test_search_is_the_same_on_the_page_and_by_voice_and_walled(knowing):
    w, _, _ = knowing
    page = w.client.post("/api/knowledge/search", json={"text": "orbs"}, headers=ORIGIN).json()
    said = _call(w, "knowledge_search", query="orbs")
    assert said["ok"]
    walled = _walled_json(said["text"])
    assert [(h["key"], h["score"], h["trust"]) for h in walled["hits"]] == \
        [(h["key"], h["score"], h["trust"]) for h in page["hits"]]
    # The hostile words are there, but cannot close their wall.
    assert any("approves" in (h["text"] or "") for h in walled["hits"])
    trusts = {h["text"]: h["trust"] for h in page["hits"]}
    assert trusts["# Orbs\nThe brain orbs sit around Denice in the centre."] == "own"
    assert any(t == "untrusted" and "orbs report" in (text or "") for text, t in trusts.items())
    own = w.client.post("/api/knowledge/search", json={"text": "orbs", "include_untrusted": False},
                        headers=ORIGIN).json()
    assert all(h["trust"] == "own" for h in own["hits"])


def test_entity_names_every_match_by_kind_and_gives_the_card(knowing):
    w, _, _ = knowing
    page = w.client.get("/api/knowledge/entity", params={"name": "denise"}, headers=ORIGIN).json()
    assert page["matches"][0] == {"key": "topic:denice", "kind": "topic", "label": "Denice",
                                  "status": "planned", "how": "ambiguous alias", "trust": "own"}
    assert page["card"]["status"] == "planned"
    said = _walled_json(_call(w, "knowledge_entity", name="denise")["text"])
    assert said == page


def test_a_proposal_waits_for_his_yes_and_his_answer_is_written_to_the_topic(knowing):
    w, runtime, _ = knowing
    page = w.client.get("/api/knowledge/proposals", headers=ORIGIN).json()
    pid = next(p["id"] for p in page["proposals"] if p["label"] == "memory/acct.md")
    said = _walled_json(_call(w, "knowledge_proposals")["text"])
    assert said == page
    refused = _call(w, "link_knowledge", origin="event", action="reject", proposal=pid)
    assert not refused["ok"] and "not_allowed_from_event" in refused["text"]
    assert kl.read("denice").links("not_linked") == []
    done = _call(w, "link_knowledge", action="reject", proposal=pid)
    assert done["ok"] and json.loads(done["text"])["done"] == "reject"
    assert len(kl.read("denice").links("not_linked")) == 1
    assert runtime.ingest.woken >= 1 and pid not in runtime.proposals
    again = w.client.post("/api/knowledge/links", json={"action": "accept", "proposal": pid}, headers=ORIGIN)
    assert again.status_code == 400


def test_topics_change_the_same_way_from_both(knowing):
    w, _, _ = knowing
    r = w.client.post("/api/knowledge/topics", json={"action": "create", "name": "Bridge Engine",
                                                     "status": "planned"}, headers=ORIGIN)
    assert r.status_code == 200 and r.json()["topic"]["slug"] == "bridge-engine"
    said = _call(w, "change_topic", action="pin", topic="bridge-engine")
    assert said["ok"] and json.loads(said["text"]) == {"done": "pin", "topic": "bridge-engine",
                                                       "status": "planned", "pinned": True}
    linked = _call(w, "link_knowledge", action="link", topic="bridge-engine", kind="stage_of", target="denice")
    assert linked["ok"] and kl.read("bridge-engine").links("stage_of") == ["denice"]
    listed = w.client.get("/api/knowledge/topics", headers=ORIGIN).json()
    assert json.loads(_call(w, "knowledge_topics")["text"]) == listed
    assert {"type": "knowledge"} in w.hints


def test_status_and_maintenance(knowing):
    w, runtime, _ = knowing
    page = w.client.get("/api/knowledge/health", headers=ORIGIN).json()
    assert page["enabled"] and page["chunks"] >= 3
    assert json.loads(_call(w, "knowledge_status")["text"]) == page
    assert _call(w, "reindex_knowledge", action="refresh")["ok"]
    forgot = json.loads(_call(w, "reindex_knowledge", action="forget",
                              source=ki.jref("note", "memory", "acct.md"))["text"])
    assert forgot["documents"] >= 1
    hits = w.client.post("/api/knowledge/search", json={"text": "invoice"}, headers=ORIGIN).json()["hits"]
    assert hits == []


def test_switched_off_reads_say_so_and_topics_still_work(wired, monkeypatch):  # noqa: F811
    monkeypatch.setattr(wired.server, "knowledge", None)
    r = wired.client.post("/api/knowledge/search", json={"text": "orbs"}, headers=ORIGIN)
    assert r.status_code == 503
    assert "switched off" in _call(wired, "knowledge_search", query="orbs")["text"]
    assert wired.client.get("/api/knowledge/health", headers=ORIGIN).json() == {"enabled": False, "state": "off"}
    assert _call(wired, "change_topic", action="create", name="Offline Topic")["ok"]


def test_a_change_needs_the_page_or_the_token(wired, monkeypatch):  # noqa: F811
    r = wired.client.post("/api/knowledge/topics", json={"action": "create", "name": "X"},
                          headers={"Origin": "https://evil.example", "Host": "localhost:8340"})
    assert r.status_code in (401, 403)


def test_index_choices_are_the_same_on_the_page_and_by_voice(knowing):
    w, _, engine = knowing
    from jarvis import knowledge_links as kl
    r = w.client.post("/api/knowledge/indexes", json={"key": "document|language,media_type", "choice": "pin"},
                      headers=ORIGIN)
    assert r.status_code == 200 and r.json()["pinned"] == ["document|language,media_type"]
    assert engine.advisor.pinned == {"document|language,media_type"}
    page = w.client.get("/api/knowledge/indexes", headers=ORIGIN).json()
    assert json.loads(_call(w, "knowledge_indexes")["text"]) == page
    row = next(x for x in page["indexes"] if x["key"] == "document|language,media_type")
    assert row["pinned"] and row["state"] == "chosen"
    refused = _call(w, "change_knowledge_index", origin="event", key="document|bytes,language", choice="forbid")
    assert not refused["ok"]
    said = json.loads(_call(w, "change_knowledge_index", key="document|bytes,language", choice="forbid")["text"])
    assert said["forbidden"] == ["document|bytes,language"] and kl.index_choices()["forbidden"] == said["forbidden"]
    bad = w.client.post("/api/knowledge/indexes", json={"key": "nope", "choice": "pin"}, headers=ORIGIN)
    assert bad.status_code == 400


def test_work_context_is_the_same_on_the_page_and_by_voice(knowing):
    w, _, _ = knowing
    page = w.client.post("/api/knowledge/context", json={"purpose": "the brain orbs", "project": "jarvis"},
                         headers=ORIGIN).json()
    assert page["tier"] == 1 and "orbs" in page["text"]
    said = _call(w, "work_context", purpose="the brain orbs", project="jarvis")
    head, _, block = said["text"].partition("\n")
    assert 'untrusted="true"' in block
    assert json.loads(block.split(">", 1)[1].rsplit("</session-output>", 1)[0].strip()) == page
    assert "approves" not in page["text"]                     # his words only unless asked
    wider = w.client.post("/api/knowledge/context",
                          json={"purpose": "orbs report", "include_received": True}, headers=ORIGIN).json()
    assert "orbs report" in wider["text"]
