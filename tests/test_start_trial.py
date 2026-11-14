"""Confirm or revert: a changed start setup is a trial until a page reaches
it, and goes back to the last setup that worked when none does in time
(start_settings, server._confirm_or_revert, web_auth.on_page_reached)."""
from __future__ import annotations

import argparse
import asyncio

import pytest

from jarvis import server, settings_service, start_settings, subsystem_diagnostics, web_auth

WORKED = {"host": "127.0.0.1", "port": 8401, "https": False, "allowed_origins": []}
TRIED = {"host": "127.0.0.1", "port": 8402, "https": False, "allowed_origins": []}


@pytest.fixture
def started(monkeypatch):
    """A start from Settings on TRIED, listening, after one that a page
    reached on WORKED; the clock, the look and the restart are fakes."""
    monkeypatch.setattr(settings_service, "_started", {})
    monkeypatch.setattr(settings_service, "_served", {})
    monkeypatch.setattr(settings_service, "_listening", None)
    monkeypatch.setattr(server, "SERVING_LOOK_SEC", 0.01)
    monkeypatch.setattr(server, "TRIAL_LOOK_SEC", 0.01)
    clock = [1_000_000.0]
    monkeypatch.setattr(start_settings, "now", lambda: clock[0])
    restarts: list[str] = []
    monkeypatch.setattr(server, "_restart_server", restarts.append)
    start_settings.remember_served(WORKED)
    settings_service.change("port", TRIED["port"])
    settings_service.change("https", False)
    settings_service.note_serving(TRIED, lambda: True, from_settings=True)
    monkeypatch.setattr(server, "_page_reached_event", None)
    return clock, restarts


def _guard_scope(origin=None, token=None) -> dict:
    headers = [(b"host", f"127.0.0.1:{web_auth.api_port()}".encode())]
    if origin:
        headers.append((b"origin", origin.encode()))
    if token:
        headers.append((b"authorization", f"Bearer {token}".encode()))
    return {"type": "websocket", "path": "/ws/sessions", "headers": headers}


async def _through_the_gate(scope: dict) -> bool:
    """Whether the gate let `scope` through to the app."""
    passed = []

    async def app(scope, receive, send):
        passed.append(True)

    async def receive():
        return {"type": "websocket.connect"}

    async def send(message):
        pass
    await web_auth.OriginGuard(app)(scope, receive, send)
    return bool(passed)


def _page_origin() -> str:
    return sorted(web_auth.allowed_origins())[0]


def test_a_trial_is_confirmed_by_a_page_connecting(started):
    clock, restarts = started

    async def scenario():
        server._page_reached_event = asyncio.Event()
        task = asyncio.create_task(server._confirm_or_revert(TRIED))
        await asyncio.sleep(0.1)
        assert start_settings.trial()["setup"] == TRIED
        assert settings_service.view("server")["start"]["trial"]["seconds_left"] == 60
        assert start_settings.document()["last_good"] == WORKED
        assert await _through_the_gate(_guard_scope(origin=_page_origin()))
        await asyncio.wait_for(task, 5)
    asyncio.run(scenario())
    assert start_settings.document()["last_good"] == TRIED
    assert start_settings.trial() is None and restarts == []


def test_a_trial_no_page_reaches_goes_back_and_restarts(started):
    clock, restarts = started

    async def scenario():
        server._page_reached_event = asyncio.Event()
        task = asyncio.create_task(server._confirm_or_revert(TRIED))
        await asyncio.sleep(0.1)
        clock[0] += start_settings.TRIAL_WINDOW_SEC - 1
        await asyncio.sleep(0.1)
        assert not task.done() and restarts == []
        clock[0] += 2
        await asyncio.wait_for(task, 5)
    asyncio.run(scenario())
    assert len(restarts) == 1 and "no page reached" in restarts[0]
    assert settings_service.value("port") == WORKED["port"]
    reverted = start_settings.document()["reverted"]
    assert reverted["why"] == start_settings.UNREACHED
    assert reverted["from"] == TRIED and reverted["to"] == WORKED
    # The fallback is still the setup a page reached: only a confirm writes it.
    assert start_settings.document()["last_good"] == WORKED
    assert start_settings.trial() is None
    # The start after it is on the setup that worked: no trial.
    assert start_settings.begin_trial(WORKED) is None
    said = server._start_trial_said(settings_service.view("server")["start"])
    assert any("went back to http://127.0.0.1:8401" in line for line in said)


def test_the_fallback_is_written_only_after_a_confirm(started):
    start_settings.begin_trial(TRIED)
    assert start_settings.document()["last_good"] == WORKED
    start_settings.fall_back(start_settings.UNREACHED)
    assert start_settings.document()["last_good"] == WORKED
    start_settings.begin_trial(TRIED)
    start_settings.remember_served(TRIED)
    assert start_settings.document()["last_good"] == TRIED


def test_no_setup_reached_yet_is_no_trial(monkeypatch):
    assert start_settings.document()["last_good"] is None
    assert start_settings.begin_trial(TRIED) is None


@pytest.mark.parametrize("flags", [dict(host="127.0.0.1", port=None, ssl=False),
                                   dict(host=None, port=8403, ssl=False),
                                   dict(host=None, port=None, ssl=True)])
def test_command_line_overrides_never_start_a_trial(started, flags, monkeypatch):
    monkeypatch.setattr(start_settings, "bindable", lambda host, port: True)
    assert server._apply_start_settings(argparse.Namespace(**flags)) is False
    settings_service.note_serving(TRIED, lambda: True, from_settings=False)
    assert settings_service.from_settings_at_start() is False
    assert settings_service.view("server")["start"]["overridden"] is True
    assert server._apply_start_settings(argparse.Namespace(host=None, port=None, ssl=False))


def test_the_tool_token_alone_never_confirms(started):
    token = server.data_paths.ensure_tool_token()

    async def scenario():
        server._page_reached_event = asyncio.Event()
        assert await _through_the_gate(_guard_scope(token=token))
        assert await _through_the_gate(_guard_scope(origin=_page_origin(), token=token))
        assert not server._page_reached_event.is_set()
        assert await _through_the_gate(_guard_scope(origin=_page_origin()))
        assert server._page_reached_event.is_set()
    asyncio.run(scenario())


def test_diagnostics_shows_the_trial_and_going_back(started):
    clock, _ = started
    start_settings.begin_trial(TRIED)
    row = subsystem_diagnostics.start_setup(
        {**start_settings.status(), "from_file": True, "overridden": False}, clock[0])
    assert row.state == subsystem_diagnostics.OK and "Trying http://127.0.0.1:8402" in row.summary
    start_settings.fall_back(start_settings.UNREACHED)
    clock[0] += 120
    row = subsystem_diagnostics.start_setup(
        {**start_settings.status(), "from_file": True, "overridden": False}, clock[0])
    assert row.state == subsystem_diagnostics.DEGRADED
    assert "No page reached http://127.0.0.1:8402" in row.summary
