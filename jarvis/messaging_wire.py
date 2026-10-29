# GENERATED from team_server/teamserver/messaging_models.py by
# scripts/generate_messaging_contract.py. Do not edit: edit that file and
# regenerate. Both test suites check this copy is exact.
"""The human-messaging contract (capability `messaging.v1`): the one
canonical definition of what crosses the wire between a paired JARVIS and the
team server, and the rules both sides apply to it.

This file is the source. It imports nothing but the standard library and
Pydantic, because it is used in two places that share no package: the team
server imports it from here, and JARVIS carries a generated copy
(`jarvis/messaging_wire.py`, written by `scripts/generate_messaging_contract.py`
and checked byte for byte by both test suites). The same script writes the
JSON fixtures (`tests/fixtures/messaging_contract.json`) and the page's types
(`frontend/src/messagingContract.ts`). Edit here, then regenerate.

What it holds:

    the envelope       a message as JARVIS submits it, and as it was accepted
    the digest         the one serialization the owner's approval binds to
    receipts           received / read, per message and recipient, first
                       timestamps kept, never moved backwards
    work state         one owner's view of one exchange: placement, the
                       reminder, addressed, dismissed and whose turn it is
    sync               keyset pages of a full reconciliation scan
    errors             stable codes, their HTTP status and whether to retry
    fault points       where an injected test fault may stop a transfer

Wire names are snake_case. Display names and node labels are presentation,
never authority: the sender, the server and the source node's owner come
from the authenticated session.
"""
from __future__ import annotations

import hashlib
import json
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Literal, Optional, Union
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

# -- identity of the contract -------------------------------------------------

CAPABILITY = "messaging.v1"          # advertised in /version `capabilities`
SCHEMA_VERSION = 1                   # every envelope carries it
# Changes when any wire shape, rule or fixture changes. Workers build
# against one revision; a breaking change bumps the number and regenerates
# every consumer in the same batch.
INTERFACE_REVISION = "messaging.v1-contract.1"
DIGEST_VERSION = 1


def messaging_offered(capabilities: list[str] | tuple[str, ...]) -> bool:
    """Whether a team server's /version `capabilities` offers this contract.
    An older server lacks it: messaging is then unavailable and nothing else
    changes."""
    return CAPABILITY in capabilities


# -- limits -------------------------------------------------------------------

SUBJECT_MAX_CHARS = 200
TEXT_MAX_CHARS = 20_000
RELATED_MAX = 10
ATTACHMENTS_MAX = 10
FILENAME_MAX_BYTES = 255
MEDIA_TYPE_MAX_CHARS = 127
# Proposed defaults (gui plan "Transport, persistence and file handling");
# the server may configure lower, never higher, before release.
FILE_MAX_BYTES = 25 * 1024 * 1024
PACKAGE_MAX_BYTES = 100 * 1024 * 1024
# The JSON body of POST /api/v1/messages: room for TEXT_MAX_CHARS of
# four-byte characters and the envelope. Separate from the global 16 KiB
# limit on every other route and from the streamed upload limits.
MESSAGE_REQUEST_MAX_BYTES = 128 * 1024
SYNC_PAGE_DEFAULT = 25
SYNC_PAGE_MAX = 50
SYNC_CONTINUATION_TTL_SEC = 600
RECEIPT_BATCH_MAX = 100
INT64_MAX = 2**63 - 1

_SHA256 = r"^[0-9a-f]{64}$"
_DIGEST = r"^sha256:[0-9a-f]{64}$"
_MEDIA_TYPE = r"^[a-z0-9][a-z0-9!#$&^_.+-]{0,62}/[a-z0-9][a-z0-9!#$&^_.+-]{0,62}$"

GithubId = Annotated[int, Field(ge=-(2**31), le=INT64_MAX)]   # staging test members are negative
Revision = Annotated[int, Field(ge=1, le=INT64_MAX)]


class _Strict(BaseModel):
    """Unknown fields are refused, so a misspelt field is an error and not a
    silently dropped fact. A newer schema version is refused outright."""
    model_config = ConfigDict(extra="forbid", frozen=True)


# -- normalization ------------------------------------------------------------
#
# Done ONCE, by JARVIS, when a draft is saved, before it is shown for review:
# what the owner reviews is already the normalized text, so the bytes
# approved are the bytes sent. The team server never rewrites content; it
# refuses text that is not already normalized (`content_not_normalized`).

