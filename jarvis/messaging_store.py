"""The local record of human messaging: drafts, approved envelopes and their
sends, the messages this owner has, their receipts, the owner's work items,
the sync position of this node, receipts and work commands still owed to
the team server and which arrivals are due to be announced, and the
collaboration bundles' local records: the sender's preflight stored with
the revision it was made for, the recipient's openability of each received
file per content revision with what was last reported (the receipt it owes
is an owed receipt of kind `openability`), and the recipient's review
checklist per thread (never sent).

Documents in the record engine (`records.py`), redesigned from the
`human_*` tables (docs/planning/knowledge-engine/plan-uds.md):

- `message`, `/people/<github id>/messages/<message id>@<server id>.message`:
  ONE message from draft to approved, sent, accepted, received and read,
  each step a version. Its parts are the draft (`draft`), its send record
  (`send`), every approved envelope by content revision (`approved`), the
  sender's preflights (`preflight`), the accepted message itself
  (`accepted`), its receipts (`receipts`), the receipts owed for it
  (`owed`) and the openability of its files (`openability`).
- `thread`, `/people/<github id>/threads/<thread id>@<server id>.thread`:
  the owner's work item (`work`), the work command waiting to go
  (`command`) and the review checklist (`review`).
- `/attention/announcements/<github id>@<message id>@<server id>.attention`:
  whether an arrival is due to be announced (kept here, in the attention
  type's folder).
- `/people/<github id>/settings/sync.settings`: this owner's sync position
  per server and node (`sync`).

`human_switch` held the messaging switch there used to be; nothing reads it
and it moves as the move kit's plain rows.

Every record is namespaced by the team server's `server_id` and the
owner's GitHub id (the owner in the path, the server in the id and as a
field checked on every read), so a JARVIS paired again, to another server
or as another person, never mixes or releases one owner's work as
another's. A store opened on another path than the data folder's database
(tests' second node) keeps its documents under a root of its own.

Each write the rules pair is one atomic unit: an approved envelope with its
send (`save_approved`), an accepted send with its message, receipts and work
item (`record_accepted`), a sync page with its continuation (`store_page`).
Mutable records keep the higher revision they have seen (receipts per
message, work state per thread); messages are immutable; nothing absent
from a scan is ever pruned. Records are kept as the contract's own JSON
(`model_dump(mode="json")`). A part keeps the SQLite `rowid` it was moved
with while SQLite would have kept it, and `seq`, the order the queries
that read "in the order written" go by (the rowid when moved, then a
clock count; a write that replaced its row in SQLite takes a new one).

The store publishes nothing: whoever wrote announces the change after this
returns, so a hint never runs ahead of a commit.
"""
from __future__ import annotations

import copy
import json
import re
import threading
import time
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, NamedTuple, Optional
from uuid import UUID

from jarvis import data_paths, records
from jarvis import messaging_ports as ports
from jarvis import messaging_wire as wire


class OpenabilityRow(NamedTuple):
    """One received file's openability record: the folded facts (bundle_status
    owns their shape), the report they make now, and the report last sent."""
    facts: str
    report: Optional[str]
    reported: Optional[str]


# Sends whose state the delivery loop owns from here on: an edit or a
# discard of their revision is refused until they settle.
_OUT = frozenset({"submitting", "accept_unknown", "accepted"})


# -- shared by the people's documents (messaging_files too) --------------------------------

def doc_id(*parts: Any) -> str:
    """Several ids as one path segment, one spelling each: `@` joins them
    (escaped inside a part), then `records.segment`."""
    return records.segment("@".join(str(p).replace("%", "%25").replace("@", "%40")
                                    for p in parts))


def person(owner: Any) -> str:
    return f"/people/{records.segment(owner)}"


_seq_lock = threading.Lock()
_seq_last = 0


def next_seq() -> int:
    """The order key of a part written now: after every moved rowid and
    every earlier write of this process."""
    global _seq_last
    with _seq_lock:
        _seq_last = max(time.time_ns(), _seq_last + 1)
        return _seq_last


def _bad_constant(name: str):
    raise ValueError(name)


def parse_json(text: Any) -> Optional[Any]:
    """A JSON object or list held as text, or None when it is not one."""
    if not isinstance(text, str):
        return None
    try:
        value = json.loads(text, parse_constant=_bad_constant)
        json.dumps(value, allow_nan=False)          # 1e999 reads as an infinity
    except ValueError:
        return None
    return value if isinstance(value, (dict, list)) else None


def keep_json(part: dict, column: str, text: Any,
              render: Optional[Callable[[Any], str]] = None) -> None:
    """A JSON text column into `part`: its value parsed; the text itself in
    `part["raw"]` when it is not JSON, or (with `render`, the move) when
    rendering the value again would not give the very same text."""
    from jarvis import knowledge_move as move
    value = parse_json(text)
    part[column] = value
    if text is None:
        return
    same = False
    if value is not None and render is not None:
        try:        # as the engine gives it back: every object's keys sorted
            same = render(json.loads(json.dumps(value, sort_keys=True))) == text
        except Exception:
            same = False
    elif value is not None:
        same = True
    if not same:
        part.setdefault("raw", {})[column] = move.cell(text)


def json_text(part: dict, column: str, render: Callable[[Any], str]) -> Optional[str]:
    """A JSON text column back from its part: the kept text, else the value
    rendered."""
    raw = part.get("raw") or {}
    if column in raw:
        from jarvis import knowledge_move as move
        return move.uncell(raw[column])
    value = part.get(column)
    return None if value is None else render(value)


def compact(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)


def spaced(value: Any) -> str:
    return json.dumps(value)


def model_render(model) -> Callable[[Any], str]:
    return lambda value: model.model_validate(value).model_dump_json()


