"""JARVIS's side of human messaging as interfaces and local rules: what the
messaging store, client and routes (delivery), the approval guard
(approval), file snapshots (files) and the transfer presentation (signals)
are built against, and what their tests replace.

The wire contract itself is `messaging_wire.py`, a generated copy of the
team server's `teamserver/messaging_models.py`; nothing here redefines a
wire shape. What is here is local: the switch, availability, the send
states of one approved revision, the dismissal guard, the page's shapes
and the presentation event.

Every local service takes its clock, id source, random source, transport,
store, presentation dispatcher and fault adapter
(`messaging_wire.NO_FAULTS` in production). Production and test adapters
implement the same Protocols; no route or setting approves, resets or
injects anything.
"""
from __future__ import annotations

import os
from datetime import datetime
from typing import Callable, Literal, Mapping, Optional, Protocol, runtime_checkable
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from jarvis import messaging_wire as wire

# -- the switch and availability ------------------------------------------------

# JARVIS's own switch, off unless set to 1/true/yes/on. Off: no messaging
# route, loop or tool runs and the Queue says it is switched off. Nothing
# stored (drafts, messages, approved files) is deleted by switching it off.
ENABLE_FLAG = "JARVIS_MESSAGING"
_TRUE = {"1", "true", "yes", "on"}


def messaging_enabled(env: Mapping[str, str] = os.environ) -> bool:
    return env.get(ENABLE_FLAG, "").strip().lower() in _TRUE


Availability = Literal["available", "switched_off", "not_paired", "unsupported"]

AVAILABILITY_SHOWN: dict[str, str] = {
    "available": "Messaging is available.",
    "switched_off": "Messaging is switched off on this JARVIS.",
    "not_paired": "Pair this JARVIS with the team server to send and receive messages.",
    "unsupported": "The team server does not offer messaging yet; your drafts are kept here.",
}


def availability(*, enabled: bool, paired: bool, capabilities: list[str]) -> Availability:
    """Fail closed, in this order. Voice, agents and the network view do not
    depend on any of it."""
    if not enabled:
        return "switched_off"
    if not paired:
        return "not_paired"
    if not wire.messaging_offered(capabilities):
        return "unsupported"
    return "available"


# -- one approved revision, from draft to accepted ---------------------------------

SendState = Literal[
    "draft",              # being edited; nothing can send it
    "awaiting_approval",  # frozen for review; the owner has not said yes
    "approved",           # the owner said yes to exactly this revision; key reserved
    "submitting",         # an attempt is on its way (persisted before it left)
    "accept_unknown",     # the answer was lost: retry the same key, never a new send
    "accepted",           # the team server has it
    "failed",             # refused for good (a non-retryable code), shown with Retry
    "cancelled",          # stopped before acceptance
    "superseded",         # an edit replaced this revision before it was sent
]
SendEvent = Literal[
    "review", "edit", "approve", "submit", "accepted", "answer_lost",
    "refused", "retry", "cancel",
]

_SEND_TRANSITIONS: dict[tuple[str, str], str] = {
    ("draft", "review"): "awaiting_approval",
    ("draft", "edit"): "draft",
    ("draft", "cancel"): "cancelled",
    ("awaiting_approval", "edit"): "superseded",
    ("awaiting_approval", "approve"): "approved",
    ("awaiting_approval", "cancel"): "cancelled",
    ("approved", "edit"): "superseded",
    ("approved", "submit"): "submitting",
    ("approved", "cancel"): "cancelled",
    ("submitting", "accepted"): "accepted",
    ("submitting", "answer_lost"): "accept_unknown",
    ("submitting", "refused"): "failed",
    ("accept_unknown", "retry"): "submitting",
    ("accept_unknown", "accepted"): "accepted",
    ("failed", "retry"): "submitting",
    ("failed", "edit"): "superseded",
    ("failed", "cancel"): "cancelled",
}
# Repeating what already happened is harmless (a second Approve click, a
# second acceptance answer) and changes nothing.
_IDEMPOTENT = {("approved", "approve"), ("accepted", "accepted"), ("cancelled", "cancel")}

