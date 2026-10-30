"""JARVIS's side of human messaging as interfaces and local rules: what the
messaging store, client and routes (delivery), the approval guard
(approval), file snapshots (files) and the transfer presentation (signals)
are built against, and what their tests replace.

The wire contract itself is `messaging_wire.py`, a generated copy of the
team server's `teamserver/messaging_models.py`; nothing here redefines a
wire shape. What is here is local: the switch, availability, the send
states of one approved revision, the dismissal guard, the page's routes
and shapes, the socket frames and the presentation event.

Every local service takes its clock, id source, random source, transport,
store, presentation dispatcher and fault adapter
(`messaging_wire.NO_FAULTS` in production). Production and test adapters
implement the same Protocols. No route other than the owner's existing
acceptance actions (the capture box's Accept/Send, and the voice read-back
heard in full) approves anything, and no route or setting resets or
injects anything.

The page's routes (`messaging_api.py`, behind the existing web boundary;
the page never holds the team server's credential). A refusal is the team
server's error body, `{"error": {"code", "message", "latest"?}}`, with a
code from `wire.ERRORS` or `LOCAL_ERRORS` and that code's status;
`revision_conflict` carries the current record as `latest`.

    GET    /api/team/messages                          -> QueueSnapshot
    GET    /api/team/messages/history?continuation&limit -> HistoryPage
    GET    /api/team/messages/recipients               -> wire.RecipientList
    GET    /api/team/messages/threads/{thread_id}      -> LocalItem
    PUT    /api/team/messages/threads/{thread_id}/work-state
                                 wire.WorkCommandRequest -> WorkCommandResult
    POST   /api/team/messages/drafts                   DraftSave -> Draft (201)
    PUT    /api/team/messages/drafts/{message_id}      DraftSave -> Draft
    DELETE /api/team/messages/drafts/{message_id}?expected_draft_revision=N  (204)
    POST   /api/team/messages/drafts/{message_id}/review  DraftAction -> Draft
    POST   /api/team/messages/drafts/{message_id}/cancel  DraftAction -> Draft
    POST   /api/team/messages/drafts/{message_id}/retry   DraftAction -> Draft
    POST   /api/team/messages/drafts/{message_id}/files
               (the file's bytes; X-Filename and Content-Type headers) -> LocalFile
    POST   /api/team/messages/{message_id}/read                -> LocalMessage
    POST   /api/team/messages/{message_id}/files/{attachment_id}/download -> LocalFile
    POST   /api/team/messages/{message_id}/files/{attachment_id}/reveal   (204)

Approving is not among them: the inline review's Send is the existing
`POST /api/capture/{capture_id}/send` on the capture the review opened
(`Draft.capture_id`), and the voice path is the existing read-back.
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime
from typing import (AsyncIterator, Callable, Literal, Mapping, Optional, Protocol,
                    runtime_checkable)
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


# -- the page's own refusals ---------------------------------------------------------

# Codes the page can get from JARVIS that the team server never sends,
# with the same shape as wire.ERRORS. "transport" is also what a send's
# `failure` says when no answer came.
LOCAL_ERRORS: dict[str, wire.ErrorSpec] = {
    "messaging_unavailable": wire.ErrorSpec(503, False, "Messaging is not available; "
                                                        "see the Queue for why."),
    "draft_not_found": wire.ErrorSpec(404, False, "That draft is not here any more."),
    "send_refused": wire.ErrorSpec(409, False, "That is not possible at this point of "
                                               "sending."),
    "not_approved": wire.ErrorSpec(409, False, "This revision has not been approved; "
                                               "review it again."),
    "snapshot_missing": wire.ErrorSpec(409, False, "A file's private copy is missing; "
                                                   "attach it again."),
    "transport": wire.ErrorSpec(502, True, "The team server did not answer."),
}


# -- one approved revision, from draft to accepted ---------------------------------
#
# Approval and the cancel window: the owner's yes is the existing acceptance
# (Accept/Send on the review's capture, or its read-back heard in full).
# Staging, the cancel window and the turn deferral then run as for an agent
# while the revision stays `awaiting_approval`. Only the hand-over
# (`Destination.deliver`) records the approval (`ApprovalGuard.approve`)
# and the approved envelope, in one transaction: that is `approved`, and it
# is what delivery submits. So a cancel in the window leaves it awaiting
# approval and nothing for delivery to find, and a restart while it is
# staged (the staged list lives in memory) loses only the pending yes: the
# owner is asked again, and nothing is sent unheard.

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
# Repeating what already happened is harmless (a second Review, Approve or
# Send click, a second acceptance answer) and changes nothing.
_IDEMPOTENT = {("awaiting_approval", "review"), ("approved", "approve"),
               ("submitting", "submit"), ("accepted", "accepted"), ("cancelled", "cancel")}

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


DismissBlocker = Literal["unsent_text", "upload_pending", "send_unresolved"]

DISMISS_BLOCKERS_SHOWN: dict[str, str] = {
    "unsent_text": "There is a reply here that has not been sent.",
    "upload_pending": "A file is still being uploaded.",
    "send_unresolved": "A send has not finished; retry it or cancel it first.",
}


def dismiss_blockers(*, unsent_text: bool, send_states: list[str],
                     uploads_pending: bool) -> list[DismissBlocker]:
    """Why an item cannot be dismissed yet, as codes for the page to word
    (empty: it can). The team server cannot see any of these."""
    blockers: list[DismissBlocker] = []
    if unsent_text:
        blockers.append("unsent_text")
    if uploads_pending:
        blockers.append("upload_pending")
    if any(s in SEND_UNRESOLVED for s in send_states):
        blockers.append("send_unresolved")
    return blockers


# -- the page's shapes ---------------------------------------------------------------

class _Local(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


# A file as the page shows it. Sending: staged (the private copy is made)
# -> uploading -> available (verified on the team server). Receiving:
# available (on the team server, not here) -> downloading -> verified (here,
# size and hash checked; only now can it be revealed). Either side: failed
# (with a code), missing (the private copy is gone: attach it again).
FileState = Literal["staged", "uploading", "available", "downloading", "verified",
                    "failed", "missing"]


class LocalFile(_Local):
    attachment_id: UUID
    state: FileState
    bytes_done: int = Field(default=0, ge=0)
    failure: Optional[str] = None                 # a wire.ERRORS or LOCAL_ERRORS code


def uploads_pending(files: list[LocalFile]) -> bool:
    return any(f.state in ("staged", "uploading") for f in files)


class LocalSend(_Local):
    """One revision's way out. Never content: the page has that already."""
    message_id: UUID
    content_revision: int = Field(ge=1)
    state: SendState
    content_digest: Optional[str] = None          # set from review on
    attempts: int = Field(default=0, ge=0)
    failure: Optional[str] = None                 # a wire.ERRORS or LOCAL_ERRORS code
    next_attempt_at: Optional[datetime] = None


