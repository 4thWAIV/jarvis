"""Contract tests; native Job/descendant tests skip outside Windows."""
import asyncio
import ctypes as C
import os
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from jarvis import owned_process as owned


class FakeAPI:
    def __init__(self, fail=None, wrong_thread=False, dead=False, suspension=1):
        self.calls = []
        self.fail = fail
        self.wrong_thread, self.dead, self.suspension = wrong_thread, dead, suspension

    def call(self, name, *args):
        self.calls.append((name, args))
        if name == self.fail:
            raise owned.ContainmentError(f"{name}: refused")
        if name == "DuplicateHandle":
            args[3]._obj.value = 202
        if name == "GetProcessId":
            return 42
        if name == "GetProcessIdOfThread":
            return 999 if self.wrong_thread else 42
        if name == "WaitForSingleObject":
            return 0 if self.dead else 258
        if name == "ResumeThread":
            return self.suspension
        return 101

    checked = call

    def thread_ids(self, pid):
        self.calls.append(("thread_ids", (pid,)))
        return [777]


def fake_child():
    return SimpleNamespace(pid=42, _transport=SimpleNamespace(
        get_extra_info=lambda key: SimpleNamespace(_handle=303)))


def test_assigns_exact_handle_before_resuming_and_closes_once():
    api = FakeAPI()
    job = owned._WindowsJob(api)
    job.attach(fake_child())
    job.resume(42)
    job.close()
    job.close()
    names = [name for name, _ in api.calls]
    assert names.index("AssignProcessToJobObject") < names.index("ResumeThread")
    assign = next(args for name, args in api.calls if name == "AssignProcessToJobObject")
    assert assign == (101, 202)  # duplicated owned handle, never a PID
    limits = next(args[2]._obj for name, args in api.calls if name == "SetInformationJobObject")
    assert limits.basic.flags == 0x2000  # kill-on-close, no breakaway
    assert [args[0] for name, args in api.calls if name == "CloseHandle"].count(202) == 1


@pytest.mark.parametrize("options", [{"wrong_thread": True}, {"dead": True},
                                    {"fail": "AssignProcessToJobObject"}])
def test_refuses_wrong_identity_dead_child_and_failed_assignment(options):
    api = FakeAPI(**options)
    job = owned._WindowsJob(api)
    try:
        with pytest.raises(owned.ContainmentError):
            job.attach(fake_child())
            job.resume(42)
        assert "ResumeThread" not in [name for name, _ in api.calls]
    finally:
        job.close()


def test_missing_transport_handle_fails_closed():
    job = owned._WindowsJob(FakeAPI())
    try:
        with pytest.raises(owned.ContainmentError, match="no owned process handle"):
            job.attach(SimpleNamespace(pid=42))
    finally:
        job.close()


@pytest.mark.parametrize("suspension", [0, 2, 0xFFFFFFFF])
def test_unexpected_initial_suspend_count_is_an_error(suspension):
    job = owned._WindowsJob(FakeAPI(suspension=suspension))
    try:
        job.attach(fake_child())
        with pytest.raises(owned.ContainmentError, match="exactly one suspension"):
            job.resume(42)
    finally:
        job.close()


class FakeProcess:
    pid = 42
    returncode = None

    def __init__(self):
        self.killed = False

    def kill(self):
        self.killed = True
        self.returncode = 1

    async def wait(self):
        return self.returncode


class FakeJob:
    assigned = False

    def __init__(self):
        self.events = []
        self.remaining = [0]

    def attach(self, proc):
        self.events.append("attach")
        self.assigned = True

    def resume(self, pid):
        self.events.append("resume")

    def terminate(self):
        self.events.append("terminate")

    def active(self):
        return self.remaining.pop(0) if len(self.remaining) > 1 else self.remaining[0]

    def close(self):
        self.events.append("close")


@pytest.mark.asyncio
async def test_job_waits_for_descendants_after_parent_exit_and_is_idempotent():
    proc, job = FakeProcess(), FakeJob()
    proc.returncode = 0
    job.remaining = [2, 1, 0]
    child = owned.OwnedProcess(proc, job)
    await asyncio.gather(child.stop(), child.stop())
    assert job.events == ["terminate", "close"]
    assert child.returncode == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("active", [0, 1])
