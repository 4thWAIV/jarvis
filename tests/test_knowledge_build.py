"""The initial index build (Tim's yes, 2026-11-12): it finishes, it is
paced by its own share of one core, it goes on across restarts, and it says
how far it is.

- Order: the records that hold people's words, newest first; then the
  brain's notes; then JARVIS's own other files; then registered folders'
  files, most recently changed first.
- Pace: after each batch the build rests in proportion to the CPU the batch
  took (`lanes.BUILD_SHARE` of one core), whatever the Mac's load.
- Coverage in passes (Tim, 2026-11-12: nothing capped): pass one gives
  every item its whole-item vector; each pass after it gives every item its
  next band of sections, until every section of every item has its vector;
  a changed item goes back to pass one.
- Resumable: the build commits a few texts at a time with its progress, so
  a restart goes on from the last commit.
- Visible: `build_health` says items and sections done of their totals,
  the pass, what waits and the time left.
"""
from __future__ import annotations

import threading
import time

import pytest

from jarvis.knowledge import embedder as emb
from jarvis.knowledge import lanes, vectors as vec
from jarvis.knowledge.uds import Caller, Transaction, in_process

TIM = Caller("tim")
WAIT_SEC = 20.0


@pytest.fixture(autouse=True)
def indexing_enabled(monkeypatch):
    monkeypatch.setenv("JARVIS_KNOWLEDGE", "1")


class Noting(emb.FakeEmbedder):
    """Fake vectors, noting each passage text it was given, in order."""

    def __init__(self) -> None:
        super().__init__(32)
        self.passages: list[str] = []

    def embed(self, texts, purpose):
        if purpose == "passage":
            self.passages.extend(texts)
        return super().embed(texts, purpose)


def _until(test, sec: float = WAIT_SEC) -> bool:
    end = time.monotonic() + sec
    while time.monotonic() < end:
        if test():
            return True
        time.sleep(0.02)
    return test()


def _sections(n: int, word: str) -> dict:
    return {"text": " ".join(f"{word} {i}" for i in range(n)),
            "sections": [{"heading": f"part {i}", "text": f"{word} section {i}"} for i in range(n)]}


def test_the_build_order_is_records_newest_first_then_notes_then_files_newest_first():
    rank = vec.build_rank
    record_old = {"_key": "/work/a.work", "_type": "work", "_at": 100.0}
    record_new = {"_key": "/work/b.work", "_type": "work", "_at": 200.0}
    note = {"_key": "/jarvis/brain/memory/x.md", "_type": "md", "_content": {"sha256": "x"}, "_at": 300.0}
    own = {"_key": "/jarvis/answers/p/a.md", "_type": "md", "_content": {"sha256": "y"}, "_at": 300.0}
    file_old = {"_key": "/folders/f/old.md", "_type": "md", "_content": {"sha256": "z"}, "_at": 900.0,
                "mtime": 10.0}
    file_new = {"_key": "/folders/f/new.md", "_type": "md", "_content": {"sha256": "w"}, "_at": 50.0,
                "mtime": 20.0}
    ordered = sorted([file_old, own, note, record_old, file_new, record_new], key=rank)
    assert [d["_key"] for d in ordered] == [
        "/work/b.work", "/work/a.work", "/jarvis/brain/memory/x.md", "/jarvis/answers/p/a.md",
        "/folders/f/new.md", "/folders/f/old.md"]


def _job(o, sha, rank, stage, sections=40):
    return vec.Job(o, f"/{o}", sha, rank, stage, sections)


def test_the_queue_keeps_one_entry_an_item_whole_vectors_first_then_bands():
    q = vec.BuildQueue()
    q.put(_job(1, "s1", (3, -1.0), 2))
    q.put(_job(2, "s2", (3, -2.0), 1))
    q.put(_job(3, "s3", (0, -1.0), 1))
    q.put(_job(4, "s4", (4, -1.0), 0))             # a whole-item vector goes before any band
    q.put(_job(1, "s5", (3, -1.0), 0))             # changed: back to pass one, newest content only
    assert len(q) == 4 and q.stage() == 0
    assert [(j.o, j.sha, j.stage) for j in iter(q.pop, None)] == [
        (1, "s5", 0), (4, "s4", 0), (3, "s3", 1), (2, "s2", 1)]
    q.put(_job(5, "new", (3, -1.0), 0))
    q.again(_job(5, "old", (3, -1.0), 3))          # a stage done on old content does not replace it
    assert q.pop().sha == "new" and q.pop() is None


