"""Records found by meaning (Tim's answer to "Records and meaning search",
option a): the records that hold people's words (Work items, messages,
the conversation's turns, packages, projects, runs, workflows, threads,
language documents) get a whole-record vector and one per part, made in
the background after the write; JARVIS's own records (attention updates,
settings) get none. A record counts as waiting until it is embedded, an
old vector never answers for new text, many changes before the background
lane reaches a record cost one embedding, and a record is one hit in the
one search however many of its parts match."""
from __future__ import annotations

import os
import time
from pathlib import Path

import pytest
from tests.settings_fixtures import hold_setting

@pytest.fixture(autouse=True)
def indexing_enabled(monkeypatch):
    # These tests exercise indexing, with the legacy installation input.
    # Settings persistence and live enable/disable have their own tests.
    hold_setting("knowledge_enabled", "1")


from jarvis.knowledge import embedder as emb
from jarvis.knowledge import items as item_texts
from jarvis.knowledge.uds import Caller, Query, Stream, Transaction, in_process

TIM = Caller("tim")
WAIT_SEC = 20.0

WORK = {"id": "w1", "title": "Audio icons", "state": "working",
        "latest_update": "Drawing the headset for the whole audio system.",
        "pieces": [{"key": "headset", "note": "The headset turns every sound off and on at once."},
                   {"key": "speaker", "note": "The speaker mutes what JARVIS says, nothing else."}],
        "decisions": [{"key": "colour", "question": "Should the icons be drawn in amber or in white?",
                       "answer": "White, like the rest of the bar."}],
        "events": [{"kind": "update", "summary": "A version's own change log, not its words."}]}

RECORDS = {
    "/work/w1.work": WORK,
    "/agents/a1/messages/m1.message": {"text": "Please look at the build failure again.",
                                       "reply": "The build fails because a test needs the network."},
    "/conversation/t1.turn": {"kind": "heard", "text": "remind me what the weather will be tomorrow"},
    "/packages/p1.package": {"title": "Voice logs and search", "purpose": "Decide how voice logs are kept."},
    "/projects/p1.project": {"name": "JARVIS", "summary": "A voice assistant for the whole Mac."},
    "/runs/r1.run": {"prompt": "Write the release notes for the update.", "summary": "Release notes written."},
    "/workflows/f1.workflow": {"name": "Round four build", "summary": "Rotating the voice logs now."},
    "/people/1/threads/t1.thread": {"subject": "Lunch on Friday at noon"},
    "/language/d1.langdoc": {"text": "Una frase en español para el modelo."},
    # JARVIS's own facts: found by fields and words never, by meaning never.
    "/attention/a1.attention": {"facts": "An agent answered your message about the build."},
    "/people/1/settings/voice.settings": {"note": "Capture sounds are on for this user."},
}
EMBEDDED = {p for p in RECORDS if not p.endswith((".attention", ".settings"))}


@pytest.fixture
def fake(monkeypatch):
    found = emb.FakeEmbedder(32)
    monkeypatch.setattr(emb, "RUNNER", lambda: found)
    return found


@pytest.fixture
def engine(tmp_path, fake):
    with in_process(tmp_path / "engine") as opened:
        yield opened


def _write(uds, records: dict) -> None:
    tx = Transaction()
    for path, data in records.items():
        tx.put(path, data, content=None)
    uds.transaction(TIM, tx, change="test")


def _state(uds, path: str):
    return (uds.get(TIM, path, {Stream.VECTORS}).vectors or {}).get("state")


def _passages(fake) -> int:
    return sum(n for purpose, n in fake.calls if purpose == "passage")


def test_the_records_that_hold_people_s_words_are_embedded_and_no_others(engine):
    e, uds = engine
    _write(uds, RECORDS)
    assert e.vectors.wait_idle(WAIT_SEC)
    states = {p: _state(uds, p) for p in RECORDS}
    assert {p for p, s in states.items() if s == "embedded"} == EMBEDDED
    assert all(states[p] is None for p in set(RECORDS) - EMBEDDED)
    counted = uds.count(TIM, under="/")
    assert (counted.embedded, counted.waiting) == (len(EMBEDDED), 0)


