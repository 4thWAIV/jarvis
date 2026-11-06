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

# The cues and the pause as a user who has chosen none has them.
PAUSE = {"intervals": {"tentative": 1.0, "deciding": 0.75, "likely": 0.75, "send": 0.5},
         "schedule": {"tentative": 1.0, "deciding": 1.75, "likely": 2.5, "send": 3.0},
         "patience": 3.0}
CUES = {"cues": voice_control.CUE_DEFAULTS, "volume": voice_control.VOLUME_DEFAULT, **PAUSE}


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
    assert voice_control.read() == {**voice_control.DEFAULTS, "saved": False, "sounds": True, **CUES, "scope": "local"}


@pytest.mark.parametrize("stop,switch", [("keep", "continue"), ("keep", "stop"),
                                         ("discard", "continue"), ("discard", "stop")])
def test_each_of_the_four_combinations_is_saved_and_read_back(published, stop, switch):
    got = voice_control.change(stop=stop, switch=switch)
    assert got == {"stop": stop, "switch": switch, "saved": True, "sounds": True, **CUES, "scope": "local"}
    assert voice_control.read() == got
    assert published == [{"type": voice_control.FRAME}]


def test_changing_one_setting_keeps_the_other():
    voice_control.change(stop="discard", switch="continue")
    assert voice_control.change(switch="stop") == {
        "stop": "discard", "switch": "stop", "saved": True, "sounds": True, **CUES, "scope": "local"}


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
    ({"intervals": {}}, "invalid-interval"),
    ({"intervals": []}, "invalid-interval"),
    ({"intervals": {"tentative": 0.24}}, "invalid-interval"),
    ({"intervals": {"send": 5.01}}, "invalid-interval"),
    ({"intervals": {"likely": "1"}}, "invalid-interval"),
    ({"intervals": {"likely": True}}, "invalid-interval"),
    ({"intervals": {"patience": 3}}, "invalid-interval"),
    ({"intervals": {"tentative": 1.0, "late": 1.0}}, "invalid-interval"),
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
    assert voice_control.read() == {**voice_control.DEFAULTS, "saved": False, **CUES, "scope": "member"}
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
        "stop": "discard", "switch": voice_control.DEFAULTS["switch"], "saved": True, "sounds": True, **CUES, "scope": "local"}
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


def test_the_defaults_total_is_the_scheduler_s_patience():
    assert END_OF_TURN_SEC == voice_control.patience() == 3.0


def test_the_patience_is_the_total_of_the_intervals_and_keeps_the_other_settings():
    voice_control.change(stop="discard")
    assert voice_control.read()["patience"] == 3.0                  # not saved by the change
    got = voice_control.change(intervals={"tentative": 2, "send": 1.25})
    assert got["stop"] == "discard"
    assert got["intervals"] == {"tentative": 2.0, "deciding": 0.75, "likely": 0.75, "send": 1.25}
    assert got["schedule"] == {"tentative": 2.0, "deciding": 2.75, "likely": 3.5, "send": 4.75}
    assert got["patience"] == 4.75
    voice_control.change(switch="continue", intervals={"deciding": 0.25})
    assert voice_control.patience() == 4.25
    assert voice_control.read()["intervals"]["tentative"] == 2.0      # merged, not replaced


@pytest.mark.parametrize("seconds,ok", [(0.25, True), (5, True), (0.2499, False), (5.001, False)])
def test_each_interval_s_bounds_are_taken_at_both_ends(seconds, ok):
    for stage in voice_control.PAUSE_STAGES:
        if ok:
            assert voice_control.change(intervals={stage: seconds})["intervals"][stage] == seconds
        else:
            with pytest.raises(voice_control.Invalid):
                voice_control.change(intervals={stage: seconds})


def test_every_choice_of_intervals_keeps_the_stages_in_order_before_the_send():
    import itertools
    for chosen in itertools.product((0.25, 1.3, 5.0), repeat=4):
        got = voice_control.change(intervals=dict(zip(voice_control.PAUSE_STAGES, chosen)))
        times = [got["schedule"][s] for s in voice_control.PAUSE_STAGES]
        assert 0 < times[0] < times[1] < times[2] < times[3] == got["patience"]


def test_patience_falls_back_to_the_defaults_total_when_the_store_cannot_be_read(monkeypatch):
    voice_control.change(intervals={"send": 4})
    monkeypatch.setattr(run_store, "_connect", lambda *_a, **_k: (_ for _ in ()).throw(
        sqlite3.OperationalError("locked")))
    assert voice_control.patience() == 3.0


# ── capture sounds ───────────────────────────────────────────────────────

