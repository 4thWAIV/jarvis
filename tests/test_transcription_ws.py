"""/ws/transcribe with a fake engine: the protocol's order, its limits, one
capture at a time, finish and abort, failures, teardown, and the web
boundary. No model weights and no microphone are involved."""

import json
import threading
import time

import pytest
from fastapi import FastAPI
from contextlib import asynccontextmanager
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from jarvis import access_keys
from jarvis import data_paths
from jarvis import transcriber
from jarvis import transcription_api
from jarvis import web_auth
from jarvis.transcriber import PROTOCOL, RecognitionEvent

PAGE = {"Origin": "http://localhost:5173"}
START = {"type": "start", "version": 1, "sampleRate": 16000, "channels": 1,
         "format": "pcm_s16le"}
SILENCE = b"\x00\x00" * 1600


def word(text: str) -> bytes:
    """A frame the fake engine hears as one word (padded to an even size)."""
    raw = text.encode()
    return raw + b" " * (len(raw) % 2)


class FakeSession:
    """Each non-silent frame is a word; a silent frame after words ends the
    segment. `gate` blocks decoding until set; `explode` makes it fail."""

    def __init__(self, engine):
        self.engine = engine
        self.words = []
        self.segment = 0
        self.closed = False

    def accept_pcm16(self, pcm):
        self.engine.gate.wait()
        if self.engine.explode:
            raise RuntimeError("decoder blew up")
        if pcm.strip(b"\x00"):
            self.words.append(pcm.decode().strip())
            return [RecognitionEvent("interim", " ".join(self.words), self.segment)]
        return self._final()

    def finish(self):
        self.engine.finish_gate.wait()
        return self._final()

    def _final(self):
        if not self.words:
            return []
        event = RecognitionEvent("final", " ".join(self.words), self.segment)
        self.words, self.segment = [], self.segment + 1
        return [event]

    def close(self):
        self.closed = True


class FakeEngine:
    def __init__(self):
        self.gate = threading.Event()
        self.gate.set()
        self.finish_gate = threading.Event()
        self.finish_gate.set()
        self.explode = False
        self.sessions = []

    def open_session(self):
        session = FakeSession(self)
        self.sessions.append(session)
        return session


@pytest.fixture
def engine():
    return FakeEngine()


def _app(factory):
    @asynccontextmanager
    async def lifespan(app):
        transcription_api.start(factory)
        yield
        await transcription_api.stop()

    app = FastAPI(lifespan=lifespan)
    transcription_api.mount(app)

    @app.get("/ping")
    async def ping():
        return {"ok": True}

    return web_auth.OriginGuard(app)


@pytest.fixture
def client(engine):
    with TestClient(_app(lambda: engine)) as c:
        yield c


def _open(client, headers=PAGE):
    return client.websocket_connect(PROTOCOL["path"], headers=headers)


def _ready(ws):
    ws.send_text(json.dumps(START))
    msg = ws.receive_json()
    while msg["type"] == "loading":
        msg = ws.receive_json()
    assert msg["type"] == "ready", msg
    return msg["session"]


def _until(ws, kind):
    seen = []
    while True:
        msg = ws.receive_json()
        seen.append(msg)
        if msg["type"] == kind:
            return seen


def test_words_come_back_as_interims_then_one_final_per_segment(client):
    with _open(client) as ws:
        session = _ready(ws)
        for w in ("open", "the", "door"):
            ws.send_bytes(word(w))
        ws.send_bytes(SILENCE)
        seen = _until(ws, "final")
        assert all(m["session"] == session for m in seen)
        assert [m for m in seen if m["type"] == "final"] == [
            {"type": "final", "session": session, "segment": 0, "text": "open the door"}]
        assert seen[0]["type"] == "interim" and seen[0]["text"] == "open"


def test_the_same_sentence_twice_is_two_finals(client):
    with _open(client) as ws:
        _ready(ws)
        finals = []
        for _ in range(2):
            ws.send_bytes(word("again"))
            ws.send_bytes(SILENCE)
            finals.append(_until(ws, "final")[-1])
        assert [(f["segment"], f["text"]) for f in finals] == [(0, "again"), (1, "again")]


