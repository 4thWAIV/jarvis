"""
voice_diagnostics.py — the record of how voice input is doing, so a dropped
sentence can be traced to its cause from data rather than guessed at.

While the microphone is open, one sample a second (a sample line): what
the recogniser received and decoded and how long decoding took, how far
behind it is, how late the server's own timers fire, JARVIS's CPU and the
machine's load, and what the page reported over /ws/transcribe that second
(frames captured and sent, audio held, long browser stalls, notifications
shown). Each sample is classified into one health state with one cause
(`classify`, `diagnose`): its English, and its code and values (`CAUSES`)
that the page words in the interface's language. Every capture start and
end, every failure and every line of
text the page showed, and every switch of speech model, is one event
line; a failure also carries
the cause in force and the busiest processes on the machine at that moment.

Counts and timings only: no audio, and no words except the text the page
itself displayed. Samples are kept SAMPLE_KEEP_SEC, events EVENT_KEEP_SEC.
They are files on disk, indexed as Content (a log read whole, not records):
each day's samples and each day's events, one JSON line each, in
data_paths.voice_dir() ("the record" below). A record from before the
codes has none, and the page shows its English as it was written.

transcription_api.py runs one `CaptureMonitor` per connected page; the
server's routes read `summary` and `history`.
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import subprocess
import threading
import time
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

from jarvis import data_paths, knowledge_move as move
from jarvis.said import Said, english
from jarvis.transcriber import BYTES_PER_SECOND, PROTOCOL

log = logging.getLogger("jarvis.voice_diagnostics")

SAMPLE_KEEP_SEC = 7 * 24 * 3600
EVENT_KEEP_SEC = 30 * 24 * 3600
SAMPLE_EVERY_SEC = PROTOCOL["telemetryEverySec"]
PROBE_EVERY_SEC = 0.25
PRUNE_EVERY_SAMPLES = 3600
# A sample is taken this many seconds-between-samples after the last one
# when the page has sent nothing.
TELEMETRY_LATE = 1.5

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
# "dropped" is the page throwing away audio it held for the recogniser
# (its reason one of PROTOCOL["dropReasons"], with the seconds it lost).
INCIDENT_KINDS = frozenset({"overload", "decoder-failed", "interrupted", "busy",
                            "model-unavailable", "dropped"})
# The failures that lost speech: the recogniser refusing audio, and the
# page dropping what it held. Every count of drops counts these.
DROP_KINDS = frozenset({"overload", "dropped"})
# "model-load" is speech input loading another language's model in place of
# the one it had (speech_models.switch), with how long it took.
EVENT_KINDS = INCIDENT_KINDS | {"ready", "finish", "reconnect", "message", "model-load"}
NOTICE_SOURCES_MAX = 20
MESSAGE_TEXT_MAX = 1000
MESSAGES_PER_TELEMETRY = 20
HISTORY_MAX_SAMPLES = 24 * 3600

# Why a second was not healthy, by code: the English the brain and the log
# read, and, word for word, the page's `shell.voice.cause.<code>`
# (tests/test_voice_diagnostics.py holds the two together).
CAUSES = {
    "noFrames": "No audio from the microphone",
    "tabHidden": "The JARVIS tab is hidden, so Chrome slows it",
    "audioHeld": "{seconds} s of audio waiting in the browser",
    "pageBusy": "The page was busy for {ms} ms",
    "audioInConnection": "{seconds} s of audio waiting in the connection",
    "serverLate": "JARVIS's server was {ms} ms late",
    "machineBusy": "Machine busy (load {load})",
    "jarvisBusy": "JARVIS busy (CPU {cpu}%)",
}



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
    # The cause by code (CAUSES) and its values; none for a healthy second.
    cause_code: Optional[str] = None
    cause_values: dict = field(default_factory=dict)


# The columns of a sample line (the table it replaced, `voice_samples`).
SAMPLE_COLUMNS = tuple(f.name for f in fields(Sample))
# Stored as JSON text.
_JSON_COLUMNS = ("notices_by_source", "cause_values")


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
    # The cause by code (CAUSES) and its values; an older record has none.
    cause_code: Optional[str] = None
    cause_values: dict = field(default_factory=dict)
    # The seconds of audio a drop lost (a "dropped" event); none otherwise.
    lost_sec: Optional[float] = None


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


def parse_drop(msg: dict) -> Optional[tuple[str, float]]:
    """The drop an abort message reports (`dropped`: its reason and the
    seconds of audio the page held and threw away), or None for an abort
    that reports none; ValueError when what it reports is wrong."""
    dropped = msg.get("dropped")
    if dropped is None:
        return None
    if not isinstance(dropped, dict) or dropped.get("reason") not in PROTOCOL["dropReasons"]:
        raise ValueError("dropped")
    return dropped["reason"], _number(dropped, "lostSec", cap=3600)


# ── the diagnosis ────────────────────────────────────────────────────────

def diagnose(s: Sample, cpu_count: int = os.cpu_count() or 1) -> tuple[str, Optional[Said]]:
    """One health state for a second, with its cause by code (none when
    healthy). The first that is true, in the order the audio travels: the
    microphone, the browser, the connection, the server, the decoder.
    Figures are rounded as the English says them."""
    if s.frames_captured == 0 and not s.tab_hidden:
        return "no-frames", Said("noFrames")
    if s.tab_hidden:
        return "browser-behind", Said("tabHidden")
    if s.held_sec is not None and s.held_sec >= HELD_WARN_SEC:
        return "browser-behind", Said("audioHeld", {"seconds": round(s.held_sec, 1)})
    if s.long_task_ms is not None and s.long_task_ms >= LONG_TASK_WARN_MS:
        return "browser-behind", Said("pageBusy", {"ms": round(s.long_task_ms)})
    if s.socket_sec is not None and s.socket_sec >= SOCKET_WARN_SEC:
        return "connection-behind", Said("audioInConnection", {"seconds": round(s.socket_sec, 1)})
    slow = (s.rtf is not None and s.rtf >= RTF_WARN) or s.backlog_sec >= BACKLOG_WARN_SEC
    if s.loop_delay_ms >= LOOP_DELAY_WARN_MS and not slow:
        return "server-starved", Said("serverLate", {"ms": round(s.loop_delay_ms)})
    if slow:
        others = s.load_avg - s.process_cpu / 100
        if others > cpu_count / 2:
            return "decoder-slow", Said("machineBusy", {"load": round(s.load_avg, 1)})
        return "decoder-slow", Said("jarvisBusy", {"cpu": round(s.process_cpu)})
    return "healthy", None


def cause_english(cause: Optional[Said]) -> str:
    """A cause in English: what the brain, the log and the record read."""
    return english(CAUSES[cause.code], cause.values) if cause else ""


def classify(s: Sample, cpu_count: int = os.cpu_count() or 1) -> tuple[str, str]:
    """One health state for a second, with its cause in a few words."""
    state, cause = diagnose(s, cpu_count)
    return state, cause_english(cause)


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
#
# Two logs, each a file a day (local date) in data_paths.voice_dir():
# `<date>.samples.jsonl` and `<date>.events.jsonl`, one JSON line a sample
# or event holding the columns of the tables they replaced (`voice_samples`,
# `voice_events`), each value as knowledge_move.cell keeps it. A line is
# only ever appended, on the thread the caller runs it on (the audio path's
# side thread, asyncio.to_thread); prune deletes a day's file once all of it
# is past its keeping time and rewrites the one day it cuts through. Each
# file is indexed in memory as it grows (`_Log`): every line's time and
# where it is, so a read opens only the lines it wants.

def _day(at: Any) -> str:
    """The local date a line belongs to, YYYY-MM-DD."""
    try:
        return time.strftime("%Y-%m-%d", time.localtime(float(at)))
    except (TypeError, ValueError, OverflowError, OSError):
        return "0000-00-00"


def _line(cells: dict) -> bytes:
    return (json.dumps({k: move.cell(v) for k, v in cells.items()}, ensure_ascii=False,
                       separators=(",", ":")) + "\n").encode("utf-8")


def _cells(raw: bytes) -> Optional[dict]:
    """A line's cells, or None for a line that is not one (torn by a crash)."""
    try:
        data = json.loads(raw)
        return {k: move.uncell(v) for k, v in data.items()} if isinstance(data, dict) else None
    except (ValueError, move.MoveError):
        return None