def test_capture_sounds_are_on_until_he_turns_them_off_and_keep_the_other_settings():
    assert voice_control.read()["sounds"] is True
    voice_control.change(stop="discard", intervals={"send": 1.5})
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
    """A JARVIS updated from schema 2: his saved choices stay, capture
    sounds read as the default, and his patience saved on its own is
    dropped for the default intervals' total (migration 7)."""
    with run_store._connect() as conn:
        run_store.migrate(conn, "voice_control_schema",
                          {k: v for k, v in voice_control._MIGRATIONS.items() if k < 3}, "voice control")
        conn.execute("INSERT INTO voice_input_settings (user_key, stop_mode, switch_mode, patience_sec, "
                     "updated_at) VALUES ('local', 'discard', 'continue', 3, 0)")
    got = voice_control.read()
    assert (got["stop"], got["switch"], got["sounds"]) == ("discard", "continue", True)
    assert {k: got[k] for k in PAUSE} == PAUSE


# ── the capture cues ─────────────────────────────────────────────────────

def test_each_cue_is_changed_alone_and_merged_with_what_he_chose_before(published):
    voice_control.change(cues={"tentative": {"tone": "low"}})
    got = voice_control.change(cues={"tentative": {"on": False}, "start": {"tone": "double"}})
    assert got["cues"]["tentative"] == {"on": False, "tone": "low"}
    assert got["cues"]["start"] == {"on": True, "tone": "double"}
    assert got["cues"]["stop"] == voice_control.CUE_DEFAULTS["stop"]
    assert voice_control.read()["cues"] == got["cues"]
    assert len(published) == 2


def test_the_volume_and_the_intervals_are_saved_and_keep_the_other_settings():
    voice_control.change(stop="discard", sounds=False)
    got = voice_control.change(volume=30, intervals={"likely": 2, "tentative": 0.5})
    assert (got["volume"], got["stop"], got["sounds"]) == (30, "discard", False)
    assert got["schedule"] == {"tentative": 0.5, "deciding": 1.25, "likely": 3.25, "send": 3.75}
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
    ({"cues": {"likely": {"percent": 50}}}, "invalid-cue"),  # shares of patience are gone
    ({"cues": {"likely": {"before": 1.0}}}, "invalid-cue"),  # seconds before are gone
    ({"cues": {"send": {"on": False}}}, "invalid-cue"),      # the send is no cue
    ({"cues": {"deciding": {"tone": "siren"}}}, "invalid-cue"),
])
def test_a_bad_cue_change_is_refused_and_nothing_is_saved(published, change, code):
    with pytest.raises(voice_control.Invalid) as refused:
        voice_control.change(**change)
    assert refused.value.code == code
    assert voice_control.read()["saved"] is False and published == []


def test_a_cue_that_is_off_keeps_its_interval_and_every_stage_is_still_timed():
    """Off, a cue is silent, but its interval still passes: the schedule
    (and so the send) is the same, and the turn controller is still given
    the stage."""
    on = voice_control.read()
    got = voice_control.change(cues={"deciding": {"on": False}})
    assert voice_control.schedule_of(got) == voice_control.schedule_of(on) == [
        ("tentative", 1.0), ("deciding", 1.75), ("likely", 2.5), ("send", 3.0)]
    assert got["patience"] == 3.0
    assert not voice_control.cue_sounds(got, "deciding")


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
        True, True, True, True, False, True]
    assert "deciding" in dict(voice_control.schedule_of(settings))


def test_saved_cues_jarvis_no_longer_takes_read_as_the_defaults():
    voice_control.change(stop="keep")
    with run_store._connect() as conn:
        conn.execute("UPDATE voice_input_settings SET cue_settings = ?, cue_volume = 900",
                     ('{"start": {"tone": "siren"}, "stop": {"on": false}, '
                      '"tentative": {"percent": 80}}',))
        conn.execute("UPDATE voice_input_settings SET pause_intervals = ?",
                     ('{"tentative": 9, "deciding": "x", "likely": 1.5, "late": 2}',))
    got = voice_control.read()
    assert got["cues"]["start"] == voice_control.CUE_DEFAULTS["start"]
    assert got["cues"]["stop"]["on"] is False
    assert got["cues"]["tentative"] == voice_control.CUE_DEFAULTS["tentative"]
    # Each interval JARVIS does not take is the default; the one it takes stays.
    assert got["intervals"] == {"tentative": 1.0, "deciding": 0.75, "likely": 1.5, "send": 0.5}
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
    r = client.post(voice_control.PATH, json={"intervals": {"send": 1}})
    assert r.status_code == 200 and r.json()["patience"] == 3.5
    r = client.post(voice_control.PATH, json={"intervals": {"send": 9}})
    assert (r.status_code, r.json()["code"]) == (400, "invalid-interval")


