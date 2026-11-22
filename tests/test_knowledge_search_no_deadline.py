"""A knowledge search runs to the end and answers; it is cancelled only when
its caller no longer needs it, never by a timer; it runs in the engine's
fast lane with the background lane held; voice does not block on it
(construction-drafts.md "Knowledge search: no deadline that drops
results", approved 2026-11-21)."""
from __future__ import annotations

import asyncio
import socket
import threading
import time
from types import SimpleNamespace

import pytest

from jarvis import knowledge_api, knowledge_client as kc
from jarvis.knowledge import embedder as emb
from jarvis.knowledge import items as items_mod
from jarvis.knowledge import serve, vecmath
from jarvis.knowledge.engine import Engine
from jarvis.knowledge.uds import Caller, EngineUDS, Query
from tests.settings_fixtures import hold_setting

TIM = Caller("tim")


@pytest.fixture
def engine(tmp_path, monkeypatch):
    hold_setting("knowledge_enabled", "1")
    monkeypatch.setattr(Engine, "compact_in_child", False)
    monkeypatch.setattr(vecmath, "MATH", vecmath.Reference())
    monkeypatch.setattr(emb, "RUNNER", lambda: emb.FakeEmbedder(32))
    e = Engine.open(tmp_path / "k")
    yield e
    e.close()


def _uds(engine) -> EngineUDS:
    return EngineUDS(kc.InProcessTransport(engine))


def _fill(engine, n=12):
    uds = _uds(engine)
    for i in range(n):
        uds.put(TIM, f"/notes/n{i}.md", data={},
                content={"text": f"# Lighthouse {i}\nthe lighthouse keeper trims lamp {i}\n\n"
                                 f"# Other\nnothing about it here {i}"})
    assert engine.vectors.wait_idle(20)
    return uds


# --- the engine ---------------------------------------------------------------

def test_a_search_answers_in_full_however_long_it_takes(engine, monkeypatch):
    """No timer stops it: a clock that jumps a day at every look still
    gets every hit, and the answer is never partial."""
    uds = _fill(engine)
    ticks = iter(range(0, 10 ** 12, 86400))
    monkeypatch.setattr(items_mod.time, "monotonic", lambda: float(next(ticks)))
    found = uds.search_page(TIM, Query(meaning="lighthouse keeper", words="lighthouse keeper", limit=50))
    assert len(found["hits"]) == 12
    assert not found["partial"]
    assert all({"vector", "fulltext"} <= set(h.matched_by) for h in found["hits"])


def test_a_word_s_sections_are_read_only_for_the_hits_answered(engine, monkeypatch):
    uds = _fill(engine)
    read: list[str] = []
    real, real_many = engine.store.get, engine.store.newest_many

    def counting(family, key, *a, **k):
        if family == items_mod.CONTENT:
            read.append(key.decode())
        return real(family, key, *a, **k)

    def counting_many(family, keys, *a, **k):
        keys = list(keys)
        if family == items_mod.CONTENT:
            read.extend(key.decode() for key in keys)
        return real_many(family, keys, *a, **k)
    monkeypatch.setattr(engine.store, "get", counting)
    monkeypatch.setattr(engine.store, "newest_many", counting_many)
    found = uds.search_page(TIM, Query(words="lighthouse keeper", limit=3))
    assert len(found["hits"]) == 3
    assert sorted(read) == sorted(h.path for h in found["hits"])
    assert all(h.sections == (0,) for h in found["hits"])     # the Lighthouse section, not Other


def test_a_search_whose_caller_has_gone_stops_and_answers_nothing(engine):
    _fill(engine)
    with pytest.raises(items_mod.Cancelled):
        engine.items.search({"caller": {"person": "tim"}, "words": "lighthouse"}, gone=lambda: True)
    answer = serve.dispatch(engine, "POST", "/v1/items/search",
                            {"caller": {"person": "tim"}, "words": "lighthouse"}, gone=lambda: True)
    assert answer.status == serve.CANCELLED


def test_every_search_s_time_is_kept_in_the_engine_s_route_times(engine):
    _fill(engine)
    serve.dispatch(engine, "POST", "/v1/items/search", {"caller": {"person": "tim"}, "words": "lighthouse"})
    assert "POST /v1/items/search" in str(engine.route_times.snapshot())


def test_the_query_and_the_walk_have_no_timer(engine):
    from jarvis.knowledge import query
    engine.transaction({"puts": [{"_key": f"chunk:{i}", "_kind": "chunk", "text": "the lighthouse lamp"}
                                 for i in range(5)]})
    assert engine.vectors.wait_idle(20)
    with pytest.raises(Exception):
        query.run(engine, {"match": "lighthouse", "budget_ms": 1})     # no such clause any more
    found = query.run(engine, {"match": "lighthouse", "near": {"text": "lighthouse"}})
    assert not found["partial"] and len(found["hits"]) == 5
    with pytest.raises(items_mod.Cancelled):
        query.run(engine, {"match": "lighthouse"}, gone=lambda: True)