class Draft(_Local):
    """The owner's unsent message on an item, or a new conversation's first
    message: local only until approved.

    `draft_revision` moves on every save; a save names the revision it
    edited, and a stale one is `revision_conflict` with this draft as
    `latest`, so an agent's draft and the page's debounced save never
    overwrite each other silently. `send.content_revision` is the revision
    the owner reviews and approves: it moves only when an edit follows a
    review (`revise`). Text and subject are normalized when saved
    (`normalize_text`, `normalize_subject`); the page's editor keeps its
    own text and only review shows the saved, normalized form. An item has
    at most one editable draft (state `draft` or `awaiting_approval`);
    others on it are on their way out."""
    message_id: UUID
    thread_id: UUID                               # == message_id for a new conversation
    in_reply_to_message_id: Optional[UUID] = None
    related_message_ids: list[UUID] = Field(default_factory=list,
                                            max_length=wire.RELATED_MAX)
    recipient_github_id: int
    draft_revision: int = Field(ge=1)
    subject: str = Field(default="", max_length=wire.SUBJECT_MAX_CHARS)
    text: str = Field(default="", max_length=wire.TEXT_MAX_CHARS)
    attachments: list[wire.AttachmentRef] = Field(default_factory=list,
                                                  max_length=wire.ATTACHMENTS_MAX)
    files: list[LocalFile] = Field(default_factory=list,
                                   max_length=wire.ATTACHMENTS_MAX)   # attachments' order
    send: LocalSend
    capture_id: Optional[int] = None              # the review's capture, while one is open
    agent_drafted: bool = False                   # shown as a draft the model wrote


