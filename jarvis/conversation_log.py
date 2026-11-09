"""The conversation bus: one durable record of the conversation, messages
to and from agents included.

Every item of the conversation is appended here first and only then shown:
what Tim said (heard or typed), what JARVIS said, each capture, each message
to an agent, each message an agent sent him, each agent's answer, and
JARVIS's notices (delivered, not picked up, workflow updates). The page's
history and each agent's thread are read back from here, so they survive a
reload, a restart and a second tab, and page back without limit.

A message's status is never copied in: an item points at its `messages`
row (run_store) and its status is read from there when the item is read
(`statuses`), so it is always the current one.

Every message to and from him has a counterpart (one_channel: an agent's
session, a person, a workflow, a work item or a package), and every message
from one has the proof that admitted it: `inbound` refuses one without. A
thread is every item with one counterpart (`thread_of`), and `threads` lists
them newest first. `source_kind`/`source_id` point at the row that is the
truth for the message's own state (a `messages` row, a session message, a
work event, an inbox row), as `message_id` always has.

`append` is the only writer. Whoever wants to know an item was added (the
server, to tell the pages to read again) is `connect`ed; it is
told after the row is committed. Items that hold an agent's words (FROM_AGENT,
ANSWER, and text the brain composed out of them) are another session's words:
the brain reads them only inside an untrusted block.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from contextlib import closing
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Callable

from jarvis import run_store


class Counterpart(StrEnum):
    """Who a thread is with: one per message to or from him."""
    AGENT = "agent"          # ref: the listed agent's session id
    PERSON = "person"        # ref: the person's GitHub id
    WORKFLOW = "workflow"    # ref: the workflow's id
    WORK = "work"            # ref: the work item's id
    PACKAGE = "package"      # ref: the package inbox row's id


# What admitted a message from a counterpart: each a proof JARVIS checked,
# never the message's own word. A subagent's proof is its parent's kind of
# proof with `sender_subagent` set.
PROOFS = frozenset({
    "transcript",            # the agent's own Claude Code transcript showed it
    "subagent_transcript",   # one of its Claude subagents' files did
    "codex_rollout",         # its Codex rollout did
    "codex_subagent_rollout",  # a Codex subagent naming it as parent did
    "watcher",               # read from the session's own transcript by the watcher
    "access_key",            # an access key whose label is the agent
    "team_server",           # the team server's signed-in member
})

# The kinds of item that are a message from a counterpart to him.
INBOUND_KINDS = frozenset({"from_agent", "answer", "message_in"})


class Source(StrEnum):
    """The record that is the truth for a message's own state, and how its
    `source_id` names the row there."""
    MESSAGE = "message"                  # run_store messages row: an agent's answer
    SESSION_MESSAGE = "session_message"  # run_store session_messages row
    NEEDS_BOARD = "needs_board"          # "<project>:<mtime>": one proven rewrite
    WORKFLOW_DECISION = "workflow_decision"  # "<workflow id>:<decision key>"
    WORK_DECISION = "work_decision"      # "<work item id>:<decision key>"
    PACKAGE = "package"                  # the package inbox row's id
    PERSON_MESSAGE = "person_message"    # the team server's message id


# Shown in the Conversation view and an agent's thread (`page`, `thread`)
# until the page's one list decides where they go (one_channel phase 4):
# every kind but the messages from the other channels.
_SHOWN = "kind <> 'message_in'"


class ItemKind(StrEnum):
    HEARD = "heard"          # what Tim said
    TYPED = "typed"          # what Tim typed in the white box
    JARVIS = "jarvis"        # what JARVIS said
    CAPTURE = "capture"      # a capture made of Tim's words
    TO_AGENT = "to_agent"    # a message sent (or queued) to an agent
    FROM_AGENT = "from_agent"  # a message an agent sent Tim
    ANSWER = "answer"        # an agent's answer to one of Tim's messages
    NOTICE = "notice"        # delivered, not picked up, workflow updates
    MESSAGE_IN = "message_in"  # a message to him from any other counterpart


@dataclass(frozen=True)
class ConversationItem:
    id: int
    at: float
    kind: ItemKind
    text: str
    agent_session_id: str | None   # the agent it is to or from
    agent_name: str | None
    message_id: int | None         # run_store messages row (TO_AGENT, ANSWER)
    session_message_id: int | None # run_store session_messages row (FROM_AGENT)
    capture_id: int | None
    # Kind-specific facts: a CAPTURE's heard turns; a capture NOTICE's
    # "capture_event" (cleared, merged, restored) and who did it ("by").
    detail: dict = field(default_factory=dict)
    # The thread it is in, and for a message from the counterpart the proof
    # that admitted it (PROOFS) and the subagent that wrote it, if one did.
    counterpart_kind: str | None = None
    counterpart_ref: str | None = None
    sender_evidence: str | None = None
    sender_subagent: str | None = None
    # The row that is the truth for its own state.
    source_kind: str | None = None
    source_id: str | None = None

    @classmethod
    def new(cls, kind: ItemKind, text: str, *, agent_session_id: str | None = None,
            agent_name: str | None = None, message_id: int | None = None,
            session_message_id: int | None = None, capture_id: int | None = None,
            detail: dict | None = None, counterpart_kind: str | None = None,
            counterpart_ref: str | None = None, sender_evidence: str | None = None,
            sender_subagent: str | None = None, source_kind: str | None = None,
            source_id: str | None = None) -> "ConversationItem":
        """An item not yet appended: `append` gives it its id and time. An
        item to or from an agent is in that agent's thread unless it says
        otherwise."""
        if counterpart_kind is None and agent_session_id:
            counterpart_kind, counterpart_ref = Counterpart.AGENT, agent_session_id
        return cls(0, 0.0, ItemKind(kind), text, agent_session_id, agent_name,
                   message_id, session_message_id, capture_id, dict(detail or {}),
                   str(counterpart_kind) if counterpart_kind else None,
                   str(counterpart_ref) if counterpart_ref else None,
                   sender_evidence, sender_subagent or None, source_kind,
                   str(source_id) if source_id is not None else None)

    @property
    def inbound(self) -> bool:
        """A message from its counterpart to him."""
        return str(self.kind) in INBOUND_KINDS


TEXT_MAX = 20000
PAGE = 100

_SCHEMA = """
    CREATE TABLE IF NOT EXISTS conversation_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        at REAL NOT NULL,
        kind TEXT NOT NULL,
        text TEXT NOT NULL,
        agent_session_id TEXT,
        agent_name TEXT,
        message_id INTEGER,
        session_message_id INTEGER,
        capture_id INTEGER,
        detail TEXT NOT NULL DEFAULT '{}'
    );
    CREATE INDEX IF NOT EXISTS conversation_items_by_agent
        ON conversation_items (agent_session_id, id);
    -- One item per message sent, per answer and per message received:
    -- recording one again (a restart, a repeated event) is a no-op.
    CREATE UNIQUE INDEX IF NOT EXISTS conversation_items_once_per_message
        ON conversation_items (kind, message_id)
        WHERE message_id IS NOT NULL AND kind IN ('to_agent', 'answer');
    CREATE UNIQUE INDEX IF NOT EXISTS conversation_items_once_per_session_message
        ON conversation_items (session_message_id)
        WHERE session_message_id IS NOT NULL;
