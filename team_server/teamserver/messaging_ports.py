"""The team server's side of human messaging as interfaces: what the
messaging routes (`messaging.py`, delivery) and the attachment routes
(`attachments.py`, files) are built against, and what tests replace.

Every service takes its dependencies: the repository, the attachment
storage, the clock (`clock.now` by default), the id source and the fault
adapter (`messaging_models.NO_FAULTS` in production). Production and test
adapters implement the same Protocols; there is no test-only branch in a
service and no route that resets, approves or injects anything.

Authorization is the repository caller's job and is never optional: every
read and write names the authenticated viewer, and a record the viewer may
not see is `message_not_found`, even for a team administrator.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import AsyncIterator, Callable, Optional, Protocol, runtime_checkable
from uuid import UUID

from .messaging_models import (FILE_MAX_BYTES, OWNER_STAGING_QUOTA_BYTES, PACKAGE_MAX_BYTES,
                               STAGING_EXPIRY_SEC, UPLOAD_IDLE_TIMEOUT_SEC,
                               UPLOAD_STREAMS_MAX, AcceptedMessage, AnnouncementLease,
                               AnnouncementRequest, AttachmentRef, ReceiptFacts, ReceiptKind,
                               StagingState, SyncPage, WorkCommand, WorkState)

# How long one node's announcement claim holds before another of the
# owner's nodes may claim it (decision 23; the signals stream's setting).
ANNOUNCEMENT_LEASE_SEC = 30.0

# The file settings' names and defaults. A deployment may set any of them
# lower than the contract's default, never higher. Wired into config.py at
# files integration.
ATTACHMENT_SETTINGS: dict[str, int] = {
    "TEAM_SERVER_FILE_MAX_BYTES": FILE_MAX_BYTES,
    "TEAM_SERVER_PACKAGE_MAX_BYTES": PACKAGE_MAX_BYTES,
    "TEAM_SERVER_STAGING_QUOTA_BYTES": OWNER_STAGING_QUOTA_BYTES,
    "TEAM_SERVER_STAGING_EXPIRY_SEC": STAGING_EXPIRY_SEC,
    "TEAM_SERVER_UPLOAD_STREAMS_MAX": UPLOAD_STREAMS_MAX,
    "TEAM_SERVER_UPLOAD_IDLE_TIMEOUT_SEC": UPLOAD_IDLE_TIMEOUT_SEC,
}

Clock = Callable[[], datetime]


@runtime_checkable
class IdSource(Protocol):
    def new_id(self) -> UUID: ...


@runtime_checkable
class MessageRepository(Protocol):
    """Durable messages, receipts, work state and announcement leases
    (MongoDB, majority writes).

    Acceptance, in this order (the fault points name the gaps): pin each
    manifest file (`AttachmentStorage.pin`) -> server_after_pin_before_insert
    -> `insert_accepted`, the visibility commit -> server_after_insert_before_
    projection -> `derive_projections` -> server_after_commit_before_response
    -> `mark_published` for each file (housekeeping, repairable).

    The source node is checked with `bind_node`, messaging's own permanent
    binding of a node id to the member who first used it, made from the
    network record on first use and never purged with presence."""

    async def bind_node(self, server_id: str, github_id: int, node_id: UUID) -> bool:
        """True when `node_id` is this member's: already bound to them, or
        unbound and published by them in the network record (then bound
        for good). False for anyone else's or an unknown node."""

    async def find_by_idempotency(self, server_id: str, sender_github_id: int,
                                  idempotency_key: UUID) -> Optional[AcceptedMessage]: ...

    async def get_visible(self, server_id: str, viewer_github_id: int,
                          message_id: UUID) -> Optional[AcceptedMessage]: ...

    async def insert_accepted(self, message: AcceptedMessage) -> tuple[AcceptedMessage, bool]:
        """Insert, or find the identical earlier one: (the stored message,
        duplicate). On a unique-index collision with a different digest or
        key, whoever's it is, raises ContractError("idempotency_conflict"),
        which says nothing about the other message."""

    async def derive_projections(self, message: AcceptedMessage) -> None:
        """The receipts record and both participants' work items for an
        accepted message, idempotently: each item records which message ids
        it has applied (not on the wire) and applies them in (accepted_at,
        message_id) order, so a repair after a crash never re-applies one.
        Self-send: one item."""

    async def receipts(self, server_id: str, viewer_github_id: int,
                       message_id: UUID) -> Optional[ReceiptFacts]: ...

    async def add_receipt(self, server_id: str, recipient_github_id: int, message_id: UUID,
                          kind: ReceiptKind, node_id: UUID, now: datetime) -> ReceiptFacts: ...

    async def work_state(self, server_id: str, owner_github_id: int,
                         thread_id: UUID) -> Optional[WorkState]: ...

    async def change_work_state(self, server_id: str, owner_github_id: int, thread_id: UUID,
                                change: WorkCommand, now: datetime) -> WorkState: ...

    async def sync_page(self, server_id: str, owner_github_id: int, node_id: UUID,
                        continuation: Optional[str], limit: int, now: datetime) -> SyncPage: ...

    async def announce(self, server_id: str, owner_github_id: int, message_id: UUID,
                       request: AnnouncementRequest, now: datetime, new_token: UUID,
                       lease_sec: float) -> AnnouncementLease:
        """`apply_announcement` by compare-and-set on the lease's revision;
        only the message's recipient may call it."""


