"""process_inspect, and what its callers do with each answer.

EVIDENCE LIMIT: the Windows tests drive a fake `kernel32` at the ctypes seam
(`process_inspect._kernel32`, `_last_error`, `_is_windows`). They show the
branch logic and that no signal is sent. They do NOT show that the real
Win32 calls behave this way; that needs the native Windows tests listed in
the commit message. The POSIX tests run the real `os.kill(pid, 0)` on the
machine running them."""
import json
import os
import subprocess
import sys
from ctypes import wintypes

import pytest

from jarvis import process_inspect as pi
from jarvis import server_address
from jarvis.process_inspect import Identity, Inspection, State


# --- POSIX, real processes ------------------------------------------------

@pytest.mark.skipif(sys.platform == "win32", reason="POSIX path")
def test_own_process_is_alive_without_identity_on_posix():
    got = pi.inspect_process(os.getpid())
    assert got.state is State.ALIVE and got.identity is None


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX path")
def test_an_exited_process_is_exited_and_a_running_one_is_left_running():
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"],
                             stdin=subprocess.DEVNULL)
    try:
        for _ in range(20):
            assert pi.inspect_process(child.pid).state is State.ALIVE
            assert child.poll() is None
    finally:
        child.terminate()
        child.wait(timeout=5)
    assert pi.inspect_process(child.pid).state is State.EXITED


@pytest.mark.parametrize("pid", [None, 0, -1, "x", True, float("inf")])
def test_a_pid_that_cannot_be_one_is_exited_and_reaches_no_api(monkeypatch, pid):
    def forbidden(*a):
        pytest.fail("an invalid PID reached a process API")
    monkeypatch.setattr(os, "kill", forbidden)
    monkeypatch.setattr(pi, "_kernel32", forbidden)
    got = pi.inspect_process(pid)
    assert got.state is State.EXITED and got.reason in ("invalid_pid", "no_such_process")


def test_a_pid_too_large_for_the_os_is_exited_on_posix(monkeypatch):
    def too_big(pid, sig):
        raise OverflowError("int too big")
    monkeypatch.setattr(pi, "_is_windows", lambda: False)
    monkeypatch.setattr(os, "kill", too_big)
    assert pi.inspect_process(2 ** 80).state is State.EXITED


def test_permission_denied_is_unknown_never_exited_or_alive(monkeypatch):
    def denied(pid, sig):
        raise PermissionError(1, "Operation not permitted")
    monkeypatch.setattr(pi, "_is_windows", lambda: False)
    monkeypatch.setattr(os, "kill", denied)
    got = pi.inspect_process(4242)
    assert (got.state, got.reason) == (State.UNKNOWN, "access_denied")


def test_another_os_error_is_unknown(monkeypatch):
    def broken(pid, sig):
        raise OSError(5, "I/O error")
    monkeypatch.setattr(pi, "_is_windows", lambda: False)
    monkeypatch.setattr(os, "kill", broken)
    assert pi.inspect_process(4242).state is State.UNKNOWN


def test_an_identity_check_without_a_verified_identity_is_unknown_not_alive(monkeypatch):
    monkeypatch.setattr(pi, "_is_windows", lambda: False)
    monkeypatch.setattr(os, "kill", lambda pid, sig: None)
    got = pi.inspect_process(4242, Identity(4242, "anything"))
    assert (got.state, got.reason) == (State.UNKNOWN, "identity_unverifiable")


def test_an_identity_for_another_pid_is_unknown(monkeypatch):
    monkeypatch.setattr(os, "kill", lambda pid, sig: None)
    got = pi.inspect_process(4242, Identity(1, "t"))
    assert (got.state, got.reason) == (State.UNKNOWN, "identity_for_another_pid")


# --- Windows, fake kernel32 (does NOT prove Windows) ------------------------

