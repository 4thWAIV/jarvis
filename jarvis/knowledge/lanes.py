"""The engine's two lanes (UDS plan, "Two lanes").

Record reads and writes and change detection run at the process's normal
priority and are never paused. Text extraction, sections, embedding,
compaction, the linker and cards are the background lane: their threads
lower themselves here, and the pace (`Engine.set_pace`) pauses them.

On macOS a thread is lowered by its quality of service
(`pthread_set_qos_class_self_np(QOS_CLASS_BACKGROUND)`), which also
throttles its disk reads and writes; elsewhere (CI runs Linux) the calls
only keep the flag, so the lanes are still tested.

A background thread that takes a lock record writes need, or waits on a
commit, would hold them up at its own low priority (priority inversion).
`foreground()` raises it for exactly that span: `Store.commit`, a flush and
the manifest installs run inside it.
"""
from __future__ import annotations

import ctypes
import logging
import sys
import threading
from contextlib import contextmanager
from typing import Iterator, Optional

log = logging.getLogger("jarvis.knowledge.lanes")

# The build (reading files, embedding them) keeps to this share of one core,
# whatever else the Mac is doing: after each step its thread rests long
# enough that the CPU the step took is this share of the time (`rest_for`).
# The pace still stops it while the conversation needs the machine
# (Tim, 2026-11-12: a fixed slice of one core, not the Mac's total CPU).
BUILD_SHARE = 0.5

QOS_CLASS_UNSPECIFIED = 0x00
QOS_CLASS_BACKGROUND = 0x09
QOS_CLASS_DEFAULT = 0x15

_local = threading.local()
_lib: Optional[ctypes.CDLL] = None
_lib_failed = False


def _libsystem() -> Optional[ctypes.CDLL]:
    global _lib, _lib_failed
    if sys.platform != "darwin" or _lib_failed:
        return None
    if _lib is None:
        try:
            lib = ctypes.CDLL("/usr/lib/libSystem.dylib", use_errno=True)
            lib.pthread_set_qos_class_self_np.argtypes = [ctypes.c_uint, ctypes.c_int]
            lib.pthread_set_qos_class_self_np.restype = ctypes.c_int
            lib.pthread_self.restype = ctypes.c_void_p
            lib.pthread_get_qos_class_np.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint),
                                                     ctypes.POINTER(ctypes.c_int)]
            lib.pthread_get_qos_class_np.restype = ctypes.c_int
            _lib = lib
        except (OSError, AttributeError) as e:
            log.warning("thread priorities are not available (%s)", e)
            _lib_failed = True
            return None
    return _lib


def _set_qos(qos: int) -> None:
    lib = _libsystem()
    if lib is None:
        return
    if lib.pthread_set_qos_class_self_np(qos, 0) != 0:
        log.debug("could not set this thread's QoS to %#x (errno %s)", qos, ctypes.get_errno())


def qos_class() -> Optional[int]:
    """This thread's QoS class on macOS, else None."""
    lib = _libsystem()
    if lib is None:
        return None
    qos, rel = ctypes.c_uint(0), ctypes.c_int(0)
    if lib.pthread_get_qos_class_np(lib.pthread_self(), ctypes.byref(qos), ctypes.byref(rel)) != 0:
        return None
    return qos.value


def background() -> None:
    """Lower the calling thread into the background lane, for good."""
    if getattr(_local, "lowered", False):
        return
    before = qos_class()
    _local.normal = before if before not in (None, QOS_CLASS_UNSPECIFIED) else QOS_CLASS_DEFAULT
    _local.lowered = True
    _local.raised = 0
    _set_qos(QOS_CLASS_BACKGROUND)


def is_background() -> bool:
    """Whether this thread is in the background lane and not raised now."""
    return bool(getattr(_local, "lowered", False)) and not getattr(_local, "raised", 0)


@contextmanager
def foreground() -> Iterator[None]:
    """On a lowered thread, run at its normal priority for the span, then
    lower it again; on any other thread, nothing. Nests. Also a decorator."""
    if not getattr(_local, "lowered", False):
        yield
        return
    _local.raised = getattr(_local, "raised", 0) + 1
    if _local.raised == 1:
        _set_qos(_local.normal)
    try:
        yield
    finally:
        _local.raised -= 1
        if _local.raised == 0:
            _set_qos(QOS_CLASS_BACKGROUND)


@contextmanager
def lowered() -> Iterator[None]:
    """The background lane for a span on a thread that is otherwise normal
    (a request thread running the linker or a compaction)."""
    if getattr(_local, "lowered", False):
        yield
        return
    background()
    try:
        yield
    finally:
        _local.lowered = False
        _local.raised = 0
        _set_qos(_local.normal)


def rest_for(cpu_used: float, share: float = BUILD_SHARE) -> float:
    """How long a background thread rests after a step that took
    `cpu_used` seconds of CPU, so that it keeps to `share` of one core."""
    return max(0.0, cpu_used) * (1.0 / share - 1.0)
