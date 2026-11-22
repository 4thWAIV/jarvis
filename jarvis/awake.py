"""The Mac kept awake while JARVIS's own work runs, and how long it slept.

Measured on package-9a8f7c72f87b run-1 (2026-11-22): the Mac went to idle
sleep at 11:13:00 (`pmset -g log`: "Entering Sleep state due to 'Idle
Sleep'") while the swarm opened Tim's reader, and woke only for 45-second
maintenance wakes (11:28:06, 11:43:59, 11:59:51) until a person woke it at
12:07:15. The run moved only inside those wakes: the outline turn at
11:28:11, the owners' reads at 11:44. Nothing held the Mac awake, because a
run's process is a background process with no person at the keyboard.

`held` is the one way JARVIS keeps it awake: `caffeinate` (part of macOS)
holds the power assertions named by its flags for as long as this process
lives (`-w <pid>`), and is let go when the block ends. No caffeinate (not
macOS) holds nothing.

`Clock` tells a wait's time by the wall clock from its time awake:
`time.monotonic` on macOS is `mach_absolute_time`, which stops while the
Mac sleeps, so the difference is the time it slept (and asyncio's own
timeouts, which run on it, never fire during a sleep).
"""
from __future__ import annotations

import contextlib
import os
import shutil
import subprocess
import time
from typing import Iterator

# Idle sleep only: the sleep that stopped the run. The display may sleep.
RUN_FLAGS: tuple[str, ...] = ("-i",)
# A sleep shorter than this is the clocks' own drift, never reported.
SLEPT_MIN_SEC = 2.0


@contextlib.contextmanager
def held(flags: tuple[str, ...] = RUN_FLAGS) -> Iterator[bool]:
    """The Mac kept from sleeping (by `flags`) while the block runs. Yields
    whether an assertion is held."""
    tool = shutil.which("caffeinate")
    holder = None
    if tool:
        try:
            holder = subprocess.Popen([tool, *flags, "-w", str(os.getpid())],
                                      stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                      stderr=subprocess.DEVNULL)
        except OSError:
            holder = None
    try:
        yield holder is not None
    finally:
        if holder is not None and holder.poll() is None:
            holder.terminate()
            with contextlib.suppress(subprocess.TimeoutExpired):
                holder.wait(5)


class Clock:
    """A span's seconds by the wall clock and the seconds the Mac slept in
    it (wall time less time awake)."""

    def __init__(self) -> None:
        self.began = time.time()
        self._awake = time.monotonic()

    def seconds(self) -> float:
        return time.time() - self.began

    def slept(self) -> float:
        """Seconds asleep since it began; 0 under `SLEPT_MIN_SEC`."""
        gap = self.seconds() - (time.monotonic() - self._awake)
        return round(gap, 1) if gap >= SLEPT_MIN_SEC else 0.0
