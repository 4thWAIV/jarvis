"""The voice record: classifying a second, judging the pipeline, checking
the page's telemetry, recording and reading samples and events, keeping them
only so long, the server's timer probe, the busiest-process reading, one
capture's monitor, and the two read routes. No microphone and no model."""

import asyncio
import json
import subprocess
import time
from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient

from jarvis import voice_diagnostics as vd
from jarvis.transcriber import Window
from jarvis.voice_diagnostics import Event, Sample


def healthy(**changes) -> Sample:
    base = dict(at=1000.0, capture="c1", received_sec=1.0, decoded_sec=1.0, backlog_sec=0.1,
                oldest_sec=0.1, rtf=0.5, loop_delay_ms=5, process_cpu=100, load_avg=3,
                frames_captured=10, frames_sent=10, held_sec=0, socket_sec=0,
                long_task_ms=0, long_tasks=0, tab_hidden=False, notices=1,
                notices_by_source={"status": 1}, voice_msgs=3, session_msgs=1, renders=4,
                quiet=False)
    base.update(changes)
    return Sample(**base)


# ── classify ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("changes, state, cause", [
    ({}, "healthy", ""),
    ({"frames_captured": 0}, "no-frames", "No audio from the microphone"),
    ({"tab_hidden": True}, "browser-behind", "The JARVIS tab is hidden, so Chrome slows it"),
    ({"held_sec": 1.5}, "browser-behind", "1.5 s of audio waiting in the browser"),
    ({"long_task_ms": 350}, "browser-behind", "The page was busy for 350 ms"),
    ({"socket_sec": 1.2}, "connection-behind", "1.2 s of audio waiting in the connection"),
    ({"loop_delay_ms": 450}, "server-starved", "JARVIS's server was 450 ms late"),
    ({"rtf": 1.08, "load_avg": 16.9, "process_cpu": 220}, "decoder-slow", "Machine busy (load 16.9)"),
    ({"rtf": 1.08, "load_avg": 3, "process_cpu": 220}, "decoder-slow", "JARVIS busy (CPU 220%)"),
    ({"backlog_sec": 1.3, "rtf": None}, "decoder-slow", "JARVIS busy (CPU 100%)"),
])
def test_a_second_is_given_the_first_cause_along_the_audio_s_path(changes, state, cause):
    assert vd.classify(healthy(**changes), cpu_count=16) == (state, cause)


def test_the_microphone_comes_before_everything_downstream():
    s = healthy(frames_captured=0, held_sec=3, socket_sec=3, rtf=2, loop_delay_ms=900)
    assert vd.classify(s, cpu_count=16)[0] == "no-frames"


def test_a_late_server_with_a_slow_decoder_is_the_decoder():
    s = healthy(loop_delay_ms=450, rtf=1.2, load_avg=2, process_cpu=200)
    assert vd.classify(s, cpu_count=16)[0] == "decoder-slow"


def test_a_second_with_no_telemetry_is_judged_on_the_server_s_numbers_alone():
    s = Sample(at=1.0, backlog_sec=0.2, rtf=0.4)
    assert vd.classify(s) == ("healthy", "")
    assert vd.pipeline(s)["mic"]["state"] == "unknown"


def test_the_pipeline_marks_each_stage():
    p = vd.pipeline(healthy(backlog_sec=1.4, rtf=0.95, socket_sec=2.5))
    assert {k: v["state"] for k, v in p.items()} == {
        "mic": "ok", "browser": "ok", "connection": "bad", "recogniser": "warn", "jarvis": "ok"}
    assert p["recogniser"]["value"] == 1.4


# ── telemetry ────────────────────────────────────────────────────────────

def telemetry(**changes) -> dict:
    base = {"type": "telemetry", "session": "s", "framesCaptured": 10, "framesSent": 10,
            "heldSec": 0.1, "socketSec": 0.0, "longTaskMs": 60.0, "longTasks": 1,
            "tabHidden": False, "notices": {"status": 2, "toast": 1}, "voiceMsgs": 4,
            "sessionMsgs": 1, "renders": 12, "quiet": False,
            "messages": [{"source": "Status", "text": "restarting...", "at": 1_700_000_000_000}]}
    base.update(changes)
    return base


