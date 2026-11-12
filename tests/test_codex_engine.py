"""Codex agents as runs on the Codex app server (codex_engine.py), through
the real RunExecutor against a stand-in app server on a Unix socket
(fixtures/fake_app_server.py). No model runs, no real app server is reached
and no real Codex home is read: the engine's home is a fresh folder."""
import asyncio
import importlib
import shutil
import tempfile
from pathlib import Path

import pytest
import pytest_asyncio

from tests.fixtures.fake_app_server import FakeAppServer
from tests.fixtures.waiting import until_async


@pytest.fixture
def home():
    # A Unix socket's path is short on macOS (104 bytes): not under tmp_path.
    folder = Path(tempfile.mkdtemp(prefix="cx", dir="/tmp"))
    yield folder
    shutil.rmtree(folder, ignore_errors=True)


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    from jarvis import data_paths
    importlib.reload(data_paths)
    from jarvis import run_store
    importlib.reload(run_store)
    from jarvis import run_executor, codex_engine
    importlib.reload(run_executor)
    importlib.reload(codex_engine)
    return run_store, run_executor, codex_engine, tmp_path


async def _engine(env, home, **bounds):
    store, run_executor, codex_engine, _tmp = env
    ex = run_executor.RunExecutor(store)
    engine = codex_engine.CodexEngine(ex, lambda: home, **bounds)
    ex.add_engine(engine)
    seen = []
    ex.subscribe(seen.append)
    return ex, engine, seen


@pytest_asyncio.fixture
async def server(home):
    fake = FakeAppServer(home)
    await fake.start()
    yield fake
    await fake.drop()


@pytest.mark.asyncio
async def test_a_codex_run_is_a_thread_that_never_asks_and_has_no_sandbox(env, home, server):
    store, _, _, tmp = env
    ex, engine, seen = await _engine(env, home)
    run_id = await ex.spawn("make the header blue", "site", str(tmp), "voice",
                            engine="codex", model="gpt-test")
    run = await ex.wait_for(run_id)
    assert run["status"] == store.RunStatus.SUCCEEDED, run["error"]
    [start] = server.sent("thread/start")
    assert start == {"cwd": str(tmp), "model": "gpt-test", "approvalPolicy": "never",
                     "sandbox": "danger-full-access"}
    [turn] = server.sent("turn/start")
    assert turn == {"threadId": "thread-1",
                    "input": [{"type": "text", "text": "make the header blue"}]}
    assert run["engine"] == "codex" and run["engine_session"] == "thread-1"
    assert run["requested_model"] == "gpt-test" and run["model"] == "gpt-test"
    assert run["result_text"] == "Done: the header is blue." and run["is_error"] == 0
    assert (run["input_tokens"], run["output_tokens"], run["cache_read_tokens"]) == (1200, 340, 800)
    # The thread was on the run's record before the run was said to start.
    started = next(m for m in seen if m["type"] == "run_started")
    assert started["run"]["engine_session"] == "thread-1"
    await engine.close()


@pytest.mark.asyncio
async def test_its_events_are_the_run_events_every_reader_knows(env, home, server):
    store, _, _, tmp = env
    from jarvis import stream_parser
    ex, engine, _ = await _engine(env, home)
    run_id = await ex.spawn("make the header blue", "site", str(tmp), "voice", engine="codex")
    await ex.wait_for(run_id)
    events = [stream_parser.parse_line(e["payload"]) for e in store.get_events(run_id)]
    kinds = [e["type"] for e in events]
    assert kinds == ["system", "assistant", "assistant", "result"]
    assert events[0]["subtype"] == "init" and events[0]["session_id"] == "thread-1"
    assert stream_parser.assistant_parts(events[1]) == ("", ["Bash"])
    assert stream_parser.summarize_assistant(events[2]) == "Done: the header is blue."
    # So the outcome check reads it as a Claude run's: a command ran, it worked.
    assert stream_parser.assess_outcome(events, events[-1]["result"]) == stream_parser.OK
    await engine.close()


@pytest.mark.asyncio
async def test_a_failed_turn_fails_the_run_with_its_words(env, home, server):
    store, _, _, tmp = env
    server.fail_turn = "the model refused"
    ex, engine, _ = await _engine(env, home)
    run = await ex.wait_for(await ex.spawn("x", "site", str(tmp), "voice", engine="codex"))
    assert run["status"] == store.RunStatus.FAILED
    assert run["error"] == "the model refused" and run["is_error"] == 1
    await engine.close()