def test_interims_are_coalesced_but_the_final_is_not(client):
    with _open(client) as ws:
        _ready(ws)
        for i in range(20):
            ws.send_bytes(word(f"w{i:02d}"))
        ws.send_bytes(SILENCE)
        seen = _until(ws, "final")
        interims = [m for m in seen if m["type"] == "interim"]
        assert len(interims) < 20
        assert seen[-1]["text"] == " ".join(f"w{i:02d}" for i in range(20))


def test_a_start_with_the_wrong_audio_format_is_refused(client):
    with _open(client) as ws:
        ws.send_text(json.dumps({**START, "sampleRate": 48000}))
        msg = ws.receive_json()
        assert (msg["type"], msg["code"]) == ("error", "invalid-format")
        assert "sampleRate=16000" in msg["message"]


def test_the_first_message_must_be_start(client):
    with _open(client) as ws:
        ws.send_text(json.dumps({"type": "finish"}))
        assert ws.receive_json()["code"] == "invalid-format"


@pytest.mark.parametrize("frame", [b"", b"\x01\x02\x03", b"\x00" * 6402])
def test_an_empty_odd_or_oversized_frame_is_refused(client, frame):
    with _open(client) as ws:
        _ready(ws)
        ws.send_bytes(frame)
        assert ws.receive_json()["code"] == "invalid-format"


def test_a_second_page_is_told_it_is_busy_and_the_first_keeps_listening(client):
    with _open(client) as first:
        _ready(first)
        with _open(client) as second:
            second.send_text(json.dumps(START))
            msg = second.receive_json()
            assert (msg["type"], msg["code"]) == ("error", "busy")
        first.send_bytes(word("still"))
        first.send_bytes(SILENCE)
        assert _until(first, "final")[-1]["text"] == "still"


def test_finish_drains_what_was_sent_then_says_finished(client, engine):
    with _open(client) as ws:
        _ready(ws)
        engine.gate.clear()
        ws.send_bytes(word("last"))
        ws.send_bytes(word("words"))
        ws.send_text(json.dumps({"type": "finish"}))
        engine.gate.set()
        seen = _until(ws, "finished")
        assert [(m["type"], m.get("text")) for m in seen if m["type"] != "interim"] == [
            ("final", "last words"), ("finished", None)]


def test_finish_that_cannot_complete_in_time_is_reported_as_interrupted(
        client, engine, monkeypatch):
    monkeypatch.setitem(PROTOCOL, "finishLimitSec", 0.2)
    with _open(client) as ws:
        _ready(ws)
        ws.send_bytes(word("slow"))
        engine.finish_gate.clear()
        ws.send_text(json.dumps({"type": "finish"}))
        seen = _until(ws, "error")
        assert seen[-1]["code"] == "interrupted"
        assert not [m for m in seen if m["type"] == "final"]
        engine.finish_gate.set()


def test_abort_discards_what_was_said(client, engine):
    with _open(client) as ws:
        _ready(ws)
        engine.gate.clear()
        ws.send_bytes(word("never"))
        ws.send_text(json.dumps({"type": "abort"}))
        with pytest.raises(WebSocketDisconnect):
            ws.receive_json()
    engine.gate.set()
    # The next page gets the microphone, and nothing of the old words.
    with _open(client) as ws:
        _ready(ws)
        ws.send_bytes(word("fresh"))
        ws.send_bytes(SILENCE)
        assert _until(ws, "final")[-1]["text"] == "fresh"


def test_a_page_that_disconnects_mid_sentence_sends_no_command(client, engine):
    with _open(client) as ws:
        _ready(ws)
        ws.send_bytes(word("half"))
    deadline = time.monotonic() + 2
    while transcription_api.service._active is not None and time.monotonic() < deadline:
        time.sleep(0.01)
    assert transcription_api.service._active is None
    with _open(client) as ws:
        _ready(ws)
        ws.send_bytes(word("new"))
        ws.send_bytes(SILENCE)
        assert _until(ws, "final")[-1]["text"] == "new"


