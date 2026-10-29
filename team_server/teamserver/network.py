"""The team's network: each paired JARVIS backend's current record, the
snapshot every backend receives in answer to its update, and the server's
own health.

A node is a running JARVIS backend. Its record (`network_nodes`, keyed by
the node's own ID) belongs to the member whose device session first
published it; the owner is never taken from the request, and nobody else
can publish under that ID. Each update comes from one process (`boot_id`)
with a rising `seq`: an update from a boot the node has moved past, or a
`seq` not above the last stored, is refused. Every write is conditional on
the record it was checked against, so two updates can never both pass. The
last human activity moves only for a new `activity_seq`, and never back.

Presence, quota ages and the end-of-week projections are worked out when a
snapshot is built, from the stored receipt and observation times; none of
them is stored.

The server's health is one monitor per process: a database ping every
PROBE_INTERVAL_SEC shared by every caller (`/health/ready` included), the
last node write that succeeded, the last database error by class name only,
failures in a row, uptime, CPU and memory, and a short history.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from collections import deque
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Optional

from pymongo import ReturnDocument
from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import DuplicateKeyError, PyMongoError

from teamserver import build_info, clock, team as team_mod
from teamserver.errors import conflict, forbidden
from teamserver.models import (AccountPace, BrainSummary, HealthPoint,
                               NetworkSnapshot, NodeUpdate, NodeView, Resources,
                               ServerHealth)

log = logging.getLogger("teamserver.network")

CAPABILITY = "network"
ONLINE_WITHIN = timedelta(seconds=15)
DELAYED_WITHIN = timedelta(seconds=45)
# A member's backends: each data directory is one node. Bounded so one
# session cannot fill the roster with invented node IDs.
NODES_PER_MEMBER = 16
# Boots a node has moved past, kept to refuse their delayed updates.
RETIRED_BOOTS_KEPT = 16
# A node that has sent nothing for this long is removed (TTL index).
NODE_PURGE_AFTER = timedelta(days=30)
WRITE_RETRIES = 8
WEEK_HOURS = 7 * 24.0

PROBE_INTERVAL_SEC = 5
PROBE_TIMEOUT_SEC = 3
HISTORY_EVERY = timedelta(seconds=30)
HISTORY_POINTS = 30
RESOURCES_AGE_MAX_MS = 60_000

META_ID = "network"


class DatabaseDown(Exception):
    """The database failed on the network route: answered 503 with the
    server's health alone."""


# -- storing a node's update ---------------------------------------------------

async def publish(db: AsyncDatabase, member: dict, team: dict, update: NodeUpdate,
                  health: "HealthMonitor") -> NetworkSnapshot:
    """Store `member`'s update and return the team's snapshot."""
    now = clock.now()
    with health.watching(write=True):
        await store_node(db, member, update, now)
        revision = await next_revision(db)
    health.wrote(now)
    with health.watching():
        docs = await current_nodes(db, team)
    return snapshot(docs, team, update.node_id, revision, now, health.report())


async def store_node(db: AsyncDatabase, member: dict, update: NodeUpdate,
                     now: datetime) -> None:
    node_id = str(update.node_id)
    for _ in range(WRITE_RETRIES):
        current = await db.network_nodes.find_one({"_id": node_id})
        if current is None:
            mine = await db.network_nodes.count_documents(
                {"owner_github_id": member["github_id"]})
            if mine >= NODES_PER_MEMBER:
                raise conflict("node_limit",
                               f"A member can have at most {NODES_PER_MEMBER} nodes.")
            doc = {"_id": node_id, "owner_github_id": member["github_id"],
                   "retired_boots": [], "activity_seq": None,
                   "last_human_activity_at": None, "last_human_activity_source": None,
                   **_observation(update, now), **_activity(None, update, now)}
            try:
                await db.network_nodes.insert_one(doc)
                return
            except DuplicateKeyError:
                continue
        _check_order(current, member, update)
        op: dict = {"$set": {**_observation(update, now), **_activity(current, update, now)}}
        if current["boot_id"] != str(update.boot_id):
            op["$push"] = {"retired_boots": {"$each": [current["boot_id"]],
                                             "$slice": -RETIRED_BOOTS_KEPT}}
        result = await db.network_nodes.update_one(
            {"_id": node_id, "boot_id": current["boot_id"], "seq": current["seq"]}, op)
        if result.matched_count == 1:
            return
    raise conflict("busy", "This node is changing too quickly; try again.")