def test_a_job_embeds_its_whole_vector_or_one_band_of_sections():
    band = vec.SECTION_BAND
    assert list(vec.band_props(0, 40)) == [vec.WHOLE]
    assert list(vec.band_props(1, 40)) == list(range(1, band + 1))
    assert list(vec.band_props(3, 2 * band + 3)) == list(range(2 * band + 1, 2 * band + 4))
    space = vec.Space("s#items")
    assert vec.next_stage(space, 7, 40) == 0
    space.props[7] = {vec.WHOLE}
    space.where[(7, vec.WHOLE)] = 1
    assert vec.next_stage(space, 7, 0) is None and vec.next_stage(space, 7, 40) == 1
    space.props[7] |= set(range(1, band + 1))
    space.where.update({(7, p): 1 for p in space.props[7]})
    assert vec.next_stage(space, 7, 40) == 2
    space.props[7] |= set(range(band + 1, 41))
    space.where.update({(7, p): 1 for p in space.props[7]})
    assert vec.next_stage(space, 7, 40) is None


def test_the_engine_embeds_in_the_build_order(tmp_path, monkeypatch):
    fake = Noting()
    monkeypatch.setattr(emb, "RUNNER", lambda: fake)
    with in_process(tmp_path / "engine") as (engine, uds):
        engine.vectors.set_paused(True)
        tx = Transaction()
        tx.put("/folders/f/old.md", {"title": "old", "mtime": 10.0}, content={"text": "folderold words"})
        tx.put("/folders/f/new.md", {"title": "new", "mtime": 20.0}, content={"text": "foldernew words"})
        tx.put("/jarvis/brain/memory/n.md", {"title": "n"}, content={"text": "brainnote words"})
        uds.transaction(TIM, tx, change="test")
        uds.transaction(TIM, Transaction().put("/work/w.work", {"title": "recordwork words"}, content=None),
                        change="test")
        engine.vectors.set_paused(False)
        assert _until(lambda: engine.vectors.build_health()["state"] == "done")
        firsts = [next(i for i, t in enumerate(fake.passages) if word in t)
                  for word in ("recordwork", "brainnote", "foldernew", "folderold")]
        assert firsts == sorted(firsts), fake.passages


def test_the_build_rests_for_its_share_of_one_core(tmp_path, monkeypatch):
    """Each batch is followed by a rest in proportion to the CPU it took:
    at half a core, a batch that took 0.1 s of CPU is followed by 0.1 s."""
    assert lanes.rest_for(0.1, 0.5) == pytest.approx(0.1)
    assert lanes.rest_for(0.1, 0.25) == pytest.approx(0.3)
    assert lanes.rest_for(-1.0) == 0.0

    class Busy(emb.FakeEmbedder):
        def embed(self, texts, purpose):
            end = time.thread_time() + 0.02            # 20 ms of this thread's CPU
            while time.thread_time() < end:
                pass
            return super().embed(texts, purpose)
    busy = Busy(32)
    monkeypatch.setattr(emb, "RUNNER", lambda: busy)
    monkeypatch.setattr(lanes, "BUILD_SHARE", 0.25)
    with in_process(tmp_path / "engine") as (engine, uds):
        rests = []
        real = lanes.rest_for
        monkeypatch.setattr(lanes, "rest_for", lambda used, share=None: rests.append(used) or real(used, 0.25))
        began = time.monotonic()
        uds.transaction(TIM, Transaction().put("/folders/f/a.md", {"title": "a"},
                                               content=_sections(7, "alpha")), change="test")
        assert _until(lambda: engine.vectors.build_health()["state"] == "done")
        took = time.monotonic() - began
    # The whole-item vector in pass one, then 7 sections: a batch of four, one of three.
    assert len(rests) == 3 and all(r >= 0.015 for r in rests)
    assert took >= 3 * sum(rests) * 0.9                             # rested three times the CPU


