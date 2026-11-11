"""closed_files.py — a log file its writer has finished with.

A file that grows (a voice log, a run's output stream) is closed by its one
writer once nothing more goes in it: every write bit taken off its mode, so
the file says so itself and no reader has to ask the writer. The content
ingest (knowledge_content.py) indexes a root's files only once they are
closed (`Root.closed_only`), so it reads each one once, whole, and never
again while it grows. A writer that must add to a closed file after all
(a run's last event racing its finish) reopens it for that write and closes
it again, and the ingest then reads it once more.
"""
from __future__ import annotations

import contextlib
import os
import stat as stat_mod
from pathlib import Path
from typing import Iterator, Union

WRITE_BITS = stat_mod.S_IWUSR | stat_mod.S_IWGRP | stat_mod.S_IWOTH

PathLike = Union[str, os.PathLike]


def is_closed(mode: int) -> bool:
    """Whether a regular file's mode says it is closed."""
    return stat_mod.S_ISREG(mode) and not mode & WRITE_BITS


def closed(path: PathLike) -> bool:
    """Whether the regular file at `path` (never through a link) is closed."""
    try:
        return is_closed(os.lstat(path).st_mode)
    except OSError:
        return False


def close(path: PathLike) -> bool:
    """Close the file: no write bit for anyone. False when it is not there
    or not a regular file; closing a closed file changes nothing."""
    try:
        mode = os.lstat(path).st_mode
    except OSError:
        return False
    if not stat_mod.S_ISREG(mode):
        return False
    if mode & WRITE_BITS:
        os.chmod(path, stat_mod.S_IMODE(mode) & ~WRITE_BITS)
    return True


@contextlib.contextmanager
def reopened(path: PathLike) -> Iterator[Path]:
    """The file made writable by its owner for one write, and closed again
    after it when it was closed before (or not there yet: it is then left
    as the write made it)."""
    path = Path(path)
    was = closed(path)
    if was:
        os.chmod(path, stat_mod.S_IMODE(os.lstat(path).st_mode) | stat_mod.S_IWUSR)
    try:
        yield path
    finally:
        if was:
            close(path)