@pytest.mark.asyncio
async def test_an_untested_app_server_starts_nothing_and_says_why(env, home):
    store, _, _, tmp = env
    fake = FakeAppServer(home, version="0.999.0")
    await fake.start()
    try:
        ex, engine, _ = await _engine(env, home)
        run = await ex.wait_for(await ex.spawn("x", "site", str(tmp), "voice", engine="codex"))
        assert run["status"] == store.RunStatus.FAILED
        assert "0.999.0" in run["error"] and "has not been checked against" in run["error"]
        assert fake.sent("thread/start") == []
        await engine.close()
    finally:
        await fake.drop()


@pytest.mark.asyncio
async def test_no_app_server_fails_the_run_rather_than_leaving_it(env, home):
    store, _, _, tmp = env
    ex, engine, _ = await _engine(env, home)
    run = await ex.wait_for(await ex.spawn("x", "site", str(tmp), "voice", engine="codex"))
    assert run["status"] == store.RunStatus.FAILED
    assert run["error"].startswith("could not reach the Codex app server")


@pytest.mark.asyncio
async def test_an_unknown_engine_is_refused_before_any_run_is_recorded(env):
    store, run_executor, _, tmp = env
    ex = run_executor.RunExecutor(store)
    with pytest.raises(ValueError):
        await ex.spawn("x", "site", str(tmp), "voice", engine="gemini")
    assert store.list_runs() == []


@pytest.mark.asyncio
async def test_cancelling_interrupts_its_turn(env, home, server):
    store, _, _, tmp = env
    server.hold = True
    ex, engine, _ = await _engine(env, home)
    run_id = await ex.spawn("x", "site", str(tmp), "voice", engine="codex")
    assert await until_async(lambda: server._turn is not None)
    assert await ex.cancel(run_id) is True
    run = await ex.wait_for(run_id)
    assert run["status"] == store.RunStatus.CANCELLED
    assert server.sent("turn/interrupt") == [{"threadId": "thread-1", "turnId": "turn-1"}]
    await engine.close()


@pytest.mark.asyncio
async def test_steering_joins_the_turn_under_way(env, home, server):
    store, _, _, tmp = env
    from jarvis import session_steer
    server.hold = True
    ex, engine, _ = await _engine(env, home)
    run_id = await ex.spawn("x", "site", str(tmp), "voice", engine="codex")
    assert await until_async(lambda: server._turn is not None)
    assert await until_async(lambda: store.get_run(run_id)["status"] == "running")
    assert await ex.steer(run_id, "and the footer too") == session_steer.SENT
    assert server.sent("turn/steer") == [{"threadId": "thread-1", "expectedTurnId": "turn-1",
                                          "input": [{"type": "text", "text": "and the footer too"}]}]
    await server.finish_turn()
    assert (await ex.wait_for(run_id))["status"] == store.RunStatus.SUCCEEDED
    # A finished run, and a Claude run, cannot be steered.
    assert (await ex.steer(run_id, "more")).startswith("failed")
    await engine.close()


@pytest.mark.asyncio
async def test_a_claude_run_cannot_be_steered(env):
    store, run_executor, _, tmp = env
    ex = run_executor.RunExecutor(store)
    run_id = store.create_run("x", "site", str(tmp), "voice")
    store.update_run(run_id, status=store.RunStatus.RUNNING)
    said = await ex.steer(run_id, "more")
    assert said.startswith("failed") and "cannot be steered" in said


@pytest.mark.asyncio
async def test_a_retry_forks_the_thread_it_picks_up_from(env, home, server):
    store, _, _, tmp = env
    ex, engine, _ = await _engine(env, home)
    first = await ex.wait_for(await ex.spawn("x", "site", str(tmp), "voice", engine="codex"))
    again = await ex.wait_for(await ex.spawn("x", "site", str(tmp), "voice",
                                             resume_from=first["id"], engine="codex"))
    assert again["status"] == store.RunStatus.SUCCEEDED
    [fork] = server.sent("thread/fork")
    assert fork["threadId"] == "thread-1" and fork["approvalPolicy"] == "never"
    assert fork["sandbox"] == "danger-full-access"
    assert again["engine_session"] == "thread-2"
    await engine.close()


@pytest.mark.asyncio
async def test_after_the_app_server_restarts_the_run_is_picked_back_up(env, home, server):
    store, _, _, tmp = env
    server.hold = True
    ex, engine, _ = await _engine(env, home, reconnect_every_sec=0.05)
    run_id = await ex.spawn("x", "site", str(tmp), "voice", engine="codex")
    assert await until_async(lambda: server._turn is not None)
    await server.drop()
    # It finished while JARVIS was away: read back from the thread.
    server.turn_status_on_list = "completed"
    await server.start()
    run = await ex.wait_for(run_id)
    assert run["status"] == store.RunStatus.SUCCEEDED, run["error"]
    assert server.sent("thread/resume") == [{"threadId": "thread-1", "excludeTurns": True}]
    await engine.close()


