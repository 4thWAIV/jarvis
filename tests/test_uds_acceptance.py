"""The UDS acceptance checks (PYTHON-DESIGN.md section 5, Tim's approved
logical design), run against the knowledge engine through the UDS
interface (`jarvis/knowledge/uds.py`) and the in-process engine.

Round 1 of the plan (docs/planning/knowledge-engine/plan-uds.md) satisfied
paths, atomic writes, delete and history; round 2 (content-and-search)
satisfies the rest: vectors (a deterministic fake embedder stands in for
the model), full text, rights and AI proposals. One test per check, named
by its number.
"""
from __future__ import annotations

import time

import pytest

from jarvis.knowledge import embedder as emb
from jarvis.knowledge.uds import (Caller, Conflict, Denied, Link, Query, Stream, Transaction,
                                  in_process)
from tests.settings_fixtures import hold_setting

TIM = Caller("tim")
ANA = Caller("ana")                     # a staffing manager: given the CVs' data, not always their content
ANA_AI = Caller("ana", agent="helper", model="sonnet")
ALL = {Stream.ENTRY, Stream.DATA, Stream.CONTENT, Stream.SECTIONS}
CV = "# Experience\nLed the data platform.\n## Certifications\nSOC 2 Type II auditor."


@pytest.fixture
def opened(tmp_path, monkeypatch):
    # These checks are about indexing, so indexing is on, as an installation
    # with no saved Settings choice and JARVIS_KNOWLEDGE set has it.
    hold_setting("knowledge_enabled", "1")
    monkeypatch.setattr(emb, "RUNNER", lambda: emb.FakeEmbedder(32))
    with in_process(tmp_path / "engine") as found:
        yield found


@pytest.fixture
def uds(opened):
    return opened[1]


def _embedded(opened) -> None:
    assert opened[0].vectors.wait_idle(10)


def test_paths_one_path_per_item(uds):
    """Every item has one path, the key to all of its streams; its type is
    the path's suffix and a folder (no suffix) has no content."""
    item = uds.put(TIM, "/members/u-42/cv/current.pdf", data={"title": "CV"},
                   content={"text": CV, "ref": "jref:1:n:folder_file:f/cv.pdf"})
    assert item.path == "/members/u-42/cv/current.pdf"
    assert item.kind == "pdf"
    assert item.entry["parent"] == "/members/u-42/cv"
    assert item.entry["ancestors"] == ["/members", "/members/u-42", "/members/u-42/cv"]
    assert item.content["ref"] == "jref:1:n:folder_file:f/cv.pdf"
    assert [s.heading for s in item.sections] == ["Experience", "Certifications"]
    folder = uds.put(TIM, "/members/u-42", data={"name": "Ana"})
    assert folder.kind == "folder"
    with pytest.raises(ValueError, match="folder has no content"):
        uds.put(TIM, "/members/u-43", data={}, content={"text": "x"})
    for bad in ("members/x", "/a//b", "/a/../b", "/a/", "/"):
        with pytest.raises(ValueError):
            uds.put(TIM, bad, data={})


def test_check_1_counted_once(uds, opened):
    """N items with content count N, with embedding coverage as embedded and
    waiting; an item with many sections is still one; a changed content
    waits again, and its old vectors never answer for its new text."""
    long = "\n".join(f"# Part {i}\nText of part {i}." for i in range(30))
    uds.put(TIM, "/cv/a.md", data={"title": "A"}, content={"text": CV})
    uds.put(TIM, "/cv/long.md", data={"title": "Long"}, content={"text": long})
    uds.put(TIM, "/people/ana.person", data={"name": "Ana"})        # data only: nothing to embed
    _embedded(opened)
    counts = uds.count(TIM)
    assert (counts.items, counts.embedded, counts.waiting) == (3, 2, 0)
    assert uds.get(TIM, "/cv/long.md", {Stream.VECTORS}).vectors == {"state": "embedded", "sections": 30}
    opened[0].vectors.set_paused(True)
    uds.put(TIM, "/cv/a.md", data={"title": "A"}, content={"text": "# Gardening\nRoses and tulips."})
    counts = uds.count(TIM, under="/cv")
    assert (counts.items, counts.embedded, counts.waiting) == (2, 1, 1)
    assert uds.get(TIM, "/cv/a.md", {Stream.VECTORS}).vectors["state"] == "waiting"
    assert "/cv/a.md" not in [h.path for h in uds.search(TIM, Query(meaning="SOC 2 Type II auditor"))]
    opened[0].vectors.set_paused(False)
    _embedded(opened)
    assert uds.count(TIM).embedded == 2
    assert uds.search(TIM, Query(meaning="roses tulips gardening", limit=1))[0].path == "/cv/a.md"


