"""The conversation bus: one durable record of the conversation, messages
to and from agents included.

Every item of the conversation is appended here first and only then shown:
what Tim said (heard or typed), what JARVIS said, each capture, each message
to an agent, each message an agent sent him, each agent's answer, and
JARVIS's notices (delivered, not picked up, workflow updates). The page's
history and each agent's thread are read back from here, so they survive a
reload, a restart and a second tab, and page back without limit.

The record is documents (message_records.py): what Tim and JARVIS said
(heard, typed, captures, JARVIS's words, notices) is one `turn` each, at
/conversation/<date>/<id>.turn; a message to or from a counterpart is one
`message` document holding its items, so a message JARVIS sent an agent,
its TO_AGENT item and the agent's ANSWER are one record
(/agents/<session id>/messages/m<id>.message). A thread, the conversation
and the brain's recall are queries over both, by the items' own fields and
ids: an item's id still orders everything and pages back.

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

Every message to him is also one thing (one_channel round 1,
docs/planning/one-channel/messages-by-work-item.md): from (`sender`, the
proven sender as a principal, and `sender_name`), about (`about`, the Work
item it belongs to, or none), says (`text`), carries (`carries`, the package
inbox row it brings, or none) and state (`state`: new, read, answered, done,
moved only forward by `mark`). `inbound` writes every one new; the items on
record before it have no state and are no message. `work_threads`,
`work_thread` and `general` read them.

`append` is the only writer of items and `mark` of a message's state.
Whoever wants to know an item was added (the
server, to tell the pages to read again) is `connect`ed; it is
told after the write has landed. Items that hold an agent's words (FROM_AGENT,
ANSWER, and text the brain composed out of them) are another session's words:
the brain reads them only inside an untrusted block.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Callable

from jarvis import message_records, records, run_store


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
    "agent_signature",       # an onboarded agent's key signed it (server.PROVEN_BY_SIGNATURE)
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
    SIGNED = "signed"                    # POST /api/messages: the message's own id


# Each source's update in the attention record (attention_store: kind, and
# the event that is the message), and whether its source_id is the update's
# source with a ":<part>" after it (one board rewrite, one decision key):
# then the update is about its newest message.
UPDATES: dict[Source, tuple[str, str, bool]] = {
    Source.MESSAGE: ("agent_message", "answered", False),
    Source.SESSION_MESSAGE: ("session_message", "arrived", False),
    Source.NEEDS_BOARD: ("needs_board", "changed", True),
    Source.WORKFLOW_DECISION: ("workflow", "decision_needed", True),
    Source.WORK_DECISION: ("work", "decision_needed", True),
    Source.PACKAGE: ("package", "arrived", False),
    Source.PERSON_MESSAGE: ("arrival", "arrived", False),
    Source.SIGNED: ("message", "arrived", False),
}


class MessageState(StrEnum):
    """Where a message to him stands, in order: each `mark` only moves it on."""
    NEW = "new"              # not yet read
    READ = "read"            # heard in full, or marked read
    ANSWERED = "answered"    # his reply went, or his answer is recorded
    DONE = "done"            # nothing more is wanted: signed off, set aside, closed


_STATE_ORDER = {s: i for i, s in enumerate(MessageState)}

# A sender as a principal (reporters.py's forms, and these): who it is from.
SENDER_AGENT = "agent:"
SENDER_PERSON = "person:"


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
    # A message to him (one_channel round 1): from, about, carries, state.
    sender: str | None = None        # the proven sender: agent:<session>, person:<id>, key:<label>
    sender_name: str | None = None   # the sender as it was named when it arrived
    about: str | None = None         # the Work item it belongs to (also on his reply to it)
    carries: str | None = None       # the package inbox row it brings
    state: str | None = None         # MessageState; None for any other item
    state_at: float | None = None

    @classmethod
    def new(cls, kind: ItemKind, text: str, *, agent_session_id: str | None = None,
            agent_name: str | None = None, message_id: int | None = None,
            session_message_id: int | None = None, capture_id: int | None = None,
            detail: dict | None = None, counterpart_kind: str | None = None,
            counterpart_ref: str | None = None, sender_evidence: str | None = None,
            sender_subagent: str | None = None, source_kind: str | None = None,
            source_id: str | None = None, sender: str | None = None,
            sender_name: str | None = None, about: str | None = None,
            carries: str | None = None, state: str | None = None) -> "ConversationItem":
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
                   str(source_id) if source_id is not None else None, sender or None,
                   sender_name or None, str(about) if about else None,
                   str(carries) if carries else None,
                   str(MessageState(state)) if state else None)

    @property
    def inbound(self) -> bool:
        """A message from its counterpart to him."""
        return str(self.kind) in INBOUND_KINDS

    @property
    def is_message(self) -> bool:
        """One of his messages (one_channel round 1): a message from a
        counterpart recorded with a state."""
        return self.inbound and self.state is not None


TEXT_MAX = 20000
PAGE = 100

MAPPINGS = [message_records.ConversationMapping()]
"""The move's mapping (knowledge_move): `messages`, `session_messages`
and `conversation_items` as message and turn documents, and back."""

_listener: Callable[[ConversationItem], None] | None = None


def connect(listener: Callable[[ConversationItem], None] | None) -> None:
    """Who is told of every item once it is stored: the server, which tells
    the pages to read again. One listener; a listener that raises is ignored,
    because the record is what matters."""
    global _listener
    _listener = listener


def _tell(item: ConversationItem) -> None:
    if _listener is not None:
        try:
            _listener(item)
        except Exception:
            pass


def _item(row: dict) -> ConversationItem:
    return ConversationItem(row["id"], row["at"], ItemKind(row["kind"]), row["text"],
                            row["agent_session_id"], row["agent_name"], row["message_id"],
                            row["session_message_id"], row["capture_id"],
                            message_records.item_detail(row), row["counterpart_kind"],
                            row["counterpart_ref"], row["sender_evidence"],
                            row["sender_subagent"], row["source_kind"], row["source_id"],
                            row["sender"], row["sender_name"], row["about"], row["carries"],
                            row["state"], row["state_at"])


def _row(item: ConversationItem) -> dict:
    return {"id": item.id, "at": item.at, "kind": str(item.kind), "text": item.text,
            "agent_session_id": item.agent_session_id, "agent_name": item.agent_name,
            "message_id": item.message_id, "session_message_id": item.session_message_id,
            "capture_id": item.capture_id, "detail": json.dumps(item.detail),
            "counterpart_kind": item.counterpart_kind, "counterpart_ref": item.counterpart_ref,
            "sender_evidence": item.sender_evidence, "sender_subagent": item.sender_subagent,
            "source_kind": item.source_kind, "source_id": item.source_id, "sender": item.sender,
            "sender_name": item.sender_name, "about": item.about, "carries": item.carries,
            "state": item.state, "state_at": item.state_at}


# -- finding items ------------------------------------------------------------------
#
# A turn holds one item at its top level; a message document holds its items
# in `items`. Every read names its conditions both ways (a turn's `field`, a
# message's `items[].field`), takes only items between two ids, and checks
# every item again here: a document matches when any of its items does, and
# the engine compares text folded.

def _conditions(where: list) -> tuple[list, list]:
    return list(where), [[f"items[].{f}", op, v] for f, op, v in where]


def _found(test: Callable[[dict], bool], where: list, *, turns: bool = True,
           messages: bool = True, lo: int | None = None, hi: int | None = None) -> list[dict]:
    turn_where, message_where = _conditions(where)
    return message_records.find_items(test, turn_where if turns else None,
                                      message_where if messages else None, lo=lo, hi=hi)


def _newest(test: Callable[[dict], bool], where: list, before: int | None, limit: int, *,
            narrow: bool, turns: bool = True, messages: bool = True) -> list[ConversationItem]:
    """The newest `limit` items passing `test` before item `before`, oldest
    first. `narrow`: `where` names few items (an agent, a Work item), so
    they are read at once; else looked for back from the newest a widening
    window of ids at a time."""
    limit = max(1, min(int(limit), PAGE * 5))
    hi = int(before) if before is not None else message_records.next_item_id()
    if narrow:
        found = _found(test, where, turns=turns, messages=messages, hi=hi)
    else:
        found = []
        span = max(64, limit * 2)
        top = hi
        while top > 0 and len(found) < limit:
            lo = max(0, top - span)
            found += _found(test, where, turns=turns, messages=messages, lo=lo, hi=top)
            top, span = lo, span * 4
    found.sort(key=lambda r: r["id"], reverse=True)
    return [_item(r) for r in reversed(found[:limit])]


def _by_id(item_id: int, unit: records.Unit | None = None):
    """(the record holding item `item_id`, its row), or None."""
    item_id = int(item_id)
    for rec in message_records.turns([["id", "=", item_id]], unit):
        if rec.data.get("id") == item_id:
            return rec, message_records.unpack_item(rec.data)
    for rec in message_records.message_docs([["items[].id", "=", item_id]], unit):
        for row in message_records.doc_items(rec):
            if row["id"] == item_id:
                return rec, row
    return None


def _not_shown(row: dict) -> bool:
    return row["kind"] != str(ItemKind.MESSAGE_IN)


# -- writing ------------------------------------------------------------------------

def _clash(unit: records.Unit, row: dict) -> dict | None:
    """An item already on record for the same message (once per message
    sent, per answer, per session message, per message from one source)."""
    kind = row["kind"]
    if kind in message_records.ONCE_PER_MESSAGE and row["message_id"] is not None:
        for rec in message_records.message_docs([["items[].message_id", "=", row["message_id"]]],
                                                unit):
            for found in message_records.doc_items(rec):
                if found["kind"] == kind and found["message_id"] == row["message_id"]:
                    return found
    if row["session_message_id"] is not None:
        where = [["session_message_id", "=", row["session_message_id"]]]
        for found in _found(lambda r: r["session_message_id"] == row["session_message_id"],
                            where, turns=True, messages=True):
            return found
    if kind == str(ItemKind.MESSAGE_IN) and row["source_kind"] is not None \
            and row["source_id"] is not None:
        where = [["source_id", "=", row["source_id"]]]
        for rec in message_records.message_docs(_conditions(where)[1], unit):
            for found in message_records.doc_items(rec):
                if (found["kind"], found["source_kind"], found["source_id"]) == \
                        (kind, row["source_kind"], row["source_id"]):
                    return found
    return None


def _message_doc_for(unit: records.Unit, how: str, number) -> records.Rec | None:
    """The message document an item of a message joins: the one holding
    its messages (or session_messages) row, else one made of items of it."""
    field = "message" if how == "m" else "session_message"
    for rec in message_records.message_docs([[f"{field}.id", "=", number]], unit):
        held = rec.data.get(field)
        if held is not None and held.get("id") == number:
            return rec
    item_field = "message_id" if how == "m" else "session_message_id"
    for rec in message_records.message_docs([[f"items[].{item_field}", "=", number]], unit):
        if rec.data.get(field) is None and any(
                message_records.placement(r) == (how, number)
                for r in message_records.doc_items(rec)):
            return rec
    return None


def _writer(row: dict) -> records.Caller:
    if row["kind"] in (str(ItemKind.HEARD), str(ItemKind.TYPED)):
        return records.TIM
    if row["kind"] in INBOUND_KINDS and row.get("sender"):
        return records.by(row["sender"])
    return records.JARVIS


def append(item: ConversationItem) -> ConversationItem:
    """Store one item (the only writer), then tell the listeners. An item
    already recorded for the same message is not recorded again; the one
    on record comes back. Text is kept whole up to TEXT_MAX."""
    text = str(item.text or "").strip()[:TEXT_MAX]
    if not text:
        raise ValueError("A conversation item has words.")

    def store(unit: records.Unit):
        now = time.time()
        stored = replace(item, text=text, at=now, state_at=now if item.state else None)
        row = _row(stored)
        found = _clash(unit, row)
        if found is not None:
            return _item(found), False
        item_id = message_records.take_id(unit, "conversation_items",
                                          message_records.last_item_id_plus_one)
        stored = replace(stored, id=item_id)
        row["id"] = item_id
        packed = message_records.pack_item(row)
        how, number = message_records.placement(row)
        summary = f"{row['kind']} {item_id}"
        if how == "turn":
            unit.put(message_records.turn_path(item_id, now), {**packed, "events": []},
                     change=row["kind"], summary=summary)
        else:
            rec = _message_doc_for(unit, how, number) if how in ("m", "s") else None
            if rec is not None:
                data = dict(rec.data, items=[*rec.data.get("items", []), packed], events=[])
                data["items"].sort(key=lambda i: i.get("id") or 0)
                unit.put(rec.path, data, change=row["kind"], summary=summary)
            else:
                data = {"items": [packed], "events": []}
                unit.put(message_records.message_doc_path(data, how, number), data,
                         change=row["kind"], summary=summary)
        unit.after(lambda: _tell(stored))
        return stored, True

    found, _ = records.atomic(store, by=_writer(_row(item)))
    return found


def page(before: int | None = None, limit: int = PAGE) -> list[ConversationItem]:
    """The conversation, oldest first: the last `limit` items before item
    `before` (the newest when None). Messages from the other channels
    (MESSAGE_IN) are in their threads (`thread_of`), not here yet."""
    return _newest(_not_shown, [], before, limit, narrow=False)


def after(item_id: int, limit: int = PAGE) -> list[ConversationItem]:
    """The items after `item_id`, oldest first, at most `limit`: the change
    feed the knowledge ingest follows by cursor."""
    limit = max(1, min(int(limit), PAGE * 5))
    end = message_records.next_item_id()
    found: list[dict] = []
    lo, span = int(item_id) + 1, max(64, limit * 2)
    while lo < end and len(found) < limit:
        hi = min(end, lo + span)
        found += _found(lambda r: True, [], lo=lo, hi=hi)
        lo, span = hi, span * 4
    found.sort(key=lambda r: r["id"])
    return [_item(r) for r in found[:limit]]


def thread(agent_session_id: str, before: int | None = None,
           limit: int = PAGE) -> list[ConversationItem]:
    """Everything to and from one agent, oldest first, the same way."""
    return _newest(lambda r: r["agent_session_id"] == agent_session_id and _not_shown(r),
                   [["agent_session_id", "=", agent_session_id]], before, limit, narrow=True)


class Unproven(ValueError):
    """A message from a counterpart with no proof of who sent it."""


def inbound(kind: ItemKind, counterpart: Counterpart, ref: str, name: str, text: str,
            evidence: str, *, source_kind: str, source_id, subagent: str = "",
            sender: str = "", sender_name: str = "", about: str = "", carries: str = "",
            **links) -> ConversationItem:
    """Record one message to him from a counterpart, in its thread, with the
    proof that admitted it: the one way in for every channel. Refused
    (Unproven) without a proof JARVIS checked (PROOFS), a counterpart or a
    source. Recorded once per source, as a new message: from `sender` (an
    agent's or a person's thread is its sender unless told otherwise),
    about the Work item `about`, carrying the package inbox row `carries`."""
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
        sender, sender_name = sender or f"{SENDER_AGENT}{ref}", sender_name or name
    elif counterpart is Counterpart.PERSON:
        sender, sender_name = sender or f"{SENDER_PERSON}{ref}", sender_name or name
    return append(ConversationItem.new(
        kind, text, counterpart_kind=counterpart, counterpart_ref=ref, sender_evidence=evidence,
        sender_subagent=subagent, source_kind=source_kind, source_id=source_id,
        detail={**links.pop("detail", {}), "name": name}, sender=sender,
        sender_name=sender_name, about=about, carries=carries, state=MessageState.NEW,
        **links))


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
    kind, ref = str(Counterpart(counterpart)), str(ref)
    return _newest(lambda r: r["counterpart_kind"] == kind and r["counterpart_ref"] == ref,
                   [["counterpart_ref", "=", ref]], before, limit, narrow=True)


@dataclass(frozen=True)
class Thread:
    counterpart_kind: str
    counterpart_ref: str
    name: str                 # the counterpart as its newest message named it
    latest: ConversationItem  # its newest item
    inbound: int              # how many messages from it are on record


def threads(limit: int = PAGE) -> list[Thread]:
    """Every thread with a message from its counterpart, newest first."""
    inbound_kinds = sorted(INBOUND_KINDS)
    counted: dict[tuple[str, str], int] = {}
    for row in _found(lambda r: r["kind"] in INBOUND_KINDS and r["counterpart_kind"] is not None,
                      [["kind", "in", inbound_kinds]], turns=False):
        key = (row["counterpart_kind"], row["counterpart_ref"])
        counted[key] = counted.get(key, 0) + 1
    out = []
    for (kind, ref), n in counted.items():
        rows = _found(lambda r, k=kind, f=ref: (r["counterpart_kind"], r["counterpart_ref"]) == (k, f),
                      [["counterpart_ref", "=", ref]])
        rows.sort(key=lambda r: r["id"], reverse=True)
        name = ""
        for r in rows:
            if r["agent_name"] is not None or '"name"' in str(r["detail"] or "").lower():
                name = message_records.item_detail(r).get("name") or r["agent_name"] or ""
                break
        out.append(Thread(kind, ref, name, _item(rows[0]), n))
    out.sort(key=lambda t: t.latest.id, reverse=True)
    return out[:max(1, int(limit))]


def get(item_id: int) -> ConversationItem | None:
    """One item by its id, or None."""
    found = _by_id(item_id)
    return _item(found[1]) if found else None


def update_of(item: ConversationItem) -> tuple[str, str] | None:
    """The attention update (kind, source id) a message to him is, or None
    for an item that is no message from a counterpart."""
    if not item.inbound or item.source_kind not in UPDATES.keys() or item.source_id is None:
        return None
    kind, _, keyed = UPDATES[Source(item.source_kind)]
    return kind, item.source_id.rpartition(":")[0] if keyed else item.source_id


def message_for(kind: str, source_id: str, event: str) -> ConversationItem | None:
    """The message an attention update is about: the newest inbound item
    from its source, while the update's event is that message (a workflow
    update about a new phase is no message). None otherwise."""
    found = [(s, keyed) for s, (k, e, keyed) in UPDATES.items() if k == kind and e == event]
    if not found:
        return None
    source, keyed = found[0]
    source_id = str(source_id)
    if keyed:
        where = [["source_id", "prefix", f"{source_id}:"]]
        matches = lambda r: str(r["source_id"] or "").startswith(f"{source_id}:")  # noqa: E731
    else:
        where = [["source_id", "=", source_id]]
        matches = lambda r: r["source_id"] == source_id  # noqa: E731
    rows = _found(lambda r: r["source_kind"] == str(source) and matches(r)
                  and r["kind"] in INBOUND_KINDS, where)
    return _item(max(rows, key=lambda r: r["id"])) if rows else None


def capture_events(limit: int = 200) -> list[ConversationItem]:
    """The latest capture notices (cleared, merged, restored, sent), newest
    last: what list_captures reads the recently cleared ones from."""
    notice = str(ItemKind.NOTICE)
    return _newest(lambda r: r["kind"] == notice and r["capture_id"] is not None,
                   [["kind", "=", notice], ["capture_id", "exists", True]], None, limit,
                   narrow=False, messages=False)


def cleared_capture(capture_id: int, notice_id: int | None = None
                    ) -> tuple[ConversationItem, ConversationItem] | None:
    """(the notice that cleared capture `capture_id`, the capture as it last
    was before it): the clearing named by `notice_id`, else the latest one.
    The capture is found before its own clearing, never by number alone:
    capture numbers start again at 1 after a restart."""
    rows = _found(lambda r: r["capture_id"] == capture_id,
                  [["capture_id", "=", capture_id]], messages=False)
    notices = [r for r in rows if r["kind"] == str(ItemKind.NOTICE)]
    if notice_id is not None:
        notices = [r for r in notices if r["id"] == notice_id]
    else:
        notices = [r for r in notices
                   if message_records.item_detail(r).get("capture_event") == "cleared"]
    if not notices:
        return None
    notice = max(notices, key=lambda r: r["id"])
    if message_records.item_detail(notice).get("capture_event") != "cleared":
        return None
    captures = [r for r in rows if r["kind"] == str(ItemKind.CAPTURE) and r["id"] < notice["id"]]
    if not captures:
        return None
    return _item(notice), _item(max(captures, key=lambda r: r["id"]))


def statuses(items: list[ConversationItem]) -> dict[int, "run_store.Message"]:
    """The messages these items point at, read now: each message's live
    status and whether it has an answer."""
    return run_store.get_messages(i.message_id for i in items if i.message_id is not None)


# --- Messages to him (one_channel round 1) ---------------------------------

# A message is an item with a state (`inbound` gives every new one NEW).
def _is_message(row: dict) -> bool:
    return row["state"] is not None


_STATED = [["state", "exists", True]]


def mark(item_id: int, state: MessageState) -> ConversationItem | None:
    """Move one message on to `state` (the only writer of a message's
    state): only forward (new, read, answered, done), so a late Read never
    undoes his answer. The message as it now is, or None when `item_id` is
    no message. Tells the listener when it moved."""
    state = MessageState(state)

    def move(unit: records.Unit):
        found = _by_id(item_id, unit)
        if found is None or not _is_message(found[1]):
            return None, False
        rec, row = found
        if _STATE_ORDER[MessageState(row["state"])] >= _STATE_ORDER[state]:
            return _item(row), False
        row = {**row, "state": str(state), "state_at": time.time()}
        packed = message_records.pack_item(row)
        if rec.path.endswith("." + message_records.TURN):
            unit.put(rec.path, {**packed, "events": []}, change=str(state),
                     summary=f"item {row['id']} {state}")
        else:
            items = [packed if i.get("id") == row["id"] else i for i in rec.data.get("items", [])]
            unit.put(rec.path, dict(rec.data, items=items, events=[]), change=str(state),
                     summary=f"item {row['id']} {state}")
        moved = _item(row)
        unit.after(lambda: _tell(moved))
        return moved, True

    found, _ = records.atomic(move)
    return found


def messages_where(*, source: tuple[str, str] | None = None,
                   carries: str | None = None) -> list[int]:
    """The ids of the messages from one source (its kind and id) or
    carrying one package inbox row: what a `mark` of something settled
    elsewhere (a decision answered, a package signed off) moves on."""
    if source is not None:
        kind, ref = str(source[0]), str(source[1])
        rows = _found(lambda r: r["source_kind"] == kind and r["source_id"] == ref
                      and _is_message(r), [["source_id", "=", ref]])
    elif carries:
        rows = _found(lambda r: r["carries"] == str(carries) and _is_message(r),
                      [["carries", "=", str(carries)]])
    else:
        return []
    return sorted(r["id"] for r in rows)


@dataclass(frozen=True)
class WorkThread:
    """One Work item's messages, as the Inbox and the item's row count them."""
    work: str
    new: int                             # messages not yet read
    total: int                           # messages about it
    latest_at: float                     # its newest message's time
    first_new: ConversationItem | None   # its oldest unread message


def work_threads(limit: int = PAGE) -> list[WorkThread]:
    """Every Work item with messages about it, the one with the newest
    message first, each with its count of new ones and its first new one."""
    new = str(MessageState.NEW)
    groups: dict[str, list[dict]] = {}
    for row in _found(lambda r: r["about"] is not None and _is_message(r),
                      [["about", "exists", True], *_STATED]):
        groups.setdefault(row["about"], []).append(row)
    out = []
    for about, rows in groups.items():
        fresh = [r for r in rows if r["state"] == new]
        first = min(fresh, key=lambda r: r["id"]) if fresh else None
        out.append((max(r["id"] for r in rows), WorkThread(
            about, len(fresh), len(rows), float(max(r["at"] for r in rows)),
            _item(first) if first else None)))
    out.sort(key=lambda t: t[0], reverse=True)
    return [t for _, t in out[:max(1, int(limit))]]


def new_about(work_id: str) -> int:
    """How many messages about one Work item are new."""
    new = str(MessageState.NEW)
    return len(_found(lambda r: r["about"] == str(work_id) and r["state"] == new,
                      [["about", "=", str(work_id)], ["state", "=", new]]))


def work_thread(work_id: str, before: int | None = None,
                limit: int = PAGE) -> list[ConversationItem]:
    """Everything about one Work item, oldest first: the messages to him
    about it and what he sent back about them, as one conversation."""
    return _newest(lambda r: r["about"] == str(work_id), [["about", "=", str(work_id)]],
                   before, limit, narrow=True)


def _general(row: dict) -> bool:
    return (row["about"] is None and _is_message(row)
            and row["counterpart_kind"] != str(Counterpart.PERSON))


def general(before: int | None = None, limit: int = PAGE) -> list[ConversationItem]:
    """The messages to him that belong to no Work item, newest first. A
    person's messages stay in their own threads (the Queue), so are not here."""
    return list(reversed(_newest(_general, _STATED, before, limit, narrow=False)))


