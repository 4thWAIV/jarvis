"""The UDS acceptance checks (PYTHON-DESIGN.md section 5, Tim's approved
logical design), run against the knowledge engine through the UDS
interface (`jarvis/knowledge/uds.py`) and the in-process engine.

Round 1 of the plan (docs/planning/knowledge-engine/plan-uds.md) satisfies
paths, atomic writes, delete and history; the checks that need vectors,
full text, rights or AI proposals are marked for round 2 and skipped until
it builds them. One test per check, named by its number.
"""
from __future__ import annotations

import time

import pytest

from jarvis.knowledge.uds import Caller, Conflict, Query, Stream, Transaction, in_process

ROUND_2 = pytest.mark.skip(reason="UDS round 2 (content-and-search) builds this check")
TIM = Caller("tim")
ALL = {Stream.ENTRY, Stream.DATA, Stream.CONTENT, Stream.SECTIONS}
CV = "# Experience\nLed the data platform.\n## Certifications\nSOC 2 Type II auditor."


@pytest.fixture
def uds(tmp_path):
    with in_process(tmp_path / "engine") as (_engine, found):
        yield found


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


@ROUND_2
def test_check_1_counted_once(uds):
    """N items with content count N, with embedding coverage as embedded and waiting."""


@ROUND_2
def test_check_2_one_hit_per_item(uds):
    """One hit for an item matched by vector, keyword and field, all three in matched_by."""


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


def test_check_4_delete_removes_all_streams(uds):
    uds.put(TIM, "/cv/a.md", data={"title": "A", "lang": "es"}, content={"text": CV})
    uds.put(TIM, "/cv/b.md", data={"title": "B", "lang": "es"}, content={"text": "b"})
    uds.put(TIM, "/people/ana.person", data={"cv": "/cv/a.md"})
    before = uds.count(TIM).items
    uds.delete(TIM, "/cv/a.md")
    assert uds.count(TIM).items == before - 1
    assert uds.get(TIM, "/cv/a.md", ALL) is None
    assert [i.path for i in uds.scan(TIM, "/")] == ["/cv/b.md", "/people/ana.person"]
    assert [i.path for i in uds.list(TIM, "/", where=[["lang", "=", "es"]]).items] == ["/cv/b.md"]
    assert uds.links(TIM, "/people/ana.person") == []
    assert uds.search(TIM, Query(where={"title": "A"})) == []


@ROUND_2
def test_check_5_separate_rights(uds):
    """A data-only reader gets data and nothing of content, sections, vectors or full text."""


@ROUND_2
def test_check_6_exact_phrase(uds):
    """A phrase search finds every readable item holding the phrase, and only those."""


@ROUND_2
def test_check_7_links_are_gated(uds):
    """No link whose other end the caller cannot read."""


@ROUND_2
def test_check_8_hidden_fields(uds):
    """A where filter on a hidden field matches nothing."""


@ROUND_2
def test_check_9_ai_rights(uds):
    """An AI caller never sees more than its person; its proposals carry agent, person, model and time."""


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