def test_a_search_holds_the_background_lane_while_it_runs(engine):
    """The vector build, compaction and maintenance are held while any
    search runs, nested searches included, and freed with the last."""
    assert not engine.held()
    with engine.searching():
        assert engine.held() and engine.vectors.held and engine.store.paused
        with engine.searching():
            assert engine.held()
        assert engine.held() and engine.vectors.held
    assert not engine.held() and not engine.vectors.held and not engine.store.paused
    engine.set_pace("hold")
    with engine.searching():
        pass
    assert engine.held() and engine.vectors.held          # the pace still holds it
    engine.set_pace("free")
    assert not engine.held()


def test_the_search_route_runs_in_the_fast_lane(engine, monkeypatch):
    _fill(engine)
    seen = {}
    real = engine.items.search

    def watching(body, gone=items_mod.never_gone):
        seen["held"] = engine.held()
        return real(body, gone)
    monkeypatch.setattr(engine.items, "search", watching)
    serve.dispatch(engine, "POST", "/v1/items/search", {"caller": {"person": "tim"}, "words": "lighthouse"})
    assert seen["held"] is True and not engine.held()


def test_the_linker_waits_while_a_search_runs(engine):
    went = threading.Event()
    with engine.searching():
        t = threading.Thread(target=lambda: (engine.wait_for_searches(), went.set()), daemon=True)
        t.start()
        time.sleep(0.2)
        assert not went.is_set()
    assert went.wait(5)
    assert engine.linker.wait_free == engine.wait_for_searches


def test_the_engine_sees_its_caller_close_the_connection():
    """serve's `gone`: a connection the caller closed reads nothing."""
    a, b = socket.socketpair()
    handler = SimpleNamespace(connection=a)
    assert serve._HTTPHandler._caller_gone(handler) is False
    b.close()
    assert serve._HTTPHandler._caller_gone(handler) is True
    a.close()


# --- the client ---------------------------------------------------------------

def test_cancel_ends_a_waiting_call_and_the_engine_s_search(engine):
    _fill(engine)
    transport = kc.InProcessTransport(engine)
    cancel = kc.Cancel()
    cancel.cancel()
    with pytest.raises(kc.Cancelled):
        transport.request("POST", "/v1/items/search", {"caller": {"person": "tim"}, "words": "lamp"}, None, cancel)


def test_cancel_closes_a_waiting_http_call(monkeypatch):
    """Over HTTP a search is waited for with no timeout; its caller's
    cancel closes the connection and the call raises Cancelled at once."""
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]
    held = []
    threading.Thread(target=lambda: held.append(listener.accept()), daemon=True).start()
    from jarvis.knowledge import address
    monkeypatch.setattr(address, "read", lambda: SimpleNamespace(host="127.0.0.1", port=port, started_by=(7,)))
    transport = kc.HTTPTransport(lambda: 7, token=lambda: "t")
    cancel = kc.Cancel()
    threading.Timer(0.3, cancel.cancel).start()
    began = time.monotonic()
    with pytest.raises(kc.Cancelled):
        transport.request("POST", "/v1/items/search", {"words": "x"}, None, cancel)
    assert time.monotonic() - began < 5
    listener.close()