"""

# 2 (one_channel phase 1): each item's counterpart, its proof and its source;
# the items already on record are put in their agent's thread, an answer as
# read by the watcher and a session message by the proof it was admitted on.
_COUNTERPARTS = """
    ALTER TABLE conversation_items ADD COLUMN counterpart_kind TEXT;
    ALTER TABLE conversation_items ADD COLUMN counterpart_ref TEXT;
    ALTER TABLE conversation_items ADD COLUMN sender_evidence TEXT;
    ALTER TABLE conversation_items ADD COLUMN sender_subagent TEXT;
    ALTER TABLE conversation_items ADD COLUMN source_kind TEXT;
    ALTER TABLE conversation_items ADD COLUMN source_id TEXT;
    UPDATE conversation_items SET counterpart_kind = 'agent', counterpart_ref = agent_session_id
        WHERE agent_session_id IS NOT NULL AND agent_session_id <> '';
    UPDATE conversation_items SET source_kind = 'message', source_id = CAST(message_id AS TEXT)
        WHERE message_id IS NOT NULL;
    UPDATE conversation_items SET sender_evidence = 'watcher' WHERE kind = 'answer';
    UPDATE conversation_items SET source_kind = 'session_message',
        source_id = CAST(session_message_id AS TEXT)
        WHERE session_message_id IS NOT NULL;
    CREATE INDEX IF NOT EXISTS conversation_items_by_counterpart
        ON conversation_items (counterpart_kind, counterpart_ref, id);
    -- One item per message from a counterpart, however often it is reported.
    CREATE UNIQUE INDEX IF NOT EXISTS conversation_items_once_per_source
        ON conversation_items (kind, source_kind, source_id)
        WHERE source_kind IS NOT NULL AND kind = 'message_in';