SEND_UNRESOLVED = frozenset({"approved", "submitting", "accept_unknown", "failed"})


class SendRefused(Exception):
    def __init__(self, state: str, event: str):
        super().__init__(f"{event} is not possible while {state}")
        self.state, self.event = state, event


def next_send_state(state: SendState, event: SendEvent) -> SendState:
    """The one table every send path follows. Notably refused: approving a
    draft never reviewed, submitting anything not approved, editing or
    cancelling while an attempt is out or its answer is unknown (that is
    settled first: after acceptance there is no recall), and anything after
    acceptance."""
    if (state, event) in _IDEMPOTENT:
        return state
    try:
        return _SEND_TRANSITIONS[(state, event)]  # type: ignore[return-value]
    except KeyError:
        raise SendRefused(state, event) from None


def dismiss_blockers(*, unsent_text: bool, send_states: list[str],
                     uploads_pending: bool) -> list[str]:
    """Why an item cannot be dismissed yet, as codes for the page to word
    (empty: it can). The team server cannot see any of these."""
    blockers = []
    if unsent_text:
        blockers.append("unsent_text")
    if uploads_pending:
        blockers.append("upload_pending")
    if any(s in SEND_UNRESOLVED for s in send_states):
        blockers.append("send_unresolved")
    return blockers


# -- what the page reads (/api/team/messages) -----------------------------------