class FakeKernel32:
    """Records every call; fails the test if anything could end a process."""

    def __init__(self, *, handle=0x123456789, error=0, exit_code=259, wait=pi.WAIT_TIMEOUT,
                 created=0x01D9_0000_0000_0001, times_ok=True, exit_ok=True):
        self.handle, self.error, self.exit_code = handle, error, exit_code
        self.wait, self.created = wait, created
        self.times_ok, self.exit_ok = times_ok, exit_ok
        self.calls = []

    def OpenProcess(self, access, inherit, pid):
        self.calls.append(("open", access, inherit, pid))
        return self.handle

    def GetExitCodeProcess(self, handle, out):
        self.calls.append(("exitcode", handle))
        out._obj.value = self.exit_code
        return self.exit_ok

    def GetProcessTimes(self, handle, made, *rest):
        self.calls.append(("times", handle))
        made._obj.dwLowDateTime = self.created & 0xFFFFFFFF
        made._obj.dwHighDateTime = self.created >> 32
        return self.times_ok

    def WaitForSingleObject(self, handle, ms):
        self.calls.append(("wait", handle, ms))
        return self.wait

    def CloseHandle(self, handle):
        self.calls.append(("close", handle))
        return 1

    def TerminateProcess(self, *a):
        pytest.fail("TerminateProcess must never be called")


def on_windows(monkeypatch, fake, last_error=0):
    monkeypatch.setattr(pi, "_is_windows", lambda: True)
    monkeypatch.setattr(pi, "_kernel32", lambda: fake)
    monkeypatch.setattr(pi, "_last_error", lambda: last_error)

    def no_signal(*a):
        pytest.fail("Windows inspection must not send a signal")
    monkeypatch.setattr(os, "kill", no_signal)


def test_windows_running_process_is_alive_with_a_token_and_the_handle_is_closed(monkeypatch):
    fake = FakeKernel32()
    on_windows(monkeypatch, fake)
    got = pi.inspect_process(1234)
    assert got.state is State.ALIVE
    assert got.identity == Identity(1234, f"win-filetime:{fake.created}")
    names = [c[0] for c in fake.calls]
    assert names[0] == "open" and names[-1] == "close"
    # read-only rights: query-limited and synchronize, nothing that can end it
    assert fake.calls[0][1] == 0x1000 | 0x00100000 and fake.calls[0][2] is False


def test_windows_a_real_exit_code_259_is_exited_because_the_wait_says_so(monkeypatch):
    on_windows(monkeypatch, FakeKernel32(exit_code=259, wait=pi.WAIT_OBJECT_0))
    got = pi.inspect_process(1234)
    assert (got.state, got.reason) == (State.EXITED, "exited_with_still_active_code")


def test_windows_an_exit_code_other_than_259_is_exited_without_waiting(monkeypatch):
    fake = FakeKernel32(exit_code=0)
    on_windows(monkeypatch, fake)
    assert pi.inspect_process(1234).state is State.EXITED
    assert "wait" not in [c[0] for c in fake.calls]


def test_windows_a_failed_wait_is_unknown(monkeypatch):
    on_windows(monkeypatch, FakeKernel32(wait=0xFFFFFFFF))
    assert pi.inspect_process(1234).state is State.UNKNOWN


def test_windows_a_failed_exit_code_read_is_unknown_and_closes_the_handle(monkeypatch):
    fake = FakeKernel32(exit_ok=False)
    on_windows(monkeypatch, fake)
    assert pi.inspect_process(1234).state is State.UNKNOWN
    assert fake.calls[-1][0] == "close"


@pytest.mark.parametrize("error,state,reason", [
    (87, State.EXITED, "no_such_process"),
    (5, State.UNKNOWN, "access_denied"),
    (1450, State.UNKNOWN, "open_failed:1450"),
])
def test_windows_open_failures_map_access_denied_to_unknown(monkeypatch, error, state, reason):
    fake = FakeKernel32(handle=0)
    on_windows(monkeypatch, fake, last_error=error)
    got = pi.inspect_process(1234)
    assert (got.state, got.reason) == (state, reason)
    assert [c[0] for c in fake.calls] == ["open"]   # no handle, so nothing to close


def test_windows_a_pid_above_32_bits_never_reaches_the_api(monkeypatch):
    fake = FakeKernel32()
    on_windows(monkeypatch, fake)
    assert pi.inspect_process(2 ** 32).state is State.EXITED
    assert fake.calls == []


def test_windows_matching_identity_stays_alive(monkeypatch):
    fake = FakeKernel32()
    on_windows(monkeypatch, fake)
    expected = Identity(1234, f"win-filetime:{fake.created}")
    got = pi.inspect_process(1234, expected)
    assert got.state is State.ALIVE and got.identity == expected


