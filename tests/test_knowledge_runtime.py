"""The knowledge engine as the server runs it (phase 4, first step): the
pace, the runtime's loop and the Diagnostics row."""
from __future__ import annotations

import asyncio

import pytest

from jarvis import knowledge_client as kc
from jarvis import knowledge_runtime as kr
from jarvis import subsystem_diagnostics as sd
from tests.fixtures.waiting import until_async


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


def test_the_floor_holds_the_engine():
    pace = kr.Pace(Clock())
    assert pace.decide(True, 5.0) == ("hold", "conversation")
    assert pace.decide(False, 5.0) == ("free", "free")
    assert pace.decide(False, None) == ("free", "free")


def test_a_busy_cpu_holds_but_breathes_once_a_minute():
    clock = Clock()
    pace = kr.Pace(clock)
    modes = []
    for _ in range(int(2 * kr.CPU_HOLD_MAX_SEC + 10)):
        modes.append(pace.decide(False, 95.0)[0])
        clock.t += 1.0
    frees = [i for i, m in enumerate(modes) if m == "free"]
    assert frees and frees[0] == kr.CPU_HOLD_MAX_SEC
    assert len(frees) == 2 * int(kr.BREATHE_SEC)          # one breath a minute, two seconds each
    assert modes[: int(kr.CPU_HOLD_MAX_SEC)] == ["hold"] * int(kr.CPU_HOLD_MAX_SEC)


def test_speech_during_a_breath_holds_at_once():
    clock = Clock()
    pace = kr.Pace(clock)
    for _ in range(int(kr.CPU_HOLD_MAX_SEC) + 1):
        pace.decide(False, 95.0)
        clock.t += 1
    assert pace.mode == "free"
    assert pace.decide(True, 95.0)[0] == "hold"


class FakeSupervisor:
    def __init__(self):
        self.state = "idle"
        self.started = 0
        self.ticks = 0
        self.paced = []
        self.restarts = []
        self.last_reason = None
        self.last_health = {"state": "ok", "store": {"writable": True}, "keyword": {"chunks": 7},
                            "vectors": {"embedder": None}}
        sup = self

        class Client:
            def pace(self, mode):
                sup.paced.append(mode)
                return {"pace": mode}
        self.client = Client()

    def start(self):
        self.started += 1
        self.state = "running"

    def pid(self):
        return 4242 if self.state == "running" else None

    def tick(self):
        self.ticks += 1
        return self.state

    def stop(self):
        self.state = "idle"


def test_the_runtime_starts_tells_the_pace_once_per_change_and_stops(monkeypatch):
    monkeypatch.setattr(kr, "PACE_EVERY_SEC", 0.01)
    monkeypatch.setattr(kc, "PING_EVERY_SEC", 0.02)
    busy = {"v": False}
    sup = FakeSupervisor()
    heard = []
    rt = kr.KnowledgeRuntime(floor_busy=lambda: busy["v"], cpu_percent=lambda: None,
                             supervisor=sup, start_after=0)
    rt.pace_listeners.append(heard.append)
    assert rt.health()["state"] == "starting"

    async def scenario():
        task = asyncio.create_task(rt.run())
        assert await until_async(lambda: sup.paced == ["free"]), sup.paced
        busy["v"] = True
        assert await until_async(lambda: sup.paced == ["free", "hold"]), sup.paced
        busy["v"] = False
        assert await until_async(lambda: sup.paced == ["free", "hold", "free"]
                                 and sup.ticks >= 2), (sup.paced, sup.ticks)
        await asyncio.sleep(0.05)             # more looks: a pace unchanged is not told again
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await rt.stop()
    asyncio.run(scenario())
    assert sup.started == 1 and sup.ticks >= 2
    assert sup.paced == ["free", "hold", "free"]
    assert "hold" in heard and heard[-1] == "free"
    assert sup.state == "idle"


def test_a_failing_floor_reading_holds():
    rt = kr.KnowledgeRuntime(floor_busy=lambda: 1 / 0, cpu_percent=lambda: None,
                             supervisor=FakeSupervisor(), start_after=0)
    assert asyncio.run(rt.look_pace()) == "hold"


# --- the Diagnostics row ---------------------------------------------------

def test_the_row_when_switched_off_is_idle():
    s = sd.knowledge(None, 0)
    assert s.state == sd.IDLE and s.said.code == "knowledgeOff"


@pytest.mark.parametrize("health, state, code", [
    ({"state": "starting"}, sd.UNKNOWN, "starting"),
    ({"state": "restarting", "reason": "exited with 1"}, sd.DEGRADED, "badCheck"),
    ({"state": "down", "reason": "exited with 1"}, sd.DEGRADED, "badCheck"),
    ({"state": "running", "engine": {"store": {"writable": False, "problems": ["log corrupt"]}}},
     sd.DEGRADED, "badCheck"),
    ({"state": "running", "pace": "free", "engine": {"store": {"writable": True}, "keyword": {"chunks": 3},
                                                     "vectors": {"embedder": None}}},
     sd.OK, "knowledgeOk"),
])
def test_the_row_is_never_worse_than_degraded(health, state, code):
    s = sd.knowledge(health, 0)
    assert s.state == state and s.said.code == code
    assert s.state != sd.DOWN


def test_the_row_says_the_pace_and_the_embedder():
    s = sd.knowledge({"state": "running", "pace": "hold", "pace_reason": "cpu", "cpu_percent": 91.4,
                      "engine": {"store": {"writable": True}, "keyword": {"chunks": 1},
                                 "vectors": {"embedder": "fake-32", "waiting": 2}}}, 0)
    checks = {c.id: c for c in s.checks}
    assert checks["pace"].said.code == "knowledgeHeldCpu" and checks["pace"].said.values == {"percent": 91}
    assert checks["embedder"].said.code == "knowledgeEmbedding"
    none = sd.knowledge({"state": "running", "pace": "hold", "pace_reason": "conversation",
                         "engine": {"store": {"writable": True}, "vectors": {}}}, 0)
    assert {c.id: c.said.code for c in none.checks}["embedder"] == "knowledgeNoEmbedder"
    assert {c.id: c.said.code for c in none.checks}["pace"] == "knowledgeHeld"