_TEXT_ALLOWED_CONTROLS = {"\n", "\t"}


def normalize_text(value: str) -> str:
    """Line endings to LF and Unicode NFC. Other control characters are not
    removed here: `text_problem` refuses them, so nothing is silently lost."""
    return unicodedata.normalize("NFC", value.replace("\r\n", "\n").replace("\r", "\n"))


def normalize_subject(value: str) -> str:
    """One line: NFC, every run of whitespace one space, trimmed."""
    return " ".join(unicodedata.normalize("NFC", value).split())


def _has_control(value: str, allowed: set[str]) -> bool:
    return any(unicodedata.category(c) in ("Cc", "Cf", "Zl", "Zp") and c not in allowed
               for c in value)


def text_problem(value: str) -> Optional[str]:
    if value != normalize_text(value):
        return "content_not_normalized"
    if _has_control(value, _TEXT_ALLOWED_CONTROLS):
        return "invalid_request"
    return None


def subject_problem(value: str) -> Optional[str]:
    if value != normalize_subject(value):
        return "content_not_normalized"
    if _has_control(value, set()):
        return "invalid_request"
    return None


def filename_problem(value: str) -> Optional[str]:
    """A safe display name: no path, no control character, not a dot name."""
    if value != unicodedata.normalize("NFC", value) or value != value.strip():
        return "content_not_normalized"
    if (not value or value in (".", "..") or "/" in value or "\\" in value
            or len(value.encode("utf-8")) > FILENAME_MAX_BYTES or _has_control(value, set())):
        return "invalid_request"
    return None


# -- the envelope -------------------------------------------------------------

class AttachmentRef(_Strict):
    """One file in a message's immutable manifest. The bytes are uploaded
    separately; this binds them by size and SHA-256. The order of the
    manifest is part of what was approved."""
    attachment_id: UUID
    filename: str = Field(min_length=1, max_length=FILENAME_MAX_BYTES)
    media_type: str = Field(max_length=MEDIA_TYPE_MAX_CHARS, pattern=_MEDIA_TYPE,
                            description="The sender's hint only; never a safety decision")
    size_bytes: int = Field(ge=0, le=FILE_MAX_BYTES)
    sha256: str = Field(pattern=_SHA256)

    @model_validator(mode="after")
    def _safe_name(self):
        if filename_problem(self.filename):
            raise ValueError("filename is not a safe display name")
        return self


class MessageContent(_Strict):
    """What the owner approves, apart from who sends it (the session says
    that). `thread_id` is the root message's id: a new conversation's root
    has `thread_id == message_id`; a reply carries its parent's thread and
    names its exact parent."""
    schema_version: Literal[1] = SCHEMA_VERSION
    message_id: UUID
    thread_id: UUID
    in_reply_to_message_id: Optional[UUID] = None
    related_message_ids: list[UUID] = Field(default_factory=list, max_length=RELATED_MAX)
    recipient_github_id: GithubId
    content_revision: Revision
    subject: str = Field(default="", max_length=SUBJECT_MAX_CHARS)
    text: str = Field(default="", max_length=TEXT_MAX_CHARS)
    attachments: list[AttachmentRef] = Field(default_factory=list, max_length=ATTACHMENTS_MAX)

    @model_validator(mode="after")
    def _rules(self):
        if text_problem(self.text) or subject_problem(self.subject):
            raise ValueError("subject or text is not normalized")
        if not self.text and not self.attachments:
            raise ValueError("a message has text, a file, or both")
        root = self.thread_id == self.message_id
        if root and self.in_reply_to_message_id is not None:
            raise ValueError("a thread's root replies to nothing")
        if not root and self.in_reply_to_message_id is None:
            raise ValueError("a message in an existing thread names its exact parent")
        linked = [self.message_id, *self.related_message_ids]
        if len(set(linked)) != len(linked) or self.in_reply_to_message_id == self.message_id:
            raise ValueError("related ids are distinct and never the message itself")
        ids = [a.attachment_id for a in self.attachments]
        if len(set(ids)) != len(ids):
            raise ValueError("each attachment appears once")
        if sum(a.size_bytes for a in self.attachments) > PACKAGE_MAX_BYTES:
            raise ValueError("the files are larger than one message may carry")
        return self


