"""The Work view, held: every item worked out once and kept up to date one
item at a time, so the Work tab never asks the server to work out the list.

Built at startup (`load`). After that each item is worked out again only
when something it shows has changed, and only that item:

  - a write to its record (`work_store.create`, `record`, or a wait his
    answer ended: `work_store.on_changed`) hands the item's id to the view
    (`refresh`), which reads that item's rows again on a thread of its own
    (`start`, `drain`), never on the writer's: a report's write never waits
    on the view;
  - a piece owner's live state (an agent or subagent starting, stopping or
    changing what it is doing, `LIVE_KEY`) changes, as the session watcher's
    poll shows it (`tick`, every second, never on a request): that item is
    composed again from the record it holds, with no read;
  - the clock passing a threshold the record's read-time rules measure
    (`work_store.next_change`: quiet, stalled, unlinked, overdue, into
    History) composes it again from the record it holds;
  - its source's stat changing (`tick`, every SOURCE_EVERY_SEC);
  - the packages sent for it, the projects holding its source, or the names
    of the agents on it changing (`packages_changed`, `projects_changed`,
    `names_changed`).

The view's lock is never held while anything is read or worked out: each
of these reads and composes outside it and takes it only to swap the
results in (`_commit`). Every composition is stamped with the record's
revision and the order it began in (`_Worked.stamp`), and one is kept only
when it is newer than the one held, so a newer composition is never
overwritten by an older one that finished after it.

Each item whose JSON changed goes to every page once, as one frame on
/ws/sessions with the view's revision, which moves by one per frame:

  work_item          the item's whole JSON (exactly what /api/work lists),
                     the list it is in ("items", "history", or None when it
                     is past the listing's LIST_MAX), the order of both lists
                     and the groups;
  work_item_history  an item that has moved to History, the same fields;
  work_item_removed  an item that has left the view (past LIST_MAX), by id.

`/api/work` returns the held view with its revision, serialised once per
change (`body`), so it reads nothing: a page reads it on load and on
reconnect, and again when a frame's revision is not the next one.

An item's JSON is `work_api.item_json` of `work_store.compose`, the same
calls a fresh read makes, so the page and the brain's work_status say the
same facts. A live owner's evidence clock (its activity's age, the snapshot
it came from) is not a change by itself (`_settled`).
"""
from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional

from jarvis import data_paths, package_inbox, work_api, work_store

log = logging.getLogger(__name__)

FRAME_ITEM = "work_item"
FRAME_HISTORY = "work_item_history"
FRAME_REMOVED = "work_item_removed"
ITEMS, HISTORY = "items", "history"

# What of a live owner is a change worth a frame: who it is, its state, what
# it is doing now and, while it is working, when it last did something (which
# moves the piece's quiet_at). An idle or finished owner's observed time moves
# on every look and changes nothing shown.
LIVE_KEY = ("name", "state", "doing")
# The parts of a live owner that move on every look and say nothing new; its
# `written` too, unless it is working.
_VOLATILE_LIVE = ("activity", "snapshot_id")
# How often each source is looked at again (a stat each), in ticks of a second.
SOURCE_EVERY_SEC = 30.0
# How often a wait already answered elsewhere is looked for (wait_links.sweep),
# which every read of the list used to do.
SWEEP_EVERY_SEC = 10.0

Live = Callable[[work_store.Who], Optional[work_store.LiveAgent]]


def _who_key(person: work_store.Who) -> tuple:
    return person.kind, person.ref


def _live_key(agent: Optional[work_store.LiveAgent]) -> Optional[tuple]:
    if agent is None:
        return None
    working = agent.state == work_store.LIVE_WORKING
    return (*(getattr(agent, f) for f in LIVE_KEY), agent.written if working else None)


def _held_live(item: work_store.WorkItem) -> Live:
    """Each piece owner's live state as the item last showed it."""
    held = {_who_key(p.who): p.live for p in item.pieces}
    return lambda person: held.get(_who_key(person))


