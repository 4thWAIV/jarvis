"""Observing whether a process is running, without touching it.

One question, answered the same way for every caller: is this PID a running
process, and is it the one that was expected? Nothing here signals, waits
on, or terminates a process; ending one is a different interface with its
own proof and does not belong here.

The answer has three states and `unknown` is a real answer:

    alive    the process was confirmed running
    exited   the process was confirmed not running (or `expected_identity`
             was given and the PID now belongs to another process)
    unknown  it could not be inspected: access denied, an unexpected OS
             error, or no way to verify what was asked

A denied inspection is `unknown`, never `exited` and never `alive`: a
process we may not open exists, but we cannot say more. Callers decide what
`unknown` means to them; none may treat it as permission to remove a record,
reuse a PID or clean anything up.

Identity. `Identity(pid, start_token)` is the process's native creation
stamp read from the OS while the process is open. The token is OPAQUE: it
is compared for equality with another token from the same platform and for
nothing else. It is not a clock, has no order, and is not the same thing as
the `startedAt` the Claude roster records, which nothing here claims to
equal. Where no verified route exists (macOS and other POSIX, which need
`libproc`/`sysctl` structure layouts that are not checked in this tree),
`identity` is None and a caller that asks for an identity check gets
`unknown` rather than a guess.

Windows uses read-only handles through `ctypes` only: `OpenProcess` with
PROCESS_QUERY_LIMITED_INFORMATION and SYNCHRONIZE (neither can end the
process), `GetExitCodeProcess`, `GetProcessTimes`, a zero-timeout
`WaitForSingleObject` and `CloseHandle`. `os.kill(pid, 0)` is never used
there: on Windows it terminates the process. POSIX keeps `os.kill(pid, 0)`,
which only tests for existence.

The standard library only. `WinDLL` and the last-error read are seams
(`_kernel32`, `_last_error`) that tests replace; the Windows branch has not
been run on Windows by those tests.
"""
from __future__ import annotations

import enum
import os
import sys
from dataclasses import dataclass
from typing import Optional


class State(str, enum.Enum):
    ALIVE = "alive"
    EXITED = "exited"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class Identity:
    """A process as the OS names it: the PID and an opaque creation token."""
    pid: int
    start_token: str


@dataclass(frozen=True)
class Inspection:
    state: State
    identity: Optional[Identity]   # present only when the OS gave one for this process
    reason: str                    # a short code saying why the state was chosen


# Windows constants.
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
SYNCHRONIZE = 0x00100000
STILL_ACTIVE = 259
WAIT_OBJECT_0 = 0x00000000
WAIT_TIMEOUT = 0x00000102
ERROR_ACCESS_DENIED = 5
ERROR_INVALID_PARAMETER = 87
MAX_WINDOWS_PID = 0xFFFFFFFF


def _is_windows() -> bool:
    return sys.platform == "win32"


def _valid_pid(pid) -> Optional[int]:
    """`pid` as a positive integer, else None. 0 and negatives mean a process
    group to `os.kill` and are not processes."""
    if isinstance(pid, bool):
        return None
    try:
        number = int(pid)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if number > 0 else None


def inspect_process(pid, expected_identity: Optional[Identity] = None) -> Inspection:
    """Look at one PID. Never raises, never signals.

    With `expected_identity`, a confirmed native identity that differs means
    the PID was reused: the expected process has `exited`, reason
    `identity_mismatch`, and `identity` is the process there now. If the
    identity cannot be read, or `expected_identity` names another PID, the
    answer is `unknown` (never `alive`), reason `identity_unverifiable` or
    `identity_for_another_pid`."""
    number = _valid_pid(pid)
    if number is None:
        return Inspection(State.EXITED, None, "invalid_pid")
    if expected_identity is not None and expected_identity.pid != number:
        return Inspection(State.UNKNOWN, None, "identity_for_another_pid")
    try:
        if _is_windows():
            found = _inspect_windows(number)
        else:
            found = _inspect_posix(number)
    except Exception as e:  # inspection must never take a caller down
        return Inspection(State.UNKNOWN, None, f"inspection_error:{type(e).__name__}")
    if expected_identity is None or found.state is not State.ALIVE:
        return found
    if found.identity is None:
        return Inspection(State.UNKNOWN, None, "identity_unverifiable")
    if found.identity != expected_identity:
        return Inspection(State.EXITED, found.identity, "identity_mismatch")
    return found


