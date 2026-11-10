"""The Work view, held: every item worked out once and kept up to date one
item at a time, so the Work tab never asks the server to work out the list.

Built at startup (`load`). After that each item is worked out again only
when something it shows has changed, and only that item:

  - a write to its record (`work_store.create`, `record`, or a wait his
    answer ended: `work_store.on_changed`) reads that item's rows again
    (`refresh`);
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
from typing import Callable, Optional

from jarvis import data_paths, package_inbox, work_api, work_store

log = logging.getLogger(__name__)

FRAME_ITEM = "work_item"
FRAME_HISTORY = "work_item_history"
FRAME_REMOVED = "work_item_removed"
ITEMS, HISTORY = "items", "history"

# What of a live owner is a change worth a frame: who it is, its state, what
# it is doing now and when it last did something (which moves its quiet_at).
LIVE_KEY = ("name", "state", "doing", "written")
# The parts of a live owner that move on every look and say nothing new.
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
    return None if agent is None else tuple(getattr(agent, f) for f in LIVE_KEY)


def _held_live(item: work_store.WorkItem) -> Live:
    """Each piece owner's live state as the item last showed it."""
    held = {_who_key(p.who): p.live for p in item.pieces}
    return lambda person: held.get(_who_key(person))


def _settled(item_json: dict) -> dict:
    """The JSON without the parts of a live owner that move on every look."""
    def bare(live):
        return live and {k: v for k, v in live.items() if k not in _VOLATILE_LIVE}
    return {**item_json, "live": bare(item_json.get("live")),
            "pieces": [{**p, "live": bare(p.get("live"))} for p in item_json.get("pieces", [])]}


