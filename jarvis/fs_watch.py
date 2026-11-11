"""Finding what changed in the watched folders (plan-uds.md, "How changes
are found").

Three parts, none of which reads a file's contents:

- `Watcher`: one FSEvents stream over several root folders, reached
  through `ctypes` on CoreServices (no new dependency). It asks for file
  events (`FileEvents`), no deferral (`NoDefer`) and news of a root itself
  moving or going (`WatchRoot`), with plain C paths (`UseCFTypes` off).
  The stream is scheduled on a GCD serial queue of its own
  (`FSEventStreamSetDispatchQueue`, `dispatch_queue_create` from
  libSystem), so no CFRunLoop thread is kept. It starts from a given event
  ID (`since`), so FSEvents replays what changed while JARVIS was away;
  `last_event_id` is the newest ID handed to `on_events` and returned
  from, which the caller persists only once it has handled them. An event
  that says changes were dropped or coalesced (`must_scan`) asks the
  caller to scan that subtree (`scan_requests`).
- `scan`: the backup scan, a stat-only walk comparing a folder with what
  is known (size, modified time, inode). It never follows a link, takes
  regular files only, always passes over `.git`, and lets the caller skip
  more (the folder catalog's rule, the index's scope, `git_ignored`).
- `ScanClock`: when a backup scan is due: at start, after the Mac wakes
  (the wall clock moved on more than `WAKE_GAP_SEC` beyond the monotonic
  clock, which does not advance in sleep) and every `BACKUP_EVERY_SEC`.

Paths are canonical: roots go through realpath, since FSEvents reports
/private/var for /var. Nothing here raises into CoreServices: an exception
in the caller's `on_events` is logged. `on_events` runs on the stream's
queue, one batch at a time; it must not call `stop` or `restart`.

git is asked through one seam, `RUNNER`, and only where the Command Line
Tools are installed (`workspace_ref.git_available`). This module imports
nothing from server.py or the knowledge engine.
"""
from __future__ import annotations

import ctypes
import ctypes.util
import logging
import os
import stat as stat_mod
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Mapping, Optional

log = logging.getLogger(__name__)

# ── FSEvents constants (FSEvents.h) ──────────────────────────────────────

SINCE_NOW = 0xFFFFFFFFFFFFFFFF          # kFSEventStreamEventIdSinceNow

CREATE_USE_CF_TYPES = 0x01
CREATE_NO_DEFER = 0x02
CREATE_WATCH_ROOT = 0x04
CREATE_FILE_EVENTS = 0x10
CREATE_FLAGS = CREATE_FILE_EVENTS | CREATE_NO_DEFER | CREATE_WATCH_ROOT

MUST_SCAN_SUBDIRS = 0x01
USER_DROPPED = 0x02
KERNEL_DROPPED = 0x04
EVENT_IDS_WRAPPED = 0x08
HISTORY_DONE = 0x10
ROOT_CHANGED = 0x20
ITEM_CREATED = 0x100
ITEM_REMOVED = 0x200
ITEM_INODE_META_MOD = 0x400
ITEM_RENAMED = 0x800
ITEM_MODIFIED = 0x1000
ITEM_IS_FILE = 0x10000
ITEM_IS_DIR = 0x20000
ITEM_IS_SYMLINK = 0x40000

_UTF8 = 0x08000100                      # kCFStringEncodingUTF8

LATENCY_SEC = 0.3
DRAIN_TIMEOUT_SEC = 5.0