class SendRequest(MessageContent):
    """POST /api/v1/messages. `content_digest` is `content_digest()` over this
    content with the session's server and sender; the server recomputes it
    and refuses a mismatch. `idempotency_key` is reserved when the revision
    is approved and kept for every retry of exactly that revision."""
    source_node_id: UUID
    idempotency_key: UUID
    content_digest: str = Field(pattern=_DIGEST)


class AcceptedMessage(MessageContent):
    """The team server's one authoritative record of a message: immutable
    once inserted. `accepted_at` is the server's clock and, with
    `message_id`, the order of everything."""
    server_id: str = Field(min_length=1, max_length=64)
    sender_github_id: GithubId
    source_node_id: UUID
    content_digest: str = Field(pattern=_DIGEST)
    idempotency_key: UUID
    accepted_at: datetime


class SendResult(_Strict):
    """The answer to POST /api/v1/messages, the same for a first send and for
    an identical retry. `duplicate` says the server had it already."""
    message: AcceptedMessage
    duplicate: bool


# -- the digest -----------------------------------------------------------------

def digest_input(content: MessageContent, *, server_id: str, sender_github_id: int) -> dict:
    """Everything that can change what the owner sends. Not in it: the source
    node (the session's transport context), the idempotency key (the
    operation, not the content) and anything presentational."""
    return {
        "digest_version": DIGEST_VERSION,
        "schema_version": content.schema_version,
        "server_id": server_id,
        "sender_github_id": sender_github_id,
        "recipient_github_id": content.recipient_github_id,
        "message_id": str(content.message_id),
        "thread_id": str(content.thread_id),
        "in_reply_to_message_id": (str(content.in_reply_to_message_id)
                                   if content.in_reply_to_message_id else None),
        "related_message_ids": sorted(str(r) for r in content.related_message_ids),
        "content_revision": content.content_revision,
        "subject": content.subject,
        "text": content.text,
        "attachments": [{"attachment_id": str(a.attachment_id), "filename": a.filename,
                         "media_type": a.media_type, "size_bytes": a.size_bytes,
                         "sha256": a.sha256} for a in content.attachments],
    }


