"""Reading inside a registered folder: one directory page, or one bounded
window of one file. No policy here: the caller (folder_service.py) has
already decided which entries the folder's reader shows (folder_policy.py)
and hands that rule in as `visible`.

Race-resistant by construction. Every read starts from the folder's
canonical root opened as a directory without following a link, and walks
down one component at a time with `openat(..., O_NOFOLLOW | O_DIRECTORY)`
on the descriptor before it, so a path that is checked is the path that is
read: a component swapped for a symlink between the listing and the read
fails to open (ELOOP) instead of leading outside, and so does a link the
folder itself holds. The file is opened the same way, non-blocking so a
FIFO cannot hang the reader, and must be a regular file by `fstat` on that
same descriptor. Nothing is ever read whole: a directory page is at most
PAGE_ENTRIES out of at most SCAN_MAX_ENTRIES looked at within LIST_SECONDS,
and a file window is at most TEXT_MAX_LINES and TEXT_MAX_BYTES read with
`pread` at an offset, however large the file.

On Windows the same walk is done with handles (`_win_handles`): the root
opened as itself, each folder below opened relative to the handle before it
with NtCreateFile, a reparse point (junction, symlink, a OneDrive
placeholder) opened as itself and refused, never followed. Each component
must be exactly the name the folder holds it under, so an 8.3 alias
(CREDEN~1.JSO) or another case (.SSH) never reaches a file the policy hides,
and a name Windows would read as something else (a stream, a device such as
CON, a trailing dot) is listed as "other" and never opened. Windows has no
pread; `pread` here reads at an offset through ReadFile. Writing through
`opened_dir` (work_packages.save_result) stays closed on Windows.

Blocking on purpose; the callers run it off the event loop.
"""
from __future__ import annotations

import errno
import json
import os
import stat
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterator, Optional

from jarvis import history_read, repo_read
from jarvis.folder_contract import (FolderError, LINE_MAX_CHARS, LIST_SECONDS, PAGE_ENTRIES,
                                    SCAN_MAX_ENTRIES, SNIFF_BYTES, TEXT_MAX_BYTES,
                                    TEXT_MAX_LINES, TRANSCRIPT_WINDOW_BYTES)

# The walk above needs descriptor-relative opens that refuse a link
# (O_DIRECTORY, O_NOFOLLOW, O_NONBLOCK, dir_fd), scandir of a descriptor and
# pread. Windows' Python has none of them, and its path-based open follows
# junctions, so there the handle walk in `_win_handles` does it instead;
# this module never falls back to a weaker open. Anywhere else without
# them, every read is refused as `unsupported`.
_POSIX_WALK = (all(hasattr(os, n) for n in ("O_DIRECTORY", "O_NOFOLLOW", "O_NONBLOCK", "pread"))
               and os.open in os.supports_dir_fd and os.stat in os.supports_dir_fd
               and os.scandir in os.supports_fd)
_WINDOWS = sys.platform == "win32"
SUPPORTED = _POSIX_WALK or _WINDOWS

if _POSIX_WALK:
    _DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    _FILE_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK


def pread(fd: int, n: int, offset: int) -> bytes:
    """At most `n` bytes of the descriptor `fd` from `offset`, leaving its
    position alone: os.pread, or ReadFile at an offset on Windows."""
    from jarvis.knowledge import oscompat
    return oscompat.pread(fd, n, offset)


def _win_refusal(e: OSError) -> FolderError:
    """A Windows walk's error as the folder catalog says it: a link or a
    name Windows would read as something else is forbidden, not a regular
    file is unsupported, a sharing violation or no access is access_denied,
    anything else missing."""
    from jarvis import _win_handles
    if isinstance(e, _win_handles.Refused):
        return FolderError("unsupported" if e.why == "kind" else "forbidden")
    if isinstance(e, PermissionError):
        return FolderError("access_denied")
    return FolderError("missing")