# ── events ────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Event:
    path: str
    flags: int
    event_id: int

    created = property(lambda self: bool(self.flags & ITEM_CREATED))
    removed = property(lambda self: bool(self.flags & ITEM_REMOVED))
    renamed = property(lambda self: bool(self.flags & ITEM_RENAMED))
    modified = property(lambda self: bool(self.flags & (ITEM_MODIFIED | ITEM_INODE_META_MOD)))
    is_dir = property(lambda self: bool(self.flags & ITEM_IS_DIR))
    is_file = property(lambda self: bool(self.flags & ITEM_IS_FILE))
    is_symlink = property(lambda self: bool(self.flags & ITEM_IS_SYMLINK))
    must_scan = property(lambda self: bool(self.flags & (MUST_SCAN_SUBDIRS | USER_DROPPED | KERNEL_DROPPED)))
    root_changed = property(lambda self: bool(self.flags & ROOT_CHANGED))
    history_done = property(lambda self: bool(self.flags & HISTORY_DONE))


def canonical(path: str) -> str:
    return os.path.realpath(os.path.expanduser(path))


def locate(path: str, roots: list[str]) -> Optional[tuple[str, str]]:
    """The (root, path relative to it) a canonical path falls under, the
    deepest root first; None outside every root. The root itself is ''."""
    for root in sorted((canonical(r) for r in roots), key=len, reverse=True):
        if path == root:
            return root, ""
        if path.startswith(root.rstrip("/") + "/"):
            return root, path[len(root.rstrip("/")) + 1:]
    return None


def scan_requests(events: list[Event], roots: list[str]) -> list[tuple[str, str]]:
    """Every (root, subtree) the events ask to be scanned in full: a
    must_scan event's folder, and the whole root when the root itself moved
    or went. A subtree inside another asked for is left out."""
    asked: set[tuple[str, str]] = set()
    for event in events:
        if not (event.must_scan or event.root_changed):
            continue
        where = locate(event.path.rstrip("/") or "/", roots)
        if where is None:
            if event.root_changed:
                where = next(((canonical(r), "") for r in roots
                              if canonical(r).startswith(event.path.rstrip("/") + "/")
                              or canonical(r) == event.path.rstrip("/")), None)
            if where is None:
                continue
        asked.add((where[0], "" if event.root_changed else where[1]))
    kept = []
    for root, under in sorted(asked, key=lambda a: (a[0], len(a[1]))):
        if any(r == root and (u == "" or under == u or under.startswith(u + "/")) for r, u in kept):
            continue
        kept.append((root, under))
    return kept


# ── the CoreServices and libdispatch bindings ────────────────────────────

_CALLBACK = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t,
                             ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32),
                             ctypes.POINTER(ctypes.c_uint64))
_DISPATCH_FN = ctypes.CFUNCTYPE(None, ctypes.c_void_p)


class _Context(ctypes.Structure):
    _fields_ = [("version", ctypes.c_long), ("info", ctypes.c_void_p),
                ("retain", ctypes.c_void_p), ("release", ctypes.c_void_p),
                ("copyDescription", ctypes.c_void_p)]


