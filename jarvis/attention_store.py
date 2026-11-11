"""attention_store.py — the durable record of background events.

Every routine event JARVIS used to announce on its own (an agent's message
not picked up or answered, a session's message, a needs board, a session that
wants him, a run or a package run ending, a person's message arriving, not
being signed in) is first an attention item here, linked to its source
record by kind and id. Nothing is said by recording one: whether anything is
said, and what, is attention.py's admission policy and its one digest.

The record is three document types in the engine (`records.py`):

    attention  `/attention/<kind>/<source id>.attention`, one per source
               object (kind, source_id): an evolving event (delivered, then
               answered) is the same document's next revision, never a
               second item. `facts` are JARVIS's own (codes, counts, ids);
               `untrusted` are other people's words (names, subjects), only
               ever handed to the brain walled. `audible` says whether this
               node may speak of it (an arrival only once this node holds its
               presentation lease). `state` is what he has done with it
               (pending, seen, resolved); `digested_revision` is the revision
               a digest last finished with, so a revision is offered to a
               digest once. `presentations` are every digest it was shown in,
               at which revision. Each revision is a version of the document
               whose `events` holds that revision's event (its time, revision,
               event, facts and words), so `history` survives the item moving
               on; a version that records none (seen, resolved, presented)
               has `events: []`.
    digest     `/attention/digests/<id>.digest`: each digest attempt and its
               outcome (generating, silent, discarded, failed, queued,
               played, interrupted), a version per change. Played means the
               audio was acknowledged in full; nothing here claims he heard
               or read anything.
    settings   `/settings/attention.settings`: the admission policy's
               tunable trial values, held in memory (read once per engine,
               refreshed by every write through this module), so the policy
               never waits on the engine.

Item and digest ids are integers the page and the brain name them by, given
out from `/attention/ids.counter` in the same write as the record they
number. `announcements` and `digests` are never a kind's folder (another
record's documents and the digests live there).
"""
from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field, fields
from typing import Any, Callable, Iterable, Iterator, Optional

from jarvis import brain_activity, data_paths, records
from jarvis import knowledge_move as move
from jarvis.knowledge_move import cell as _cell, uncell as _uncell

PENDING, SEEN, RESOLVED = "pending", "seen", "resolved"
STATES = (PENDING, SEEN, RESOLVED)

# A digest's outcomes, in the order they can happen.
GENERATING, SILENT, DISCARDED, FAILED, QUEUED, PLAYED, INTERRUPTED = (
    "generating", "silent", "discarded", "failed", "queued", "played", "interrupted")
DIGEST_OUTCOMES = (GENERATING, SILENT, DISCARDED, FAILED, QUEUED, PLAYED, INTERRUPTED)
# The outcomes after which the items a digest was built from are done with.
FINISHED_WITH = (SILENT, PLAYED)

# What a page or the brain is shown at most at once.
LIST_MAX = 200

ROOT = "/attention"
DIGESTS = ROOT + "/digests"
COUNTER_PATH = ROOT + "/ids.counter"
ORPHANS_PATH = ROOT + "/orphans.orphans"
SETTINGS_PATH = "/settings/attention.settings"
ITEM_TYPE, DIGEST_TYPE = "attention", "digest"
# Folders under /attention that are never a kind's: another record's
# documents (messaging's announcements) and the digests.
RESERVED_FOLDERS = frozenset({"announcements", "digests"})
# Every attention item holds this field and no other document under
# /attention does: how the item queries tell them apart.
_IS_ITEM = ["digested_revision", "exists", True]
WRITE_BATCH = 500


def _folder(kind: str) -> str:
    seg = records.segment(kind)
    if seg in RESERVED_FOLDERS:
        # Its first letter percent-encoded: a spelling `segment` never
        # makes, so it is no other kind's folder either.
        seg = "%%%02X" % ord(seg[0]) + seg[1:]
    return seg


def item_path(kind: str, source_id: Any) -> str:
    return f"{ROOT}/{_folder(str(kind))}/{records.segment(str(source_id))}.{ITEM_TYPE}"


def digest_path(digest_id: int) -> str:
    return f"{DIGESTS}/{records.segment(digest_id)}.{DIGEST_TYPE}"


def _is_item_path(path: str) -> bool:
    if not path.startswith(ROOT + "/") or not path.endswith("." + ITEM_TYPE):
        return False
    rest = path[len(ROOT) + 1:]
    return rest.count("/") == 1 and rest.split("/", 1)[0] not in RESERVED_FOLDERS