@dataclass
class _File:
    inode: int = -1
    offset: int = 0
    # Per line: (at, its offset, its length, what the log keeps of it).
    lines: list = field(default_factory=list)


class _Log:
    """One kind of line, a file a day, indexed in memory as it grows."""

    def __init__(self, kind: str, keep: Callable[[dict], Any]) -> None:
        self.kind = kind
        self.suffix = f".{kind}.jsonl"
        self._keep = keep
        self._files: dict[str, _File] = {}

    def path(self, day: str) -> Path:
        return data_paths.voice_dir() / f"{day}{self.suffix}"

    def days(self) -> list[tuple[str, Path]]:
        folder = data_paths.voice_dir()
        if not folder.is_dir():
            return []
        return sorted((p.name[:-len(self.suffix)], p) for p in folder.iterdir()
                      if p.name.endswith(self.suffix) and p.is_file())

    def append(self, cells: dict) -> None:
        folder = data_paths.voice_dir()
        folder.mkdir(parents=True, exist_ok=True)
        with open(self.path(_day(cells["at"])), "a+b") as f:
            end = f.seek(0, os.SEEK_END)
            torn = end and (f.seek(end - 1), f.read(1))[1] != b"\n"
            f.write((b"\n" if torn else b"") + _line(cells))

    def index(self, path: Path) -> list:
        """The file's lines as (at, offset, length, kept), read on from
        where the last look stopped."""
        key = str(path)
        held = self._files.setdefault(key, _File())
        try:
            info = path.stat()
        except FileNotFoundError:
            self._files.pop(key, None)
            return []
        if info.st_ino != held.inode or info.st_size < held.offset:
            held = self._files[key] = _File(info.st_ino)
        if info.st_size > held.offset:
            with open(path, "rb") as f:
                f.seek(held.offset)
                block = f.read(info.st_size - held.offset)
            start = 0
            while True:
                end = block.find(b"\n", start)
                if end < 0:
                    break
                cells = _cells(block[start:end])
                at = cells.get("at") if cells else None
                if isinstance(at, (int, float)):
                    held.lines.append((at, held.offset + start, end - start, self._keep(cells)))
                start = end + 1
            held.offset += start
        return held.lines

    def between(self, start: float, end: float) -> list[tuple[Path, tuple]]:
        """(file, line) for every line from `start` to `end`, by time."""
        first = _day(start - 86400) if math.isfinite(start) else "0000-00-00"
        last = _day(end + 86400) if math.isfinite(end) else "9999-99-99"
        found = [(at, n, i, path, line) for n, (day, path) in enumerate(self.days()) if first <= day <= last
                 for i, line in enumerate(self.index(path)) if start <= (at := line[0]) <= end]
        return [(path, line) for *_, path, line in sorted(found, key=lambda x: x[:3])]

    def read(self, chosen: list[tuple[Path, tuple]]) -> list[dict]:
        """The cells of the lines chosen, in their order."""
        by_file: dict[Path, list[tuple]] = {}
        for path, line in chosen:
            by_file.setdefault(path, []).append(line)
        got: dict[tuple, dict] = {}
        for path, lines in by_file.items():
            low = min(line[1] for line in lines)
            high = max(line[1] + line[2] for line in lines)
            try:
                with open(path, "rb") as f:
                    f.seek(low)
                    block = f.read(high - low)
            except FileNotFoundError:
                continue
            for line in lines:
                cells = _cells(block[line[1] - low:line[1] - low + line[2]])
                if cells is not None:
                    got[(path, line[1])] = cells
        return [got[(p, line[1])] for p, line in chosen if (p, line[1]) in got]

    def prune(self, cutoff: float) -> None:
        """Drop every line from before `cutoff`: a file all of it before, a
        file it cuts through written again without them. The index is kept
        only for the last two days, which every summary reads."""
        days = self.days()
        for day, path in days[:-2]:
            self._prune_file(day, path, cutoff)
            self._files.pop(str(path), None)
        for day, path in days[-2:]:
            self._prune_file(day, path, cutoff)

    def _prune_file(self, day: str, path: Path, cutoff: float) -> None:
        lines = self.index(path)
        if all(line[0] < cutoff for line in lines):
            if lines or day < _day(cutoff):
                path.unlink(missing_ok=True)
                self._files.pop(str(path), None)
            return
        if not any(line[0] < cutoff for line in lines):
            return
        kept = self.read([(path, line) for line in lines if line[0] >= cutoff])
        tmp = path.with_name(path.name + ".pruning")
        with open(tmp, "wb") as f:
            f.write(b"".join(_line(c) for c in kept))
        os.replace(tmp, path)
        self._files.pop(str(path), None)


