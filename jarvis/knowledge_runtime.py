"""The knowledge engine as the JARVIS server runs it (design sections 7.2
and 7.14, invariant I4).

The engine is always up (UDS plan, "Always up"): unless
`JARVIS_ENGINE_AUTOSTART` is off, the server makes one `KnowledgeRuntime`
first thing at startup, starts the engine through the `Supervisor`
(`start_engine`) and waits up to `knowledge_client.READY_WAIT_SEC` for its
health before serving the page; then `run` watches it, every second while
it is not running and every `knowledge_client.PING_EVERY_SEC` while it is.
`knowledge_settings` governs background indexing and embedding. Pausing
stops the ingest and content children; record storage and search remain
available. The engine reads the same saved setting from its own store.

The pace follows hearing, and holds only the background lane (lanes.py).
Every `PACE_EVERY_SEC` the runtime decides `hold` or `free` and tells the
engine (`/v1/pace`, which stops its compaction child, its embedding and
its maintenance; record reads and writes go on) and every listener (the
ingest child, stopped whole; the content child, whose reading stops while
its watcher and path entries go on):

- hold while the conversation floor is not idle: he is speaking, a turn is
  being gathered or checked, the recogniser is behind, the brain is
  answering, or JARVIS is speaking;
- hold while the machine's CPU is over `CPU_HOLD_PERCENT`, but give
  `BREATHE_SEC` free each `CPU_HOLD_MAX_SEC`, so a busy machine still sees
  one small batch a minute while the floor is free;
- free otherwise.

Nothing on the voice path waits on any of this: every blocking call runs
in a thread, and a failure is logged and tried again on the next look.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import subprocess
import sys
import time
from typing import Callable, Optional

from jarvis import data_paths, knowledge_client
from jarvis.knowledge_content import state_path
from jarvis.knowledge import address as engine_address

log = logging.getLogger("jarvis.knowledge_runtime")

INGEST_EVERY_SEC = 600.0
CONTENT_RESTART_SEC = 5.0
INGEST_AGAIN_SEC = 5.0
PROPOSALS_EVERY_SEC = 60.0
PACE_EVERY_SEC = 1.0
READY_POLL_SEC = 0.1
CPU_HOLD_PERCENT = 70.0
CPU_HOLD_MAX_SEC = 60.0
BREATHE_SEC = 2.0


class Pace:
    """The hold-or-free rule, apart from any process, so it is tested alone."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._cpu_since: Optional[float] = None
        self._breathe_until: Optional[float] = None
        self.mode = "free"
        self.reason = "free"

    def decide(self, floor_busy: bool, cpu_percent: Optional[float]) -> tuple[str, str]:
        now = self._clock()
        if floor_busy:
            self._cpu_since = None
            self._breathe_until = None
            self.mode, self.reason = "hold", "conversation"
        elif cpu_percent is not None and cpu_percent > CPU_HOLD_PERCENT:
            if self._breathe_until is not None and now < self._breathe_until:
                self.mode, self.reason = "free", "breathing"
            else:
                if self._breathe_until is not None:
                    self._breathe_until = None
                    self._cpu_since = now
                if self._cpu_since is None:
                    self._cpu_since = now
                if now - self._cpu_since >= CPU_HOLD_MAX_SEC:
                    self._breathe_until = now + BREATHE_SEC
                    self.mode, self.reason = "free", "breathing"
                else:
                    self.mode, self.reason = "hold", "cpu"
        else:
            self._cpu_since = None
            self._breathe_until = None
            self.mode, self.reason = "free", "free"
        return self.mode, self.reason


def ingest_pid_path():
    return data_paths.data_dir() / "knowledge-ingest.pid"


def _spawn_ingest() -> subprocess.Popen:
    return subprocess.Popen([sys.executable, "-m", "jarvis.knowledge_ingest"],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=None,
                            close_fds=True)