def _text(value) -> str:
    return json.dumps(value or {}, sort_keys=True, ensure_ascii=False)


def _json_cell(name: str, value: Any) -> dict:
    """A JSON text column as a document field: its value when the text is
    exactly that value as this module writes it, else the text itself as
    `<name>_text` (nothing is lost either way)."""
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except ValueError:
            parsed = None
        else:
            if json.dumps(parsed, sort_keys=True, ensure_ascii=False) == value:
                return {name: parsed}
    return {name + "_text": _cell(value)}


def _json_text(data: dict, name: str) -> Any:
    """The column's text again (see `_json_cell`)."""
    if name in data:
        return json.dumps(data[name], sort_keys=True, ensure_ascii=False)
    return _uncell(data.get(name + "_text"))


def _json_value(data: dict, name: str) -> dict:
    if name in data:
        return data[name] if isinstance(data[name], dict) else {}
    try:
        value = json.loads(_json_text(data, name))
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


class _Held:
    """A value held in memory per engine: read once, replaced by the
    writes through this module, read again only when the engine bound
    changes (a test's own engine, a restarted one)."""

    def __init__(self, load: Callable[[], Any]) -> None:
        self._load = load
        self._lock = threading.Lock()
        self._engine: Any = None
        self._value: Any = None
        self._have = False

    def get(self) -> Any:
        engine = records.uds()
        with self._lock:
            if self._have and self._engine is engine:
                return self._value
        try:
            value = self._load()
        except records.RecordsUnavailable:
            with self._lock:
                if self._have:
                    return self._value      # the last held, while the engine is away
            raise
        return self.set(value, engine)

    def set(self, value: Any, engine: Any = None) -> Any:
        with self._lock:
            self._engine = records.uds() if engine is None else engine
            self._value, self._have = value, True
        return value


# ── settings ──────────────────────────────────────────────────────────────

class Refused(ValueError):
    """A settings change that is not one: the value and why, said plainly."""


@dataclass(frozen=True)
class Settings:
    """The admission policy's trial values. Tuning candidates from the
    notifications proposal, not claims about a person's presence."""
    oldest_event_sec: float = 60.0      # the oldest eligible item is at least this old
    digest_cooldown_sec: float = 120.0  # since the last digest attempt, whatever its outcome
    settled_exchange_sec: float = 10.0  # since the last exchange settled and his last activity
    engagement_sec: float = 120.0       # his last activity within this is "engaged"
    away: bool = False                  # he said he is away: nothing routine is spoken


# (lowest, highest) for each number.
BOUNDS = {
    "oldest_event_sec": (0.0, 3600.0),
    "digest_cooldown_sec": (10.0, 3600.0),
    "settled_exchange_sec": (0.0, 600.0),
    "engagement_sec": (10.0, 3600.0),
}

_SETTING_NAMES = frozenset(f.name for f in fields(Settings))


def validate(changes: dict) -> dict:
    """The changes as typed values, or Refused naming the first wrong one.
    One check for the page's route and the brain's tool alike."""
    if not isinstance(changes, dict) or not changes:
        raise Refused("Nothing to change.")
    out: dict = {}
    for key, value in changes.items():
        if key not in _SETTING_NAMES:
            raise Refused(f"There is no attention setting called {key!r}.")
        if key == "away":
            if not isinstance(value, bool):
                raise Refused("away is true or false.")
            out[key] = value
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise Refused(f"{key} is a number of seconds.")
        low, high = BOUNDS[key]
        if not low <= float(value) <= high:
            raise Refused(f"{key} is between {low:g} and {high:g} seconds.")
        out[key] = float(value)
    return out


def _settings_document(rows: Iterable[tuple[Any, Any, Optional[int]]]) -> dict:
    """The settings document from (key, value text, rowid) rows: each known
    setting whose text is its value as written here under `values`, any
    other row kept as it was under `other`. `rowids` keeps the row number
    each setting had in SQLite (for the way back; none for one saved since)."""
    values: dict = {}
    rowids: dict = {}
    other: list = []
    for key, text, rowid in rows:
        if isinstance(key, str) and key in _SETTING_NAMES and isinstance(text, str):
            try:
                parsed = json.loads(text)
            except ValueError:
                parsed = None
            else:
                if json.dumps(parsed) == text:
                    values[key] = parsed
                    if rowid is not None:
                        rowids[key] = rowid
                    continue
        other.append({"key": _cell(key), "value": _cell(text), "rowid": rowid})
    return {"values": values, "rowids": rowids, "other": other}


