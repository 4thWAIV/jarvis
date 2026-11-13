"""The Work view, held (work_view.py): built once, each item worked out
again only when it changes, and sent to the pages as one frame.

One write is one frame for one item; a piece owner's live state changing is
a frame for that item alone, worked out from the record held, and its
evidence clock ticking is none; the clock passing a threshold moves the
item; a move to History has its own frame; and /api/work answers from what
is held, reading no file and no row, in milliseconds."""
from __future__ import annotations

import builtins
import time

import pytest

from jarvis import package_inbox, work_packages, work_store, work_view
from tests.test_brain_page_parity import wired  # noqa: F401  (the fixture)

SESSION = "s-live"


@pytest.fixture
def folder(wired):
    path = wired.tmp / "speech"
    path.mkdir()
    return str(path)


@pytest.fixture
def owners():
    """Each piece owner's live state, as the session watcher's poll would show it."""
    return {}


@pytest.fixture
def view(wired, owners, monkeypatch):
    frames: list[dict] = []
    held = work_view.WorkView(frames.append,
                              live=lambda who: owners.get(who.ref) if who.ref else None)
    # Told of every write as the server's own view is, for this test only.
    monkeypatch.setitem(work_store._changed_listeners, "test.view", held.refresh)
    held.load()
    held.frames = frames
    return held


def _live(state="working", doing="Using Bash", written=None, age=0.0):
    return work_store.LiveAgent(name="Builder", state=state, written=written or 1000.0,
                                doing=doing, active_within=120.0,
                                activity={"evidence_age_seconds": age}, snapshot_id=f"snap-{age}")


def test_one_write_is_one_frame_for_one_item(view, folder):
    first = work_store.create("Speech migration", "folder", folder)
    other = work_store.create("Captions", "folder", folder)
    view.frames.clear()
    revision = view.revision
    work_store.record(work_store.WorkEvent(first.id, "update", "Engine wired.", {}))
    [frame] = view.frames
    assert frame["type"] == "work_item" and frame["list"] == "items"
    assert frame["item"]["id"] == first.id and frame["item"]["latest_update"] == "Engine wired."
    assert frame["revision"] == revision + 1 == view.revision
    assert frame["order"]["items"] == [first.id, other.id]
    assert [g["ids"] for g in frame["groups"]] == [[first.id, other.id]]


def test_a_frame_carries_exactly_what_a_fresh_read_lists(view, folder):
    from jarvis import work_api
    item = work_store.create("Speech migration", "folder", folder)
    work_store.record(work_store.WorkEvent(item.id, "decision_needed", "Which engine?",
                                           {"key": "engine", "options": ["Kroko", "Apple"]}))
    fresh = work_api.item_json(work_store.get(item.id))
    assert view.frames[-1]["item"] == fresh == view.item(item.id)


def test_a_live_owner_changing_moves_only_its_item_and_its_clock_does_not(view, folder, owners):
    on = work_store.create("Speech migration", "folder", folder)
    off = work_store.create("Captions", "folder", folder)
    for item, ref in ((on, SESSION), (off, "s-other")):
        work_store.record(work_store.WorkEvent(item.id, "piece", "On it.", {
            "key": "build", "title": "Build", "state": "working",
            "who": {"kind": "agent", "ref": ref}}))
    view.frames.clear()
    owners[SESSION] = _live()
    view.tick()
    [frame] = view.frames
    assert frame["item"]["id"] == on.id and frame["item"]["state"] == "working"
    assert frame["item"]["pieces"][0]["live"]["doing"] == "Using Bash"
    # Its evidence ageing, with nothing else new, is no change.
    owners[SESSION] = _live(age=1.5)
    view.tick()
    assert len(view.frames) == 1
    # What it is doing now is.
    owners[SESSION] = _live(doing="Using Edit", written=1001.0)
    view.tick()
    assert [f["item"]["pieces"][0]["live"]["doing"] for f in view.frames] == ["Using Bash", "Using Edit"]