def _a_row_from_schema(version, cue_settings, patience=1.5):
    """A row saved at `version`, read now (every later migration run)."""
    with run_store._connect() as conn:
        run_store.migrate(conn, "voice_control_schema",
                          {k: v for k, v in voice_control._MIGRATIONS.items() if k <= version},
                          "voice control")
        conn.execute("INSERT INTO voice_input_settings (user_key, stop_mode, switch_mode, patience_sec, "
                     "cue_settings, updated_at) VALUES ('local', 'keep', 'stop', ?, ?, 0)",
                     (patience, cue_settings))
    return voice_control.read()


@pytest.mark.parametrize("version,saved,patience", [
    (4, None, 1.5), (4, '{"start": {"tone": "double"}}', 4),
    (4, '{"tentative": {"before": 3, "tone": "low"}, "likely": {"before": 0.5}}', 1.5),
    (5, '{"tentative": {"before": 2.5, "tone": "double", "on": false}, '
        '"deciding": {"before": 1.75, "tone": "rise"}, "likely": {"before": 1.0}}', 1.5),
    (6, '{"tentative": {"percent": 40, "tone": "double", "on": false}, '
        '"deciding": {"percent": 65, "tone": "rise"}, "likely": {"percent": 85}}', 1.5),
    (6, '{"tentative": {"percent": 20, "tone": "low"}}', 10),
    (6, 'not json', 1.5),
])
def test_old_timings_and_a_patience_saved_alone_read_as_the_default_intervals(
        version, saved, patience):
    """Migration 7 (after 6 for seconds before): a patience saved on its own
    and the cues' shares of it are dropped, so the row reads the default
    intervals, a total of 3.0 s; each cue keeps its tone and on or off."""
    got = _a_row_from_schema(version, saved, patience)
    assert {k: got[k] for k in PAUSE} == PAUSE
    assert all(set(got["cues"][n]) == {"on", "tone"} for n in voice_control.CUES)
    if saved and '"tone": "low"' in saved:
        assert got["cues"]["tentative"]["tone"] == "low"
    if saved and '"on": false' in saved:
        assert (got["cues"]["tentative"]["on"], got["cues"]["tentative"]["tone"]) == (False, "double")
        assert got["cues"]["deciding"]["tone"] == "rise"
    with run_store._connect() as conn:
        raw, kept = conn.execute("SELECT cue_settings, patience_sec FROM voice_input_settings").fetchone()
    assert kept is None and (raw is None or ("before" not in raw and "percent" not in raw))
    got = voice_control.change(intervals={"likely": 1})        # a change saves cleanly after it
    assert got["patience"] == 3.25


def test_the_brain_s_schema_offers_the_same_cues_and_tones():
    from jarvis import jarvis_mcp
    assert jarvis_mcp._CUE_TONES == voice_control.TONES
    assert jarvis_mcp._PAUSE_CUES == voice_control.PAUSE_CUES
    assert jarvis_mcp._CUE_NAMES == voice_control.CUES
    spec = next(t for t in jarvis_mcp.TOOL_SPECS if t["name"] == "set_voice_input_settings")
    cues = spec["inputSchema"]["properties"]["cues"]["properties"]
    assert tuple(cues) == voice_control.CUES
    assert all(set(cues[name]["properties"]) == {"on", "tone"} for name in voice_control.CUES)
    assert "patience" not in spec["inputSchema"]["properties"]
    intervals = spec["inputSchema"]["properties"]["intervals"]["properties"]
    assert tuple(intervals) == voice_control.PAUSE_STAGES
    assert all((i["type"], i["minimum"], i["maximum"]) == (
        "number", voice_control.INTERVAL_MIN, voice_control.INTERVAL_MAX) for i in intervals.values())
    volume = spec["inputSchema"]["properties"]["volume"]
    assert (volume["minimum"], volume["maximum"]) == (voice_control.VOLUME_MIN,
                                                      voice_control.VOLUME_MAX)
    play = next(t for t in jarvis_mcp.TOOL_SPECS if t["name"] == "play_cue")
    assert tuple(play["inputSchema"]["properties"]["cue"]["enum"]) == voice_control.CUES


def test_the_speech_heard_cue_is_its_own_setting_and_off_silences_only_it():
    assert voice_control.CUES == ("start", "stop", "heard", "tentative", "deciding", "likely")
    assert voice_control.read()["cues"]["heard"] == {"on": True, "tone": "double"}
    others = {voice_control.CUE_DEFAULTS[n]["tone"] for n in voice_control.CUES if n != "heard"}
    assert "double" not in others, "its default tone is used by no other cue"
    settings = voice_control.change(cues={"heard": {"on": False}})
    assert [n for n in voice_control.CUES if not voice_control.cue_sounds(settings, n)] == ["heard"]
    settings = voice_control.change(cues={"heard": {"on": True, "tone": "pulse"}})
    assert settings["cues"]["heard"] == {"on": True, "tone": "pulse"}
    with pytest.raises(voice_control.Invalid):
        voice_control.change(cues={"heard": {"tone": "siren"}})