class Docs:
    """The documents one atomic unit reads and changes: each read once and
    kept, changed in place, and written at `flush` only when it changed
    (an empty one deleted)."""

    def __init__(self, unit: records.Unit, empty: Callable[[str, dict], bool]):
        self.unit, self._empty = unit, empty
        self.orig: dict[str, Optional[dict]] = {}
        self.cur: dict[str, Optional[dict]] = {}
        self.tags: dict[str, tuple[str, Optional[str]]] = {}

    def peek(self, path: str) -> Optional[dict]:
        if path not in self.cur:
            rec = self.unit.get(path)
            data = None if rec is None else rec.data
            self.orig[path] = copy.deepcopy(data)
            self.cur[path] = data
        return self.cur[path]

    def get(self, path: str, make: Callable[[], dict]) -> dict:
        found = self.peek(path)
        if found is None:
            found = self.cur[path] = make()
        return found

    def adopt(self, rec: records.Rec) -> dict:
        if rec.path not in self.cur:
            self.orig[rec.path] = copy.deepcopy(rec.data)
            self.cur[rec.path] = rec.data
        return self.cur[rec.path]

    def scan(self, under: str, **kw) -> list[dict]:
        return [self.adopt(rec) for rec in self.unit.scan(under, **kw)]

    def touch(self, path: str, change: str, summary: Optional[str] = None) -> None:
        self.tags[path] = (change, summary)

    def changed(self, path: str) -> bool:
        data = self.cur.get(path)
        if data is not None and self._empty(path, data):
            data = None
        return data != self.orig.get(path)

    def flush(self) -> bool:
        """Stage every changed document; whether any did."""
        any_changed = False
        for path, data in self.cur.items():
            if not self.changed(path):
                continue
            any_changed = True
            change, summary = self.tags.get(path, ("changed", None))
            if data is None or self._empty(path, data):
                self.unit.delete(path, change=change, summary=summary)
            else:
                data["events"] = []
                self.unit.put(path, data, change=change, summary=summary)
        return any_changed


# -- this store's documents -----------------------------------------------------------------

def _message_doc(server_id: str, owner: int, message_id: str) -> dict:
    return {"server_id": server_id, "owner": owner, "message_id": message_id,
            "thread_id": None, "draft": None, "send": None, "approved": [], "preflight": [],
            "accepted": None, "receipts": None, "owed": [], "openability": [], "events": []}


def _thread_doc(server_id: str, owner: int, thread_id: str) -> dict:
    return {"server_id": server_id, "owner": owner, "thread_id": thread_id,
            "work": None, "command": None, "review": None, "events": []}


def _announcement_doc(server_id: str, owner: int, message_id: str) -> dict:
    return {"server_id": server_id, "owner": owner, "message_id": message_id,
            "state": None, "rowid": None, "seq": None, "events": []}


def _sync_doc(owner: int) -> dict:
    return {"owner": owner, "sync": [], "events": []}


def _empty(path: str, data: dict) -> bool:
    if path.endswith(".message"):
        return not any(data.get(k) for k in ("draft", "send", "approved", "preflight", "accepted",
                                             "receipts", "owed", "openability"))
    if path.endswith(".thread"):
        return not any(data.get(k) for k in ("work", "command", "review"))
    if path.endswith(".attention"):
        return data.get("state") is None
    if path.endswith(".settings"):
        return not data.get("sync")
    return False


def _thread_of(doc: dict) -> Optional[str]:
    if doc.get("accepted"):
        return doc["accepted"]["thread_id"]
    if doc.get("draft"):
        return doc["draft"]["thread_id"]
    return None


_RENDER = {
    "accepted": model_render(wire.AcceptedMessage),
    "draft": model_render(ports.Draft),
    "send": model_render(ports.LocalSend),
    "approved": model_render(ports.ApprovedSend),
    "receipts": model_render(wire.ReceiptFacts),
    "work": model_render(wire.WorkState),
    "command": model_render(wire.WorkCommandRequest),
    "report": model_render(wire.OpenabilityReport),
}


def _replaced(**fields) -> dict:
    """A part written as SQLite's INSERT OR REPLACE would: a new row."""
    return {**fields, "rowid": None, "seq": next_seq()}


