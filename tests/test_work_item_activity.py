"""An agent works on a Work item only through a piece of it in progress
(work_store.in_progress): a piece reported working, or a planned or ready
piece a subagent was put on, whose owner is in a turn (the one definition,
worker_inventory.working). An agent busy elsewhere never makes a piece in
review, done, blocked or waiting on him read working: not the item's state,
not its live line, not whether he can approve it, not the row's orb
(`workers.ts` `pieceInProgress`, the same rule)."""
from __future__ import annotations

import time

import pytest

from jarvis import work_store
from tests.test_brain_page_parity import wired  # noqa: F401  (the fixture)
from tests.test_work_states import _key, _piece

BUSY = "a5b4be76-7b3d-4a0c-9220-817d38b9e5f3"     # in a turn, on something
SUB = "aeb4b37bbf9ed2571"


def _live(states):
    """A live lookup: (session, agent id) -> LiveAgent in the state given."""
    def look(session, agent=""):
        state = states.get(agent or session)
        if state is None:
            return None
        return work_store.LiveAgent(name=agent or session, state=state, written=time.time(),
                                    doing="model", active_within=120.0)
    return look


@pytest.fixture
def items(wired, tmp_path, monkeypatch):
    made = []
    for name in ("a", "b"):
        folder = tmp_path / name
        folder.mkdir()
        made.append(work_store.create(f"Item {name}", "folder", str(folder)))
    # Both are agents JARVIS has (a report naming one is refused otherwise),
    # neither in a turn until a test says so.
    _use(monkeypatch, {BUSY: "idle", SUB: "idle"})
    return made


def _use(monkeypatch, states):
    monkeypatch.setattr(work_store, "_live_lookup", _live({BUSY: "idle", SUB: "idle", **states}))


def _agent(ref=BUSY):
    return {"kind": "agent", "ref": ref}


def test_an_agent_busy_on_one_item_is_not_working_on_its_review_piece_elsewhere(wired, items, monkeypatch):
    a, b = items
    key = _key()
    _piece(wired, a.id, "build", auth=key, state="working", who=_agent())
    _piece(wired, b.id, "done-work", auth=key, state="verifying", evidence=["abc1234"], who=_agent())
    _use(monkeypatch, {BUSY: work_store.LIVE_WORKING})
    got_a, got_b = work_store.get(a.id), work_store.get(b.id)
    assert got_a.state == work_store.WORKING and got_a.pieces[0].state == work_store.WORKING
    assert got_b.state == work_store.VERIFYING and got_b.live is None
    assert got_b.pieces[0].live is None and got_b.pieces[0].state == work_store.VERIFYING
    item = wired.client.get(f"/api/work/{b.id}").json()["item"]
    assert item["state"] == "verifying" and item["approvable"] is True


def test_a_subagent_waiting_on_background_work_does_not_hold_a_review_piece(wired, items, monkeypatch):
    """Live, "Session messages": a verifying piece's subagent waiting on its
    own background work read the item activity_unknown and withheld Approve."""
    _, b = items
    _piece(wired, b.id, "pr-90", auth=_key(), state="verifying", evidence=["abc1234"],
           who=_agent(f"{BUSY}/sub:{SUB}"))
    _use(monkeypatch, {SUB: work_store.LIVE_WAITING})
    item = wired.client.get(f"/api/work/{b.id}").json()["item"]
    assert item["state"] == "verifying" and item["approvable"] is True
    assert item["live"] is None


@pytest.mark.parametrize("state,extra", [
    ("verifying", {"evidence": ["abc1234"]}),
    ("blocked", {"until": ["the build server is down"]}),
])
def test_no_piece_out_of_progress_reads_working_whatever_its_owner_does(wired, items, monkeypatch, state, extra):
    _, b = items
    _piece(wired, b.id, "p", auth=_key(), state=state, who=_agent(f"{BUSY}/sub:{SUB}"), **extra)
    _use(monkeypatch, {SUB: work_store.LIVE_WORKING, BUSY: work_store.LIVE_WORKING})
    got = work_store.get(b.id)
    assert got.state != work_store.WORKING and got.live is None
    assert all(p.state != work_store.WORKING and p.live is None for p in got.pieces)


def test_a_subagent_put_on_a_ready_piece_works_on_it_while_in_its_turn(wired, items, monkeypatch):
    a, _ = items
    _piece(wired, a.id, "docs", auth=_key(), state="ready", who=_agent(f"{BUSY}/sub:{SUB}"))
    _use(monkeypatch, {SUB: work_store.LIVE_WORKING})
    got = work_store.get(a.id)
    assert got.pieces[0].state == work_store.WORKING and got.state == work_store.WORKING
    _use(monkeypatch, {SUB: "finished"})
    assert work_store.get(a.id).pieces[0].state == work_store.READY


def test_a_session_owning_a_ready_piece_is_not_working_on_it_until_it_reports_working(wired, items, monkeypatch):
    a, _ = items
    _piece(wired, a.id, "next", auth=_key(), state="ready", who=_agent())
    _use(monkeypatch, {BUSY: work_store.LIVE_WORKING})
    got = work_store.get(a.id)
    assert got.pieces[0].state == work_store.READY and got.state == work_store.READY


def test_a_working_claim_reads_working_only_while_its_owner_is_in_a_turn(wired, items, monkeypatch):
    a, _ = items
    _piece(wired, a.id, "build", auth=_key(), state="working", who=_agent())
    _use(monkeypatch, {BUSY: "idle"})
    assert work_store.get(a.id).pieces[0].state == work_store.ACTIVITY_UNKNOWN
    _use(monkeypatch, {BUSY: work_store.LIVE_WORKING})
    assert work_store.get(a.id).pieces[0].state == work_store.WORKING


@pytest.mark.parametrize("reported,sub,expected", [
    ("working", False, True), ("working", True, True),
    ("ready", True, True), ("planned", True, True), ("ready", False, False),
    ("verifying", True, False), ("verifying", False, False), ("done", True, False),
    ("blocked", True, False), ("waiting_on_tim", True, False),
])
def test_in_progress_is_the_one_rule(reported, sub, expected):
    who = work_store.Who("agent", f"{BUSY}/sub:{SUB}" if sub else BUSY)
    assert work_store.in_progress(reported, who) is expected