def _settings_rows(data: dict) -> list[tuple[Any, Any, Optional[int]]]:
    """(key, value text, rowid) of every saved setting (the rows they were)."""
    rowids = data.get("rowids") or {}
    rows = [(k, json.dumps(v), rowids.get(k)) for k, v in (data.get("values") or {}).items()]
    rows += [(_uncell(o["key"]), _uncell(o["value"]), o.get("rowid"))
             for o in data.get("other") or ()]
    return rows


def _load_settings() -> Settings:
    rec = records.get(SETTINGS_PATH)
    saved: dict = {}
    for key, text, _ in _settings_rows({} if rec is None else rec.data):
        try:
            saved.update(validate({key: json.loads(text)}))
        except (Refused, ValueError, TypeError):
            continue
    return Settings(**saved)


_settings = _Held(_load_settings)


def settings() -> Settings:
    """The saved settings, each missing or unreadable value its default.
    Held in memory: the engine is read once, and again only after another
    engine is bound."""
    return _settings.get()


def change_settings(changes: dict) -> Settings:
    """Validate and save `changes`; the settings as saved now."""
    typed = validate(changes)

    def change(data: Optional[dict]) -> dict:
        rows = {key: [text, rowid] for key, text, rowid in _settings_rows(data or {})}
        for key, value in typed.items():
            rows.setdefault(key, [None, None])[0] = json.dumps(value)
        return _settings_document((k, text, rowid) for k, (text, rowid) in rows.items())

    records.update(SETTINGS_PATH, change, by=records.TIM, change="settings",
                   summary="attention settings: " + ", ".join(sorted(typed)))
    return _settings.set(_load_settings())


# ── items ─────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Item:
    id: int
    node_id: str
    kind: str
    source_id: str
    revision: int
    event: str
    facts: dict = field(default_factory=dict)
    untrusted: dict = field(default_factory=dict)
    audible: bool = True
    state: str = PENDING
    digested_revision: int = 0
    created_at: float = 0.0
    updated_at: float = 0.0
    seen_at: Optional[float] = None

    def public(self) -> dict:
        """As the page's route and the brain's tool give it."""
        return {"id": self.id, "kind": self.kind, "source_id": self.source_id,
                "revision": self.revision, "event": self.event, "facts": self.facts,
                "untrusted": self.untrusted, "state": self.state,
                "created_at": self.created_at, "updated_at": self.updated_at,
                "seen_at": self.seen_at}


ITEM_COLUMNS = ("id", "node_id", "kind", "source_id", "revision", "event", "facts", "untrusted",
                "audible", "state", "digested_revision", "created_at", "updated_at", "seen_at")
EVENT_COLUMNS = ("id", "item_id", "at", "revision", "event", "facts", "untrusted")
JSON_COLUMNS = ("facts", "untrusted")


def _item(data: dict) -> Item:
    return Item(id=data["id"], node_id=data["node_id"], kind=data["kind"],
                source_id=data["source_id"], revision=data["revision"], event=data["event"],
                facts=_json_value(data, "facts"), untrusted=_json_value(data, "untrusted"),
                audible=bool(data["audible"]), state=data["state"],
                digested_revision=data["digested_revision"], created_at=data["created_at"],
                updated_at=data["updated_at"], seen_at=data.get("seen_at"))


def item_document(row: dict, presentations: list, events: list) -> dict:
    """An attention document from its item row's columns (JSON text for
    facts and untrusted), its presentations ({digest_id, revision}) and the
    events this version records."""
    data: dict = {}
    for c in ITEM_COLUMNS:
        if c in JSON_COLUMNS:
            data.update(_json_cell(c, row[c]))
        else:
            data[c] = row[c]
    data["presentations"] = presentations
    data["events"] = events
    return data


def event_entry(row: dict) -> dict:
    """One event as a version's `events` holds it, from its row's columns
    (its item is the document's own; `id` only when it had one)."""
    out: dict = {} if row.get("id") is None else {"id": row["id"]}
    for c in ("at", "revision", "event"):
        out[c] = row[c]
    for c in JSON_COLUMNS:
        out.update(_json_cell(c, row[c]))
    return out