class _Local(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class LocalSend(_Local):
    """One revision's way out. Never content: the page has that already."""
    message_id: UUID
    content_revision: int = Field(ge=1)
    state: SendState
    content_digest: Optional[str] = None          # set from review on
    attempts: int = Field(default=0, ge=0)
    failure: Optional[str] = None                 # an ERRORS code or "transport"
    next_attempt_at: Optional[datetime] = None


class Draft(_Local):
    """The owner's unsent response or new message on an item: local only
    until approved. Normalized when saved (`normalize_text`), so review
    shows exactly what would be sent."""
    message_id: UUID
    thread_id: UUID
    in_reply_to_message_id: Optional[UUID] = None
    related_message_ids: list[UUID] = Field(default_factory=list,
                                            max_length=wire.RELATED_MAX)
    recipient_github_id: int
    content_revision: int = Field(ge=1)
    subject: str = Field(default="", max_length=wire.SUBJECT_MAX_CHARS)
    text: str = Field(default="", max_length=wire.TEXT_MAX_CHARS)
    attachments: list[wire.AttachmentRef] = Field(default_factory=list,
                                                  max_length=wire.ATTACHMENTS_MAX)
    send: LocalSend
    agent_drafted: bool = False                   # shown as a draft the model wrote


Direction = Literal["incoming", "outgoing"]


class LocalMessage(_Local):
    message: wire.AcceptedMessage
    direction: Direction                          # for a self-message, both are true;
    self_message: bool                            # it is shown once
    receipts: wire.ReceiptFacts


class LocalItem(_Local):
    work_state: wire.WorkState
    messages: list[LocalMessage]                  # by (accepted_at, message_id)
    draft: Optional[Draft] = None
    dismiss_blockers: list[str] = Field(default_factory=list)


class QueueSnapshot(_Local):
    availability: Availability
    items: list[LocalItem]
    stale: bool = False                           # last sync failed; this is the last known


# -- the transfer presentation (signals) ----------------------------------------

class TransferEvent(_Local):
    """Asks the page to draw one human transfer with the existing ray and orb
    machinery. It has no authority: delivery and read facts come only from
    the store. `event_id` is "human:" + a uuid, so it can never collide with
    an agent's numeric message number."""
    event_id: str = Field(pattern=r"^human:[0-9a-f-]{36}$")
    message_id: UUID
    thread_id: UUID
    direction: Direction
    source_node_id: UUID
    sender_github_id: int
    recipient_github_id: int
    occurred_at: datetime
    replay: bool                                  # on reconnect: reconcile, never animate


# -- the services' dependencies ----------------------------------------------------

Clock = Callable[[], datetime]


@runtime_checkable
class IdSource(Protocol):
    def new_id(self) -> UUID: ...


@runtime_checkable
class RandomSource(Protocol):
    def random(self) -> float: ...


class TransportError(Exception):
    """A request that did not succeed. `code` is an ERRORS code, or
    "transport" when no answer came (the request may still have been
    accepted: treat it as `answer_lost`, never as nothing happened)."""

    def __init__(self, code: str, latest: Optional[dict] = None,
                 retry_after: Optional[float] = None):
        super().__init__(code)
        self.code, self.latest, self.retry_after = code, latest, retry_after

    @property
    def retryable(self) -> bool:
        spec = wire.ERRORS.get(self.code)
        return self.code == "transport" or bool(spec and spec.retryable)


@runtime_checkable
class ServeTransport(Protocol):
    """The paired team server, through `team_client`'s session; the page
    never holds its credential. A connection generation change discards
    answers to requests made under the previous one."""

    async def capabilities(self) -> list[str]: ...

    async def send(self, request: wire.SendRequest) -> wire.SendResult: ...

    async def get(self, message_id: UUID) -> wire.AcceptedMessage: ...

    async def sync(self, continuation: Optional[str], limit: int) -> wire.SyncPage: ...

    async def receipt(self, message_id: UUID,
                      request: wire.ReceiptRequest) -> wire.ReceiptFacts: ...

    async def change_work_state(self, thread_id: UUID,
                                request: wire.WorkCommandRequest) -> wire.WorkState: ...


@runtime_checkable
class LocalMessagingStore(Protocol):
    """SQLite under `data_paths.db_path()`, namespaced by server and owner.
    Each write that the rules pair is one transaction: an approved envelope
    with its pending send; an incoming envelope with its work item and the
    receipt still owed. A hint goes out only after the commit."""

    def save_draft(self, server_id: str, owner_github_id: int, draft: Draft) -> Draft: ...

    def draft(self, server_id: str, owner_github_id: int, message_id: UUID) -> Optional[Draft]: ...

    def save_approved(self, server_id: str, owner_github_id: int,
                      request: wire.SendRequest) -> LocalSend: ...

    def set_send(self, server_id: str, owner_github_id: int, send: LocalSend) -> None: ...

    def unresolved_sends(self, server_id: str, owner_github_id: int) -> list[LocalSend]: ...

    def store_incoming(self, server_id: str, owner_github_id: int, entry: wire.SyncEntry,
                       receipt_owed: bool) -> bool: ...

    def receipts_owed(self, server_id: str, owner_github_id: int) -> list[UUID]: ...

    def sync_position(self, server_id: str, owner_github_id: int,
                      node_id: UUID) -> Optional[str]: ...

    def set_sync_position(self, server_id: str, owner_github_id: int, node_id: UUID,
                          continuation: Optional[str]) -> None: ...

    def snapshot(self, server_id: str, owner_github_id: int) -> list[LocalItem]: ...


class ApprovedSend(_Local):
    """What the guard releases: exactly the approved revision."""
    request: wire.SendRequest
    approved_at: datetime


class NotApproved(Exception):
    """The one guard refused: the stored revision is not approved, or changed."""


@runtime_checkable
class ApprovalGuard(Protocol):
    """The one backend gate every outgoing human message passes, whatever
    path started it (voice, the page, an agent's draft). Agents may draft
    and ask for review; only the owner's acceptance approves."""

    def require_approved_revision(self, message_id: UUID,
                                  content_revision: int) -> ApprovedSend: ...


@runtime_checkable
class AttachmentSnapshots(Protocol):
    """Private, immutable copies of the files the owner chose, hashed when
    copied. Sending reads these, never the original path."""

    def verified_attachment_manifest(self, message_id: UUID,
                                     content_revision: int) -> list[wire.AttachmentRef]: ...


@runtime_checkable
class PresentationDispatcher(Protocol):
    """Draws and announces transfers. `reserve_announcement` takes the
    one-time lease that decides which of the owner's nodes speaks; a
    browser's de-duplication is not a lease."""

    def present(self, event: TransferEvent) -> None: ...

    def reserve_announcement(self, event_id: str, node_id: UUID) -> Optional[str]: ...

    def complete_announcement(self, lease: str) -> None: ...
