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
They are files on disk (a log read whole, not records): the samples and
the events of each half hour, one JSON line each, in data_paths.voice_dir()
("the record" below), each file closed once the next is made and indexed by
the content ingest only then, once. A record from before the
codes has none, and the page shows its English as it was written.

transcription_api.py runs one `CaptureMonitor` per connected page; the
server's routes read `summary` and `history`.
"""
from __future__ import annotations

import asyncio
import contextlib
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

from jarvis import closed_files, data_paths, knowledge_move as move
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
# Two logs, samples and events, each a file every WINDOW_SEC (half an hour
# on the clock) in data_paths.voice_dir(): `<local date>/<HHMM>.samples.jsonl`
# and `<local date>/<HHMM>.events.jsonl`, named by the local time their half
# hour starts, one JSON line a sample or event holding the columns of the
# tables they replaced (`voice_samples`, `voice_events`), each value as
# knowledge_move.cell keeps it. A line goes in the file of its half hour, or,
# written late (its half hour's file closed or older than the newest), in
# the newest one. A line is only ever appended, on the thread the caller
# runs it on (the audio path's side thread, asyncio.to_thread).
#
# A file is closed (closed_files.close: made read-only) once the next one is
# made, and every file whose half hour has ended is closed within
# CLOSE_EVERY_SEC by the `Closer`, and at every prune, so the content ingest
# (knowledge_content.py, `/jarvis/voice`) reads each one once, whole, after
# it is closed. A name taken by a closed file gets `-1`, `-2`... (a late
# line, or the hour a clock change repeats). Prune deletes a file once all
# of it is past its keeping time, so a closed file is never written again.
# Each file is indexed in memory as it grows (`_Log`): every line's time and
# where it is, so a read opens only the lines it wants. A round-3 day file,
# `<date>.samples.jsonl` at the top of the folder, is read as one file
# spanning its day.

WINDOW_SEC = 30 * 60
CLOSE_EVERY_SEC = 60.0
# How far a line's time may lie outside its file's half hour: a line
# written late goes in the newest file, and a clock change repeats an hour.
LATE_SEC = 2 * 3600
# Each file's index is kept in memory while its half hour is this recent.
INDEX_KEEP_SEC = 2 * 86400
_DAY_SEC = 86400


def _window(at: Any) -> float:
    """The start of the half hour a time is in (epoch seconds)."""
    try:
        return math.floor(float(at) / WINDOW_SEC) * WINDOW_SEC
    except (TypeError, ValueError, OverflowError):
        return 0.0


def _local(day: str, hhmm: str = "0000") -> Optional[float]:
    """Epoch seconds of a local date and time as a file is named, or None."""
    try:
        y, m, d = (int(x) for x in day.split("-"))
        hour, minute = int(hhmm[:2]), int(hhmm[2:])
        if len(hhmm) != 4 or not (0 <= hour < 24 and 0 <= minute < 60):
            return None
        return time.mktime((y, m, d, hour, minute, 0, 0, 0, -1))
    except (ValueError, OverflowError):
        return None


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


@dataclass(frozen=True)
class _Piece:
    """One file of a log: the time it starts and how long it spans."""
    start: float
    span: float
    path: Path
    legacy: bool = False             # a round-3 day file

    @property
    def end(self) -> float:
        return self.start + self.span


def file_rel(kind: str, start: float, n: int = 0) -> str:
    """A half hour's file, relative to the voice folder: its local date's
    folder, then the local time it starts (and `-n` after a closed one)."""
    return time.strftime("%Y-%m-%d/%H%M", time.localtime(start)) + (f"-{n}" if n else "") + f".{kind}.jsonl"


class _Log:
    """One kind of line, a file every half hour, indexed in memory as it
    grows."""

    def __init__(self, kind: str, keep: Callable[[dict], Any]) -> None:
        self.kind = kind
        self.suffix = f".{kind}.jsonl"
        self._keep = keep
        self._files: dict[str, _File] = {}
        # The file being written, per voice folder: (its half hour, its path).
        self._current: dict[str, tuple[float, Path]] = {}

    def pieces(self) -> list[_Piece]:
        """Every file of this log, oldest first."""
        folder = data_paths.voice_dir()
        if not folder.is_dir():
            return []
        found: list[_Piece] = []
        for entry in folder.iterdir():
            if entry.is_symlink():
                continue
            if entry.is_dir():
                for p in entry.iterdir():
                    if p.name.endswith(self.suffix) and p.is_file() and not p.is_symlink():
                        start = _local(entry.name, p.name[:-len(self.suffix)].split("-", 1)[0])
                        if start is not None:
                            found.append(_Piece(start, WINDOW_SEC, p))
            elif entry.name.endswith(self.suffix) and entry.is_file():
                start = _local(entry.name[:-len(self.suffix)])
                if start is not None:
                    found.append(_Piece(start, _DAY_SEC, entry, legacy=True))
        return sorted(found, key=lambda p: (p.start, p.path.name))

    def append(self, cells: dict) -> None:
        path = self._open_for(_window(cells["at"]))
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a+b") as f:
            end = f.seek(0, os.SEEK_END)
            torn = end and (f.seek(end - 1), f.read(1))[1] != b"\n"
            f.write((b"\n" if torn else b"") + _line(cells))

    def _open_for(self, window: float) -> Path:
        """The file a line of this half hour goes in: the one being written,
        unless the line's half hour is newer or that file has been closed
        meanwhile; then the next file is made and every other one closed."""
        folder = data_paths.voice_dir()
        current = self._current.get(str(folder))
        if current is None:
            newest = [p for p in self.pieces() if not p.legacy]
            if newest:
                current = (_window(newest[-1].start), newest[-1].path)
        if current is not None and window <= current[0] and not closed_files.closed(current[1]):
            return current[1]
        window = max(window, current[0]) if current is not None else window
        n = 0
        while closed_files.closed(path := folder / file_rel(self.kind, window, n)):
            n += 1
        for piece in self.pieces():
            if piece.path != path:
                closed_files.close(piece.path)
        self._current[str(folder)] = (window, path)
        return path

    def close_ended(self, now: float) -> int:
        """Close every file whose span has ended; how many were closed now."""
        return sum(closed_files.close(p.path) for p in self.pieces()
                   if p.end <= now and not closed_files.closed(p.path))

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

    def all_lines(self) -> list:
        """Every line of every file, file by file."""
        return [line for piece in self.pieces() for line in self.index(piece.path)]

    def between(self, start: float, end: float) -> list[tuple[Path, tuple]]:
        """(file, line) for every line from `start` to `end`, by time."""
        found = [(at, n, i, piece.path, line) for n, piece in enumerate(self.pieces())
                 if piece.start - LATE_SEC <= end and piece.end + LATE_SEC >= start
                 for i, line in enumerate(self.index(piece.path)) if start <= (at := line[0]) <= end]
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

    def prune(self, cutoff: float, now: float) -> None:
        """Delete every file all of whose lines are from before `cutoff`
        (an empty one once its span is): whole files only, so a closed file
        is never written again, and a file `cutoff` cuts through stays until
        all of it is past. The index is kept only for the files of the last
        two days, which every summary reads."""
        for piece in self.pieces():
            lines = self.index(piece.path)
            if all(line[0] < cutoff for line in lines) and (lines or piece.end < cutoff):
                piece.path.unlink(missing_ok=True)
                self._files.pop(str(piece.path), None)
                if not piece.legacy:
                    with contextlib.suppress(OSError):
                        piece.path.parent.rmdir()          # only once it is empty
            elif piece.end < now - INDEX_KEEP_SEC:
                self._files.pop(str(piece.path), None)


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
                ids = [line[3].get("id") for line in _events.all_lines()]
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
    """Close every file whose half hour has ended, and delete what is past
    its keeping time."""
    now = time.time() if now is None else now
    with _writing, _reading:
        _samples.close_ended(now)
        _events.close_ended(now)
        _samples.prune(now - SAMPLE_KEEP_SEC, now)
        _events.prune(now - EVENT_KEEP_SEC, now)


def close_ended(now: Optional[float] = None) -> int:
    """Close every voice file whose half hour has ended (the newest one
    once the next half hour has begun, even with nothing written since), so
    the content ingest reads it; how many were closed now."""
    now = time.time() if now is None else now
    with _writing:
        return _samples.close_ended(now) + _events.close_ended(now)


class Closer:
    """Closes the voice files whose half hour has ended, every
    CLOSE_EVERY_SEC, off the event loop, for as long as speech input runs
    (transcription_api starts and stops it)."""

    def __init__(self) -> None:
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
            await asyncio.sleep(CLOSE_EVERY_SEC)
            try:
                await asyncio.to_thread(close_ended)
            except Exception:
                log.exception("voice files not closed")


def is_samples_file(name: str) -> bool:
    return name.endswith(_samples.suffix)


def samples_summary(data: bytes) -> dict:
    """What the content ingest keeps of a closed samples file: its summary
    fields only (a second's numbers have nothing to search). The time it
    spans, how many seconds were listened to, the sentences heard and
    messages typed, and every second that was not healthy, by state and by
    cause code."""
    first = last = None
    seconds = finals = typed = 0
    states: dict[str, int] = {}
    causes: dict[str, int] = {}
    for raw in data.splitlines():
        cells = _cells(raw) if raw.strip() else None
        at = cells.get("at") if cells else None
        if not isinstance(at, (int, float)):
            continue
        first = at if first is None else min(first, at)
        last = at if last is None else max(last, at)
        seconds += 1
        finals += int(cells.get("finals") or 0)
        typed += int(cells.get("typed") or 0)
        state = cells.get("state") or "healthy"
        if state != "healthy":
            states[str(state)] = states.get(str(state), 0) + 1
            code = cells.get("cause_code")
            if code:
                causes[str(code)] = causes.get(str(code), 0) + 1
    return {"from_at": first, "to_at": last, "samples": seconds,
            "listened_sec": seconds * SAMPLE_EVERY_SEC, "finals": finals, "typed": typed,
            "failures": sum(states.values()), "states": states, "causes": causes}


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
        return max((line[0] for line in _events.all_lines()
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
    """voice_samples and voice_events as the half-hour files above: each row
    one line, its rowid kept on a sample's (the table has no key of its own).
    The files are written open; the first prune closes every one whose half
    hour has ended."""
    name = "voice"
    tables = ("voice_samples", "voice_events")
    under: tuple = ()
    history = False
    files_under = ("voice",)

    def documents(self, conn):
        return iter(())

    def files(self, conn):
        for log, table in ((_samples, "voice_samples"), (_events, "voice_events")):
            windows: dict[float, list[bytes]] = {}
            for row in move.table_rows(conn, table):
                windows.setdefault(_window(row.cells["at"]), []).append(_line(move.row_data(row)))
            for start, lines in windows.items():
                yield f"voice/{file_rel(log.kind, start)}", b"".join(lines)

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
