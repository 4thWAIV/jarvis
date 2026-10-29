"""host_resources.py — the whole machine's CPU and memory, every five seconds.

What this backend reports to the team server's network (the `resources` of
its node update). Whole-host figures, not this process's: process CPU, load
average and process memory (voice_diagnostics) answer a different question.
Inside a container with limits, the container's own figures, and the scope
says so.

  macOS   host_statistics (CPU ticks) and host_statistics64 (memory pages)
          through ctypes, hw.memsize through sysctl. Memory used is what
          Activity Monitor calls Memory Used: app memory, wired and
          compressed.
  Linux   /proc/stat and /proc/meminfo (used = MemTotal - MemAvailable), or
          the cgroup v2 files when the cgroup has a CPU or memory limit:
          cpu.stat over cpu.max (or every core), memory.current less
          inactive_file over memory.max (or the host's memory).
  other   unsupported, and says so.

CPU busy is a difference between two readings, so the first sample has no
CPU figure: the state is `warming_up` until the second. A failed reading is
kept as one (`error`, with the error's class name in `last_error`), never
filled in with the previous figures or with zeros.

Standard library only (CLAUDE.md: no new dependencies).
"""
from __future__ import annotations

import asyncio
import ctypes
import logging
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, Protocol

log = logging.getLogger("jarvis.host_resources")

SAMPLE_SEC = 5.0
# The node update refuses an older reading (Resources.age_ms, le=60_000).
MAX_AGE_MS = 60_000


@dataclass(frozen=True)
class Reading:
    """One look at the machine. `cpu_busy` and `cpu_capacity` are running
    counters in the same unit; busy percent is the change in one over the
    change in the other between two readings."""
    scope: str                      # "host" or "container"
    cpu_busy: float
    cpu_capacity: float
    memory_used: int
    memory_total: int


class Reader(Protocol):
    def read(self) -> Reading: ...


# -- macOS -----------------------------------------------------------------

_HOST_CPU_LOAD_INFO = 3
_HOST_VM_INFO64 = 4
_KERN_SUCCESS = 0


class _CpuLoadInfo(ctypes.Structure):
    # mach/host_info.h: CPU_STATE_USER, SYSTEM, IDLE, NICE, in ticks.
    _fields_ = [("user", ctypes.c_uint32), ("system", ctypes.c_uint32),
                ("idle", ctypes.c_uint32), ("nice", ctypes.c_uint32)]


class _VmStatistics64(ctypes.Structure):
    # mach/vm_statistics.h, struct vm_statistics64, in order.
    _fields_ = [
        ("free_count", ctypes.c_uint32), ("active_count", ctypes.c_uint32),
        ("inactive_count", ctypes.c_uint32), ("wire_count", ctypes.c_uint32),
        ("zero_fill_count", ctypes.c_uint64), ("reactivations", ctypes.c_uint64),
        ("pageins", ctypes.c_uint64), ("pageouts", ctypes.c_uint64),
        ("faults", ctypes.c_uint64), ("cow_faults", ctypes.c_uint64),
        ("lookups", ctypes.c_uint64), ("hits", ctypes.c_uint64),
        ("purges", ctypes.c_uint64),
        ("purgeable_count", ctypes.c_uint32), ("speculative_count", ctypes.c_uint32),
        ("decompressions", ctypes.c_uint64), ("compressions", ctypes.c_uint64),
        ("swapins", ctypes.c_uint64), ("swapouts", ctypes.c_uint64),
        ("compressor_page_count", ctypes.c_uint32), ("throttled_count", ctypes.c_uint32),
        ("external_page_count", ctypes.c_uint32), ("internal_page_count", ctypes.c_uint32),
        ("total_uncompressed_pages_in_compressor", ctypes.c_uint64),
    ]