class MessagingStore:
    """`LocalMessagingStore` over the record engine. Also: `set_capture`
    (the review's capture, which is not an edit), `transition_send` (a
    send's state by compare-and-set, for the delivery loop and the page
    racing on one send) and `submitting` (sends a crash left mid-attempt)."""

    def __init__(self, path: Optional[Path] = None):
        self.path = Path(path) if path is not None else data_paths.db_path()
        self.root = "" if self.path == data_paths.db_path() else \
            "/stores/" + records.segment(str(self.path))

    # -- paths ------------------------------------------------------------------------

    def _messages(self, owner) -> str:
        return f"{self.root}{person(owner)}/messages"

    def _threads(self, owner) -> str:
        return f"{self.root}{person(owner)}/threads"

    def _message_path(self, server_id, owner, message_id) -> str:
        return f"{self._messages(owner)}/{doc_id(message_id, server_id)}.message"

    def _thread_path(self, server_id, owner, thread_id) -> str:
        return f"{self._threads(owner)}/{doc_id(thread_id, server_id)}.thread"

    def _announcements(self) -> str:
        return f"{self.root}/attention/announcements"

    def _announcement_path(self, server_id, owner, message_id) -> str:
        return f"{self._announcements()}/{doc_id(owner, message_id, server_id)}.attention"

    def _sync_path(self, owner) -> str:
        return f"{self.root}{person(owner)}/settings/sync.settings"

    # -- units ------------------------------------------------------------------------

    def _write(self, fn: Callable[[Docs], Any], *, by=records.JARVIS,
               full_sync: bool = False) -> Any:
        def run(unit: records.Unit):
            docs = Docs(unit, _empty)
            result = fn(docs)
            docs.flush()
            return result
        return records.atomic(run, by=by, full_sync=full_sync)

    def _message(self, docs: Docs, server_id, owner, message_id) -> dict:
        path = self._message_path(server_id, owner, message_id)
        return docs.get(path, lambda: _message_doc(server_id, owner, str(message_id)))

    def _thread(self, docs: Docs, server_id, owner, thread_id) -> dict:
        path = self._thread_path(server_id, owner, thread_id)
        return docs.get(path, lambda: _thread_doc(server_id, owner, str(thread_id)))

    def _read(self, path: str, server_id: str) -> Optional[dict]:
        rec = records.get(path)
        if rec is None or rec.data.get("server_id") != server_id:
            return None
        return rec.data

    def _scan(self, under: str, server_id: str, where: Optional[list] = None,
              check: Callable[[dict], bool] = lambda d: True) -> list[dict]:
        found = records.scan(under, where=[["server_id", "=", server_id]] + list(where or []))
        return [r.data for r in found if r.data.get("server_id") == server_id and check(r.data)]

    def schema_version(self) -> int:
        """Kept for callers: the record has no SQLite schema any more."""
        return 0

    # -- drafts -----------------------------------------------------------------------

    @staticmethod
    def _draft_of(doc: Optional[dict]) -> Optional[ports.Draft]:
        if not doc or not doc.get("draft"):
            return None
        return ports.Draft.model_validate(doc["draft"]["body"])

    @staticmethod
    def _send_of(doc: Optional[dict]) -> Optional[ports.LocalSend]:
        if not doc or not doc.get("send"):
            return None
        return ports.LocalSend.model_validate(doc["send"]["body"])

    @staticmethod
    def _set_draft(doc: dict, draft: ports.Draft) -> None:
        doc["draft"] = _replaced(thread_id=str(draft.thread_id),
                                 body=draft.model_dump(mode="json"))
        doc["thread_id"] = _thread_of(doc)

    def _set_send(self, doc: dict, send: ports.LocalSend) -> None:
        """The send, and the draft's copy of it when the draft is on that
        revision."""
        doc["send"] = _replaced(content_revision=send.content_revision, state=send.state,
                                body=send.model_dump(mode="json"))
        draft = self._draft_of(doc)
        if draft is not None and draft.send.content_revision == send.content_revision:
            self._set_draft(doc, draft.model_copy(update={"send": send}))

    def _owned(self, docs: Docs, server_id, owner, message_id) -> dict:
        return self._message(docs, server_id, owner, message_id)

    def create_draft(self, server_id: str, owner_github_id: int,
                     draft: ports.Draft) -> ports.Draft:
        def go(docs: Docs):
            doc = self._owned(docs, server_id, owner_github_id, draft.message_id)
            existing = self._draft_of(doc)
            if existing is not None:
                raise wire.ContractError("revision_conflict", latest=existing)
            self._set_draft(doc, draft)
            self._set_send(doc, draft.send)
            docs.touch(self._message_path(server_id, owner_github_id, draft.message_id),
                       "draft_created", "a draft begun")
        self._write(go)
        return draft

    def _current(self, doc, expected) -> ports.Draft:
        current = self._draft_of(doc)
        if current is None:
            raise wire.ContractError("draft_not_found")
        if current.draft_revision != expected:
            raise wire.ContractError("revision_conflict", latest=current)
        return current

    def save_draft(self, server_id: str, owner_github_id: int, draft: ports.Draft,
                   expected_draft_revision: int) -> ports.Draft:
        """The draft as edited, its revision moved on, and its send record:
        an edit after review replaces the send of the reviewed revision, so a
        revision approved but not yet sent can never go out after it. A send
        already out (or accepted) is never replaced by a stale edit."""
        def go(docs: Docs):
            doc = self._owned(docs, server_id, owner_github_id, draft.message_id)
            self._current(doc, expected_draft_revision)
            stored = self._send_of(doc)
            if stored is not None and stored.state in _OUT and stored != draft.send:
                raise ports.SendRefused(stored.state, "edit")
            saved = draft.model_copy(update={"draft_revision": expected_draft_revision + 1})
            self._set_draft(doc, saved)
            self._set_send(doc, saved.send)
            docs.touch(self._message_path(server_id, owner_github_id, draft.message_id),
                       "draft_saved", "the draft edited")
        self._write(go)
        return self.draft(server_id, owner_github_id, draft.message_id)

    def set_capture(self, server_id: str, owner_github_id: int, message_id: UUID,
                    capture_id: Optional[int]) -> ports.Draft:
        """Which capture the review opened (or None once it closed). Not an
        edit: the draft revision stays."""
        def go(docs: Docs):
            doc = self._owned(docs, server_id, owner_github_id, message_id)
            draft = self._draft_of(doc)
            if draft is None:
                raise wire.ContractError("draft_not_found")
            draft = draft.model_copy(update={"capture_id": capture_id})
            self._set_draft(doc, draft)
            docs.touch(self._message_path(server_id, owner_github_id, message_id), "capture",
                       "the review's capture")
            return draft
        return self._write(go)

    def forget_captures(self) -> None:
        """Every draft's review capture forgotten, at startup: captures live
        in the server's memory and their numbering starts again with it, so
        a number kept from before would name some other capture. A draft
        awaiting approval stays so; reviewing it again opens its capture."""
        def go(docs: Docs):
            for doc in docs.scan(f"{self.root}/people",
                                 where=[["draft.body.capture_id", "exists"]], types=["message"]):
                draft = self._draft_of(doc)
                if draft is None or draft.capture_id is None:
                    continue
                self._set_draft(doc, draft.model_copy(update={"capture_id": None}))
                docs.touch(self._message_path(doc["server_id"], doc["owner"],
                                              doc["message_id"]), "capture", "captures forgotten")
        self._write(go)

    def draft(self, server_id: str, owner_github_id: int,
              message_id: UUID) -> Optional[ports.Draft]:
        return self._draft_of(self._read(
            self._message_path(server_id, owner_github_id, message_id), server_id))

    def discard_draft(self, server_id: str, owner_github_id: int, message_id: UUID,
                      expected_draft_revision: int) -> None:
        def go(docs: Docs):
            doc = self._owned(docs, server_id, owner_github_id, message_id)
            self._current(doc, expected_draft_revision)
            stored = self._send_of(doc)
            if stored is not None and stored.state in _OUT | {"approved"}:
                raise ports.SendRefused(stored.state, "cancel")
            doc["draft"] = None
            doc["send"] = None
            doc["thread_id"] = _thread_of(doc)
            docs.touch(self._message_path(server_id, owner_github_id, message_id),
                       "draft_discarded", "the draft discarded")
        self._write(go)

    # -- approval and sending ---------------------------------------------------------

    def save_approved(self, approved: ports.ApprovedSend) -> ports.LocalSend:
        req = approved.request
        send = ports.LocalSend(message_id=req.message_id, content_revision=req.content_revision,
                               state="approved", content_digest=req.content_digest)
        server_id, owner = approved.server_id, approved.owner_github_id

        def go(docs: Docs):
            doc = self._owned(docs, server_id, owner, req.message_id)
            doc["approved"] = [a for a in doc["approved"]
                               if a["content_revision"] != req.content_revision]
            doc["approved"].append(_replaced(content_revision=req.content_revision,
                                             body=approved.model_dump(mode="json")))
            self._set_send(doc, send)
            docs.touch(self._message_path(server_id, owner, req.message_id), "approved",
                       "a content revision approved")
        self._write(go, by=records.TIM, full_sync=True)
        return send

    def approved(self, server_id: str, owner_github_id: int, message_id: UUID,
                 content_revision: int) -> Optional[ports.ApprovedSend]:
        doc = self._read(self._message_path(server_id, owner_github_id, message_id), server_id)
        found = next((a for a in (doc or {}).get("approved", [])
                      if a["content_revision"] == content_revision), None)
        return None if found is None else ports.ApprovedSend.model_validate(found["body"])

    def set_send(self, server_id: str, owner_github_id: int, send: ports.LocalSend) -> None:
        def go(docs: Docs):
            self._set_send(self._owned(docs, server_id, owner_github_id, send.message_id), send)
            docs.touch(self._message_path(server_id, owner_github_id, send.message_id), "send",
                       "the send record")
        self._write(go)

    def send(self, server_id: str, owner_github_id: int,
             message_id: UUID) -> Optional[ports.LocalSend]:
        return self._send_of(self._read(
            self._message_path(server_id, owner_github_id, message_id), server_id))

    def transition_send(self, server_id: str, owner_github_id: int, expected: ports.LocalSend,
                        new: ports.LocalSend) -> bool:
        """Write `new` only if the stored send is still exactly `expected`."""
        def go(docs: Docs):
            doc = self._owned(docs, server_id, owner_github_id, expected.message_id)
            if self._send_of(doc) != expected:
                return False
            self._set_send(doc, new)
            docs.touch(self._message_path(server_id, owner_github_id, expected.message_id),
                       "send", "the send record moved on")
            return True
        return self._write(go)

    def _sends(self, server_id, owner, states) -> list[ports.LocalSend]:
        found = self._scan(self._messages(owner), server_id,
                           [["send.state", "in", sorted(states)]],
                           lambda d: bool(d.get("send")) and d["send"]["state"] in states)
        return [self._send_of(d) for d in sorted(found, key=lambda d: d["message_id"])]

    def unresolved_sends(self, server_id: str, owner_github_id: int) -> list[ports.LocalSend]:
        return self._sends(server_id, owner_github_id, ports.SEND_UNRESOLVED)

    def submitting(self, server_id: str, owner_github_id: int) -> list[ports.LocalSend]:
        return self._sends(server_id, owner_github_id, {"submitting"})

    def _keep(self, docs: Docs, server_id, owner, m: wire.AcceptedMessage,
              receipts: wire.ReceiptFacts, work: wire.WorkState) -> None:
        path = self._message_path(server_id, owner, m.message_id)
        doc = self._owned(docs, server_id, owner, m.message_id)
        if doc["accepted"] is None:
            doc["accepted"] = {"rowid": None, "thread_id": str(m.thread_id), "incoming": 0,
                               "body": m.model_dump(mode="json")}
            doc["thread_id"] = _thread_of(doc)
            docs.touch(path, "accepted", "the message accepted by the team server")
        self._keep_receipts(docs, server_id, owner, receipts)
        self._keep_work(docs, server_id, owner, work)

    def _keep_receipts(self, docs: Docs, server_id, owner, facts: wire.ReceiptFacts) -> None:
        doc = self._owned(docs, server_id, owner, facts.message_id)
        held = doc["receipts"]
        if held is None:
            doc["receipts"] = {"rowid": None, "revision": facts.revision,
                               "body": facts.model_dump(mode="json")}
        elif facts.revision > held["revision"]:
            held.update(revision=facts.revision, body=facts.model_dump(mode="json"))
            held.pop("raw", None)
        else:
            return
        docs.touch(self._message_path(server_id, owner, facts.message_id), "receipts",
                   "newer receipts")

    def _keep_work(self, docs: Docs, server_id, owner, state: wire.WorkState) -> None:
        doc = self._thread(docs, server_id, owner, state.thread_id)
        held = doc["work"]
        fields = dict(revision=state.revision, inactive=int(state.placement == "inactive"),
                      body=state.model_dump(mode="json"))
        if held is None:
            doc["work"] = {"rowid": None, **fields}
        elif state.revision > held["revision"]:
            held.update(fields)
            held.pop("raw", None)
        else:
            return
        docs.touch(self._thread_path(server_id, owner, state.thread_id), "work_state",
                   "a newer work state")

    def _owe(self, doc: dict, kind: str) -> bool:
        if any(o["kind"] == kind for o in doc["owed"]):
            return False
        doc["owed"].append({"kind": kind, "rowid": None, "seq": next_seq()})
        return True

    def _first_incoming(self, docs: Docs, server_id, owner, message_id) -> bool:
        doc = self._owned(docs, server_id, owner, message_id)
        accepted = doc["accepted"]
        if accepted is None or accepted["incoming"]:
            return False
        accepted["incoming"] = 1
        self._owe(doc, "received")
        docs.touch(self._message_path(server_id, owner, message_id), "received",
                   "arrived here")
        path = self._announcement_path(server_id, owner, message_id)
        ann = docs.get(path, lambda: _announcement_doc(server_id, owner, str(message_id)))
        ann.update(state="due", rowid=None, seq=next_seq())
        docs.touch(path, "announcement", "due")
        return True

    def record_accepted(self, server_id: str, owner_github_id: int,
                        result: wire.SendResult) -> bool:
        m = result.message

        def go(docs: Docs):
            self._keep(docs, server_id, owner_github_id, m, result.receipts, result.work_state)
            doc = self._owned(docs, server_id, owner_github_id, m.message_id)
            sent = self._send_of(doc)
            if sent is not None:
                self._set_send(doc, sent.model_copy(update={
                    "state": "accepted", "failure": None, "next_attempt_at": None}))
                docs.touch(self._message_path(server_id, owner_github_id, m.message_id),
                           "accepted", "sent and accepted")
            return m.recipient_github_id == owner_github_id and \
                self._first_incoming(docs, server_id, owner_github_id, m.message_id)
        return self._write(go)

    # -- incoming and sync -------------------------------------------------------------

    def store_page(self, server_id: str, owner_github_id: int, node_id: UUID,
                   page: wire.SyncPage) -> list[UUID]:
        return self.store_page_changes(server_id, owner_github_id, node_id, page)[0]

    def store_page_changes(self, server_id: str, owner_github_id: int, node_id: UUID,
                           page: wire.SyncPage) -> tuple[list[UUID], bool]:
        """`store_page`, and whether any record changed (a rescan of what is
        already here changes nothing, and so publishes no hint)."""
        def go(docs: Docs):
            new: list[UUID] = []
            for e in page.entries:
                self._keep(docs, server_id, owner_github_id, e.message, e.receipts,
                           e.work_state)
                if e.message.recipient_github_id == owner_github_id and self._first_incoming(
                        docs, server_id, owner_github_id, e.message.message_id):
                    new.append(e.message.message_id)
            changed = any(docs.changed(p) for p in list(docs.cur))
            self._set_sync(docs, server_id, owner_github_id, node_id, page.continuation)
            return new, changed
        return self._write(go)

    def _set_sync(self, docs: Docs, server_id, owner, node_id, continuation) -> None:
        path = self._sync_path(owner)
        doc = docs.get(path, lambda: _sync_doc(owner))
        doc["sync"] = [s for s in doc["sync"]
                       if not (s["server_id"] == server_id and s["node_id"] == str(node_id))]
        if continuation is not ...:
            doc["sync"].append(_replaced(server_id=server_id, node_id=str(node_id),
                                         continuation=continuation))
        docs.touch(path, "sync", "the sync position")

    def sync_position(self, server_id: str, owner_github_id: int,
                      node_id: UUID) -> Optional[str]:
        rec = records.get(self._sync_path(owner_github_id))
        found = next((s for s in (rec.data["sync"] if rec else [])
                      if s["server_id"] == server_id and s["node_id"] == str(node_id)), None)
        return None if found is None else found["continuation"]

    def restart_sync(self, server_id: str, owner_github_id: int, node_id: UUID) -> None:
        """Forget this node's continuation (`sync_restart`): the next page
        starts a new scan. Nothing stored is pruned."""
        self._write(lambda docs: self._set_sync(docs, server_id, owner_github_id, node_id, ...))

    def owe_receipt(self, server_id: str, owner_github_id: int, message_id: UUID,
                    kind: wire.ReceiptKind) -> None:
        def go(docs: Docs):
            if self._owe(self._owned(docs, server_id, owner_github_id, message_id), kind):
                docs.touch(self._message_path(server_id, owner_github_id, message_id), "owed",
                           "a receipt owed")
        self._write(go)

    def receipts_owed(self, server_id: str, owner_github_id: int) -> list[ports.OwedReceipt]:
        owed = [(o["seq"], d["message_id"], o["kind"])
                for d in self._scan(self._messages(owner_github_id), server_id,
                                    [["owed[].kind", "exists"]])
                for o in d["owed"]]
        return [ports.OwedReceipt(message_id=UUID(m), kind=k) for _, m, k in sorted(owed)]

    def settle_receipts(self, server_id: str, owner_github_id: int,
                        sent: list[ports.OwedReceipt], result: wire.ReceiptBatchResult) -> None:
        def go(docs: Docs):
            for facts in result.facts:
                self._keep_receipts(docs, server_id, owner_github_id, facts)
            for owed in sent:
                doc = self._owned(docs, server_id, owner_github_id, owed.message_id)
                kept = [o for o in doc["owed"] if o["kind"] != owed.kind]
                if kept != doc["owed"]:
                    doc["owed"] = kept
                    docs.touch(self._message_path(server_id, owner_github_id, owed.message_id),
                               "receipt_sent", "an owed receipt sent")
        self._write(go)

    # -- work state --------------------------------------------------------------------

    @staticmethod
    def _work_of(doc: Optional[dict]) -> Optional[wire.WorkState]:
        if not doc or not doc.get("work"):
            return None
        return wire.WorkState.model_validate(doc["work"]["body"])

    def save_work_state(self, server_id: str, owner_github_id: int,
                        state: wire.WorkState) -> wire.WorkState:
        def go(docs: Docs):
            self._keep_work(docs, server_id, owner_github_id, state)
            return self._work_of(self._thread(docs, server_id, owner_github_id, state.thread_id))
        return self._write(go)

    def work_state(self, server_id: str, owner_github_id: int,
                   thread_id: UUID) -> Optional[wire.WorkState]:
        return self._work_of(self._read(
            self._thread_path(server_id, owner_github_id, thread_id), server_id))

    def queue_command(self, server_id: str, owner_github_id: int, thread_id: UUID,
                      request: wire.WorkCommandRequest) -> None:
        def go(docs: Docs):
            doc = self._thread(docs, server_id, owner_github_id, thread_id)
            doc["command"] = _replaced(body=request.model_dump(mode="json"))
            docs.touch(self._thread_path(server_id, owner_github_id, thread_id),
                       "command_queued", "a work command to send")
        self._write(go)

    def pending_commands(self, server_id: str, owner_github_id: int
                         ) -> list[tuple[UUID, wire.WorkCommandRequest]]:
        found = self._scan(self._threads(owner_github_id), server_id,
                           [["command.seq", "exists"]], lambda d: bool(d.get("command")))
        found.sort(key=lambda d: d["command"]["seq"])
        return [(UUID(d["thread_id"]), wire.WorkCommandRequest.model_validate(
            d["command"]["body"])) for d in found]

    def settle_command(self, server_id: str, owner_github_id: int, thread_id: UUID) -> None:
        def go(docs: Docs):
            path = self._thread_path(server_id, owner_github_id, thread_id)
            doc = docs.peek(path)
            if doc is not None and doc.get("server_id") == server_id and doc.get("command"):
                doc["command"] = None
                docs.touch(path, "command_settled", "the work command sent")
        self._write(go)

    # -- announcements -------------------------------------------------------------------

    def announcements_due(self, server_id: str, owner_github_id: int) -> list[UUID]:
        found = self._scan(self._announcements(), server_id,
                           [["owner", "=", owner_github_id], ["state", "=", "due"]],
                           lambda d: d["owner"] == owner_github_id and d["state"] == "due")
        return [UUID(d["message_id"]) for d in sorted(found, key=lambda d: d["seq"])]

    def set_announcement(self, server_id: str, owner_github_id: int, message_id: UUID,
                         state: ports.AnnouncementLocal) -> None:
        def go(docs: Docs):
            path = self._announcement_path(server_id, owner_github_id, message_id)
            ann = docs.get(path, lambda: _announcement_doc(server_id, owner_github_id,
                                                           str(message_id)))
            ann.update(state=state, rowid=None, seq=next_seq())
            docs.touch(path, "announcement", state)
        self._write(go)

    # -- collaboration bundles ------------------------------------------------------------

    def save_preflight(self, server_id: str, owner_github_id: int, message_id: UUID,
                       content_revision: int, capability_digest: str, readiness: str,
                       body: str) -> None:
        """The sender's preflight for one content revision (its approval's)."""
        def go(docs: Docs):
            doc = self._owned(docs, server_id, owner_github_id, message_id)
            part = _replaced(content_revision=content_revision,
                             capability_digest=capability_digest, readiness=readiness)
            keep_json(part, "body", body)
            doc["preflight"] = [p for p in doc["preflight"]
                                if p["content_revision"] != content_revision] + [part]
            docs.touch(self._message_path(server_id, owner_github_id, message_id), "preflight",
                       "the sender's preflight")
        self._write(go)

    def preflight(self, server_id: str, owner_github_id: int, message_id: UUID,
                  content_revision: int) -> Optional[str]:
        doc = self._read(self._message_path(server_id, owner_github_id, message_id), server_id)
        found = next((p for p in (doc or {}).get("preflight", [])
                      if p["content_revision"] == content_revision), None)
        return None if found is None else json_text(found, "body", compact)

    @staticmethod
    def _openability_row(part: dict) -> OpenabilityRow:
        return OpenabilityRow(json_text(part, "facts", spaced),
                              json_text(part, "report", _RENDER["report"]),
                              json_text(part, "reported", _RENDER["report"]))

    def openability(self, server_id: str, owner_github_id: int, message_id: UUID,
                    attachment_id: UUID, content_revision: int) -> Optional[OpenabilityRow]:
        doc = self._read(self._message_path(server_id, owner_github_id, message_id), server_id)
        found = next((p for p in (doc or {}).get("openability", [])
                      if p["attachment_id"] == str(attachment_id)
                      and p["content_revision"] == content_revision), None)
        return None if found is None else self._openability_row(found)

    def save_openability(self, server_id: str, owner_github_id: int, message_id: UUID,
                         attachment_id: UUID, content_revision: int, facts: str,
                         report: Optional[str], owe: bool) -> None:
        """The record and, when its report changed, the openability receipt
        owed for the message, in one write."""
        def go(docs: Docs):
            doc = self._owned(docs, server_id, owner_github_id, message_id)
            part = next((p for p in doc["openability"]
                         if p["attachment_id"] == str(attachment_id)
                         and p["content_revision"] == content_revision), None)
            if part is None:
                part = {"attachment_id": str(attachment_id), "content_revision": content_revision,
                        "rowid": None, "seq": next_seq(), "reported": None}
                doc["openability"].append(part)
            raw = part.pop("raw", {})
            if "reported" in raw:
                part["raw"] = {"reported": raw["reported"]}
            keep_json(part, "facts", facts)
            keep_json(part, "report", report)
            if owe:
                self._owe(doc, "openability")
            docs.touch(self._message_path(server_id, owner_github_id, message_id),
                       "openability", "a file's openability")
        self._write(go)

    def openability_reports(self, server_id: str, owner_github_id: int,
                            message_id: UUID) -> list[str]:
        """Every report this node has made on the message's files, in the
        order the files were first opened."""
        doc = self._read(self._message_path(server_id, owner_github_id, message_id), server_id)
        parts = sorted((doc or {}).get("openability", []), key=lambda p: p["seq"])
        return [r for r in (json_text(p, "report", _RENDER["report"]) for p in parts)
                if r is not None]

    def openability_reported(self, server_id: str, owner_github_id: int, message_id: UUID,
                             sent: list[str]) -> None:
        """The reports in `sent` reached the team server. A record whose
        report changed since it was sent owes the receipt again."""
        went = [parse_json(s) for s in sent]

        def go(docs: Docs):
            doc = self._owned(docs, server_id, owner_github_id, message_id)
            again = False
            for part in doc["openability"]:
                text = json_text(part, "report", _RENDER["report"])
                if text is None:
                    continue
                if parse_json(text) in went:
                    part["reported"] = part["report"]
                    raw = part.get("raw") or {}
                    if "report" in raw:
                        raw["reported"] = raw["report"]
                    else:
                        raw.pop("reported", None)
                    if not raw:
                        part.pop("raw", None)
                else:
                    again = True
            if again:
                self._owe(doc, "openability")
            docs.touch(self._message_path(server_id, owner_github_id, message_id),
                       "openability_reported", "openability reports sent")
        self._write(go)

    def review(self, server_id: str, owner_github_id: int,
               thread_id: UUID) -> Optional[tuple[int, str]]:
        """The recipient's review record of a thread: (revision, body)."""
        doc = self._read(self._thread_path(server_id, owner_github_id, thread_id), server_id)
        part = (doc or {}).get("review")
        return None if not part else (part["revision"], json_text(part, "body", spaced))

    def save_review(self, server_id: str, owner_github_id: int, thread_id: UUID,
                    expected_revision: Optional[int], revision: int, body: str) -> bool:
        """Write the record only if it is still at `expected_revision` (None:
        only if there is none yet)."""
        def go(docs: Docs):
            doc = self._thread(docs, server_id, owner_github_id, thread_id)
            held = doc["review"]
            if expected_revision is None:
                if held is not None:
                    return False
                doc["review"] = {"rowid": None, "revision": revision}
            else:
                if held is None or held["revision"] != expected_revision:
                    return False
                held.pop("raw", None)
                held["revision"] = revision
            keep_json(doc["review"], "body", body)
            docs.touch(self._thread_path(server_id, owner_github_id, thread_id), "review",
                       "the review checklist")
            return True
        return self._write(go)

    # -- reads -----------------------------------------------------------------------------

    @staticmethod
    def _local_message(doc: dict, owner: int) -> ports.LocalMessage:
        m = wire.AcceptedMessage.model_validate(doc["accepted"]["body"])
        outgoing = m.sender_github_id == owner
        return ports.LocalMessage(
            message=m, direction="outgoing" if outgoing else "incoming",
            self_message=outgoing and m.recipient_github_id == owner,
            receipts=wire.ReceiptFacts.model_validate(doc["receipts"]["body"]))

    @staticmethod
    def _in_thread(doc: dict, thread: str) -> bool:
        return bool(doc.get("accepted")) and doc["accepted"]["thread_id"] == thread

    def _thread_messages(self, docs: list[dict], owner: int, thread: str
                         ) -> list[ports.LocalMessage]:
        """A thread's messages in order, each he sent with what the replies
        cite of it (`ports.with_citations`)."""
        return ports.with_citations(_sort_messages(
            [self._local_message(d, owner) for d in docs if self._in_thread(d, thread)]))

    def message(self, server_id: str, owner_github_id: int,
                message_id: UUID) -> Optional[ports.LocalMessage]:
        doc = self._read(self._message_path(server_id, owner_github_id, message_id), server_id)
        if not doc or not doc.get("accepted"):
            return None
        thread = doc["accepted"]["thread_id"]
        docs = self._scan(self._messages(owner_github_id), server_id,
                          [["accepted.thread_id", "=", thread]])
        return next(m for m in self._thread_messages(docs, owner_github_id, thread)
                    if m.message.message_id == message_id)

    def _item(self, messages: list[dict], thread_doc: dict, owner: int) -> ports.LocalItem:
        state = self._work_of(thread_doc)
        thread = str(state.thread_id)
        drafts = [self._draft_of(d) for d in sorted(
            (d for d in messages if d.get("draft") and d["draft"]["thread_id"] == thread),
            key=lambda d: d["draft"]["seq"])]
        drafts = [d for d in drafts if d.send.state != "accepted"]
        editable = [d for d in drafts if d.send.state in ports.SEND_EDITABLE]
        command = thread_doc.get("command")
        return ports.LocalItem(
            work_state=state, messages=self._thread_messages(messages, owner, thread),
            drafts=drafts,
            pending_command=(wire.WorkCommandRequest.model_validate(command["body"])
                             if command else None),
            dismiss_blockers=ports.dismiss_blockers(
                unsent_text=any(d.text or d.attachments for d in editable),
                send_states=[d.send.state for d in drafts],
                uploads_pending=any(ports.uploads_pending(d.files) for d in drafts)))

    def item(self, server_id: str, owner_github_id: int,
             thread_id: UUID) -> Optional[ports.LocalItem]:
        doc = self._read(self._thread_path(server_id, owner_github_id, thread_id), server_id)
        if self._work_of(doc) is None:
            return None
        thread = str(thread_id)
        messages = self._scan(self._messages(owner_github_id), server_id,
                              [["thread_id", "=", thread]])
        return self._item(messages, doc, owner_github_id)

    def _everything(self, server_id: str, owner: int) -> tuple[list[dict], list[dict]]:
        return (self._scan(self._messages(owner), server_id),
                self._scan(self._threads(owner), server_id, [["work.revision", "exists"]],
                           lambda d: bool(d.get("work"))))

    def _items(self, messages, threads, owner, inactive: bool) -> list[ports.LocalItem]:
        chosen = [t for t in threads if t["work"]["inactive"] == int(inactive)]
        chosen.sort(key=lambda t: wire.item_order_key(self._work_of(t)), reverse=True)
        return [self._item(messages, t, owner) for t in chosen]

    def snapshot(self, server_id: str, owner_github_id: int, *,
                 availability: ports.Availability, stale: bool) -> ports.QueueSnapshot:
        messages, threads = self._everything(server_id, owner_github_id)
        items = self._items(messages, threads, owner_github_id, inactive=False)
        history = sum(1 for t in threads if t["work"]["inactive"] == 1)
        with_work = {t["thread_id"] for t in threads}
        unsent = [self._draft_of(d) for d in sorted(
            (d for d in messages if d.get("draft")
             and d["draft"]["thread_id"] == d["message_id"]
             and d["draft"]["thread_id"] not in with_work),
            key=lambda d: d["draft"]["seq"])]
        return ports.QueueSnapshot(server_id=server_id, owner_github_id=owner_github_id,
                                   availability=availability, items=items, unsent=unsent,
                                   history_count=history, stale=stale)

    def history(self, server_id: str, owner_github_id: int, continuation: Optional[str],
                limit: int) -> ports.HistoryPage:
        messages, threads = self._everything(server_id, owner_github_id)
        items = self._items(messages, threads, owner_github_id, inactive=True)
        start = int(continuation) if continuation and continuation.isdigit() else 0
        end = start + max(1, min(limit, ports.HISTORY_PAGE_MAX))
        return ports.HistoryPage(items=items[start:end],
                                 continuation=str(end) if end < len(items) else None)


