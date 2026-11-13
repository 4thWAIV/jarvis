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
        self.restarts = 0
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

    def ready(self):
        return self.state == "running"

    def health(self):
        return {"state": self.state, "reason": self.last_reason, "restarts": self.restarts,
                "footprint_mb": None, "budget_mb": 384}

    def stop(self):
        self.state = "idle"


def test_the_runtime_starts_tells_the_pace_once_per_change_and_stops(monkeypatch):
    monkeypatch.setattr(kr, "PACE_EVERY_SEC", 0.01)
    monkeypatch.setattr(kc, "PING_EVERY_SEC", 0.02)
    busy = {"v": False}
    sup = FakeSupervisor()
    heard = []
    rt = kr.KnowledgeRuntime(floor_busy=lambda: busy["v"], cpu_percent=lambda: None,
                             supervisor=sup)
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
                             supervisor=FakeSupervisor())
    assert asyncio.run(rt.look_pace()) == "hold"


# --- the Diagnostics row ---------------------------------------------------

def test_the_row_without_a_runtime_is_idle():
    s = sd.knowledge(None, 0)
    assert s.state == sd.IDLE and s.said.code == "knowledgeOff"


@pytest.mark.parametrize("health, state, process", [
    ({"state": "starting"}, sd.DOWN, "knowledgeNotUp"),
    ({"state": "idle"}, sd.DOWN, "knowledgeNotUp"),
    ({"state": "restarting", "reason": "exited with 1"}, sd.DOWN, "knowledgeRestarting"),
    ({"state": "running", "engine": None}, sd.DOWN, "knowledgeNotAnswering"),
    ({"state": "running", "engine": {"store": {"writable": False, "problems": ["log corrupt"]}}},
     sd.DEGRADED, "knowledgeReadOnly"),
    ({"state": "running", "pace": "free", "engine": {"store": {"writable": True}, "keyword": {"chunks": 3},
                                                     "vectors": {"embedder": None}}},
     sd.OK, "knowledgeOk"),
])
def test_an_engine_that_is_not_running_is_down(health, state, process):
    for on in (True, False):
        s = sd.knowledge({**health, "knowledge": on}, 0)
        assert s.state == state
        assert {c.id: c.said.code for c in s.checks}["process"] == process


def test_with_knowledge_off_ingest_is_switched_off_and_the_engine_can_be_fine():
    s = sd.knowledge({"state": "running", "knowledge": False, "pace": "free",
                      "ingest": {"running": False, "passes": 0},
                      "engine": {"store": {"writable": True}, "keyword": {"chunks": 0},
                                 "vectors": {"embedder": None}}}, 0)
    checks = {c.id: c for c in s.checks}
    assert s.state == sd.OK and s.said.code == "knowledgeOk"
    assert checks["ingest"].state == sd.IDLE and checks["ingest"].said.code == "knowledgeIngestOff"
    assert checks["embedder"].state == sd.IDLE and checks["embedder"].said.code == "knowledgeIngestOff"


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


# --- always up, whatever JARVIS_KNOWLEDGE says --------------------------------

class SlowSupervisor(FakeSupervisor):
    """Answers its health only after `answers_after` looks."""

    def __init__(self, answers_after):
        super().__init__()
        self.looks = 0
        self.answers_after = answers_after

    def ready(self):
        self.looks += 1
        return self.state == "running" and self.looks > self.answers_after


def test_start_engine_waits_for_its_health(monkeypatch):
    monkeypatch.setattr(kr, "READY_POLL_SEC", 0.001)
    monkeypatch.setattr(kr, "end_leftover_ingest", lambda: None)
    sup = SlowSupervisor(answers_after=3)
    rt = kr.KnowledgeRuntime(floor_busy=lambda: False, cpu_percent=lambda: None, supervisor=sup)
    assert asyncio.run(rt.start_engine(wait=5)) is True
    assert sup.started == 1 and sup.looks == 4 and rt.started


def test_start_engine_gives_up_waiting_but_not_on_the_engine(monkeypatch):
    monkeypatch.setattr(kr, "READY_POLL_SEC", 0.001)
    monkeypatch.setattr(kr, "end_leftover_ingest", lambda: None)
    sup = SlowSupervisor(answers_after=10 ** 9)
    rt = kr.KnowledgeRuntime(floor_busy=lambda: False, cpu_percent=lambda: None, supervisor=sup)
    assert asyncio.run(rt.start_engine(wait=0.05)) is False
    assert sup.state == "running" and rt.health()["state"] == "running"


