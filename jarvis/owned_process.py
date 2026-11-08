"""Contain only subprocesses JARVIS creates; keep asyncio responsible for pipes.

Windows children start suspended, enter a non-breakaway, kill-on-close Job,
then resume. No process is opened or killed by a discovered PID. The one
CPython-specific bridge borrows and duplicates asyncio's existing Popen handle;
unsupported transports fail closed. POSIX launch and signalling are unchanged.
"""
from __future__ import annotations

import asyncio
import ctypes as C
import os
import time


class ContainmentError(RuntimeError):
    """Owned-process containment could not be established or confirmed stopped."""


# Fixed-width Windows layouts also allow API-contract tests on non-Windows hosts.
DWORD = C.c_uint32
HANDLE = C.c_void_p
SIZE_T = C.c_size_t


class _Limits(C.Structure):
    _fields_ = [("process_time", C.c_int64), ("job_time", C.c_int64),
                ("flags", DWORD), ("min_working_set", SIZE_T),
                ("max_working_set", SIZE_T), ("active_limit", DWORD),
                ("affinity", SIZE_T), ("priority", DWORD), ("scheduling", DWORD)]


class _ExtendedLimits(C.Structure):
    _fields_ = [("basic", _Limits), ("io", C.c_uint64 * 6),
                ("process_memory", SIZE_T), ("job_memory", SIZE_T),
                ("peak_process_memory", SIZE_T), ("peak_job_memory", SIZE_T)]


class _Accounting(C.Structure):
    _fields_ = [("times", C.c_int64 * 4), ("page_faults", DWORD),
                ("total", DWORD), ("active", DWORD), ("terminated", DWORD)]


class _ThreadEntry(C.Structure):
    _fields_ = [("size", DWORD), ("usage", DWORD), ("tid", DWORD),
                ("pid", DWORD), ("base_priority", C.c_int32),
                ("delta_priority", C.c_int32), ("flags", DWORD)]


class _WinAPI:
    def __init__(self):
        self.dll = C.WinDLL("kernel32", use_last_error=True)
        signatures = {
            "CreateJobObjectW": ([HANDLE, C.c_wchar_p], HANDLE),
            "SetInformationJobObject": ([HANDLE, C.c_int, HANDLE, DWORD], C.c_int),
            "AssignProcessToJobObject": ([HANDLE, HANDLE], C.c_int),
            "TerminateJobObject": ([HANDLE, C.c_uint], C.c_int),
            "QueryInformationJobObject": ([HANDLE, C.c_int, HANDLE, DWORD, HANDLE], C.c_int),
            "CloseHandle": ([HANDLE], C.c_int),
            "GetCurrentProcess": ([], HANDLE),
            "DuplicateHandle": ([HANDLE, HANDLE, HANDLE, C.POINTER(HANDLE), DWORD, C.c_int, DWORD], C.c_int),
            "GetProcessId": ([HANDLE], DWORD),
            "WaitForSingleObject": ([HANDLE, DWORD], DWORD),
            "CreateToolhelp32Snapshot": ([DWORD, DWORD], HANDLE),
            "Thread32First": ([HANDLE, C.POINTER(_ThreadEntry)], C.c_int),
            "Thread32Next": ([HANDLE, C.POINTER(_ThreadEntry)], C.c_int),
            "OpenThread": ([DWORD, C.c_int, DWORD], HANDLE),
            "GetProcessIdOfThread": ([HANDLE], DWORD),
            "ResumeThread": ([HANDLE], DWORD),
        }
        for name, (args, result) in signatures.items():
            function = getattr(self.dll, name)
            function.argtypes, function.restype = args, result

    def call(self, name, *args):
        return getattr(self.dll, name)(*args)

    def checked(self, name, *args):
        result = self.call(name, *args)
        if not result:
            raise ContainmentError(f"{name}: {C.WinError(C.get_last_error())}")
        return result

    def thread_ids(self, pid):
        snapshot = self.call("CreateToolhelp32Snapshot", 4, 0)
        if snapshot == C.c_void_p(-1).value:
            raise ContainmentError(f"CreateToolhelp32Snapshot: {C.WinError(C.get_last_error())}")
        try:
            entry = _ThreadEntry()
            entry.size = C.sizeof(entry)
            found = []
            more = self.call("Thread32First", snapshot, C.byref(entry))
            while more:
                if entry.pid == pid:
                    found.append(entry.tid)
                entry.size = C.sizeof(entry)
                more = self.call("Thread32Next", snapshot, C.byref(entry))
            if C.get_last_error() != 18:  # ERROR_NO_MORE_FILES
                raise ContainmentError(f"thread enumeration: {C.WinError(C.get_last_error())}")
            return found
        finally:
            self.checked("CloseHandle", snapshot)