def draft_content(draft: Draft) -> wire.MessageContent:
    """The content a draft would send, as review digests it and approval
    binds it (refused by the wire rules if it could not be sent)."""
    return wire.MessageContent(
        message_id=draft.message_id, thread_id=draft.thread_id,
        in_reply_to_message_id=draft.in_reply_to_message_id,
        related_message_ids=list(draft.related_message_ids),
        recipient_github_id=draft.recipient_github_id,
        content_revision=draft.send.content_revision, subject=draft.subject, text=draft.text,
        attachments=list(draft.attachments))


def revise(draft: Draft) -> LocalSend:
    """The send record an edit leaves the draft with: unchanged while it is
    still a draft; after review (awaiting approval, approved, failed,
    cancelled or superseded) editing goes on as content_revision + 1, a new
    draft. While an attempt is out, unknown or accepted it is refused
    (SendRefused): that is settled first."""
    state = draft.send.state
    if state == "draft":
        return draft.send
    if state not in ("cancelled", "superseded"):
        next_send_state(state, "edit")
    return LocalSend(message_id=draft.message_id,
                     content_revision=draft.send.content_revision + 1, state="draft")


class DraftSave(_Local):
    """A save from the page or an agent tool. `expected_draft_revision` is
    None only when creating. A new conversation has no `thread_id` (JARVIS
    uses the message id it mints); a reply names its item and exact parent.
    `attachment_ids` are files already copied privately on this JARVIS
    (`.../files`), in the order they will be sent."""
    expected_draft_revision: Optional[int] = Field(default=None, ge=1)
    thread_id: Optional[UUID] = None
    in_reply_to_message_id: Optional[UUID] = None
    related_message_ids: list[UUID] = Field(default_factory=list,
                                            max_length=wire.RELATED_MAX)
    recipient_github_id: int
    subject: str = Field(default="", max_length=wire.SUBJECT_MAX_CHARS)
    text: str = Field(default="", max_length=wire.TEXT_MAX_CHARS)
    attachment_ids: list[UUID] = Field(default_factory=list, max_length=wire.ATTACHMENTS_MAX)


class DraftAction(_Local):
    """Review, cancel or retry the draft as it was at this revision."""
    expected_draft_revision: int = Field(ge=1)


Direction = Literal["incoming", "outgoing"]


class LocalMessage(_Local):
    """One message in an item. A message to oneself is served once, as
    `outgoing` with `self_message` true; its receipts are the owner's own
    as recipient (each of their nodes in `nodes`)."""
    message: wire.AcceptedMessage
    direction: Direction
    self_message: bool
    receipts: wire.ReceiptFacts
    files: list[LocalFile] = Field(default_factory=list,
                                   max_length=wire.ATTACHMENTS_MAX)   # manifest order


class LocalItem(_Local):
    work_state: wire.WorkState
    messages: list[LocalMessage]                  # by (accepted_at, message_id)
    drafts: list[Draft] = Field(default_factory=list)
    # A work command made while the team server could not be reached, sent
    # with its expected revision once it can (the latest one per item).
    pending_command: Optional[wire.WorkCommandRequest] = None
    dismiss_blockers: list[DismissBlocker] = Field(default_factory=list)


HISTORY_PAGE_DEFAULT = 25
HISTORY_PAGE_MAX = 50


class QueueSnapshot(_Local):
    """What the Queue reads: Waiting and Active / Outbox (never dismissed
    items: those are paged from history), and the new conversations not yet
    accepted, whatever their send state, so nothing on its way out is
    invisible. `server_id` and `owner_github_id` say whose it is (None when
    not paired): a snapshot for another pairing is not this one."""
    server_id: Optional[str] = None
    owner_github_id: Optional[int] = None
    availability: Availability
    items: list[LocalItem]
    unsent: list[Draft] = Field(default_factory=list)
    history_count: int = Field(default=0, ge=0)
    stale: bool = False                           # last sync failed; this is the last known


