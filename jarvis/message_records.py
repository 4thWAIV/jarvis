"""Messages to and from agents and the conversation's turns, as documents
(round 3, `records-as-documents`, of docs/planning/knowledge-engine/plan-uds.md).

Two document types, shared by `run_store` (a message's own state) and
`conversation_log` (the conversation):

- `message`, one message to or from him, from staged to queued, sent,
  delivered, answered and read: its `messages` row (`message`, what JARVIS
  sent a session and how far it got), or its `session_messages` row
  (`session_message`, a message a session dropped as a file), and its
  conversation-log items (`items`: the TO_AGENT that recorded it, the
  agent's ANSWER, a FROM_AGENT or a MESSAGE_IN), each a version as it moves.
  One Channel's "every message to and from Tim in one record": a message
  and its conversation item are the same document. At
  `/agents/<session id>/messages/<id>.message` for an agent; a message from
  any other counterpart (a work item, a package, a person, a workflow) at
  `/messages/<counterpart kind>/<ref>/<id>.message`. The id is `m<messages
  id>`, `s<session_messages id>`, or `c<item id>` for a message that is
  only its item.
- `turn`, one item of what Tim and JARVIS said (heard, typed, capture,
  jarvis, notice) at `/conversation/<local date>/<item id>.turn`.

Ids stay numbers, as callers know them: the next item, message and session
message ids are counters at `/conversation/counters/<table>.counter`, read
and moved in the same unit as the write that takes one, so two writers never
take the same id. Every column keeps its name; a NULL is a field left out;
an item's `detail` is its JSON as an object (and its exact text beside it,
`detail_text`, whenever the object alone would not give the same text back).
"""
from __future__ import annotations

import json
import time
from typing import Any, Callable, Iterable, Iterator, Optional

from jarvis import records

MESSAGE_COLUMNS = (
    "id", "session_id", "provider", "agent_name", "project", "capture_id", "text", "status",
    "staged_at", "sent_at", "delivered_at", "started_at", "answered_at", "reply", "failure")
SESSION_MESSAGE_COLUMNS = (
    "id", "project", "agent_name", "subject", "body", "received_at", "read_at",
    "sender_session", "sender_evidence", "sender_subagent")
ITEM_COLUMNS = (
    "id", "at", "kind", "text", "agent_session_id", "agent_name", "message_id",
    "session_message_id", "capture_id", "detail", "counterpart_kind", "counterpart_ref",
    "sender_evidence", "sender_subagent", "source_kind", "source_id", "sender", "sender_name",
    "about", "carries", "state", "state_at")

TURN_KINDS = frozenset({"heard", "typed", "capture", "jarvis", "notice"})
"""The items that are turns: what Tim and JARVIS said. Every other item is
part of a message document."""
ONCE_PER_MESSAGE = frozenset({"to_agent", "answer"})

TURN = "turn"
MESSAGE = "message"
COUNTERS = "/conversation/counters"
TURNS_UNDER = "/conversation"
MESSAGES_UNDER = ("/agents", "/messages")
IN_LIST_MAX = 256           # a window of ids up to this wide is asked for as a list


# -- cells ----------------------------------------------------------------------

def _cell(value: Any) -> Any:
    from jarvis.knowledge_move import cell
    return cell(value)


def _uncell(value: Any) -> Any:
    from jarvis.knowledge_move import uncell
    return uncell(value)


def pack(row: dict, columns: Iterable[str]) -> dict:
    """A row as document fields: every column by its name, NULLs left out."""
    return {c: _cell(row[c]) for c in columns if c in row and row[c] is not None}


def unpack(data: Optional[dict], columns: Iterable[str]) -> dict:
    data = data or {}
    return {c: _uncell(data[c]) if c in data else None for c in columns}


def _plain(obj: Any) -> bool:
    """JSON the engine keeps as it is: no names it reserves (`_...`)."""
    if isinstance(obj, dict):
        return all(isinstance(k, str) and not k.startswith("_") and _plain(v) for k, v in obj.items())
    if isinstance(obj, list):
        return all(_plain(v) for v in obj)
    return True


