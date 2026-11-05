"""human_activity: what counts as a person using JARVIS, and what never does."""
import importlib
import json
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent))

from tests.test_voice_ws import FakeBrain, _drain_until

PAGE = {"Origin": "http://localhost:5173"}


class Clock:
    def __init__(self, now=100.0):
        self.now = now

    def __call__(self):
        return self.now


@pytest.fixture
def data_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    from jarvis import data_paths
    importlib.reload(data_paths)
    return tmp_path


def _record(data_dir: Path) -> dict:
    return json.loads((data_dir / "human-activity.json").read_text())


# -- the recorder ----------------------------------------------------------------

def test_nothing_observed_is_none_not_now(data_dir):
    from jarvis.human_activity import HumanActivity
    assert HumanActivity().current() is None


def test_an_observation_is_a_sequence_number_an_age_and_a_source(data_dir):
    from jarvis.human_activity import HumanActivity
    clock = Clock()
    rec = HumanActivity(clock=clock, wall=lambda: 1_800_000_000.0)
    assert rec.observe("gui") == 0
    clock.now += 2.5
    assert rec.current() == {"activity_seq": 0, "age_ms": 2500, "source": "gui"}
    assert rec.observe("speech") == 1
    assert rec.current() == {"activity_seq": 1, "age_ms": 0, "source": "speech"}
    assert _record(data_dir) == {"activity_seq": 1, "observed_at": 1_800_000_000.0,
                                 "source": "speech"}


def test_the_sequence_keeps_rising_across_a_restart_and_nothing_is_replayed(data_dir):
    from jarvis.human_activity import HumanActivity
    first = HumanActivity()
    first.observe("typed"); first.observe("gui")
    restarted = HumanActivity()
    assert restarted.current() is None, "an earlier run's activity is never sent again"
    assert restarted.observe("gui") == 2


def test_older_than_a_day_is_not_sent(data_dir):
    from jarvis.human_activity import HumanActivity
    clock = Clock()
    rec = HumanActivity(clock=clock)
    rec.observe("gui")
    clock.now += 86_400.001
    assert rec.current() is None


def test_an_unknown_source_is_refused(data_dir):
    from jarvis.human_activity import HumanActivity
    with pytest.raises(ValueError):
        HumanActivity().observe("agent")


# -- the server: the page's route and the voice socket ---------------------------

@pytest.fixture
def server(monkeypatch, data_dir):
    monkeypatch.setenv("FISH_API_KEY", "fish-test")
    from jarvis import run_store
    importlib.reload(run_store)
    from jarvis import server
    importlib.reload(server)
    run_store.init_db()
    from jarvis import human_activity
    monkeypatch.setattr(human_activity, "recorder", human_activity.HumanActivity())

    async def fake_synth(text):
        return b"MP3:" + text.encode()

    monkeypatch.setattr(server, "_synth_for_speech", fake_synth)
    server._last_greeting_time = time.time()
    with TestClient(server.app) as c:
        server.brain_instance = FakeBrain()
        yield c, server, human_activity


@pytest.mark.parametrize("source", ["gui", "typed"])
def test_the_page_reports_gui_and_typed_activity(server, source):
    c, _, activity = server
    r = c.post("/api/team/activity", json={"source": source}, headers=PAGE)
    assert r.status_code == 204
    assert activity.recorder.current()["source"] == source


def test_an_agent_with_the_tool_token_is_not_a_person(server):
    c, _, activity = server
    from jarvis import data_paths
    token = data_paths.ensure_tool_token()
    r = c.post("/api/team/activity", json={"source": "gui"},
               headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 403
    assert activity.recorder.current() is None


def test_a_stranger_page_is_refused(server):
    c, _, activity = server
    r = c.post("/api/team/activity", json={"source": "gui"},
               headers={"Origin": "http://evil.example"})
    assert r.status_code == 403
    assert activity.recorder.current() is None


@pytest.mark.parametrize("body", [{"source": "gui", "text": "my password is"},
                                  {"source": "speech"}, {"source": "gui", "x": 1, "y": 2},
                                  {}])
def test_no_content_and_no_other_source_rides_along(server, body):
    c, _, activity = server
    r = c.post("/api/team/activity", json=body, headers=PAGE)
    assert r.status_code == 422
    assert activity.recorder.current() is None


def test_loading_the_page_and_reading_status_is_not_activity(server):
    c, _, activity = server
    c.get("/api/team", headers=PAGE)
    c.get("/api/usage", headers=PAGE)
    with c.websocket_connect("/ws/voice", headers=PAGE) as ws:
        ws.receive_json(); ws.receive_json()
    assert activity.recorder.current() is None


def test_jarvis_speaking_is_not_activity(server):
    c, srv, activity = server
    srv._last_greeting_time = 0                      # he greets on connect
    with c.websocket_connect("/ws/voice", headers=PAGE) as ws:
        _drain_until(ws, lambda m: m["type"] == "audio")
    assert activity.recorder.current() is None


def test_accepted_speech_is_activity_and_its_echo_is_not(server, data_dir):
    c, srv, activity = server
    with c.websocket_connect("/ws/voice", headers=PAGE) as ws:
        ws.receive_json(); ws.receive_json()
        ws.send_json({"type": "transcript", "text": "hello there", "isFinal": True})
        _drain_until(ws, lambda m: m["type"] == "audio")
        assert activity.recorder.current()["activity_seq"] == 0
        assert activity.recorder.current()["source"] == "speech"
        # JARVIS is saying "Echo: hello there." and the microphone hears it.
        ws.send_json({"type": "transcript", "text": "echo hello there", "isFinal": True})
        ws.send_json({"type": "transcript", "text": "what is the weather like",
                      "isFinal": True})
        _drain_until(ws, lambda m: m["type"] == "audio" and "weather" in m["text"])
    assert activity.recorder.current()["activity_seq"] == 1, "the echo was not counted"
    assert [t for t, _ in srv.brain_instance.turns] == ["hello there",
                                                        "what is the weather like"]
    kept = (data_dir / "human-activity.json").read_text()
    assert "hello" not in kept and "weather" not in kept


def test_stop_and_say_that_again_are_activity(server):
    c, _, activity = server
    with c.websocket_connect("/ws/voice", headers=PAGE) as ws:
        ws.receive_json(); ws.receive_json()
        ws.send_json({"type": "transcript", "text": "say that again", "isFinal": True})
        _drain_until(ws, lambda m: m["type"] == "audio")
        assert activity.recorder.current()["activity_seq"] == 0
        ws.send_json({"type": "transcript", "text": "stop", "isFinal": True})
        ws.send_json({"type": "transcript", "text": "hello there", "isFinal": True})
        _drain_until(ws, lambda m: m["type"] == "audio" and "hello" in m["text"])
    assert activity.recorder.current()["activity_seq"] == 2


def test_typed_words_on_the_voice_socket_are_left_to_the_page(server):
    c, _, activity = server
    with c.websocket_connect("/ws/voice", headers=PAGE) as ws:
        ws.receive_json(); ws.receive_json()
        ws.send_json({"type": "transcript", "text": "hello there", "isFinal": True,
                      "typed": True})
        _drain_until(ws, lambda m: m["type"] == "audio")
    assert activity.recorder.current() is None
