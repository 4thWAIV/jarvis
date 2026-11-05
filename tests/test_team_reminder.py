"""The sign-in reminder's attention item (team_reminder.py, started by
team_api.start): recorded once per process when this JARVIS is not signed
in after startup settles, never when it is signed in, resolved once it is,
and never spoken by itself. team_client's own states decide what "signed
in" means.
"""
from __future__ import annotations

import asyncio

import pytest

from jarvis import team_api, team_client, team_reminder
from jarvis.attention_store import AttentionStore
from jarvis.team_client import TeamConnection


@pytest.fixture(autouse=True)
def _fresh_process(monkeypatch):
    """Each test is a new JARVIS start: nothing recorded yet."""
    monkeypatch.setattr(team_reminder, "_said", False)


def _connection(state: str) -> TeamConnection:
    conn = TeamConnection(store=object())
    if state != "disconnected":
        conn.saved = {"server_url": "https://team.example", "server_id": "s",
                      "last_contact_at": team_client._now()}
        conn.signed_out = state == "signed_out"
    assert conn.state() == state
    return conn


def _pending(store: AttentionStore) -> list[tuple[str, str, str]]:
    return [(i.kind, i.source_id, i.event) for i in store.items()]


async def _settle(task: asyncio.Task) -> None:
    for _ in range(50):
        if task.done():
            return
        await asyncio.sleep(0.01)


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["disconnected", "signed_out"])
async def test_not_signed_in_is_recorded_once_per_process_and_resolved_on_sign_in(state):
    store = AttentionStore()
    conn = _connection(state)
    first = asyncio.create_task(team_reminder.remind_once(store, conn.signed_in, settle_sec=0,
                                                          watch_sec=0.01))
    await asyncio.sleep(0.05)
    assert _pending(store) == [("sign_in", "team", "not_signed_in")]
    await team_reminder.remind_once(store, conn.signed_in, settle_sec=0)
    assert len(store.history(store.items()[0].id)) == 1, "once per process"
    conn.signed_out, conn.saved = False, {"server_url": "https://team.example",
                                         "server_id": "s",
                                         "last_contact_at": team_client._now()}
    await _settle(first)
    assert first.done() and _pending(store) == [], "resolved once signed in"


@pytest.mark.asyncio
async def test_signed_in_nothing_is_recorded():
    store = AttentionStore()
    await team_reminder.remind_once(store, _connection("connected").signed_in, settle_sec=0)
    assert _pending(store) == []


@pytest.mark.asyncio
async def test_signed_in_at_the_look_is_not_recorded_after_a_later_sign_out():
    """Once a start: the one look is after startup settles, and a sign-out
    later in the same process is the page's to remind about."""
    store = AttentionStore()
    conn = _connection("connected")
    await team_reminder.remind_once(store, conn.signed_in, settle_sec=0)
    conn.signed_out = True
    task = asyncio.create_task(team_reminder.remind_once(store, conn.signed_in, settle_sec=0,
                                                         watch_sec=0.01))
    await asyncio.sleep(0.05)
    assert _pending(store) == [("sign_in", "team", "not_signed_in")]
    task.cancel()


def test_signing_in_counts_as_not_signed_in():
    conn = _connection("disconnected")
    conn.pairing = object()
    assert conn.state() == "pairing" and not conn.signed_in()
    assert _connection("connected").signed_in()


@pytest.mark.asyncio
async def test_team_api_start_records_it_after_settling_and_stop_cancels(monkeypatch):
    """The lifecycle: started with the attention record, the reminder waits
    SETTLE_SEC; stopping before then records nothing, and after it, it is
    recorded."""
    store = AttentionStore()
    assert team_reminder.SETTLE_SEC == 30.0
    conn = _connection("disconnected")
    monkeypatch.setattr(team_client, "connection", conn)
    monkeypatch.setattr(conn, "start", lambda **kw: None)
    monkeypatch.setattr(team_api.host_resources.sampler, "run", _never)
    team_api.start(attention=store)              # the real wait: stopped before it ends
    await team_api.stop()
    assert _pending(store) == [] and team_api._reminder is None

    monkeypatch.setattr(team_reminder, "SETTLE_SEC", 0.0)
    team_api.start(attention=store)
    await asyncio.sleep(0.05)
    assert _pending(store) == [("sign_in", "team", "not_signed_in")]
    await team_api.stop()


async def _never():
    """The host sampler, not run here."""
