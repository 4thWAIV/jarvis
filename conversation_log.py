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

`append` is the only writer. Whoever wants to know an item was added (the
server, to tell the pages to read again) is `connect`ed; it is
told after the row is committed. Items that hold an agent's words (FROM_AGENT,
ANSWER, and text the brain composed out of them) are another session's words:
the brain reads them only inside an untrusted block.
"""

from __future__ import annotations

import json
import sqlite3
import time
from contextlib import closing
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Callable

import run_store


class ItemKind(StrEnum):
    HEARD = "heard"          # what Tim said
    TYPED = "typed"          # what Tim typed in the white box
    JARVIS = "jarvis"        # what JARVIS said
    CAPTURE = "capture"      # a capture made of Tim's words
    TO_AGENT = "to_agent"    # a message sent (or queued) to an agent
    FROM_AGENT = "from_agent"  # a message an agent sent Tim
    ANSWER = "answer"        # an agent's answer to one of Tim's messages
    NOTICE = "notice"        # delivered, not picked up, workflow updates


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

    @classmethod
    def new(cls, kind: ItemKind, text: str, *, agent_session_id: str | None = None,
            agent_name: str | None = None, message_id: int | None = None,
            session_message_id: int | None = None, capture_id: int | None = None,
            detail: dict | None = None) -> "ConversationItem":
        """An item not yet appended: `append` gives it its id and time."""
        return cls(0, 0.0, ItemKind(kind), text, agent_session_id, agent_name,
                   message_id, session_message_id, capture_id, dict(detail or {}))


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

_listener: Callable[[ConversationItem], None] | None = None


def connect(listener: Callable[[ConversationItem], None] | None) -> None:
    """Who is told of every item once it is stored: the server, which tells
    the pages to read again. One listener; a listener that raises is ignored,
    because the record is what matters."""
    global _listener
    _listener = listener


def _connect() -> sqlite3.Connection:
    conn = run_store._connect()
    conn.executescript(_SCHEMA)
    return conn


def _item(row) -> ConversationItem:
    return ConversationItem(row["id"], row["at"], ItemKind(row["kind"]), row["text"],
                            row["agent_session_id"], row["agent_name"], row["message_id"],
                            row["session_message_id"], row["capture_id"],
                            json.loads(row["detail"] or "{}"))


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
            "agent_name, message_id, session_message_id, capture_id, detail) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (stored.at, str(stored.kind), stored.text, stored.agent_session_id,
             stored.agent_name, stored.message_id, stored.session_message_id,
             stored.capture_id, json.dumps(stored.detail)))
        conn.commit()
        if cur.rowcount:
            stored = replace(stored, id=cur.lastrowid)
            new = True
        else:
            row = conn.execute(
                "SELECT * FROM conversation_items WHERE (kind=? AND message_id=?) "
                "OR (session_message_id IS NOT NULL AND session_message_id=?)",
                (str(stored.kind), stored.message_id, stored.session_message_id)).fetchone()
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
    `before` (the newest when None)."""
    return _read("1=1", [], before, limit)


def thread(agent_session_id: str, before: int | None = None,
           limit: int = PAGE) -> list[ConversationItem]:
    """Everything to and from one agent, oldest first, the same way."""
    return _read("agent_session_id = ?", [agent_session_id], before, limit)


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