class HistoryPage(_Local):
    """Dismissed items, newest arrival first (`wire.item_order_key`)."""
    items: list[LocalItem] = Field(max_length=HISTORY_PAGE_MAX)
    continuation: Optional[str] = Field(default=None, max_length=512)


class WorkCommandResult(_Local):
    """The item's state after a command. `pending`: the team server could
    not be reached, the command is kept and sent with its expected revision
    when it can be, and `work_state` is the last known."""
    work_state: wire.WorkState
    pending: bool = False


# -- the read-back of a human message ------------------------------------------------
#
# A review opens a capture whose text is this read-back and which carries
#     capture["human"] = {"github_id", "message_id", "content_revision",
#                         "content_digest"}
# with capture["to"] the recipient's label. server.py picks the human
# Recipient when capture["human"] is set and agent resolution otherwise; a
# human is never an agent session. Because the read-back names the
# recipient, the subject, the text and every file with its size, any change
# to them changes the capture's text and so undoes its acceptance
# (`transmission.accepted`); a new content revision closes the capture.

def spoken_size(size_bytes: int) -> str:
    for unit, scale in (("gigabytes", 1 << 30), ("megabytes", 1 << 20), ("kilobytes", 1 << 10)):
        if size_bytes >= scale:
            return f"{size_bytes / scale:.1f}".rstrip("0").rstrip(".") + " " + unit
    return f"{size_bytes} byte" + ("" if size_bytes == 1 else "s")


def human_readback(draft: Draft, recipient_label: str) -> str:
    """Exactly what is read back, and accepted, for a human message."""
    lines = [f"To {recipient_label}."]
    if draft.subject:
        lines.append(f"Subject: {draft.subject}.")
    if draft.text:
        lines.append(draft.text)
    if draft.attachments:
        lines.append("Files: " + "; ".join(f"{a.filename}, {spoken_size(a.size_bytes)}"
                                           for a in draft.attachments) + ".")
    return "\n".join(lines)


# -- the transfer presentation (signals) ----------------------------------------
#
# On /ws/sessions: {"type": MESSAGES_HINT} after any committed messaging
# change (read /api/team/messages again; a "snapshot" frame means the same),
# and {"type": TRANSFER_FRAME, "event": TransferEvent} for a transfer to
# draw. A message's transfer is presented when it is new to this node: an
# outgoing one when its hand-over succeeded (`Destination.went`), an
# incoming one when the store first holds it as the owner's incoming
# (`store_page` / `record_accepted` say which). A message the owner sent
# from another of their nodes is stored, not presented.

MESSAGES_HINT = "messages"
TRANSFER_FRAME = "human_transfer"
_EVENT_NAMESPACE = UUID("5f0c2a52-0f8e-4f62-9b1d-6a3c8e9d4b71")


def transfer_event_id(message_id: UUID, direction: str) -> str:
    """The same on every node of the owner, so a lease and a page's
    already-shown set agree: "human:" + uuid5 over the message and its
    direction."""
    return "human:" + str(uuid.uuid5(_EVENT_NAMESPACE, f"{message_id}:{direction}"))


class TransferEvent(_Local):
    """Asks the page to draw one human transfer with the existing ray and orb
    machinery. It has no authority: delivery and read facts come only from
    the store. `event_id` is `transfer_event_id`, so it can never collide
    with an agent's numeric message number. `replay` is true only when
    JARVIS re-sends recent events to a page that (re)connected, so it can
    reconcile rays and badges without animating; a live push is false."""
    event_id: str = Field(pattern=r"^human:[0-9a-f-]{36}$")
    message_id: UUID
    thread_id: UUID
    direction: Direction
    owner_github_id: int                          # whose JARVIS this is
    source_node_id: UUID
    sender_github_id: int
    recipient_github_id: int
    occurred_at: datetime
    replay: bool


class HumanTransferFrame(_Local):
    type: Literal["human_transfer"] = TRANSFER_FRAME
    event: TransferEvent