SqliteMessagingStore = MessagingStore
"""The store's name before the record engine; callers still use it."""


def _sort_messages(messages: list[ports.LocalMessage]) -> list[ports.LocalMessage]:
    return sorted(messages, key=lambda lm: (lm.message.accepted_at, str(lm.message.message_id)))


# -- the move: the human_* tables to these documents and back ---------------------------------

def _rows_of(conn, table: str) -> Iterator[dict]:
    """Every row of `table` with its rowid, none when the file lacks it."""
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                    (table,)).fetchone() is None:
        return
    cur = conn.execute(f'SELECT rowid AS "__rowid", * FROM "{table}"')
    names = [d[0] for d in cur.description]
    for r in cur:
        yield dict(zip(names, r))


def _plain(v: Any) -> Any:
    from jarvis import knowledge_move as move
    return move.cell(v)


def _unplain(v: Any) -> Any:
    from jarvis import knowledge_move as move
    return move.uncell(v)


_MESSAGE_PATH = re.compile(r"/people/[^/]+/messages/[^/]+\.message")
_THREAD_PATH = re.compile(r"/people/[^/]+/threads/[^/]+\.thread")
_SYNC_PATH = re.compile(r"/people/[^/]+/settings/sync\.settings")
_ANNOUNCEMENT_PATH = re.compile(r"/attention/announcements/[^/]+\.attention")