def _same_words(data: dict, event: str, facts_text: str, untrusted_text: str) -> bool:
    return (data["event"], _json_text(data, "facts"), _json_text(data, "untrusted")) == (
        event, facts_text, untrusted_text)


def _next_id(unit: records.Unit, which: str) -> int:
    """The next id of `which` ("item" or "digest"), given out in the
    unit's own write: one past the counter, or past the largest held when
    the counter has none yet (a moved record)."""
    rec = unit.get(COUNTER_PATH)
    counters = dict(rec.data) if rec is not None else {}
    if which not in counters:
        if which == "item":
            top = records.scan(ROOT, types=[ITEM_TYPE], where=[_IS_ITEM], order=("id", "desc"),
                               limit=1)
        else:
            top = records.scan(DIGESTS, types=[DIGEST_TYPE], order=("id", "desc"), limit=1)
        found = [r.data.get("id") for r in top]
        counters[which] = max([0] + [i for i in found if isinstance(i, int)])
    counters[which] = int(counters[which]) + 1
    unit.put(COUNTER_PATH, counters, change="id", summary=f"{which} {counters[which]}")
    return counters[which]


def _items_where(where: list, *, order: Optional[tuple[str, str]] = None,
                 limit: Optional[int] = None) -> list[records.Rec]:
    return [r for r in records.scan(ROOT, types=[ITEM_TYPE], where=[_IS_ITEM] + where,
                                    order=order, limit=limit) if _is_item_path(r.path)]


def _newest_first(recs: list[records.Rec]) -> list[records.Rec]:
    return sorted(recs, key=lambda r: (r.data["updated_at"], r.data["id"]), reverse=True)


Clock = Callable[[], float]

# Kinds that are counted and listed but never spoken, by the digest or
# anyone: a package Tim was sent (package_inbox.py) waits in his backlog
# until he asks for it. Recorded silent, and never raised to audible.
SILENT_KINDS = frozenset({"package", "work_follow_up", "work_stalled"})


def _load_last_started() -> Optional[float]:
    found = list(records.scan(DIGESTS, types=[DIGEST_TYPE], order=("started_at", "desc"), limit=1))
    return found[0].data["started_at"] if found else None


_last_started = _Held(_load_last_started)