def _check_order(current: dict, member: dict, update: NodeUpdate) -> None:
    if current["owner_github_id"] != member["github_id"]:
        raise forbidden("node_not_yours", "That node belongs to another member.")
    boot = str(update.boot_id)
    if boot in (current.get("retired_boots") or []):
        raise conflict("stale_boot", "A newer run of this node has already reported.")
    if boot == current["boot_id"] and update.seq <= current["seq"]:
        raise conflict("stale_sequence", "A newer update from this node is already stored.")


def _observation(update: NodeUpdate, now: datetime) -> dict:
    """The fields every accepted update replaces, with each quota window's
    observation time fixed from its age at receipt."""
    return {"boot_id": str(update.boot_id), "seq": update.seq,
            "received_at": now, "purge_at": now + NODE_PURGE_AFTER,
            "label": update.label, "jarvis_version": update.jarvis_version,
            "os": update.os, "resources": update.resources.model_dump(),
            "brains": [{"account_ref": b.account_ref, "provider": b.provider,
                        "role": b.role, "state": b.state,
                        "windows": [{"window": w.window,
                                     "used_percent": w.used_percent,
                                     "expected_used_percent": w.expected_used_percent,
                                     "resets_at": w.resets_at,
                                     "observed_at": now - timedelta(milliseconds=w.age_ms)}
                                    for w in b.windows]}
                       for b in update.brains]}


def _activity(current: Optional[dict], update: NodeUpdate, now: datetime) -> dict:
    """The activity fields to change: none for a repeated `activity_seq`; the
    time (receipt minus age) only when it is later than the one stored."""
    activity = update.activity
    if activity is None:
        return {}
    seen = (current or {}).get("activity_seq")
    if seen is not None and activity.activity_seq <= seen:
        return {}
    changes: dict = {"activity_seq": activity.activity_seq}
    at = now - timedelta(milliseconds=activity.age_ms)
    previous = (current or {}).get("last_human_activity_at")
    if previous is None or at > previous:
        changes.update(last_human_activity_at=at, last_human_activity_source=activity.source)
    return changes


async def next_revision(db: AsyncDatabase) -> int:
    doc = await db.network_meta.find_one_and_update(
        {"_id": META_ID}, {"$inc": {"revision": 1}}, upsert=True,
        return_document=ReturnDocument.AFTER)
    return doc["revision"]


async def current_nodes(db: AsyncDatabase, team: dict) -> list[dict]:
    """The nodes of active members; a suspended or departed member's nodes
    are never published."""
    active = [m["github_id"] for m in team["members"].values() if m["state"] == "active"]
    return await db.network_nodes.find({"owner_github_id": {"$in": active}}).to_list(None)


# -- the snapshot ----------------------------------------------------------------

def snapshot(docs: list[dict], team: dict, you, revision: int, now: datetime,
             server: ServerHealth) -> NetworkSnapshot:
    """`docs` are `current_nodes` of the same `team`."""
    members = team["members"]
    nodes = [node_view(d, members[team_mod.key(d["owner_github_id"])], now) for d in docs]
    nodes.sort(key=lambda n: (n.owner_login.lower(), n.label.lower(), str(n.node_id)))
    return NetworkSnapshot(revision=revision, server_time=now, you_node_id=you,
                           nodes=nodes, server=server)


def presence(last_seen: datetime, now: datetime) -> str:
    age = now - last_seen
    if age <= ONLINE_WITHIN:
        return "online"
    if age <= DELAYED_WITHIN:
        return "delayed"
    return "offline"


