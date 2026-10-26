"""
voice_diagnostics.py — the record of how voice input is doing, so a dropped
sentence can be traced to its cause from data rather than guessed at.

While the microphone is open, one sample a second (`voice_samples`): what
the recogniser received and decoded and how long decoding took, how far
behind it is, how late the server's own timers fire, JARVIS's CPU and the
machine's load, and what the page reported over /ws/transcribe that second
(frames captured and sent, audio held, long browser stalls, notifications
shown). Each sample is classified into one health state with one cause
(`classify`). Every capture start and end, every failure and every line of
text the page showed is one row of `voice_events`; a failure also carries
the cause in force and the busiest processes on the machine at that moment.

Counts and timings only: no audio, and no words except the text the page
itself displayed. Samples are kept SAMPLE_KEEP_SEC, events EVENT_KEEP_SEC.
The tables live in the same database as runs and messages.

transcription_api.py runs one `CaptureMonitor` per connected page; the
server's routes read `summary` and `history`.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import time
from contextlib import closing
from dataclasses import asdict, dataclass, field, fields
from typing import Awaitable, Callable, Optional

import run_store
from transcriber import BYTES_PER_SECOND

log = logging.getLogger("jarvis.voice_diagnostics")

SAMPLE_KEEP_SEC = 7 * 24 * 3600
EVENT_KEEP_SEC = 30 * 24 * 3600
SAMPLE_EVERY_SEC = 1.0
PROBE_EVERY_SEC = 0.25
PRUNE_EVERY_SAMPLES = 3600

# What counts as behind, everywhere it is judged (the page draws the same
# lines from the health message's numbers).
BACKLOG_WARN_SEC = 1.0
HELD_WARN_SEC = 1.0
SOCKET_WARN_SEC = 1.0
LONG_TASK_WARN_MS = 200.0
LOOP_DELAY_WARN_MS = 200.0
RTF_WARN = 0.9

STATES = ("healthy", "no-frames", "browser-behind", "connection-behind",
          "server-starved", "decoder-slow")
# The events that are a failure, which get the cause and the busiest processes.
INCIDENT_KINDS = frozenset({"overload", "decoder-failed", "interrupted", "busy",
                            "model-unavailable"})
EVENT_KINDS = INCIDENT_KINDS | {"ready", "finish", "reconnect", "message"}
NOTICE_SOURCES_MAX = 20
MESSAGE_TEXT_MAX = 1000
MESSAGES_PER_TELEMETRY = 20
HISTORY_MAX_SAMPLES = 24 * 3600

_SCHEMA = """
    CREATE TABLE IF NOT EXISTS voice_samples (
        at REAL NOT NULL,
        capture TEXT,
        received_sec REAL, decoded_sec REAL, backlog_sec REAL, oldest_sec REAL,
        rtf REAL,
        loop_delay_ms REAL, process_cpu REAL, load_avg REAL,
        frames_captured INTEGER, frames_sent INTEGER, held_sec REAL, socket_sec REAL,
        long_task_ms REAL, long_tasks INTEGER, tab_hidden INTEGER,
        notices INTEGER, notices_by_source TEXT,
        voice_msgs INTEGER, session_msgs INTEGER, renders INTEGER,
        finals INTEGER, typed INTEGER, quiet INTEGER,
        state TEXT NOT NULL, cause TEXT
    );
    CREATE INDEX IF NOT EXISTS voice_samples_by_time ON voice_samples (at);
    CREATE TABLE IF NOT EXISTS voice_events (
        id INTEGER PRIMARY KEY,
        at REAL NOT NULL,
        capture TEXT,
        kind TEXT NOT NULL,
        source TEXT, text TEXT,
        cause TEXT, top_processes TEXT
    );
    CREATE INDEX IF NOT EXISTS voice_events_by_time ON voice_events (at);
