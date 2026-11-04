"""His two voice input settings (voice_control.py): the defaults until he
chooses, each choice kept per user in this JARVIS's database (the team
member signed in, else the local owner), every value checked the same way
for the route and the brain, a change published once and never otherwise,
and a store that cannot be read refused rather than guessed at."""

import sqlite3
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from jarvis import run_store, team_client, voice_control


@pytest.fixture
def published(monkeypatch):
    frames: list[dict] = []
    monkeypatch.setattr(voice_control, "_publish", frames.append)
    return frames


def _signed_in(monkeypatch, github_id=7, signed_out=False):
    monkeypatch.setattr(team_client, "connection", SimpleNamespace(
        signed_in=lambda: not signed_out,
        snapshot=lambda: {"state": "signed_out" if signed_out else "connected",
                          "me": {"github_id": github_id}}))


def test_nothing_chosen_reads_the_defaults_and_says_so():
    assert voice_control.read() == {**voice_control.DEFAULTS, "saved": False, "scope": "local"}


@pytest.mark.parametrize("stop,switch", [("keep", "continue"), ("keep", "stop"),
                                         ("discard", "continue"), ("discard", "stop")])
def test_each_of_the_four_combinations_is_saved_and_read_back(published, stop, switch):
    got = voice_control.change(stop=stop, switch=switch)
    assert got == {"stop": stop, "switch": switch, "saved": True, "scope": "local"}
    assert voice_control.read() == got
    assert published == [{"type": voice_control.FRAME}]


def test_changing_one_setting_keeps_the_other():
    voice_control.change(stop="discard", switch="continue")
    assert voice_control.change(switch="stop") == {
        "stop": "discard", "switch": "stop", "saved": True, "scope": "local"}


def test_a_default_never_overwrites_a_saved_choice(monkeypatch):
    voice_control.change(stop="discard", switch="continue")
    monkeypatch.setattr(voice_control, "DEFAULTS", {"stop": "keep", "switch": "stop"})
    assert voice_control.read()["stop"] == "discard"


@pytest.mark.parametrize("change,code", [
    ({}, "nothing-to-change"),
    ({"stop": "maybe"}, "invalid-stop"),
    ({"stop": 1}, "invalid-stop"),
    ({"switch": "pause"}, "invalid-switch"),
    ({"stop": "keep", "switch": ""}, "invalid-switch"),
])
def test_a_bad_change_is_refused_and_nothing_is_saved_or_published(published, change, code):
    with pytest.raises(voice_control.Invalid) as refused:
        voice_control.change(**change)
    assert refused.value.code == code
    assert voice_control.read()["saved"] is False
    assert published == []


def test_settings_are_per_user_never_shared(monkeypatch):
    voice_control.change(stop="discard", switch="continue")             # the local owner
    _signed_in(monkeypatch, github_id=7)
    assert voice_control.user_key() == "github:7"
    assert voice_control.read() == {**voice_control.DEFAULTS, "saved": False, "scope": "member"}
    voice_control.change(stop="keep", switch="stop")
    _signed_in(monkeypatch, github_id=8)
    assert voice_control.read()["saved"] is False
    _signed_in(monkeypatch, github_id=7)
    assert voice_control.read()["stop"] == "keep"
    _signed_in(monkeypatch, github_id=7, signed_out=True)
    assert voice_control.user_key() == voice_control.LOCAL_OWNER
    assert voice_control.read()["stop"] == "discard"


def test_a_store_that_cannot_be_read_is_refused_not_guessed(monkeypatch, published):
    def broken(*_a, **_k):
        raise sqlite3.OperationalError("disk I/O error")
    monkeypatch.setattr(run_store, "_connect", broken)
    with pytest.raises(voice_control.Unreadable):
        voice_control.read()
    with pytest.raises(voice_control.Unreadable):
        voice_control.change(stop="keep")
    assert published == []


def test_a_newer_schema_than_this_jarvis_is_unreadable():
    with run_store._connect() as conn:
        conn.execute("CREATE TABLE voice_control_schema (version INTEGER NOT NULL)")
        conn.execute("INSERT INTO voice_control_schema VALUES (99)")
    with pytest.raises(voice_control.Unreadable):
        voice_control.read()


@pytest.fixture
def client(published):
    app = FastAPI()
    voice_control.mount(app, publish=published.append)
    return TestClient(app)


def test_the_routes_read_and_change_with_the_same_answers(client, published):
    assert client.get(voice_control.PATH).json()["saved"] is False
    r = client.post(voice_control.PATH, json={"stop": "discard"})
    assert r.status_code == 200
    assert r.json() == voice_control.read() == {
        "stop": "discard", "switch": voice_control.DEFAULTS["switch"], "saved": True, "scope": "local"}
    assert published == [{"type": voice_control.FRAME}]


@pytest.mark.parametrize("body,code", [({}, "nothing-to-change"), ({"stop": 5}, "invalid-stop"),
                                       ({"switch": "later"}, "invalid-switch")])
def test_the_route_refuses_what_the_service_refuses(client, body, code):
    r = client.post(voice_control.PATH, json=body)
    assert (r.status_code, r.json()["code"]) == (400, code)


def test_the_route_says_unreadable_and_the_page_keeps_listening_off(client, monkeypatch):
    def broken(*_a, **_k):
        raise sqlite3.OperationalError("locked")
    monkeypatch.setattr(run_store, "_connect", broken)
    r = client.get(voice_control.PATH)
    assert (r.status_code, r.json()["code"]) == (503, "unreadable")