def _parts(relative: Path) -> list[str]:
    parts = [p for p in relative.parts if p not in ("", ".")]
    if relative.anchor or any(p == ".." or "/" in p or "\\" in p for p in parts):
        raise FolderError("forbidden")
    return parts


@contextmanager
def _win_dir(root: Path, parts: list[str], exact: bool = True,
             identity: Optional[str] = None):
    """A handle on `root`/`parts`, each folder opened relative to the one
    before; the caller's handle to use until the block ends. Given the
    folder's recorded `identity`, the root held must be that directory:
    another one put at its path, however ordinary, is identity_mismatch."""
    from jarvis import _win_handles
    if not SUPPORTED:
        raise FolderError("unsupported")
    try:
        handle = _win_handles.open_root(root)
    except OSError as e:
        raise _win_refusal(e) from None
    try:
        if identity:
            try:
                held = _win_handles.identity(handle)
            except OSError as e:
                raise _win_refusal(e) from None
            if held != identity:
                raise FolderError("identity_mismatch")
        for part in parts:
            try:
                nxt = _win_handles.open_child_dir(handle, part, exact)
            except OSError as e:
                raise _win_refusal(e) from None
            handle.close()
            handle = nxt
        yield handle
    finally:
        handle.close()


def open_named_file(path: Path) -> tuple[int, os.stat_result, Path]:
    """Windows: (descriptor, its stat, the path it has now) for the regular
    file the absolute `path` names, walked from its drive as written: every
    folder on the way opened relative to the one before and held until the
    file is open, a link anywhere refused rather than resolved first. Names
    may be written in another case or as 8.3 aliases; the path returned is
    the one Windows gives the open file, in its own names, for the caller's
    policy to judge before a byte is read. Only a local drive's path: a
    network or UNC path (localhost's C$ share among them) would name this
    machine's files where the policy's protected places do not, so it is
    refused, as written and as the open file's path. FolderError as
    `open_file`."""
    if not _WINDOWS:
        raise FolderError("unsupported")
    from jarvis import _win_handles
    path = Path(path)
    if not path.is_absolute() or not path.name or path.name in (".", ".."):
        raise FolderError("forbidden")
    anchor = Path(path.anchor)
    if not _local_drive(str(anchor)):
        raise FolderError("forbidden")
    with _win_dir(anchor, _parts(path.parent.relative_to(anchor)), exact=False) as parent:
        try:
            fd, info, _ = _win_handles.open_child_file(parent, path.name, exact=False)
        except OSError as e:
            raise _win_refusal(e) from None
    try:
        held = _win_handles.held_path(fd)
    except OSError:
        os.close(fd)
        raise FolderError("missing") from None
    if not _local_drive(held[:3]):
        os.close(fd)
        raise FolderError("forbidden")
    return fd, info, Path(held)


def _local_drive(anchor: str) -> bool:
    """A drive letter's root (C: and its separator), never a share."""
    return (len(anchor) == 3 and anchor[0].isascii() and anchor[0].isalpha()
            and anchor[1:] == ":\\")


def _refusal(e: OSError, name: Optional[str] = None, dir_fd: Optional[int] = None) -> FolderError:
    """An operating system error as the folder catalog says it. Opening a
    link with O_NOFOLLOW fails ELOOP, or ENOTDIR where O_DIRECTORY is asked
    for too: either way, if `name` in `dir_fd` is a link it is refused as
    one, never reported as missing."""
    if name is not None and e.errno in (errno.ELOOP, errno.ENOTDIR):
        try:
            if stat.S_ISLNK(os.stat(name, dir_fd=dir_fd, follow_symlinks=False).st_mode):
                return FolderError("forbidden")
        except OSError:
            pass
    if e.errno in (errno.ENOENT, errno.ENOTDIR):
        return FolderError("missing")
    if e.errno in (errno.EACCES, errno.EPERM):
        return FolderError("access_denied")
    if e.errno == errno.ELOOP:
        return FolderError("forbidden")
    return FolderError("missing")