"""


def _connect():
    conn = run_store._connect()
    conn.executescript(_SCHEMA)
    return conn


@dataclass
class Sample:
    at: float
    capture: Optional[str] = None
    received_sec: float = 0.0
    decoded_sec: float = 0.0
    backlog_sec: float = 0.0
    oldest_sec: float = 0.0
    rtf: Optional[float] = None           # decode time / audio decoded; None if nothing decoded
    loop_delay_ms: float = 0.0
    process_cpu: float = 0.0              # percent of one core
    load_avg: float = 0.0
    frames_captured: Optional[int] = None  # None: the page sent no telemetry this second
    frames_sent: Optional[int] = None
    held_sec: Optional[float] = None
    socket_sec: Optional[float] = None
    long_task_ms: Optional[float] = None
    long_tasks: Optional[int] = None
    tab_hidden: Optional[bool] = None
    notices: Optional[int] = None
    notices_by_source: dict = field(default_factory=dict)
    voice_msgs: Optional[int] = None
    session_msgs: Optional[int] = None
    renders: Optional[int] = None
    finals: int = 0
    typed: int = 0
    quiet: Optional[bool] = None
    state: str = "healthy"
    cause: str = ""


_SAMPLE_COLUMNS = [f.name for f in fields(Sample)]


@dataclass(frozen=True)
class Event:
    at: float
    kind: str
    capture: Optional[str] = None
    source: Optional[str] = None
    text: Optional[str] = None
    cause: Optional[str] = None
    top_processes: list = field(default_factory=list)
    id: int = 0


# ── the page's telemetry ─────────────────────────────────────────────────

@dataclass
class Telemetry:
    """One second of what the page measured, as it sent it, checked."""
    frames_captured: int
    frames_sent: int
    held_sec: float
    socket_sec: float
    long_task_ms: float
    long_tasks: int
    tab_hidden: bool
    notices: dict
    voice_msgs: int
    session_msgs: int
    renders: int
    quiet: bool
    messages: list           # [(source, text, at)]


def _number(msg: dict, key: str, kind=float, cap: float = 1e9):
    value = msg.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(key)
    if value != value or value < 0 or value > cap:     # NaN, negative, absurd
        raise ValueError(key)
    return kind(value)


def parse_telemetry(msg: dict) -> Telemetry:
    """The page's telemetry message as a Telemetry; ValueError naming the
    first field that is missing or wrong. Text is bounded here, once."""
    notices = msg.get("notices")
    if not isinstance(notices, dict):
        raise ValueError("notices")
    counted = {}
    for source, count in list(notices.items())[:NOTICE_SOURCES_MAX]:
        if not isinstance(source, str) or isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise ValueError("notices")
        counted[source[:40]] = min(count, 10_000)
    raw = msg.get("messages")
    if not isinstance(raw, list):
        raise ValueError("messages")
    messages = []
    for m in raw[:MESSAGES_PER_TELEMETRY]:
        if not isinstance(m, dict) or not isinstance(m.get("text"), str) \
                or not isinstance(m.get("source"), str):
            raise ValueError("messages")
        at = m.get("at")
        at = float(at) / 1000 if isinstance(at, (int, float)) and not isinstance(at, bool) else time.time()
        messages.append((m["source"][:40], m["text"][:MESSAGE_TEXT_MAX], at))
    for key in ("tabHidden", "quiet"):
        if not isinstance(msg.get(key), bool):
            raise ValueError(key)
    return Telemetry(
        frames_captured=_number(msg, "framesCaptured", int, 1000),
        frames_sent=_number(msg, "framesSent", int, 1000),
        held_sec=_number(msg, "heldSec", cap=3600),
        socket_sec=_number(msg, "socketSec", cap=3600),
        long_task_ms=_number(msg, "longTaskMs", cap=60_000),
        long_tasks=_number(msg, "longTasks", int, 10_000),
        tab_hidden=msg["tabHidden"],
        notices=counted,
        voice_msgs=_number(msg, "voiceMsgs", int, 100_000),
        session_msgs=_number(msg, "sessionMsgs", int, 100_000),
        renders=_number(msg, "renders", int, 100_000),
        quiet=msg["quiet"],
        messages=messages,
    )


# ── the diagnosis ────────────────────────────────────────────────────────

def classify(s: Sample, cpu_count: int = os.cpu_count() or 1) -> tuple[str, str]:
    """One health state for a second, with its cause in a few words. The
    first that is true, in the order the audio travels: the microphone, the
    browser, the connection, the server, the decoder."""
    if s.frames_captured == 0 and not s.tab_hidden:
        return "no-frames", "No audio from the microphone"
    if s.tab_hidden:
        return "browser-behind", "The JARVIS tab is hidden, so Chrome slows it"
    if s.held_sec is not None and s.held_sec >= HELD_WARN_SEC:
        return "browser-behind", f"{s.held_sec:.1f} s of audio waiting in the browser"
    if s.long_task_ms is not None and s.long_task_ms >= LONG_TASK_WARN_MS:
        return "browser-behind", f"The page was busy for {s.long_task_ms:.0f} ms"
    if s.socket_sec is not None and s.socket_sec >= SOCKET_WARN_SEC:
        return "connection-behind", f"{s.socket_sec:.1f} s of audio waiting in the connection"
    slow = (s.rtf is not None and s.rtf >= RTF_WARN) or s.backlog_sec >= BACKLOG_WARN_SEC
    if s.loop_delay_ms >= LOOP_DELAY_WARN_MS and not slow:
        return "server-starved", f"JARVIS's server was {s.loop_delay_ms:.0f} ms late"
    if slow:
        others = s.load_avg - s.process_cpu / 100
        if others > cpu_count / 2:
            return "decoder-slow", f"Machine busy (load {s.load_avg:.1f})"
        return "decoder-slow", f"JARVIS busy (CPU {s.process_cpu:.0f}%)"
    return "healthy", ""


def pipeline(s: Optional[Sample]) -> dict:
    """Each stage the audio passes through, ok / warn / bad, with the delay
    or rate that judged it."""
    def judge(value, warn, bad):
        if value is None:
            return "unknown"
        return "bad" if value >= bad else "warn" if value >= warn else "ok"
    if s is None:
        return {k: {"state": "unknown", "value": None}
                for k in ("mic", "browser", "connection", "recogniser", "jarvis")}
    mic = "unknown" if s.frames_captured is None else \
        "bad" if s.frames_captured == 0 and not s.tab_hidden else "ok"
    browser = max((judge(s.held_sec, HELD_WARN_SEC, 2 * HELD_WARN_SEC),
                   judge(s.long_task_ms, LONG_TASK_WARN_MS, 2 * LONG_TASK_WARN_MS),
                   "warn" if s.tab_hidden else "ok"), key=_RANK.get)
    recogniser = max((judge(s.backlog_sec, BACKLOG_WARN_SEC, 2 * BACKLOG_WARN_SEC),
                      judge(s.rtf, RTF_WARN, 1.0)), key=_RANK.get)
    return {
        "mic": {"state": mic, "value": s.frames_captured},
        "browser": {"state": browser, "value": s.held_sec},
        "connection": {"state": judge(s.socket_sec, SOCKET_WARN_SEC, 2 * SOCKET_WARN_SEC),
                       "value": s.socket_sec},
        "recogniser": {"state": recogniser, "value": s.backlog_sec},
        "jarvis": {"state": judge(s.loop_delay_ms, LOOP_DELAY_WARN_MS, 2 * LOOP_DELAY_WARN_MS),
                   "value": s.loop_delay_ms},
    }


_RANK = {"unknown": 0, "ok": 1, "warn": 2, "bad": 3}


# ── measuring the server and the machine ─────────────────────────────────

class LoopProbe:
    """How late the server's event loop runs a timer: a timer set for every
    PROBE_EVERY_SEC, and the worst lateness since the last `take`. A loop
    busy with other work delays every socket read, the audio's included."""

    def __init__(self) -> None:
        self._worst = 0.0
        self._task: Optional[asyncio.Task] = None

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.get_running_loop().create_task(self._run())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None

    async def _run(self) -> None:
        while True:
            due = time.monotonic() + PROBE_EVERY_SEC
            await asyncio.sleep(PROBE_EVERY_SEC)
            self._worst = max(self._worst, (time.monotonic() - due) * 1000)

    def take(self) -> float:
        worst, self._worst = self._worst, 0.0
        return max(worst, 0.0)


