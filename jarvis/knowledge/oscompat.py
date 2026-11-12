"""The engine's few operating-system calls that differ on Windows, in one
place: open flags, the writer lock, flushing a folder, and holding a child.

macOS and Linux keep exactly what the engine did before: O_NOFOLLOW,
`flock`, an fsync of the folder and SIGSTOP / SIGCONT. On Windows:

- every file is opened in binary mode (O_BINARY), or the C runtime would
  turn each newline in a log record into two bytes;
- O_NOFOLLOW does not exist; the engine's folder is the user's own
  (data_paths), and a link put there needs that user;
- the writer lock is `msvcrt.locking` on the LOCK file's first byte, held
  until the descriptor is closed or unlocked, refused at once when another
  process holds it (BlockingIOError, as `flock` with LOCK_NB);
- a folder cannot be opened to fsync it; NTFS journals the rename itself;
- a child cannot be stopped and continued by a signal, so pausing it is a
  no-op there (the pace still holds new work back).
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys

WINDOWS = sys.platform == "win32"

O_BINARY = getattr(os, "O_BINARY", 0)
O_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)

if WINDOWS:
    import msvcrt
else:
    import fcntl


def lock(fd: int) -> None:
    """Take `fd`'s exclusive writer lock without waiting; BlockingIOError
    when another holder has it."""
    if not WINDOWS:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return
    os.lseek(fd, 0, os.SEEK_SET)
    try:
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
    except OSError as e:
        raise BlockingIOError(str(e)) from e


def unlock(fd: int) -> None:
    if not WINDOWS:
        fcntl.flock(fd, fcntl.LOCK_UN)
        return
    os.lseek(fd, 0, os.SEEK_SET)
    try:
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    except OSError:
        pass


def full_fsync(fd: int) -> None:
    """`F_FULLFSYNC` where it exists (macOS), else plain fsync."""
    if not WINDOWS:
        op = getattr(fcntl, "F_FULLFSYNC", None)
        if op is not None:
            try:
                fcntl.fcntl(fd, op)
                return
            except OSError:
                pass
    os.fsync(fd)


def fsync_dir(directory) -> None:
    if WINDOWS:
        return
    fd = os.open(str(directory), os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def hold(child: subprocess.Popen, paused: bool) -> None:
    """Stop or continue `child` (POSIX only)."""
    if WINDOWS:
        return
    child.send_signal(signal.SIGSTOP if paused else signal.SIGCONT)


def end(pid: int, forcefully: bool) -> None:
    """Ask `pid` to end (SIGTERM), or end it (SIGKILL; TerminateProcess on
    Windows, which has no gentler form for a process with no window).
    ProcessLookupError when it is gone."""
    if WINDOWS:
        try:
            os.kill(pid, signal.SIGTERM)   # TerminateProcess
        except OSError as e:
            raise ProcessLookupError(str(e)) from e
        return
    os.kill(pid, signal.SIGKILL if forcefully else signal.SIGTERM)
