"""The engine's two lanes (UDS plan, "Two lanes"): record reads and writes
at normal priority and never paused; the background lane lowered on its own
threads and paused by the pace."""
from __future__ import annotations

import statistics
import sys
import threading
import time

import pytest

from jarvis.knowledge import embedder as emb
from jarvis.knowledge import lanes
from jarvis.knowledge import vecmath
from jarvis.knowledge.engine import Engine


def _in_thread(fn):
    box = {}

    def run():
        box["v"] = fn()
    t = threading.Thread(target=run)
    t.start()
    t.join(10)
    return box["v"]


def test_a_thread_is_normal_until_it_lowers_itself():
    def look():
        seen = [lanes.is_background()]
        lanes.background()
        seen.append(lanes.is_background())
        with lanes.foreground():
            seen.append(lanes.is_background())
            with lanes.foreground():
                seen.append(lanes.is_background())
            seen.append(lanes.is_background())
        seen.append(lanes.is_background())
        return seen
    assert _in_thread(look) == [False, True, False, False, False, True]


def test_lowered_is_for_a_span_and_foreground_on_a_normal_thread_is_nothing():
    def look():
        seen = []
        with lanes.foreground():
            seen.append(lanes.is_background())
        with lanes.lowered():
            seen.append(lanes.is_background())
        seen.append(lanes.is_background())
        return seen
    assert _in_thread(look) == [False, True, False]


@pytest.mark.skipif(sys.platform != "darwin", reason="thread QoS is macOS's")
def test_on_macos_the_lanes_are_the_thread_s_quality_of_service():
    def look():
        seen = [lanes.qos_class()]
        lanes.background()
        seen.append(lanes.qos_class())
        with lanes.foreground():
            seen.append(lanes.qos_class())
        seen.append(lanes.qos_class())
        return seen
    normal, low, raised, again = _in_thread(look)
    assert low == lanes.QOS_CLASS_BACKGROUND and again == lanes.QOS_CLASS_BACKGROUND
    assert raised == (normal if normal != lanes.QOS_CLASS_UNSPECIFIED else lanes.QOS_CLASS_DEFAULT)


def test_a_commit_from_a_background_thread_runs_in_the_foreground(tmp_path, monkeypatch):
    monkeypatch.setattr(Engine, "compact_in_child", False)
    monkeypatch.setattr(emb, "RUNNER", lambda: None)
    e = Engine.open(tmp_path / "k")
    try:
        submit = e.store.wal.submit
        seen = []

        def watched(ops):
            seen.append(lanes.is_background())
            return submit(ops)
        monkeypatch.setattr(e.store.wal, "submit", watched)

        def write():
            lanes.background()
            e.transaction({"puts": [{"_key": "project:a", "_kind": "project", "_label": "A"}]})
            return lanes.is_background()
        assert _in_thread(write) is True              # lowered again after the commit
        assert seen == [False]                        # but not while it committed
    finally:
        e.close()


@pytest.mark.knowledge_engine
def test_a_held_background_lane_never_delays_a_record_write(tmp_path, monkeypatch):
    """The pace holds: the compaction child is stopped mid-merge and
    embedding waits. Several hundred writes and reads still each finish
    promptly."""
    monkeypatch.setattr(Engine, "compact_in_child", True)
    monkeypatch.setattr(vecmath, "MATH", vecmath.Reference())
    monkeypatch.setattr(emb, "RUNNER", lambda: emb.FakeEmbedder(32))
    e = Engine.open(tmp_path / "k")
    compacted = threading.Event()
    try:
        for g in range(4):                            # four generations: a full tier
            e.transaction({"puts": [{"_key": f"chunk:{g}-{i}", "_kind": "chunk", "_label": f"c{g}-{i}",
                                     "text": f"words of chunk {g} {i} " * 20} for i in range(50)]})
            e.store.flush()
        e.set_pace("hold")
        threading.Thread(target=lambda: (e.store.compact(), compacted.set()), daemon=True).start()
        end = time.monotonic() + 10
        while e.store.child is None and time.monotonic() < end:
            time.sleep(0.01)
        assert e.store.child is not None, "the compaction child never started"
        waiting = e.vectors.pending()

        writes, reads = [], []
        for i in range(400):
            t0 = time.perf_counter()
            e.transaction({"puts": [{"_key": f"project:p{i}", "_kind": "project", "_label": f"P{i}",
                                     "summary": f"project number {i}"},
                                    {"_key": f"chunk:new-{i}", "_kind": "chunk", "_label": f"n{i}",
                                     "text": f"a new chunk {i}"}]})
            writes.append(time.perf_counter() - t0)
            t0 = time.perf_counter()
            assert e.docs.get(f"project:p{i}")["summary"] == f"project number {i}"
            reads.append(time.perf_counter() - t0)

        assert not compacted.is_set()                 # the background lane really was held
        assert e.vectors.pending() >= waiting + 400   # and embedding waited
        for name, times in (("write", writes), ("read", reads)):
            q = statistics.quantiles(times, n=100)
            print(f"{name}: p50 {q[49] * 1000:.2f} ms, p99 {q[98] * 1000:.2f} ms, "
                  f"max {max(times) * 1000:.2f} ms")
        assert max(writes) < 0.1, max(writes)
        assert max(reads) < 0.1, max(reads)
    finally:
        e.set_pace("free")
        assert compacted.wait(60)
        e.close()
