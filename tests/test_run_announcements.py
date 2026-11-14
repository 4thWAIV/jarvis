"""JARVIS recording when work he started has ended.

Before the run pipeline had a voice subscriber, JARVIS would start a run and
never mention it again. Now each ending of a run he started is an attention
item (nothing is spoken by the server: the one admission policy and the
brain's digest decide that).

Two things are load-bearing.

**Origin.** Only runs JARVIS himself started (origin "voice") are recorded.
The user runs plenty of other things — from the dashboard, from work mode,
from a terminal — and those are not his to talk about.

**Which thread the executor calls back on.** The session watcher shipped the
opposite arrangement once: its callback fired on a poller thread, where
`asyncio.create_task` raises RuntimeError, the error was swallowed, and
announcements silently never happened while every test passed. So this file
asserts the property rather than assuming it — `test_the_executor_publishes_
on_the_event_loop_thread` drives a REAL RunExecutor and checks that
`asyncio.get_running_loop()` succeeds inside the subscriber.
"""

import asyncio
import importlib
import stat
import sys
import threading
from pathlib import Path

import pytest

from tests.attention_fixtures import attention, recorded  # noqa: F401  (a fixture)
from tests.settings_fixtures import hold_setting

sys.path.insert(0, str(Path(__file__).parent.parent))

FIXTURE = Path(__file__).parent / "fixtures" / "stream_success.jsonl"


@pytest.fixture
def wired(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path / "data"))
    hold_setting("brain_autostart", "0")
    from jarvis import data_paths
    importlib.reload(data_paths)
    from jarvis import run_store
    importlib.reload(run_store)
    from jarvis import server as server_module
    importlib.reload(server_module)
    run_store.init_db()
    return server_module


class FakeSpeech:
    def __init__(self, boom=False):
        self.calls = []
        self.boom = boom

    async def say(self, text, priority=None, **k):
        self.calls.append((text, priority))
        if self.boom:
            raise RuntimeError("the mouth is broken")


def _finished(project="chitauri", status="succeeded", origin="voice", **over):
    run = {"id": "r-1", "project_name": project, "status": status,
           "origin": origin, "error": "", "prompt": "build it"}
    run.update(over)
    return {"type": "run_finished", "run": run}


async def _drain(server):
    """Let the fire-and-forget announcement tasks run to completion."""
    for _ in range(10):
        pending = [t for t in server._bg_tasks if not t.done()]
        if not pending:
            break
        await asyncio.gather(*pending, return_exceptions=True)


# --- the origin gate --------------------------------------------------------

@pytest.fixture
def heard(wired, monkeypatch, attention):
    """The server with a fake voice and a fresh attention record."""
    speech = FakeSpeech()
    monkeypatch.setattr(wired, "speech", speech)
    monkeypatch.setattr(wired, "attention_record", attention)
    return wired, speech, attention


@pytest.mark.asyncio
async def test_a_run_jarvis_started_is_recorded_and_nothing_is_said(heard):
    server, speech, attention = heard
    server._on_run_event(_finished())
    await _drain(server)
    assert speech.calls == [], "the server speaks of no routine event itself"
    assert recorded(attention) == [("run", "r-1", "succeeded")]
    assert attention.items()[0].untrusted == {"project": "chitauri"}


@pytest.mark.asyncio
@pytest.mark.parametrize("origin", ["api", "self", "dashboard", "", None])
async def test_a_run_jarvis_did_not_start_is_not_recorded(heard, origin):
    """The user runs things of their own. Those are not his to report."""
    server, speech, attention = heard
    server._on_run_event(_finished(origin=origin))
    await _drain(server)
    assert speech.calls == [] and recorded(attention) == []


@pytest.mark.asyncio
async def test_only_finished_messages_are_recorded(heard):
    server, speech, attention = heard
    server._on_run_event({"type": "run_started", "run": _finished()["run"]})
    server._on_run_event({"type": "run_event", "run_id": "r-1"})
    server._on_run_event({"type": "run_updated", "run": _finished()["run"]})
    await _drain(server)
    assert recorded(attention) == []


# --- what is recorded -----------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["failed", "timed_out"])
async def test_a_failure_is_recorded_as_what_it_was(heard, status):
    server, speech, attention = heard
    server._on_run_event(_finished(status=status))
    await _drain(server)
    assert recorded(attention) == [("run", "r-1", status)] and speech.calls == []


@pytest.mark.asyncio
async def test_a_cancelled_run_is_not_recorded(heard):
    """The user asked for it."""
    server, speech, attention = heard
    server._on_run_event(_finished(status="cancelled"))
    await _drain(server)
    assert recorded(attention) == []


