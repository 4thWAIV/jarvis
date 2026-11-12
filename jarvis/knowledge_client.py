"""JARVIS's side of the knowledge engine: the client, its transports and the
supervisor (design sections 7.2, 7.3 and 18).

Only the JARVIS server holds the engine token. Before sending it, the
client checks that the address record's pid is the child the supervisor
started, so a stale or planted record never receives it. Every call has a
deadline (I3); a call past it raises `EngineUnavailable`, and the caller
answers from SQLite and Markdown instead.

Python tests reach the engine only through this module, with
`InProcessTransport` in place of HTTP, so a bare `pytest` starts no engine.

The engine is always up (UDS plan, "Always up"). The supervisor starts
`python -m jarvis.knowledge serve` with a pipe on its stdin (closing it ends
the engine, even across the server's `os.execv`), pings its health every
`PING_EVERY_SEC`, and restarts it whenever it exits or stops answering:
the first restart at once, then after each delay of `RESTART_BACKOFF_SEC`
while it keeps dying, the delays starting over once it has stayed healthy
`STABLE_AFTER_SEC`. It never gives up. Its physical footprint is measured
and reported against its budget, never acted on. It never adopts an engine
it did not start: an earlier engine still holding the lock is given time to
exit, then ended by its pid.
"""
from __future__ import annotations

import ctypes
import http.client
import json
import logging
import os
import subprocess
import sys
import time
from typing import Any, Callable, Optional, Protocol

from jarvis import data_paths
from jarvis.knowledge import address as engine_address, oscompat

log = logging.getLogger("jarvis.knowledge_client")

PING_EVERY_SEC = 5.0
PING_FAILURES_MAX = 2
RESTART_BACKOFF_SEC = (0.0, 1.0, 2.0, 5.0)
STABLE_AFTER_SEC = 60.0
READY_WAIT_SEC = 60.0
ENGINE_LOG_KEEP_BYTES = 1 << 20     # past this the engine's log keeps only its last quarter
PREVIOUS_EXIT_WAIT_SEC = 10.0
TERM_GRACE_SEC = 5.0
DEFAULT_DEADLINE_SEC = 0.5


def enabled() -> bool:
    """`JARVIS_KNOWLEDGE`: ingest, embedding and the knowledge tools, off by
    default. The engine itself runs whatever it says (`autostart`)."""
    return os.getenv("JARVIS_KNOWLEDGE", "0").strip().lower() in ("1", "true", "yes", "on")


def autostart() -> bool:
    """`JARVIS_ENGINE_AUTOSTART`, on by default: the server starts the engine
    first thing and keeps it up. Every test sets it off."""
    return os.getenv("JARVIS_ENGINE_AUTOSTART", "1").strip().lower() not in ("0", "false", "no", "off")


def memory_budget_mb() -> int:
    """`JARVIS_KNOWLEDGE_MEMORY_MB`, else 384, or 256 on a Mac with less than
    16 GB, and, unless it was set, more by the embedding model's measured
    share while one is installed (embedding_models.extra_memory_mb)."""
    raw = os.getenv("JARVIS_KNOWLEDGE_MEMORY_MB", "").strip()
    if raw.isdigit() and int(raw) > 0:
        return int(raw)
    try:
        total = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
    except (ValueError, OSError):
        total = 0
    from jarvis import embedding_models
    return (256 if 0 < total < 16 * (1 << 30) else 384) + embedding_models.extra_memory_mb()


class EngineUnavailable(RuntimeError):
    """The engine is down, late, or not the one we started."""


class Transport(Protocol):
    def request(self, method: str, path: str, body: Any, deadline: float) -> tuple[int, Any]: ...


class HTTPTransport:
    """Loopback HTTP to the engine this supervisor started, and no other."""

    def __init__(self, expected_pid: Callable[[], Optional[int]],
                 token: Callable[[], str] = data_paths.ensure_knowledge_token) -> None:
        self._expected_pid = expected_pid
        self._token = token

    def request(self, method: str, path: str, body: Any, deadline: float) -> tuple[int, Any]:
        found = engine_address.read()
        child = self._expected_pid()
        if found is None or child is None or found.pid != child:
            raise EngineUnavailable("no engine of ours is recorded")
        if found.host not in ("127.0.0.1", "localhost"):
            raise EngineUnavailable("the engine record is not loopback")
        data = None if body is None else json.dumps(body).encode("utf-8")
        conn = http.client.HTTPConnection(found.host, found.port, timeout=deadline)
        try:
            headers = {"Authorization": f"Bearer {self._token()}",
                       "Host": f"{found.host}:{found.port}"}
            if data is not None:
                headers["Content-Type"] = "application/json"
            conn.request(method, path, body=data, headers=headers)
            response = conn.getresponse()
            raw = response.read()
            return response.status, (json.loads(raw) if raw else None)
        except (OSError, http.client.HTTPException, ValueError) as e:
            raise EngineUnavailable(str(e)) from e
        finally:
            conn.close()


