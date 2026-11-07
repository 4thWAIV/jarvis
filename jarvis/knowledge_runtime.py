"""The knowledge engine as the JARVIS server runs it (design sections 7.2
and 7.14, invariant I4).

Off unless `JARVIS_KNOWLEDGE` is on. When on, the server makes one
`KnowledgeRuntime` at startup; it waits `START_AFTER_SEC`, so the brain and
the recogniser are up first, then starts the engine through the
`Supervisor` and watches it every `knowledge_client.PING_EVERY_SEC`.

The pace follows hearing. Every `PACE_EVERY_SEC` the runtime decides
`hold` or `free` and tells the engine (`/v1/pace`, which stops its
compaction child and its embedding) and every listener (the ingest child):

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
import logging
import time
from typing import Callable, Optional

from jarvis import knowledge_client

log = logging.getLogger("jarvis.knowledge_runtime")

START_AFTER_SEC = 10.0
PACE_EVERY_SEC = 1.0
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


class KnowledgeRuntime:
    def __init__(self, *, floor_busy: Callable[[], bool],
                 cpu_percent: Callable[[], Optional[float]],
                 supervisor: Optional[knowledge_client.Supervisor] = None,
                 start_after: float = START_AFTER_SEC,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.floor_busy = floor_busy
        self.cpu_percent = cpu_percent
        self.supervisor = supervisor or knowledge_client.Supervisor()
        self.start_after = start_after
        self.pace = Pace(clock)
        self.started = False
        self.pace_listeners: list[Callable[[str], None]] = []
        self._sent: Optional[tuple[int, str]] = None     # (engine pid, mode) last told
        self.last_cpu: Optional[float] = None

    # -- the loop -----------------------------------------------------------
    async def run(self) -> None:
        await asyncio.sleep(self.start_after)
        await asyncio.to_thread(self.supervisor.start)
        self.started = True
        last_tick = time.monotonic()
        while True:
            try:
                await self.look_pace()
                if time.monotonic() - last_tick >= knowledge_client.PING_EVERY_SEC:
                    last_tick = time.monotonic()
                    await asyncio.to_thread(self.supervisor.tick)
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

    async def stop(self) -> None:
        await asyncio.to_thread(self.supervisor.stop)

    # -- what Diagnostics reads ---------------------------------------------
    def health(self) -> dict:
        sup = self.supervisor
        return {"state": sup.state if self.started else "starting",
                "reason": sup.last_reason, "restarts": len(sup.restarts),
                "pace": self.pace.mode, "pace_reason": self.pace.reason, "cpu_percent": self.last_cpu,
                "engine": sup.last_health if sup.state == "running" else None}