# -- the services' dependencies ----------------------------------------------------

Clock = Callable[[], datetime]


@runtime_checkable
class IdSource(Protocol):
    def new_id(self) -> UUID: ...


@runtime_checkable
class RandomSource(Protocol):
    def random(self) -> float: ...


class TransportError(Exception):
    """A request that did not succeed. `code` is the answer's code, or
    "transport" when no answer came (the request may still have been
    accepted: treat it as `answer_lost`, never as nothing happened).
    `status` is the HTTP status when there was an answer."""

    def __init__(self, code: str, status: Optional[int] = None, latest: Optional[dict] = None,
                 retry_after: Optional[float] = None):
        super().__init__(code)
        self.code, self.status, self.latest, self.retry_after = code, status, latest, retry_after

    @property
    def retryable(self) -> bool:
        if self.code == "transport":
            return True
        status = self.status or getattr(wire.ERRORS.get(self.code), "status", 400)
        return wire.error_spec(self.code, status).retryable

    @property
    def session_ended(self) -> bool:
        """Stop submitting and keep every unsent draft and approved send."""
        status = self.status or getattr(wire.ERRORS.get(self.code), "status", 0)
        return wire.session_ended(status)


@runtime_checkable
class ServeTransport(Protocol):
    """The paired team server, through `team_client`'s session; the page
    never holds its credential. A connection generation change discards
    answers to requests made under the previous one. JSON calls use the
    existing call; the attachment byte streams use one streamed request on
    the team connection (owned by the files stream)."""

    async def capabilities(self) -> list[str]: ...

    async def recipients(self) -> wire.RecipientList: ...

    async def send(self, request: wire.SendRequest) -> wire.SendResult: ...

    async def get(self, message_id: UUID) -> wire.AcceptedMessage: ...

    async def sync(self, node_id: UUID, continuation: Optional[str],
                   limit: int) -> wire.SyncPage: ...

    async def receipts(self, batch: wire.ReceiptBatch) -> wire.ReceiptBatchResult: ...

    async def change_work_state(self, thread_id: UUID,
                                request: wire.WorkCommandRequest) -> wire.WorkState: ...

    async def announce(self, message_id: UUID,
                       request: wire.AnnouncementRequest) -> wire.AnnouncementLease: ...

    async def create_attachment(self, request: wire.AttachmentCreate) -> wire.AttachmentStatus: ...

    async def upload_attachment(self, attachment_id: UUID,
                                chunks: AsyncIterator[bytes]) -> wire.AttachmentStatus: ...

    async def finalize_attachment(self, attachment_id: UUID) -> wire.AttachmentStatus: ...

    async def attachment_status(self, attachment_id: UUID) -> wire.AttachmentStatus: ...

    def download_attachment(self, message_id: UUID,
                            attachment_id: UUID) -> AsyncIterator[bytes]: ...


class OwedReceipt(_Local):
    """A receipt this node has stored the fact for and not yet had settled."""
    message_id: UUID
    kind: wire.ReceiptKind


AnnouncementLocal = Literal["due", "claimed", "attempted", "completed", "elsewhere"]


class ApprovedSend(_Local):
    """The approval record and the envelope it releases, one row in one
    home (the store's approved table): exactly the approved revision, who
    approved it, on which of their JARVIS, how, and when."""
    server_id: str
    owner_github_id: int
    request: wire.SendRequest
    via: Literal["voice", "gui"]
    approved_at: datetime