def _canonical(obj: Any) -> Optional[str]:
    try:
        return json.dumps(obj, sort_keys=True, allow_nan=False)
    except (TypeError, ValueError):
        return None


def pack_item(row: dict) -> dict:
    """A conversation item's row as fields: its detail an object, with its
    exact text beside it when the object alone would not give it back."""
    out = pack({k: v for k, v in row.items() if k != "detail"}, ITEM_COLUMNS)
    text = row.get("detail")
    obj = None
    if isinstance(text, str):
        try:
            obj = json.loads(text)
        except ValueError:
            obj = None
    if isinstance(obj, dict) and _plain(obj) and _canonical(obj) is not None:
        out["detail"] = obj
        if _canonical(obj) != text:
            out["detail_text"] = _cell(text)
    elif text is not None:
        out["detail_text"] = _cell(text)
    return out


def unpack_item(data: dict, columns: Iterable[str] = ITEM_COLUMNS) -> dict:
    """An item's fields back as its row in `columns` (detail as its stored
    text)."""
    row = unpack({k: v for k, v in data.items() if k not in ("detail", "detail_text")}, columns)
    if "detail_text" in data:
        row["detail"] = _uncell(data["detail_text"])
    elif "detail" in data:
        row["detail"] = _canonical(data["detail"])
    return row


def item_detail(row: dict) -> dict:
    try:
        found = json.loads(row.get("detail") or "{}")
    except (TypeError, ValueError):
        return {}
    return found if isinstance(found, dict) else {}


# -- paths ------------------------------------------------------------------------

def turn_path(item_id: int, at: float) -> str:
    try:
        day = time.strftime("%Y-%m-%d", time.localtime(float(at)))
    except (TypeError, ValueError, OverflowError, OSError):
        day = "undated"
    return f"{TURNS_UNDER}/{day}/{records.segment(item_id)}.{TURN}"


def message_path(doc_id: str, counterpart_kind: Optional[str], ref: Optional[str]) -> str:
    if counterpart_kind == "agent" and ref:
        return f"/agents/{records.segment(ref)}/messages/{records.segment(doc_id)}.{MESSAGE}"
    return (f"/messages/{records.segment(counterpart_kind or 'none')}/"
            f"{records.segment(ref or 'none')}/{records.segment(doc_id)}.{MESSAGE}")


def counter_path(table: str) -> str:
    return f"{COUNTERS}/{records.segment(table)}.counter"


def placement(row: dict) -> tuple[str, Any]:
    """Which document an item belongs to: ("turn", id), ("m", messages id),
    ("s", session_messages id) or ("c", its own id)."""
    kind = row.get("kind")
    if kind in TURN_KINDS or not isinstance(kind, str):
        return "turn", row["id"]
    if kind in ONCE_PER_MESSAGE and row.get("message_id") is not None:
        return "m", row["message_id"]
    if row.get("session_message_id") is not None:
        return "s", row["session_message_id"]
    return "c", row["id"]


def doc_id(how: str, number: Any) -> str:
    return f"{how}{number}"


def message_doc_path(data: dict, how: str, number: Any) -> str:
    """Where a message document lives: its messages row's session, else the
    counterpart of its first item, else the session that sent it."""
    m = data.get("message")
    if m is not None:
        return message_path(doc_id(how, number), "agent", m.get("session_id"))
    for item in data.get("items") or []:
        if item.get("counterpart_kind"):
            return message_path(doc_id(how, number), item.get("counterpart_kind"),
                                item.get("counterpart_ref"))
        if item.get("agent_session_id"):
            return message_path(doc_id(how, number), "agent", item.get("agent_session_id"))
    s = data.get("session_message")
    if s is not None and s.get("sender_session"):
        return message_path(doc_id(how, number), "agent", s.get("sender_session"))
    return message_path(doc_id(how, number), None, None)


