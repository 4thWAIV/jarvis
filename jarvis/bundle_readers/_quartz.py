"""macOS's own PDF engine (PDFKit and CoreGraphics), reached through
`osascript -l JavaScript`, and the PNG writer for what it draws.

No ImageIO, NSBitmapImageRep, CGImageDestination or `sips`: on this Mac
ImageIO's writers crash (SIGSEGV), and its GIF reader too. A page is drawn
into a plain CoreGraphics bitmap (RGBA, top row first), the raw bytes are
written to a private file, and `png` encodes them with zlib.

`run` is the one subprocess seam; tests replace `RUNNER`. Every call has a
timeout, and a crash, a timeout or an answer that is not JSON is
`QuartzFailed`, never an exception the caller has to guess at.
"""
from __future__ import annotations

import json
import shutil
import struct
import subprocess
import sys
import time
import zlib
from typing import Callable

OSASCRIPT = "/usr/bin/osascript"

# The seam: subprocess.run's signature. Tests put a fake here.
RUNNER: Callable[..., subprocess.CompletedProcess] = subprocess.run

# How long health is believed before osascript is asked again.
HEALTH_TTL_SEC = 300.0
HEALTH_TIMEOUT_SEC = 15.0


class QuartzFailed(Exception):
    """osascript did not answer: `reason` is "unavailable", "timeout",
    "crashed" or "bad_answer"."""

    def __init__(self, reason: str, detail: str = ""):
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason


def available_here() -> bool:
    return sys.platform == "darwin" and shutil.which(OSASCRIPT) is not None


def run(script: str, args: list[str], timeout: float) -> dict:
    """Run a JXA script's `run(argv)` and return the JSON it answers."""
    if RUNNER is subprocess.run and not available_here():
        raise QuartzFailed("unavailable", "osascript is not on this machine")
    try:
        done = RUNNER([OSASCRIPT, "-l", "JavaScript", "-e", script, *args],
                      capture_output=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        raise QuartzFailed("timeout", f"after {timeout:.0f}s") from None
    except OSError as e:
        raise QuartzFailed("unavailable", type(e).__name__) from None
    if done.returncode != 0:
        raise QuartzFailed("crashed", f"exit {done.returncode}")
    try:
        answer = json.loads(done.stdout.decode("utf-8", "replace"))
    except ValueError:
        raise QuartzFailed("bad_answer") from None
    if not isinstance(answer, dict):
        raise QuartzFailed("bad_answer")
    return answer


_HEALTH_SCRIPT = """
ObjC.import('Foundation'); ObjC.import('PDFKit'); ObjC.import('CoreGraphics');
function run(argv) {
  var cs = $.CGColorSpaceCreateDeviceRGB();
  var ctx = $.CGBitmapContextCreate(null, 4, 4, 8, 16, cs, 1);
  var doc = $.PDFDocument.alloc.initWithData($.NSData.data);
  return JSON.stringify({pdfkit: typeof $.PDFDocument !== 'undefined', bitmap: !!ctx});
}
"""

_health: dict[str, object] = {"at": None, "ok": False}


def healthy(now: Callable[[], float] = time.monotonic) -> bool:
    """Whether PDFKit and a CoreGraphics bitmap answer through osascript,
    asked at most once per HEALTH_TTL_SEC."""
    at = _health["at"]
    if at is not None and now() - float(at) < HEALTH_TTL_SEC:
        return bool(_health["ok"])
    try:
        answer = run(_HEALTH_SCRIPT, [], HEALTH_TIMEOUT_SEC)
        ok = bool(answer.get("pdfkit")) and bool(answer.get("bitmap"))
    except QuartzFailed:
        ok = False
    _health.update(at=now(), ok=ok)
    return ok


def forget_health() -> None:
    _health.update(at=None, ok=False)


def png(width: int, height: int, rgba: bytes) -> bytes:
    """Raw 8-bit RGBA rows, top first, as a PNG. CoreGraphics gives
    premultiplied alpha; pages are drawn on white, so alpha is opaque."""
    stride = width * 4
    if width < 1 or height < 1 or len(rgba) != stride * height:
        raise ValueError("bitmap size does not match its dimensions")
    rows = b"".join(b"\x00" + rgba[y * stride:(y + 1) * stride] for y in range(height))

    def chunk(kind: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + kind + data
                + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF))

    header = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header)
            + chunk(b"IDAT", zlib.compress(rows, 6)) + chunk(b"IEND", b""))