_samples = _Log("samples", lambda c: (c.get("finals") or 0, c.get("typed") or 0)
                if c.get("finals") or c.get("typed") else None)
_events = _Log("events", lambda c: c)
# One writer at a time (appends, the event ids, prune), and one reader of the
# indexes at a time; a reader never waits on a writer, so a read never holds
# up a sample being written.
_writing = threading.Lock()
_reading = threading.RLock()
_next_event_id: dict[str, int] = {}


def record_sample(s: Sample) -> None:
    row = asdict(s)
    for key in _JSON_COLUMNS:
        row[key] = json.dumps(row[key])
    for key in ("tab_hidden", "quiet"):
        row[key] = None if row[key] is None else int(row[key])
    with _writing:
        _samples.append({c: row[c] for c in SAMPLE_COLUMNS})


def record_event(event: Event) -> int:
    assert event.kind in EVENT_KINDS, event.kind
    with _writing:
        folder = str(data_paths.voice_dir())
        if folder not in _next_event_id:
            with _reading:
                ids = [line[3].get("id") for _, path in _events.days() for line in _events.index(path)]
            _next_event_id[folder] = max((i for i in ids if isinstance(i, int)), default=0) + 1
        event_id = _next_event_id[folder]
        _next_event_id[folder] = event_id + 1
        _events.append({
            "id": event_id, "at": event.at, "capture": event.capture, "kind": event.kind,
            "source": event.source, "text": event.text, "cause": event.cause,
            "top_processes": json.dumps(event.top_processes) if event.top_processes else None,
            "cause_code": event.cause_code,
            "cause_values": json.dumps(event.cause_values) if event.cause_code else None,
            "lost_sec": event.lost_sec})
        return event_id