class AttentionStore:
    """The one writer of the attention record. `on_change` is told after
    every committed change (the server's hint to the pages)."""

    def __init__(self, clock: Clock = time.time,
                 on_change: Optional[Callable[[], None]] = None,
                 node_id: Optional[Callable[[], str]] = None) -> None:
        self._clock = clock
        self._on_change = on_change or (lambda: None)
        self._node_id = node_id or data_paths.node_id

    def _now(self) -> float:
        return float(self._clock())

    def _changed(self) -> None:
        try:
            self._on_change()
        except Exception:
            pass

    def record(self, kind: str, source_id: str, event: str, *, facts: Optional[dict] = None,
               untrusted: Optional[dict] = None, audible: bool = True) -> Item:
        """Record what happened to one source object. The same event with the
        same facts again changes nothing (a poll seeing it twice) but may
        make it this node's to speak of; anything new is the item's next
        revision, pending again, and a line of its history. `audible` is only
        ever raised here, never lowered (`set_audible` lowers it)."""
        now = self._now()
        audible = audible and kind not in SILENT_KINDS
        source_id = str(source_id)
        facts_text, untrusted_text = _text(facts or {}), _text(untrusted or {})
        path = item_path(kind, source_id)
        node = self._node_id()

        def write(unit: records.Unit) -> tuple[Item, bool]:
            rec = unit.get(path)
            if rec is not None and _same_words(rec.data, event, facts_text, untrusted_text) \
                    and rec.data["state"] != RESOLVED:
                if audible and not rec.data["audible"]:
                    data = {**rec.data, "audible": 1, "events": []}
                    unit.put(path, data, change="audible", summary=f"{kind} {source_id}: audible")
                    return _item(data), False
                return _item(rec.data), False
            row = {"event": event, "facts": facts_text, "untrusted": untrusted_text,
                   "state": PENDING, "updated_at": now, "seen_at": None}
            if rec is None:
                row.update(id=_next_id(unit, "item"), node_id=node, kind=kind,
                           source_id=source_id, revision=1, audible=int(audible),
                           digested_revision=0, created_at=now)
                presentations: list = []
            else:
                row.update({c: rec.data[c] for c in ("id", "node_id", "kind", "source_id",
                                                     "digested_revision", "created_at")})
                row["revision"] = rec.data["revision"] + 1
                row["audible"] = max(int(rec.data["audible"]), int(audible))
                presentations = rec.data.get("presentations") or []
            entry = event_entry({"at": now, "revision": row["revision"], "event": event,
                                 "facts": facts_text, "untrusted": untrusted_text})
            data = item_document(row, presentations, [entry])
            unit.put(path, data, change=event,
                     summary=f"{kind} {source_id}: {event} (revision {row['revision']})"[:2000])
            return _item(data), True

        item, news = records.atomic(write)
        if news:
            self._changed()
        return item

    def set_audible(self, kind: str, source_id: str, audible: bool) -> None:
        """Whether this node may speak of the item. Not a revision: being
        allowed to speak of it is not news, so it neither makes it pending
        again nor makes it younger."""
        audible = audible and kind not in SILENT_KINDS

        def write(unit: records.Unit) -> None:
            rec = unit.get(item_path(kind, source_id))
            if rec is None or int(rec.data["audible"]) == int(audible):
                return
            unit.put(rec.path, {**rec.data, "audible": int(audible), "events": []},
                     change="audible", summary=f"{kind} {source_id}: audible {bool(audible)}")

        records.atomic(write)

    def resolve(self, kind: str, source_id: str) -> bool:
        """The source no longer wants him (a needs board emptied, a session no
        longer waiting, signed in): out of the pending count and the digest.
        Its history stays. True when something changed."""
        now = self._now()

        def write(unit: records.Unit) -> bool:
            rec = unit.get(item_path(kind, source_id))
            if rec is None or rec.data["state"] == RESOLVED:
                return False
            unit.put(rec.path, {**rec.data, "state": RESOLVED, "updated_at": now, "events": []},
                     change="resolved", summary=f"{kind} {source_id}: resolved")
            return True

        changed = records.atomic(write)
        if changed:
            self._changed()
        return changed

    def get(self, kind: str, source_id: str) -> Optional[Item]:
        rec = records.get(item_path(kind, source_id))
        return _item(rec.data) if rec is not None else None

    def items(self, states: Iterable[str] = (PENDING,), limit: int = LIST_MAX) -> list[Item]:
        """Items in `states`, newest change first."""
        states = [s for s in states if s in STATES]
        if not states:
            return []
        limit = max(1, min(limit, LIST_MAX))
        found = [r for r in _items_where([["state", "in", states]], order=("updated_at", "desc"),
                                         limit=limit) if r.data["state"] in states]
        if len(found) >= limit:
            # Items changed at the same moment as the last one shown come
            # newest id first, as they always have: read every one of them.
            edge = found[-1].data["updated_at"]
            seen = {r.path for r in found}
            found += [r for r in _items_where([["state", "in", states], ["updated_at", "=", edge]])
                      if r.path not in seen and r.data["state"] in states]
        return [_item(r.data) for r in _newest_first(found)[:limit]]

    def pending_count(self) -> int:
        pending = [_IS_ITEM, ["state", "=", PENDING]]
        return (records.count(ROOT, types=[ITEM_TYPE], where=pending).items
                - sum(records.count(f"{ROOT}/{f}", types=[ITEM_TYPE], where=pending).items
                      for f in RESERVED_FOLDERS))

    def candidates(self, since: float) -> list[Item]:
        """What a digest may speak of: pending, this node's to speak, a
        revision no digest has finished with, and changed at or after `since`
        (when he was last seen to come back: nothing older is a backlog to
        play). Oldest first."""
        if since == float("inf"):
            return []
        found = _items_where([["state", "=", PENDING], ["audible", "=", 1],
                              ["updated_at", ">=", since]])
        found = [r for r in found if r.data["state"] == PENDING and r.data["audible"] == 1
                 and r.data["revision"] > r.data["digested_revision"]
                 and r.data["updated_at"] >= since]
        found.sort(key=lambda r: (r.data["updated_at"], r.data["id"]))
        return [_item(r.data) for r in found]

    def _mark(self, paths: list[str], ok: Callable[[dict], bool]) -> int:
        """Mark seen, now, those of `paths` still pending and `ok`, in writes
        of at most `WRITE_BATCH`; his act. How many were marked."""
        now = self._now()
        count = 0
        for i in range(0, len(paths), WRITE_BATCH):
            chunk = paths[i:i + WRITE_BATCH]

            def write(unit: records.Unit) -> int:
                n = 0
                for path in chunk:
                    rec = unit.get(path)
                    if rec is None or rec.data["state"] != PENDING or not ok(rec.data):
                        continue
                    unit.put(path, {**rec.data, "state": SEEN, "seen_at": now, "events": []},
                             change="seen",
                             summary=f"{rec.data['kind']} {rec.data['source_id']}: seen")
                    n += 1
                return n

            count += records.atomic(write, by=records.TIM)
        if count:
            self._changed()
        return count

    def mark_seen(self, ids: Optional[Iterable[int]] = None) -> int:
        """He has reviewed these (every pending one when `ids` is None). How
        many were marked. Seen is his act: his click, his mark, or hearing it
        read in full (`mark_seen_source`); no digest sets it."""
        if ids is None:
            return self._mark([r.path for r in _items_where([["state", "=", PENDING]])],
                              lambda d: True)
        wanted = sorted({int(i) for i in ids})
        if not wanted:
            return 0
        return self._mark([r.path for r in _items_where([["id", "in", wanted]])],
                          lambda d: d["id"] in wanted)

    def mark_seen_source(self, kind: str, source_id: str,
                         revision: Optional[int] = None) -> int:
        """He has heard this source read to him in full: its item is seen,
        if still pending and, when `revision` is given, still at the
        revision that was read (a newer one is news he has not heard). How
        many were marked, 0 or 1."""
        return self._mark([item_path(kind, source_id)],
                          lambda d: revision is None or d["revision"] == int(revision))

    def history(self, item_id: int) -> list[dict]:
        found = [r for r in _items_where([["id", "=", int(item_id)]]) if r.data["id"] == int(item_id)]
        if not found:
            return []
        return [{"at": e["at"], "revision": e["revision"], "event": e["event"],
                 "facts": _json_value(e, "facts"), "untrusted": _json_value(e, "untrusted")}
                for v in records.history(found[0].path, fields=["events"])
                for e in (v.data or {}).get("events") or ()]

    # ── digests ──────────────────────────────────────────────────────────

    def begin_digest(self, items: list[Item]) -> int:
        """A digest is being made from these items at these revisions."""
        now = self._now()

        def write(unit: records.Unit) -> int:
            digest_id = _next_id(unit, "digest")
            unit.put(digest_path(digest_id), {"id": digest_id, "started_at": now,
                                              "updated_at": now, "outcome": GENERATING,
                                              "reason": None, "text": None},
                     change=GENERATING, summary=f"digest {digest_id} begun")
            for i in items:
                rec = unit.get(item_path(i.kind, i.source_id))
                if rec is None or rec.data["id"] != i.id:
                    continue
                shown = [p for p in rec.data.get("presentations") or ()
                         if p["digest_id"] != digest_id]
                shown.append({"digest_id": digest_id, "revision": i.revision})
                unit.put(rec.path, {**rec.data, "presentations": shown, "events": []},
                         change="presented",
                         summary=f"{i.kind} {i.source_id}: in digest {digest_id} "
                                 f"at revision {i.revision}")
            return digest_id

        digest_id = records.atomic(write)
        _last_started.set(now)
        brain_activity.note(brain_activity.DIGEST, brain_activity.STARTED, id=str(digest_id))
        return digest_id

    def current_revisions(self, items: list[Item]) -> dict[int, tuple[int, str]]:
        """Each item's revision and state now, by id."""
        out: dict[int, tuple[int, str]] = {}
        for i in items:
            rec = records.get(item_path(i.kind, i.source_id))
            if rec is not None and rec.data["id"] == i.id:
                out[i.id] = (rec.data["revision"], rec.data["state"])
        return out

    def finish_digest(self, digest_id: int, outcome: str, *, reason: str = "",
                      text: Optional[str] = None) -> None:
        """Record the outcome. Silent or played: its items are done with at
        the revisions it was built from (a newer revision is still offered).
        Any other outcome leaves them as they were."""
        if outcome not in DIGEST_OUTCOMES:
            raise ValueError(f"unknown digest outcome {outcome!r}")
        now = self._now()
        digest_id = int(digest_id)

        def write(unit: records.Unit) -> None:
            rec = unit.get(digest_path(digest_id))
            if rec is not None:
                unit.put(rec.path, {**rec.data, "outcome": outcome, "reason": reason or None,
                                    "text": rec.data.get("text") if text is None else text,
                                    "updated_at": now},
                         change=outcome, summary=f"digest {digest_id}: {outcome}")
            if outcome not in FINISHED_WITH:
                return
            for item in unit.scan(ROOT, types=[ITEM_TYPE], where=[
                    _IS_ITEM, ["presentations[].digest_id", "=", digest_id]]):
                if not _is_item_path(item.path):
                    continue
                revs = [p["revision"] for p in item.data.get("presentations") or ()
                        if p["digest_id"] == digest_id]
                if not revs or item.data["digested_revision"] >= revs[0]:
                    continue
                unit.put(item.path, {**item.data, "digested_revision": revs[0], "events": []},
                         change="digested",
                         summary=f"{item.data['kind']} {item.data['source_id']}: digest "
                                 f"{digest_id} {outcome} at revision {revs[0]}")

        records.atomic(write)
        # Its outcome on the brain activity feed; never its text or reason.
        brain_activity.note(brain_activity.DIGEST, outcome, id=str(digest_id))
        self._changed()

    def last_digest_started(self) -> Optional[float]:
        """When the last digest began. Held in memory (the admission check
        asks on every look): read once, then set by `begin_digest`."""
        return _last_started.get()

    def digests(self, limit: int = 20) -> list[dict]:
        out = []
        for r in records.scan(DIGESTS, types=[DIGEST_TYPE], order=("id", "desc"), limit=limit):
            d = r.data
            shown = _items_where([["presentations[].digest_id", "=", d["id"]]])
            out.append({"id": d["id"], "started_at": d["started_at"],
                        "updated_at": d["updated_at"], "outcome": d["outcome"],
                        "reason": d["reason"], "items": sorted(i.data["id"] for i in shown)})
        return out