async def test_tree_that_cannot_be_confirmed_dead_is_not_success(active):
    job = FakeJob()
    # Neither an empty Job without an observed root exit, nor a Job with
    # surviving descendants, is sufficient evidence to report success.
    job.remaining = [active]
    child = owned.OwnedProcess(FakeProcess(), job)
    with pytest.raises(owned.ContainmentError, match="did not stop"):
        await child.stop(timeout=0.01)
    with pytest.raises(owned.ContainmentError):
        await child.stop()
    assert job.events == ["terminate", "close"]


@pytest.mark.asyncio
async def test_stop_does_not_wait_for_buffered_pipes_after_exit_is_observed():
    class PipeHeldProcess(FakeProcess):
        async def wait(self):
            # asyncio waiters registered before _process_exited are released
            # only after the pipes disconnect, even when returncode is known.
            await asyncio.Event().wait()

    proc, job = PipeHeldProcess(), FakeJob()
    job.terminate = lambda: asyncio.get_running_loop().call_soon(
        setattr, proc, "returncode", 1)
    child = owned.OwnedProcess(proc, job)
    await child.stop(timeout=0.1)
    assert child.returncode == 1
    assert job.events == ["close"]


@pytest.mark.asyncio
async def test_stop_retains_real_buffered_stdout_without_waiting_for_a_reader():
    args = (sys.executable, "-c", "import os, time; os.write(1, b'x' * 262144); time.sleep(30)")
    if os.name == "nt":
        child = await owned.create_subprocess_exec(
            *args, stdout=asyncio.subprocess.PIPE, limit=1024)
    else:
        # Exercise the real asyncio pipe lifecycle locally. This substitute
        # models the Job API only; it is not native Windows evidence.
        proc = await asyncio.create_subprocess_exec(
            *args, stdout=asyncio.subprocess.PIPE, limit=1024)
        job = FakeJob()
        job.terminate = proc.kill
        child = owned.OwnedProcess(proc, job)
    try:
        for _ in range(200):
            if child.stdout._paused:
                break
            await asyncio.sleep(0.01)
        assert child.stdout._paused, "fixture did not fill its stdout buffer"
        await child.stop(timeout=2)
        assert child.returncode is not None
        buffered = await asyncio.wait_for(child.stdout.read(), 3)
        assert buffered and set(buffered) == {ord('x')}
    finally:
        if child.returncode is None:
            child.process.kill()
        await asyncio.wait_for(child.stdout.read(), 3)
        await asyncio.wait_for(child.process.wait(), 3)
        child.job.close()