@runtime_checkable
class LocalMessagingStore(Protocol):
    """SQLite under `data_paths.db_path()`, every row namespaced by server
    and owner. Each write that the rules pair is one transaction, and a hint
    goes out only after the commit. Every mutable record keeps the higher
    revision it has seen (receipts per message, work state per thread);
    nothing absent from a scan is pruned. A stale draft save raises
    `wire.ContractError("revision_conflict", latest=<the draft>)`."""

    # drafts
    def create_draft(self, server_id: str, owner_github_id: int, draft: Draft) -> Draft: ...

    def save_draft(self, server_id: str, owner_github_id: int, draft: Draft,
                   expected_draft_revision: int) -> Draft: ...

    def draft(self, server_id: str, owner_github_id: int, message_id: UUID) -> Optional[Draft]: ...

    def discard_draft(self, server_id: str, owner_github_id: int, message_id: UUID,
                      expected_draft_revision: int) -> None: ...

    # approval and sending
    def save_approved(self, approved: ApprovedSend) -> LocalSend:
        """The approval record, its envelope and the send record `approved`,
        in one transaction. The only way an envelope enters the outbox."""

    def approved(self, server_id: str, owner_github_id: int, message_id: UUID,
                 content_revision: int) -> Optional[ApprovedSend]: ...

    def set_send(self, server_id: str, owner_github_id: int, send: LocalSend) -> None: ...

    def unresolved_sends(self, server_id: str, owner_github_id: int) -> list[LocalSend]: ...

    def record_accepted(self, server_id: str, owner_github_id: int,
                        result: wire.SendResult) -> bool:
        """The accepted message, its receipts, the owner's work state and the
        send record `accepted`, in one transaction. For a message to oneself
        it is also stored as incoming (the received receipt owed, the
        announcement due); True then, for the caller to present it."""

    # incoming and sync
    def store_page(self, server_id: str, owner_github_id: int, node_id: UUID,
                   page: wire.SyncPage) -> list[UUID]:
        """Every entry and the page's continuation (None once the scan is
        complete) in one transaction. Returns the messages first held here
        as the owner's incoming, for each of which the received receipt is
        now owed and the announcement due."""

    def sync_position(self, server_id: str, owner_github_id: int,
                      node_id: UUID) -> Optional[str]: ...

    def owe_receipt(self, server_id: str, owner_github_id: int, message_id: UUID,
                    kind: wire.ReceiptKind) -> None: ...

    def receipts_owed(self, server_id: str, owner_github_id: int) -> list[OwedReceipt]: ...

    def settle_receipts(self, server_id: str, owner_github_id: int, sent: list[OwedReceipt],
                        result: wire.ReceiptBatchResult) -> None:
        """Store the facts (higher revision wins) and stop owing what was
        sent, including `not_found`."""

    # work state
    def save_work_state(self, server_id: str, owner_github_id: int,
                        state: wire.WorkState) -> wire.WorkState:
        """Keep the higher revision; returns what is now stored."""

    def queue_command(self, server_id: str, owner_github_id: int, thread_id: UUID,
                      request: wire.WorkCommandRequest) -> None: ...

    def pending_commands(self, server_id: str, owner_github_id: int
                         ) -> list[tuple[UUID, wire.WorkCommandRequest]]: ...

    def settle_command(self, server_id: str, owner_github_id: int, thread_id: UUID) -> None: ...

    # announcements: eligibility is durable here; the lease is the team server's
    def announcements_due(self, server_id: str, owner_github_id: int) -> list[UUID]: ...

    def set_announcement(self, server_id: str, owner_github_id: int, message_id: UUID,
                         state: AnnouncementLocal) -> None: ...

    # reads
    def message(self, server_id: str, owner_github_id: int,
                message_id: UUID) -> Optional[LocalMessage]: ...

    def item(self, server_id: str, owner_github_id: int, thread_id: UUID) -> Optional[LocalItem]: ...

    def snapshot(self, server_id: str, owner_github_id: int, *, availability: Availability,
                 stale: bool) -> QueueSnapshot: ...

    def history(self, server_id: str, owner_github_id: int, continuation: Optional[str],
                limit: int) -> HistoryPage: ...


class NotApproved(Exception):
    """The one guard refused: the stored revision is not approved, or changed."""


class SnapshotMissing(Exception):
    """A file's private copy is gone or no longer matches: an actionable
    error (`snapshot_missing`), never silently replaced by the original."""

    def __init__(self, attachment_id: UUID):
        super().__init__(str(attachment_id))
        self.attachment_id = attachment_id