def test_sections_are_embedded_breadth_first_across_items(tmp_path, monkeypatch):
    """Pass one gives every item its whole-item vector; then each pass gives
    every item its next band of sections, so coverage rises evenly instead
    of one dense file finishing before the next begins."""
    fake = Noting()
    monkeypatch.setattr(emb, "RUNNER", lambda: fake)
    monkeypatch.setattr(vec, "SECTION_BAND", 4)
    monkeypatch.setattr(vec, "COMMIT_EVERY", 8)
    with in_process(tmp_path / "engine") as (engine, uds):
        engine.vectors.set_paused(True)
        tx = Transaction()
        tx.put("/folders/f/new.md", {"title": "new", "mtime": 20.0}, content=_sections(10, "newer"))
        tx.put("/folders/f/old.md", {"title": "old", "mtime": 10.0}, content=_sections(6, "older"))
        uds.transaction(TIM, tx, change="test")
        engine.vectors.set_paused(False)
        assert _until(lambda: engine.vectors.build_health()["state"] == "done")
        health = engine.vectors.build_health()
    order = []
    for text in fake.passages:
        who = "N" if "newer" in text else "O"
        part = text.rsplit("section ", 1)[-1] if "section" in text else "whole"
        order.append(f"{who}{'' if part == 'whole' else int(part) // 4 + 1}")
    # Pass one, then band 1 of both, band 2 of both, band 3 of the one left.
    assert order == ["N", "O"] + ["N1"] * 4 + ["O1"] * 4 + ["N2"] * 4 + ["O2"] * 2 + ["N3"] * 2, order
    assert health["items_embedded"] == health["items_total"] == 2
    assert health["sections_embedded"] == health["sections_total"] == 16
    assert health["texts_waiting"] == 0 and health["pass"] is None


def test_no_item_s_sections_are_capped(tmp_path, monkeypatch):
    """Every section of every item gets its vector, however many it has
    (once 2,000 was the most an item's sections had)."""
    monkeypatch.setattr(emb, "RUNNER", lambda: emb.FakeEmbedder(8))
    monkeypatch.setattr(vec, "SECTION_BAND", 512)
    monkeypatch.setattr(vec, "COMMIT_EVERY", 1024)
    with in_process(tmp_path / "engine") as (engine, uds):
        uds.transaction(TIM, Transaction().put("/folders/f/sheet.csv", {"title": "sheet"},
                                               content=_sections(2050, "row")), change="test")
        assert _until(lambda: engine.vectors.build_health()["state"] == "done", 60)
        health = engine.vectors.build_health()
        space = engine.vectors.spaces[engine.vectors.item_space_id]
        o = engine.vectors._sizes["/folders/f/sheet.csv"][0]
        assert len(space.props[o]) == 2051
        assert health["sections_embedded"] == health["sections_total"] == 2050 and health["passes"] == 6


def test_a_changed_item_goes_back_to_pass_one(tmp_path, monkeypatch):
    """A change of an item's content deletes its vectors in the same write;
    its whole-item vector is made again before any other item's next band."""
    fake = Noting()
    monkeypatch.setattr(emb, "RUNNER", lambda: fake)
    monkeypatch.setattr(vec, "SECTION_BAND", 2)
    monkeypatch.setattr(vec, "COMMIT_EVERY", 2)
    gate = threading.Event()

    class Gated(Noting):
        def embed(self, texts, purpose):
            if purpose == "passage" and any("section 2" in t for t in texts):
                gate.wait(10)                       # held at the second band
            return super().embed(texts, purpose)
    fake = Gated()
    monkeypatch.setattr(emb, "RUNNER", lambda: fake)
    with in_process(tmp_path / "engine") as (engine, uds):
        tx = Transaction()
        tx.put("/folders/f/a.md", {"title": "a", "mtime": 20.0}, content=_sections(6, "alpha"))
        tx.put("/folders/f/b.md", {"title": "b", "mtime": 10.0}, content=_sections(6, "beta"))
        uds.transaction(TIM, tx, change="test")
        assert _until(lambda: engine.vectors.build_health()["sections_embedded"] >= 4)
        held = engine.vectors.build_health()
        assert held["items_embedded"] == 2 and held["pass"] == 3
        uds.transaction(TIM, Transaction().put("/folders/f/b.md", {"title": "b", "mtime": 30.0},
                                               content=_sections(3, "betanew")), change="test")
        changed = engine.vectors.build_health()
        assert changed["items_embedded"] == 1 and changed["sections_total"] == 9 and changed["pass"] == 1
        before = len(fake.passages)
        gate.set()
        assert _until(lambda: engine.vectors.build_health()["state"] == "done")
        after = fake.passages[before:]
        health = engine.vectors.build_health()
    first_new = next(i for i, t in enumerate(after) if "betanew" in t)
    assert "section" not in after[first_new]                      # its whole-item vector first
    assert health["sections_embedded"] == health["sections_total"] == 9
    assert not any("beta section" in t for t in after)            # nothing made for the old text