@dataclass(frozen=True)
class StagingRecord:
    """One staged file, as the storage holds it. Never on the wire as such:
    its owner sees it as AttachmentStatus."""
    server_id: str
    owner_github_id: int
    attachment_id: UUID
    message_id: UUID
    size_bytes: int
    sha256: str
    state: StagingState
    received_bytes: int
    expires_at: Optional[datetime]
    verified_attempt: Optional[str] = None      # the attempt finalize chose
    pinned_digest: Optional[str] = None         # the message digest it is pinned under
    pinned_at: Optional[datetime] = None


@dataclass(frozen=True)
class Attempt:
    """One upload attempt's bytes, as written: its own id (the GridFS
    file), and the size and SHA-256 the server computed."""
    attempt_id: str
    size_bytes: int
    sha256: str


@runtime_checkable
class AttachmentStorage(Protocol):
    """Streamed file bytes (GridFS first) and their staging records. Every
    staging call is scoped by server and owner: someone else's record is
    None, exactly like no record. `attachment_id` stays the same across
    attempts; each attempt is its own GridFS file, and finalize chooses one
    verified attempt by compare-and-set, leaving the rest to the sweep.

    Cleanup and recovery run under one fenced lease (`claim_recovery`): a
    call carrying an older fence than the newest is refused."""

    async def create_staging(self, server_id: str, owner_github_id: int, attachment_id: UUID,
                             message_id: UUID, size_bytes: int, sha256: str,
                             now: datetime) -> StagingRecord: ...

    async def staging(self, server_id: str, owner_github_id: int,
                      attachment_id: UUID) -> Optional[StagingRecord]: ...

    async def latest_attempt(self, server_id: str, owner_github_id: int,
                             attachment_id: UUID) -> Optional[str]:
        """The attempt that last finished writing: the finalize route names
        no attempt, so the service asks which one to check."""

    async def write_attempt(self, server_id: str, owner_github_id: int, attachment_id: UUID,
                            chunks: AsyncIterator[bytes], max_bytes: int) -> Attempt:
        """Counts the bytes it reads whatever Content-Length said, and
        aborts (deleting the attempt) past `max_bytes` or on disconnect."""

    async def finalize(self, server_id: str, owner_github_id: int, attachment_id: UUID,
                       attempt_id: str, now: datetime) -> StagingRecord:
        """uploading -> verified when the attempt's size and hash are the
        record's (else ContractError("attachment_mismatch")); a verified
        record stays as it is."""

    async def pin(self, server_id: str, sender_github_id: int, ref: AttachmentRef,
                  message_id: UUID, digest: str, now: datetime) -> bool:
        """verified -> pinned for this message by compare-and-set on the
        server, owner, message, size and SHA-256. A record already pinned to
        the same message by the same sender may move to a new digest (that
        message was never accepted); any other pin is refused (False)."""

    async def mark_published(self, server_id: str, attachment_id: UUID) -> None: ...

    async def read(self, server_id: str, attachment_id: UUID) -> AsyncIterator[bytes]:
        """The verified bytes. Called only after the service found an
        accepted message, visible to the viewer, whose manifest names it."""

    async def claim_recovery(self, worker_id: str, now: datetime,
                             lease_sec: float) -> Optional[int]:
        """The fence for this pass, or None while another worker holds it."""

    async def stale_pins(self, fence: int, older_than: datetime,
                         limit: int) -> list[StagingRecord]: ...

    async def release_pin(self, fence: int, record: StagingRecord) -> bool:
        """pinned -> verified by compare-and-set on the whole record. The
        caller has proven no accepted message has its message_id."""

    async def claim_expired(self, fence: int, now: datetime, limit: int) -> list[StagingRecord]:
        """Unpinned records past `expires_at` -> expired, by compare-and-set."""

    async def delete(self, fence: int, record: StagingRecord) -> None:
        """An expired record's metadata, chunks and every attempt."""

    async def sweep_attempts(self, fence: int, older_than: datetime, limit: int) -> int:
        """Delete attempts no record chose that are older than `older_than`
        (abandoned or superseded uploads); how many."""
