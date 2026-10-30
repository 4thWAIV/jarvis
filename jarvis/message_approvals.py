"""The owner's approval of a message to a person, and the one gate every
outgoing human message passes.

A message to a person goes the way a message to an agent goes
(`transmission.py`): it is accepted (Accept or Send on its capture box, or
its read-back heard in full), staged, held for the turn, offered the cancel
window and handed over. What differs is only who it is for, so this module
is the human adapters and the guard behind them:

    MessageApprovals   the production `ApprovalGuard`. Review freezes a
                       draft's content revision (its digest over exactly
                       what would be sent, files re-hashed from their
                       private copies); approval is a compare-and-set on
                       that revision and digest which reserves the
                       idempotency key and writes the approved envelope
                       (`LocalMessagingStore.save_approved`); release hands
                       back that stored envelope only while its send is
                       unresolved and its files still match.
    GuardedTransport   the team server transport the sender is given: its
                       `send` refuses any request that is not the stored
                       approved envelope of a send already on record as
                       `submitting`. Every outgoing human message, reply,
                       forward and message to oneself goes through it,
                       whichever code path built the request.
    Reviews            a review opens the capture it is accepted on
                       (`ReviewCaptures`), whose text is the read-back.
    HumanRecipient,    `transmission.Recipient` and `Destination` for a
    HumanDestination   capture bound to a reviewed revision
                       (capture["human"]). Their hand-over is the approval:
                       the human "sent" is the approved envelope durably in
                       the outbox, never acceptance or delivery.
    MessagingTools     the brain's tools: draft a message and ask for its
                       review. Neither approves anything; only the owner's
                       acceptance of the review's capture does.

The trusted JARVIS enforces this policy for its owner. It does not prove a
human click to the team server, which trusts the paired JARVIS, and it is no
defence against unrestricted software on the same machine. Logs carry ids,
revisions and codes only: never a message's text, subject, file names or a
credential.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import AsyncIterator, Callable, Literal, Optional
from uuid import UUID

from pydantic import ValidationError

from jarvis import agent_directory
from jarvis import messaging_ports as ports
from jarvis import messaging_wire as wire
from jarvis import transmission
from jarvis.speech import Priority
from jarvis.transfer_presentation import transfer_event

log = logging.getLogger(__name__)

Via = Literal["voice", "gui"]

# The human destination's successful hand-over: approved, and in the outbox.
APPROVED = "approved"
# Its refusals, as the send's failure codes (`LOCAL_ERRORS`).
UNAVAILABLE = "messaging_unavailable"
NOT_APPROVED = "not_approved"
SNAPSHOT_MISSING = "snapshot_missing"

ME = "Me"


@dataclass(frozen=True)
class Identity:
    """Whose JARVIS this is on which team server, and which node of theirs."""
    server_id: str
    owner_github_id: int
    node_id: UUID

    @property
    def ns(self) -> tuple[str, int]:
        return self.server_id, self.owner_github_id


def _same_files(found: list[wire.AttachmentRef], approved: list[wire.AttachmentRef]) -> None:
    """The private copies are exactly the files approved, in order, or the
    first that differs is SnapshotMissing: never silently replaced."""
    if found == approved:
        return
    for i, ref in enumerate(approved):
        if i >= len(found) or found[i] != ref:
            raise ports.SnapshotMissing(ref.attachment_id)
    raise ports.SnapshotMissing(found[len(approved)].attachment_id)


class MessageApprovals:
    """The production `ApprovalGuard` over the messaging store and the file
    snapshots. It has no table of its own: the approval record and the
    envelope it releases live in the store's approved table, written in one
    transaction with the send record (`save_approved`)."""

    def __init__(self, store: ports.LocalMessagingStore, snapshots: ports.AttachmentSnapshots,
                 *, ids: ports.IdSource, clock: ports.Clock):
        self.store, self.snapshots, self.ids, self.clock = store, snapshots, ids, clock

    # -- what a draft would send ------------------------------------------------

    def _draft(self, server_id: str, owner_github_id: int, message_id: UUID) -> ports.Draft:
        draft = self.store.draft(server_id, owner_github_id, message_id)
        if draft is None:
            raise wire.ContractError("draft_not_found")
        return draft

    def _content(self, server_id: str, owner_github_id: int,
                 draft: ports.Draft) -> tuple[wire.MessageContent, str]:
        """The content and digest the draft would send now, its files
        re-hashed from their private copies (SnapshotMissing)."""
        _same_files(self.snapshots.verified_attachment_manifest(
            server_id, owner_github_id, draft.message_id, draft.send.content_revision),
            list(draft.attachments))
        try:
            content = ports.draft_content(draft)
        except ValidationError:
            raise wire.ContractError("invalid_request") from None
        return content, wire.content_digest(content, server_id=server_id,
                                            sender_github_id=owner_github_id)

    def awaiting(self, server_id: str, owner_github_id: int, message_id: UUID,
                 content_revision: int, content_digest: str) -> Optional[ports.Draft]:
        """The draft, if it is still awaiting approval at exactly this
        revision and digest; else None."""
        draft = self.store.draft(server_id, owner_github_id, message_id)
        if (draft is None or draft.send.state != "awaiting_approval"
                or draft.send.content_revision != content_revision
                or draft.send.content_digest != content_digest):
            return None
        return draft

    # -- ApprovalGuard ----------------------------------------------------------

    def request_review(self, server_id: str, owner_github_id: int, message_id: UUID,
                       expected_draft_revision: int) -> ports.Draft:
        draft = self._draft(server_id, owner_github_id, message_id)
        if draft.draft_revision != expected_draft_revision:
            raise wire.ContractError("revision_conflict", latest=draft)
        state = ports.next_send_state(draft.send.state, "review")
        _content, digest = self._content(server_id, owner_github_id, draft)
        if (draft.send.state, draft.send.content_digest) == (state, digest):
            return draft                                  # a second Review: nothing changes
        self.store.set_send(server_id, owner_github_id, draft.send.model_copy(
            update={"state": state, "content_digest": digest}))
        log.info("message review: message=%s revision=%s", message_id,
                 draft.send.content_revision)
        return self._draft(server_id, owner_github_id, message_id)

    def approve(self, server_id: str, owner_github_id: int, message_id: UUID,
                content_revision: int, content_digest: str, source_node_id: UUID,
                via: Via) -> ports.ApprovedSend:
        done = self.store.approved(server_id, owner_github_id, message_id, content_revision)
        if done is not None:
            if (done.request.content_digest != content_digest
                    or self._send(server_id, owner_github_id, message_id,
                                  content_revision) is None):
                raise ports.NotApproved(str(message_id))
            return done                                   # a repeated yes changes nothing
        draft = self.awaiting(server_id, owner_github_id, message_id, content_revision,
                              content_digest)
        if draft is None:
            raise ports.NotApproved(str(message_id))
        content, digest = self._content(server_id, owner_github_id, draft)
        if digest != content_digest:
            # Saved again since the review without a new revision: what he
            # accepted is not what would go.
            raise ports.NotApproved(str(message_id))
        approved = ports.ApprovedSend(
            server_id=server_id, owner_github_id=owner_github_id, via=via,
            approved_at=self.clock(),
            request=wire.SendRequest(**content.model_dump(), source_node_id=source_node_id,
                                     idempotency_key=self.ids.new_id(),
                                     content_digest=digest))
        self.store.save_approved(approved)
        log.info("message approved: message=%s revision=%s via=%s", message_id,
                 content_revision, via)
        return approved

    def _send(self, server_id: str, owner_github_id: int, message_id: UUID,
              content_revision: int) -> Optional[ports.LocalSend]:
        """The send record of this revision while it is unresolved
        (approved, submitting, answer lost or failed), else None."""
        return next((s for s in self.store.unresolved_sends(server_id, owner_github_id)
                     if s.message_id == message_id and s.content_revision == content_revision),
                    None)

    def _released(self, server_id: str, owner_github_id: int, message_id: UUID,
                  content_revision: int) -> tuple[ports.ApprovedSend, ports.LocalSend]:
        found = self.store.approved(server_id, owner_github_id, message_id, content_revision)
        send = self._send(server_id, owner_github_id, message_id, content_revision)
        if found is None or send is None:
            raise ports.NotApproved(str(message_id))
        request = found.request
        if wire.content_digest(wire.content_of(request), server_id=server_id,
                               sender_github_id=owner_github_id) != request.content_digest:
            raise ports.NotApproved(str(message_id))
        _same_files(self.snapshots.verified_attachment_manifest(
            server_id, owner_github_id, message_id, content_revision), list(request.attachments))
        return found, send

    def require_approved_revision(self, server_id: str, owner_github_id: int,
                                  message_id: UUID, content_revision: int) -> ports.ApprovedSend:
        return self._released(server_id, owner_github_id, message_id, content_revision)[0]

    def release(self, server_id: str, owner_github_id: int,
                request: wire.SendRequest) -> None:
        """Whether `request` may leave now: exactly the stored approved
        envelope of its revision, whose attempt is already on record
        (`submitting`, persisted before it leaves). NotApproved otherwise."""
        approved, send = self._released(server_id, owner_github_id, request.message_id,
                                        request.content_revision)
        if approved.request != request or send.state != "submitting":
            log.warning("message send refused: message=%s revision=%s state=%s",
                        request.message_id, request.content_revision, send.state)
            raise ports.NotApproved(str(request.message_id))

class GuardedTransport:
    """A `ServeTransport` whose `send` passes the guard first. Delivery is
    given this one, never the bare transport, so nothing reaches the team
    server's message route that the owner did not approve: not a draft, not
    a revision awaiting approval, not a changed or cancelled one, and not an
    attempt that is not on record. Everything else is the transport's own."""

    def __init__(self, transport: ports.ServeTransport, approvals: MessageApprovals,
                 server_id: str, owner_github_id: int):
        self._transport, self._approvals = transport, approvals
        self._ns = (server_id, owner_github_id)

    async def send(self, request: wire.SendRequest) -> wire.SendResult:
        self._approvals.release(*self._ns, request)
        return await self._transport.send(request)

    # The rest is the transport's own, unchanged.

    async def capabilities(self) -> list[str]:
        return await self._transport.capabilities()

    async def recipients(self) -> wire.RecipientList:
        return await self._transport.recipients()

    async def get(self, message_id: UUID) -> wire.AcceptedMessage:
        return await self._transport.get(message_id)

    async def sync(self, node_id: UUID, continuation: Optional[str],
                   limit: int) -> wire.SyncPage:
        return await self._transport.sync(node_id, continuation, limit)

    async def receipts(self, batch: wire.ReceiptBatch) -> wire.ReceiptBatchResult:
        return await self._transport.receipts(batch)

    async def change_work_state(self, thread_id: UUID,
                                request: wire.WorkCommandRequest) -> wire.WorkState:
        return await self._transport.change_work_state(thread_id, request)

    async def announce(self, message_id: UUID,
                       request: wire.AnnouncementRequest) -> wire.AnnouncementLease:
        return await self._transport.announce(message_id, request)

    async def create_attachment(self, request: wire.AttachmentCreate) -> wire.AttachmentStatus:
        return await self._transport.create_attachment(request)

    async def upload_attachment(self, attachment_id: UUID,
                                chunks: AsyncIterator[bytes]) -> wire.AttachmentStatus:
        return await self._transport.upload_attachment(attachment_id, chunks)

    async def finalize_attachment(self, attachment_id: UUID) -> wire.AttachmentStatus:
        return await self._transport.finalize_attachment(attachment_id)

    async def attachment_status(self, attachment_id: UUID) -> wire.AttachmentStatus:
        return await self._transport.attachment_status(attachment_id)

    def download_attachment(self, message_id: UUID,
                            attachment_id: UUID) -> AsyncIterator[bytes]:
        return self._transport.download_attachment(message_id, attachment_id)