def test_check_2_one_hit_per_item(uds, opened):
    """One hit for an item matched by vector, keyword and field, all three in
    matched_by, with the sections where it matched."""
    uds.put(TIM, "/cv/a.md", data={"title": "A", "lang": "es"}, content={"text": CV})
    uds.put(TIM, "/cv/b.md", data={"title": "B", "lang": "en"},
            content={"text": "# Cooking\nBread, soup and SOC 2 notes."})
    uds.put(TIM, "/cv/c.md", data={"title": "C", "lang": "es"}, content={"text": "# Travel\nTrains."})
    _embedded(opened)
    hits = uds.search(TIM, Query(meaning="SOC 2 Type II auditor certifications", words="auditor SOC",
                                 where={"lang": "es"}))
    assert [h.path for h in hits].count("/cv/a.md") == 1
    top = hits[0]
    assert top.path == "/cv/a.md"
    assert top.matched_by == frozenset({"vector", "fulltext", "data"})
    assert 1 in top.sections                          # the Certifications section
    assert "/cv/b.md" not in [h.path for h in hits]   # a keyword hit outside the condition


def test_check_3_atomic_put(uds, tmp_path):
    """Entry, data, content and sections of one put land in one commit: one
    log number, readable together, and a refused transaction leaves none of
    its writes (vectors follow the write, Tim's answer `vectors-follow-writes`)."""
    done = uds.transaction(TIM, Transaction()
                           .put("/cv/a.md", {"title": "A"}, content={"text": CV})
                           .put("/cv/b.md", {"title": "B"}, content={"text": "b"}))
    a = uds.get(TIM, "/cv/a.md", ALL)
    b = uds.get(TIM, "/cv/b.md", ALL)
    assert a.lsn == b.lsn == done["lsn"]
    assert a.data == {"title": "A"} and a.content["text"] == CV and len(a.sections) == 2
    with pytest.raises(Conflict):
        uds.transaction(TIM, Transaction()
                        .put("/cv/c.md", {"title": "C"}, content={"text": "c"})
                        .put("/cv/a.md", {"title": "A2"}, expect_version=7))
    assert uds.get(TIM, "/cv/c.md") is None
    assert uds.get(TIM, "/cv/a.md").data == {"title": "A"}


def test_check_4_delete_removes_all_streams(uds, opened):
    uds.put(TIM, "/cv/a.md", data={"title": "A", "lang": "es"}, content={"text": CV})
    uds.put(TIM, "/cv/b.md", data={"title": "B", "lang": "es"}, content={"text": "b"})
    uds.put(TIM, "/people/ana.person", data={"cv": "/cv/a.md"})
    _embedded(opened)
    before = uds.count(TIM).items
    uds.delete(TIM, "/cv/a.md")
    assert uds.count(TIM).items == before - 1
    assert uds.get(TIM, "/cv/a.md", ALL) is None
    assert [i.path for i in uds.scan(TIM, "/")] == ["/cv/b.md", "/people/ana.person"]
    assert [i.path for i in uds.list(TIM, "/", where=[["lang", "=", "es"]]).items] == ["/cv/b.md"]
    assert uds.links(TIM, "/people/ana.person") == []
    assert uds.search(TIM, Query(where={"title": "A"})) == []
    for q in (Query(phrase="SOC 2 Type II"), Query(words="auditor"), Query(meaning=CV)):
        assert "/cv/a.md" not in [h.path for h in uds.search(TIM, q)]


def test_check_5_separate_rights(uds, opened):
    """A data-only reader gets data and nothing of content, sections, vectors
    or full text, and those reads look like the item is missing; a path it
    has no right to at all looks missing in every call."""
    uds.put(TIM, "/cv/a.md", data={"title": "CV", "skills": ["sql"]},
            content={"text": CV, "ref": "jref:1:n:folder_file:f/cv.md"})
    uds.put(TIM, "/private/p.md", data={"title": "P"}, content={"text": CV})
    uds.set_rights(TIM, "/cv", [{"person": "ana", "data": True, "content": False}])
    _embedded(opened)
    seen = uds.get(ANA, "/cv/a.md", ALL | {Stream.VECTORS})
    assert seen.data == {"title": "CV", "skills": ["sql"]}
    assert seen.content is None and seen.sections is None and seen.vectors is None
    assert seen.entry["content"] is None              # not even its reference or hash
    for q in (Query(phrase="SOC 2 Type II"), Query(words="auditor"), Query(meaning="data platform")):
        assert uds.search(ANA, q) == []
        assert {h.path for h in uds.search(TIM, q)} == {"/cv/a.md", "/private/p.md"}
    counts = uds.count(ANA)
    assert (counts.items, counts.embedded, counts.waiting) == (1, 0, 0)
    assert uds.get(ANA, "/private/p.md", ALL) is None
    assert uds.get(ANA, "/private/p.md") == uds.get(ANA, "/private/missing.md") is None
    assert uds.history(ANA, "/private/p.md") == uds.history(ANA, "/private/missing.md") == []
    assert [i.path for i in uds.scan(ANA, "/")] == ["/cv/a.md"]
    with pytest.raises(Denied):
        uds.put(ANA, "/cv/a.md", data={"title": "mine"})
    with pytest.raises(Denied):
        uds.set_rights(ANA, "/cv", [{"person": "ana", "data": True, "content": True}])