class CpuMeter:
    """This process's CPU over the time since the last `take`, in percent of
    one core (two busy threads read 200)."""

    def __init__(self) -> None:
        self._cpu = time.process_time()
        self._wall = time.monotonic()

    def take(self) -> float:
        cpu, wall = time.process_time(), time.monotonic()
        spent, elapsed = cpu - self._cpu, wall - self._wall
        self._cpu, self._wall = cpu, wall
        return 100 * spent / elapsed if elapsed > 0 else 0.0


def load_average() -> float:
    try:
        return os.getloadavg()[0]
    except OSError:
        return 0.0


def top_processes(limit: int = 5) -> list[dict]:
    """The busiest processes on the machine by name and CPU, from `ps`. Names
    only, never arguments."""
    try:
        out = subprocess.run(["ps", "-Aco", "pcpu=,comm=", "-r"], capture_output=True,
                             text=True, timeout=5, check=True).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    rows = []
    for line in out.splitlines():
        cpu, _, name = line.strip().partition(" ")
        try:
            rows.append({"name": name.strip()[:60], "cpu": float(cpu)})
        except ValueError:
            continue
        if len(rows) == limit:
            break
    return rows


# ── the record ───────────────────────────────────────────────────────────

def record_sample(s: Sample) -> None:
    row = asdict(s)
    row["notices_by_source"] = json.dumps(s.notices_by_source)
    for key in ("tab_hidden", "quiet"):
        row[key] = None if row[key] is None else int(row[key])
    with closing(_connect()) as conn:
        conn.execute(f"INSERT INTO voice_samples ({','.join(_SAMPLE_COLUMNS)}) "
                     f"VALUES ({','.join('?' * len(_SAMPLE_COLUMNS))})",
                     [row[c] for c in _SAMPLE_COLUMNS])
        conn.commit()