class IngestScheduler:
    """One ingest pass at a time, in its own child (knowledge_ingest.py):
    started while the pace is free and the engine runs, every
    `INGEST_EVERY_SEC`, and again soon after a pass that left more; stopped
    with SIGSTOP when the pace holds and continued with SIGCONT when it
    frees. A pass left stopped by a server that went away is ended at the
    next start (`end_leftover`)."""

    def __init__(self, supervisor, *, spawn: Callable[[], subprocess.Popen] = _spawn_ingest,
                 clock: Callable[[], float] = time.monotonic,
                 enabled: Callable[[], bool] = knowledge_client.enabled) -> None:
        self.supervisor = supervisor
        self.enabled = enabled
        self._spawn = spawn
        self._clock = clock
        self.proc: Optional[subprocess.Popen] = None
        self.stopped = False
        self.due_at = 0.0
        self.passes = 0
        self.last_finished_at: Optional[float] = None
        self.last_stats: Optional[dict] = None
        self.last_error: Optional[str] = None

    def wake(self) -> None:
        """Something changed (a hint): the next free moment runs a pass."""
        self.due_at = min(self.due_at, self._clock())

    def __call__(self, mode: str) -> None:
        self._reap()
        if not self.enabled():
            self.stop()
            return
        if self.proc is not None:
            want_stopped = mode == "hold"
            if want_stopped != self.stopped:
                self.proc.send_signal(signal.SIGSTOP if want_stopped else signal.SIGCONT)
                self.stopped = want_stopped
            return
        if mode == "free" and self.enabled() and self.supervisor.pid() is not None \
                and self._clock() >= self.due_at:
            self._start()

    def _start(self) -> None:
        found = engine_address.read()
        if found is None or found.pid != self.supervisor.pid():
            return
        self.proc = self._spawn()
        self.stopped = False
        try:
            ingest_pid_path().write_text(str(self.proc.pid))
        except OSError:
            pass
        given = json.dumps({"port": found.port, "token": data_paths.ensure_knowledge_token()})
        try:
            self.proc.stdin.write((given + "\n").encode())
            self.proc.stdin.close()
        except OSError as e:
            log.warning("could not hand the ingest pass its engine: %s", e)

    def _reap(self) -> None:
        proc = self.proc
        if proc is None or proc.poll() is None:
            return
        out = proc.stdout.read().decode("utf-8", "replace").strip().splitlines() if proc.stdout else []
        self.proc = None
        self.passes += 1
        self.last_finished_at = time.time()
        try:
            self.last_stats = json.loads(out[-1]) if out else None
        except ValueError:
            self.last_stats = None
        errors = (self.last_stats or {}).get("errors") or []
        self.last_error = errors[0] if errors else (
            None if proc.returncode == 0 else f"exited with {proc.returncode}")
        more = bool((self.last_stats or {}).get("more"))
        self.due_at = self._clock() + (INGEST_AGAIN_SEC if more else INGEST_EVERY_SEC)
        try:
            ingest_pid_path().unlink()
        except OSError:
            pass

    def stop(self) -> None:
        proc, self.proc = self.proc, None
        if proc is not None and proc.poll() is None:
            proc.kill()             # works on a stopped process too
            proc.wait()

    def health(self) -> dict:
        stats = self.last_stats or {}
        return {"running": self.proc is not None, "stopped": self.stopped, "passes": self.passes,
                "last_finished_at": self.last_finished_at, "error": self.last_error,
                "documents": stats.get("documents", 0), "chunks": stats.get("chunks", 0),
                "more": bool(stats.get("more"))}


def content_pid_path():
    return data_paths.data_dir() / "knowledge-content.pid"


def _spawn_content() -> subprocess.Popen:
    return subprocess.Popen([sys.executable, "-m", "jarvis.knowledge_content"],
                            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=None,
                            close_fds=True)