# The record the running server attends with, for the adapters it does not
# build itself (a human message's send outcome). None outside a server.
_current: Optional[AttentionStore] = None


def use(store: Optional[AttentionStore]) -> None:
    global _current
    _current = store


def current() -> Optional[AttentionStore]:
    return _current


# ── the move (knowledge_move): SQLite's tables to these documents and back ──

def _table_rows(conn, table: str) -> list[dict]:
    """Every row of `table` as {column: value} in rowid order (with the
    rowid as `rowid` for a table that keeps it apart), or none when the
    file has no such table."""
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                        (table,)).fetchone():
        return []
    info = move.table_info(conn, table)
    return [dict(r.cells, **({} if r.rowid is None else {"rowid": r.rowid}))
            for r in move.read_rows(conn, info)]


def _with_new_ids(rows: list[dict], column: str, order: Callable[[dict], Any]) -> list[dict]:
    """Rows written since the move carry no `column` (an event's id, a row
    number): each gets the next one after the largest, in `order`."""
    top = max([r[column] for r in rows if isinstance(r.get(column), int)] + [0])
    for r in sorted((r for r in rows if r.get(column) is None), key=order):
        top += 1
        r[column] = top
    return rows


class ItemMapping:
    """attention_items, attention_events and attention_presentations as
    attention documents: an item's events become its versions (oldest
    first, each the event's revision as it was recorded), its presentations
    a list on it. Events or presentations naming no item are kept whole in
    `ORPHANS_PATH`, which nothing reads."""
    name = "attention"
    tables = ("attention_items", "attention_events", "attention_presentations")
    under = (ROOT,)
    types = ("attention", "orphans")
    history = True

    def documents(self, conn) -> Iterator[move.Doc]:
        events: dict = {}
        for e in _table_rows(conn, "attention_events"):
            events.setdefault(e["item_id"], []).append(e)
        shown: dict = {}
        for p in _table_rows(conn, "attention_presentations"):
            shown.setdefault(p["item_id"], []).append(p)
        for row in _table_rows(conn, "attention_items"):
            pres = [{"digest_id": p["digest_id"], "revision": p["revision"], "rowid": p["rowid"]}
                    for p in shown.pop(row["id"], [])]
            history = []
            for e in sorted(events.pop(row["id"], []), key=lambda e: e["id"]):
                then = {**row, "revision": e["revision"], "event": e["event"],
                        "facts": e["facts"], "untrusted": e["untrusted"], "updated_at": e["at"],
                        "state": PENDING, "seen_at": None}
                before = [p for p in pres if _earlier(p["revision"], e["revision"])]
                history.append(move.Revision(
                    item_document(then, before, [event_entry(e)]), by=records.JARVIS,
                    change=str(e["event"])[:200],
                    summary=f"{row['kind']} {row['source_id']}: {e['event']} "
                            f"(revision {e['revision']})"[:2000]))
            yield move.Doc(item_path(row["kind"], row["source_id"]),
                           item_document(row, pres, []), history=tuple(history))
        orphans = {"events": [{k: _cell(v) for k, v in e.items()}
                              for found in events.values() for e in found],
                   "presentations": [{k: _cell(v) for k, v in p.items()}
                                     for found in shown.values() for p in found]}
        if orphans["events"] or orphans["presentations"]:
            yield move.Doc(ORPHANS_PATH, orphans)

    def rows(self, docs: Iterable[move.Doc]) -> dict[str, list[move.Row]]:
        items, events, shown = [], [], []
        for doc in docs:
            if doc.path == ORPHANS_PATH:
                events += [{k: _uncell(v) for k, v in e.items()} for e in doc.data.get("events", ())]
                shown += [{k: _uncell(v) for k, v in p.items()}
                          for p in doc.data.get("presentations", ())]
                continue
            if not _is_item_path(doc.path) or "digested_revision" not in doc.data:
                continue
            d = doc.data
            items.append({c: _json_text(d, c) if c in JSON_COLUMNS else d.get(c)
                          for c in ITEM_COLUMNS})
            versions = [r.data for r in doc.history] + [d]
            for n, v in enumerate(versions):
                for e in v.get("events") or ():
                    events.append({"id": e.get("id"), "item_id": d["id"], "at": e["at"],
                                   "revision": e["revision"], "event": e["event"],
                                   "facts": _json_text(e, "facts"),
                                   "untrusted": _json_text(e, "untrusted"), "_order": (n,)})
            shown += [{"digest_id": p["digest_id"], "item_id": d["id"], "revision": p["revision"],
                       "rowid": p.get("rowid")} for p in d.get("presentations") or ()]
        _with_new_ids(events, "id", lambda e: (e["at"], e["item_id"], e["_order"]))
        _with_new_ids(shown, "rowid", lambda p: (p["digest_id"], p["item_id"]))
        return {
            "attention_items": [move.Row(r) for r in items],
            "attention_events": [move.Row({c: e[c] for c in EVENT_COLUMNS}) for e in events],
            "attention_presentations": [move.Row({c: p[c] for c in ("digest_id", "item_id", "revision")},
                                                 p["rowid"]) for p in shown],
        }


