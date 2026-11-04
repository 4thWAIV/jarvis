"""attention_store.py — the durable record of background events.

Every routine event JARVIS used to announce on its own (an agent's message
delivered or answered, a session's message, a needs board, a session that
wants him, a run or a package run ending, a person's message arriving, not
being signed in) is first an attention item here, linked to its source
record by kind and id. Nothing is said by recording one: whether anything is
said, and what, is attention.py's admission policy and its one digest.

    attention_items     one row per source object (kind, source_id): an
                        evolving event (delivered, then answered) moves the
                        same row to a new revision; it is never a second
                        item. `facts` are JARVIS's own (codes, counts, ids);
                        `untrusted` are other people's words (names,
                        subjects), only ever handed to the brain walled.
                        `audible` says whether this node may speak of it (an
                        arrival only once this node holds its presentation
                        lease). `state` is what he has done with it
                        (pending, seen, resolved); `digested_revision` is the
                        revision a digest last finished with, so a revision
                        is offered to a digest once.
    attention_events    append-only: every revision as it was recorded, so
                        the source's history survives the item moving on.
    attention_digests   each digest attempt and its outcome (generating,
                        silent, discarded, failed, queued, played,
                        interrupted), and attention_presentations the items
                        and revisions it was built from. Played means the
                        audio was acknowledged in full; nothing here claims
                        he heard or read anything.
    attention_settings  the admission policy's tunable trial values.

The record is in JARVIS's one database under its own schema version
(`attention_schema`, run_store.migrate); every change is one transaction.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from contextlib import closing
from dataclasses import dataclass, field, fields
from typing import Callable, Iterable, Optional

from jarvis import data_paths, run_store

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

_SCHEMA = """
    CREATE TABLE IF NOT EXISTS attention_items (
        id                INTEGER PRIMARY KEY,
        node_id           TEXT NOT NULL,
        kind              TEXT NOT NULL,
        source_id         TEXT NOT NULL,
        revision          INTEGER NOT NULL,
        event             TEXT NOT NULL,
        facts             TEXT NOT NULL,
        untrusted         TEXT NOT NULL,
        audible           INTEGER NOT NULL,
        state             TEXT NOT NULL,
        digested_revision INTEGER NOT NULL DEFAULT 0,
        created_at        REAL NOT NULL,
        updated_at        REAL NOT NULL,
        seen_at           REAL,
        UNIQUE (kind, source_id)
    );
    CREATE INDEX IF NOT EXISTS attention_items_by_state ON attention_items (state, updated_at);
    CREATE TABLE IF NOT EXISTS attention_events (
        id        INTEGER PRIMARY KEY,
        item_id   INTEGER NOT NULL REFERENCES attention_items(id),
        at        REAL NOT NULL,
        revision  INTEGER NOT NULL,
        event     TEXT NOT NULL,
        facts     TEXT NOT NULL,
        untrusted TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS attention_events_by_item ON attention_events (item_id, id);
    CREATE TABLE IF NOT EXISTS attention_digests (
        id         INTEGER PRIMARY KEY,
        started_at REAL NOT NULL,
        updated_at REAL NOT NULL,
        outcome    TEXT NOT NULL,
        reason     TEXT,
        text       TEXT
    );
    CREATE TABLE IF NOT EXISTS attention_presentations (
        digest_id INTEGER NOT NULL REFERENCES attention_digests(id),
        item_id   INTEGER NOT NULL REFERENCES attention_items(id),
        revision  INTEGER NOT NULL,
        PRIMARY KEY (digest_id, item_id)
    );
    CREATE TABLE IF NOT EXISTS attention_settings (
        key   TEXT PRIMARY KEY,
        value TEXT NOT NULL
    );
"""

# Each step is appended, never changed (run_store.migrate).
_MIGRATIONS = {1: _SCHEMA}

_migrated: set[str] = set()
_migrating = threading.Lock()


def _connect() -> sqlite3.Connection:
    conn = run_store._connect()
    path = conn.execute("PRAGMA database_list").fetchone()["file"]
    if path not in _migrated:
        with _migrating:
            if path not in _migrated:
                run_store.migrate(conn, "attention_schema", _MIGRATIONS, "attention")
                conn.commit()
                _migrated.add(path)
    return conn


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


def validate(changes: dict) -> dict:
    """The changes as typed values, or Refused naming the first wrong one.
    One check for the page's route and the brain's tool alike."""
    if not isinstance(changes, dict) or not changes:
        raise Refused("Nothing to change.")
    known = {f.name for f in fields(Settings)}
    out: dict = {}
    for key, value in changes.items():
        if key not in known:
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


def settings() -> Settings:
    """The saved settings, each missing or unreadable value its default."""
    with closing(_connect()) as conn:
        rows = conn.execute("SELECT key, value FROM attention_settings").fetchall()
    saved: dict = {}
    for row in rows:
        try:
            saved.update(validate({row["key"]: json.loads(row["value"])}))
        except (Refused, ValueError):
            continue
    return Settings(**saved)


def change_settings(changes: dict) -> Settings:
    """Validate and save `changes`; the settings as saved now."""
    typed = validate(changes)
    with closing(_connect()) as conn, conn:
        conn.executemany(
            "INSERT INTO attention_settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            [(k, json.dumps(v)) for k, v in typed.items()])
    return settings()


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


def _item(row: sqlite3.Row) -> Item:
    return Item(id=row["id"], node_id=row["node_id"], kind=row["kind"],
                source_id=row["source_id"], revision=row["revision"], event=row["event"],
                facts=json.loads(row["facts"]), untrusted=json.loads(row["untrusted"]),
                audible=bool(row["audible"]), state=row["state"],
                digested_revision=row["digested_revision"], created_at=row["created_at"],
                updated_at=row["updated_at"], seen_at=row["seen_at"])


def _text(value: dict) -> str:
    return json.dumps(value or {}, sort_keys=True, ensure_ascii=False)


Clock = Callable[[], float]


class AttentionStore:
    """The one writer of the attention tables. `on_change` is told after
    every committed change (the server's hint to the pages)."""

    def __init__(self, clock: Clock = time.time,
                 on_change: Optional[Callable[[], None]] = None,
                 node_id: Optional[Callable[[], str]] = None) -> None:
        self._clock = clock
        self._on_change = on_change or (lambda: None)
        self._node_id = node_id or data_paths.node_id
        with closing(_connect()):
            pass

    def _changed(self) -> None:
        try:
            self._on_change()
        except Exception:
            pass

    def record(self, kind: str, source_id: str, event: str, *, facts: Optional[dict] = None,
               untrusted: Optional[dict] = None, audible: bool = True) -> Item:
        """Record what happened to one source object. The same event with the
        same facts again changes nothing (a poll seeing it twice); anything
        new is the item's next revision, pending again, and a line of its
        history. `audible` is only ever raised here, never lowered: whether
        this node may speak of it is `set_audible`'s."""
        now = self._clock()
        facts_text, untrusted_text = _text(facts or {}), _text(untrusted or {})
        with closing(_connect()) as conn, conn:
            row = conn.execute("SELECT * FROM attention_items WHERE kind = ? AND source_id = ?",
                               (kind, str(source_id))).fetchone()
            if row is not None and (row["event"], row["facts"], row["untrusted"]) == (
                    event, facts_text, untrusted_text) and row["state"] != RESOLVED:
                return _item(row)
            if row is None:
                cur = conn.execute(
                    "INSERT INTO attention_items (node_id, kind, source_id, revision, event, "
                    "facts, untrusted, audible, state, created_at, updated_at) "
                    "VALUES (?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?)",
                    (self._node_id(), kind, str(source_id), event, facts_text, untrusted_text,
                     int(audible), PENDING, now, now))
                item_id, revision = cur.lastrowid, 1
            else:
                item_id, revision = row["id"], row["revision"] + 1
                conn.execute(
                    "UPDATE attention_items SET revision = ?, event = ?, facts = ?, "
                    "untrusted = ?, audible = MAX(audible, ?), state = ?, updated_at = ?, "
                    "seen_at = NULL WHERE id = ?",
                    (revision, event, facts_text, untrusted_text, int(audible), PENDING, now,
                     item_id))
            conn.execute(
                "INSERT INTO attention_events (item_id, at, revision, event, facts, untrusted) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (item_id, now, revision, event, facts_text, untrusted_text))
            item = _item(conn.execute("SELECT * FROM attention_items WHERE id = ?",
                                      (item_id,)).fetchone())
        self._changed()
        return item

    def set_audible(self, kind: str, source_id: str, audible: bool) -> None:
        """Whether this node may speak of the item. Not a revision: being
        allowed to speak of it is not news, so it neither makes it pending
        again nor makes it younger."""
        with closing(_connect()) as conn, conn:
            conn.execute("UPDATE attention_items SET audible = ? WHERE kind = ? AND source_id = ?",
                         (int(audible), kind, str(source_id)))

    def resolve(self, kind: str, source_id: str) -> bool:
        """The source no longer wants him (a needs board emptied, a session no
        longer waiting, signed in): out of the pending count and the digest.
        Its history stays. True when something changed."""
        with closing(_connect()) as conn, conn:
            changed = conn.execute(
                "UPDATE attention_items SET state = ?, updated_at = ? "
                "WHERE kind = ? AND source_id = ? AND state != ?",
                (RESOLVED, self._clock(), kind, str(source_id), RESOLVED)).rowcount
        if changed:
            self._changed()
        return bool(changed)

    def get(self, kind: str, source_id: str) -> Optional[Item]:
        with closing(_connect()) as conn:
            row = conn.execute("SELECT * FROM attention_items WHERE kind = ? AND source_id = ?",
                               (kind, str(source_id))).fetchone()
        return _item(row) if row else None

    def items(self, states: Iterable[str] = (PENDING,), limit: int = LIST_MAX) -> list[Item]:
        """Items in `states`, newest change first."""
        states = [s for s in states if s in STATES]
        if not states:
            return []
        marks = ",".join("?" * len(states))
        with closing(_connect()) as conn:
            rows = conn.execute(
                f"SELECT * FROM attention_items WHERE state IN ({marks}) "
                f"ORDER BY updated_at DESC, id DESC LIMIT ?",
                (*states, max(1, min(limit, LIST_MAX)))).fetchall()
        return [_item(r) for r in rows]

    def pending_count(self) -> int:
        with closing(_connect()) as conn:
            return conn.execute("SELECT COUNT(*) FROM attention_items WHERE state = ?",
                                (PENDING,)).fetchone()[0]

    def candidates(self, since: float) -> list[Item]:
        """What a digest may speak of: pending, this node's to speak, a
        revision no digest has finished with, and changed at or after `since`
        (when he was last seen to come back: nothing older is a backlog to
        play). Oldest first."""
        with closing(_connect()) as conn:
            rows = conn.execute(
                "SELECT * FROM attention_items WHERE state = ? AND audible = 1 "
                "AND revision > digested_revision AND updated_at >= ? "
                "ORDER BY updated_at, id", (PENDING, since)).fetchall()
        return [_item(r) for r in rows]

    def mark_seen(self, ids: Optional[Iterable[int]] = None) -> int:
        """He has reviewed these (every pending one when `ids` is None). How
        many were marked. Seen is his act alone: no digest or playback sets
        it."""
        now = self._clock()
        with closing(_connect()) as conn, conn:
            if ids is None:
                count = conn.execute(
                    "UPDATE attention_items SET state = ?, seen_at = ? WHERE state = ?",
                    (SEEN, now, PENDING)).rowcount
            else:
                wanted = [int(i) for i in ids]
                count = 0
                for item_id in wanted:
                    count += conn.execute(
                        "UPDATE attention_items SET state = ?, seen_at = ? "
                        "WHERE id = ? AND state = ?", (SEEN, now, item_id, PENDING)).rowcount
        if count:
            self._changed()
        return count

    def history(self, item_id: int) -> list[dict]:
        with closing(_connect()) as conn:
            rows = conn.execute("SELECT * FROM attention_events WHERE item_id = ? ORDER BY id",
                                (item_id,)).fetchall()
        return [{"at": r["at"], "revision": r["revision"], "event": r["event"],
                 "facts": json.loads(r["facts"]), "untrusted": json.loads(r["untrusted"])}
                for r in rows]

    # ── digests ──────────────────────────────────────────────────────────

    def begin_digest(self, items: list[Item]) -> int:
        """A digest is being made from these items at these revisions."""
        now = self._clock()
        with closing(_connect()) as conn, conn:
            digest_id = conn.execute(
                "INSERT INTO attention_digests (started_at, updated_at, outcome) "
                "VALUES (?, ?, ?)", (now, now, GENERATING)).lastrowid
            conn.executemany(
                "INSERT INTO attention_presentations (digest_id, item_id, revision) "
                "VALUES (?, ?, ?)", [(digest_id, i.id, i.revision) for i in items])
        return digest_id

    def current_revisions(self, items: list[Item]) -> dict[int, tuple[int, str]]:
        """Each item's revision and state now, by id."""
        if not items:
            return {}
        marks = ",".join("?" * len(items))
        with closing(_connect()) as conn:
            rows = conn.execute(f"SELECT id, revision, state FROM attention_items "
                                f"WHERE id IN ({marks})", [i.id for i in items]).fetchall()
        return {r["id"]: (r["revision"], r["state"]) for r in rows}

    def finish_digest(self, digest_id: int, outcome: str, *, reason: str = "",
                      text: Optional[str] = None) -> None:
        """Record the outcome. Silent or played: its items are done with at
        the revisions it was built from (a newer revision is still offered).
        Any other outcome leaves them as they were."""
        if outcome not in DIGEST_OUTCOMES:
            raise ValueError(f"unknown digest outcome {outcome!r}")
        now = self._clock()
        with closing(_connect()) as conn, conn:
            conn.execute(
                "UPDATE attention_digests SET outcome = ?, reason = ?, "
                "text = COALESCE(?, text), updated_at = ? WHERE id = ?",
                (outcome, reason or None, text, now, digest_id))
            if outcome in FINISHED_WITH:
                conn.execute(
                    "UPDATE attention_items SET digested_revision = MAX(digested_revision, "
                    "(SELECT p.revision FROM attention_presentations p WHERE p.digest_id = ? "
                    " AND p.item_id = attention_items.id)) "
                    "WHERE id IN (SELECT item_id FROM attention_presentations WHERE digest_id = ?)",
                    (digest_id, digest_id))
        self._changed()

    def last_digest_started(self) -> Optional[float]:
        with closing(_connect()) as conn:
            row = conn.execute("SELECT MAX(started_at) FROM attention_digests").fetchone()
        return row[0]

    def digests(self, limit: int = 20) -> list[dict]:
        with closing(_connect()) as conn:
            rows = conn.execute("SELECT * FROM attention_digests ORDER BY id DESC LIMIT ?",
                                (limit,)).fetchall()
            out = []
            for r in rows:
                items = [p["item_id"] for p in conn.execute(
                    "SELECT item_id FROM attention_presentations WHERE digest_id = ?",
                    (r["id"],))]
                out.append({"id": r["id"], "started_at": r["started_at"],
                            "updated_at": r["updated_at"], "outcome": r["outcome"],
                            "reason": r["reason"], "items": items})
        return out