def node_view(doc: dict, member: dict, now: datetime) -> NodeView:
    """One node. `resources.age_ms` is the reading's age when the node sent
    it (at `last_seen_at`); quota ages are worked out to `now`."""
    at = doc.get("last_human_activity_at")
    return NodeView(node_id=doc["_id"], owner_github_id=doc["owner_github_id"],
                    owner_login=member["login"], label=doc["label"],
                    jarvis_version=doc["jarvis_version"], os=doc["os"],
                    presence=presence(doc["received_at"], now),
                    last_seen_at=doc["received_at"],
                    resources=Resources(**doc["resources"]),
                    brains=brain_summary(doc["brains"], now),
                    activity_state="available" if at else "never_observed",
                    last_human_activity_at=at,
                    last_human_activity_source=doc.get("last_human_activity_source") if at else None)


def brain_summary(accounts: list[dict], now: datetime) -> BrainSummary:
    """Each distinct account once, and the plain average of the measured
    ones' weekly remaining and pace. Unmeasured accounts count toward
    `configured` only, so one of two measured reads as 1/2."""
    paces = [account_pace(a, now) for a in _distinct(accounts)]
    measured = [p for p in paces if p.remaining_percent is not None]
    return BrainSummary(
        configured=len(paces), measured=len(measured),
        average_remaining_percent=_mean([p.remaining_percent for p in measured]),
        pace_points=_mean([p.pace_points for p in measured if p.pace_points is not None]),
        accounts=paces)


def _distinct(accounts: list[dict]) -> list[dict]:
    """One entry per provider account: sessions sharing a quota count once,
    and a measured report of it is preferred."""
    chosen: dict[tuple, dict] = {}
    for a in accounts:
        k = (a["provider"], a["account_ref"])
        if k not in chosen or (chosen[k]["state"] != "measured" and a["state"] == "measured"):
            chosen[k] = a
    return list(chosen.values())


def account_pace(account: dict, now: datetime) -> AccountPace:
    """The seven-day window of a measured account, as of its observation:
    remaining, pace (expected minus used, the Brain tile's figure) and where
    the current rate ends the week. A window past its reset describes a week
    that is over, so it counts as unmeasured."""
    week = None
    if account["state"] == "measured":
        week = next((w for w in account["windows"] if w["window"] == "seven_day"), None)
    ident = {k: account[k] for k in ("account_ref", "provider", "role")}
    if week is None or week["resets_at"] <= now:
        return AccountPace(**ident, remaining_percent=None, pace_points=None,
                           resets_at=None, left_at_reset_percent=None,
                           runs_out_hours_before_reset=None, age_ms=None)
    used, expected = week["used_percent"], week["expected_used_percent"]
    left, runs_out = end_of_week(used, week["observed_at"], week["resets_at"])
    return AccountPace(**ident, remaining_percent=round(100 - used, 1),
                       pace_points=None if expected is None else round(expected - used, 1),
                       resets_at=week["resets_at"], left_at_reset_percent=left,
                       runs_out_hours_before_reset=runs_out,
                       age_ms=max(0, int((now - week["observed_at"]).total_seconds() * 1000)))


def end_of_week(used: float, observed_at: datetime,
                resets_at: datetime) -> tuple[Optional[float], Optional[float]]:
    """(percent left at reset, None) when the week's rate so far lasts until
    the reset, else (None, hours before the reset it runs out). The week
    started WEEK_HOURS before its reset."""
    to_reset = (resets_at - observed_at).total_seconds() / 3600
    elapsed = WEEK_HOURS - to_reset
    if elapsed <= 0 or to_reset < 0:
        return None, None
    if used <= 0:
        return 100.0, None
    rate = used / elapsed
    at_reset = used + rate * to_reset
    if at_reset <= 100:
        return round(100 - at_reset, 1), None
    return None, round(to_reset - (100 - used) / rate, 1)


def _mean(values: list[float]) -> Optional[float]:
    return round(sum(values) / len(values), 1) if values else None


# -- the server's own health ---------------------------------------------------