def canonical_bytes(value: dict) -> bytes:
    """Sorted keys, no whitespace, UTF-8 (not \\u escapes)."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def content_digest(content: MessageContent, *, server_id: str, sender_github_id: int) -> str:
    return "sha256:" + hashlib.sha256(canonical_bytes(
        digest_input(content, server_id=server_id, sender_github_id=sender_github_id))).hexdigest()


def content_of(message: MessageContent) -> MessageContent:
    """The approvable content of a request or an accepted message."""
    return MessageContent.model_validate(message.model_dump(include=set(MessageContent.model_fields)))


# -- receipts -------------------------------------------------------------------

ReceiptKind = Literal["received", "read"]


class NodeReceipt(_Strict):
    node_id: UUID
    received_at: datetime


class ReceiptFacts(_Strict):
    """The recipient's facts about one message. `received_at` is the first
    durable local storage by any of the recipient's nodes, `read_at` the
    first time the owner had the body displayed. Each is set once, by the
    server's clock, and never moves; a read may arrive before a received
    and is kept (the received fact then stays unset until it comes)."""
    message_id: UUID
    recipient_github_id: GithubId
    received_at: Optional[datetime] = None
    read_at: Optional[datetime] = None
    nodes: list[NodeReceipt] = Field(default_factory=list)
    revision: Revision


class ReceiptRequest(_Strict):
    """POST /api/v1/messages/{message_id}/receipts, by the recipient's own
    paired node. `read` only ever comes from the owner's GUI after the body
    was displayed (or an explicit mark-read), never from a notification, a
    summary or the model."""
    kind: ReceiptKind
    node_id: UUID


def merge_receipt(facts: ReceiptFacts, kind: ReceiptKind, node_id: UUID,
                  now: datetime) -> ReceiptFacts:
    """Apply one receipt idempotently. The revision moves only when a fact
    is new; a repeat changes nothing."""
    received_at, read_at, nodes = facts.received_at, facts.read_at, list(facts.nodes)
    if kind == "received":
        if received_at is None:
            received_at = now
        if all(n.node_id != node_id for n in nodes):
            nodes.append(NodeReceipt(node_id=node_id, received_at=now))
    elif read_at is None:
        read_at = now
    changed = (received_at, read_at, nodes) != (facts.received_at, facts.read_at, facts.nodes)
    return facts.model_copy(update={
        "received_at": received_at, "read_at": read_at, "nodes": nodes,
        "revision": facts.revision + 1 if changed else facts.revision})


# -- one owner's work state -----------------------------------------------------
#
# The unit of the queue is one exchange: one owner's work item per thread.
# Its key is (server, owner_github_id, thread_id); the page's item id IS the
# thread id. A reply joins its parent's item, a new unrelated message makes
# another, and a related link connects two items without merging them or
# granting access. Self-send is one item. Messages, receipts and replies
# stay on the messages; the item holds only the owner's own cues.

Placement = Literal["queued", "active", "inactive"]


class WorkState(_Strict):
    server_id: str = Field(min_length=1, max_length=64)
    owner_github_id: GithubId
    thread_id: UUID
    correspondent_github_id: GithubId      # == owner for a self-thread
    placement: Placement
    arrival_at: datetime                   # see `on_message`; put-back never moves it
    unread_reminder: bool                  # personal; never retracts a read receipt
    addressed_at: Optional[datetime] = None
    dismissed_at: Optional[datetime] = None
    action_owner_github_id: GithubId       # whose turn: owner or correspondent
    revision: Revision


def item_order_key(state: WorkState) -> tuple:
    """Newest arrival first, the thread id breaking ties: sort with this key
    and reverse=True."""
    return (state.arrival_at, str(state.thread_id))


def new_work_state(*, server_id: str, owner_github_id: int, message: AcceptedMessage) -> WorkState:
    """The owner's item for a thread whose first message the owner has just
    sent or received. Sent by the owner to someone else: Active / Outbox and
    their turn. Received, or sent to oneself: Waiting in the queue, and the
    owner's turn."""
    outgoing = message.sender_github_id == owner_github_id
    self_thread = outgoing and message.recipient_github_id == owner_github_id
    correspondent = message.recipient_github_id if outgoing else message.sender_github_id
    return WorkState(
        server_id=server_id, owner_github_id=owner_github_id, thread_id=message.thread_id,
        correspondent_github_id=correspondent,
        placement="active" if outgoing and not self_thread else "queued",
        arrival_at=message.accepted_at, unread_reminder=not outgoing or self_thread,
        action_owner_github_id=correspondent if outgoing and not self_thread
        else owner_github_id,
        revision=1)


def on_message(state: WorkState, message: AcceptedMessage) -> WorkState:
    """An accepted message joined this owner's existing item. An incoming one
    (including to oneself) is a new arrival: it moves `arrival_at` to the
    top, sets the reminder, gives the owner the turn, and brings a dismissed
    item back to the queue. One the owner sent to someone else puts the item
    in Active / Outbox and makes it their turn. Receipts never come here."""
    owner = state.owner_github_id
    incoming = message.recipient_github_id == owner
    if incoming:
        update = {"arrival_at": max(state.arrival_at, message.accepted_at),
                  "unread_reminder": True, "action_owner_github_id": owner}
        if state.placement == "inactive":
            update.update(placement="queued", dismissed_at=None)
    else:
        update = {"action_owner_github_id": state.correspondent_github_id,
                  "placement": "active", "dismissed_at": None}
    return _revised(state, update)


def _revised(state: WorkState, update: dict) -> WorkState:
    changed = any(getattr(state, k) != v for k, v in update.items())
    return state.model_copy(update={**update, "revision": state.revision + 1}) if changed else state


class _Command(_Strict):
    expected_revision: Revision


class OpenItem(_Command):
    """Activate: Waiting -> Active. Records no read receipt; that is a
    separate ReceiptRequest once a specific body was displayed."""
    command: Literal["open"] = "open"


class PutBack(_Command):
    command: Literal["put_back"] = "put_back"


class SetAddressed(_Command):
    command: Literal["set_addressed"] = "set_addressed"
    addressed: bool


class SetUnreadReminder(_Command):
    command: Literal["set_unread_reminder"] = "set_unread_reminder"
    unread: bool


class SetActionOwner(_Command):
    """Move to my side / Waiting on <correspondent>: the owner's cue only."""
    command: Literal["set_action_owner"] = "set_action_owner"
    action_owner_github_id: GithubId