def test_a_record_s_parts_are_its_sections_and_its_change_log_is_not_its_words():
    doc = {"_type": "work", **WORK}
    texts = item_texts.record_texts(doc)
    assert len(texts) == 1 + 4                        # the whole, its own fields, two pieces, one decision
    assert texts[0].startswith("Audio icons\n") and "headset" in texts[0]
    assert any("pieces headset" in t and "every sound off" in t for t in texts[1:])
    assert any("decisions colour" in t and "White, like the rest" in t for t in texts[1:])
    assert not any("change log" in t for t in texts)
    # The engine gives keys back sorted: the same record gives the same texts.
    reordered = {"_type": "work", **dict(reversed(list(WORK.items()))),
                 "pieces": [dict(reversed(list(p.items()))) for p in WORK["pieces"]]}
    assert item_texts.record_texts(reordered) == texts
    # One part, short: the whole record only. A long one: sections of it.
    assert len(item_texts.record_texts({"_type": "turn", "text": "a few words said"})) == 1
    long = " ".join(f"word{i}" for i in range(1500))
    texts = item_texts.record_texts({"_type": "message", "text": long})
    assert len(texts) > 2 and all(len(t) <= item_texts.WHOLE_TEXT_MAX for t in texts)
    assert item_texts.record_texts({"_type": "attention", "facts": "JARVIS's own words here"}) == []


def test_a_record_waits_until_it_is_embedded_and_an_old_vector_never_answers(engine, fake):
    e, uds = engine
    _write(uds, {"/work/w1.work": WORK})
    assert e.vectors.wait_idle(WAIT_SEC)
    assert _state(uds, "/work/w1.work") == "embedded"
    e.vectors.set_paused(True)                        # the pace holds: he is talking
    changed = {**WORK, "latest_update": "Now about a lighthouse keeper and his lamp, nothing else.",
               "pieces": [], "decisions": []}
    _write(uds, {"/work/w1.work": changed})
    assert _state(uds, "/work/w1.work") == "waiting"
    assert uds.count(TIM, under="/work").waiting == 1
    # The old text's vectors are gone with the write: meaning finds nothing.
    hits = uds.search(TIM, Query(meaning="headset turns every sound off"))
    assert "/work/w1.work" not in {h.path for h in hits}
    before = _passages(fake)
    time.sleep(0.3)
    assert _passages(fake) == before                  # nothing embedded while held
    e.vectors.set_paused(False)
    assert e.vectors.wait_idle(WAIT_SEC)
    assert _state(uds, "/work/w1.work") == "embedded"
    hits = uds.search(TIM, Query(meaning="lighthouse keeper and his lamp"))
    assert hits and hits[0].path == "/work/w1.work"


def test_many_changes_of_a_busy_record_cost_one_embedding(engine, fake):
    e, uds = engine
    e.vectors.set_paused(True)
    for n in range(10):
        _write(uds, {"/conversation/t1.turn": {"kind": "heard", "text": f"change number {n} of the turn"}})
    before = _passages(fake)
    e.vectors.set_paused(False)
    assert e.vectors.wait_idle(WAIT_SEC)
    assert _passages(fake) - before == 1              # the newest text, once
    hits = uds.search(TIM, Query(meaning="change number 9 of the turn"))
    assert hits[0].path == "/conversation/t1.turn"


def test_a_record_is_one_hit_found_by_meaning_words_phrase_and_fields(engine):
    e, uds = engine
    _write(uds, RECORDS)
    assert e.vectors.wait_idle(WAIT_SEC)
    hits = uds.search(TIM, Query(meaning="the headset turns every sound off", words="headset sound"))
    paths = [h.path for h in hits]
    assert paths[0] == "/work/w1.work" and paths.count("/work/w1.work") == 1
    assert {"vector", "fulltext"} <= set(hits[0].matched_by)
    assert hits[0].sections                           # where in the record it matched: its parts
    hits = uds.search(TIM, Query(meaning="speaker mutes", phrase="speaker mutes what JARVIS says",
                                 where={"state": "working"}))
    assert [h.path for h in hits] == ["/work/w1.work"]
    # JARVIS's own records are never found by meaning.
    hits = uds.search(TIM, Query(meaning="an agent answered your message about the build"))
    assert "/attention/a1.attention" not in {h.path for h in hits}