@runtime_checkable
class ApprovalGuard(Protocol):
    """The one backend gate every outgoing human message passes, whatever
    path started it (voice, the page, an agent's draft). Agents may draft
    and request review; only the owner's acceptance approves. Every call
    names the server and owner, so a re-paired JARVIS never releases
    another owner's work. A test guard over a plain table (`TableGuard`)
    never stands in for this one in integration tests."""

    def request_review(self, server_id: str, owner_github_id: int, message_id: UUID,
                       expected_draft_revision: int) -> Draft:
        """Freeze the draft's content revision for review: its digest set,
        `awaiting_approval`. Agent tools may call this; it approves nothing."""

    def approve(self, server_id: str, owner_github_id: int, message_id: UUID,
                content_revision: int, content_digest: str, source_node_id: UUID,
                via: Literal["voice", "gui"]) -> ApprovedSend:
        """The owner's yes, from `Destination.deliver` only: compare-and-set
        on the stored revision and digest (stale: NotApproved), the
        idempotency key reserved, `LocalMessagingStore.save_approved`. A
        repeat for the same revision returns the same record."""

    def require_approved_revision(self, server_id: str, owner_github_id: int,
                                  message_id: UUID, content_revision: int) -> ApprovedSend:
        """What every submit and retry sends: the stored approved envelope,
        whose files still match their private copies (else SnapshotMissing)."""


@runtime_checkable
class ReviewCaptures(Protocol):
    """server.py's capture box, as the review route uses it: open the
    capture a review is accepted on, and close it when the revision it was
    for is superseded, cancelled or sent."""

    def open_for_review(self, draft: Draft, readback: str, recipient_label: str) -> int: ...

    def close(self, capture_id: int) -> None: ...


@runtime_checkable
class AttachmentSnapshots(Protocol):
    """Private, immutable copies of the files the owner chose, hashed while
    copied, under `data_paths.messaging_snapshots_dir()`; finished downloads
    under `data_paths.messaging_downloads_dir()`. Sending reads the copies,
    never the original path. A verified download is never opened by JARVIS:
    the owner's one action on it is to reveal it in Finder."""

    async def snapshot(self, server_id: str, owner_github_id: int, message_id: UUID,
                       filename: str, media_type: Optional[str],
                       chunks: AsyncIterator[bytes]) -> wire.AttachmentRef:
        """Copy, hash and name (`normalize_filename`, `normalize_media_type`)."""

    def verified_attachment_manifest(self, server_id: str, owner_github_id: int,
                                     message_id: UUID,
                                     content_revision: int) -> list[wire.AttachmentRef]:
        """The draft's files, re-hashed from their copies (SnapshotMissing)."""

    async def ensure_uploaded(self, server_id: str, owner_github_id: int, message_id: UUID,
                              content_revision: int) -> list[wire.AttachmentRef]:
        """Called by delivery before each submit of an approved revision:
        every file staged, uploaded and verified on the team server
        (SnapshotMissing, or TransportError)."""

    def refs(self, server_id: str, owner_github_id: int, message_id: UUID,
             attachment_ids: list[UUID]) -> list[wire.AttachmentRef]:
        """A draft save's `attachment_ids` as manifest entries, in order
        (SnapshotMissing for a file with no private copy for this draft)."""

    def cleanup(self, server_id: str, owner_github_id: int) -> int:
        """Remove abandoned partial files and snapshots nothing still names;
        how many were removed."""

    def files(self, server_id: str, owner_github_id: int, message_id: UUID) -> list[LocalFile]: ...

    async def download(self, server_id: str, owner_github_id: int, message_id: UUID,
                       attachment_id: UUID) -> LocalFile: ...

    def reveal(self, server_id: str, owner_github_id: int, message_id: UUID,
               attachment_id: UUID) -> None: ...


@runtime_checkable
class PresentationDispatcher(Protocol):
    """Draws and announces transfers. Before enqueuing speech a node claims
    the team server's lease for the message (`ServeTransport.announce`);
    a browser's de-duplication is not a lease."""

    def present(self, event: TransferEvent) -> None: ...

    async def reserve_announcement(self, event: TransferEvent) -> Optional[UUID]:
        """The lease token, or None when another node holds it or it is done."""

    async def attempted(self, event: TransferEvent, token: UUID) -> None: ...

    async def complete_announcement(self, event: TransferEvent, token: UUID) -> None: ...