class _Lib:
    def __init__(self) -> None:
        cs = ctypes.CDLL("/System/Library/Frameworks/CoreServices.framework/CoreServices")
        cf = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
        system = ctypes.CDLL("/usr/lib/libSystem.B.dylib")
        self.type_array_callbacks = ctypes.c_void_p.in_dll(cf, "kCFTypeArrayCallBacks")

        def fn(lib, name, res, *args):
            f = getattr(lib, name)
            f.restype, f.argtypes = res, list(args)
            return f

        vp = ctypes.c_void_p
        self.CFStringCreateWithCString = fn(cf, "CFStringCreateWithCString", vp, vp, ctypes.c_char_p, ctypes.c_uint32)
        self.CFArrayCreate = fn(cf, "CFArrayCreate", vp, vp, ctypes.POINTER(vp), ctypes.c_long, vp)
        self.CFRelease = fn(cf, "CFRelease", None, vp)
        self.Create = fn(cs, "FSEventStreamCreate", vp, vp, _CALLBACK, ctypes.POINTER(_Context), vp,
                         ctypes.c_uint64, ctypes.c_double, ctypes.c_uint32)
        self.SetDispatchQueue = fn(cs, "FSEventStreamSetDispatchQueue", None, vp, vp)
        self.Start = fn(cs, "FSEventStreamStart", ctypes.c_ubyte, vp)
        self.FlushSync = fn(cs, "FSEventStreamFlushSync", None, vp)
        self.Stop = fn(cs, "FSEventStreamStop", None, vp)
        self.Invalidate = fn(cs, "FSEventStreamInvalidate", None, vp)
        self.Release = fn(cs, "FSEventStreamRelease", None, vp)
        self.GetLatestEventId = fn(cs, "FSEventStreamGetLatestEventId", ctypes.c_uint64, vp)
        self.CurrentEventId = fn(cs, "FSEventsGetCurrentEventId", ctypes.c_uint64)
        self.queue_create = fn(system, "dispatch_queue_create", vp, ctypes.c_char_p, vp)
        self.dispatch_sync_f = fn(system, "dispatch_sync_f", None, vp, vp, _DISPATCH_FN)
        self.dispatch_release = fn(system, "dispatch_release", None, vp)

    def path_array(self, paths: list[str]) -> int:
        strings = [self.CFStringCreateWithCString(None, p.encode("utf-8"), _UTF8) for p in paths]
        values = (ctypes.c_void_p * len(strings))(*strings)
        array = self.CFArrayCreate(None, values, len(strings), ctypes.addressof(self.type_array_callbacks))
        for s in strings:
            self.CFRelease(s)        # the array retains its own
        return array


_lib: Optional[_Lib] = None
_lib_lock = threading.Lock()


def _bindings() -> _Lib:
    global _lib
    with _lib_lock:
        if _lib is None:
            _lib = _Lib()
        return _lib


def available() -> bool:
    """macOS, with CoreServices and libdispatch loadable."""
    if sys.platform != "darwin":
        return False
    try:
        _bindings()
    except (OSError, AttributeError, ValueError):
        return False
    return True


def current_event_id() -> int:
    return int(_bindings().CurrentEventId())


@_DISPATCH_FN
def _nothing(_context) -> None:
    pass


class Watcher:
    """One FSEvents stream over `roots`, handing each batch of events to
    `on_events` on the stream's own serial queue."""

    def __init__(self, roots: list[str], on_events: Callable[[list[Event]], None], *,
                 since: Optional[int] = None, latency: float = LATENCY_SEC):
        self.roots = [canonical(r) for r in roots]
        self.on_events = on_events
        self.latency = latency
        self._since = since
        self.last_event_id: int = since or 0
        self._stream: Optional[int] = None
        self._queue: Optional[int] = None
        self._callback = _CALLBACK(self._deliver)     # kept for the stream's life
        self._lock = threading.Lock()

    @property
    def running(self) -> bool:
        return self._stream is not None

    def start(self) -> None:
        with self._lock:
            if self._stream is not None:
                return
            lib = _bindings()
            if self._since is None:
                self.last_event_id = int(lib.CurrentEventId())
            since = SINCE_NOW if self._since is None else int(self._since)
            paths = lib.path_array(self.roots)
            try:
                context = _Context(0, None, None, None, None)
                stream = lib.Create(None, self._callback, ctypes.byref(context), paths,
                                    since, float(self.latency), CREATE_FLAGS)
            finally:
                lib.CFRelease(paths)
            if not stream:
                raise OSError("FSEventStreamCreate failed")
            queue = lib.queue_create(b"jarvis.fs_watch", None)
            lib.SetDispatchQueue(stream, queue)
            if not lib.Start(stream):
                lib.Invalidate(stream)
                lib.Release(stream)
                lib.dispatch_release(queue)
                raise OSError("FSEventStreamStart failed")
            self._stream, self._queue = stream, queue

    def stop(self) -> None:
        """Deliver what is pending, then invalidate and release the stream
        and its queue. Never call it from `on_events`."""
        with self._lock:
            stream, queue = self._stream, self._queue
            if stream is None:
                return
            lib = _bindings()
            lib.FlushSync(stream)
            lib.Stop(stream)
            lib.Invalidate(stream)
            lib.dispatch_sync_f(queue, None, _nothing)     # nothing left running on it
            lib.Release(stream)
            lib.dispatch_release(queue)
            self._stream = self._queue = None
            self._since = self.last_event_id

    def restart(self, roots: list[str]) -> None:
        """The roots changed: a new stream from `last_event_id`."""
        self.stop()
        self.roots = [canonical(r) for r in roots]
        self._since = self.last_event_id or None
        self.start()

    def _deliver(self, _stream, _info, count, paths, flags, ids) -> None:
        """The C callback. Never raises into CoreServices."""
        try:
            names = ctypes.cast(paths, ctypes.POINTER(ctypes.c_char_p))
            events = [Event(os.fsdecode(names[i] or b""), int(flags[i]), int(ids[i]))
                      for i in range(count)]
            self.handle(events)
        except BaseException:      # noqa: BLE001 - a C callback must not raise
            log.exception("fs_watch: reading an FSEvents batch failed")

    def handle(self, events: list[Event]) -> None:
        """Hand one batch to `on_events`; the newest ID is kept only once it
        has returned."""
        if not events:
            return
        try:
            self.on_events(events)
        except Exception:          # noqa: BLE001
            log.exception("fs_watch: on_events failed; those events are not marked handled")
            return
        newest = max(e.event_id for e in events)
        if newest > self.last_event_id:
            self.last_event_id = newest