def test_check_6_exact_phrase(uds):
    """A phrase search finds every readable item holding the phrase, and only
    those, with the sections it is in."""
    uds.put(TIM, "/cv/a.md", data={}, content={"text": CV})
    uds.put(TIM, "/cv/b.md", data={}, content={"text": "# Notes\nType II of SOC 2, said backwards."})
    uds.put(TIM, "/cv/c.md", data={}, content={"text": "# Audit\nShe holds a soc 2 type ii report."})
    uds.put(TIM, "/cv/d.md", data={}, content={"text": "# Other\nSOC 2\n\nType II, across a paragraph."})
    uds.put(TIM, "/private/e.md", data={}, content={"text": "# Audit\nSOC 2 Type II, privately."})
    uds.put(TIM, "/cv/f.md", data={"note": "SOC 2 Type II"})       # in its data, not its content
    uds.set_rights(TIM, "/cv", [{"person": "ana", "data": True, "content": True}])
    tim = {h.path: h for h in uds.search(TIM, Query(phrase="SOC 2 Type II"))}
    assert set(tim) == {"/cv/a.md", "/cv/c.md", "/cv/d.md", "/private/e.md"}
    assert tim["/cv/a.md"].sections == (1,) and tim["/cv/a.md"].matched_by == frozenset({"fulltext"})
    ana = {h.path for h in uds.search(ANA, Query(phrase="SOC 2 Type II"))}
    assert ana == {"/cv/a.md", "/cv/c.md", "/cv/d.md"}
    assert {h.path for h in uds.search(TIM, Query(phrase="SOC 2 Type II", under="/cv"))} == \
        {"/cv/a.md", "/cv/c.md", "/cv/d.md"}
    assert [h.path for h in uds.search(TIM, Query(phrase="Type II of SOC 2"))] == ["/cv/b.md"]


def test_check_7_links_are_gated(uds):
    """No link whose other end the caller cannot read: references, derived
    links and the change feed alike."""
    uds.put(TIM, "/cv/a.md", data={"title": "A"})
    uds.put(TIM, "/private/salary.md", data={"title": "pay"})
    uds.put(TIM, "/cv/ana.person", data={"cv": "/cv/a.md", "pay": "/private/salary.md"})
    uds.transaction(TIM, Transaction().link("/cv/ana.person", "mentions", "/private/salary.md", model="rules"))
    uds.set_rights(TIM, "/cv", [{"person": "ana", "data": True, "content": True}])
    assert {lk.target for lk in uds.links(TIM, "/cv/ana.person", direction="out")} == \
        {"/cv/a.md", "/private/salary.md"}
    assert {(lk.kind, lk.target) for lk in uds.links(ANA, "/cv/ana.person", direction="out")} == \
        {("cv", "/cv/a.md")}
    assert uds.links(ANA, "/private/salary.md") == []
    assert uds.search(ANA, Query(linked_to="/private/salary.md")) == []
    assert {h.path for h in uds.search(ANA, Query(linked_to="/cv/a.md"))} == {"/cv/ana.person"}
    feed = uds.changes(ANA, 0)["changes"]
    assert feed and all(i["path"].startswith("/cv/") for c in feed for i in c["items"])
    assert all(k["to"].startswith("/cv/") for c in feed for k in c.get("links") or [])