class ContentScheduler:
    """The content child (knowledge_content.py): one, long-lived, started
    while ingest is on and the engine runs, handed the engine's pid and
    token, told the pace as `hold` and `free` lines (its reading stops; its
    watcher and path entries go on), started again `CONTENT_RESTART_SEC`
    after it ends or when the engine is a new process, and ended with the
    server (`stop`, or its stdin closing)."""

    def __init__(self, supervisor, *, spawn: Callable[[], subprocess.Popen] = _spawn_content,
                 clock: Callable[[], float] = time.monotonic,
                 enabled: Callable[[], bool] = knowledge_client.enabled) -> None:
        self.supervisor = supervisor
        self.enabled = enabled
        self._spawn = spawn
        self._clock = clock
        self.proc: Optional[subprocess.Popen] = None
        self.engine_pid: Optional[int] = None
        self.mode: Optional[str] = None
        self.due_at = 0.0
        self.starts = 0
        self.last_exit: Optional[int] = None

    def __call__(self, mode: str) -> None:
        if self.proc is not None and self.proc.poll() is not None:
            self.last_exit = self.proc.returncode
            self._forget()
            self.due_at = self._clock() + CONTENT_RESTART_SEC
        engine = self.supervisor.pid()
        if self.proc is not None and (engine != self.engine_pid or not self.enabled()):
            self.stop()
        if self.proc is None:
            if self.enabled() and engine is not None and self._clock() >= self.due_at:
                self._start(engine)
            if self.proc is None:
                return
        if mode != self.mode:
            self._say(mode)

    def _start(self, engine: int) -> None:
        found = engine_address.read()
        if found is None or found.pid != engine:
            return
        self.proc = self._spawn()
        self.engine_pid = engine
        self.mode = None
        self.starts += 1
        try:
            content_pid_path().write_text(str(self.proc.pid))
        except OSError:
            pass
        self._say(json.dumps({"pid": engine, "token": data_paths.ensure_knowledge_token()}))

    def _say(self, line: str) -> None:
        try:
            self.proc.stdin.write((line + "\n").encode())
            self.proc.stdin.flush()
            if line in ("hold", "free"):
                self.mode = line
        except (OSError, ValueError) as e:
            log.warning("could not tell the content child %s: %s", line[:10], e)

    def _forget(self) -> None:
        self.proc = None
        self.engine_pid = None
        self.mode = None
        try:
            content_pid_path().unlink()
        except OSError:
            pass

    def stop(self) -> None:
        proc = self.proc
        if proc is not None:
            try:
                proc.stdin.close()          # its sign to finish what it holds and end
            except OSError:
                pass
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
        self._forget()

    def health(self) -> dict:
        try:
            state = json.loads(state_path().read_text())
        except (OSError, ValueError):
            state = {}
        return {"running": self.proc is not None, "starts": self.starts, "last_exit": self.last_exit,
                "paused": self.mode == "hold", **{k: v for k, v in state.items() if k != "paused"}}