@pytest.mark.asyncio
async def test_a_turn_lost_with_the_app_server_fails_the_run(env, home, server):
    store, _, _, tmp = env
    server.hold = True
    ex, engine, _ = await _engine(env, home, reconnect_every_sec=0.05)
    run_id = await ex.spawn("x", "site", str(tmp), "voice", engine="codex")
    assert await until_async(lambda: server._turn is not None)
    await server.drop()
    server._turn = {"thread": "thread-1", "id": "another-turn"}
    await server.start()
    run = await ex.wait_for(run_id)
    assert run["status"] == store.RunStatus.FAILED
    assert "lost when the Codex app server restarted" in run["error"]
    await engine.close()


@pytest.mark.asyncio
async def test_an_app_server_that_stays_away_fails_the_run_within_the_bound(env, home, server):
    store, _, _, tmp = env
    server.hold = True
    ex, engine, _ = await _engine(env, home, lost_bound_sec=0.3, reconnect_every_sec=0.05,
                                  poll_sec=0.05)
    run_id = await ex.spawn("x", "site", str(tmp), "voice", engine="codex")
    assert await until_async(lambda: server._turn is not None)
    await server.drop()
    run = await ex.wait_for(run_id)
    assert run["status"] == store.RunStatus.FAILED
    assert run["error"].startswith("lost the Codex app server")
    await engine.close()


@pytest.mark.asyncio
async def test_the_wall_clock_bound_interrupts_and_times_out(env, home, server):
    store, _, _, tmp = env
    server.hold = True
    ex, engine, _ = await _engine(env, home, poll_sec=0.05)
    run_id = await ex.spawn("x", "site", str(tmp), "voice", engine="codex", timeout_sec=0.5)
    run = await ex.wait_for(run_id)
    assert run["status"] == store.RunStatus.TIMED_OUT
    assert server.sent("turn/interrupt")
    await engine.close()


@pytest.mark.asyncio
async def test_its_plan_s_limits_are_kept_under_openai(env, home, server):
    _store, _, _, tmp = env
    from jarvis import usage_store
    ex, engine, _ = await _engine(env, home)
    await ex.wait_for(await ex.spawn("x", "site", str(tmp), "voice", engine="codex"))
    week = usage_store.week(service="openai")
    assert week["measured"] and week["used"] == 40.0
    assert usage_store.week(service="anthropic")["measured"] is False
    await engine.close()


@pytest.mark.asyncio
async def test_its_activity_is_openai_s_thread_while_it_runs(env, home, server):
    _store, _, _, tmp = env
    from jarvis import worker_activity
    server.hold = True
    ex, engine, _ = await _engine(env, home)
    run_id = await ex.spawn("x", "site", str(tmp), "voice", engine="codex")
    assert await until_async(lambda: worker_activity.managed("openai", "thread-1"))
    await server.finish_turn()
    await ex.wait_for(run_id)
    assert worker_activity.managed("openai", "thread-1") == {}
    await engine.close()


@pytest.mark.asyncio
async def test_it_answers_only_requests_about_its_own_threads(env, home, server):
    _store, _, _, tmp = env
    server.hold = True
    ex, engine, _ = await _engine(env, home)
    run_id = await ex.spawn("x", "site", str(tmp), "voice", engine="codex")
    assert await until_async(lambda: server._turn is not None)
    await server.ask(91, "item/commandExecution/requestApproval", {"threadId": "his-own-thread"})
    await server.ask(92, "item/commandExecution/requestApproval", {"threadId": "thread-1"})
    assert await until_async(lambda: server.answers)
    await asyncio.sleep(0.1)
    assert server.answers == [{"id": 92, "result": {"decision": "decline"}}]
    await server.finish_turn()
    await ex.wait_for(run_id)
    await engine.close()


def test_the_version_is_read_from_the_user_agent():
    from jarvis import codex_engine
    assert codex_engine.version_of({"userAgent": "codex_cli_rs/0.167.1 (Mac OS 15.0) x"}) == "0.167.1"
    assert codex_engine.version_of({}) is None
    # The release the daemon moved to on its own, checked and run against.
    assert codex_engine.check_version({"userAgent": "codex_cli_rs/0.167.2 (Mac OS 15.0)"}) == "0.167.2"
    with pytest.raises(codex_engine.Untested):
        codex_engine.check_version({"userAgent": "codex_cli_rs/0.1.0"})
