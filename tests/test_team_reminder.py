"""The spoken sign-in reminder (team_reminder.py, started by team_api.start):
said once per process when this JARVIS is not signed in after startup
settles, through the notice path it is given, and never when it is signed in.
team_client's own states decide what "signed in" means.
"""
from __future__ import annotations

import pytest

from jarvis import team_api, team_client, team_reminder
from jarvis.team_client import TeamConnection


@pytest.fixture(autouse=True)
def _fresh_process(monkeypatch):
    """Each test is a new JARVIS start: nothing said yet."""
    monkeypatch.setattr(team_reminder, "_said", False)


class _Notices:
    def __init__(self):
        self.said: list[tuple[str, str, str]] = []

    async def __call__(self, line: str, note: str, what: str) -> None:
        self.said.append((line, note, what))


def _connection(state: str) -> TeamConnection:
    conn = TeamConnection(store=object())
    if state != "disconnected":
        conn.saved = {"server_url": "https://team.example", "server_id": "s",
                      "last_contact_at": team_client._now()}
        conn.signed_out = state == "signed_out"
    assert conn.state() == state
    return conn


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["disconnected", "signed_out"])
async def test_not_signed_in_is_said_once_per_process(state):
    say = _Notices()
    conn = _connection(state)
    await team_reminder.remind_once(say, conn.signed_in, settle_sec=0)
    await team_reminder.remind_once(say, conn.signed_in, settle_sec=0)
    assert say.said == [(team_reminder.LINE, team_reminder.NOTE, "team sign-in")]
    assert say.said[0][0] == ("You're not signed in to the team server, sir; messages "
                              "between people are off until you are.")


@pytest.mark.asyncio
async def test_signed_in_nothing_is_said():
    say = _Notices()
    await team_reminder.remind_once(say, _connection("connected").signed_in, settle_sec=0)
    assert say.said == []


@pytest.mark.asyncio
async def test_signed_in_at_the_look_is_not_said_after_a_later_sign_out():
    """Once a start: the one look is after startup settles, and a sign-out
    later in the same process is the page's to remind about, not a second
    spoken line."""
    say = _Notices()
    conn = _connection("connected")
    await team_reminder.remind_once(say, conn.signed_in, settle_sec=0)
    conn.signed_out = True
    await team_reminder.remind_once(say, conn.signed_in, settle_sec=0)
    assert say.said == [(team_reminder.LINE, team_reminder.NOTE, "team sign-in")]


def test_signing_in_counts_as_not_signed_in():
    conn = _connection("disconnected")
    conn.pairing = object()
    assert conn.state() == "pairing" and not conn.signed_in()
    assert _connection("connected").signed_in()


@pytest.mark.asyncio
async def test_team_api_start_says_it_once_after_settling_and_stop_cancels(monkeypatch):
    """The lifecycle: started with a notice path, the reminder waits
    SETTLE_SEC; stopping before then says nothing, and after it, it is said."""
    say = _Notices()
    assert team_reminder.SETTLE_SEC == 30.0
    conn = _connection("disconnected")
    monkeypatch.setattr(team_client, "connection", conn)
    monkeypatch.setattr(conn, "start", lambda **kw: None)
    monkeypatch.setattr(team_api.host_resources.sampler, "run", _never)
    team_api.start(say=say)                      # the real wait: stopped before it ends
    await team_api.stop()
    assert say.said == [] and team_api._reminder is None

    monkeypatch.setattr(team_reminder, "SETTLE_SEC", 0.0)
    team_api.start(say=say)
    await team_api._reminder
    assert [s[0] for s in say.said] == [team_reminder.LINE]
    await team_api.stop()


async def _never():
    """The host sampler, not run here."""