# --- POSIX ----------------------------------------------------------------

def _inspect_posix(pid: int) -> Inspection:
    """`os.kill(pid, 0)` only tests for existence on POSIX. EPERM means the
    process exists under another user: that is `unknown`, since we may not
    look further. (`server_address` once called that alive and
    `session_watch` once called it gone; neither is established.) An
    unreaped zombie still answers alive, as it always did."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return Inspection(State.EXITED, None, "no_such_process")
    except PermissionError:
        return Inspection(State.UNKNOWN, None, "access_denied")
    except OverflowError:
        return Inspection(State.EXITED, None, "invalid_pid")
    except OSError as e:
        return Inspection(State.UNKNOWN, None, f"os_error:{e.errno}")
    return Inspection(State.ALIVE, None, "alive_identity_unavailable")


# --- Windows --------------------------------------------------------------

_kernel32_cached = None


def _declare(kernel32) -> None:
    """Prototypes for every call, so a 64-bit HANDLE is never cut to an int."""
    from ctypes import POINTER, wintypes
    handle, dword, boolean = wintypes.HANDLE, wintypes.DWORD, wintypes.BOOL
    filetime = POINTER(wintypes.FILETIME)
    kernel32.OpenProcess.argtypes = [dword, boolean, dword]
    kernel32.OpenProcess.restype = handle
    kernel32.GetExitCodeProcess.argtypes = [handle, POINTER(dword)]
    kernel32.GetExitCodeProcess.restype = boolean
    kernel32.GetProcessTimes.argtypes = [handle, filetime, filetime, filetime, filetime]
    kernel32.GetProcessTimes.restype = boolean
    kernel32.WaitForSingleObject.argtypes = [handle, dword]
    kernel32.WaitForSingleObject.restype = dword
    kernel32.CloseHandle.argtypes = [handle]
    kernel32.CloseHandle.restype = boolean


def _kernel32():
    global _kernel32_cached
    if _kernel32_cached is None:
        import ctypes
        loaded = ctypes.WinDLL("kernel32", use_last_error=True)
        _declare(loaded)
        _kernel32_cached = loaded
    return _kernel32_cached


def _last_error() -> int:
    import ctypes
    return ctypes.get_last_error()


def _inspect_windows(pid: int) -> Inspection:
    if pid > MAX_WINDOWS_PID:
        return Inspection(State.EXITED, None, "invalid_pid")
    import ctypes
    from ctypes import wintypes
    k = _kernel32()
    handle = k.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION | SYNCHRONIZE, False, pid)
    if not handle:
        code = _last_error()
        if code == ERROR_INVALID_PARAMETER:
            return Inspection(State.EXITED, None, "no_such_process")
        if code == ERROR_ACCESS_DENIED:
            return Inspection(State.UNKNOWN, None, "access_denied")
        return Inspection(State.UNKNOWN, None, f"open_failed:{code}")
    try:
        status = wintypes.DWORD(0)
        if not k.GetExitCodeProcess(handle, ctypes.byref(status)):
            return Inspection(State.UNKNOWN, None, f"exit_code_failed:{_last_error()}")
        identity = _windows_identity(k, handle, pid)
        if status.value != STILL_ACTIVE:
            return Inspection(State.EXITED, identity, "exited")
        # 259 is also a legal exit code, so a zero-timeout wait decides:
        # a running process is unsignalled, an exited one is signalled.
        waited = k.WaitForSingleObject(handle, 0)
        if waited == WAIT_TIMEOUT:
            return Inspection(State.ALIVE, identity,
                              "running" if identity else "running_identity_unavailable")
        if waited == WAIT_OBJECT_0:
            return Inspection(State.EXITED, identity, "exited_with_still_active_code")
        return Inspection(State.UNKNOWN, identity, f"wait_failed:{waited:#x}")
    finally:
        k.CloseHandle(handle)


def _windows_identity(k, handle, pid: int) -> Optional[Identity]:
    """The creation FILETIME as an opaque token, read from the open handle."""
    from ctypes import byref, wintypes
    made, ended, kernel, user = (wintypes.FILETIME() for _ in range(4))
    if not k.GetProcessTimes(handle, byref(made), byref(ended), byref(kernel), byref(user)):
        return None
    ticks = (made.dwHighDateTime << 32) | made.dwLowDateTime
    if ticks == 0:
        return None
    return Identity(pid, f"win-filetime:{ticks}")