def _end_leftover(pid_path, module: str) -> None:
    """A child the last server left behind (stopped, perhaps) is killed, but
    only when that pid is still that child."""
    try:
        pid = int(pid_path.read_text().strip())
    except (OSError, ValueError):
        return
    try:
        command = subprocess.run(["ps", "-o", "command=", "-p", str(pid)], capture_output=True,
                                 text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return
    if module in command:
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass
    try:
        pid_path.unlink()
    except OSError:
        pass


def end_leftover_ingest() -> None:
    _end_leftover(ingest_pid_path(), "jarvis.knowledge_ingest")


def end_leftover_content() -> None:
    _end_leftover(content_pid_path(), "jarvis.knowledge_content")


class KnowledgeRuntime:
    def __init__(self, *, floor_busy: Callable[[], bool],
                 cpu_percent: Callable[[], Optional[float]],
                 supervisor: Optional[knowledge_client.Supervisor] = None,
                 clock: Callable[[], float] = time.monotonic,
                 enabled: Callable[[], bool] = knowledge_client.enabled) -> None:
        self.floor_busy = floor_busy
        self.cpu_percent = cpu_percent
        self.supervisor = supervisor or knowledge_client.Supervisor()
        self.enabled = enabled
        self.pace = Pace(clock)
        self.started = False
        self.pace_listeners: list[Callable[[str], None]] = []
        self.ingest = IngestScheduler(self.supervisor, enabled=enabled)
        self.pace_listeners.append(self.ingest)
        self.content = ContentScheduler(self.supervisor, enabled=enabled)
        self.pace_listeners.append(self.content)
        # Told (new, gone) when the linker's proposals change; the server
        # records each as an attention item and resolves it when it goes.
        self.proposal_listeners: list[Callable[[list[dict], list[str]], None]] = []
        self.proposals: dict[str, dict] = {}
        self.hot = knowledge_client.HotCards()
        # Told when what the engine holds has changed (a pass ended, the hot
        # cards moved): the server sends the pages a `knowledge` hint.
        self.change_listeners: list[Callable[[], None]] = []
        self._seen_passes = 0
        self._sent: Optional[tuple[int, str]] = None     # (engine pid, mode) last told
        self.last_cpu: Optional[float] = None

    # -- the loop -----------------------------------------------------------
    async def start_engine(self, wait: float = knowledge_client.READY_WAIT_SEC) -> bool:
        """Start the engine and wait up to `wait` seconds for its health to
        answer, every blocking step in a thread. True once it answered."""
        await asyncio.to_thread(end_leftover_ingest)
        await asyncio.to_thread(end_leftover_content)
        await asyncio.to_thread(self.supervisor.start)
        self.started = True
        end = time.monotonic() + wait
        while True:
            if await asyncio.to_thread(self.supervisor.ready):
                return True
            if time.monotonic() >= end:
                return False
            await asyncio.sleep(READY_POLL_SEC)
            if self.supervisor.pid() is None:
                await asyncio.to_thread(self.supervisor.tick)

    async def run(self) -> None:
        if not self.started:
            await self.start_engine(wait=0)
        last_tick = time.monotonic()
        last_proposals = 0.0
        while True:
            try:
                await self.look_pace()
                down = self.supervisor.pid() is None
                if down or time.monotonic() - last_tick >= knowledge_client.PING_EVERY_SEC:
                    last_tick = time.monotonic()
                    await asyncio.to_thread(self.supervisor.tick)
                passed = self.ingest.passes != self._seen_passes
                if self.enabled() and self.supervisor.pid() and (
                        passed or time.monotonic() - last_proposals >= PROPOSALS_EVERY_SEC):
                    last_proposals = time.monotonic()
                    self._seen_passes = self.ingest.passes
                    await self.look_proposals()
                    await self.look_hot()
                    if passed:
                        self._changed()
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 - logged, looked at again next time
                log.warning("knowledge runtime: %s", e)
            await asyncio.sleep(PACE_EVERY_SEC)

    async def look_pace(self) -> str:
        try:
            busy = bool(self.floor_busy())
        except Exception:  # noqa: BLE001 - when in doubt, hearing comes first
            busy = True
        try:
            self.last_cpu = self.cpu_percent()
        except Exception:  # noqa: BLE001
            self.last_cpu = None
        mode, _ = self.pace.decide(busy, self.last_cpu)
        for listener in self.pace_listeners:
            try:
                listener(mode)
            except Exception as e:  # noqa: BLE001
                log.warning("knowledge pace listener: %s", e)
        pid = self.supervisor.pid()
        if pid is not None and self._sent != (pid, mode):
            try:
                await asyncio.to_thread(self.supervisor.client.pace, mode)
                self._sent = (pid, mode)
            except (knowledge_client.EngineUnavailable, knowledge_client.KnowledgeRefused) as e:
                log.debug("could not tell the engine its pace: %s", e)
        return mode

    async def look_proposals(self) -> None:
        try:
            found = await asyncio.to_thread(self.supervisor.client.call, "GET", "/v1/proposals")
        except (knowledge_client.EngineUnavailable, knowledge_client.KnowledgeRefused) as e:
            log.debug("could not read the proposals: %s", e)
            return
        self.take_proposals(found.get("proposals") or [])

    async def look_hot(self) -> None:
        try:
            found = await asyncio.to_thread(self.supervisor.client.call, "GET", "/v1/hot", None, 2.0)
        except (knowledge_client.EngineUnavailable, knowledge_client.KnowledgeRefused) as e:
            log.debug("could not read the hot cards: %s", e)
            return
        before = self.hot.engine_id
        self.hot.update(found.get("engine_id"), found.get("cards") or [])
        if before is not None and before != self.hot.engine_id:
            self._changed()

    def _changed(self) -> None:
        for listener in self.change_listeners:
            try:
                listener()
            except Exception as e:  # noqa: BLE001
                log.warning("knowledge change listener: %s", e)

    def take_proposals(self, proposals: list[dict]) -> None:
        now = {p["id"]: p for p in proposals if isinstance(p, dict) and p.get("id")}
        new = [p for pid, p in now.items() if pid not in self.proposals]
        gone = [pid for pid in self.proposals if pid not in now]
        self.proposals = now
        if new or gone:
            for listener in self.proposal_listeners:
                try:
                    listener(new, gone)
                except Exception as e:  # noqa: BLE001
                    log.warning("knowledge proposal listener: %s", e)

    async def stop(self) -> None:
        await asyncio.to_thread(self.ingest.stop)
        await asyncio.to_thread(self.content.stop)
        await asyncio.to_thread(self.supervisor.stop)

    # -- what Diagnostics reads ---------------------------------------------
    def health(self) -> dict:
        sup = self.supervisor
        facts = sup.health()
        return {**facts, "state": facts["state"] if self.started else "starting",
                "knowledge": self.enabled(),
                "pace": self.pace.mode, "pace_reason": self.pace.reason, "cpu_percent": self.last_cpu,
                "engine": sup.last_health if sup.state == "running" else None,
                "ingest": self.ingest.health(), "content": self.content.health()}