def test_until_unneeded_waits_to_the_end_and_cancels_only_when_the_caller_goes():
    seen = {}

    def slow(cancel):
        time.sleep(0.4)
        return {"done": True}

    assert asyncio.run(knowledge_api.until_unneeded(slow)) == {"done": True}

    def endless(cancel):
        seen["cancel"] = cancel
        while not cancel.cancelled:
            time.sleep(0.01)
        raise kc.Cancelled("gone")

    async def gone_soon():
        gone_soon.n = getattr(gone_soon, "n", 0) + 1
        return gone_soon.n > 2

    with pytest.raises(kc.Cancelled):
        asyncio.run(knowledge_api.until_unneeded(endless, gone_soon))
    assert seen["cancel"].cancelled

    async def cancelled_waiter():
        task = asyncio.ensure_future(knowledge_api.until_unneeded(endless))
        await asyncio.sleep(0.1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    seen.clear()
    asyncio.run(cancelled_waiter())
    assert seen["cancel"].cancelled


def test_the_context_pack_waits_for_its_search():
    from jarvis import context_service as cs
    hits = [{"key": "/a.md", "label": "a.md", "source": "a.md", "trust": "own", "text": "the lighthouse",
             "score": 1.0}]

    def slow(text, own):
        time.sleep(0.6)       # longer than any deadline the pack once had
        return hits
    p = cs.pack(purpose="lighthouse", search=slow)
    assert p.tier == 1 and not p.partial


# --- package research's timing ------------------------------------------------

def test_each_role_search_s_seconds_go_in_the_run_s_timing(tmp_path):
    from jarvis import research_log, run_report
    log = tmp_path / research_log.NAME
    with run_report.step({"folder": str(tmp_path), "id": "j", "package": "p", "run": "r"}, "research"):
        research_log.record(log, "author", "knowledge_search", {"query": "x"}, True, '{"hits": []}',
                            seconds=3.25)
    timing = __import__("json").loads((tmp_path / run_report.TIMING).read_text())
    step = timing["steps"][0]
    assert step["searches"][0]["seconds"] == 3.25 and step["searches"][0]["tool"] == "knowledge_search"
    assert step["search_seconds"] == 3.25
    assert timing["totals"]["searches"] == 1 and timing["totals"]["search_seconds"] == 3.25


# --- voice does not block -------------------------------------------------------

def test_by_voice_a_slow_search_says_it_is_looking_and_answers_when_it_arrives(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from tests.test_brain_page_parity import _Brain, wired as wired_fixture  # noqa: F401
    import importlib
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path / "data"))
    hold_setting("brain_autostart", "0")
    from jarvis import data_paths
    importlib.reload(data_paths)
    from jarvis import server
    importlib.reload(server)
    release = threading.Event()

    def slow_search(ctx, text, **kw):
        cancel = kw.get("cancel")
        while not release.is_set():
            if cancel is not None and cancel.cancelled:
                raise kc.Cancelled("gone")
            time.sleep(0.01)
        return {"query": text, "hits": [{"key": "/notes/lamp.md", "text": "the lighthouse lamp"}]}
    monkeypatch.setattr(server.knowledge_api, "search", slow_search)
    monkeypatch.setattr(server, "KNOWLEDGE_LOOKING_SEC", 0.1)
    turns: list[tuple[str, str]] = []

    async def heard(text, heard=None, origin="user"):
        turns.append((text, origin))
    monkeypatch.setattr(server, "_handle_utterance", heard)
    brain = _Brain("user")
    brain.generation = 1
    monkeypatch.setattr(server, "brain_instance", server.brain_instance)
    with TestClient(server.app) as client:
        started, server.brain_instance = server.brain_instance, brain   # after the start sets its own
        r = client.post("/internal/tool", headers={"Authorization": f"Bearer {data_paths.ensure_tool_token()}"},
                        json={"tool": "knowledge_search", "arguments": {"query": "lighthouse"}})
        assert r.status_code == 200
        assert "Still looking" in r.json()["text"]
        assert not turns
        release.set()
        until = time.monotonic() + 5
        while not turns and time.monotonic() < until:
            time.sleep(0.02)
        server.brain_instance = started
        assert turns and turns[0][1] == "system"
        assert "lighthouse lamp" in turns[0][0] and "untrusted" in turns[0][0]


def test_a_search_the_brain_no_longer_needs_is_cancelled(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from tests.test_brain_page_parity import _Brain
    import importlib
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path / "data"))
    hold_setting("brain_autostart", "0")
    from jarvis import data_paths
    importlib.reload(data_paths)
    from jarvis import server
    importlib.reload(server)
    cancels: list = []

    def endless(ctx, text, **kw):
        cancels.append(kw["cancel"])
        while not kw["cancel"].cancelled:
            time.sleep(0.01)
        raise kc.Cancelled("gone")
    monkeypatch.setattr(server.knowledge_api, "search", endless)
    monkeypatch.setattr(server, "KNOWLEDGE_LOOKING_SEC", 0.1)
    brain = _Brain("user")
    brain.generation = 1
    monkeypatch.setattr(server, "brain_instance", server.brain_instance)
    with TestClient(server.app) as client:
        started, server.brain_instance = server.brain_instance, brain   # after the start sets its own
        r = client.post("/internal/tool", headers={"Authorization": f"Bearer {data_paths.ensure_tool_token()}"},
                        json={"tool": "knowledge_search", "arguments": {"query": "lighthouse"}})
        assert "Still looking" in r.json()["text"]
        brain.generation = 2                          # the conversation was replaced
        until = time.monotonic() + 5
        while not (cancels and cancels[0].cancelled) and time.monotonic() < until:
            time.sleep(0.02)
        server.brain_instance = started
        assert cancels and cancels[0].cancelled


def test_the_tool_servers_wait_for_a_search_with_no_timeout():
    """The brain's, a role's and an agent's tool servers wait for a search
    (and the context pack's) to answer, however long it takes."""
    from jarvis import jarvis_mcp
    assert jarvis_mcp.TIMEOUT_FOR["knowledge_search"] is None
    assert jarvis_mcp.TIMEOUT_FOR["work_context"] is None