def record_event(event: Event) -> int:
    assert event.kind in EVENT_KINDS, event.kind
    with closing(_connect()) as conn:
        cur = conn.execute(
            "INSERT INTO voice_events (at, capture, kind, source, text, cause, top_processes) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (event.at, event.capture, event.kind, event.source, event.text, event.cause,
             json.dumps(event.top_processes) if event.top_processes else None))
        conn.commit()
        return cur.lastrowid


def prune(now: Optional[float] = None) -> None:
    now = time.time() if now is None else now
    with closing(_connect()) as conn:
        conn.execute("DELETE FROM voice_samples WHERE at < ?", (now - SAMPLE_KEEP_SEC,))
        conn.execute("DELETE FROM voice_events WHERE at < ?", (now - EVENT_KEEP_SEC,))
        conn.commit()


def _sample(row) -> Sample:
    values = {c: row[c] for c in _SAMPLE_COLUMNS}
    values["notices_by_source"] = json.loads(values["notices_by_source"] or "{}")
    for key in ("tab_hidden", "quiet"):
        if values[key] is not None:
            values[key] = bool(values[key])
    return Sample(**values)


def _event(row) -> Event:
    return Event(at=row["at"], kind=row["kind"], capture=row["capture"], source=row["source"],
                 text=row["text"], cause=row["cause"],
                 top_processes=json.loads(row["top_processes"] or "[]"), id=row["id"])