def _count(structure: type) -> ctypes.c_uint32:
    """The `count` argument host_statistics takes: the size in 32-bit words."""
    return ctypes.c_uint32(ctypes.sizeof(structure) // ctypes.sizeof(ctypes.c_int32))


class MacReader:
    """The Mach host interfaces, opened once (mach_host_self is a port right;
    asking for it every sample would leak one each time)."""

    def __init__(self) -> None:
        lib = ctypes.CDLL("/usr/lib/libSystem.B.dylib")
        lib.mach_host_self.restype = ctypes.c_uint32
        for name in ("host_statistics", "host_statistics64"):
            fn = getattr(lib, name)
            fn.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_void_p,
                           ctypes.POINTER(ctypes.c_uint32)]
            fn.restype = ctypes.c_int
        lib.host_page_size.argtypes = [ctypes.c_uint32, ctypes.POINTER(ctypes.c_size_t)]
        lib.host_page_size.restype = ctypes.c_int
        lib.sysctlbyname.argtypes = [ctypes.c_char_p, ctypes.c_void_p,
                                     ctypes.POINTER(ctypes.c_size_t), ctypes.c_void_p,
                                     ctypes.c_size_t]
        lib.sysctlbyname.restype = ctypes.c_int
        self._lib = lib
        self._host = lib.mach_host_self()
        page = ctypes.c_size_t(0)
        self._check(lib.host_page_size(self._host, ctypes.byref(page)), "host_page_size")
        self._page = int(page.value)
        total = ctypes.c_uint64(0)
        size = ctypes.c_size_t(ctypes.sizeof(total))
        if lib.sysctlbyname(b"hw.memsize", ctypes.byref(total), ctypes.byref(size),
                            None, 0) != 0:
            raise OSError("sysctl hw.memsize failed")
        self._total = int(total.value)

    @staticmethod
    def _check(code: int, what: str) -> None:
        if code != _KERN_SUCCESS:
            raise OSError(f"{what} failed (kern_return {code})")

    def read(self) -> Reading:
        cpu = _CpuLoadInfo()
        count = _count(_CpuLoadInfo)
        self._check(self._lib.host_statistics(self._host, _HOST_CPU_LOAD_INFO,
                                              ctypes.byref(cpu), ctypes.byref(count)),
                    "host_statistics")
        vm = _VmStatistics64()
        count = _count(_VmStatistics64)
        self._check(self._lib.host_statistics64(self._host, _HOST_VM_INFO64,
                                                ctypes.byref(vm), ctypes.byref(count)),
                    "host_statistics64")
        busy = cpu.user + cpu.system + cpu.nice
        app = max(0, vm.internal_page_count - vm.purgeable_count)
        used = (app + vm.wire_count + vm.compressor_page_count) * self._page
        return Reading(scope="host", cpu_busy=float(busy),
                       cpu_capacity=float(busy + cpu.idle),
                       memory_used=min(used, self._total), memory_total=self._total)


# -- Linux -----------------------------------------------------------------

def _first_line(path: Path) -> str:
    return path.read_text().splitlines()[0].strip()


def _keyed(path: Path) -> dict[str, int]:
    """`name value` lines (cpu.stat, memory.stat) or `Name: value kB` lines
    (/proc/meminfo) as {name: value}."""
    out: dict[str, int] = {}
    for line in path.read_text().splitlines():
        parts = line.replace(":", " ").split()
        if len(parts) >= 2 and parts[1].isdigit():
            out[parts[0]] = int(parts[1])
    return out


