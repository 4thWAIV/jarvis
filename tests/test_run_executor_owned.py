"""RunExecutor ownership around spawn, cancellation and native descendants."""
import asyncio
import ctypes as C
import importlib
import os
import sys
from types import SimpleNamespace

import pytest

from jarvis import owned_process as owned


@pytest.fixture
def executor(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path / "data"))
    from jarvis import data_paths, run_store, run_executor
    importlib.reload(data_paths)
    importlib.reload(run_store)
    run_store.init_db()
    ex = run_executor.RunExecutor(run_store, grace_sec=2, poll_sec=0.02)
    return ex, run_store


@pytest.mark.asyncio
async def test_cancel_during_spawn_does_not_report_success_until_child_reaped(executor, monkeypatch, tmp_path):
    ex, store = executor
    entered, release = asyncio.Event(), asyncio.Event()
    actual_spawn = asyncio.create_subprocess_exec
    children = []

    async def delayed(*args, **kwargs):
        child = await actual_spawn(*args, **kwargs)
        children.append(child)
        entered.set()
        await release.wait()
        return child

    monkeypatch.setattr(asyncio, "create_subprocess_exec", delayed)
    marker = tmp_path / "work-started"
    ex._command = lambda *args: [sys.executable, "-c",
                                "from pathlib import Path; import time; "
                                f"Path({str(marker)!r}).write_text('started'); time.sleep(30)"]
    run_id = await ex.spawn("prompt", "fixture", str(tmp_path), "test")
    await asyncio.wait_for(entered.wait(), 5)
    cancellation = asyncio.create_task(ex.cancel(run_id))
    try:
        await asyncio.sleep(0.05)
        assert not cancellation.done()
        assert store.get_run(run_id)["status"] != store.RunStatus.CANCELLED
        release.set()
        assert await asyncio.wait_for(cancellation, 5)
        assert children[0].returncode is not None
        if os.name == "nt":
            assert not marker.exists(), "a cancelled suspended child executed work"
        assert (await ex.wait_for(run_id))["status"] == store.RunStatus.CANCELLED
        assert ex._procs == {}
        assert ex._slots._value == ex._max_concurrent
    finally:
        release.set()
        await asyncio.gather(cancellation, return_exceptions=True)


@pytest.mark.asyncio
async def test_unavailable_containment_is_a_visible_failed_run(executor, monkeypatch, tmp_path):
    ex, store = executor

    async def denied(*args, **kwargs):
        raise owned.ContainmentError("AssignProcessToJobObject refused")

    monkeypatch.setattr(owned, "create_subprocess_exec", denied)
    run_id = await ex.spawn("prompt", "fixture", str(tmp_path), "test")
    with pytest.raises(owned.ContainmentError, match="refused"):
        await ex.wait_for(run_id)
    run = store.get_run(run_id)
    assert run["status"] == store.RunStatus.FAILED
    assert "AssignProcessToJobObject refused" in run["error"]


@pytest.mark.asyncio
async def test_cancel_does_not_claim_success_when_tree_stop_fails(executor, tmp_path):
    ex, store = executor
    run_id = store.create_run("prompt", "fixture", str(tmp_path), "test")

    def refused():
        raise owned.ContainmentError("TerminateJobObject refused")

    job = SimpleNamespace(terminate=refused, close=lambda: None)
    ex._procs[run_id] = owned.OwnedProcess(SimpleNamespace(returncode=None), job)
    with pytest.raises(owned.ContainmentError, match="TerminateJobObject refused"):
        await ex.cancel(run_id)
    run = store.get_run(run_id)
    assert run["status"] == store.RunStatus.FAILED
    assert "TerminateJobObject refused" in run["error"]


@pytest.mark.skipif(os.name != "nt", reason="requires native Windows Job Objects")
@pytest.mark.asyncio
async def test_native_executor_cancel_stops_witness_grandchild(executor, tmp_path):
    ex, store = executor
    ready = tmp_path / "owned-descendant.pid"
    script = ("import subprocess, sys, time\nfrom pathlib import Path\n"
              "p=subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])\n"
              f"Path({str(ready)!r}).write_text(str(p.pid))\n"
              "time.sleep(30)\n")
    # Launch contract only: the native branch owns Windows CLI argv parsing.
    ex._command = lambda *args: [sys.executable, "-c", script]
    run_id = await ex.spawn("prompt", "fixture", str(tmp_path), "test")
    handle = None
    api = owned._WinAPI()
    api.dll.OpenProcess.argtypes = [owned.DWORD, C.c_int, owned.DWORD]
    api.dll.OpenProcess.restype = owned.HANDLE
    try:
        for _ in range(250):
            if ready.exists() and ready.read_text():
                break
            await asyncio.sleep(0.02)
        handle = api.dll.OpenProcess(0x00100000, False, int(ready.read_text()))
        assert handle
        assert api.call("WaitForSingleObject", handle, 0) == 258
        assert await asyncio.wait_for(ex.cancel(run_id), 6)
        assert api.call("WaitForSingleObject", handle, 0) == 0
        assert (await ex.wait_for(run_id))["status"] == store.RunStatus.CANCELLED
    finally:
        await ex.cancel(run_id)
        if handle:
            api.checked("CloseHandle", handle)