# -- reading ------------------------------------------------------------------------

def turns(where: list, unit: Optional[records.Unit] = None) -> list[records.Rec]:
    if unit is not None:
        return unit.scan(TURNS_UNDER, types=[TURN], where=where)
    return records.all_of(TURNS_UNDER, types=[TURN], where=where)


def message_docs(where: list, unit: Optional[records.Unit] = None) -> list[records.Rec]:
    out: list[records.Rec] = []
    for under in MESSAGES_UNDER:
        if unit is not None:
            out += unit.scan(under, types=[MESSAGE], where=where)
        else:
            out += records.all_of(under, types=[MESSAGE], where=where)
    return out


def doc_items(rec: records.Rec) -> list[dict]:
    return [unpack_item(i) for i in rec.data.get("items") or []]


def window(field: str, lo: int, hi: int) -> list:
    """`lo <= field < hi` as conditions the index answers."""
    if hi - lo <= IN_LIST_MAX:
        return [[field, "in", list(range(lo, hi))]]
    return [[field, ">=", lo], [field, "<", hi]]


def find_items(test: Callable[[dict], bool], turn_where: Optional[list],
               message_where: Optional[list], *, lo: Optional[int] = None,
               hi: Optional[int] = None) -> list[dict]:
    """Every item (as its row) passing `test` with `lo <= id < hi`, from the
    turns matching `turn_where` and the message documents matching
    `message_where` (None: not that kind of document)."""
    def bounded(row: dict) -> bool:
        i = row.get("id")
        if not isinstance(i, int):
            return False
        return (lo is None or i >= lo) and (hi is None or i < hi) and test(row)

    out: list[dict] = []
    span_t: list = []
    span_m: list = []
    if lo is not None and hi is not None:
        span_t, span_m = window("id", lo, hi), window("items[].id", lo, hi)
    elif hi is not None:
        span_t, span_m = [["id", "<", hi]], [["items[].id", "<", hi]]
    elif lo is not None:
        span_t, span_m = [["id", ">=", lo]], [["items[].id", ">=", lo]]
    if turn_where is not None:
        for rec in turns(turn_where + span_t):
            row = unpack_item(rec.data)
            if bounded(row):
                out.append(row)
    if message_where is not None:
        for rec in message_docs(message_where + span_m):
            out += [row for row in doc_items(rec) if bounded(row)]
    return out


def next_value(table: str) -> Optional[int]:
    rec = records.get(counter_path(table))
    return None if rec is None else int(rec.data["next"])


def take_id(unit: records.Unit, table: str, fallback: Callable[[], int]) -> int:
    """The next id of `table`, taken in this unit: the counter is read
    (a condition of the write) and moved on."""
    rec = unit.get(counter_path(table))
    number = int(rec.data["next"]) if rec is not None else fallback()
    unit.put(counter_path(table), {"table": table, "next": number + 1, "events": []},
             change="counter", summary=f"{table} id {number} taken")
    return number


def last_item_id() -> int:
    """The largest item id on record (0: none), for a store with no counter."""
    best = 0
    for rec in records.scan(TURNS_UNDER, types=[TURN], order=("id", "desc"), limit=1):
        best = max(best, int(rec.data.get("id") or 0))
    for under in MESSAGES_UNDER:
        for rec in records.scan(under, types=[MESSAGE], where=[["items[].id", "exists", True]]):
            for item in rec.data.get("items") or []:
                if isinstance(item.get("id"), int):
                    best = max(best, item["id"])
    return best


def last_item_id_plus_one() -> int:
    return last_item_id() + 1


def last_message_id() -> int:
    best = 0
    for under in MESSAGES_UNDER:
        for rec in records.scan(under, types=[MESSAGE], where=[["message.id", "exists", True]],
                                order=("message.id", "desc"), limit=1):
            best = max(best, int(rec.data["message"]["id"]))
    return best