class Dismiss(_Command):
    """JARVIS refuses this locally while the item has unsent text or an
    unresolved send (`dismiss_blockers`); the server cannot see those."""
    command: Literal["dismiss"] = "dismiss"


class Restore(_Command):
    command: Literal["restore"] = "restore"


WorkCommand = Annotated[Union[OpenItem, PutBack, SetAddressed, SetUnreadReminder,
                              SetActionOwner, Dismiss, Restore],
                        Field(discriminator="command")]


class WorkCommandRequest(_Strict):
    """PUT /api/v1/messages/threads/{thread_id}/work-state."""
    change: WorkCommand


class ContractError(Exception):
    """A rule refused something: `code` is one of ERRORS. `latest` carries
    the current record for a revision conflict."""

    def __init__(self, code: str, latest: Optional[BaseModel] = None):
        super().__init__(code)
        self.code, self.latest = code, latest


def apply_work_command(state: WorkState, change: WorkCommand, now: datetime) -> WorkState:
    """One owner's change to their own item, by optimistic concurrency: a
    stale `expected_revision` is refused with the latest state, never
    overwritten. A change that changes nothing keeps the revision, so a
    retried identical command is safe."""
    if change.expected_revision != state.revision:
        raise ContractError("revision_conflict", latest=state)
    if isinstance(change, OpenItem):
        update = {"placement": "active", "dismissed_at": None}
    elif isinstance(change, PutBack):
        update = {"placement": "queued", "dismissed_at": None}
    elif isinstance(change, SetAddressed):
        update = {"addressed_at": (state.addressed_at or now) if change.addressed else None}
    elif isinstance(change, SetUnreadReminder):
        update = {"unread_reminder": change.unread}
    elif isinstance(change, SetActionOwner):
        allowed = {state.owner_github_id, state.correspondent_github_id}
        if change.action_owner_github_id not in allowed:
            raise ContractError("invalid_request")
        self_thread = state.correspondent_github_id == state.owner_github_id
        update = {"action_owner_github_id": state.owner_github_id if self_thread
                  else change.action_owner_github_id}
    elif isinstance(change, Dismiss):
        update = {"placement": "inactive",
                  "dismissed_at": state.dismissed_at if state.placement == "inactive" else now}
    else:
        if state.placement != "inactive":
            return state
        update = {"placement": "queued", "dismissed_at": None}
    return _revised(state, update)


# -- sync -----------------------------------------------------------------------
#
# The correctness baseline is a full keyset-paginated reconciliation scan of
# every message the owner may see, ordered by (accepted_at, message_id), with
# each finished scan starting again from the beginning (a new epoch). Each
# entry carries the current receipt and work-state revisions, and the client
# keeps whichever revision is higher. Something inserted or changed behind
# the cursor is found by the next scan. The continuation is opaque, bound by
# the server to the owner, server, node, epoch and last key, and expires
# after SYNC_CONTINUATION_TTL_SEC (`sync_restart`: begin a new scan). It
# is never an access credential. Nothing is deleted in v1: a record absent
# from a scan is never pruned locally.

class SyncEntry(_Strict):
    message: AcceptedMessage
    receipts: ReceiptFacts
    work_state: WorkState                  # the owner's item for message.thread_id


class SyncPage(_Strict):
    """GET /api/v1/messages/sync?continuation=…&limit=…"""
    epoch: str = Field(min_length=1, max_length=64)
    entries: list[SyncEntry] = Field(max_length=SYNC_PAGE_MAX)
    continuation: Optional[str] = Field(default=None, max_length=512)
    scan_complete: bool                   # no continuation: the next call starts a new epoch
    server_time: datetime


# -- errors ---------------------------------------------------------------------

@dataclass(frozen=True)
class ErrorSpec:
    status: int
    retryable: bool        # JARVIS retries it itself, with RETRY_* backoff
    shown: str             # what the owner is told; never the content