def test_check_8_hidden_fields(uds):
    """A where filter on a hidden field matches nothing; the field is gone
    from what the reader gets, and from counts by it."""
    uds.put(TIM, "/cv/a.md", data={"title": "A", "salary": 100, "pay": {"base": 90}})
    uds.put(TIM, "/cv/b.md", data={"title": "B", "salary": 200, "pay": {"base": 180}})
    uds.set_rights(TIM, "/cv", [{"person": "ana", "data": True, "content": True,
                                 "hidden": ["salary", "pay.base"]}])
    assert [i.path for i in uds.list(TIM, "/", where=[["salary", "=", 100]]).items] == ["/cv/a.md"]
    assert uds.list(ANA, "/", where=[["salary", "=", 100]]).items == ()
    assert uds.list(ANA, "/", where=[["salary", ">", 0]]).items == ()
    assert uds.list(ANA, "/", where=[["pay.base", "=", 90]]).items == ()
    assert uds.search(ANA, Query(where={"salary": 100})) == []
    assert uds.count(ANA, where=[["salary", "=", 100]]).items == 0
    assert uds.get(ANA, "/cv/a.md").data == {"title": "A", "pay": {}}
    assert uds.count(ANA, group_by="salary").groups == ()
    assert [i.path for i in uds.list(ANA, "/", where=[["title", "=", "B"]]).items] == ["/cv/b.md"]


def test_check_9_ai_rights(uds, opened):
    """An AI caller never sees more than its person; its writes and its
    proposals carry the agent, the person, the model and the time, and its
    links wait for the owner's yes."""
    uds.put(TIM, "/cv/a.md", data={"title": "A"}, content={"text": CV})
    uds.put(TIM, "/cv/b.md", data={"title": "B"}, content={"text": "# Cooking\nBread."})
    uds.put(TIM, "/private/p.md", data={"title": "P"}, content={"text": CV})
    uds.set_rights(TIM, "/cv", [{"person": "ana", "data": True, "content": True, "write": True}])
    _embedded(opened)
    for q in (Query(meaning="SOC 2 auditor"), Query(words="auditor"), Query(phrase="Type II"),
              Query(where={"title": "P"}), Query()):
        own = {h.path for h in uds.search(ANA, q)}
        assert {h.path for h in uds.search(ANA_AI, q)} <= own
        assert "/private/p.md" not in own
    assert uds.get(ANA_AI, "/private/p.md") is None
    uds.put(ANA_AI, "/cv/notes.md", data={"title": "notes"}, content={"text": "# Seen\nA fits."})
    written = uds.history(TIM, "/cv/notes.md")[0]
    assert (written.by, written.agent, written.model) == ("ana", "helper", "sonnet")
    before = time.time()
    proposal = uds.propose_link(ANA_AI, Link("/cv/notes.md", "about", "/cv/a.md", "derived"))
    assert (proposal.person, proposal.agent, proposal.model, proposal.state) == \
        ("ana", "helper", "sonnet", "proposed")
    assert proposal.at >= before - 1
    assert uds.links(TIM, "/cv/a.md", origins=["derived"]) == []
    with pytest.raises(Conflict):                      # an item its person cannot read looks missing
        uds.propose_link(ANA_AI, Link("/cv/notes.md", "about", "/private/p.md", "derived"))
    with pytest.raises(Denied):
        uds.answer_proposal(ANA_AI, proposal.path, "accept")
    uds.answer_proposal(TIM, proposal.path, "accept")
    link = uds.links(TIM, "/cv/a.md", origins=["derived"])[0]
    assert (link.source, link.model) == ("/cv/notes.md", "sonnet") and link.at is not None
    assert [p.state for p in uds.proposals(TIM, state="accepted")] == ["accepted"]


def test_check_10_history_get_at(uds):
    """get_at returns the whole item, all streams, as it stood then; a
    delete is a moment in the history too."""
    uds.put(TIM, "/cv/a.md", data={"title": "first"}, content={"text": "# One\nold"})
    time.sleep(0.01)
    then = time.time()
    time.sleep(0.01)
    uds.put(TIM, "/cv/a.md", data={"title": "second"}, content={"text": "# Two\nnew"})
    time.sleep(0.01)
    between = time.time()
    time.sleep(0.01)
    uds.delete(TIM, "/cv/a.md")
    old = uds.get_at(TIM, "/cv/a.md", then, ALL)
    assert old.version == 1 and old.data == {"title": "first"}
    assert old.content["text"] == "# One\nold" and old.sections[0].heading == "One"
    assert uds.get_at(TIM, "/cv/a.md", between, ALL).data == {"title": "second"}
    assert uds.get_at(TIM, "/cv/a.md", time.time()) is None
    assert uds.get_at(TIM, "/cv/a.md", 0) is None
    assert [v.op for v in uds.history(TIM, "/cv/a.md")] == ["delete", "put", "put"]