"""

# Each step is appended, never changed (run_store.migrate).
_MIGRATIONS = {1: _SCHEMA, 2: _COUNTERPARTS}

_listener: Callable[[ConversationItem], None] | None = None


def connect(listener: Callable[[ConversationItem], None] | None) -> None:
    """Who is told of every item once it is stored: the server, which tells
    the pages to read again. One listener; a listener that raises is ignored,
    because the record is what matters."""
    global _listener
    _listener = listener


_migrated: set[str] = set()
_migrating = threading.Lock()


def _connect() -> sqlite3.Connection:
    conn = run_store._connect()
    path = conn.execute("PRAGMA database_list").fetchone()["file"]
    if path not in _migrated:
        with _migrating:
            if path not in _migrated:
                # Version 1 is the table as it was before it had a version.
                conn.executescript(_SCHEMA)
                before = _version(conn)
                run_store.migrate(conn, "conversation_schema", _MIGRATIONS, "conversation")
                if before < 2:
                    _session_message_proofs(conn)
                conn.commit()
                _migrated.add(path)
    return conn


def _version(conn) -> int:
    try:
        row = conn.execute("SELECT version FROM conversation_schema").fetchone()
    except sqlite3.OperationalError:
        return 0
    return row[0] if row else 0


def _session_message_proofs(conn) -> None:
    """Migration 2's last step: each session message already on record is
    given the proof it was admitted on (run_store's session_messages), when
    that table has it."""
    have = {r[1] for r in conn.execute("PRAGMA table_info(session_messages)")}
    if not {"sender_evidence", "sender_subagent"} <= have:
        return
    conn.execute(
        "UPDATE conversation_items SET "
        "sender_evidence = (SELECT m.sender_evidence FROM session_messages m "
        "WHERE m.id = conversation_items.session_message_id), "
        "sender_subagent = (SELECT m.sender_subagent FROM session_messages m "
        "WHERE m.id = conversation_items.session_message_id) "
        "WHERE session_message_id IS NOT NULL")


def _item(row) -> ConversationItem:
    return ConversationItem(row["id"], row["at"], ItemKind(row["kind"]), row["text"],
                            row["agent_session_id"], row["agent_name"], row["message_id"],
                            row["session_message_id"], row["capture_id"],
                            json.loads(row["detail"] or "{}"), row["counterpart_kind"],
                            row["counterpart_ref"], row["sender_evidence"],
                            row["sender_subagent"], row["source_kind"], row["source_id"])


def append(item: ConversationItem) -> ConversationItem:
    """Store one item (the only writer), then tell the listeners. An item
    already recorded for the same message is not recorded again; the one
    on record comes back. Text is kept whole up to TEXT_MAX."""
    text = str(item.text or "").strip()[:TEXT_MAX]
    if not text:
        raise ValueError("A conversation item has words.")
    stored = replace(item, text=text, at=time.time())
    with closing(_connect()) as conn:
        cur = conn.execute(
            "INSERT OR IGNORE INTO conversation_items (at, kind, text, agent_session_id, "
            "agent_name, message_id, session_message_id, capture_id, detail, counterpart_kind, "
            "counterpart_ref, sender_evidence, sender_subagent, source_kind, source_id) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (stored.at, str(stored.kind), stored.text, stored.agent_session_id,
             stored.agent_name, stored.message_id, stored.session_message_id,
             stored.capture_id, json.dumps(stored.detail), stored.counterpart_kind,
             stored.counterpart_ref, stored.sender_evidence, stored.sender_subagent,
             stored.source_kind, stored.source_id))
        conn.commit()
        if cur.rowcount:
            stored = replace(stored, id=cur.lastrowid)
            new = True
        else:
            row = conn.execute(
                "SELECT * FROM conversation_items WHERE (kind=? AND message_id=?) "
                "OR (session_message_id IS NOT NULL AND session_message_id=?) "
                "OR (kind=? AND source_kind=? AND source_id=?)",
                (str(stored.kind), stored.message_id, stored.session_message_id,
                 str(stored.kind), stored.source_kind, stored.source_id)).fetchone()
            return _item(row)
    if new and _listener is not None:
        try:
            _listener(stored)
        except Exception:
            pass
    return stored


def _read(where: str, args: list, before: int | None, limit: int) -> list[ConversationItem]:
    limit = max(1, min(int(limit), PAGE * 5))
    if before is not None:
        where += " AND id < ?"
        args = [*args, int(before)]
    with closing(_connect()) as conn:
        rows = conn.execute(f"SELECT * FROM conversation_items WHERE {where} "
                            f"ORDER BY id DESC LIMIT ?", [*args, limit]).fetchall()
    return [_item(r) for r in reversed(rows)]


def page(before: int | None = None, limit: int = PAGE) -> list[ConversationItem]:
    """The conversation, oldest first: the last `limit` items before item
    `before` (the newest when None). Messages from the other channels
    (MESSAGE_IN) are in their threads (`thread_of`), not here yet."""
    return _read(_SHOWN, [], before, limit)


def after(item_id: int, limit: int = PAGE) -> list[ConversationItem]:
    """The items after `item_id`, oldest first, at most `limit`: the change
    feed the knowledge ingest follows by cursor."""
    limit = max(1, min(int(limit), PAGE * 5))
    with closing(_connect()) as conn:
        rows = conn.execute("SELECT * FROM conversation_items WHERE id > ? ORDER BY id LIMIT ?",
                            (int(item_id), limit)).fetchall()
    return [_item(r) for r in rows]


def thread(agent_session_id: str, before: int | None = None,
           limit: int = PAGE) -> list[ConversationItem]:
    """Everything to and from one agent, oldest first, the same way."""
    return _read(f"agent_session_id = ? AND {_SHOWN}", [agent_session_id], before, limit)


class Unproven(ValueError):
    """A message from a counterpart with no proof of who sent it."""


def inbound(kind: ItemKind, counterpart: Counterpart, ref: str, name: str, text: str,
            evidence: str, *, source_kind: str, source_id, subagent: str = "",
            **links) -> ConversationItem:
    """Record one message to him from a counterpart, in its thread, with the
    proof that admitted it: the one way in for every channel. Refused
    (Unproven) without a proof JARVIS checked (PROOFS), a counterpart or a
    source. Recorded once per source."""
    if str(kind) not in INBOUND_KINDS:
        raise ValueError(f"{kind} is not a message to him.")
    if evidence not in PROOFS:
        raise Unproven(f"No proof of who sent it ({evidence or 'none'}): it is not recorded.")
    if not ref or source_id in (None, ""):
        raise Unproven("A message to him names its counterpart and its source.")
    counterpart = Counterpart(counterpart)
    if counterpart is Counterpart.AGENT:
        links.setdefault("agent_session_id", ref)
        links.setdefault("agent_name", name)
    return append(ConversationItem.new(
        kind, text, counterpart_kind=counterpart, counterpart_ref=ref, sender_evidence=evidence,
        sender_subagent=subagent, source_kind=source_kind, source_id=source_id,
        detail={**links.pop("detail", {}), "name": name}, **links))


def outbound(kind: ItemKind, counterpart: Counterpart, ref: str, text: str,
             **links) -> ConversationItem:
    """Record one message from him to a counterpart, in its thread."""
    if str(kind) in INBOUND_KINDS:
        raise ValueError(f"{kind} is a message to him, not from him.")
    return append(ConversationItem.new(kind, text, counterpart_kind=Counterpart(counterpart),
                                       counterpart_ref=ref, **links))


def thread_of(counterpart: Counterpart, ref: str, before: int | None = None,
              limit: int = PAGE) -> list[ConversationItem]:
    """Everything to and from one counterpart, oldest first."""
    return _read("counterpart_kind = ? AND counterpart_ref = ?",
                 [str(Counterpart(counterpart)), str(ref)], before, limit)


@dataclass(frozen=True)
class Thread:
    counterpart_kind: str
    counterpart_ref: str
    name: str                 # the counterpart as its newest message named it
    latest: ConversationItem  # its newest item
    inbound: int              # how many messages from it are on record


def threads(limit: int = PAGE) -> list[Thread]:
    """Every thread with a message from its counterpart, newest first."""
    kinds = sorted(INBOUND_KINDS)
    with closing(_connect()) as conn:
        rows = conn.execute(
            f"SELECT counterpart_kind, counterpart_ref, MAX(id) AS last, "
            f"SUM(kind IN ({','.join('?' * len(kinds))})) AS inbound "
            f"FROM conversation_items WHERE counterpart_kind IS NOT NULL "
            f"GROUP BY counterpart_kind, counterpart_ref HAVING inbound > 0 "
            f"ORDER BY last DESC LIMIT ?", (*kinds, max(1, int(limit)))).fetchall()
        latest = {r["id"]: _item(r) for r in conn.execute(
            f"SELECT * FROM conversation_items WHERE id IN ({','.join('?' * len(rows))})",
            [r["last"] for r in rows])} if rows else {}
        named = {}
        for r in rows:
            n = conn.execute(
                "SELECT agent_name, detail FROM conversation_items WHERE counterpart_kind=? AND "
                "counterpart_ref=? AND (agent_name IS NOT NULL OR detail LIKE '%\"name\"%') "
                "ORDER BY id DESC LIMIT 1", (r["counterpart_kind"], r["counterpart_ref"])).fetchone()
            named[(r["counterpart_kind"], r["counterpart_ref"])] = (
                (json.loads(n["detail"] or "{}").get("name") or n["agent_name"] or "") if n else "")
    return [Thread(r["counterpart_kind"], r["counterpart_ref"],
                   named[(r["counterpart_kind"], r["counterpart_ref"])], latest[r["last"]],
                   int(r["inbound"])) for r in rows]


def capture_events(limit: int = 200) -> list[ConversationItem]:
    """The latest capture notices (cleared, merged, restored, sent), newest
    last: what list_captures reads the recently cleared ones from."""
    with closing(_connect()) as conn:
        rows = conn.execute(
            "SELECT * FROM conversation_items WHERE kind='notice' AND capture_id IS NOT NULL "
            "ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [_item(r) for r in reversed(rows)]


def cleared_capture(capture_id: int, notice_id: int | None = None
                    ) -> tuple[ConversationItem, ConversationItem] | None:
    """(the notice that cleared capture `capture_id`, the capture as it last
    was before it): the clearing named by `notice_id`, else the latest one.
    The capture is found before its own clearing, never by number alone:
    capture numbers start again at 1 after a restart."""
    with closing(_connect()) as conn:
        if notice_id is not None:
            notice = conn.execute("SELECT * FROM conversation_items WHERE id=? AND "
                                  "kind='notice' AND capture_id=?",
                                  (notice_id, capture_id)).fetchone()
        else:
            notice = conn.execute(
                "SELECT * FROM conversation_items WHERE kind='notice' AND capture_id=? "
                "AND detail LIKE '%\"capture_event\": \"cleared\"%' ORDER BY id DESC LIMIT 1",
                (capture_id,)).fetchone()
        if notice is None or json.loads(notice["detail"] or "{}").get("capture_event") != "cleared":
            return None
        capture = conn.execute(
            "SELECT * FROM conversation_items WHERE kind='capture' AND capture_id=? AND id<? "
            "ORDER BY id DESC LIMIT 1", (capture_id, notice["id"])).fetchone()
    return (_item(notice), _item(capture)) if capture else None


def statuses(items: list[ConversationItem]) -> dict[int, "run_store.Message"]:
    """The messages these items point at, read now: each message's live
    status and whether it has an answer."""
    return run_store.get_messages(i.message_id for i in items if i.message_id is not None)