# ── the backup scan ──────────────────────────────────────────────────────


@dataclass(frozen=True)
class Stat:
    size: int
    mtime_ns: int
    inode: int


@dataclass
class ScanResult:
    added: list[str] = field(default_factory=list)
    changed: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    skipped: int = 0
    seconds: float = 0.0
    complete: bool = True          # False: the budget ran out; `removed` is then empty


def _stat(st: os.stat_result) -> Stat:
    return Stat(st.st_size, st.st_mtime_ns, st.st_ino)


def stat_of(path) -> Optional[Stat]:
    """A regular file's size, modified time and inode, never through a
    link; None for anything else or nothing there."""
    try:
        st = os.lstat(path)
    except OSError:
        return None
    return _stat(st) if stat_mod.S_ISREG(st.st_mode) else None


def _no_skip(_rel: str, _is_dir: bool) -> bool:
    return False


def scan(root: str, known: Mapping[str, Stat], *, under: str = "",
         skip: Callable[[str, bool], bool] = _no_skip,
         budget_sec: Optional[float] = None) -> ScanResult:
    """Compare the regular files under `root` (or its subtree `under`) with
    `known`, by size, modified time and inode. Paths are relative to root,
    '/'-joined. A link is neither walked nor reported; `.git` is always
    passed over. A folder that cannot be read keeps what is known under it
    (nothing there is reported removed); with the budget spent the result
    is incomplete and reports nothing removed."""
    began = time.monotonic()
    root = canonical(root)
    under = under.strip("/")
    result = ScanResult()
    seen: set[str] = set()
    unread: list[str] = []
    stack = [under]
    while stack:
        if budget_sec is not None and time.monotonic() - began > budget_sec:
            result.complete = False
            break
        rel_dir = stack.pop()
        try:
            entries = list(os.scandir(os.path.join(root, rel_dir) if rel_dir else root))
        except OSError:
            unread.append(rel_dir)
            continue
        for entry in entries:
            rel = f"{rel_dir}/{entry.name}" if rel_dir else entry.name
            try:
                if entry.is_symlink():
                    result.skipped += 1
                    continue
                is_dir = entry.is_dir(follow_symlinks=False)
                if is_dir:
                    if entry.name == ".git" or skip(rel, True):
                        result.skipped += 1
                    else:
                        stack.append(rel)
                    continue
                if not entry.is_file(follow_symlinks=False) or skip(rel, False):
                    result.skipped += 1
                    continue
                now = _stat(entry.stat(follow_symlinks=False))
            except OSError:
                unread.append(rel)
                continue
            seen.add(rel)
            before = known.get(rel)
            if before is None:
                result.added.append(rel)
            elif before != now:
                result.changed.append(rel)
    if result.complete:
        prefix = under + "/" if under else ""
        for rel in known:
            if rel in seen or (prefix and not rel.startswith(prefix)):
                continue
            if any(u == "" or rel == u or rel.startswith(u + "/") for u in unread):
                continue
            result.removed.append(rel)
    result.added.sort()
    result.changed.sort()
    result.removed.sort()
    result.seconds = time.monotonic() - began
    return result