@contextmanager
def opened_dir(root: Path, relative: Path) -> Iterator[int]:
    """A descriptor for `relative` inside `root`, reached component by
    component without following any link. Every read and every open of a
    file comes through here, so this is where an unsupported platform is
    refused. Windows has no descriptor for a folder to write through by
    dir_fd, so there this is refused too; its reads walk by handle."""
    if not SUPPORTED or not _POSIX_WALK:
        raise FolderError("unsupported")
    parts = [p for p in relative.parts if p not in ("", ".")]
    if any(p == ".." or "/" in p for p in parts):
        raise FolderError("forbidden")
    try:
        fd = os.open(str(root), _DIR_FLAGS)
    except OSError as e:
        raise _refusal(e) from None
    try:
        for part in parts:
            try:
                nxt = os.open(part, _DIR_FLAGS, dir_fd=fd)
            except OSError as e:
                raise _refusal(e, part, fd) from None
            os.close(fd)
            fd = nxt
        yield fd
    finally:
        os.close(fd)


def open_file(root: Path, relative: Path,
              identity: Optional[str] = None) -> tuple[int, os.stat_result]:
    """(descriptor, its stat) for the regular file `relative` inside
    `root`, by the same walk. The caller owns the descriptor and closes it:
    `opened_file` for a read here, a message attachment's held source
    (draft_files.py) for a read that outlives one call. `identity`, the
    root's recorded filesystem identity, is checked on Windows' held root;
    the POSIX walk reads as it always has."""
    if not relative.name or relative.name in (".", ".."):
        raise FolderError("forbidden")
    if _WINDOWS:
        from jarvis import _win_handles
        with _win_dir(root, _parts(relative.parent), identity=identity) as parent:
            try:
                fd, info, _ = _win_handles.open_child_file(parent, relative.name)
            except OSError as e:
                raise _win_refusal(e) from None
        return fd, info
    with opened_dir(root, relative.parent) as parent:
        try:
            fd = os.open(relative.name, _FILE_FLAGS, dir_fd=parent)
        except OSError as e:
            raise _refusal(e, relative.name, parent) from None
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise FolderError("unsupported")
    except BaseException:
        os.close(fd)
        raise
    return fd, info


@contextmanager
def opened_file(root: Path, relative: Path,
                identity: Optional[str] = None) -> Iterator[tuple[int, os.stat_result]]:
    """(descriptor, its stat) for the regular file `relative` inside
    `root`, closed when the read is done."""
    fd, info = open_file(root, relative, identity)
    try:
        yield fd, info
    finally:
        os.close(fd)


def kind_of(root: Path, relative: Path, identity: Optional[str] = None) -> str:
    """"dir", "file", "link" or "other" for one entry, by its parent's
    descriptor; FolderError `missing` if there is none."""
    if _WINDOWS:
        from jarvis import _win_handles
        parts = _parts(relative)
        with _win_dir(root, parts[:-1], identity=identity) as parent:
            if not parts:
                return "dir"
            try:
                return _win_handles.child_kind(parent, parts[-1])
            except OSError as e:
                raise _win_refusal(e) from None
    if not relative.parts:
        return "dir"
    with opened_dir(root, relative.parent) as parent:
        try:
            info = os.stat(relative.name, dir_fd=parent, follow_symlinks=False)
        except OSError as e:
            raise _refusal(e) from None
    return _kind(info.st_mode)


def _kind(mode: int) -> str:
    if stat.S_ISLNK(mode):
        return "link"
    if stat.S_ISDIR(mode):
        return "dir"
    if stat.S_ISREG(mode):
        return "file"
    return "other"


# ── a directory page ─────────────────────────────────────────────────────

@dataclass(frozen=True)
class Entry:
    name: str
    kind: str                 # dir, file, link (never followed) or other
    size: Optional[int]
    modified: Optional[float]

    def sort_key(self) -> list:
        return [0 if self.kind == "dir" else 1, self.name.casefold(), self.name]