def test_the_build_goes_on_from_its_last_commit_after_a_restart(tmp_path, monkeypatch):
    monkeypatch.setattr(vec, "SECTION_BAND", 4)
    monkeypatch.setattr(vec, "COMMIT_EVERY", 4)
    stop_after = {"batches": 3}
    gate = threading.Event()

    class Stopping(Noting):
        def embed(self, texts, purpose):
            if purpose == "passage":
                stop_after["batches"] -= 1
                if stop_after["batches"] < 0:
                    gate.wait(5)                         # held here until the engine closes
            return super().embed(texts, purpose)
    first = Stopping()
    monkeypatch.setattr(emb, "RUNNER", lambda: first)
    with in_process(tmp_path / "engine") as (engine, uds):
        uds.transaction(TIM, Transaction().put("/folders/f/long.md", {"title": "long"},
                                               content=_sections(11, "gamma")), change="test")
        # The whole-item vector, then bands 1 and 2: 9 texts in three commits.
        assert _until(lambda: engine.vectors.build["texts"] == 9)
        health = engine.vectors.build_health()
        assert health["items_embedded"] == 1 and health["sections_embedded"] == 8 and health["pass"] == 4
        done_before = len(first.passages)
        gate.set()
    assert done_before == 9
    second = Noting()
    monkeypatch.setattr(emb, "RUNNER", lambda: second)
    with in_process(tmp_path / "engine") as (engine, uds):
        assert engine.vectors.build["texts"] >= 9               # kept in the engine
        assert _until(lambda: engine.vectors.build_health()["state"] == "done")
        health = engine.vectors.build_health()
        assert health["sections_embedded"] == health["sections_total"] == 11
    # Nothing committed before the restart was made again: after it, only
    # the last band (sections 9 to 11), if the held batch had not finished.
    assert all(int(t.rsplit("gamma section ", 1)[-1]) >= 8 for t in second.passages), second.passages
    assert len(second.passages) <= 3


def test_the_build_says_what_waits_and_the_time_left(tmp_path, monkeypatch):
    fake = Noting()
    monkeypatch.setattr(emb, "RUNNER", lambda: fake)
    monkeypatch.setattr(vec, "SECTION_BAND", 2)
    with in_process(tmp_path / "engine") as (engine, uds):
        engine.vectors.set_paused(True)
        tx = Transaction()
        for i in range(3):
            tx.put(f"/folders/f/{i}.md", {"title": str(i)}, content=_sections(4, f"delta{i}"))
        uds.transaction(TIM, tx, change="test")
        held = engine.vectors.build_health()
        assert held["state"] == "paused" and held["items_waiting"] == 3 and held["texts_waiting"] == 15
        assert (held["items_total"], held["sections_total"], held["sections_waiting"]) == (3, 12, 12)
        assert held["pass"] == 1 and held["passes"] == 3
        assert held["seconds_left"] is None                     # nothing measured yet
        engine.vectors.build = {"texts": 100, "items": 10, "cpu_sec": 5.0, "busy_sec": 20.0}
        measured = engine.vectors.build_health()
        assert measured["texts_per_sec"] == pytest.approx(5.0)   # 100 texts in 20 s of building
        assert measured["seconds_left"] == 3
        assert engine.health()["vectors"]["build"]["items_waiting"] == 3


# ── what Settings, the Knowledge view and the brain read ────────────────

