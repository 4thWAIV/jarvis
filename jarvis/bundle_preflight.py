"""The sender's check before a message with files goes: the draft's actual
snapshots inspected with the same readers the recipient uses
(bundle_inspection over the sender's own copies), the operations the task
needs, and the recipient's nodes from the stored team network snapshot
(bundle_capabilities). One answer, `PreflightResult`: ready, partial,
unknown or blocked, with only the gaps the sender can act on.

It is a prediction. It sends nothing to probe, and never says the
recipient opened anything. The capability snapshot it used is stored with
the revision approved (`record_for_approval`).
"""
from __future__ import annotations

from uuid import UUID

from jarvis.bundle_contract import PreflightResult


def preflight(server_id: str, owner_github_id: int, message_id: UUID) -> PreflightResult:
    """The draft's current revision against its recipient's nodes."""
    raise NotImplementedError("transport lane")


def record_for_approval(server_id: str, owner_github_id: int, message_id: UUID,
                        content_revision: int, result: PreflightResult) -> None:
    raise NotImplementedError("transport lane")