@dataclass
class Listing:
    entries: list[Entry] = field(default_factory=list)
    withheld: int = 0          # entries the reader does not show
    scanned: int = 0
    partial: bool = False      # the directory was not looked at in full
    more: bool = False         # entries after this page
    last_key: Optional[list] = None


def list_dir(root: Path, relative: Path, visible: Callable[[Path, bool], bool],
             after: Optional[list] = None, limit: int = PAGE_ENTRIES,
             seconds: float = LIST_SECONDS, scan_max: int = SCAN_MAX_ENTRIES,
             identity: Optional[str] = None) -> Listing:
    """One page of a directory: folders first, then files, by name, after
    `after` (the last key of the page before). At most `scan_max` entries
    are looked at, within `seconds`; past either the listing is `partial`
    and its order holds only among the entries seen, so a continuation
    (by key, not by position) never repeats or skips what was seen."""
    out = Listing()
    deadline = time.monotonic() + seconds
    found: list[Entry] = []
    if _WINDOWS:
        _win_scan(root, relative, visible, after, scan_max, deadline, out, found, identity)
    else:
        _posix_scan(root, relative, visible, after, scan_max, deadline, out, found)
    found.sort(key=Entry.sort_key)
    out.entries = found[:limit]
    out.more = len(found) > limit
    if out.entries:
        out.last_key = out.entries[-1].sort_key()
    return out


def _win_scan(root, relative, visible, after, scan_max, deadline, out, found,
              identity) -> None:
    """list_dir's look on Windows: the folder's entries read through its
    handle, each with its kind as the listing gives it."""
    from jarvis import _win_handles
    with _win_dir(root, _parts(relative), identity=identity) as handle:
        try:
            for raw in _win_handles.entries(handle):
                if out.scanned >= scan_max or time.monotonic() > deadline:
                    out.partial = True
                    break
                out.scanned += 1
                if not visible(relative / raw.name, raw.kind == "dir"):
                    out.withheld += 1
                    continue
                entry = Entry(raw.name, raw.kind, raw.size if raw.kind == "file" else None,
                              raw.modified)
                if after is None or entry.sort_key() > after:
                    found.append(entry)
        except OSError as e:
            raise _win_refusal(e) from None


def _posix_scan(root, relative, visible, after, scan_max, deadline, out, found) -> None:
    with opened_dir(root, relative) as fd:
        with os.scandir(fd) as it:
            for item in it:
                if out.scanned >= scan_max or time.monotonic() > deadline:
                    out.partial = True
                    break
                out.scanned += 1
                try:
                    info = item.stat(follow_symlinks=False)
                except OSError:
                    continue
                kind = _kind(info.st_mode)
                if not visible(relative / item.name, kind == "dir"):
                    out.withheld += 1
                    continue
                entry = Entry(item.name, kind, info.st_size if kind == "file" else None,
                              info.st_mtime)
                if after is None or entry.sort_key() > after:
                    found.append(entry)


# ── a file window ────────────────────────────────────────────────────────

@dataclass
class Window:
    kind: str                       # text, transcript or binary
    size: int
    modified: float
    lines: list[str] = field(default_factory=list)
    offset: int = 0                 # bytes into the file this window starts
    next_offset: Optional[int] = None   # where the next window starts, if there is more
    first_line: Optional[int] = None    # its first line's number, where known
    clipped_lines: int = 0          # lines cut at LINE_MAX_CHARS
    newlines: int = 0               # whole lines this window used up, for the next one's number
    skipped: int = 0                # records a transcript reader could not use


def _clip(line: str, window: Window) -> str:
    if len(line) > LINE_MAX_CHARS:
        window.clipped_lines += 1
        return line[:LINE_MAX_CHARS] + " …"
    return line