def history(start: float, end: float) -> tuple[list[Sample], list[Event]]:
    """Samples and events from `start` to `end`, oldest first. A range
    longer than HISTORY_MAX_SAMPLES samples is thinned evenly."""
    with closing(_connect()) as conn:
        count = conn.execute("SELECT COUNT(*) FROM voice_samples WHERE at BETWEEN ? AND ?",
                             (start, end)).fetchone()[0]
        step = max(1, -(-count // HISTORY_MAX_SAMPLES))
        samples = [_sample(r) for r in conn.execute(
            "SELECT * FROM (SELECT *, ROW_NUMBER() OVER (ORDER BY at) AS n FROM voice_samples "
            "WHERE at BETWEEN ? AND ?) WHERE (n - 1) % ? = 0 ORDER BY at", (start, end, step))]
        events = [_event(r) for r in conn.execute(
            "SELECT * FROM voice_events WHERE at BETWEEN ? AND ? ORDER BY at", (start, end))]
    return samples, events


def summary(now: Optional[float] = None) -> dict:
    """What the Diagnostics tab shows: the latest state and cause, each
    stage of the pipeline, the last two minutes of samples, and counts of
    drops, reconnects and errors in the last hour."""
    now = time.time() if now is None else now
    samples, events = history(now - 120, now)
    hour_events = history(now - 3600, now)[1]
    latest = samples[-1] if samples and now - samples[-1].at < 5 else None
    return {
        "state": latest.state if latest else "idle",
        "cause": latest.cause if latest else "",
        "pipeline": pipeline(latest),
        "last2min": [sample_json(s) for s in samples],
        "incidentsLastHour": incident_counts(hour_events),
        "today": today(now),
        "now": now,
    }


def today(now: float) -> dict:
    """Since local midnight: seconds listened (one sample a second), finals,
    typed messages, drops and reconnects."""
    local = time.localtime(now)
    midnight = time.mktime((local.tm_year, local.tm_mon, local.tm_mday, 0, 0, 0, 0, 0, -1))
    with closing(_connect()) as conn:
        listened, finals, typed = conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(finals), 0), COALESCE(SUM(typed), 0) "
            "FROM voice_samples WHERE at >= ?", (midnight,)).fetchone()
        kinds = dict(conn.execute(
            "SELECT kind, COUNT(*) FROM voice_events WHERE at >= ? GROUP BY kind",
            (midnight,)).fetchall())
    return {"listenedSec": listened * SAMPLE_EVERY_SEC, "finals": finals, "typed": typed,
            "drops": kinds.get("overload", 0), "reconnects": kinds.get("reconnect", 0)}


def incident_counts(events: list[Event]) -> dict:
    return {
        "drops": sum(e.kind == "overload" for e in events),
        "reconnects": sum(e.kind == "reconnect" for e in events),
        "errors": sum(e.kind in INCIDENT_KINDS - {"overload"} for e in events),
    }


def sample_json(s: Sample) -> dict:
    return {_camel(k): v for k, v in asdict(s).items()}


def event_json(e: Event) -> dict:
    return {_camel(k): v for k, v in asdict(e).items()}


def _camel(name: str) -> str:
    head, *rest = name.split("_")
    return head + "".join(w.title() for w in rest)


# ── one page's monitor ───────────────────────────────────────────────────

# Words typed into the page since the last sample (the voice socket counts
# them): typing right after a drop is speech that had to be typed instead.
_typed = 0


def note_typed() -> None:
    global _typed
    _typed += 1


def _take_typed() -> int:
    global _typed
    count, _typed = _typed, 0
    return count