# ── git-ignored paths ────────────────────────────────────────────────────

GIT_SECONDS = 20.0


def _run(args: list[str]) -> Optional[subprocess.CompletedProcess]:
    from jarvis import workspace_ref
    if not workspace_ref.git_available():
        return None
    try:
        return subprocess.run(["git", *args], capture_output=True, timeout=GIT_SECONDS)
    except (OSError, subprocess.SubprocessError):
        return None


# Every git call goes through this seam; tests replace it. It takes git's
# arguments (without "git") and gives a CompletedProcess, or None when git
# cannot run here.
RUNNER: Callable[[list[str]], Optional[subprocess.CompletedProcess]] = _run


def git_ignored(root: str) -> Callable[[str, bool], bool]:
    """A `skip` for `scan`: the paths git ignores under `root`, from one
    `git ls-files --others --ignored --exclude-standard --directory` call
    (an ignored folder is listed once, with a trailing slash). Outside a
    work tree, or without git, nothing is ignored. `.git` is always
    skipped."""
    done = RUNNER(["-C", canonical(root), "ls-files", "--others", "--ignored",
                   "--exclude-standard", "--directory", "-z"])
    files: set[str] = set()
    folders: set[str] = set()
    if done is not None and done.returncode == 0:
        for raw in (done.stdout or b"").split(b"\0"):
            if not raw:
                continue
            name = os.fsdecode(raw)
            (folders if name.endswith("/") else files).add(name.rstrip("/"))

    def ignored(rel: str, is_dir: bool) -> bool:
        parts = rel.split("/")
        if ".git" in parts:
            return True
        if rel in files or rel in folders:
            return True
        return any("/".join(parts[:i]) in folders for i in range(1, len(parts)))

    return ignored


# ── when a backup scan is due ────────────────────────────────────────────

BACKUP_EVERY_SEC = 6 * 3600
WAKE_GAP_SEC = 60.0


class ScanClock:
    """Says when a backup scan is due: 'start' the first time, 'wake' once
    the wall clock has moved more than WAKE_GAP_SEC beyond the monotonic
    clock since the last look (the Mac slept), 'interval' every
    BACKUP_EVERY_SEC. A reason returned counts as the scan begun."""

    def __init__(self, every_sec: float = BACKUP_EVERY_SEC, wake_gap_sec: float = WAKE_GAP_SEC):
        self.every_sec = every_sec
        self.wake_gap_sec = wake_gap_sec
        self._last_scan_mono: Optional[float] = None
        self._last_look: Optional[tuple[float, float]] = None

    def due(self, now_wall: Optional[float] = None, now_mono: Optional[float] = None) -> Optional[str]:
        now_wall = time.time() if now_wall is None else now_wall
        now_mono = time.monotonic() if now_mono is None else now_mono
        last, self._last_look = self._last_look, (now_wall, now_mono)
        reason = None
        if self._last_scan_mono is None:
            reason = "start"
        elif last is not None and (now_wall - last[0]) - (now_mono - last[1]) > self.wake_gap_sec:
            reason = "wake"
        elif now_mono - self._last_scan_mono >= self.every_sec:
            reason = "interval"
        if reason:
            self._last_scan_mono = now_mono
        return reason
