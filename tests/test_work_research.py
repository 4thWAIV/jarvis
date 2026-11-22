"""A Work item's own research (work_research.py; workflow.yaml research
work_item_research): on every change of the item, a program runs its
queries (its files, its PRs and CI runs, Tim's words on it) and writes
what was looked up to /work/<id>/research/, read into the engine at once."""
from __future__ import annotations

import json
import threading

import pytest

from jarvis import evidence_readers, history_read, records, sessions, work_research, work_store
from jarvis.knowledge import embedder as emb
from jarvis.knowledge.uds import Caller, Stream
from tests.settings_fixtures import hold_setting

TIM = Caller("tim")


@pytest.fixture(autouse=True)
def indexing(monkeypatch):
    hold_setting("knowledge_enabled", "1")
    monkeypatch.setattr(emb, "RUNNER", lambda: emb.FakeEmbedder(32))
    monkeypatch.setattr(evidence_readers, "_gh", lambda args: (True, json.dumps(
        {"number": 171, "title": "Work items in folders", "state": "MERGED"})))
    monkeypatch.setattr(history_read, "search", lambda text: history_read.Hits())


def _item(tmp_path):
    source = tmp_path / "speech"
    source.mkdir()
    item = work_store.create("Work items in folders", "folder", str(source))
    work_store.record(work_store.WorkEvent(item.id, "piece", "Built the move.", {
        "key": "move", "title": "The move", "who": {"kind": "tim"}, "state": "working",
        "evidence": ["https://github.com/4thWAIV/jarvis/pull/171"]}))
    return item.id


def test_what_was_looked_up_is_written_and_indexed(tmp_path):
    item_id = _item(tmp_path)
    asked = []

    def search(text, under, kinds):
        asked.append((text, under, kinds))
        return {"hits": [{"key": f"{under or ''}/notes.md", "source": "notes.md", "score": 0.9,
                          "why": ["words"], "text": "The move is ready."}]}
    folder = work_research.refresh(item_id, search, lambda path: "fold-1")
    assert folder == work_research.folder_of(item_id)
    assert ("Work items in folders", work_store.folder_of(item_id), None) in asked
    assert ("Work items in folders", "/folders/fold-1", None) in asked
    assert ("Work items in folders", None, ["turn"]) in asked
    looked = json.loads((folder / work_research.QUERIES).read_text())
    tools = [q["tool"] for q in looked]
    assert ("Work items in folders", None, None) in asked          # the whole record
    assert tools.count("knowledge_search") == 4 and "github" in tools and "recall" in tools
    assert {"conversation_history", "messages_to_him", "search_history"} <= set(tools)
    github = next(q for q in looked if q["tool"] == "github")
    assert github["query"]["number"] == "171" and github["hits"][0]["state"] == "MERGED"
    assert "The move is ready." in (folder / work_research.QUERIES_MD).read_text()
    # No SummarizationSession given: no summaries.
    assert not (folder / work_research.SUMMARIES).exists()
    research = f"{work_store.folder_of(item_id)}/research"
    item = records.uds().get(TIM, f"{research}/queries.md", {Stream.DATA})
    assert item is not None and item.data["state"] == "indexed"


def test_a_query_that_cannot_be_run_says_so(tmp_path):
    item_id = _item(tmp_path)

    def search(text, under, kinds):
        raise RuntimeError("the knowledge engine is switched off")
    folder = work_research.refresh(item_id, search)
    looked = json.loads((folder / work_research.QUERIES).read_text())
    assert all("switched off" in q["error"] for q in looked if q["tool"] == "knowledge_search")


def test_every_change_of_a_work_item_makes_its_research_again(tmp_path):
    item_id = _item(tmp_path)
    done = threading.Event()
    refresher = work_research.REFRESHER
    refresher.changed({item_id})                    # not started: nothing kept
    assert not refresher._waiting

    def search(text, under, kinds):
        done.set()
        return {"hits": []}
    work_store.on_changed(refresher.changed)        # as the server registers it
    refresher.start(search)
    try:
        work_store.record(work_store.WorkEvent(item_id, "update", "Moved on.", {}))
        assert done.wait(10)
    finally:
        refresher.stop()


