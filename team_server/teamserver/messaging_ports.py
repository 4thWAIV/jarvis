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

from datetime import datetime
from typing import AsyncIterator, Callable, Literal, Optional, Protocol, runtime_checkable
from uuid import UUID

from .messaging_models import (AcceptedMessage, ReceiptFacts, ReceiptKind, SyncPage,
                               WorkCommand, WorkState)

# The team server's own switch. Unset or off: `messaging.v1` is not
# advertised and every messaging route answers `messaging_disabled`.
# Switching it off deletes nothing.
MESSAGING_FLAG = "TEAM_SERVER_MESSAGING"

Clock = Callable[[], datetime]


@runtime_checkable
class IdSource(Protocol):
    def new_id(self) -> UUID: ...


@runtime_checkable
class MessageRepository(Protocol):
    """Durable messages, receipts and work state (MongoDB, majority writes).
    `insert_accepted` is the visibility commit: once it returns, the message
    exists and the sender, recipient and both work items are derived from it
    idempotently (repairable after a crash)."""

    async def find_by_idempotency(self, server_id: str, sender_github_id: int,
                                  idempotency_key: UUID) -> Optional[AcceptedMessage]: ...

    async def get_visible(self, server_id: str, viewer_github_id: int,
                          message_id: UUID) -> Optional[AcceptedMessage]: ...

    async def insert_accepted(self, message: AcceptedMessage) -> AcceptedMessage: ...

    async def receipts(self, server_id: str, message_id: UUID) -> ReceiptFacts: ...

    async def add_receipt(self, server_id: str, recipient_github_id: int, message_id: UUID,
                          kind: ReceiptKind, node_id: UUID, now: datetime) -> ReceiptFacts: ...

    async def work_state(self, server_id: str, owner_github_id: int,
                         thread_id: UUID) -> Optional[WorkState]: ...

    async def change_work_state(self, server_id: str, owner_github_id: int, thread_id: UUID,
                                change: WorkCommand, now: datetime) -> WorkState: ...

    async def sync_page(self, server_id: str, owner_github_id: int, node_id: UUID,
                        continuation: Optional[str], limit: int, now: datetime) -> SyncPage: ...


StagingState = Literal["uploading", "verified", "pinned", "published", "expired"]


@runtime_checkable
class AttachmentStorage(Protocol):
    """Streamed file bytes (GridFS first). Staging is invisible to the
    recipient; `pin` binds a verified file to one proposed message by
    compare-and-set, and cleanup can claim only unpinned expired records."""

    async def create_staging(self, server_id: str, owner_github_id: int, attachment_id: UUID,
                             message_id: UUID, size_bytes: int, sha256: str,
                             now: datetime) -> StagingState: ...

    async def write_attempt(self, attachment_id: UUID, chunks: AsyncIterator[bytes],
                            max_bytes: int) -> int: ...

    async def finalize(self, attachment_id: UUID, now: datetime) -> StagingState: ...

    async def status(self, attachment_id: UUID) -> Optional[StagingState]: ...

    async def pin(self, attachment_id: UUID, message_id: UUID, digest: str) -> bool: ...

    async def mark_published(self, attachment_id: UUID) -> None: ...

    async def read(self, attachment_id: UUID) -> AsyncIterator[bytes]: ...

    async def claim_expired(self, now: datetime, limit: int) -> list[UUID]: ...

    async def delete(self, attachment_id: UUID) -> None: ...
