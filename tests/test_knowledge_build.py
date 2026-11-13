"""The initial index build (Tim's yes, 2026-11-12): it finishes, it is
paced by its own share of one core, it goes on across restarts, and it says
how far it is.

- Order: the records that hold people's words, newest first; then the
  brain's notes; then JARVIS's own other files; then registered folders'
  files, most recently changed first.
- Pace: after each batch the build rests in proportion to the CPU the batch
  took (`lanes.BUILD_SHARE` of one core), whatever the Mac's load.
- Resumable: a long item is committed a part at a time with the build's
  progress, so a restart goes on from the part it reached.
- Visible: `build_health` says what waits, what is done and the time left.
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


def test_the_queue_keeps_one_entry_an_item_and_counts_its_texts():
    q = vec.BuildQueue()
    q.put(1, "/a", "s1", (3, -1.0), 5)
    q.put(2, "/b", "s2", (0, -1.0), 1)
    q.put(1, "/a", "s3", (3, -2.0), 7)             # changed again before it was reached
    assert len(q) == 2 and q.texts == 8
    assert q.pop() == (2, "/b", "s2", 1)
    assert q.pop() == (1, "/a", "s3", 7)            # only its newest content
    assert q.pop() is None and q.texts == 0


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
    assert len(rests) == 2 and all(r >= 0.015 for r in rests)     # 8 texts, two batches of four
    assert took >= 3 * sum(rests) * 0.9                             # rested three times the CPU


def test_a_long_item_goes_on_where_it_stopped_after_a_restart(tmp_path, monkeypatch):
    fake = Noting()
    monkeypatch.setattr(emb, "RUNNER", lambda: fake)
    monkeypatch.setattr(vec, "COMMIT_EVERY", 4)
    stop_after = {"batches": 2}
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
        assert _until(lambda: engine.vectors.build["texts"] == 8)
        assert engine.vectors.build_health()["items_waiting"] == 1
        done_before = len(first.passages)
        gate.set()
    assert done_before == 8
    second = Noting()
    monkeypatch.setattr(emb, "RUNNER", lambda: second)
    with in_process(tmp_path / "engine") as (engine, uds):
        assert engine.vectors.build["texts"] >= 8               # kept in the engine
        assert _until(lambda: engine.vectors.build_health()["state"] == "done")
        health = engine.vectors.build_health()
        assert health["items_embedded"] == 1 and health["items_waiting"] == 0
    # 12 texts in all: the 8 made before the restart were not made again.
    assert len(second.passages) <= 12 - 8 + 4


def test_the_build_says_what_waits_and_the_time_left(tmp_path, monkeypatch):
    fake = Noting()
    monkeypatch.setattr(emb, "RUNNER", lambda: fake)
    with in_process(tmp_path / "engine") as (engine, uds):
        engine.vectors.set_paused(True)
        tx = Transaction()
        for i in range(3):
            tx.put(f"/folders/f/{i}.md", {"title": str(i)}, content=_sections(4, f"delta{i}"))
        uds.transaction(TIM, tx, change="test")
        held = engine.vectors.build_health()
        assert held["state"] == "paused" and held["items_waiting"] == 3 and held["texts_waiting"] == 15
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