# -- the review's capture -----------------------------------------------------------

def capture_binding(draft: ports.Draft) -> dict:
    """capture["human"]: the reviewed revision the capture's acceptance is for."""
    return {"github_id": draft.recipient_github_id, "message_id": str(draft.message_id),
            "content_revision": draft.send.content_revision,
            "content_digest": draft.send.content_digest}


def recipient_label(members: list[wire.TeamMember], github_id: int, owner_github_id: int) -> str:
    """How a recipient is named in the read-back: Me for the owner, else the
    roster's name or login."""
    if github_id == owner_github_id:
        return ME
    member = next((m for m in members if m.github_id == github_id), None)
    return (member.name or member.login) if member else f"GitHub user {github_id}"


class Reviews:
    """Review from the page's route or an agent's tool, one path for both:
    the guard freezes the revision, the capture box opens (or keeps) the
    capture it is accepted on, and the draft records that capture. Opening
    it approves nothing; a repeat opens nothing new."""

    def __init__(self, approvals: MessageApprovals, captures: ports.ReviewCaptures,
                 recipients: Callable[[], list[wire.TeamMember]]):
        self.approvals, self.captures, self.recipients = approvals, captures, recipients

    def review(self, server_id: str, owner_github_id: int, message_id: UUID,
               expected_draft_revision: int) -> ports.Draft:
        draft = self.approvals.request_review(server_id, owner_github_id, message_id,
                                              expected_draft_revision)
        label = recipient_label(self.recipients(), draft.recipient_github_id, owner_github_id)
        capture_id = self.captures.open_for_review(draft, ports.human_readback(draft, label),
                                                   label)
        return self.approvals.store.set_capture(server_id, owner_github_id, message_id,
                                                capture_id)