def test_telemetry_is_read_into_its_fields():
    t = vd.parse_telemetry(telemetry())
    assert (t.frames_captured, t.long_task_ms, t.notices, t.quiet) == (10, 60.0, {"status": 2, "toast": 1}, False)
    assert t.messages == [("Status", "restarting...", 1_700_000_000.0)]


@pytest.mark.parametrize("key, value", [
    ("framesCaptured", -1), ("framesCaptured", "10"), ("heldSec", float("nan")),
    ("tabHidden", 1), ("quiet", None), ("notices", []), ("notices", {"x": -1}),
    ("messages", [{"text": 3, "source": "x"}]), ("longTaskMs", True),
])
def test_telemetry_with_a_wrong_field_is_refused_by_name(key, value):
    with pytest.raises(ValueError, match=key):
        vd.parse_telemetry(telemetry(**{key: value}))


def test_telemetry_text_is_bounded():
    t = vd.parse_telemetry(telemetry(messages=[{"source": "S" * 99, "text": "x" * 5000}] * 50))
    assert len(t.messages) == vd.MESSAGES_PER_TELEMETRY
    assert len(t.messages[0][0]) == 40 and len(t.messages[0][1]) == vd.MESSAGE_TEXT_MAX


# ── the record ───────────────────────────────────────────────────────────

def test_samples_and_events_come_back_as_they_were_recorded():
    s = healthy(state="decoder-slow", cause="Machine busy (load 16.9)")
    vd.record_sample(s)
    vd.record_event(Event(at=1000.5, kind="overload", capture="c1", cause=s.cause,
                          top_processes=[{"name": "rider", "cpu": 99.1}]))
    samples, events = vd.history(900, 1100)
    assert samples == [s]
    assert [(e.kind, e.cause, e.top_processes) for e in events] == [
        ("overload", "Machine busy (load 16.9)", [{"name": "rider", "cpu": 99.1}])]


def test_a_long_range_is_thinned_evenly(monkeypatch):
    monkeypatch.setattr(vd, "HISTORY_MAX_SAMPLES", 10)
    for i in range(40):
        vd.record_sample(healthy(at=float(i)))
    samples, _ = vd.history(0, 100)
    assert [s.at for s in samples] == [0.0, 4.0, 8.0, 12.0, 16.0, 20.0, 24.0, 28.0, 32.0, 36.0]


def test_old_samples_and_events_are_dropped_after_their_keeping_time():
    now = 10_000_000.0
    vd.record_sample(healthy(at=now - vd.SAMPLE_KEEP_SEC - 1))
    vd.record_sample(healthy(at=now - 10))
    vd.record_event(Event(at=now - vd.EVENT_KEEP_SEC - 1, kind="overload"))
    vd.record_event(Event(at=now - vd.SAMPLE_KEEP_SEC - 1, kind="overload"))
    vd.prune(now)
    samples, events = vd.history(0, now)
    assert [s.at for s in samples] == [now - 10]
    assert [e.at for e in events] == [now - vd.SAMPLE_KEEP_SEC - 1]


def test_the_summary_has_the_latest_state_the_pipeline_and_the_hour_s_incidents():
    now = time.time()
    for i in range(5):
        vd.record_sample(healthy(at=now - 5 + i, finals=1))
    vd.record_sample(healthy(at=now - 0.5, rtf=1.1, load_avg=16, state="decoder-slow",
                             cause="Machine busy (load 16.0)"))
    for kind in ("overload", "reconnect", "overload", "decoder-failed", "message"):
        vd.record_event(Event(at=now - 30, kind=kind))
    vd.record_event(Event(at=now - 4000, kind="overload"))
    got = vd.summary(now)
    assert (got["state"], got["cause"]) == ("decoder-slow", "Machine busy (load 16.0)")
    assert got["pipeline"]["recogniser"]["state"] == "bad"
    assert len(got["last2min"]) == 6 and got["last2min"][-1]["loadAvg"] == 16
    assert got["incidentsLastHour"] == {"drops": 2, "reconnects": 1, "errors": 1}
    assert got["today"]["finals"] == 5
    assert got["lastHour"] == {"finals": 5, "drops": 2}
    assert got["lastDropAt"] == pytest.approx(now - 30)