class LinuxReader:
    """/proc for the host; cgroup v2 for a container with limits. `root` is
    "/" except in tests, which give it a folder laid out the same way.
    `clock` is the monotonic time a container's CPU capacity is counted in."""

    def __init__(self, root: Path = Path("/"),
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._proc = root / "proc"
        self._cgroup = root / "sys" / "fs" / "cgroup"
        self._clock = clock

    def _limit(self, name: str) -> Optional[list[str]]:
        """A cgroup limit file's fields, or None when there is no limit."""
        path = self._cgroup / name
        if not path.is_file():
            return None
        fields = _first_line(path).split()
        return None if not fields or fields[0] == "max" else fields

    def _host_memory(self) -> tuple[int, int]:
        info = _keyed(self._proc / "meminfo")
        total = info["MemTotal"] * 1024
        return total - info["MemAvailable"] * 1024, total

    def _host_cpu(self) -> tuple[float, float]:
        fields = [float(v) for v in _first_line(self._proc / "stat").split()[1:9]]
        idle = fields[3] + fields[4]                   # idle + iowait
        total = sum(fields)
        return total - idle, total

    def read(self) -> Reading:
        cpu_limit = self._limit("cpu.max")
        memory_limit = self._limit("memory.max")
        if cpu_limit is None and memory_limit is None:
            busy, capacity = self._host_cpu()
            used, total = self._host_memory()
            return Reading("host", busy, capacity, used, total)

        cores = (float(cpu_limit[0]) / float(cpu_limit[1]) if cpu_limit
                 else float(os.cpu_count() or 1))
        busy = float(_keyed(self._cgroup / "cpu.stat")["usage_usec"])
        capacity = self._clock() * 1_000_000 * cores
        total = int(memory_limit[0]) if memory_limit else self._host_memory()[1]
        current = int(_first_line(self._cgroup / "memory.current"))
        inactive = _keyed(self._cgroup / "memory.stat").get("inactive_file", 0)
        used = min(max(current - inactive, 0), total)
        return Reading("container", busy, capacity, used, total)


def default_reader() -> Optional[Reader]:
    """This platform's reader, or None where there is none yet (Windows)."""
    if sys.platform == "darwin":
        return MacReader()
    if sys.platform.startswith("linux"):
        return LinuxReader()
    return None


# -- the sampler -------------------------------------------------------------

class HostResources:
    """One sampler per backend. `sample()` takes a reading; `current()` is
    the latest, in the shape of the node update's `Resources`."""

    def __init__(self, reader: Optional[Callable[[], Optional[Reader]]] = default_reader,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._make_reader = reader
        self._reader: Optional[Reader] = None
        self._clock = clock
        self._unsupported = False
        self._latest: Optional[Reading] = None
        self._cpu_percent: Optional[float] = None
        self._sampled_at: Optional[float] = None
        self._failed = False
        self.last_error: Optional[str] = None
        self.errors_in_a_row = 0

    def _ready_reader(self) -> Optional[Reader]:
        if self._reader is None and not self._unsupported:
            self._reader = self._make_reader()
            self._unsupported = self._reader is None
        return self._reader

    def sample(self) -> None:
        try:
            reader = self._ready_reader()
            if reader is None:
                return
            reading = reader.read()
        except Exception as e:                 # every failure is a reading, kept
            self._failed = True
            self.last_error = type(e).__name__
            self.errors_in_a_row += 1
            if self.errors_in_a_row == 1:
                log.warning(f"host_resources: reading failed ({self.last_error})")
            return
        self._cpu_percent = _busy_percent(self._latest, reading)
        self._latest = reading
        self._sampled_at = self._clock()
        self._failed = False
        self.errors_in_a_row = 0

    def current(self) -> dict:
        if self._unsupported:
            return _resources("unsupported", "host")
        if self._failed:
            return _resources("error", self._latest.scope if self._latest else "host")
        if self._latest is None or self._sampled_at is None:
            return _resources("warming_up", "host")
        age_ms = int(max(0.0, self._clock() - self._sampled_at) * 1000)
        if age_ms > MAX_AGE_MS:
            # The sampler has stopped: an old figure is not today's.
            return _resources("error", self._latest.scope, age_ms=MAX_AGE_MS)
        state = "measured" if self._cpu_percent is not None else "warming_up"
        return _resources(state, self._latest.scope, cpu_percent=self._cpu_percent,
                          memory_used_bytes=self._latest.memory_used,
                          memory_total_bytes=self._latest.memory_total or None,
                          age_ms=age_ms)

    async def run(self, interval: float = SAMPLE_SEC) -> None:
        """Sample every `interval` seconds until cancelled."""
        while True:
            self.sample()
            await asyncio.sleep(interval)


def _busy_percent(before: Optional[Reading], after: Reading) -> Optional[float]:
    if before is None or before.scope != after.scope:
        return None
    capacity = after.cpu_capacity - before.cpu_capacity
    if capacity <= 0:
        return None
    busy = after.cpu_busy - before.cpu_busy
    return round(min(max(100.0 * busy / capacity, 0.0), 100.0), 1)


def _resources(state: str, scope: str, *, cpu_percent: Optional[float] = None,
               memory_used_bytes: Optional[int] = None,
               memory_total_bytes: Optional[int] = None, age_ms: int = 0) -> dict:
    return {"state": state, "scope": scope, "cpu_percent": cpu_percent,
            "memory_used_bytes": memory_used_bytes,
            "memory_total_bytes": memory_total_bytes, "age_ms": age_ms}


sampler = HostResources()