def test_audio_beyond_two_seconds_behind_is_refused_not_trimmed(client, engine):
    with _open(client) as ws:
        _ready(ws)
        engine.gate.clear()
        frames = int(PROTOCOL["queueLimitSec"] * 10) + 2
        error = None
        for _ in range(frames):
            try:
                ws.send_bytes(b"\x01\x00" * 1600)
            except Exception:
                break
        error = _until(ws, "error")[-1]
        assert error["code"] == "overload"
        engine.gate.set()


def test_the_server_answers_while_decoding_is_blocked(client, engine):
    with _open(client) as ws:
        _ready(ws)
        engine.gate.clear()
        ws.send_bytes(word("stuck"))
        assert client.get("/ping", headers=PAGE).json() == {"ok": True}
        engine.gate.set()
        ws.send_bytes(SILENCE)
        assert _until(ws, "final")[-1]["text"] == "stuck"


def test_a_decoder_failure_is_reported(client, engine):
    with _open(client) as ws:
        _ready(ws)
        engine.explode = True
        ws.send_bytes(word("boom"))
        assert _until(ws, "error")[-1]["code"] == "decoder-failed"


def test_the_page_is_told_while_the_model_loads(engine):
    loaded = threading.Event()

    def slow():
        loaded.wait(5)
        return engine

    with TestClient(_app(slow)) as client:
        with _open(client) as ws:
            ws.send_text(json.dumps(START))
            assert ws.receive_json()["type"] == "loading"
            loaded.set()
            assert ws.receive_json()["type"] == "ready"


def test_a_missing_model_is_reported_with_the_setup_command():
    with TestClient(_app(transcriber.SherpaEngine)) as client:
        with _open(client) as ws:
            ws.send_text(json.dumps(START))
            msg = ws.receive_json()
            assert (msg["type"], msg["code"]) == ("error", "model-unavailable")
            assert transcriber.SETUP_COMMAND in msg["message"]


def _connect_once(client):
    with _open(client) as ws:
        _ready(ws)
        ws.send_bytes(word("x"))
        ws.send_text(json.dumps({"type": "abort"}))


def test_connecting_again_and_again_leaves_no_threads_or_sessions(client, engine):
    # One connection first, so the loop's shared worker pool (which records
    # each capture's events) already has its thread before counting.
    _connect_once(client)
    time.sleep(0.2)
    before = threading.active_count()
    for _ in range(10):
        with _open(client) as ws:
            _ready(ws)
            ws.send_bytes(word("x"))
            ws.send_text(json.dumps({"type": "abort"}))
    deadline = time.monotonic() + 2
    while not all(s.closed for s in engine.sessions) and time.monotonic() < deadline:
        time.sleep(0.01)
    assert all(s.closed for s in engine.sessions)
    assert threading.active_count() <= before


def test_shutdown_waits_for_the_running_inference_then_stops_the_worker(engine):
    with TestClient(_app(lambda: engine)) as client:
        with _open(client) as ws:
            _ready(ws)
        worker = transcription_api.service._thread
    assert transcription_api.service is None
    assert not worker.is_alive()