class InProcessTransport:
    """The routes called directly on an engine object, for tests."""

    def __init__(self, engine) -> None:
        self.engine = engine

    def request(self, method: str, path: str, body: Any, deadline: float) -> tuple[int, Any]:
        from jarvis.knowledge.serve import dispatch
        # The same JSON round trip HTTP would make, so nothing passes that
        # the wire could not carry.
        sent = None if body is None else json.loads(json.dumps(body))
        answer = dispatch(self.engine, method, path, sent)
        return answer.status, json.loads(json.dumps(answer.payload))


class KnowledgeClient:
    def __init__(self, transport: Transport) -> None:
        self.transport = transport

    def call(self, method: str, path: str, body: Any = None,
             deadline: float = DEFAULT_DEADLINE_SEC) -> Any:
        status, payload = self.transport.request(method, path, body, deadline)
        if status >= 500:
            raise EngineUnavailable(f"engine answered {status}")
        if status >= 400:
            raise KnowledgeRefused(status, payload)
        return payload

    def health(self, deadline: float = 1.0) -> dict:
        return self.call("GET", "/v1/health", deadline=deadline)

    def pace(self, mode: str) -> dict:
        return self.call("POST", "/v1/pace", {"mode": mode})


class KnowledgeRefused(RuntimeError):
    def __init__(self, status: int, payload: Any) -> None:
        super().__init__(f"{status}: {payload}")
        self.status = status
        self.payload = payload


# --- the cards JARVIS keeps in its own memory ------------------------------

HOT_CARDS_MAX_BYTES = 2 << 20


class HotCards:
    """At most `HOT_CARDS_MAX_BYTES` of the engine's most important cards
    (pinned topics first), held in the server's own memory, so the brain's
    context path has them before any engine round trip (design 7.13).
    Keyed by the engine's `engine_id`: a rebuilt engine's cards replace them
    all, and a forget clears them."""

    def __init__(self) -> None:
        self.engine_id: Optional[str] = None
        self.cards: dict[str, dict] = {}
        self.bytes = 0
        self.updated_at: Optional[float] = None

    def update(self, engine_id: Optional[str], cards: list[dict]) -> None:
        kept: dict[str, dict] = {}
        used = 0
        for card in cards:
            size = len(json.dumps(card, ensure_ascii=False))
            if used + size > HOT_CARDS_MAX_BYTES:
                break
            if isinstance(card, dict) and card.get("key"):
                kept[card["key"]] = card
                used += size
        self.engine_id, self.cards, self.bytes = engine_id, kept, used
        self.updated_at = time.time()

    def clear(self) -> None:
        self.cards, self.bytes = {}, 0

    def get(self, key: str) -> Optional[dict]:
        return self.cards.get(key)

    def find(self, words: str) -> list[dict]:
        """Cards whose label or an alias is these words, folded."""
        from jarvis import spoken_words
        want = " ".join(spoken_words.folded_words(words))
        out = []
        for card in self.cards.values():
            names = [card.get("label") or ""] + [a for cls in (card.get("aliases") or {}).values()
                                                  for a in (cls or [])]
            if any(" ".join(spoken_words.folded_words(n)) == want for n in names):
                out.append(card)
        return out


# --- physical footprint ---------------------------------------------------

RUSAGE_INFO_V2 = 2


class _RusageInfoV2(ctypes.Structure):
    _fields_ = [("ri_uuid", ctypes.c_uint8 * 16)] + [
        (name, ctypes.c_uint64) for name in (
            "ri_user_time", "ri_system_time", "ri_pkg_idle_wkups", "ri_interrupt_wkups",
            "ri_pageins", "ri_wired_size", "ri_resident_size", "ri_phys_footprint",
            "ri_proc_start_abstime", "ri_proc_exit_abstime", "ri_child_user_time",
            "ri_child_system_time", "ri_child_pkg_idle_wkups", "ri_child_interrupt_wkups",
            "ri_child_pageins", "ri_child_elapsed_abstime", "ri_diskio_bytesread",
            "ri_diskio_byteswritten")]