def test_with_nothing_recent_the_summary_says_idle():
    vd.record_sample(healthy(at=time.time() - 60))
    assert vd.summary()["state"] == "idle"


# ── measuring ────────────────────────────────────────────────────────────

def test_the_loop_probe_reports_a_blocked_loop():
    async def run():
        probe = vd.LoopProbe()
        probe.start()
        await asyncio.sleep(0.05)
        time.sleep(0.6)                      # the loop is held
        await asyncio.sleep(0.3)
        late = probe.take()
        await probe.stop()
        return late, probe.take()
    late, after = asyncio.run(run())
    assert late >= 300
    assert after == 0


def test_the_busiest_processes_are_names_and_cpu_only(monkeypatch):
    out = " 99.1 rider\n125.8 WindowServer\n  0.0 launchd\n"
    monkeypatch.setattr(vd.subprocess, "run", lambda *a, **k: SimpleNamespace(stdout=out))
    assert vd.top_processes(2) == [{"name": "rider", "cpu": 99.1},
                                   {"name": "WindowServer", "cpu": 125.8}]

    def fails(*a, **k):
        raise subprocess.TimeoutExpired("ps", 5)
    monkeypatch.setattr(vd.subprocess, "run", fails)
    assert vd.top_processes() == []


class FakeCapture:
    def __init__(self):
        self.window = Window(received_bytes=32000, decoded_bytes=16000, decode_sec=0.6, finals=1)

    def take_window(self):
        window, self.window = self.window, Window()
        return window

    def backlog(self):
        return 1.2, 1.4


def test_a_capture_s_second_combines_the_recogniser_the_server_and_the_page(monkeypatch):
    monkeypatch.setattr(vd, "load_average", lambda: 15.0)
    probe = vd.LoopProbe()
    monitor = vd.CaptureMonitor(FakeCapture(), "c1", probe, None)
    vd.note_typed()
    asyncio.run(_feed(monitor, vd.parse_telemetry(telemetry())))
    s = monitor.sample(now=5.0)
    assert (s.received_sec, s.decoded_sec, s.rtf) == (1.0, 0.5, pytest.approx(1.2))
    assert (s.backlog_sec, s.oldest_sec, s.finals, s.typed) == (1.2, 1.4, 1, 1)
    assert (s.frames_captured, s.notices, s.notices_by_source) == (10, 3, {"status": 2, "toast": 1})
    assert s.state == "decoder-slow"
    # The next second has no telemetry and no new audio.
    s2 = monitor.sample(now=6.0)
    assert (s2.frames_captured, s2.rtf, s2.typed) == (None, None, 0)


async def _feed(monitor, t):
    monitor.telemetry(t)
    await asyncio.sleep(0.05)        # the message line is written in the background


def test_page_text_in_telemetry_is_recorded_as_messages():
    monitor = vd.CaptureMonitor(FakeCapture(), "c1", vd.LoopProbe(), None)
    asyncio.run(_feed(monitor, vd.parse_telemetry(telemetry())))
    _, events = vd.history(0, 2e9)
    assert [(e.kind, e.source, e.text) for e in events] == [("message", "Status", "restarting...")]


# ── the routes ───────────────────────────────────────────────────────────

def test_the_routes_read_the_record():
    from jarvis import server
    now = time.time()
    vd.record_sample(healthy(at=now - 1))
    vd.record_event(Event(at=now - 1, kind="overload", cause="Machine busy (load 16.0)"))
    client = TestClient(server.app)
    summary = client.get("/api/voice/diagnostics").json()
    assert summary["state"] == "healthy" and summary["incidentsLastHour"]["drops"] == 1
    got = client.get("/api/voice/diagnostics/samples", params={"from": now - 10, "to": now}).json()
    assert [s["framesCaptured"] for s in got["samples"]] == [10]
    assert got["events"][0]["kind"] == "overload"
    assert client.get("/api/voice/diagnostics/samples",
                      params={"from": now, "to": now - 10}).status_code == 400
    assert client.get("/api/voice/diagnostics/samples",
                      params={"from": 0, "to": now}).status_code == 400