def prune(now: Optional[float] = None) -> None:
    now = time.time() if now is None else now
    with _writing, _reading:
        _samples.prune(now - SAMPLE_KEEP_SEC)
        _events.prune(now - EVENT_KEEP_SEC)


def _sample(row: dict) -> Sample:
    values = {c: row.get(c) for c in SAMPLE_COLUMNS}
    for key in _JSON_COLUMNS:
        values[key] = json.loads(values[key] or "{}")
    for key in ("tab_hidden", "quiet"):
        if values[key] is not None:
            values[key] = bool(values[key])
    return Sample(**values)


def _event(row: dict) -> Event:
    return Event(at=row["at"], kind=row["kind"], capture=row.get("capture"), source=row.get("source"),
                 text=row.get("text"), cause=row.get("cause"),
                 top_processes=json.loads(row.get("top_processes") or "[]"), id=row.get("id") or 0,
                 cause_code=row.get("cause_code"), cause_values=json.loads(row.get("cause_values") or "{}"),
                 lost_sec=row.get("lost_sec"))


def history(start: float, end: float) -> tuple[list[Sample], list[Event]]:
    """Samples and events from `start` to `end`, oldest first. A range
    longer than HISTORY_MAX_SAMPLES samples is thinned evenly."""
    with _reading:
        chosen = _samples.between(start, end)
        step = max(1, -(-len(chosen) // HISTORY_MAX_SAMPLES))
        samples = [_sample(c) for c in _samples.read(chosen[::step])]
        events = [_event(line[3]) for _, line in _events.between(start, end)]
    return samples, events


def summary(now: Optional[float] = None) -> dict:
    """What the Diagnostics tab shows: the latest state and cause, each
    stage of the pipeline, the last two minutes of samples, and counts of
    drops, reconnects and errors in the last hour."""
    now = time.time() if now is None else now
    samples, events = history(now - 120, now)
    hour_samples, hour_events = history(now - 3600, now)
    latest = samples[-1] if samples and now - samples[-1].at < 5 else None
    drops = [e.at for e in hour_events if e.kind in DROP_KINDS]
    return {
        "state": latest.state if latest else "idle",
        "cause": latest.cause if latest else "",
        "causeCode": latest.cause_code if latest else None,
        "causeValues": latest.cause_values if latest else {},
        "pipeline": pipeline(latest),
        "last2min": [sample_json(s) for s in samples],
        "incidentsLastHour": incident_counts(hour_events),
        # The heard share: sentences heard against speech dropped.
        "lastHour": {"finals": sum(x.finals for x in hour_samples), "drops": len(drops)},
        "lastDropAt": last_drop_at(),
        "today": today(now),
        "now": now,
    }


def today(now: float) -> dict:
    """Since local midnight: seconds listened (one sample a second), finals,
    typed messages, drops and reconnects."""
    local = time.localtime(now)
    midnight = time.mktime((local.tm_year, local.tm_mon, local.tm_mday, 0, 0, 0, 0, 0, -1))
    with _reading:
        seconds = _samples.between(midnight, float("inf"))
        kinds: dict[str, int] = {}
        for _, line in _events.between(midnight, float("inf")):
            kinds[line[3]["kind"]] = kinds.get(line[3]["kind"], 0) + 1
    counted = [line[3] for _, line in seconds if line[3]]
    return {"listenedSec": len(seconds) * SAMPLE_EVERY_SEC, "finals": sum(c[0] for c in counted),
            "typed": sum(c[1] for c in counted),
            "drops": sum(kinds.get(k, 0) for k in DROP_KINDS), "reconnects": kinds.get("reconnect", 0)}


def last_drop_at() -> Optional[float]:
    """When speech was last dropped, ever (within the record), or None."""
    with _reading:
        return max((line[0] for _, path in _events.days() for line in _events.index(path)
                    if line[3].get("kind") in DROP_KINDS), default=None)


def incident_counts(events: list[Event]) -> dict:
    return {
        "drops": sum(e.kind in DROP_KINDS for e in events),
        "reconnects": sum(e.kind == "reconnect" for e in events),
        "errors": sum(e.kind in INCIDENT_KINDS - DROP_KINDS for e in events),
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
        self._heard = asyncio.Event()       # telemetry arrived since the last sample
        self.state, self.cause = "healthy", ""
        self.cause_said: Optional[Said] = None

    def telemetry(self, telemetry: Telemetry) -> None:
        """The page's latest second. Its text lines are recorded now; its
        counts go into the next sample."""
        self._telemetry = telemetry
        self._heard.set()
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
        s.state, said = diagnose(s)
        s.cause = cause_english(said)
        s.cause_code, s.cause_values = (said.code, said.values) if said else (None, {})
        self.state, self.cause, self.cause_said = s.state, s.cause, said
        return s

    async def run(self) -> None:
        """One sample per second of the page's: taken as each telemetry
        arrives, so a sample and the page's report always belong together,
        or after TELEMETRY_LATE_SEC without one (a page that has stopped
        reporting still gets the server's own numbers recorded)."""
        taken = 0
        while True:
            try:
                await asyncio.wait_for(self._heard.wait(), SAMPLE_EVERY_SEC * TELEMETRY_LATE)
            except asyncio.TimeoutError:
                pass
            self._heard.clear()
            s = self.sample()
            taken += 1
            if taken % PRUNE_EVERY_SAMPLES == 0:
                record_soon_prune()
            try:
                await asyncio.to_thread(record_sample, s)
            except Exception:
                log.exception("voice sample not recorded")
            await self._send_health({
                "state": s.state, "cause": s.cause, "causeCode": s.cause_code,
                "causeValues": s.cause_values, "backlogSec": s.backlog_sec,
                "rtf": s.rtf, "loopDelayMs": s.loop_delay_ms,
                "processCpu": s.process_cpu, "loadAvg": s.load_avg,
                "pipeline": pipeline(s)})

    async def incident(self, kind: str, text: str = "", lost_sec: Optional[float] = None) -> None:
        """A failure, with the cause in force and the busiest processes.
        The cause and the time are taken first: listing the processes can
        take a while on a busy machine, and a sample taken meanwhile would
        put a later cause in place of the one this failure happened under."""
        at, cause, said = time.time(), self.cause, self.cause_said
        top = await asyncio.to_thread(top_processes)
        event = Event(at=at, kind=kind, capture=self._session, text=text or None,
                      cause=cause or None, top_processes=top,
                      cause_code=said.code if said else None, cause_values=said.values if said else {},
                      lost_sec=lost_sec)
        await asyncio.to_thread(record_event, event)
        _tell(event)


# Called on the event loop after each event is recorded (the server hints
# the page to read again).
listeners: list[Callable[[Event], None]] = []


async def record(event: Event) -> None:
    """Record off the event loop and tell the listeners; never raises."""
    try:
        await asyncio.to_thread(record_event, event)
    except Exception:
        log.exception("voice event not recorded")
        return
    _tell(event)


def _record_later(event: Event) -> None:
    """Record off the event loop, without making the caller wait."""
    asyncio.get_running_loop().create_task(record(event))


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


# ── the move: the two tables as the voice record's files ─────────────────

EVENT_COLUMNS = ("id", "at", "capture", "kind", "source", "text", "cause", "top_processes",
                 "cause_code", "cause_values", "lost_sec")


class _VoiceMapping:
    """voice_samples and voice_events as the day files above: each row one
    line, its rowid kept on a sample's (the table has no key of its own)."""
    name = "voice"
    tables = ("voice_samples", "voice_events")
    under: tuple = ()
    history = False
    files_under = ("voice",)

    def documents(self, conn):
        return iter(())

    def files(self, conn):
        for log, table in ((_samples, "voice_samples"), (_events, "voice_events")):
            days: dict[str, list[bytes]] = {}
            for row in move.table_rows(conn, table):
                days.setdefault(_day(row.cells["at"]), []).append(_line(move.row_data(row)))
            for day, lines in days.items():
                yield f"voice/{day}{log.suffix}", b"".join(lines)

    def rows(self, docs, files: move.Files) -> dict:
        found: dict[str, list] = {"voice_samples": [], "voice_events": []}
        for rel in files.list("voice"):
            table, columns = (("voice_samples", SAMPLE_COLUMNS) if rel.endswith(_samples.suffix) else
                              ("voice_events", EVENT_COLUMNS) if rel.endswith(_events.suffix) else (None, None))
            if table is None:
                continue
            for raw in files.read(rel).splitlines():
                data = json.loads(raw) if raw.strip() else None
                if isinstance(data, dict):
                    found[table].append(move.data_row(data, columns))
        return {"voice_samples": move.numbered(found["voice_samples"]),
                "voice_events": move.numbered(found["voice_events"], "id")}


MAPPINGS = [_VoiceMapping()]
