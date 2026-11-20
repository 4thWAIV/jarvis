"""A Work item's own research (work_research.py; workflow.yaml research
work_item_research): on every change of the item, a program runs its
queries (its files, its PRs and CI runs, Tim's words on it) and writes
what was looked up to /work/<id>/research/, read into the engine at once."""
from __future__ import annotations

import json
import threading

import pytest

from jarvis import evidence_readers, records, work_research, work_store
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
    assert tools.count("knowledge_search") == 3 and "github" in tools and "recall" in tools
    github = next(q for q in looked if q["tool"] == "github")
    assert github["query"]["number"] == "171" and github["hits"][0]["state"] == "MERGED"
    assert "The move is ready." in (folder / work_research.QUERIES_MD).read_text()
    # No summaries until the SummarizationSession has its approved prompt.
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