def new_general() -> int:
    """How many messages that belong to no Work item are new (a person's
    are counted in their own threads, so not here)."""
    new = str(MessageState.NEW)
    return len(_found(lambda r: _general(r) and r["state"] == new, [["state", "=", new]]))


def item_for_message(message_id: int) -> ConversationItem | None:
    """The item recording a message he sent an agent (TO_AGENT), or None."""
    rows = _found(lambda r: r["kind"] == str(ItemKind.TO_AGENT) and r["message_id"] == int(message_id),
                  [["message_id", "=", int(message_id)]], turns=False)
    return _item(min(rows, key=lambda r: r["id"])) if rows else None


def new_by_agent() -> dict[str, int]:
    """Each agent's new messages to him, by its session id: what its box
    counts (agent_directory's `needs`). Those signed by its own key
    (`agent:<session id>`)."""
    new = str(MessageState.NEW)
    out: dict[str, int] = {}
    for row in _found(lambda r: r["state"] == new and str(r["sender"] or "").startswith(SENDER_AGENT),
                      [["state", "=", new]]):
        key = str(row["sender"])[len(SENDER_AGENT):]
        out[key] = out.get(key, 0) + 1
    return out


def new_messages() -> list[ConversationItem]:
    """Every new message to him, oldest first: those about a Work item and
    those about none (a person's with no item are in their own threads, so
    not here, as `new_general` counts)."""
    new = str(MessageState.NEW)
    rows = _found(lambda r: r["state"] == new and (
        r["about"] is not None or r["counterpart_kind"] != str(Counterpart.PERSON)),
        [["state", "=", new]])
    return [_item(r) for r in sorted(rows, key=lambda r: r["id"])]