# -- the capture's recipient and destination ---------------------------------------

@dataclass
class HumanMessaging:
    """What the capture path needs, wired once at startup (server.py's
    `messaging.human`). `released` wakes the sender when an approval lands;
    `presentation` draws the transfer (signals)."""
    approvals: MessageApprovals
    identity: Callable[[], Optional[Identity]]
    availability: Callable[[], ports.Availability]
    recipients: Callable[[], list[wire.TeamMember]]
    released: Callable[[ports.ApprovedSend], None] = field(default=lambda approved: None)
    presentation: Optional[ports.PresentationDispatcher] = None


def _changed(label: str) -> str:
    return (f"That message to {label} has changed since you reviewed it, sir, so I "
            f"haven't sent it. Review it again.")


class HumanRecipient:
    """A person a reviewed capture is for, as `transmission.stage` asks it.
    The durable record is the draft, already awaiting approval, so staging
    writes nothing more; a refusal leaves it awaiting approval."""

    def __init__(self, capture: dict, via: Via, messaging: Optional[HumanMessaging]):
        self.capture_id, self.text = capture["id"], capture["text"]
        self.label = capture["to"] or "them"
        self.bound = dict(capture["human"])
        self.message_id = UUID(self.bound["message_id"])
        self.via, self.messaging = via, messaging
        self.who: Optional[Identity] = None

    def refusal(self) -> Optional[tuple[str, str]]:
        m = self.messaging
        self.who = m.identity() if m is not None else None
        if m is None or self.who is None or m.availability() != "available":
            return UNAVAILABLE, (f"Messaging isn't available on this JARVIS just now, sir, "
                                 f"so nothing has gone to {self.label}.")
        draft = m.approvals.awaiting(*self.who.ns, self.message_id,
                                     self.bound["content_revision"],
                                     self.bound["content_digest"])
        if (draft is None or draft.recipient_github_id != self.bound["github_id"]
                or ports.human_readback(draft, self.label) != self.text):
            return NOT_APPROVED, _changed(self.label)
        return None

    def unheard(self) -> str:
        return (f"I can't read that back to you right now, sir, so I won't send it to "
                f"{self.label} unheard.")

    def refuse(self, failure: str) -> None:
        log.info("message not staged: message=%s capture=%s failure=%s", self.message_id,
                 self.capture_id, failure)

    def open(self, capture: dict) -> tuple[str, "HumanDestination"]:
        return str(self.message_id), HumanDestination(self, self.who)


