"""Topics, asserted links, the linker and its proposals (phase 4; the linker
over topic and note items since UDS round 3)."""
from __future__ import annotations

import pytest

from jarvis import data_paths
from jarvis import knowledge_client as kc
from jarvis import knowledge_ingest as ki
from jarvis import knowledge_links as kl
from jarvis import knowledge_runtime as kr
from jarvis.knowledge.engine import Engine


@pytest.fixture(autouse=True)
def _in_process(monkeypatch):
    monkeypatch.setattr(Engine, "compact_in_child", False)


# --- the front matter and its one writer -----------------------------------------

def test_front_matter_round_trips():
    front = {"name": "Denice", "status": "planned", "pinned": True,
             "aliases": {"exact": ["Denice"], "ambiguous": ["Denise", "a, b: c"]},
             "relates_to": ["jarvis"], "not_linked": []}
    text = kl.render(front, "# Denice\n\n> quoted words\n")
    assert kl.parse(text) == (front, "# Denice\n\n> quoted words\n")


def test_create_change_link_and_turn_down():
    denice = kl.create_topic("Denice", status="planned", ambiguous=["Denise"], pinned=True)
    assert denice.slug == "denice" and denice.aliases("exact") == ["Denice"] and denice.pinned
    kl.create_topic("Bridge Engine", status="planned")
    with pytest.raises(kl.TopicError):
        kl.create_topic("Denice")
    kl.link("bridge-engine", "stage_of", "denice")
    assert kl.read("bridge-engine").links("stage_of") == ["denice"]
    with pytest.raises(kl.TopicError):
        kl.link("bridge-engine", "stage_of", "nowhere")
    kl.change_topic("denice", status="active", add_aliases={"exact": ["The Successor"]},
                    remove_aliases=["denise"])
    t = kl.read("denice")
    assert t.status == "active" and t.aliases("exact") == ["Denice", "The Successor"]
    assert t.aliases("ambiguous") == []
    kl.turn_down("denice", "jref:1:n:note:memory/x.md")
    assert kl.read("denice").links("not_linked") == ["jref:1:n:note:memory/x.md"]
    kl.link("denice", "about_sources", "jref:1:n:note:memory/x.md")
    t = kl.read("denice")
    assert t.links("about_sources") == ["jref:1:n:note:memory/x.md"] and t.links("not_linked") == []
    assert t.body.startswith("# Denice")


def test_a_file_changed_by_hand_is_never_overwritten():
    t = kl.create_topic("Orbs")
    path = kl.topic_path("orbs")
    path.write_text(t.text + "\nHis own line.\n")
    with pytest.raises(kl.TopicError, match="changed"):
        kl.change_topic("orbs", status="active", expect=t.text)
    kl.change_topic("orbs", status="active")
    assert "His own line." in path.read_text()


def test_a_linked_topic_file_is_not_read(tmp_path):
    outside = tmp_path / "x.md"
    outside.write_text("---\nname: Evil\n---\n")
    kl.topics_dir().mkdir(parents=True, exist_ok=True)
    (kl.topics_dir() / "evil.md").symlink_to(outside)
    assert kl.read("evil") is None and kl.topics() == []
    with pytest.raises(kl.TopicError):
        kl.read("../etc")


def test_topics_are_a_note_folder():
    from jarvis import history_read
    assert "topics" in history_read.NOTE_DIRS


# --- topics as items and the linker (UDS round 3) --------------------------------------

@pytest.fixture
def engine(tmp_path):
    e = Engine.open(tmp_path / "engine")
    yield e
    e.close()


def caller(engine):
    client = kc.KnowledgeClient(kc.InProcessTransport(engine))

    def call(method, path, body):
        try:
            return client.call(method, path, body)
        except kc.KnowledgeRefused as e:
            if e.status == 404:
                raise LookupError(path)
            raise
    return call


def note(name, text):
    p = data_paths.brain_home() / "memory" / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


def ingest(engine):
    """What runs in JARVIS: the content child reads the brain's notes and
    topic files into items, then an ingest pass runs the linker."""
    from tests.test_knowledge_ingest import index_brain
    index_brain(engine)
    stats = ki.run_pass(caller(engine), adapters=[])
    assert not stats.errors, stats.errors
    return stats


def topic_item(slug):
    return f"/jarvis/brain/topics/{slug}.md"


def mentions(engine, slug):
    found = engine.items.links({"caller": {"person": "tim"}, "path": topic_item(slug), "direction": "in",
                                "kind": "mentions"})["links"]
    return sorted(link["from"] for link in found)