def _settled(item_json: dict) -> dict:
    """The JSON without the parts of a live owner that move on every look."""
    def bare(live):
        if not live:
            return live
        volatile = _VOLATILE_LIVE + (() if live.get("state") == work_store.LIVE_WORKING
                                     else ("written",))
        return {k: v for k, v in live.items() if k not in volatile}
    return {**item_json, "live": bare(item_json.get("live")),
            "pieces": [{**p, "live": bare(p.get("live"))} for p in item_json.get("pieces", [])]}


@dataclass(frozen=True)
class _Inputs:
    """What a composition reads besides the record, as the view held it when
    the composition began, and the order it began in."""
    seq: int
    db: Optional[str]
    waiting: dict
    projects: dict


@dataclass(frozen=True)
class _Worked:
    """One item worked out, outside the lock, to be swapped in by `_commit`."""
    rec: work_store.ItemRecord
    item: work_store.WorkItem
    json: dict
    due: Optional[float]
    projects: list
    # (the record's revision, the order the composition began in): a newer
    # record wins, and of two from the same record the later begun.
    stamp: tuple[int, int]


class WorkView:
    """The held view. Every method may be called from any thread; frames go
    out through `publish` in revision order, under the view's lock, which is
    held only to swap results in, never while anything is read or composed."""

    def __init__(self, publish: Callable[[dict], None],
                 live: Optional[Live] = None) -> None:
        self._publish = publish
        self._live = live or work_store.live_agent
        self._lock = threading.RLock()
        self._db: Optional[str] = None
        self.revision = 0
        self._seq = 0
        self._records: dict[str, work_store.ItemRecord] = {}
        self._items: dict[str, work_store.WorkItem] = {}
        self._json: dict[str, dict] = {}
        self._due: dict[str, Optional[float]] = {}
        self._stamps: dict[str, tuple[int, int]] = {}
        self._projects: dict[str, list] = {}
        self._waiting: dict[str, int] = {}
        self._agents: Optional[dict[str, str]] = None
        self._names: dict[tuple, Optional[str]] = {}
        self._where: dict[str, str] = {}
        self._order: dict[str, list[str]] = {ITEMS: [], HISTORY: []}
        self._groups: list[dict] = []
        self._body: Optional[bytes] = None
        self._sources_at = 0.0
        self._swept_at = 0.0
        # The items written since they were last worked out, drained off the
        # writers' threads (`refresh`, `drain`).
        self._pending: set[str] = set()
        self._pending_lock = threading.Lock()
        self._wake: Optional[threading.Event] = None
        self._drainer: Optional[threading.Thread] = None

    # ── building ────────────────────────────────────────────────────────

    def _begin(self) -> _Inputs:
        """A composition's order and inputs, taken under the lock."""
        with self._lock:
            self._seq += 1
            return _Inputs(self._seq, self._db, self._waiting, dict(self._projects))

    def load(self) -> None:
        """Work out every item once (at startup, or for another database).
        Reads and composes outside the lock; an item a write has moved on
        since this began keeps the newer composition."""
        with self._pending_lock:
            self._pending.clear()          # every record is read below
        with self._lock:
            self._seq += 1
            seq = self._seq
        db = str(data_paths.db_path())
        self._agents, self._names = None, {}
        records = work_store.records()
        waiting = package_inbox.waiting_by_work()
        inputs = _Inputs(seq, db, waiting, {})
        now = time.time()
        worked = {i: self._work_out(rec, now, self._live, inputs) for i, rec in records.items()}
        with self._lock:
            same = self._db == db
            for item_id, w in list(worked.items()):
                held = self._stamps.get(item_id) if same else None
                if held is not None and held > w.stamp:
                    worked[item_id] = _Worked(self._records[item_id], self._items[item_id],
                                              self._json[item_id], self._due[item_id],
                                              self._projects.get(item_id, []), held)
            self._db, self._waiting = db, waiting
            self._records = {i: w.rec for i, w in worked.items()}
            self._items = {i: w.item for i, w in worked.items()}
            self._json = {i: w.json for i, w in worked.items()}
            self._due = {i: w.due for i, w in worked.items()}
            self._projects = {i: w.projects for i, w in worked.items()}
            self._stamps = {i: w.stamp for i, w in worked.items()}
            self._where, self._order, self._groups = self._placed()
            self.revision += 1
            self._body = None

    def _loaded(self) -> bool:
        """Whether the view holds the database in use now."""
        return self._db is not None and self._db == str(data_paths.db_path())

    def _ensure(self) -> None:
        if not self._loaded():
            self.load()

    def _agent_names(self) -> dict[str, str]:
        if self._agents is None:
            from jarvis import agent_directory
            self._agents = agent_directory.names_by_session()
        return self._agents

    def _work_out(self, rec: work_store.ItemRecord, now: float, live: Live,
                  inputs: _Inputs) -> _Worked:
        """Work the item out from its record. Outside the lock: it may read
        (the projects holding its source, the packages sent for it, the
        names on its pieces)."""
        item = work_store.compose(rec, now, live)
        projects = inputs.projects.get(rec.id)
        if projects is None:
            projects = work_api.projects_for_path(item.source_path)
        names = work_api.WhoNames(live=live, agents=self._agent_names, records=self._names)
        out = work_api.item_json(item, names, inputs.waiting, projects)
        return _Worked(rec, item, out, work_store.next_change(rec, now), projects,
                       (rec.row["revision"], inputs.seq))

    def _again(self, records: dict[str, work_store.ItemRecord], now: float,
               live_of: Callable[[str], Live], inputs: _Inputs) -> None:
        """Compose these records outside the lock, then swap them in."""
        self._commit([self._work_out(rec, now, live_of(i), inputs)
                      for i, rec in records.items()], inputs.db)

    def _held_again(self, item_ids, now: Optional[float] = None) -> None:
        """These items composed again from the records held, with their
        owners' live state as they last showed it."""
        inputs = self._begin()
        with self._lock:
            records = {i: self._records[i] for i in item_ids if i in self._records}
            items = {i: self._items[i] for i in records}
        self._again(records, time.time() if now is None else now,
                    lambda i: _held_live(items[i]), inputs)

    def _placed(self):
        """Where each item is listed now, both lists' order and the groups,
        as /api/work has always listed them (work_store.listing, grouped)."""
        items = self._items.values()
        shown = sorted((i for i in items if not i.history),
                       key=lambda i: (i.reported_state == work_store.DONE, -i.updated_at))
        past = sorted((i for i in items if i.history),
                      key=lambda i: -(i.closed_at if i.closed_at is not None else i.updated_at))
        shown, past = shown[:work_store.LIST_MAX], past[:work_store.LIST_MAX]
        where = {**{i.id: ITEMS for i in shown}, **{i.id: HISTORY for i in past}}
        order = {ITEMS: [i.id for i in shown], HISTORY: [i.id for i in past]}
        groups = [work_api.group_json(g) for g in work_store.grouped(shown)]
        return where, order, groups

    # ── changes, and their frames ───────────────────────────────────────

    def _commit(self, worked: list[_Worked], db: Optional[str]) -> None:
        """Swap in the items just worked out that are newer than those held
        (by stamp), place every item again, and send a frame for each item
        whose JSON changed or that moved. The only step under the lock; it
        reads nothing."""
        with self._lock:
            if db is None or db != self._db:
                return                     # worked out for a view since rebuilt
            kept: dict[str, dict] = {}
            for w in worked:
                item_id = w.rec.id
                held = self._stamps.get(item_id)
                if held is not None and w.stamp <= held:
                    continue               # older than what is held
                self._records[item_id], self._items[item_id] = w.rec, w.item
                self._due[item_id], self._stamps[item_id] = w.due, w.stamp
                self._projects[item_id] = w.projects
                kept[item_id] = w.json
            changed = {i for i, out in kept.items()
                       if i not in self._json or _settled(out) != _settled(self._json[i])}
            for item_id in changed:
                self._json[item_id] = kept[item_id]
            where, order, groups = self._placed()
            moved = {i for i in set(where) | set(self._where) if where.get(i) != self._where.get(i)}
            if not (changed or moved):
                return
            self._order, self._groups = order, groups
            before, self._where = self._where, where
            self._body = None
            for item_id in sorted(changed | moved, key=lambda i: (i not in changed, i)):
                now_in, was_in = where.get(item_id), before.get(item_id)
                if now_in is None and was_in is not None:
                    frame = {"type": FRAME_REMOVED, "id": item_id}
                elif now_in is None and item_id not in changed:
                    continue
                else:
                    kind = FRAME_HISTORY if now_in == HISTORY and was_in != HISTORY else FRAME_ITEM
                    frame = {"type": kind, "item": self._json[item_id], "list": now_in}
                self.revision += 1
                self._send({**frame, "revision": self.revision, "order": order, "groups": groups})

    def _send(self, frame: dict) -> None:
        try:
            self._publish(frame)
        except Exception:
            log.warning("work view: a frame was not sent", exc_info=True)

    def refresh(self, item_ids) -> None:
        """These items' records changed: hand them to the view, which reads
        them again and sends what moved. With the view's own thread running
        (`start`, the server's) this only wakes it, so the writer goes on at
        once; without it (a test, a command) the caller drains them now,
        still never under the view's lock while it reads."""
        with self._pending_lock:
            self._pending |= set(item_ids)
            wake = self._wake
        if wake is not None:
            wake.set()
        else:
            self.drain()

    def drain(self) -> None:
        """Read and work out every item handed to the view, until none is
        left. A view not yet built for this database builds itself on first
        use instead, which no page has seen yet."""
        while True:
            with self._pending_lock:
                item_ids, self._pending = self._pending, set()
            if not item_ids or not self._loaded():
                return
            inputs = self._begin()
            records = work_store.records(item_ids)
            self._again(records, time.time(), lambda i: self._live, inputs)

    def start(self) -> None:
        """Drain the items writes hand over on a thread of the view's own
        from now on (the server's, at startup)."""
        with self._pending_lock:
            if self._wake is not None:
                return
            self._wake = wake = threading.Event()
        self._drainer = threading.Thread(target=self._drain_forever, args=(wake,),
                                         name="work-view", daemon=True)
        self._drainer.start()
        wake.set()

    def stop(self) -> None:
        """Stop the view's own thread; writers drain for themselves again."""
        with self._pending_lock:
            wake, self._wake = self._wake, None
            drainer, self._drainer = self._drainer, None
        if wake is not None:
            wake.set()
        if drainer is not None:
            drainer.join(timeout=5)
        self.drain()

    def _drain_forever(self, wake: threading.Event) -> None:
        while True:
            wake.wait()
            wake.clear()
            if self._wake is not wake:
                return
            try:
                self.drain()
            except Exception:
                log.warning("work view: a change was not worked out", exc_info=True)

    def tick(self, now: Optional[float] = None) -> None:
        """Once a second, from the server's loop, never on a request: the
        items whose live owners changed, whose clock threshold has passed,
        and (every SOURCE_EVERY_SEC) whose source's stat changed. Composes
        from the held records; reads no item's rows. Looks and composes
        outside the lock."""
        now = time.time() if now is None else now
        if not self._loaded():
            return
        if now - self._swept_at >= SWEEP_EVERY_SEC:
            self._swept_at = now
            from jarvis import wait_links
            wait_links.sweep()          # tells work_store.on_changed what it moved
        inputs = self._begin()
        with self._lock:
            records, items = dict(self._records), dict(self._items)
            due = {i for i, at in self._due.items() if at is not None and at <= now}
            sources = now - self._sources_at >= SOURCE_EVERY_SEC
            if sources:
                self._sources_at = now
        fresh: dict[str, Live] = {}
        seen: dict[tuple, Optional[work_store.LiveAgent]] = {}   # each owner looked up once
        for item_id, item in items.items():
            if item.closed_at is not None or not item.pieces:
                continue
            for p in item.pieces:
                if _who_key(p.who) not in seen:
                    seen[_who_key(p.who)] = self._live(p.who)
            if any(_live_key(seen[_who_key(p.who)]) != _live_key(p.live) for p in item.pieces):
                fresh[item_id] = lambda person: seen.get(_who_key(person))
        if sources:
            for item_id, rec in records.items():
                again = rec.with_source()
                if again.source != rec.source:
                    records[item_id] = again
                    due.add(item_id)
        self._again({i: records[i] for i in set(fresh) | due if i in records}, now,
                    lambda i: fresh.get(i) or _held_live(items[i]), inputs)

    def packages_changed(self, item_ids=()) -> None:
        """The package inbox changed: how many questions wait on him for each
        item is read again, and every item whose count moved, and each of
        `item_ids` (the items the changed packages were sent for), with it."""
        if not self._loaded():
            return
        waiting = package_inbox.waiting_by_work()
        with self._lock:
            moved = {i for i in set(waiting) | set(self._waiting)
                     if waiting.get(i, 0) != self._waiting.get(i, 0)}
            self._waiting = waiting
        self.refresh(moved | set(item_ids))

    def projects_changed(self) -> None:
        """A project record changed: each item's projects are worked out again."""
        if not self._loaded():
            return
        with self._lock:
            self._projects = {}
            ids = list(self._records)
        self._held_again(ids)

    def names_changed(self) -> None:
        """The agents list changed (one came, went or was renamed): the
        agent directory is read again, and only the items with a piece on an
        agent whose name moved are composed again."""
        if not self._loaded() or self._agents is None:
            return
        from jarvis import agent_directory
        after = agent_directory.names_by_session()
        with self._lock:
            before, self._agents = self._agents or {}, after
            moved = {ref for ref in set(before) | set(after) if before.get(ref) != after.get(ref)}
            ids = [i for i, item in self._items.items()
                   if any(p.who.kind == work_store.AGENT and p.who.ref in moved
                          for p in item.pieces)]
        if ids:
            self._held_again(ids)

    # ── reading ─────────────────────────────────────────────────────────

    def body(self) -> bytes:
        """/api/work's answer: the open items, their groups, History and the
        revision, serialised once per change. Composes nothing: an item
        whose clock threshold has passed is the tick's, within a second."""
        self._ensure()
        with self._lock:
            if self._body is None:
                self._body = json.dumps({
                    ITEMS: [self._json[i] for i in self._order[ITEMS]],
                    "groups": self._groups,
                    HISTORY: [self._json[i] for i in self._order[HISTORY]],
                    "revision": self.revision}).encode()
            return self._body

    def item(self, item_id: str) -> Optional[dict]:
        """One item's JSON as the view holds it, or None."""
        return self.held(item_id)[0]

    def held(self, item_id: str) -> tuple[Optional[dict], int]:
        """One item's JSON as the view holds it (or None), and the revision
        that JSON is as of. That one item, when its clock threshold has
        passed and the tick has not yet composed it again, is composed here."""
        self._ensure()
        with self._lock:
            at = self._due.get(item_id)
        if at is not None and at <= time.time():
            self._held_again([item_id])
        with self._lock:
            return self._json.get(item_id), self.revision

    def shown(self, item: work_store.WorkItem) -> dict:
        """A write's answer: the item as the view holds it when that is at
        least as new as `item` (the record the write left), else `item`
        worked out with what the view holds (the packages' counts, its
        projects, the names it knows), never waiting for the view."""
        with self._lock:
            held = self._json.get(item.id)
            waiting = self._waiting if self._db is not None else None
            projects = self._projects.get(item.id)
        if held is not None and held.get("revision", 0) >= item.revision:
            return held
        names = work_api.WhoNames(live=self._live, agents=self._agent_names, records=self._names)
        return work_api.item_json(item, names, waiting, projects)