class HealthMonitor:
    """What the team server knows of its database and itself. One probe at a
    time serves every caller; node writes report their own outcome."""

    def __init__(self, sampler: Optional["ResourceSampler"] = None):
        self.sampler = sampler or ResourceSampler()
        self._started = time.monotonic()
        self._lock = asyncio.Lock()
        self._checked_mono: Optional[float] = None
        self.database = "unreachable"
        self.ping_ms: Optional[float] = None
        self.checked_at = clock.now()
        self.last_write_ok_at: Optional[datetime] = None
        self.last_error: Optional[str] = None
        self.failures = 0
        self.history: deque[HealthPoint] = deque(maxlen=HISTORY_POINTS)
        self._writes: Optional[bool] = None
        self._point_due: Optional[datetime] = None

    async def connected(self, db: AsyncDatabase) -> bool:
        """The database's state, probing first when the last probe is older
        than PROBE_INTERVAL_SEC. Concurrent callers share one probe."""
        if self._stale():
            async with self._lock:
                if self._stale():
                    await self._probe(db)
        return self.database == "connected"

    def _stale(self) -> bool:
        return (self._checked_mono is None
                or time.monotonic() - self._checked_mono >= PROBE_INTERVAL_SEC)

    async def _probe(self, db: AsyncDatabase) -> None:
        started = time.monotonic()
        try:
            await asyncio.wait_for(db.command("ping"), timeout=PROBE_TIMEOUT_SEC)
        except (PyMongoError, asyncio.TimeoutError, OSError) as e:
            self.failed(e)
            self.ping_ms = None
        else:
            self.ping_ms = round((time.monotonic() - started) * 1000, 1)
            self.database = "connected"
            self.failures = 0
        self.checked_at = clock.now()
        self._checked_mono = time.monotonic()

    def wrote(self, now: datetime) -> None:
        self.last_write_ok_at = now
        self.database = "connected"
        self.failures = 0
        if self._writes is None:
            self._writes = True

    def failed(self, exc: BaseException, write: bool = False) -> None:
        self.last_error = type(exc).__name__
        self.database = "unreachable"
        self.failures += 1
        if write:
            self._writes = False

    @contextmanager
    def watching(self, write: bool = False):
        """Record a database failure inside the block and raise DatabaseDown."""
        try:
            yield
        except PyMongoError as e:
            log.error("network database error: %s", type(e).__name__)
            self.failed(e, write=write)
            raise DatabaseDown() from e

    async def tick(self, db: AsyncDatabase) -> None:
        """One beat of the watch loop: probe, sample, and every
        HISTORY_EVERY add a history point."""
        await self.connected(db)
        self.sampler.sample()
        now = clock.now()
        if self._point_due is None:
            self._point_due = now + HISTORY_EVERY
        elif now >= self._point_due:
            self.history.append(HealthPoint(at=now, database_ping_ms=self.ping_ms,
                                            write_ok=self._writes))
            self._writes = None
            self._point_due = now + HISTORY_EVERY

    def report(self) -> ServerHealth:
        return ServerHealth(database=self.database, database_ping_ms=self.ping_ms,
                            database_checked_at=self.checked_at,
                            last_write_ok_at=self.last_write_ok_at,
                            last_database_error=self.last_error,
                            database_failures_in_a_row=self.failures,
                            uptime_sec=int(time.monotonic() - self._started),
                            version=build_info.VERSION, commit=build_info.commit(),
                            resources=self.sampler.resources(),
                            history=list(self.history))


async def watch(health: HealthMonitor, db: AsyncDatabase) -> None:
    """The health loop, for the life of the process."""
    while True:
        try:
            await health.tick(db)
        except Exception as e:  # the loop outlives any one beat
            log.error("health tick failed: %s", type(e).__name__)
        await asyncio.sleep(PROBE_INTERVAL_SEC)


# -- CPU and memory ------------------------------------------------------------