def next_item_id() -> int:
    """One past the largest item id: where a read of the newest begins."""
    found = next_value("conversation_items")
    return found if found is not None else last_item_id() + 1


# -- the move: SQLite rows to documents and back ------------------------------------

def _rows_of(conn, table: str) -> list[dict]:
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                    (table,)).fetchone() is None:
        return []
    cur = conn.execute(f'SELECT * FROM "{table}" ORDER BY id')
    names = [d[0] for d in cur.description]
    return [dict(zip(names, r)) for r in cur]


def _sequence(conn, table: str) -> int:
    try:
        row = conn.execute("SELECT seq FROM sqlite_sequence WHERE name=?", (table,)).fetchone()
    except Exception:
        return 0
    return int(row[0]) if row and isinstance(row[0], int) else 0


def build_documents(messages: list[dict], session_messages: list[dict],
                    items: list[dict]) -> Iterator[tuple[str, dict]]:
    """(path, data) for every turn and message document the rows make:
    the same shapes the stores write."""
    docs: dict[tuple[str, Any], dict] = {}
    for m in messages:
        docs[("m", m["id"])] = {"message": pack(m, MESSAGE_COLUMNS), "items": [], "events": []}
    for s in session_messages:
        docs[("s", s["id"])] = {"session_message": pack(s, SESSION_MESSAGE_COLUMNS), "items": [],
                                "events": []}
    for row in items:
        how, number = placement(row)
        if how == "turn":
            data = pack_item(row)
            data["events"] = []
            yield turn_path(row["id"], row["at"]), data
            continue
        docs.setdefault((how, number), {"items": [], "events": []})["items"].append(pack_item(row))
    for (how, number), data in docs.items():
        data["items"].sort(key=lambda i: i.get("id") if isinstance(i.get("id"), int) else 0)
        yield message_doc_path(data, how, number), data


class ConversationMapping:
    """The `messages`, `session_messages` and `conversation_items` tables as
    message and turn documents, and back (knowledge_move's Mapping)."""

    name = "messages-and-turns"
    tables = ("messages", "session_messages", "conversation_items")
    under = (TURNS_UNDER,) + MESSAGES_UNDER
    history = False

    def documents(self, conn) -> Iterator:
        from jarvis.knowledge_move import Doc
        messages = _rows_of(conn, "messages")
        session_messages = _rows_of(conn, "session_messages")
        items = _rows_of(conn, "conversation_items")
        for path, data in build_documents(messages, session_messages, items):
            yield Doc(path, data)
        for table, rows in (("messages", messages), ("session_messages", session_messages),
                            ("conversation_items", items)):
            top = max([r["id"] for r in rows if isinstance(r["id"], int)] + [_sequence(conn, table)])
            yield Doc(counter_path(table), {"table": table, "next": top + 1, "events": []})

    def rows(self, docs, shape=None) -> dict:
        """The rows back in the file's own shape (`shape`): an older file's
        conversation_items has fewer columns (conversation_schema 2 has 16)."""
        from jarvis.knowledge_move import TODAY, Row
        shape = shape or TODAY
        item_cols = shape.columns("conversation_items", ITEM_COLUMNS)
        message_cols = shape.columns("messages", MESSAGE_COLUMNS)
        session_cols = shape.columns("session_messages", SESSION_MESSAGE_COLUMNS)
        out: dict[str, list] = {t: [] for t in self.tables}
        for doc in docs:
            path, data = doc.path, doc.data
            if path.endswith("." + TURN) and path.startswith(TURNS_UNDER + "/"):
                out["conversation_items"].append(Row(unpack_item(data, item_cols)))
            elif path.endswith("." + MESSAGE):
                if "message" in data:
                    out["messages"].append(Row(unpack(data["message"], message_cols)))
                if "session_message" in data:
                    out["session_messages"].append(Row(unpack(data["session_message"], session_cols)))
                for item in data.get("items") or []:
                    out["conversation_items"].append(Row(unpack_item(item, item_cols)))
        return shape.fit(out)
