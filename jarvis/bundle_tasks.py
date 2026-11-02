"""One-off requests on an ordinary thread: the task context a message
carries (requested outcome, primary artifact by attachment id and hash,
supporting members, reply audience), the recipient's review checklist and
source map, the scope he accepts, companions, and repair resends linked to
what they supersede.

The task context is message content (`MessageContent.task` on the wire):
setting it changes the draft, so the owner reviews and approves it like
any edit. A repair is a new draft in the same thread, replying to the
failed message, whose `supersedes` names the old attachment; the old one is
kept. Nothing here sends: every message goes through the existing approval.
"""
from __future__ import annotations

from typing import Literal, Optional
from uuid import UUID

from jarvis.bundle_contract import ReviewState

CompanionKind = Literal["pdf_text", "page_images", "file_index"]
RepairMode = Literal["flatten_wrapper", "companions"]


def set_task(server_id: str, owner_github_id: int, message_id: UUID,
             expected_draft_revision: int, task: Optional[dict]):
    """Set or clear a draft's task context (wire TaskContext JSON); the Draft."""
    raise NotImplementedError("transport lane")


def add_companions(server_id: str, owner_github_id: int, message_id: UUID,
                   expected_draft_revision: int, attachment_id: UUID,
                   kinds: list[CompanionKind]):
    """Derived files added to a draft beside the original; the Draft."""
    raise NotImplementedError("transport lane")


def prepare_repair(server_id: str, owner_github_id: int, message_id: UUID,
                   attachment_id: UUID, mode: RepairMode):
    """A new draft replying to `message_id` with a repaired attachment and
    its supersedes link; the Draft."""
    raise NotImplementedError("transport lane")


def review_state(server_id: str, owner_github_id: int, thread_id: UUID) -> ReviewState:
    raise NotImplementedError("transport lane")


def set_scope(server_id: str, owner_github_id: int, thread_id: UUID,
              expected_revision: int, scope: Literal["full", "partial"]) -> ReviewState:
    raise NotImplementedError("transport lane")