def test_the_page_or_the_token_opens_it_and_nothing_else_does(client, monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    token = data_paths.ensure_tool_token()
    with _open(client, {"Authorization": f"Bearer {token}"}) as ws:
        _ready(ws)
    for headers in ({}, {"Origin": "https://evil.example"}):
        with pytest.raises(WebSocketDisconnect):
            with _open(client, headers) as ws:
                ws.receive_json()
    key = access_keys.create("everything", list(access_keys.CATEGORIES))
    with pytest.raises(WebSocketDisconnect):
        with _open(client, {"Authorization": f"Bearer {key}"}) as ws:
            ws.receive_json()


# ── diagnostics (voice_diagnostics.py) ───────────────────────────────────

from jarvis import voice_diagnostics

TELEMETRY = {"type": "telemetry", "framesCaptured": 10, "framesSent": 10, "heldSec": 0.0,
             "socketSec": 0.0, "longTaskMs": 0.0, "longTasks": 0, "tabHidden": False,
             "notices": {"status": 1}, "voiceMsgs": 2, "sessionMsgs": 0, "renders": 3,
             "quiet": False, "messages": [{"source": "Input", "text": "Listening again.", "at": 1}]}


def _recorded(*kinds, timeout=2.0):
    """The record once an event of every one of `kinds` is in it: each event
    is written on its own, off the event loop, so one can land before another."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        samples, events = voice_diagnostics.history(0, 2e9)
        if all(any(e.kind == kind for e in events) for kind in kinds):
            return samples, events
        time.sleep(0.02)
    return voice_diagnostics.history(0, 2e9)


def test_a_page_that_asks_for_diagnostics_is_sent_its_health_and_its_seconds_are_recorded(
        client, monkeypatch):
    monkeypatch.setattr(voice_diagnostics, "SAMPLE_EVERY_SEC", 0.1)
    with _open(client) as ws:
        ws.send_text(json.dumps({**START, "diagnostics": True}))
        while ws.receive_json()["type"] != "ready":
            pass
        ws.send_text(json.dumps(TELEMETRY))
        ws.send_bytes(SILENCE)
        health = next(m for m in iter(ws.receive_json, None) if m["type"] == "health")
        assert health["state"] in voice_diagnostics.STATES
        assert set(health["pipeline"]) == {"mic", "browser", "connection", "recogniser", "jarvis"}
        assert "text" not in health
        ws.send_text(json.dumps({"type": "abort"}))
    samples, events = _recorded("message", "ready")
    assert any(s.frames_captured == 10 and s.notices_by_source == {"status": 1} for s in samples)
    assert [(e.kind, e.text) for e in events if e.kind == "message"] == [("message", "Listening again.")]
    assert any(e.kind == "ready" for e in events)


def test_a_page_that_does_not_ask_is_never_sent_health(client, monkeypatch):
    monkeypatch.setattr(voice_diagnostics, "SAMPLE_EVERY_SEC", 0.05)
    with _open(client) as ws:
        _ready(ws)
        time.sleep(0.3)
        ws.send_bytes(word("quiet"))
        ws.send_bytes(SILENCE)
        assert all(m["type"] != "health" for m in _until(ws, "final"))


def test_bad_telemetry_is_ignored_and_listening_goes_on(client):
    with _open(client) as ws:
        ws.send_text(json.dumps({**START, "diagnostics": True}))
        while ws.receive_json()["type"] != "ready":
            pass
        ws.send_text(json.dumps({**TELEMETRY, "framesCaptured": -5}))
        ws.send_bytes(word("still"))
        ws.send_bytes(SILENCE)
        assert _until(ws, "final")[-1]["text"] == "still"


def test_a_drop_is_recorded_with_its_cause_and_the_busiest_processes_then_the_reconnect(
        client, engine, monkeypatch):
    monkeypatch.setattr(voice_diagnostics, "top_processes",
                        lambda limit=5: [{"name": "rider", "cpu": 99.0}])
    with _open(client) as ws:
        ws.send_text(json.dumps({**START, "diagnostics": True}))
        while ws.receive_json()["type"] != "ready":
            pass
        engine.gate.clear()
        for _ in range(int(PROTOCOL["queueLimitSec"] * 10) + 2):
            try:
                ws.send_bytes(b"\x01\x00" * 1600)
            except Exception:
                break
        assert _until(ws, "error")[-1]["code"] == "overload"
    engine.gate.set()
    with _open(client) as ws:
        _ready(ws)
    _, events = _recorded("reconnect")
    drop = next(e for e in events if e.kind == "overload")
    assert drop.top_processes == [{"name": "rider", "cpu": 99.0}]
    assert drop.text == "Speech recognition fell behind, so this was not heard."
    assert [e.kind for e in events if e.kind in ("ready", "reconnect")] == ["ready", "reconnect"]
