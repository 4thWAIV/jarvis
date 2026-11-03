"""What this JARVIS can do with a received file, advertised on its node
update (team_api.node_update -> `attachments`), and how a recipient's
nodes are judged from the team server's snapshot.

The advertisement is versioned and bounded (messaging wire's
`AttachmentCapabilities`): per format, the operations the readers' health
check says work now (bundle_readers.health), the archive budgets, and the
work-package manifest versions and modes. The health check runs at most
every HEALTH_EVERY_SEC, never on each five-second update, and the record
says how old it is (`observed_age_ms`). A ZIP's listing is the standard
library's (bundle_archive), so `zip` is always listed; every format a node
lists it can also download.

A node whose record has none is an older JARVIS: `legacy`, so unknown,
never assumed able or unable. A node that is not online is `offline`, and a
record checked longer than CAPABILITY_FRESH_SEC ago is `stale`; only
`advertised` counts toward a ready prediction, and a member's nodes are
judged one by one, never merged.
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone
from typing import Callable, Literal, Mapping, Optional

from jarvis import bundle_archive, work_packages
from jarvis import messaging_wire as wire

log = logging.getLogger("jarvis.bundle_capabilities")

# A capability record older than this, or from a node that is not online,
# is not trusted for a prediction.
CAPABILITY_FRESH_SEC = 10 * 60
# How often the readers' health is checked again for the advertisement.
HEALTH_EVERY_SEC = CAPABILITY_FRESH_SEC / 2
# The work-package format docs/work-packages.md describes. Its package.json
# carries no format version, so the one format there is is version 1.
WORK_PACKAGE_MANIFEST_VERSIONS = (1,)

Standing = Literal["advertised", "legacy", "stale", "offline"]

# The operations listed first, in the wire's order.
_ORDER = {op: i for i, op in enumerate(("download", "list_archive", "extract", "read_text",
                                        "render", "ocr", "edit_export", "execute"))}


def _readers_health() -> Mapping[str, object]:
    from jarvis import bundle_readers
    return bundle_readers.health()


def capabilities_from(health: Mapping[str, object], age_ms: int,
                      budgets: bundle_archive.Budgets = bundle_archive.DEFAULT_BUDGETS) -> dict:
    """The advertisement for one health reading (`format -> ReaderHealth`):
    each available reader's operations with `download`, and `zip` listing.
    Wire JSON, validated against the contract before it is returned."""
    formats: dict[str, set[str]] = {"zip": {"download", "list_archive"}}
    for fmt, reading in sorted(health.items()):
        if not getattr(reading, "available", False):
            continue
        ops = set(getattr(reading, "operations", ())) - {"execute"}
        formats.setdefault(fmt, set()).update(ops | {"download"})
    caps = wire.AttachmentCapabilities(
        observed_age_ms=max(0, min(age_ms, wire.CAPABILITY_AGE_MAX_MS)),
        max_file_bytes=wire.FILE_MAX_BYTES, max_archive_entries=budgets.max_entries,
        max_expanded_bytes=budgets.max_total_bytes,
        formats=[wire.FormatCapability(format=f, operations=sorted(ops, key=_ORDER.get))
                 for f, ops in list(formats.items())[:wire.FORMATS_MAX]],
        work_packages=wire.WorkPackageCapability(
            manifest_versions=list(WORK_PACKAGE_MANIFEST_VERSIONS),
            modes=list(work_packages.MODES)))
    return caps.model_dump(mode="json")


class Advertiser:
    """The node's advertisement, its health checked at most every
    HEALTH_EVERY_SEC. A check that fails keeps the last reading (it ages
    and goes stale for the senders), or advertises only `zip` when there
    has never been one."""

    def __init__(self, health: Callable[[], Mapping[str, object]] = _readers_health,
                 clock: Callable[[], float] = time.monotonic,
                 every: float = HEALTH_EVERY_SEC):
        self.health, self.clock, self.every = health, clock, every
        self._reading: Optional[Mapping[str, object]] = None
        self._checked: Optional[float] = None
        self._lock = threading.Lock()

    def _check(self, now: float) -> None:
        try:
            self._reading = dict(self.health())
            self._checked = now
        except Exception as e:  # a reader's health must never stop the node update
            log.warning("bundle capabilities: health check failed (%s)", type(e).__name__)
            if self._checked is None:
                self._reading, self._checked = {}, now

    def current(self) -> dict:
        with self._lock:
            now = self.clock()
            if self._checked is None or now - self._checked >= self.every:
                self._check(now)
            age_ms = int((now - self._checked) * 1000)
            return capabilities_from(self._reading or {}, age_ms)


advertiser = Advertiser()


def local_capabilities() -> dict:
    """This node's AttachmentCapabilities as wire JSON, for node_update."""
    return advertiser.current()


def _parse_time(value) -> Optional[datetime]:
    if isinstance(value, datetime):
        at = value
    elif isinstance(value, str):
        try:
            at = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    return at if at.tzinfo else at.replace(tzinfo=timezone.utc)


def node_standing(node: dict, now_ms: Optional[int] = None) -> Standing:
    """How far one NodeView's attachment capabilities can be trusted."""
    if node.get("presence") != "online":
        return "offline"
    if not node.get("attachments"):
        return "legacy"
    observed = _parse_time(node.get("attachments_observed_at"))
    now = now_ms if now_ms is not None else int(time.time() * 1000)
    if observed is None or now - observed.timestamp() * 1000 > CAPABILITY_FRESH_SEC * 1000:
        return "stale"
    return "advertised"


def advertised(node: dict) -> Optional[wire.AttachmentCapabilities]:
    """A node's capabilities as the contract reads them, or None when its
    record has none or they cannot be read (from a newer JARVIS)."""
    raw = node.get("attachments")
    if not raw:
        return None
    try:
        return wire.AttachmentCapabilities.model_validate(raw)
    except ValueError:
        return None