def test_the_embedding_runs_in_the_background_lane(engine, monkeypatch):
    """Every record is embedded by the vector index's one thread, which runs
    at background priority (lanes.background) and stops while the pace
    holds (set_paused), as for his files."""
    from jarvis.knowledge import lanes
    e, uds = engine
    assert e.vectors._worker is not None and e.vectors._worker.name == "knowledge-vectors"
    seen = []
    real = lanes.background
    monkeypatch.setattr(lanes, "background", lambda: (seen.append(True), real())[1])
    e.vectors._stop = True
    with e.vectors._todo_cond:
        e.vectors._todo_cond.notify_all()
    e.vectors._worker.join(5)
    e.vectors._stop = False
    e.vectors._start()
    _write(uds, {"/projects/p1.project": RECORDS["/projects/p1.project"]})
    assert e.vectors.wait_idle(WAIT_SEC) and seen == [True]
    assert _state(uds, "/projects/p1.project") == "embedded"


def test_records_written_before_records_had_vectors_are_embedded_at_start(tmp_path, fake, monkeypatch):
    """A record from before (no `_words` on it) is queued when the engine
    opens, counted, and its first new write keeps or drops its vectors by
    whether its words changed."""
    real = item_texts.record_words_summary
    monkeypatch.setattr(item_texts, "record_words_summary", lambda texts: None)
    with in_process(tmp_path / "engine") as (e, uds):
        _write(uds, {"/work/w1.work": WORK})
        assert e.docs.get("/work/w1.work").get("_words") is None              # as written before
    monkeypatch.setattr(item_texts, "record_words_summary", real)
    with in_process(tmp_path / "engine") as (e, uds):
        assert e.vectors.wait_idle(WAIT_SEC)
        assert _state(uds, "/work/w1.work") == "embedded"
        assert (uds.count(TIM, under="/work").embedded, uds.count(TIM, under="/work").waiting) == (1, 0)
        _write(uds, {"/work/w1.work": {**WORK, "state": "verifying"}})     # the same words
        assert _state(uds, "/work/w1.work") == "embedded"
        e.vectors.set_paused(True)
        _write(uds, {"/work/w1.work": {**WORK, "title": "Speaker icons"}})
        assert _state(uds, "/work/w1.work") == "waiting"


# --- the real model: a record found by a paraphrase ------------------------------

@pytest.mark.knowledge_engine
def test_a_work_item_is_found_by_a_paraphrase_with_the_real_model(tmp_path, monkeypatch):
    from jarvis import embedding_models as em
    from jarvis.knowledge.e5_onnx import E5Onnx
    given = os.environ.get("JARVIS_E5_MODEL_DIR")
    found = Path(given) if given else em.model_dir()
    if em.model_problem(found) is not None or em.runtime_problem() is not None:
        pytest.skip("the embedding model or onnxruntime is not here")
    model = E5Onnx(found, em.DEFAULT.space)
    monkeypatch.setattr(emb, "RUNNER", lambda: model)
    with in_process(tmp_path / "engine") as (e, uds):
        _write(uds, {
            "/work/w1.work": WORK,
            "/work/w2.work": {"title": "Subscription usage history", "state": "working",
                              "pieces": [{"key": "chart", "note": "A weekly chart of the plan's limits."}]},
            "/work/w3.work": {"title": "Package inbox", "state": "working",
                              "pieces": [{"key": "read", "note": "Opening a package does not mark it read."}]}})
        assert e.vectors.wait_idle(120)
        hits = uds.search(TIM, Query(meaning="the headphones picture"))
        assert hits[0].path == "/work/w1.work"
        hits = uds.search(TIM, Query(meaning="¿qué pasó con la bandeja de paquetes?"))
        assert hits[0].path == "/work/w3.work"