def test_windows_pid_reuse_is_reported_as_not_the_expected_process(monkeypatch):
    fake = FakeKernel32(created=0x01D9_0000_0000_0002)
    on_windows(monkeypatch, fake)
    older = Identity(1234, "win-filetime:" + str(0x01D9_0000_0000_0001))
    got = pi.inspect_process(1234, older)
    assert (got.state, got.reason) == (State.EXITED, "identity_mismatch")
    assert got.identity.start_token.endswith(str(0x01D9_0000_0000_0002))


def test_windows_an_unreadable_creation_time_cannot_confirm_an_expected_identity(monkeypatch):
    on_windows(monkeypatch, FakeKernel32(times_ok=False))
    got = pi.inspect_process(1234, Identity(1234, "win-filetime:1"))
    assert (got.state, got.reason) == (State.UNKNOWN, "identity_unverifiable")


def test_windows_denied_with_an_expected_identity_stays_unknown(monkeypatch):
    on_windows(monkeypatch, FakeKernel32(handle=0), last_error=5)
    assert pi.inspect_process(1234, Identity(1234, "t")).state is State.UNKNOWN


def test_prototypes_declare_a_64_bit_handle_everywhere():
    """The seam test above cannot catch truncation; the declarations can be
    checked on any platform because they are plain data."""
    class Fn:
        pass

    class Api:
        def __init__(self):
            for name in ("OpenProcess", "GetExitCodeProcess", "GetProcessTimes",
                         "WaitForSingleObject", "CloseHandle"):
                setattr(self, name, Fn())

    api = Api()
    pi._declare(api)
    assert api.OpenProcess.restype is wintypes.HANDLE
    for name in ("GetExitCodeProcess", "GetProcessTimes", "WaitForSingleObject", "CloseHandle"):
        assert getattr(api, name).argtypes[0] is wintypes.HANDLE, name
    assert api.WaitForSingleObject.restype is wintypes.DWORD
    assert api.OpenProcess.argtypes == [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]


def test_no_os_kill_on_the_windows_branch_in_source():
    import inspect
    assert "os.kill" not in inspect.getsource(pi._inspect_windows)


# --- callers: server_address.running ---------------------------------------

def _record(pid):
    from jarvis import data_paths
    data_paths.server_address_path().parent.mkdir(parents=True, exist_ok=True)
    data_paths.server_address_path().write_text(json.dumps(
        {"scheme": "https", "host": "::1", "port": 8341, "pid": pid}))


@pytest.mark.parametrize("state,kept", [
    (State.ALIVE, True),
    (State.EXITED, False),
    (State.UNKNOWN, True),   # fail closed: update still asks that address
])
def test_running_keeps_the_record_unless_the_process_is_confirmed_gone(
        monkeypatch, state, kept):
    _record(4242)
    monkeypatch.setattr(pi, "inspect_process",
                        lambda pid, expected_identity=None: Inspection(state, None, "t"))
    found = server_address.running()
    assert (found is not None) is kept
    if kept:
        assert found.pid == 4242


def test_running_never_deletes_the_record_whatever_the_answer(monkeypatch):
    from jarvis import data_paths
    _record(4242)
    for state in State:
        monkeypatch.setattr(pi, "inspect_process",
                            lambda pid, expected_identity=None, s=state: Inspection(s, None, "t"))
        server_address.running()
        assert data_paths.server_address_path().exists()


def test_update_refuses_to_update_under_a_server_it_cannot_inspect_or_reach(monkeypatch):
    """unknown + a refused connection must stop `python -m jarvis.update`,
    not fall through to updating a checkout under a possibly live server."""
    from jarvis import update
    _record(4242)
    monkeypatch.setattr(pi, "inspect_process",
                        lambda pid, expected_identity=None: Inspection(State.UNKNOWN, None, "access_denied"))
    monkeypatch.setattr(update, "ask_server", lambda url, to: None)
    monkeypatch.setattr(update.self_update, "update",
                        lambda **kw: pytest.fail("updated under an uninspectable server"))
    with pytest.raises(SystemExit) as stopped:
        update.main([])
    assert "refused the connection" in str(stopped.value)