class HumanDestination:
    """One staged human message, as `transmission.perform` sends it. It has
    no one-message-per-turn queue; its hand-over records the approval, and
    a cancel in the window leaves it awaiting approval with nothing in the
    outbox."""

    priority = False

    def __init__(self, recipient: HumanRecipient, who: Identity):
        self.capture_id = recipient.capture_id
        self.label, self.via, self.messaging = recipient.label, recipient.via, recipient.messaging
        self.bound, self.message_id, self.who = recipient.bound, recipient.message_id, who
        self.approved: Optional[ports.ApprovedSend] = None
        self.status: Optional[str] = None

    def record(self) -> str:
        return str(self.message_id)

    def settle(self, status: str, **fields) -> None:
        self.status = status
        log.info("message %s: message=%s revision=%s failure=%s", status, self.message_id,
                 self.bound["content_revision"], fields.get("failure"))

    def why_wait(self) -> Optional[str]:
        return None

    async def deliver(self) -> str:
        try:
            self.approved = self.messaging.approvals.approve(
                *self.who.ns, self.message_id, self.bound["content_revision"],
                self.bound["content_digest"], self.who.node_id, self.via)
        except ports.SnapshotMissing:
            return SNAPSHOT_MISSING
        except (ports.NotApproved, ports.SendRefused, wire.ContractError):
            return NOT_APPROVED
        try:
            self.messaging.released(self.approved)
        except Exception:
            # Approved and in the outbox: the sender finds it on its next pass.
            log.warning("could not wake the sender for message %s", self.message_id,
                        exc_info=True)
        return APPROVED

    def delivered(self, outcome: str) -> bool:
        return outcome == APPROVED

    async def went(self) -> None:
        presentation, a = self.messaging.presentation, self.approved
        if presentation is None or a is None:
            return
        try:
            presentation.present(transfer_event(a.owner_github_id, a.request, "outgoing",
                                                a.approved_at))
        except Exception:
            log.warning("could not draw message %s going", self.message_id, exc_info=True)

    async def say_queued(self, speech, why: str) -> None:
        await speech.say(f"Your message to {self.label} waits, sir: {why}.",
                         Priority.NORMAL, label="readback-outcome")

    async def say_outcome(self, speech, outcome: str) -> None:
        # Approved is all it is: the team server has not taken it yet, let
        # alone their JARVIS, and nothing here claims either.
        if outcome == APPROVED:
            line = f"Approved, sir. It goes to {self.label} as soon as the team server takes it."
        elif outcome == SNAPSHOT_MISSING:
            line = (f"A file's private copy is missing, sir, so nothing has gone to "
                    f"{self.label}. Attach it again.")
        else:
            line = _changed(self.label)
        await speech.say(line, Priority.NORMAL, label="readback-outcome")


