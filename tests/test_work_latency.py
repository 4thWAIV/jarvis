"""A latency guard for the Work routes: with 300 packages and 50 open items
(their pieces working, held on packages and blocked, their decisions open)
and the Work view's tick running on a thread of its own as the server runs
it, sweeping and composing as hard as it can, a report's write and a read
of /api/work each answer within WORK_ROUTE_BUDGET_SEC.

Before the view stopped holding its lock while it read and composed, a
write waited for the tick's whole pass (10 s and more on live data)."""
from __future__ import annotations

import json
import os
import shutil
import threading
import time
from pathlib import Path

import pytest

from jarvis import data_paths, package_inbox, work_packages, work_store, work_view
from tests.test_brain_page_parity import wired  # noqa: F401  (the fixture)

WORK_ROUTE_BUDGET_SEC = 1.0
PACKAGES, ITEMS = 300, 50
DECK = Path(__file__).resolve().parent.parent / "examples" / "packages" / "decision-deck"


def _seed_packages(n: int) -> list[str]:
    """`n` decision decks unpacked here, each its own name: their ids."""
    root = data_paths.packages_dir()
    root.mkdir(parents=True, exist_ok=True)
    ids = []
    for i in range(n):
        name = f"deck-{i:03d}"
        package_id = f"{name}-1.0.0-{i:012x}"
        folder = root / package_id
        shutil.copytree(DECK, folder, copy_function=os.link)   # read only: linked
        manifest = json.loads((DECK / "package.json").read_text())
        manifest["name"] = name
        (folder / "package.json").unlink()
        (folder / "package.json").write_text(json.dumps(manifest))
        ids.append(package_id)
    return ids


def _seed_items(folder: str, package_ids: list[str]) -> list[str]:
    """`ITEMS` open items, each with packages sent for it, a piece working,
    a piece held on one of its packages, a piece blocked, and a decision
    open."""
    per = len(package_ids) // ITEMS
    ids = []
    for n in range(ITEMS):
        item = work_store.create(f"Item {n}", "folder", folder)
        mine = package_ids[n * per:(n + 1) * per]
        for package_id in mine:
            package_inbox.arrive(work_packages.by_id(package_id), package_id,
                                 package_inbox.Sender(project="jarvis"), work_id=item.id)
        work_store.record(work_store.WorkEvent(item.id, "decision_needed", "Which engine?",
                                               {"key": "engine", "options": ["a", "b"]}))
        for key, state, extra in (
                ("build", "working", {}),
                ("land", "waiting_on_tim", {"until": [f"package:{mine[0]}"]}),
                ("docs", "blocked", {"until": ["the style guide"]})):
            work_store.record(work_store.WorkEvent(item.id, "piece", f"{key} reported.", {
                "key": key, "title": key.title(), "state": state,
                "who": {"kind": "agent", "ref": f"s-{n}"}, **extra}))
        ids.append(item.id)
    return ids


@pytest.fixture
def busy(wired, monkeypatch):
    """The seeded record, the server's view built and drained on its own
    thread, and its tick running without pause, sweeping every time."""
    folder = wired.tmp / "work"
    folder.mkdir()
    ids = _seed_items(str(folder), _seed_packages(PACKAGES))
    view = wired.server.work_held
    view.load()
    view.start()
    monkeypatch.setattr(work_view, "SWEEP_EVERY_SEC", 0.0)
    monkeypatch.setattr(work_view, "SOURCE_EVERY_SEC", 0.0)
    stop, ticks = threading.Event(), []

    def ticking():
        while not stop.is_set():
            # Every item's clock due, so every tick composes all of them.
            with view._lock:
                view._due = {i: 0.0 for i in view._due}
            view.tick()
            ticks.append(time.time())
    thread = threading.Thread(target=ticking, name="work-tick", daemon=True)
    thread.start()
    yield wired, ids, ticks, thread
    stop.set()
    thread.join(30)
    view.stop()


def test_writes_and_reads_answer_within_budget_while_the_tick_works(busy):
    from tests.agent_key_fixtures import onboarded_auth
    wired, ids, ticks, ticking = busy
    agent = onboarded_auth(("work",), "sess-latency")
    while len(ticks) < 1:
        time.sleep(0.01)                  # the tick is well under way
    writes, reads = [], []
    for n, item_id in enumerate(ids[:10]):
        started = time.perf_counter()
        r = wired.client.post(f"/api/work/{item_id}/events?report=latency-guard-{n:04d}",
                              auth=agent, json={"kind": "piece", "summary": "Built.",
                                                "detail": {"key": "build", "state": "working"}})
        writes.append(time.perf_counter() - started)
        assert r.status_code == 200, r.text
        started = time.perf_counter()
        r = wired.client.get("/api/work")
        reads.append(time.perf_counter() - started)
        assert r.status_code == 200 and len(r.json()["items"]) == ITEMS
    assert ticking.is_alive(), "the tick kept working throughout"
    assert max(writes) < WORK_ROUTE_BUDGET_SEC, writes
    assert max(reads) < WORK_ROUTE_BUDGET_SEC, reads