def test_the_brain_s_own_past_is_searched_then_each_turn_found_read(tmp_path, monkeypatch):
    item_id = _item(tmp_path)
    hits = history_read.Hits()
    hits.lines = ["transcript-abc:12: 2026-11-18: user: the folders move",
                  "transcript-abc:12: 2026-11-18: user: the folders move again"]
    hits.found = [("transcript-abc", 12), ("transcript-abc", 12)]
    monkeypatch.setattr(history_read, "search", lambda text: hits)
    read = []

    class Window:
        text, first, last = "user: move the Work items into folders", 10, 14
    monkeypatch.setattr(history_read, "resolve", lambda source: (source, tmp_path / source))
    monkeypatch.setattr(history_read, "read_window",
                        lambda path, around: read.append((path.name, around)) or Window())
    folder = work_research.refresh(item_id, lambda text, under, kinds: {"hits": []})
    looked = json.loads((folder / work_research.QUERIES).read_text())
    searched = next(q for q in looked if q["tool"] == "search_history")
    assert searched["query"] == "Work items in folders"
    assert [h["path"] for h in searched["hits"]] == ["transcript-abc:12"] * 2
    assert read == [("transcript-abc", 12)]                     # each turn found read once
    window = next(q for q in looked if q["tool"] == "read_history")
    assert window["hits"] == [{"path": "transcript-abc:10-14",
                               "text": "user: move the Work items into folders"}]


def test_the_summaries_are_written_and_only_cited_paths_kept(tmp_path):
    item_id = _item(tmp_path)
    given = {}

    class Summarizer:
        def __init__(self, folder):
            given["folder"] = folder

        def summarize(self, sources, purpose):
            given["sources"], given["purpose"] = sources, purpose
            said = [sessions.Summary(sources[0].id, "The move is ready.", ["/work/notes.md"]),
                    sessions.Summary(sources[0].id, "Invented.", ["/nowhere.md"])]
            return sessions.cited_in_sources(said, sources)

    def search(text, under, kinds):
        return {"hits": [{"key": "/work/notes.md", "text": "The move is ready."}]}
    folder = work_research.refresh(item_id, search, summarizer=Summarizer)
    assert given["folder"] == folder and item_id in given["purpose"]
    assert '<hit path="/work/notes.md">The move is ready.</hit>' in given["sources"][0].text
    assert all(s.text for s in given["sources"])                 # only queries that found something
    written = (folder / work_research.SUMMARIES).read_text()
    assert "The move is ready." in written and "Rests on: /work/notes.md" in written
    assert "Invented." not in written
    research = f"{work_store.folder_of(item_id)}/research"
    item = records.uds().get(TIM, f"{research}/summaries.md", {Stream.DATA})
    assert item is not None and item.data["state"] == "indexed"


def test_a_summarization_that_cannot_be_made_leaves_what_was_looked_up(tmp_path):
    item_id = _item(tmp_path)
    def cannot(folder):
        raise sessions.SessionFailed("no model found")
    folder = work_research.refresh(item_id, lambda text, under, kinds: {"hits": []},
                                   summarizer=cannot)
    assert (folder / work_research.QUERIES).exists()
    assert not (folder / work_research.SUMMARIES).exists()


def test_searches_run_at_once_and_keep_their_order():
    """Every call starts before any finishes (a barrier all must reach), and
    the queries come back in the order the calls were given."""
    gate = threading.Barrier(3, timeout=10)

    def call(n):
        def run():
            gate.wait()
            return [{"tool": "t", "query": str(n), "hits": []}]
        return run
    looked = work_research.at_once([call(1), call(2), call(3)])
    assert [q["query"] for q in looked] == ["1", "2", "3"]
    assert work_research.at_once([]) == []


def test_the_key_searches_for_some_words(tmp_path):
    asked = []

    def search(text, under, kinds):
        asked.append((text, under, kinds))
        return {"hits": []}
    looked = work_research.at_once(work_research.key_searches("the move", search,
                                                               ["/work/w1", None]))
    assert sorted(asked, key=str) == sorted([("the move", "/work/w1", None),
                                             ("the move", None, None),
                                             ("the move", None, ["turn"])], key=str)
    assert {q["tool"] for q in looked} == {"knowledge_search", "recall", "search_history"}


def test_the_engine_search_finds_a_record_by_its_words(tmp_path):
    item_id = _item(tmp_path)
    found = work_research.engine_search(60.0)("folders", None, None)
    hit = next(h for h in found["hits"] if h["key"] == f"{work_store.folder_of(item_id)}/"
               f"{item_id}.work")
    assert hit["why"] and "folders" in (hit["text"] or "")
