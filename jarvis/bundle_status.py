"""Openability: what actually happened when the recipient's JARVIS opened a
file, kept per (message, attachment, content revision) and reported to the
sender as a technical receipt over the existing messaging transport (the
wire's `OpenabilityReport` in a `ReceiptItem` of kind `openability`, only to
a team server offering `messaging.bundles.v1`).

`observe` is bundle_inspection's observer: it folds each Observation into
the record (verified, ready, partial, unsupported, corrupt, unavailable,
with the failed operation and member) and owes a receipt when the status
changes. The record carries counts, codes and the archive's member path;
never document text, device paths or logs.
"""
from __future__ import annotations

from uuid import UUID

from jarvis.bundle_inspection import Observation


def observe(observation: Observation) -> None:
    raise NotImplementedError("transport lane")


def status(server_id: str, owner_github_id: int, message_id: UUID,
           attachment_id: UUID) -> dict:
    """The latest record for one attachment (OpenabilityReport JSON): this
    node's own on the recipient's side, the reported one on the sender's."""
    raise NotImplementedError("transport lane")
