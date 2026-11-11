"""The record layer (`jarvis/records.py`): units that land whole or run
again, conditions on every record read, write before notify, segments."""
import threading

import pytest

from jarvis import records
from jarvis.knowledge.uds import Transaction


def test_a_unit_lands_whole_and_runs_after_callbacks_once():
    told = []

    def make(unit):
        assert unit.get("/t/a.work") is None
        unit.put("/t/a.work", {"n": 1, "event": {"kind": "created"}}, change="created")
        unit.put("/t/b.work", {"n": 1})
        unit.after(lambda: told.append(records.get("/t/a.work").data["n"]))
        return "made"

    assert records.atomic(make, change="test") == "made"
    assert told == [1]
    a = records.get("/t/a.work")
    assert a.version == 1 and a.data == {"n": 1, "event": {"kind": "created"}}
    assert [v.change for v in records.history("/t/a.work")] == ["created"]
    assert [v.change for v in records.history("/t/b.work")] == ["test"]


def test_a_record_read_and_changed_meanwhile_makes_the_unit_run_again():
    records.write("/t/c.work", {"n": 0})
    runs = []

    def bump(unit):
        rec = unit.get("/t/c.work")
        runs.append(rec.data["n"])
        if len(runs) == 1:          # someone else writes between our read and our write
            records.write("/t/c.work", {"n": 10})
        unit.put("/t/c.work", {"n": rec.data["n"] + 1})

    records.atomic(bump)
    assert runs == [0, 10]
    assert records.get("/t/c.work").data == {"n": 11}


def test_a_record_only_read_is_a_condition_too():
    records.write("/t/d.work", {"open": True})
    attempts = []

    def close_other(unit):
        d = unit.get("/t/d.work")
        attempts.append(d.data["open"])
        if len(attempts) == 1:
            records.write("/t/d.work", {"open": False})
        unit.put("/t/e.work", {"saw_open": d.data["open"]})

    records.atomic(close_other)
    assert attempts == [True, False]
    assert records.get("/t/e.work").data == {"saw_open": False}


def test_a_path_read_as_missing_must_still_be_missing():
    attempts = []

    def claim(unit):
        attempts.append(unit.get("/t/key.work"))
        if len(attempts) == 1:
            records.write("/t/key.work", {"by": "other"})
        if unit.get("/t/key.work") is None:
            unit.put("/t/key.work", {"by": "me"})
        else:
            unit.put("/t/lost.work", {"to": "other"})

    records.atomic(claim)
    assert records.get("/t/key.work").data == {"by": "other"}
    assert records.get("/t/lost.work").data == {"to": "other"}


def test_concurrent_increments_all_land():
    records.write("/t/counter.work", {"n": 0})

    def inc(unit):
        rec = unit.get("/t/counter.work")
        unit.put("/t/counter.work", {"n": rec.data["n"] + 1})

    threads = [threading.Thread(target=lambda: [records.atomic(inc) for _ in range(10)]) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert records.get("/t/counter.work").data == {"n": 40}


def test_history_carries_each_version_s_fields():
    for i in range(3):
        records.write("/t/h.work", {"n": i, "event": {"kind": f"k{i}", "at": 100.0 + i}},
                      change=f"k{i}", by=records.by("agent:s1"))
    found = records.history("/t/h.work", fields=["event"])
    assert [v.data["event"]["kind"] for v in found] == ["k0", "k1", "k2"]
    assert all(v.agent == "agent:s1" and v.by == "tim" for v in found)


def test_a_write_in_a_transaction_may_name_its_own_change():
    u = records.uds()
    u.transaction(records.JARVIS, Transaction().put("/t/x.work", {}).tag("mine", "my summary")
                  .put("/t/y.work", {}), change="theirs")
    assert records.history("/t/x.work")[-1].change == "mine"
    assert records.history("/t/x.work")[-1].summary == "my summary"
    assert records.history("/t/y.work")[-1].change == "theirs"


def test_nothing_is_sent_for_a_unit_that_writes_nothing():
    assert records.atomic(lambda unit: unit.get("/t/none.work")) is None
    assert records.get("/t/none.work") is None


@pytest.mark.parametrize("raw", ["a/b", "x.y", "..", "", "café", "agent:abc/sub:1", "a" * 400])
def test_segments_are_one_path_segment(raw):
    seg = records.segment(raw)
    path = f"/t/{seg}.work"
    from jarvis.knowledge import paths
    assert paths.is_path(path) and paths.type_of(path) == "work" and paths.parent(path) == "/t"
    assert "." not in seg and paths.type_of(f"/t/{seg}") == "folder"
    if len(raw) < 100:
        assert records.unsegment(seg) == raw


def test_scan_and_count():
    records.write_many([(f"/t/s/{i}.run", {"status": "queued" if i % 2 else "done", "i": i})
                        for i in range(10)])
    assert [r.data["i"] for r in records.scan("/t/s", where=[["status", "=", "queued"]])] == [1, 3, 5, 7, 9]
    assert records.count("/t/s", where=[["status", "=", "done"]]).items == 5
    assert [r.data["i"] for r in records.scan("/t/s", order=("i", "desc"), limit=3)] == [9, 8, 7]