class ResourceSampler:
    """CPU busy and memory used of the container this server runs in when
    cgroup v2 sets it a CPU or a memory limit, else of the Linux host;
    unsupported where neither can be read. CPU is unknown until the second
    sample. The same rule as JARVIS's own reader (jarvis/host_resources.py
    LinuxReader): either limit makes the scope the container, a container
    with no memory limit has the host's memory as its total, and used memory
    is clamped to the total."""

    def __init__(self, root: Path = Path("/"),
                 monotonic: Callable[[], float] = time.monotonic):
        self.root = root
        self.monotonic = monotonic
        self._counters: Optional[tuple[str, float, float]] = None
        self._at = monotonic()
        self._reading = Resources(state="warming_up", scope="host", age_ms=0)

    def sample(self) -> None:
        now = self.monotonic()
        try:
            read = self._container() or self._host()
        except (OSError, ValueError, IndexError, KeyError, AttributeError) as e:
            log.warning("resource sample failed: %s", type(e).__name__)
            self._reading, self._counters = Resources(state="error", scope="host", age_ms=0), None
            self._at = now
            return
        if read is None:
            self._reading, self._at = Resources(state="unsupported", scope="host", age_ms=0), now
            return
        scope, busy, capacity, used, total = read
        cpu = None
        if self._counters and self._counters[0] == scope and capacity > self._counters[2]:
            cpu = (busy - self._counters[1]) / (capacity - self._counters[2]) * 100
            cpu = round(min(100.0, max(0.0, cpu)), 1)
        self._counters = (scope, busy, capacity)
        self._reading = Resources(state="measured" if cpu is not None else "warming_up",
                                  scope=scope, cpu_percent=cpu,
                                  memory_used_bytes=max(0, used), memory_total_bytes=total,
                                  age_ms=0)
        self._at = now

    def resources(self) -> Resources:
        age = int((self.monotonic() - self._at) * 1000)
        return self._reading.model_copy(update={"age_ms": min(RESOURCES_AGE_MAX_MS, age)})

    def _read(self, *parts: str) -> Optional[str]:
        path = self.root.joinpath(*parts)
        return path.read_text() if path.is_file() else None

    def _limit(self, name: str) -> Optional[list[str]]:
        """A cgroup v2 limit file's fields, or None when there is no limit."""
        text = self._read("sys/fs/cgroup", name)
        fields = text.split() if text else []
        return None if not fields or fields[0] == "max" else fields

    def _container(self):
        """(scope, busy s, capacity s, used, total) from cgroup v2, or None
        when this process has neither a CPU nor a memory limit there."""
        cpu_limit, memory_limit = self._limit("cpu.max"), self._limit("memory.max")
        if cpu_limit is None and memory_limit is None:
            return None
        total = int(memory_limit[0]) if memory_limit else self._host_memory()[1]
        stat = dict(line.split() for line in self._read("sys/fs/cgroup/memory.stat").splitlines())
        current = int(self._read("sys/fs/cgroup/memory.current"))
        used = min(max(current - int(stat.get("inactive_file", 0)), 0), total)
        cpu = dict(line.split() for line in self._read("sys/fs/cgroup/cpu.stat").splitlines())
        busy = int(cpu["usage_usec"]) / 1e6
        cpus = (int(cpu_limit[0]) / int(cpu_limit[1]) if cpu_limit
                else os.cpu_count() or 1)
        return "container", busy, self.monotonic() * cpus, used, total

    def _host_memory(self) -> tuple[int, int]:
        """(used, total) bytes from /proc/meminfo: total less available."""
        mem = {line.split(":")[0]: int(line.split()[1]) * 1024
               for line in self._read("proc/meminfo").splitlines() if line.strip()}
        total = mem["MemTotal"]
        return total - mem["MemAvailable"], total

    def _host(self):
        """(scope, busy jiffies, total jiffies, used, total) from /proc, or
        None where there is no /proc."""
        stat = self._read("proc/stat")
        if stat is None or self._read("proc/meminfo") is None:
            return None
        fields = [int(v) for v in stat.splitlines()[0].split()[1:9]]
        idle = fields[3] + fields[4]
        used, total = self._host_memory()
        return "host", sum(fields) - idle, sum(fields), used, total