def test_with_knowledge_off_no_ingest_pass_starts_and_no_proposals_are_read(monkeypatch):
    monkeypatch.setattr(kr, "PACE_EVERY_SEC", 0.01)
    monkeypatch.setattr(kr, "end_leftover_ingest", lambda: None)
    sup = FakeSupervisor()
    spawned = []
    rt = kr.KnowledgeRuntime(floor_busy=lambda: False, cpu_percent=lambda: None, supervisor=sup,
                             enabled=lambda: False)
    rt.ingest._spawn = lambda: spawned.append(1)
    looked = []

    async def proposals():
        looked.append(1)
    rt.look_proposals = proposals

    async def scenario():
        task = asyncio.create_task(rt.run())
        assert await until_async(lambda: sup.paced == ["free"] and sup.ticks >= 1)
        await asyncio.sleep(0.05)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    asyncio.run(scenario())
    assert sup.started == 1 and spawned == [] and looked == []
    health = rt.health()
    assert health["knowledge"] is False and health["state"] == "running"


def test_the_server_starts_the_engine_first_and_only_when_autostart_is_on(monkeypatch):
    from jarvis import server
    made = []

    class Runtime:
        def __init__(self, **kw):
            made.append(self)

        async def start_engine(self):
            return True
    monkeypatch.setattr(kr, "KnowledgeRuntime", Runtime)
    monkeypatch.setattr(server, "knowledge", None)
    asyncio.run(server.start_knowledge_engine())
    assert made == [] and server.knowledge is None          # conftest: autostart off
    monkeypatch.setenv("JARVIS_ENGINE_AUTOSTART", "1")
    monkeypatch.setenv("JARVIS_KNOWLEDGE", "0")
    asyncio.run(server.start_knowledge_engine())
    assert len(made) == 1 and server.knowledge is made[0]
    # Record health remains available even while indexing is disabled.
    assert server.knowledge_calls.runtime is made[0]
    monkeypatch.setenv("JARVIS_KNOWLEDGE", "1")
    assert server.knowledge_calls.runtime is made[0]


def test_the_server_does_not_start_without_its_engine_and_says_why(monkeypatch):
    """Its records live in the engine: an engine that never answers stops
    startup with the supervisor's reason and the end of the engine's own
    log, and the engine it started is stopped."""
    from jarvis import data_paths, server
    data_paths.knowledge_engine_log_path().write_text("line one\nthe engine's last words\n")
    stopped = []

    class Supervisor:
        client = kc.KnowledgeClient(kc.InProcessTransport(None))

        def health(self):
            return {"state": "restarting", "restarts": 3, "reason": "exited with 1"}

    class Runtime:
        def __init__(self, **kw):
            self.supervisor = Supervisor()

        async def start_engine(self):
            return False

        async def stop(self):
            stopped.append(True)
    monkeypatch.setattr(kr, "KnowledgeRuntime", Runtime)
    monkeypatch.setattr(server, "knowledge", None)
    monkeypatch.setattr(server.records, "bind", lambda uds: None)    # the test's own records stay
    monkeypatch.setenv("JARVIS_ENGINE_AUTOSTART", "1")
    with pytest.raises(kc.EngineNotUp) as refused:
        asyncio.run(server.start_knowledge_engine())
    said = str(refused.value)
    assert "exited with 1" in said and "the engine's last words" in said and stopped == [True]


def test_the_engine_listens_without_a_reverse_name_lookup(monkeypatch):
    """HTTPServer's bind looks up the address's name (socket.getfqdn), which
    the macOS CI runner took about 35 s over, so the engine had not listened
    30 s after it started. The engine's server never asks."""
    import socket
    from jarvis.knowledge import serve

    def never(*_a):
        raise AssertionError("a name lookup at bind")
    monkeypatch.setattr(socket, "getfqdn", never)
    server = serve.EngineHTTPServer(object(), "token")
    try:
        host, port = server.server_address[:2]
        assert host == "127.0.0.1" and port > 0 and server.server_name == "127.0.0.1"
    finally:
        server.server_close()


def test_the_engine_s_stderr_is_its_own_log_kept_small(monkeypatch):
    from jarvis import data_paths
    path = data_paths.knowledge_engine_log_path()
    assert kc.engine_log_tail() == "(the knowledge engine wrote no log)"
    path.write_bytes(b"x" * 10 + b"\n" + b"y\n" * (kc.ENGINE_LOG_KEEP_BYTES // 2 + 10))
    kc._trim_log(path)
    assert path.stat().st_size <= kc.ENGINE_LOG_KEEP_BYTES // 4 and path.read_bytes().startswith(b"y\n")
    assert kc.engine_log_tail(2) == "y\ny"


def test_the_engine_is_started_before_the_rest_of_startup():
    import inspect
    from jarvis import server
    # The rest of startup is `_start_on_records`, run once the records are in
    # the engine (at once, or after the records move, records_move.py).
    source = inspect.getsource(server.lifespan)
    assert source.index("await start_knowledge_engine()") < source.index("_start_on_records()")
    assert "_start_on_records()" in inspect.getsource(server._move_records_then_start)
    assert "run_store.init_db()" in inspect.getsource(server._start_on_records)