_libproc = None


def footprint_bytes(pid: int) -> Optional[int]:
    """Another process's physical footprint (`ri_phys_footprint`), which,
    unlike resident size, leaves out the clean memory-mapped vector pages a
    scan has just read. None off macOS or when the call fails."""
    global _libproc
    if sys.platform != "darwin":
        return None
    try:
        if _libproc is None:
            _libproc = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
            _libproc.proc_pid_rusage.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_void_p]
            _libproc.proc_pid_rusage.restype = ctypes.c_int
        info = _RusageInfoV2()
        if _libproc.proc_pid_rusage(pid, RUSAGE_INFO_V2, ctypes.byref(info)) != 0:
            return None
        return int(info.ri_phys_footprint)
    except OSError:
        return None


# --- supervision ----------------------------------------------------------

class Supervisor:
    """Starts, watches and ends the one engine this server owns, and keeps
    it up.

    `tick()` is called by the server's loop, every second while the engine
    is not running and every `PING_EVERY_SEC` while it is; it does one step
    at a time and never blocks long, so it can run beside the voice path.
    Every collaborator (spawn, clock, health, footprint) is a parameter, so
    the policy is tested without a process.
    """

    def __init__(self, *, spawn: Optional[Callable[[], subprocess.Popen]] = None,
                 clock: Callable[[], float] = time.monotonic,
                 health: Optional[Callable[[], dict]] = None,
                 footprint: Callable[[int], Optional[int]] = footprint_bytes,
                 budget_mb: Optional[int] = None) -> None:
        self._spawn = spawn or _spawn_engine
        self._clock = clock
        self._health = health
        self._footprint = footprint
        self.budget_bytes = (budget_mb or memory_budget_mb()) << 20
        self.proc: Optional[subprocess.Popen] = None
        self.state = "idle"          # idle, running, restarting
        self.restarts = 0            # every restart since this server started
        self.failures = 0            # deaths in a row, for the backoff
        self.ping_failures = 0
        self._restart_at: Optional[float] = None
        self._running_since: Optional[float] = None
        self.last_reason: Optional[str] = None
        self.last_health: Optional[dict] = None
        self.footprint: Optional[int] = None
        self.client = KnowledgeClient(HTTPTransport(self.pid))

    def pid(self) -> Optional[int]:
        if self.proc is None or self.proc.poll() is not None:
            return None
        return self.proc.pid

    def start(self) -> None:
        """Start the engine; a start that fails is tried again like a death."""
        self._restart_at = None
        try:
            end_previous_engine()
            self.proc = self._spawn()
        except OSError as e:
            self._schedule_restart(self._clock(), f"could not start: {e}")
            return
        self.state = "running"
        self.ping_failures = 0
        self.last_health = None
        self._running_since = self._clock()
        log.info("knowledge engine started, pid %s", self.proc.pid)

    def tick(self) -> str:
        now = self._clock()
        if self.state == "restarting":
            if self._restart_at is not None and now >= self._restart_at:
                self.start()
            return self.state
        if self.proc is None:
            return self.state
        if self.proc.poll() is not None:
            return self._schedule_restart(now, f"exited with {self.proc.returncode}")
        if not self._healthy():
            self.ping_failures += 1
            if self.ping_failures >= PING_FAILURES_MAX:
                self._end_child()
                return self._schedule_restart(now, "stopped answering its health check")
            return self.state
        self.ping_failures = 0
        if self._running_since is not None and now - self._running_since >= STABLE_AFTER_SEC:
            self.failures = 0
        self.footprint = self._footprint(self.proc.pid)
        return self.state

    def ready(self) -> bool:
        """Whether the engine runs and has answered its health check."""
        return self.state == "running" and self.pid() is not None and self._healthy()

    def _healthy(self) -> bool:
        try:
            body = self._health() if self._health else self.client.health()
        except (EngineUnavailable, KnowledgeRefused):
            return False
        self.last_health = body if isinstance(body, dict) else None
        return isinstance(body, dict) and body.get("state") in ("ok", "degraded")

    def _schedule_restart(self, now: float, reason: str) -> str:
        """Restart at once the first time, then after a short, capped delay
        while it keeps dying; never give up."""
        self.last_reason = reason
        self.last_health = None
        delay = RESTART_BACKOFF_SEC[min(self.failures, len(RESTART_BACKOFF_SEC) - 1)]
        self.failures += 1
        self.restarts += 1
        self.state = "restarting"
        self.proc = None
        self._running_since = None
        self._restart_at = now + delay
        log.warning("knowledge engine %s; restarting in %.0f s", reason, delay)
        if delay <= 0:
            self.start()
        return self.state

    def _end_child(self) -> None:
        if self.proc is None:
            return
        _end_process(self.proc)

    def health(self) -> dict:
        """The supervisor's own facts, for Diagnostics."""
        return {"state": self.state, "reason": self.last_reason, "restarts": self.restarts,
                "footprint_mb": None if self.footprint is None else self.footprint >> 20,
                "budget_mb": self.budget_bytes >> 20}

    def stop(self) -> None:
        """Close the pipe (the engine flushes and exits), then make sure."""
        self.state = "idle"
        self._restart_at = None
        if self.proc is not None:
            _end_process(self.proc)
            self.proc = None