class CaptureMonitor:
    """Samples one capture once a second while its page is connected,
    records the sample, and sends the page its health. Failures go through
    `incident`, which records them with the cause in force."""

    def __init__(self, capture, session: str, probe: LoopProbe,
                 send_health: Callable[[dict], Awaitable[None]]) -> None:
        self._capture = capture
        self._session = session
        self._probe = probe
        self._send_health = send_health
        self._cpu = CpuMeter()
        self._telemetry: Optional[Telemetry] = None
        self.state, self.cause = "healthy", ""

    def telemetry(self, telemetry: Telemetry) -> None:
        """The page's latest second. Its text lines are recorded now; its
        counts go into the next sample."""
        self._telemetry = telemetry
        for source, text, at in telemetry.messages:
            _record_later(Event(at=at, kind="message", capture=self._session,
                                source=source, text=text))

    def sample(self, now: Optional[float] = None) -> Sample:
        now = time.time() if now is None else now
        window = self._capture.take_window()
        backlog, oldest = self._capture.backlog()
        decoded = window.decoded_bytes / BYTES_PER_SECOND
        s = Sample(at=now, capture=self._session,
                   received_sec=window.received_bytes / BYTES_PER_SECOND,
                   decoded_sec=decoded, backlog_sec=backlog, oldest_sec=oldest,
                   rtf=window.decode_sec / decoded if decoded > 0 else None,
                   loop_delay_ms=self._probe.take(), process_cpu=self._cpu.take(),
                   load_avg=load_average(), finals=window.finals, typed=_take_typed())
        t, self._telemetry = self._telemetry, None
        if t is not None:
            s.frames_captured, s.frames_sent = t.frames_captured, t.frames_sent
            s.held_sec, s.socket_sec = t.held_sec, t.socket_sec
            s.long_task_ms, s.long_tasks = t.long_task_ms, t.long_tasks
            s.tab_hidden, s.quiet = t.tab_hidden, t.quiet
            s.notices, s.notices_by_source = sum(t.notices.values()), t.notices
            s.voice_msgs, s.session_msgs, s.renders = t.voice_msgs, t.session_msgs, t.renders
        s.state, s.cause = classify(s)
        self.state, self.cause = s.state, s.cause
        return s

    async def run(self) -> None:
        taken = 0
        while True:
            await asyncio.sleep(SAMPLE_EVERY_SEC)
            s = self.sample()
            taken += 1
            if taken % PRUNE_EVERY_SAMPLES == 0:
                record_soon_prune()
            try:
                await asyncio.to_thread(record_sample, s)
            except Exception:
                log.exception("voice sample not recorded")
            await self._send_health({
                "state": s.state, "cause": s.cause, "backlogSec": s.backlog_sec,
                "rtf": s.rtf, "loopDelayMs": s.loop_delay_ms,
                "processCpu": s.process_cpu, "loadAvg": s.load_avg,
                "pipeline": pipeline(s)})

    async def incident(self, kind: str, text: str = "") -> None:
        """A failure, with the cause in force and the busiest processes."""
        top = await asyncio.to_thread(top_processes)
        event = Event(at=time.time(), kind=kind, capture=self._session, text=text or None,
                      cause=self.cause or None, top_processes=top)
        await asyncio.to_thread(record_event, event)
        _tell(event)


# Called on the event loop after each event is recorded (the server hints
# the page to read again).
listeners: list[Callable[[Event], None]] = []


def _record_later(event: Event) -> None:
    """Record off the event loop, without making the caller wait."""
    async def write() -> None:
        try:
            await asyncio.to_thread(record_event, event)
        except Exception:
            log.exception("voice event not recorded")
            return
        _tell(event)
    asyncio.get_running_loop().create_task(write())


def _tell(event: Event) -> None:
    for listener in listeners:
        try:
            listener(event)
        except Exception:
            log.exception("voice event listener failed")


def record_soon(event: Event) -> None:
    """For callers on the event loop that do not wait for the write."""
    _record_later(event)


def record_soon_prune() -> None:
    """Drop what is past its keeping time, off the event loop (at start)."""
    async def run() -> None:
        try:
            await asyncio.to_thread(prune)
        except Exception:
            log.exception("voice diagnostics not pruned")
    asyncio.get_running_loop().create_task(run())