def _runtime(build: dict, *, pace="free", knowledge=True, files=None):
    from types import SimpleNamespace
    health = {"state": "running", "pace": pace, "knowledge": knowledge,
              "content": {"waiting": files} if files is not None else {},
              "engine": {"vectors": {"build": build}}}
    return SimpleNamespace(health=lambda: health)


@pytest.mark.parametrize("engine_state, pace, knowledge, shown", [
    ("building", "free", True, "building"),
    ("paused", "hold", True, "held"),             # the conversation needs the machine
    ("paused", "free", False, "paused"),          # paused in Settings
    ("building", "hold", False, "paused"),
    ("done", "hold", True, "done"),
    ("off", "free", True, "off"),
])
def test_the_build_s_state_is_said_once_for_every_reader(engine_state, pace, knowledge, shown):
    from jarvis import knowledge_api as ka
    found = {"state": engine_state, "items_embedded": 5, "items_waiting": 2, "texts_waiting": 9,
             "texts_per_sec": 1.5, "seconds_left": 6}
    ctx = ka.Context(lambda: _runtime(found, pace=pace, knowledge=knowledge, files=3))
    got = ka.build(ctx)
    assert got["state"] == shown and got["files_waiting"] == 3 and got["seconds_left"] == 6
    assert ka.build(ka.Context(lambda: None)) is None
    assert ka.build(ka.Context(lambda: _runtime(None))) is None


def test_a_search_while_the_index_is_built_says_it_is_partial(monkeypatch):
    from jarvis import knowledge_api as ka
    found = {"state": "building", "items_embedded": 5, "items_waiting": 2, "texts_waiting": 9,
             "texts_per_sec": None, "seconds_left": None}
    monkeypatch.setattr(ka, "_item_hits", lambda *a, **k: [])
    building = ka.search(ka.Context(lambda: _runtime(found)), "orbs")
    assert building["partial"] is True and building["building"]["items_waiting"] == 2
    done = ka.search(ka.Context(lambda: _runtime({**found, "state": "done", "items_waiting": 0})), "orbs")
    assert done["partial"] is False and done["building"] is None


def test_a_busy_engine_answers_its_health_from_what_its_health_thread_keeps(tmp_path, monkeypatch):
    """The server restarts an engine that does not answer its health check
    within a second; a busy engine answers from what its own health thread
    worked out, and works it out itself only when that is stale."""
    import threading as _threading
    from jarvis.knowledge import serve
    monkeypatch.setattr(emb, "RUNNER", lambda: None)
    with in_process(tmp_path / "engine") as (engine, uds):
        monkeypatch.setattr(serve, "HEALTH_EVERY_SEC", 0.01)
        stopping = _threading.Event()
        serve._keep_health(engine, stopping)
        assert _until(lambda: engine.held_health is not None)
        monkeypatch.setattr(engine, "health", lambda: pytest.fail("worked out on the request"))
        assert serve.dispatch(engine, "GET", "/v1/health").payload["state"] == "ok"
        stopping.set()
        time.sleep(0.05)
        engine.held_health = (time.monotonic() - serve.HEALTH_STALE_SEC - 1, {"state": "ok"})
        monkeypatch.setattr(engine, "health", lambda: {"state": "live"})
        assert serve.dispatch(engine, "GET", "/v1/health").payload == {"state": "live"}


def test_the_time_left_follows_the_rate_of_the_last_commits(tmp_path, monkeypatch):
    """Short records go fast and spreadsheet sections slowly: once the
    build has commits of its own, their rate is the one the time left uses."""
    monkeypatch.setattr(emb, "RUNNER", lambda: Noting())
    with in_process(tmp_path / "engine") as (engine, uds):
        engine.vectors.set_paused(True)
        uds.transaction(TIM, Transaction().put("/folders/f/a.md", {"title": "a"},
                                               content=_sections(9, "eta")), change="test")
        engine.vectors.build = {"texts": 1000, "items": 900, "cpu_sec": 10.0, "busy_sec": 100.0}
        assert engine.vectors.build_health()["texts_per_sec"] == pytest.approx(10.0)
        engine.vectors._recent.extend([(64, 32.0), (64, 32.0)])
        health = engine.vectors.build_health()
        assert health["texts_per_sec"] == pytest.approx(2.0) and health["seconds_left"] == 5