def _spawn_engine() -> subprocess.Popen:
    """The engine, its stderr appended to its own log
    (`data_paths.knowledge_engine_log_path`), so a server that cannot reach
    it can say what it last said (`engine_log_tail`)."""
    env = dict(os.environ)
    path = data_paths.knowledge_engine_log_path()
    _trim_log(path)
    with open(path, "ab") as engine_log:
        return subprocess.Popen([sys.executable, "-m", "jarvis.knowledge", "serve"],
                                stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                stderr=engine_log, env=env, close_fds=True)


def _trim_log(path) -> None:
    """Keep the engine's log small: past ENGINE_LOG_KEEP_BYTES, its last
    quarter only."""
    try:
        if path.stat().st_size <= ENGINE_LOG_KEEP_BYTES:
            return
        with open(path, "rb") as f:
            f.seek(-(ENGINE_LOG_KEEP_BYTES // 4), os.SEEK_END)
            kept = f.read()
        path.write_bytes(kept[kept.find(b"\n") + 1:])
    except OSError:
        pass


def engine_log_tail(lines: int = 40) -> str:
    """The last lines the engine wrote, for a failure that must say why."""
    try:
        text = data_paths.knowledge_engine_log_path().read_text(errors="replace")
    except OSError:
        return "(the knowledge engine wrote no log)"
    return "\n".join(text.splitlines()[-lines:]) or "(the knowledge engine's log is empty)"


class EngineNotUp(RuntimeError):
    """The engine did not answer at startup. JARVIS's records live there, so
    the server does not start without it."""


def _end_process(proc: subprocess.Popen) -> None:
    try:
        if proc.stdin is not None:
            proc.stdin.close()
    except OSError:
        pass
    try:
        proc.wait(timeout=TERM_GRACE_SEC)
        return
    except subprocess.TimeoutExpired:
        pass
    proc.terminate()
    try:
        proc.wait(timeout=TERM_GRACE_SEC)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()


def end_previous_engine(*, wait_sec: float = PREVIOUS_EXIT_WAIT_SEC,
                        grace_sec: float = TERM_GRACE_SEC,
                        sleep: Callable[[float], None] = time.sleep) -> None:
    """An engine from before a restart sees its pipe close and exits; give it
    `wait_sec` to free the lock, then end the recorded pid. The lock decides
    whether it is still there, so no token is ever sent to it."""
    from jarvis.knowledge.engine import lock_is_free
    directory = data_paths.knowledge_dir()
    waited = 0.0
    while not lock_is_free(directory) and waited < wait_sec:
        sleep(0.1)
        waited += 0.1
    if lock_is_free(directory):
        return
    found = engine_address.running()
    if found is None:
        return
    log.warning("ending an earlier knowledge engine, pid %s, that kept the lock", found.pid)
    for forcefully, wait in ((False, grace_sec), (True, grace_sec)):
        try:
            oscompat.end(found.pid, forcefully)
        except ProcessLookupError:
            return
        spent = 0.0
        while spent < wait:
            if lock_is_free(directory):
                return
            sleep(0.1)
            spent += 0.1