def _binary(fd: int, relative: Path) -> bool:
    if Path(relative.name.lower()).suffix in repo_read.BINARY_EXTS:
        return True
    return b"\x00" in pread(fd, SNIFF_BYTES, 0)


def _start(fd: int, offset: int) -> int:
    """`offset`, moved past the rest of a line it lands in the middle of."""
    if offset <= 0:
        return 0
    if pread(fd, 1, offset - 1) == b"\n":
        return offset
    while True:
        chunk = pread(fd, 64 * 1024, offset)
        if not chunk:
            return offset
        cut = chunk.find(b"\n")
        if cut >= 0:
            return offset + cut + 1
        offset += len(chunk)


def read_text(root: Path, relative: Path, offset: int = 0,
              first_line: Optional[int] = 1, identity: Optional[str] = None) -> Window:
    """At most TEXT_MAX_LINES lines and TEXT_MAX_BYTES bytes from `offset`.
    A binary file is its kind, size and time, and nothing of its content."""
    with opened_file(root, relative, identity) as (fd, info):
        window = Window("text", info.st_size, info.st_mtime)
        if _binary(fd, relative):
            window.kind = "binary"
            return window
        offset = min(max(0, offset), info.st_size)
        data = pread(fd, TEXT_MAX_BYTES, offset)
    window.offset = offset
    window.first_line = first_line
    at_end = offset + len(data) >= info.st_size
    used = 0
    for raw in (data.split(b"\n") if data else []):
        whole = used + len(raw) < len(data)       # a newline follows it here
        if not whole and not at_end and used:
            break                                  # a line the next window starts with
        if len(window.lines) >= TEXT_MAX_LINES:
            break
        window.lines.append(_clip(raw.decode("utf-8", "replace").rstrip("\r"), window))
        used += len(raw) + (1 if whole else 0)
        window.newlines += whole
    if at_end and window.lines and window.lines[-1] == "" and data.endswith(b"\n"):
        window.lines.pop()                         # the file's last newline ends a line
    nxt = offset + used
    window.next_offset = nxt if nxt < info.st_size else None
    return window


def read_transcript(root: Path, relative: Path, message: Callable[[dict], Optional[tuple]],
                    offset: int = 0, identity: Optional[str] = None) -> Window:
    """What was said in a provider transcript, as read_history renders it,
    from `offset`: at most TRANSCRIPT_WINDOW_BYTES of raw records read, at
    most TEXT_MAX_LINES lines shown. A malformed or half-written record is
    skipped and counted; tool calls and results are not shown."""
    with opened_file(root, relative, identity) as (fd, info):
        window = Window("transcript", info.st_size, info.st_mtime)
        offset = _start(fd, min(max(0, offset), info.st_size))
        data = pread(fd, TRANSCRIPT_WINDOW_BYTES, offset)
    at_end = offset + len(data) >= info.st_size
    fallback = time.strftime("%Y-%m-%d", time.localtime(info.st_mtime))
    used = shown_bytes = 0
    records = data.split(b"\n")
    for i, raw in enumerate(records):
        last = i == len(records) - 1
        if last and not at_end:
            if used == 0:
                used = len(data)                   # one record bigger than a window: passed over
                window.skipped += 1
            break
        if len(window.lines) >= TEXT_MAX_LINES or shown_bytes >= TEXT_MAX_BYTES:
            break
        used += len(raw) + (0 if last else 1)
        if not raw.strip():
            continue
        try:
            obj = json.loads(raw)
        except ValueError:
            window.skipped += 1
            continue
        said = message(obj) if isinstance(obj, dict) else None
        if not said:
            continue
        for line in history_read.said_lines(obj, said[0], said[1], fallback):
            line = _clip(line, window)
            window.lines.append(line)
            shown_bytes += len(line.encode("utf-8")) + 1
    window.offset = offset
    nxt = offset + used
    window.next_offset = nxt if nxt < info.st_size else None
    return window