def test_the_clock_passing_a_threshold_moves_the_item_on_the_tick(view, folder, monkeypatch):
    item = work_store.create("Speech migration", "folder", folder)
    work_store.record(work_store.WorkEvent(item.id, "update", "Ready for review.",
                                           {"state": "verifying"}))
    assert view.item(item.id)["quiet"] is False
    view.frames.clear()
    later = time.time() + work_store.QUIET_AFTER_SEC + 5
    monkeypatch.setattr(work_store.time, "time", lambda: later)
    view.tick()
    [frame] = view.frames
    assert frame["item"]["id"] == item.id and frame["item"]["quiet"] is True


def test_a_move_to_history_has_its_own_frame(view, folder):
    item = work_store.create("Speech migration", "folder", folder)
    view.frames.clear()
    work_store.record(work_store.WorkEvent(item.id, "closed", "Not needed.", {}))
    [frame] = view.frames
    assert frame["type"] == "work_item_history" and frame["list"] == "history"
    assert frame["order"] == {"items": [], "history": [item.id]}


def test_the_list_answers_from_what_is_held_without_reading(wired, folder, monkeypatch):
    """/api/work with 20 items reads no file and no row, and answers in
    well under 50 ms."""
    server = wired.server
    for n in range(20):
        item = work_store.create(f"Item {n}", "folder", folder)
        work_store.record(work_store.WorkEvent(item.id, "piece", "On it.", {
            "key": "build", "title": "Build", "state": "working",
            "who": {"kind": "agent", "ref": f"s-{n}"}}))
    server.work_held.load()                      # at startup
    assert len(wired.client.get("/api/work").json()["items"]) == 20

    def refused(*args, **kwargs):
        raise AssertionError("/api/work read something")
    real_open = builtins.open
    opened = []
    monkeypatch.setattr(builtins, "open", lambda *a, **k: opened.append(a[0]) or real_open(*a, **k))
    for name in ("records", "check_source", "get", "listing", "live_agent", "_docs", "_doc"):
        monkeypatch.setattr(work_store, name, refused)
    monkeypatch.setattr(package_inbox, "waiting_by_work", refused)
    monkeypatch.setattr(work_packages, "find", refused)
    took = []
    for _ in range(5):
        started = time.perf_counter()
        r = wired.client.get("/api/work")
        took.append(time.perf_counter() - started)
        assert r.status_code == 200 and len(r.json()["items"]) == 20
    assert opened == []
    assert min(took) < 0.05, took
    assert r.json()["revision"] == server.work_held.revision


def test_a_finished_owner_s_observed_time_moving_is_no_frame(view, folder, owners):
    """An idle or finished owner is observed afresh on every look; that
    changes nothing the page shows, so it is no frame and no work."""
    item = work_store.create("Speech migration", "folder", folder)
    work_store.record(work_store.WorkEvent(item.id, "piece", "On it.", {
        "key": "build", "title": "Build", "state": "working", "who": {"kind": "agent", "ref": SESSION}}))
    owners[SESSION] = _live(state="finished", doing="idle", written=1000.0)
    view.tick()
    view.frames.clear()
    for n in range(3):
        owners[SESSION] = _live(state="finished", doing="idle", written=1001.0 + n)
        view.tick()
    assert view.frames == []


# ── the lock is never held during I/O ────────────────────────────────────

def _blocking_owner(owners, ref):
    """An owner whose live lookup waits until released, as a slow look at
    the sessions would: (started, release)."""
    import threading
    started, release = threading.Event(), threading.Event()

    class Slow(dict):
        def get(self, key, default=None):
            if key == ref:
                started.set()
                release.wait(10)
            return super().get(key, default)
    return Slow(owners), started, release