@pytest.mark.asyncio
async def test_a_run_s_lifecycle_is_one_item_with_its_history(heard):
    server, speech, attention = heard
    server._on_run_event(_finished(status="failed"))
    server._on_run_event(_finished(status="failed"))
    server._on_run_event(_finished(id="r-1", status="succeeded"))
    await _drain(server)
    (item,) = attention.items()
    assert item.event == "succeeded" and item.revision == 2
    assert [h["event"] for h in attention.history(item.id)] == ["failed", "succeeded"]


@pytest.mark.asyncio
async def test_a_burst_of_runs_is_one_item_each(heard):
    server, speech, attention = heard
    for n, project in enumerate(["chitauri", "hammer", "kestrel"]):
        server._on_run_event(_finished(id=f"r-{n}", project=project))
    await _drain(server)
    assert [k for k, _, _ in recorded(attention)] == ["run", "run", "run"]
    assert speech.calls == []


# --- nothing here may reach the executor ---------------------------------

@pytest.mark.asyncio
async def test_a_broken_record_never_escapes_the_callback(wired, monkeypatch):
    server = wired

    class Broken:
        def record(self, *a, **k):
            raise RuntimeError("the disk is full")

    monkeypatch.setattr(server, "attention_record", Broken())
    server._on_run_event(_finished())          # must not raise
    server._on_run_event(_finished(status="failed"))


@pytest.mark.asyncio
async def test_no_record_running_is_quiet_not_fatal(wired, monkeypatch):
    server = wired
    monkeypatch.setattr(server, "attention_record", None)
    monkeypatch.setattr(server, "speech", None)
    server._on_run_event(_finished())
    await _drain(server)


def test_the_voice_path_is_actually_subscribed(wired):
    """The wiring itself. Everything above tests `_on_run_event`; if nothing
    ever calls it, all of it is theatre."""
    server = wired
    assert server._on_run_event in server.run_executor_instance._subscribers


# --- the thread the executor calls back on -------------------------------

def _fake_claude(tmp_path: Path) -> str:
    script = tmp_path / "fake_claude_for_announcements.py"
    script.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        f"sys.stdout.write(open({str(FIXTURE)!r}).read())\n"
        "sys.stdout.flush()\n"
        "sys.exit(0)\n"
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return f"{sys.executable} {script}"


@pytest.fixture
def real_executor(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path / "data"))
    from jarvis import data_paths
    importlib.reload(data_paths)
    from jarvis import run_store
    importlib.reload(run_store)
    run_store.init_db()
    from jarvis import run_executor
    importlib.reload(run_executor)
    return run_store, run_executor


@pytest.mark.asyncio
async def test_the_executor_publishes_on_the_event_loop_thread(real_executor,
                                                               tmp_path):
    """The evidence behind `_on_run_event` using `_spawn` with no thread hop.

    Every publish comes out of `_finish` or `_publish_run_updated`, both
    reached only from inside the `_drive` task or `cancel()` — coroutines. If
    that ever stopped being true, `asyncio.create_task` inside the subscriber
    would raise RuntimeError on a worker thread and every announcement would
    silently stop, which is exactly the bug the session watcher shipped.
    """
    store, mod = real_executor
    here = threading.get_ident()
    loop = asyncio.get_running_loop()
    seen = []

    def cb(message):
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        seen.append((message["type"], threading.get_ident(), running))

    ex = mod.RunExecutor(store, claude_path=_fake_claude(tmp_path))
    ex.subscribe(cb)
    run_id = await ex.spawn("do a thing", "proj", str(tmp_path), "voice")
    await ex.wait_for(run_id)

    assert seen, "the executor published something"
    assert any(kind == "run_finished" for kind, _, _ in seen)
    for kind, ident, running in seen:
        assert ident == here, f"{kind} was published off the event loop thread"
        assert running is loop, f"{kind} had no running loop to schedule on"


@pytest.mark.asyncio
async def test_a_real_run_finishing_reaches_the_record(real_executor, tmp_path,
                                                       monkeypatch, attention):
    """End to end, with a real executor: `_on_run_event` is called by the real
    publish path, and the run's ending is an attention item; nothing is
    spoken."""
    store, mod = real_executor
    from jarvis import server as server_module
    importlib.reload(server_module)
    speech = FakeSpeech()
    monkeypatch.setattr(server_module, "speech", speech)
    monkeypatch.setattr(server_module, "attention_record", attention)

    ex = mod.RunExecutor(store, claude_path=_fake_claude(tmp_path))
    ex.subscribe(server_module._on_run_event)
    run_id = await ex.spawn("do a thing", "chitauri", str(tmp_path), "voice")
    await ex.wait_for(run_id)
    await _drain(server_module)

    assert speech.calls == []
    (item,) = attention.items()
    assert item.kind == "run" and item.source_id == run_id
    assert item.untrusted == {"project": "chitauri"}