def _earlier(a: Any, b: Any) -> bool:
    try:
        return a < b
    except TypeError:
        return False


DIGEST_COLUMNS = ("id", "started_at", "updated_at", "outcome", "reason", "text")


class DigestMapping:
    """attention_digests as digest documents, one per row."""
    name = "digest"
    tables = ("attention_digests",)
    under = (DIGESTS,)
    history = False

    def documents(self, conn) -> Iterator[move.Doc]:
        for row in _table_rows(conn, "attention_digests"):
            yield move.Doc(digest_path(row["id"]), {c: row[c] for c in DIGEST_COLUMNS})

    def rows(self, docs: Iterable[move.Doc]) -> dict[str, list[move.Row]]:
        return {"attention_digests": [move.Row({c: d.data.get(c) for c in DIGEST_COLUMNS})
                                      for d in docs if d.path.endswith("." + DIGEST_TYPE)]}


class SettingsMapping:
    """attention_settings as the one settings document."""
    name = "attention-settings"
    tables = ("attention_settings",)
    under = ("/settings",)
    history = False

    def documents(self, conn) -> Iterator[move.Doc]:
        rows = _table_rows(conn, "attention_settings")
        if rows:
            yield move.Doc(SETTINGS_PATH, _settings_document(
                (r["key"], r["value"], r["rowid"]) for r in rows))

    def rows(self, docs: Iterable[move.Doc]) -> dict[str, list[move.Row]]:
        found = []
        for d in docs:
            if d.path == SETTINGS_PATH:
                found += [{"key": k, "value": t, "rowid": n} for k, t, n in _settings_rows(d.data)]
        _with_new_ids(found, "rowid", lambda r: str(r["key"]))
        return {"attention_settings": [move.Row({"key": r["key"], "value": r["value"]}, r["rowid"])
                                       for r in found]}


MAPPINGS = [ItemMapping(), DigestMapping(), SettingsMapping()]