class HumanMessagingMapping:
    """The `human_*` tables (but `human_switch`) to the `message`, `thread`,
    announcement and sync documents above, and back, row for row."""

    name = "human_messaging"
    tables = ("human_messages", "human_drafts", "human_approved", "human_sends",
              "human_preflight", "human_owed", "human_receipts", "human_openability",
              "human_review", "human_work", "human_commands", "human_announcements",
              "human_sync")
    under = ("/people", "/attention/announcements")
    history = False

    def documents(self, conn) -> Iterator["Doc"]:
        from jarvis.knowledge_move import Doc
        store = MessagingStore.__new__(MessagingStore)
        store.root = ""
        out: dict[str, dict] = {}

        def message(r) -> dict:
            path = store._message_path(r["server_id"], r["owner"], r["message_id"])
            return out.setdefault(path, _message_doc(r["server_id"], r["owner"], r["message_id"]))

        def thread(r) -> dict:
            path = store._thread_path(r["server_id"], r["owner"], r["thread_id"])
            return out.setdefault(path, _thread_doc(r["server_id"], r["owner"], r["thread_id"]))

        def json_part(r, columns: dict, json_columns: dict) -> dict:
            part = {"rowid": r["__rowid"], "seq": r["__rowid"]}
            for c in columns:
                part[c] = _plain(r[c])
            for c, render in json_columns.items():
                keep_json(part, c, r[c], render)
            return part

        for r in _rows_of(conn, "human_messages"):
            doc = message(r)
            part = json_part(r, ("thread_id", "incoming"), {"body": _RENDER["accepted"]})
            del part["seq"]
            doc["accepted"] = part
        for r in _rows_of(conn, "human_drafts"):
            message(r)["draft"] = json_part(r, ("thread_id",), {"body": _RENDER["draft"]})
        for r in _rows_of(conn, "human_sends"):
            message(r)["send"] = json_part(r, ("content_revision", "state"),
                                           {"body": _RENDER["send"]})
        for r in _rows_of(conn, "human_approved"):
            message(r)["approved"].append(json_part(r, ("content_revision",),
                                                    {"body": _RENDER["approved"]}))
        for r in _rows_of(conn, "human_preflight"):
            message(r)["preflight"].append(json_part(
                r, ("content_revision", "capability_digest", "readiness"), {"body": compact}))
        for r in _rows_of(conn, "human_owed"):
            message(r)["owed"].append(json_part(r, ("kind",), {}))
        for r in _rows_of(conn, "human_receipts"):
            part = json_part(r, ("revision",), {"body": _RENDER["receipts"]})
            del part["seq"]
            message(r)["receipts"] = part
        for r in _rows_of(conn, "human_openability"):
            message(r)["openability"].append(json_part(
                r, ("attachment_id", "content_revision"),
                {"facts": spaced, "report": _RENDER["report"], "reported": _RENDER["report"]}))
        for r in _rows_of(conn, "human_work"):
            part = json_part(r, ("revision", "inactive"), {"body": _RENDER["work"]})
            del part["seq"]
            thread(r)["work"] = part
        for r in _rows_of(conn, "human_commands"):
            thread(r)["command"] = json_part(r, (), {"body": _RENDER["command"]})
        for r in _rows_of(conn, "human_review"):
            part = json_part(r, ("revision",), {"body": spaced})
            del part["seq"]
            thread(r)["review"] = part
        for r in _rows_of(conn, "human_announcements"):
            path = store._announcement_path(r["server_id"], r["owner"], r["message_id"])
            doc = out.setdefault(path, _announcement_doc(r["server_id"], r["owner"],
                                                         r["message_id"]))
            doc.update(state=_plain(r["state"]), rowid=r["__rowid"], seq=r["__rowid"])
        for r in _rows_of(conn, "human_sync"):
            path = store._sync_path(r["owner"])
            doc = out.setdefault(path, _sync_doc(r["owner"]))
            doc["sync"].append({"server_id": _plain(r["server_id"]),
                                "node_id": _plain(r["node_id"]),
                                "continuation": _plain(r["continuation"]),
                                "rowid": r["__rowid"], "seq": r["__rowid"]})
        for path, data in out.items():
            for doc_key in ("server_id", "owner", "message_id", "thread_id"):
                if doc_key in data:
                    data[doc_key] = _plain(data[doc_key])
            if path.endswith(".message"):
                data["thread_id"] = _thread_of(data)
            yield Doc(path, data)

    def rows(self, docs: Iterable["Doc"]) -> dict:
        from jarvis.knowledge_move import Row
        out: dict[str, list] = {t: [] for t in self.tables}

        def add(table: str, keys: dict, part: dict, columns: tuple, json_columns: dict,
                order: list) -> None:
            cells = {k: _unplain(v) for k, v in keys.items()}
            for c in columns:
                cells[c] = _unplain(part.get(c))
            for c, render in json_columns.items():
                cells[c] = json_text(part, c, render)
            order.append((part.get("seq") or part.get("rowid") or 0,
                          table, Row(cells, part.get("rowid"))))

        order: list = []
        for doc in docs:
            d, path = doc.data, doc.path
            if _MESSAGE_PATH.fullmatch(path):
                key = {"server_id": d["server_id"], "owner": d["owner"],
                       "message_id": d["message_id"]}
                if d.get("accepted"):
                    add("human_messages", key, d["accepted"], ("thread_id", "incoming"),
                        {"body": _RENDER["accepted"]}, order)
                if d.get("draft"):
                    add("human_drafts", key, d["draft"], ("thread_id",),
                        {"body": _RENDER["draft"]}, order)
                if d.get("send"):
                    add("human_sends", key, d["send"], ("content_revision", "state"),
                        {"body": _RENDER["send"]}, order)
                for p in d.get("approved") or []:
                    add("human_approved", key, p, ("content_revision",),
                        {"body": _RENDER["approved"]}, order)
                for p in d.get("preflight") or []:
                    add("human_preflight", key, p,
                        ("content_revision", "capability_digest", "readiness"),
                        {"body": compact}, order)
                for p in d.get("owed") or []:
                    add("human_owed", key, p, ("kind",), {}, order)
                if d.get("receipts"):
                    add("human_receipts", key, d["receipts"], ("revision",),
                        {"body": _RENDER["receipts"]}, order)
                for p in d.get("openability") or []:
                    add("human_openability", key, p, ("attachment_id", "content_revision"),
                        {"facts": spaced, "report": _RENDER["report"],
                         "reported": _RENDER["report"]}, order)
            elif _THREAD_PATH.fullmatch(path):
                key = {"server_id": d["server_id"], "owner": d["owner"],
                       "thread_id": d["thread_id"]}
                if d.get("work"):
                    add("human_work", key, d["work"], ("revision", "inactive"),
                        {"body": _RENDER["work"]}, order)
                if d.get("command"):
                    add("human_commands", key, d["command"], (), {"body": _RENDER["command"]},
                        order)
                if d.get("review"):
                    add("human_review", key, d["review"], ("revision",), {"body": spaced},
                        order)
            elif _ANNOUNCEMENT_PATH.fullmatch(path) and d.get("state") is not None:
                add("human_announcements", {"server_id": d["server_id"], "owner": d["owner"],
                                            "message_id": d["message_id"]},
                    d, ("state",), {}, order)
            elif _SYNC_PATH.fullmatch(path):
                for s in d.get("sync") or []:
                    add("human_sync", {"server_id": s["server_id"], "owner": d["owner"]}, s,
                        ("node_id", "continuation"), {}, order)
        for _, table, row in sorted(order, key=lambda o: (o[0], o[1])):
            out[table].append(row)
        return out


MAPPINGS = [HumanMessagingMapping()]