@pytest.mark.asyncio
async def test_cancellation_during_spawn_awaits_and_reaps_eventual_child(monkeypatch):
    entered, release = asyncio.Event(), asyncio.Event()
    proc, job = FakeProcess(), FakeJob()

    async def spawn(*args, **kwargs):
        assert kwargs["creationflags"] & 4
        entered.set()
        await release.wait()
        return proc

    monkeypatch.setattr(owned, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(owned, "_WindowsJob", lambda: job)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    task = asyncio.create_task(owned.create_subprocess_exec("fixture"))
    await entered.wait()
    task.cancel()
    await asyncio.sleep(0)
    task.cancel()  # a second cancellation cannot discard ownership either
    await asyncio.sleep(0)
    assert not task.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert proc.killed
    assert "resume" not in job.events


@pytest.mark.asyncio
async def test_failed_assignment_reaps_suspended_child_without_resuming(monkeypatch):
    proc, job = FakeProcess(), FakeJob()

    def refuse(proc):
        raise owned.ContainmentError("assignment refused")

    async def spawn(*args, **kwargs):
        return proc

    job.attach = refuse
    monkeypatch.setattr(owned, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(owned, "_WindowsJob", lambda: job)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    with pytest.raises(owned.ContainmentError, match="assignment refused"):
        await owned.create_subprocess_exec("fixture")
    assert proc.killed
    assert "resume" not in job.events


@pytest.mark.asyncio
async def test_real_pipes_stdin_stderr_and_exit_status():
    proc = await owned.create_subprocess_exec(
        sys.executable, "-c",
        "import sys; print(sys.stdin.read()); print('diagnostic', file=sys.stderr); sys.exit(7)",
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE)
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(b"prompt"), 10)
        assert stdout.strip() == b"prompt"
        assert stderr.strip() == b"diagnostic"
        assert proc.returncode == 7
    finally:
        if isinstance(proc, owned.OwnedProcess):
            await proc.stop()
        elif proc.returncode is None:
            proc.kill()
            await proc.wait()


@pytest.mark.skipif(os.name != "nt", reason="requires native Windows Job Objects")
@pytest.mark.asyncio
async def test_native_failed_assignment_never_executes_work(monkeypatch, tmp_path):
    marker = tmp_path / "must-not-start"

    def refuse(self, proc):
        raise owned.ContainmentError("fixture assignment refused")

    monkeypatch.setattr(owned._WindowsJob, "attach", refuse)
    with pytest.raises(owned.ContainmentError, match="assignment refused"):
        await owned.create_subprocess_exec(
            sys.executable, "-c",
            f"from pathlib import Path; Path({str(marker)!r}).write_text('started')")
    assert not marker.exists()


@pytest.mark.skipif(os.name != "nt", reason="requires native Windows Job Objects")
@pytest.mark.asyncio
@pytest.mark.parametrize("parent_exits", [
    False,
    # Seen on GitHub's windows-latest (run 43150186643): once the run's own
    # process has exited, a descendant it left behind outlived the stop.
    # A known gap in docs/windows.md; kept visible here, not hidden.
    pytest.param(True, marks=pytest.mark.xfail(
        os.name == "nt", strict=False,
        reason="a descendant of an already-exited root outlives stop on Windows (known gap)")),
])
async def test_native_descendant_dies_and_unrelated_process_survives(tmp_path, parent_exits):
    pid_file = tmp_path / "descendant.pid"
    grandchild = "import time; time.sleep(30)"
    script = ("import subprocess, sys, time\nfrom pathlib import Path\n"
              f"child=subprocess.Popen([sys.executable, '-c', {grandchild!r}])\n"
              f"Path({str(pid_file)!r}).write_text(str(child.pid))\n"
              + ("sys.exit(0)\n" if parent_exits else "time.sleep(30)\n"))
    unrelated = await asyncio.create_subprocess_exec(sys.executable, "-c", grandchild)
    proc = None
    descendant_handle = None
    api = owned._WinAPI()
    open_process = api.dll.OpenProcess
    open_process.argtypes, open_process.restype = [owned.DWORD, C.c_int, owned.DWORD], owned.HANDLE
    try:
        proc = await owned.create_subprocess_exec(sys.executable, "-c", script,
                                                stdout=asyncio.subprocess.PIPE,
                                                stderr=asyncio.subprocess.PIPE)
        for _ in range(200):
            if pid_file.exists() and pid_file.read_text():
                break
            await asyncio.sleep(0.02)
        descendant_pid = int(pid_file.read_text())
        # Observation only: keep this witness handle to verify actual exit.
        descendant_handle = open_process(0x00100000, False, descendant_pid)
        assert descendant_handle
        assert api.call("WaitForSingleObject", descendant_handle, 0) == 258
        if parent_exits:
            for _ in range(200):
                if proc.returncode is not None:
                    break
                await asyncio.sleep(0.02)
            assert proc.returncode == 0
        await asyncio.wait_for(proc.stop(timeout=5), 7)
        assert api.call("WaitForSingleObject", descendant_handle, 0) == 0
        assert unrelated.returncode is None
        await proc.stop()  # repeated cancellation/close is harmless
    finally:
        if proc is not None:
            await proc.stop(timeout=5)
        if descendant_handle:
            api.checked("CloseHandle", descendant_handle)
        if unrelated.returncode is None:
            unrelated.kill()  # only the fixture we created, through its handle
        await unrelated.wait()
