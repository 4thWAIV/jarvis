"""preflight existed, was tested, and was never called.

Its own docstring claimed it ran at startup. It did not -- which is why an
expired login reached the user as "my language systems are down" and nothing
else, four restarts running, while the one line naming the cause went
unwritten. These tests exist so it cannot quietly stop running again.
"""
import os


import pytest

from jarvis import preflight
from jarvis import server


def _check(name, status, message="x", remedy="do the thing"):
    return preflight.Check(name=name, status=status, message=message,
                           remedy=None if status == preflight.STATUS_OK else remedy)


class _Speech:
    def __init__(self):
        self.said = []

    async def say(self, text, *a, **k):
        self.said.append(text)


@pytest.mark.asyncio
async def test_a_failing_check_is_logged_with_its_remedy_and_not_spoken(monkeypatch, caplog):
    """Startup is never narrated: a failure is on the Diagnostics tab and in
    the log with its remedy, and an expired login is the brain's own
    attention item and a failed reply's reason, never a line spoken."""
    checks = [_check("claude_login", preflight.STATUS_FAIL,
                     "claude is not logged in")]
    monkeypatch.setattr(preflight, "run_checks", lambda **k: _async(checks))
    sp = _Speech()
    monkeypatch.setattr(server, "speech", sp)

    with caplog.at_level("WARNING"):
        await server._run_preflight()

    assert sp.said == []
    assert "claude is not logged in" in caplog.text and "do the thing" in caplog.text


@pytest.mark.asyncio
async def test_all_ok_says_nothing(monkeypatch):
    monkeypatch.setattr(preflight, "run_checks",
                        lambda **k: _async([_check("fish_api_key", preflight.STATUS_OK)]))
    sp = _Speech()
    monkeypatch.setattr(server, "speech", sp)

    await server._run_preflight()

    assert sp.said == [], "a clean environment gets no announcement"


@pytest.mark.asyncio
async def test_a_warning_is_logged_but_not_spoken(monkeypatch):
    """An optional setting must never delay the greeting."""
    monkeypatch.setattr(preflight, "run_checks",
                        lambda **k: _async([_check("cross_session_inbound",
                                                   preflight.STATUS_WARN)]))
    sp = _Speech()
    monkeypatch.setattr(server, "speech", sp)

    await server._run_preflight()

    assert sp.said == []


@pytest.mark.asyncio
async def test_a_broken_check_never_costs_the_user_their_server(monkeypatch):
    """This runs inside lifespan. It must not be able to stop startup."""
    async def boom(**k):
        raise RuntimeError("keychain on fire")

    monkeypatch.setattr(preflight, "run_checks", boom)
    monkeypatch.setattr(server, "speech", _Speech())

    await server._run_preflight()   # must not raise


def test_lifespan_actually_calls_it():
    """The bug was never in preflight -- it was that nobody called it."""
    import inspect
    # The startup that needs the records runs once they are in the engine
    # (server._start_on_records), from lifespan at once or after the move.
    assert "_start_on_records()" in inspect.getsource(server.lifespan)
    src = inspect.getsource(server._start_on_records)
    assert "_run_preflight" in src, (
        "lifespan must run the preflight checks; they were dead code for a "
        "whole milestone because this line was missing")


async def _async(value):
    return value