# The error body is the team server's existing shape:
#   {"error": {"code": <code>, "message": <text>, ...extra}}
# `revision_conflict` adds "latest" (the current record); `rate_limited` comes
# with Retry-After. A message the caller may not see is `message_not_found`,
# exactly as one that does not exist, so a guessed id discloses nothing.
ERRORS: dict[str, ErrorSpec] = {
    "invalid_request": ErrorSpec(422, False, "The message was refused as malformed."),
    "unsupported_schema_version": ErrorSpec(422, False, "This JARVIS and the team server "
                                                        "disagree on the message format."),
    "content_not_normalized": ErrorSpec(422, False, "The message text was not prepared "
                                                    "for sending; review it again."),
    "digest_mismatch": ErrorSpec(422, False, "The message changed after it was approved; "
                                             "review it again."),
    "too_large": ErrorSpec(413, False, "The message or its files are too large."),
    "quota_exceeded": ErrorSpec(413, False, "The team server has no room for these files."),
    "session_invalid": ErrorSpec(401, False, "Sign in to the team server again."),
    "recipient_not_allowed": ErrorSpec(403, False, "That person is not an active member "
                                                   "of the team."),
    "relation_not_allowed": ErrorSpec(403, False, "A linked message is not one both of "
                                                  "you can see."),
    "source_node_not_owned": ErrorSpec(403, False, "This JARVIS is not paired to you."),
    "message_not_found": ErrorSpec(404, False, "That message is not available."),
    "idempotency_conflict": ErrorSpec(409, False, "A different message was already sent "
                                                  "under this one's identity."),
    "revision_conflict": ErrorSpec(409, False, "This item changed on another JARVIS; "
                                               "showing the latest."),
    "attachment_not_ready": ErrorSpec(409, False, "A file is not uploaded and verified; "
                                                  "upload it again."),
    "sync_restart": ErrorSpec(410, True, "Catching up from the beginning."),
    "rate_limited": ErrorSpec(429, True, "The team server asked JARVIS to slow down."),
    "messaging_disabled": ErrorSpec(503, False, "Messaging is switched off on the team "
                                                "server."),
    "dependency_unavailable": ErrorSpec(503, True, "The team server cannot reach its "
                                                   "database."),
}
# Automatic retries of a retryable code or a transport failure: full-jitter
# exponential backoff from RETRY_BASE_SEC, capped at RETRY_MAX_SEC, at most
# RETRY_MAX_ATTEMPTS before the send is shown as failed with a Retry action.
# A Retry re-sends the same approved revision under the same idempotency key.
RETRY_BASE_SEC = 1.0
RETRY_MAX_SEC = 60.0
RETRY_MAX_ATTEMPTS = 8


def retry_delay(attempt: int, rand: float) -> float:
    """Seconds before retry `attempt` (1-based), `rand` in [0, 1) from the
    injected random source."""
    return rand * min(RETRY_MAX_SEC, RETRY_BASE_SEC * (2 ** max(0, attempt - 1)))


# -- fault points ---------------------------------------------------------------
#
# Where a test may stop a transfer, through the injected `Faults` adapter
# (both services take one; production gets `NO_FAULTS`). A fault raises
# `InjectedFault(point)`, so a test names exactly which boundary failed.
# There is no route, flag or environment variable that injects one.

FAULT_POINTS: tuple[str, ...] = (
    "client_before_send",                   # JARVIS: approved and saved, request not sent
    "server_after_commit_before_response",  # Serve: accepted durably, the answer lost
    "client_before_local_save",             # JARVIS: the answer in hand, not yet stored
    "client_after_local_save",              # JARVIS: stored, the hint not yet published
    "client_before_receipt_ack",            # JARVIS: incoming stored, receipt not sent
    "server_before_attachment_finalize",
    "server_after_attachment_finalize",
    "server_after_pin_before_insert",       # Serve: files pinned, message not inserted
)


class InjectedFault(Exception):
    def __init__(self, point: str):
        super().__init__(point)
        self.point = point


class Faults:
    """Never fires. Production services hold `NO_FAULTS`."""

    def check(self, point: str) -> None:
        if point not in FAULT_POINTS:
            raise ValueError(f"unknown fault point {point}")


class ArmedFaults(Faults):
    """A test's faults: `arm(point)` makes the next `times` checks of that
    point raise."""

    def __init__(self):
        self._armed: dict[str, int] = {}

    def arm(self, point: str, times: int = 1) -> None:
        super().check(point)
        self._armed[point] = times

    def check(self, point: str) -> None:
        super().check(point)
        if self._armed.get(point, 0) > 0:
            self._armed[point] -= 1
            raise InjectedFault(point)


NO_FAULTS = Faults()