def test_a_write_never_waits_on_a_tick_that_is_composing(wired, folder):
    """The tick looks and composes outside the view's lock: a write to
    another item while it is busy is worked out and sent at once."""
    import threading
    slow, started, release = _blocking_owner({}, SESSION)
    frames: list[dict] = []
    held = work_view.WorkView(frames.append, live=lambda who: slow.get(who.ref))
    busy = work_store.create("Speech migration", "folder", folder)
    work_store.record(work_store.WorkEvent(busy.id, "piece", "On it.", {
        "key": "build", "title": "Build", "state": "working", "who": {"kind": "agent", "ref": SESSION}}))
    other = work_store.create("Captions", "folder", folder)
    started.clear()
    held.load()
    started.clear()
    ticking = threading.Thread(target=held.tick)
    ticking.start()
    try:
        assert started.wait(5), "the tick looked up the owner"
        took = time.perf_counter()
        work_store.record(work_store.WorkEvent(other.id, "update", "Captions wired.", {}))
        held.refresh({other.id})
        took = time.perf_counter() - took
        assert took < 1.0, took
        assert held.item(other.id)["latest_update"] == "Captions wired."
    finally:
        release.set()
        ticking.join(5)


def test_an_older_composition_never_overwrites_a_newer_one(wired, folder):
    """A tick that began composing an item before a write landed finishes
    after the write's own composition: the write's is kept, and the tick's
    is dropped."""
    import threading
    started, release = threading.Event(), threading.Event()
    owners = {}

    def live(who):
        if threading.current_thread().name == "ticking":
            started.set()
            release.wait(10)
        return owners.get(who.ref)
    frames: list[dict] = []
    held = work_view.WorkView(frames.append, live=live)
    item = work_store.create("Speech migration", "folder", folder)
    work_store.record(work_store.WorkEvent(item.id, "piece", "On it.", {
        "key": "build", "title": "Build", "state": "working", "who": {"kind": "agent", "ref": SESSION}}))
    held.load()
    owners[SESSION] = _live()             # the owner starts working: the tick composes it
    ticking = threading.Thread(target=held.tick, name="ticking")
    ticking.start()
    try:
        assert started.wait(5)
        after = work_store.record(work_store.WorkEvent(item.id, "update", "Engine wired.", {}))
        held.refresh({item.id})           # worked out and kept while the tick is busy
        assert held.item(item.id)["latest_update"] == "Engine wired."
    finally:
        release.set()
        ticking.join(5)
    assert held.item(item.id)["latest_update"] == "Engine wired."
    assert held._stamps[item.id][0] == after.revision


def test_with_its_own_thread_a_write_returns_before_the_view_works_it_out(
        wired, folder, monkeypatch):
    """Once started, the view drains writes on its own thread: the writer
    goes on at once, and the frame follows."""
    import threading
    frames: list[dict] = []
    held = work_view.WorkView(frames.append, live=lambda who: None)
    monkeypatch.setitem(work_store._changed_listeners, "test.view", held.refresh)
    item = work_store.create("Speech migration", "folder", folder)
    held.load()
    gate = threading.Event()
    real = work_store.records
    monkeypatch.setattr(work_store, "records",
                        lambda ids=None: (gate.wait(10), real(ids))[1])
    held.start()
    try:
        took = time.perf_counter()
        work_store.record(work_store.WorkEvent(item.id, "update", "Engine wired.", {}))
        assert time.perf_counter() - took < 1.0
        assert frames == []             # the view has not worked it out yet
        gate.set()
        deadline = time.time() + 5
        while time.time() < deadline and not frames:
            time.sleep(0.01)
        [frame] = frames
        assert frame["item"]["latest_update"] == "Engine wired."
    finally:
        gate.set()
        held.stop()


def test_a_write_s_answer_is_the_item_it_left_even_before_the_view_has_it(wired, folder):
    """`shown` gives the view's JSON once it has caught up, else the item
    the write left, worked out from what the view holds."""
    frames: list[dict] = []
    held = work_view.WorkView(frames.append, live=lambda who: None)
    item = work_store.create("Speech migration", "folder", folder)
    held.load()
    after = work_store.record(work_store.WorkEvent(item.id, "update", "Engine wired.", {}))
    shown = held.shown(after)                  # the view was not told of the write
    assert shown["latest_update"] == "Engine wired." and shown["revision"] == after.revision
    held.refresh({item.id})
    assert held.shown(after) is held.item(item.id)