class WorkView:
    """The held view. Every method may be called from any thread; frames go
    out through `publish` in revision order, under the view's lock."""

    def __init__(self, publish: Callable[[dict], None],
                 live: Optional[Live] = None) -> None:
        self._publish = publish
        self._live = live or work_store.live_agent
        self._lock = threading.RLock()
        self._db: Optional[str] = None
        self.revision = 0
        self._records: dict[str, work_store.ItemRecord] = {}
        self._items: dict[str, work_store.WorkItem] = {}
        self._json: dict[str, dict] = {}
        self._due: dict[str, Optional[float]] = {}
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

    # ── building ────────────────────────────────────────────────────────

    def load(self) -> None:
        """Work out every item once (at startup, or for another database)."""
        with self._lock:
            records = work_store.records()
            self._db = str(data_paths.db_path())
            self._records, self._items, self._json, self._due = records, {}, {}, {}
            self._projects, self._agents, self._names = {}, None, {}
            self._waiting = package_inbox.waiting_by_work()
            now = time.time()
            for item_id, rec in records.items():
                self._json[item_id] = self._compose(rec, now, self._live)
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

    def _compose(self, rec: work_store.ItemRecord, now: float, live: Live) -> dict:
        """Work the item out from its record and keep it; its JSON."""
        item = work_store.compose(rec, now, live)
        if rec.id not in self._projects:
            self._projects[rec.id] = work_api.projects_for_path(item.source_path)
        names = work_api.WhoNames(live=live, agents=self._agent_names, records=self._names)
        out = work_api.item_json(item, names, self._waiting, self._projects[rec.id])
        self._records[rec.id], self._items[rec.id] = rec, item
        self._due[rec.id] = work_store.next_change(rec, now)
        return out

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

    def _commit(self, worked: dict[str, dict]) -> None:
        """Keep the items just worked out (id -> JSON) whose JSON changed,
        place every item again, and send a frame for each item that changed
        or moved. Under the lock."""
        changed = {i for i, out in worked.items()
                   if i not in self._json or _settled(out) != _settled(self._json[i])}
        for item_id in changed:
            self._json[item_id] = worked[item_id]
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

    def _recompose(self, item_ids, now: float, live_of: Callable[[str], Live]) -> dict:
        return {i: self._compose(self._records[i], now, live_of(i))
                for i in item_ids if i in self._records}

    def refresh(self, item_ids) -> None:
        """These items' records changed: read them again and send what moved.
        A view not yet built for this database builds itself on first use
        instead, which no page has seen yet."""
        with self._lock:
            if not self._loaded():
                return
            records = work_store.records(set(item_ids))
            now = time.time()
            self._commit({i: self._compose(rec, now, self._live) for i, rec in records.items()})

    def tick(self, now: Optional[float] = None) -> None:
        """Once a second, from the server's loop, never on a request: the
        items whose live owners changed, whose clock threshold has passed,
        and (every SOURCE_EVERY_SEC) whose source's stat changed. Composes
        from the held records; reads no item's rows."""
        now = time.time() if now is None else now
        with self._lock:
            if not self._loaded():
                return
            if now - self._swept_at >= SWEEP_EVERY_SEC:
                self._swept_at = now
                from jarvis import wait_links
                wait_links.sweep()          # tells work_store.on_changed what it moved
            fresh: dict[str, Live] = {}
            for item_id, item in self._items.items():
                if item.closed_at is not None or not item.pieces:
                    continue
                seen = {_who_key(p.who): self._live(p.who) for p in item.pieces}
                if any(_live_key(seen[_who_key(p.who)]) != _live_key(p.live) for p in item.pieces):
                    fresh[item_id] = (lambda s: lambda person: s.get(_who_key(person)))(seen)
            due = {i for i, at in self._due.items() if at is not None and at <= now}
            if now - self._sources_at >= SOURCE_EVERY_SEC:
                self._sources_at = now
                for item_id, rec in list(self._records.items()):
                    again = rec.with_source()
                    if again.source != rec.source:
                        self._records[item_id] = again
                        due.add(item_id)
            self._commit(self._recompose(
                set(fresh) | due, now, lambda i: fresh.get(i) or _held_live(self._items[i])))

    def _recompose_all(self) -> None:
        """Every item composed again from its record with its owners' live
        state as it last showed it: for a change of what names or places
        them. Reads no item's rows. Under the lock."""
        now = time.time()
        self._commit(self._recompose(list(self._records), now,
                                     lambda i: _held_live(self._items[i])))

    def packages_changed(self, item_ids=()) -> None:
        """The package inbox changed: how many questions wait on him for each
        item is read again, and every item whose count moved, and each of
        `item_ids` (the items the changed packages were sent for), with it."""
        with self._lock:
            if not self._loaded():
                return
            waiting = package_inbox.waiting_by_work()
            moved = {i for i in set(waiting) | set(self._waiting)
                     if waiting.get(i, 0) != self._waiting.get(i, 0)}
            self._waiting = waiting
            self.refresh(moved | set(item_ids))

    def projects_changed(self) -> None:
        """A project record changed: each item's projects are worked out again."""
        with self._lock:
            if not self._loaded():
                return
            self._projects = {}
            self._recompose_all()

    def names_changed(self) -> None:
        """The agents list changed (one came, went or was renamed): the
        agent directory is read again, and only the items with a piece on an
        agent whose name moved are composed again."""
        with self._lock:
            if not self._loaded() or self._agents is None:
                return
            from jarvis import agent_directory
            before, after = self._agents, agent_directory.names_by_session()
            self._agents = after
            moved = {ref for ref in set(before) | set(after) if before.get(ref) != after.get(ref)}
            ids = [i for i, item in self._items.items()
                   if any(p.who.kind == work_store.AGENT and p.who.ref in moved
                          for p in item.pieces)]
            if ids:
                self._commit(self._recompose(ids, time.time(),
                                             lambda i: _held_live(self._items[i])))

    # ── reading ─────────────────────────────────────────────────────────

    def _caught_up(self) -> None:
        """Built for the database in use, and every item whose clock
        threshold has passed composed again (with its owners' live state as
        held; the tick does this each second, so a read seldom finds one).
        Reads nothing. Under the lock."""
        self._ensure()
        now = time.time()
        due = [i for i, at in self._due.items() if at is not None and at <= now]
        if due:
            self._commit(self._recompose(due, now, lambda i: _held_live(self._items[i])))

    def body(self) -> bytes:
        """/api/work's answer: the open items, their groups, History and the
        revision, serialised once per change."""
        with self._lock:
            self._caught_up()
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
        that JSON is as of."""
        with self._lock:
            self._caught_up()
            return self._json.get(item_id), self.revision