class _WindowsJob:
    def __init__(self, api=None):
        self.api = api or _WinAPI()
        self.handle = None
        self.process = None
        self.assigned = False
        self.handle = self.api.checked("CreateJobObjectW", None, None)
        try:
            limits = _ExtendedLimits()
            limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            self.api.checked("SetInformationJobObject", self.handle, 9,
                             C.byref(limits), C.sizeof(limits))
        except BaseException:
            self.close()
            raise

    def attach(self, proc):
        # asyncio's Windows transport owns windows_utils.Popen, whose _handle
        # is the handle returned by CreateProcess. Never reopen proc.pid.
        transport = getattr(proc, "_transport", None)
        popen = transport.get_extra_info("subprocess") if transport else None
        original = getattr(popen, "_handle", None)
        if original is None:
            raise ContainmentError("Windows asyncio transport has no owned process handle")
        duplicate = HANDLE()
        current = self.api.call("GetCurrentProcess")
        self.api.checked("DuplicateHandle", current, int(original), current,
                         C.byref(duplicate), 0, False, 2)  # DUPLICATE_SAME_ACCESS
        self.process = duplicate.value
        if self.api.checked("GetProcessId", self.process) != proc.pid:
            raise ContainmentError("owned process handle does not match the launched child")
        self._alive()
        self.api.checked("AssignProcessToJobObject", self.handle, self.process)
        self.assigned = True

    def _alive(self):
        if self.api.call("WaitForSingleObject", self.process, 0) != 258:
            raise ContainmentError("owned suspended child exited before its initial-thread handoff")

    def resume(self, pid):
        self._alive()
        threads = self.api.thread_ids(pid)
        if len(threads) != 1:
            raise ContainmentError("cannot identify one initial thread in the suspended child")
        thread = self.api.checked("OpenThread", 0x0002 | 0x0800, False, threads[0])
        try:
            if self.api.checked("GetProcessIdOfThread", thread) != pid:
                raise ContainmentError("initial thread does not belong to the owned child")
            # Hold the thread handle and check the pinned process is STILL
            # alive: the PID cannot have been reused between enumeration and
            # this check. After this point we use handles only.
            self._alive()
            if self.api.call("ResumeThread", thread) != 1:
                raise ContainmentError("initial thread did not have exactly one suspension")
        finally:
            self.api.checked("CloseHandle", thread)

    def terminate(self):
        self.api.checked("TerminateJobObject", self.handle, 1)

    def active(self):
        accounting = _Accounting()
        self.api.checked("QueryInformationJobObject", self.handle, 1,
                         C.byref(accounting), C.sizeof(accounting), None)
        return accounting.active

    def close(self):
        # No inheritable Job handle is ever passed to a child. Closing the
        # last handle is a safety net, not proof of confirmed termination.
        error = None
        for field in ("handle", "process"):
            handle = getattr(self, field)
            if handle is not None:
                try:
                    self.api.checked("CloseHandle", handle)
                    setattr(self, field, None)
                except Exception as caught:
                    error = error or caught
        if error:
            raise error


class OwnedProcess:
    def __init__(self, process, job):
        self.process, self.job = process, job
        self._stop_lock = asyncio.Lock()
        self._stopped = False
        self._error = None

    def __getattr__(self, name):
        return getattr(self.process, name)

    async def stop(self, timeout=10.0):
        """Confirm the Job is empty before reporting cancellation or success."""
        async with self._stop_lock:
            if self._error:
                raise self._error
            if self._stopped:
                return
            try:
                self.job.terminate()
                deadline = time.monotonic() + timeout
                while self.job.active():
                    if time.monotonic() >= deadline:
                        raise ContainmentError("Windows owned process tree did not stop within its grace period")
                    await asyncio.sleep(min(0.02, max(0, deadline - time.monotonic())))
                await asyncio.wait_for(self.process.wait(), timeout=max(0.01, deadline - time.monotonic()))
                self.job.close()
                self._stopped = True
            except BaseException as error:
                if isinstance(error, asyncio.CancelledError):
                    raise
                self._error = ContainmentError(f"could not confirm Windows owned process tree stopped: {error}")
                # Even when confirmation fails, kill-on-close remains a
                # last resort. Never translate that into a successful stop.
                try:
                    self.job.close()
                except Exception:
                    pass
                raise self._error from error


async def _settle(task):
    """Retain cleanup ownership through repeated caller cancellation."""
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            continue
    return task.result()


async def _abort_spawn(proc, job):
    if job is not None and job.assigned:
        await OwnedProcess(proc, job).stop()
        return
    try:
        if proc.returncode is None:
            proc.kill()  # asyncio uses its own process handle; never a PID lookup
        await asyncio.wait_for(proc.wait(), timeout=10)
    except Exception as error:
        raise ContainmentError(f"could not reap the owned child after launch failure: {error}") from error
    finally:
        if job is not None:
            job.close()


async def create_subprocess_exec(*args, **kwargs):
    job = _WindowsJob() if os.name == "nt" else None
    if job is not None:
        kwargs["creationflags"] = kwargs.get("creationflags", 0) | 0x00000004  # CREATE_SUSPENDED
    spawning = asyncio.create_task(asyncio.create_subprocess_exec(*args, **kwargs))
    try:
        proc = await asyncio.shield(spawning)
    except asyncio.CancelledError:
        try:
            proc = await _settle(spawning)
            await _settle(asyncio.create_task(_abort_spawn(proc, job)))
        finally:
            if job is not None:
                job.close()
        raise
    except BaseException:
        if job is not None:
            job.close()
        raise
    if job is None:
        return proc
    try:
        job.attach(proc)
        job.resume(proc.pid)
        return OwnedProcess(proc, job)
    except BaseException as error:
        try:
            await _settle(asyncio.create_task(_abort_spawn(proc, job)))
        except BaseException as cleanup:
            raise ContainmentError(f"Windows containment failed: {error}; cleanup failed: {cleanup}") from error
        raise