def test_topics_become_nodes_with_their_authored_links(engine):
    kl.create_topic("Denice", status="planned")
    kl.create_topic("Bridge Engine", status="planned")
    kl.link("bridge-engine", "stage_of", "denice")
    ingest(engine)
    t = engine.docs.get("topic:denice")
    assert t["_status"] == "planned" and t["aliases"]["exact"] == ["Denice"]
    assert t["_source"] == topic_item("denice")
    assert [e["to"] for e in engine.graph.edges("topic:bridge-engine", "out", "stage_of")] == ["topic:denice"]
    kl.unlink("bridge-engine", "stage_of", "denice")
    ingest(engine)
    assert list(engine.graph.edges("topic:bridge-engine", "out", "stage_of")) == []
    kl.topic_path("bridge-engine").unlink()
    ingest(engine)
    assert engine.docs.get("topic:bridge-engine") is None and engine.docs.get("topic:denice") is not None


def test_exact_aliases_make_mentions_and_ambiguous_ones_need_company(engine):
    kl.create_topic("Denice", status="planned", ambiguous=["Denise"])
    note("a.md", "# A\nDenice is the successor.\n\n## More\nDenise again in the same file.\n")
    note("b.md", "# B\nDenise from accounting called.\n")
    note("c.md", "# C\nThe word denicely is not the name.\n")
    ingest(engine)
    assert mentions(engine, "denice") == ["/jarvis/brain/memory/a.md"]     # not b (ambiguous alone), not c
    proposals = engine.linker.proposals
    assert [p["kind"] for p in proposals if p.get("label") == "memory/b.md"] == ["about"]
    b = next(p for p in proposals if p.get("label") == "memory/b.md")
    assert b["document"] == "/jarvis/brain/memory/b.md" and b["source"].endswith(":note:memory/b.md")
    assert (b["exact"], b["ambiguous"], b["trust"]) == (0, 1, "own")
    found = engine.items.links({"caller": {"person": "tim"}, "path": topic_item("denice"), "direction": "in",
                                "kind": "mentions"})["links"]
    assert all(link["origin"] == "derived" and link["weight"] == 0.9 and link["model"] == "linker"
               for link in found)


def test_a_turned_down_proposal_is_never_made_again_and_an_accepted_one_is_kept(engine):
    kl.create_topic("Denice", ambiguous=["Denise"])
    note("b.md", "# B\nDenise from accounting called.\n")
    note("d.md", "# D\nDenice one.\n\n## Two\nDenice two.\n")
    ingest(engine)
    by_label = {p["label"]: p for p in engine.linker.proposals}
    assert set(by_label) == {"memory/b.md", "memory/d.md"}
    kl.turn_down("denice", by_label["memory/b.md"]["source"])
    kl.link("denice", "about_sources", by_label["memory/d.md"]["source"])
    ingest(engine)
    assert engine.linker.proposals == []
    assert kl.read("denice").links("about_sources") == [by_label["memory/d.md"]["source"]]


def test_topics_sharing_a_name_are_proposed_as_the_same(engine):
    kl.create_topic("Proteus Engine", exact=["Proteus"])
    kl.create_topic("Proteus", status="unknown")
    ingest(engine)
    same = [p for p in engine.linker.proposals if p["kind"] == "same_as"]
    assert len(same) == 1 and {same[0]["topic"], same[0]["other"]} == {"topic:proteus", "topic:proteus-engine"}
    kl.link("proteus", "same_as", "proteus-engine")
    ingest(engine)
    assert not [p for p in engine.linker.proposals if p["kind"] == "same_as"]


def test_the_linker_removes_only_its_own_mentions(engine):
    from jarvis.knowledge.uds import Caller, EngineUDS, Transaction
    kl.create_topic("Denice")
    note("a.md", "# A\nDenice here.\n")
    note("e.md", "# E\nNothing named.\n")
    ingest(engine)
    assert mentions(engine, "denice") == ["/jarvis/brain/memory/a.md"]
    uds = EngineUDS(kc.InProcessTransport(engine))
    uds.transaction(Caller("tim"), Transaction().link("/jarvis/brain/memory/e.md", "mentions", topic_item("denice"),
                                                      model="someone-else"))
    note("a.md", "# A\nNo name now.\n")
    import os
    import time
    os.utime(data_paths.brain_home() / "memory" / "a.md", (time.time() + 9, time.time() + 9))
    ingest(engine)
    assert mentions(engine, "denice") == ["/jarvis/brain/memory/e.md"]     # the other model's stays


def test_the_runtime_tells_new_and_gone_proposals_once():
    seen = []
    rt = kr.KnowledgeRuntime(floor_busy=lambda: False, cpu_percent=lambda: None,
                             supervisor=kc.Supervisor(spawn=lambda: None, budget_mb=10))
    rt.proposal_listeners.append(lambda new, gone: seen.append(([p["id"] for p in new], gone)))
    rt.take_proposals([{"id": "a"}, {"id": "b"}])
    rt.take_proposals([{"id": "a"}, {"id": "b"}])
    rt.take_proposals([{"id": "b"}, {"id": "c"}])
    assert seen == [(["a", "b"], []), (["c"], ["a"])]
