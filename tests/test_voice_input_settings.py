"""His voice input settings (voice_control.py): the defaults until he
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
from jarvis.speech import END_OF_TURN_SEC

# The cues as a user who has chosen none has them.
CUES = {"cues": voice_control.CUE_DEFAULTS, "volume": voice_control.VOLUME_DEFAULT}


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
    assert voice_control.read() == {**voice_control.DEFAULTS, "saved": False, "patience": END_OF_TURN_SEC, "sounds": True, **CUES, "scope": "local"}


@pytest.mark.parametrize("stop,switch", [("keep", "continue"), ("keep", "stop"),
                                         ("discard", "continue"), ("discard", "stop")])
def test_each_of_the_four_combinations_is_saved_and_read_back(published, stop, switch):
    got = voice_control.change(stop=stop, switch=switch)
    assert got == {"stop": stop, "switch": switch, "saved": True, "patience": END_OF_TURN_SEC, "sounds": True, **CUES, "scope": "local"}
    assert voice_control.read() == got
    assert published == [{"type": voice_control.FRAME}]


def test_changing_one_setting_keeps_the_other():
    voice_control.change(stop="discard", switch="continue")
    assert voice_control.change(switch="stop") == {
        "stop": "discard", "switch": "stop", "saved": True, "patience": END_OF_TURN_SEC, "sounds": True, **CUES, "scope": "local"}


def test_a_default_never_overwrites_a_saved_choice(monkeypatch):
    voice_control.change(stop="discard", switch="continue")
    monkeypatch.setattr(voice_control, "DEFAULTS", {"stop": "keep", "switch": "stop", "sounds": True})
    assert voice_control.read()["stop"] == "discard"


@pytest.mark.parametrize("change,code", [
    ({}, "nothing-to-change"),
    ({"stop": "maybe"}, "invalid-stop"),
    ({"stop": 1}, "invalid-stop"),
    ({"switch": "pause"}, "invalid-switch"),
    ({"stop": "keep", "switch": ""}, "invalid-switch"),
    ({"patience": 0.1}, "invalid-patience"),
    ({"patience": 11}, "invalid-patience"),
    ({"patience": "4"}, "invalid-patience"),
    ({"patience": True}, "invalid-patience"),
    ({"sounds": "off"}, "invalid-sounds"),
    ({"sounds": 0}, "invalid-sounds"),
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
    assert voice_control.read() == {**voice_control.DEFAULTS, "saved": False, "patience": END_OF_TURN_SEC, **CUES, "scope": "member"}
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
        "stop": "discard", "switch": voice_control.DEFAULTS["switch"], "saved": True, "patience": END_OF_TURN_SEC, "sounds": True, **CUES, "scope": "local"}
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


def test_patience_is_one_saved_value_that_keeps_the_other_settings_and_falls_back():
    assert voice_control.patience() == END_OF_TURN_SEC
    voice_control.change(stop="discard")
    assert voice_control.read()["patience"] == END_OF_TURN_SEC      # not saved by the change
    got = voice_control.change(patience=4)
    assert (got["stop"], got["patience"]) == ("discard", 4.0)
    voice_control.change(switch="continue")
    assert voice_control.patience() == 4.0


def test_patience_falls_back_to_the_constant_when_the_store_cannot_be_read(monkeypatch):
    voice_control.change(patience=4)
    monkeypatch.setattr(run_store, "_connect", lambda *_a, **_k: (_ for _ in ()).throw(
        sqlite3.OperationalError("locked")))
    assert voice_control.patience() == END_OF_TURN_SEC


# ── capture sounds ───────────────────────────────────────────────────────

def test_capture_sounds_are_on_until_he_turns_them_off_and_keep_the_other_settings():
    assert voice_control.read()["sounds"] is True
    voice_control.change(stop="discard", patience=4)
    assert voice_control.read()["sounds"] is True                # not saved by another change
    got = voice_control.change(sounds=False)
    assert (got["sounds"], got["stop"], got["patience"], got["saved"]) == (False, "discard", 4.0, True)
    voice_control.change(switch="continue")
    assert voice_control.read()["sounds"] is False
    assert voice_control.change(sounds=True)["sounds"] is True


def test_capture_sounds_are_per_user(monkeypatch):
    voice_control.change(sounds=False)                           # the local owner
    _signed_in(monkeypatch, github_id=7)
    assert voice_control.read()["sounds"] is True
    _signed_in(monkeypatch, github_id=7, signed_out=True)
    assert voice_control.read()["sounds"] is False


def test_the_route_changes_capture_sounds_and_publishes_once(client, published):
    r = client.post(voice_control.PATH, json={"sounds": False})
    assert r.status_code == 200 and r.json()["sounds"] is False
    assert client.get(voice_control.PATH).json()["sounds"] is False
    assert published == [{"type": voice_control.FRAME}]
    r = client.post(voice_control.PATH, json={"sounds": "no"})
    assert (r.status_code, r.json()["code"]) == (400, "invalid-sounds")


def test_a_row_saved_before_capture_sounds_existed_reads_them_on():
    """A JARVIS updated from schema 2: his saved choices stay, and capture
    sounds read as the default."""
    with run_store._connect() as conn:
        run_store.migrate(conn, "voice_control_schema",
                          {k: v for k, v in voice_control._MIGRATIONS.items() if k < 3}, "voice control")
        conn.execute("INSERT INTO voice_input_settings (user_key, stop_mode, switch_mode, patience_sec, "
                     "updated_at) VALUES ('local', 'discard', 'continue', 3, 0)")
    got = voice_control.read()
    assert (got["stop"], got["switch"], got["patience"], got["sounds"]) == ("discard", "continue", 3.0, True)


# ── the capture cues ─────────────────────────────────────────────────────

PERCENTS = lambda got: [got["cues"][n]["percent"] for n in voice_control.PAUSE_CUES]  # noqa: E731


@pytest.mark.parametrize("patience,seconds", [(1.5, [0.6, 0.98, 1.28]), (4, [1.6, 2.6, 3.4])])
def test_the_default_pause_cues_all_play_in_order_before_the_send_at_any_patience(patience, seconds):
    """40, 65 and 85 percent of his patience, the send at 100: at his 1.5 s
    all three play, as at 4."""
    got = voice_control.change(patience=patience)
    assert PERCENTS(got) == [40, 65, 85]
    assert voice_control.pause_cue_shares(got) == [("tentative", 0.4), ("deciding", 0.65),
                                                  ("likely", 0.85)]
    assert [voice_control.pause_cue_at(got, n) for n in voice_control.PAUSE_CUES] == seconds
    assert got["cues"]["deciding"] == {"on": True, "tone": "low", "percent": 65}


def test_each_cue_is_changed_alone_and_merged_with_what_he_chose_before(published):
    voice_control.change(cues={"tentative": {"tone": "low"}})
    got = voice_control.change(cues={"tentative": {"on": False}, "start": {"tone": "double"}})
    assert got["cues"]["tentative"] == {"on": False, "tone": "low", "percent": 40}
    assert got["cues"]["start"] == {"on": True, "tone": "double"}
    assert got["cues"]["stop"] == voice_control.CUE_DEFAULTS["stop"]
    assert voice_control.read()["cues"] == got["cues"]
    assert len(published) == 2


def test_the_volume_and_the_timings_are_saved_and_keep_the_other_settings():
    voice_control.change(stop="discard", sounds=False)
    got = voice_control.change(volume=30, cues={"likely": {"percent": 90}, "tentative": {"percent": 20}})
    assert (got["volume"], got["stop"], got["sounds"]) == (30, "discard", False)
    assert PERCENTS(got) == [20, 65, 90]
    assert voice_control.change(switch="continue")["volume"] == 30


@pytest.mark.parametrize("change,code", [
    ({"volume": 2}, "invalid-volume"),
    ({"volume": 101}, "invalid-volume"),
    ({"volume": True}, "invalid-volume"),
    ({"cues": {}}, "invalid-cue"),
    ({"cues": []}, "invalid-cue"),
    ({"cues": {"echo": {"on": False}}}, "invalid-cue"),
    ({"cues": {"start": {}}}, "invalid-cue"),
    ({"cues": {"start": {"on": "no"}}}, "invalid-cue"),
    ({"cues": {"start": {"tone": "siren"}}}, "invalid-cue"),
    ({"cues": {"start": {"percent": 50}}}, "invalid-cue"),  # only a pause cue is timed
    ({"cues": {"likely": {"before": 1.0}}}, "invalid-cue"),  # seconds before are gone
    ({"cues": {"likely": {"percent": 4}}}, "invalid-cue"),
    ({"cues": {"likely": {"percent": 96}}}, "invalid-cue"),  # never level with the send
    ({"cues": {"likely": {"percent": 100}}}, "invalid-cue"),
    ({"cues": {"likely": {"percent": 87.5}}}, "invalid-cue"),  # whole percent
    ({"cues": {"likely": {"percent": "90"}}}, "invalid-cue"),
    ({"cues": {"likely": {"percent": True}}}, "invalid-cue"),
    ({"cues": {"likely": {"percent": 40}}}, "invalid-cue-timing"),  # level with the tentative
    ({"cues": {"likely": {"percent": 69}}}, "invalid-cue-timing"),  # within 5 of deciding
    ({"cues": {"tentative": {"percent": 61}}}, "invalid-cue-timing"),  # within 5 of deciding
    ({"cues": {"tentative": {"percent": 90}}}, "invalid-cue-timing"),  # after the likely
    ({"cues": {"deciding": {"percent": 44}}}, "invalid-cue-timing"),
    ({"cues": {"deciding": {"percent": 30}}}, "invalid-cue-timing"),  # before the tentative
    ({"cues": {"deciding": {"percent": 81}}}, "invalid-cue-timing"),
    ({"cues": {"deciding": {"percent": 95}}}, "invalid-cue-timing"),  # after the likely
    ({"cues": {"deciding": {"tone": "siren"}}}, "invalid-cue"),
])
def test_a_bad_cue_change_is_refused_and_nothing_is_saved(published, change, code):
    with pytest.raises(voice_control.Invalid) as refused:
        voice_control.change(**change)
    assert refused.value.code == code
    assert voice_control.read()["saved"] is False and published == []


@pytest.mark.parametrize("name,low,high", [("tentative", 5, 60), ("deciding", 45, 80),
                                           ("likely", 70, 95)])
def test_each_pause_cue_s_bounds_are_its_neighbours_and_both_ends_are_taken(name, low, high):
    """The service's bounds are exactly what it takes: each end is accepted,
    one point past either is refused."""
    assert voice_control.pause_cue_bounds(voice_control.read()["cues"], name) == (low, high)
    for percent, ok in ((low, True), (high, True), (low - 1, False), (high + 1, False)):
        if ok:
            assert voice_control.change(cues={name: {"percent": percent}})["cues"][name]["percent"] == percent
            voice_control.change(cues={name: {"percent": voice_control.CUE_DEFAULTS[name]["percent"]}})
        else:
            with pytest.raises(voice_control.Invalid):
                voice_control.change(cues={name: {"percent": percent}})


def test_the_refusal_says_the_bounds_he_can_choose():
    with pytest.raises(voice_control.Invalid) as refused:
        voice_control.change(cues={"deciding": {"percent": 90}})
    assert "deciding 45 to 80" in refused.value.message


def test_moving_the_timings_at_once_is_judged_together():
    with pytest.raises(voice_control.Invalid) as refused:   # the deciding cue left behind
        voice_control.change(cues={"tentative": {"percent": 70}, "likely": {"percent": 90}})
    assert refused.value.code == "invalid-cue-timing"
    got = voice_control.change(cues={"tentative": {"percent": 70}, "deciding": {"percent": 80},
                                     "likely": {"percent": 90}})
    assert PERCENTS(got) == [70, 80, 90]


def test_a_cue_that_is_off_is_skipped_and_does_not_constrain_the_order(published):
    """Off, the deciding cue is not timed, holds nothing in place and may be
    anywhere; turning it back on where it would cross is refused."""
    got = voice_control.change(cues={"deciding": {"on": False}})
    assert voice_control.pause_cue_shares(got) == [("tentative", 0.4), ("likely", 0.85)]
    assert voice_control.pause_cue_bounds(got["cues"], "tentative") == (5, 80)
    got = voice_control.change(cues={"tentative": {"percent": 70}, "deciding": {"percent": 20}})
    assert PERCENTS(got) == [70, 20, 85]
    with pytest.raises(voice_control.Invalid) as refused:
        voice_control.change(cues={"deciding": {"on": True}})
    assert refused.value.code == "invalid-cue-timing"
    assert voice_control.read()["cues"]["deciding"]["on"] is False
    got = voice_control.change(cues={"deciding": {"on": True, "percent": 78}})
    assert PERCENTS(got) == [70, 78, 85] and got["cues"]["deciding"]["on"] is True
    assert len(published) == 3


def test_each_gap_between_the_pause_cues_is_in_seconds_at_his_patience():
    """Each gap is set by the two cues' own shares, and a cue that is off is
    skipped when finding a neighbour."""
    got = voice_control.change(patience=4, cues={"tentative": {"percent": 20}})
    gaps = {n: voice_control.pause_cue_gaps(got, n) for n in voice_control.PAUSE_CUES}
    assert gaps["tentative"] == {"after": None, "until": ("deciding", 1.8)}
    assert gaps["deciding"] == {"after": ("tentative", 1.8), "until": ("likely", 0.8)}
    assert gaps["likely"] == {"after": ("deciding", 0.8), "until": (None, 0.6)}
    got = voice_control.change(cues={"deciding": {"on": False}})
    assert voice_control.pause_cue_gaps(got, "tentative")["until"] == ("likely", 2.6)
    assert voice_control.pause_cue_gaps(got, "likely")["after"] == ("tentative", 2.6)


def test_changing_his_patience_moves_every_cue_with_it_and_never_out_of_order():
    for patience in (0.5, 1.5, 4, 10):
        got = voice_control.change(patience=patience)
        at = [voice_control.pause_cue_at(got, n) for n in voice_control.PAUSE_CUES]
        assert 0 < at[0] < at[1] < at[2] < patience


def test_cue_sounds_need_the_master_switch_and_the_cue():
    settings = voice_control.read()
    assert voice_control.cue_sounds(settings, "likely")
    settings = voice_control.change(cues={"likely": {"on": False}})
    assert not voice_control.cue_sounds(settings, "likely") and voice_control.cue_sounds(settings, "tentative")
    settings = voice_control.change(sounds=False, cues={"likely": {"on": True}})
    assert not voice_control.cue_sounds(settings, "likely")


def test_the_deciding_cue_off_silences_only_it():
    settings = voice_control.change(cues={"deciding": {"on": False}})
    assert [voice_control.cue_sounds(settings, n) for n in voice_control.CUES] == [
        True, True, True, False, True]
    assert "deciding" not in dict(voice_control.pause_cue_shares(settings))


def test_saved_cues_jarvis_no_longer_takes_read_as_the_defaults():
    voice_control.change(stop="keep")
    with run_store._connect() as conn:
        conn.execute("UPDATE voice_input_settings SET cue_settings = ?, cue_volume = 900",
                     ('{"start": {"tone": "siren"}, "stop": {"on": false}, '
                      '"tentative": {"percent": 80}, "likely": {"percent": 50}}',))
    got = voice_control.read()
    assert got["cues"]["start"] == voice_control.CUE_DEFAULTS["start"]
    assert got["cues"]["stop"]["on"] is False
    # Out of order: every pause timing is the default.
    assert PERCENTS(got) == [40, 65, 85]
    assert got["volume"] == voice_control.VOLUME_DEFAULT


def test_the_voice_path_reads_the_defaults_when_the_store_cannot_be_read(monkeypatch):
    voice_control.change(volume=20)
    monkeypatch.setattr(run_store, "_connect", lambda *_a, **_k: (_ for _ in ()).throw(
        sqlite3.OperationalError("locked")))
    assert voice_control.read_or_defaults() == voice_control.defaults()


def test_the_route_changes_the_cues_and_refuses_what_the_service_refuses(client, published):
    r = client.post(voice_control.PATH, json={"cues": {"stop": {"tone": "low"}}, "volume": 40})
    assert r.status_code == 200
    assert (r.json()["cues"]["stop"]["tone"], r.json()["volume"]) == ("low", 40)
    assert client.get(voice_control.PATH).json() == r.json()
    r = client.post(voice_control.PATH, json={"cues": {"likely": {"percent": 60}}})
    assert (r.status_code, r.json()["code"]) == (400, "invalid-cue-timing")


def _a_row_from_schema(version, cue_settings):
    """A row saved at `version`, read now (every later migration run)."""
    with run_store._connect() as conn:
        run_store.migrate(conn, "voice_control_schema",
                          {k: v for k, v in voice_control._MIGRATIONS.items() if k <= version},
                          "voice control")
        conn.execute("INSERT INTO voice_input_settings (user_key, stop_mode, switch_mode, patience_sec, "
                     "cue_settings, updated_at) VALUES ('local', 'keep', 'stop', 1.5, ?, 0)", (cue_settings,))
    return voice_control.read()


@pytest.mark.parametrize("version,saved", [
    (4, None), (4, '{"start": {"tone": "double"}}'),
    (4, '{"tentative": {"before": 3, "tone": "low"}, "likely": {"before": 0.5}}'),
    (4, '{"tentative": {"before": 1.5}}'),
    (4, '{"likely": {"before": "x"}}'),
    (5, '{"tentative": {"before": 2.5, "tone": "double", "on": false}, '
        '"deciding": {"before": 1.75, "tone": "rise"}, "likely": {"before": 1.0}}'),
    (5, 'not json'),
])
def test_timings_saved_as_seconds_before_read_as_the_default_shares_keeping_tone_and_on(
        version, saved):
    """Migration 6: a lead in seconds cannot be a share, so each is dropped
    and those cues read with the default shares; tone and on or off stay."""
    got = _a_row_from_schema(version, saved)
    assert PERCENTS(got) == [40, 65, 85]
    assert all("before" not in got["cues"][n] for n in voice_control.PAUSE_CUES)
    if saved and '"tone": "low"' in saved:
        assert got["cues"]["tentative"]["tone"] == "low"
    if version == 5 and saved.startswith("{"):
        assert (got["cues"]["tentative"]["on"], got["cues"]["tentative"]["tone"]) == (False, "double")
        assert got["cues"]["deciding"]["tone"] == "rise"
    with run_store._connect() as conn:
        raw = conn.execute("SELECT cue_settings FROM voice_input_settings").fetchone()[0]
    assert raw is None or "before" not in raw
    voice_control.change(cues={"likely": {"percent": 90}})   # a change saves cleanly after it
    assert PERCENTS(voice_control.read()) == [40, 65, 90]


def test_the_brain_s_schema_offers_the_same_cues_and_tones():
    from jarvis import jarvis_mcp
    assert jarvis_mcp._CUE_TONES == voice_control.TONES
    assert jarvis_mcp._PAUSE_CUES == voice_control.PAUSE_CUES
    assert jarvis_mcp._CUE_NAMES == voice_control.CUES
    spec = next(t for t in jarvis_mcp.TOOL_SPECS if t["name"] == "set_voice_input_settings")
    cues = spec["inputSchema"]["properties"]["cues"]["properties"]
    assert tuple(cues) == voice_control.CUES
    for name in voice_control.PAUSE_CUES:
        percent = cues[name]["properties"]["percent"]
        assert (percent["type"], percent["minimum"], percent["maximum"]) == (
            "integer", voice_control.PERCENT_MIN, voice_control.PERCENT_MAX)
    volume = spec["inputSchema"]["properties"]["volume"]
    assert (volume["minimum"], volume["maximum"]) == (voice_control.VOLUME_MIN,
                                                      voice_control.VOLUME_MAX)
    play = next(t for t in jarvis_mcp.TOOL_SPECS if t["name"] == "play_cue")
    assert tuple(play["inputSchema"]["properties"]["cue"]["enum"]) == voice_control.CUES