def staged_note(record: Optional[str], problem: Optional[str], capture_id: int,
                label: str) -> str:
    """What the brain is told after it staged a capture for a person."""
    if problem:
        return problem
    return (f"Capture {capture_id} is staged for {label}: when this turn ends "
            f"it is approved and goes out, after a short moment in which he can stop it. "
            f"He has heard it already, so do not repeat it: say briefly that it is going "
            f"and end your turn. Approved is not delivered; do not call this tool again "
            f"for it.")


# -- the brain's tools -----------------------------------------------------------------

# Drafts are saved through the same function as the page's draft routes
# (messaging_api): (identity, the draft to edit or None for a new one, the
# save, written by an agent) -> the saved draft. A conflict raises
# wire.ContractError("revision_conflict", latest=<the draft>).
DraftWriter = Callable[[Identity, Optional[UUID], ports.DraftSave, bool], ports.Draft]

def _uuid(value) -> Optional[UUID]:
    try:
        return UUID(str(value))
    except (TypeError, ValueError):
        return None


class MessagingTools:
    """The brain's draft and review tools. The brain may draft and ask for
    a review; approving is never a tool."""

    def __init__(self, messaging: HumanMessaging, reviews: Reviews, save_draft: DraftWriter):
        self.messaging, self.reviews, self.save_draft = messaging, reviews, save_draft

    def _who(self) -> Optional[Identity]:
        m = self.messaging
        return m.identity() if m.availability() == "available" else None

    def _recipient(self, said: str, owner_github_id: int) -> tuple[Optional[int], str]:
        key = agent_directory.spoken_key(said)
        if not key:
            return None, "Say who it is for (or reply_to a message); nothing was drafted."
        if key in ("me", "myself", "self"):
            return owner_github_id, ""
        members = self.messaging.recipients()
        found = [m for m in members
                 if key in {agent_directory.spoken_key(m.login),
                            agent_directory.spoken_key(m.name or "")} - {""}]
        if len(found) == 1:
            return found[0].github_id, ""
        names = ", ".join(recipient_label(members, m.github_id, owner_github_id)
                          for m in (found or members))
        if found:
            return None, f"That could be {names}. Ask him which; nothing was drafted."
        return None, (f"There is no one called {said} on the team. The team is: "
                      f"{names or 'nobody yet'}. Nothing was drafted.")

    def draft_message(self, args: dict) -> str:
        who = self._who()
        if who is None:
            return "Messaging isn't available on this JARVIS; nothing was drafted."
        editing = _uuid(args.get("message")) if args.get("message") is not None else None
        if args.get("message") is not None and editing is None:
            return "That is not a draft's message id; nothing was drafted."
        save = {"subject": wire.normalize_subject(str(args.get("subject") or "")),
                "text": wire.normalize_text(str(args.get("text") or ""))}
        if editing is not None:
            current = self.messaging.approvals.store.draft(*who.ns, editing)
            if current is None:
                return "There is no draft by that id; nothing was saved."
            revision = args.get("draft_revision")
            save.update(expected_draft_revision=revision if isinstance(revision, int)
                        else current.draft_revision,
                        thread_id=current.thread_id,
                        in_reply_to_message_id=current.in_reply_to_message_id,
                        related_message_ids=list(current.related_message_ids),
                        recipient_github_id=current.recipient_github_id,
                        attachment_ids=[a.attachment_id for a in current.attachments])
        elif args.get("reply_to") is not None:
            parent = self.messaging.approvals.store.message(*who.ns, _uuid(args["reply_to"])) \
                if _uuid(args["reply_to"]) else None
            if parent is None:
                return "There is no message by that id to reply to; nothing was drafted."
            p = parent.message
            save.update(thread_id=p.thread_id, in_reply_to_message_id=p.message_id,
                        recipient_github_id=(p.recipient_github_id
                                             if p.sender_github_id == who.owner_github_id
                                             else p.sender_github_id))
        else:
            github_id, problem = self._recipient(str(args.get("to") or ""), who.owner_github_id)
            if github_id is None:
                return problem
            save.update(recipient_github_id=github_id)
        try:
            draft = self.save_draft(who, editing, ports.DraftSave(**save), True)
        except ValidationError:
            return "That draft is too long or malformed; nothing was saved."
        except wire.ContractError as e:
            if e.code == "revision_conflict":
                return ("Someone edited that draft since you last saw it; nothing was "
                        "saved. Read it again before changing it.")
            return f"The draft was refused ({e.code}); nothing was saved."
        except ports.SendRefused:
            return "That message is already on its way out; it cannot be edited now."
        to = recipient_label(self.messaging.recipients(), draft.recipient_github_id,
                             who.owner_github_id)
        return (f"Draft {draft.message_id} to {to} is saved at revision "
                f"{draft.draft_revision}. Nothing is sent. Ask him whether to review it; "
                f"request_message_review opens it for him.")

    def request_message_review(self, args: dict) -> str:
        who = self._who()
        if who is None:
            return "Messaging isn't available on this JARVIS; nothing was opened."
        message_id, revision = _uuid(args.get("message")), args.get("draft_revision")
        if message_id is None or not isinstance(revision, int):
            return "Give the draft's message id and the revision you last saw."
        try:
            draft = self.reviews.review(*who.ns, message_id, revision)
        except wire.ContractError as e:
            if e.code == "revision_conflict":
                return ("That draft changed since you last saw it (it is at revision "
                        f"{getattr(e.latest, 'draft_revision', '?')}); nothing was opened.")
            if e.code == "draft_not_found":
                return "There is no draft by that id."
            return f"It cannot be reviewed as it stands ({e.code}); nothing was opened."
        except ports.SnapshotMissing:
            return "A file's private copy is missing; he must attach it again."
        except ports.SendRefused:
            return "That message is past review; list its item to see where it stands."
        return (f"Capture {draft.capture_id} holds the message as it would go. Nothing is "
                f"approved. Ask him whether he wants it read back; read_capture reads it. "
                f"On his yes after hearing it, call send_message with capture "
                f"{draft.capture_id}.")
