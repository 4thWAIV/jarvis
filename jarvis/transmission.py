"""One message sent on Tim's word, whoever it is for.

Every message JARVIS sends for him goes the same way, and this is that way,
shared: an agent session (`server._AgentDestination`) and a human's JARVIS
through the team server (the messaging service's destination) differ only
in who they are, so each is an adapter and the sequence is here once.

    acceptance    a capture is sendable only once it has been heard in full
                  since it last changed (`read_capture`, or Read on its box)
                  or accepted on screen (Accept, Send). `accepted` is that
                  rule; the payload a destination binds to it (a human
                  message's files and revision) is the destination's.
    staging       `stage`: the recipient's own refusals first, then "no
                  voice, nothing goes unheard", then the durable record is
                  written and the send is staged; nothing is spoken or sent.
    the turn      a staged send waits for the turn under way to end
                  (`server._perform_staged_steers` at the turn's end, or at
                  once when no turn is under way). That drain is the
                  server's: one list, so one order and exactly-once.
    perform       `perform`: the cancel window, then the destination's own
                  reason to wait (an agent's one-message-per-turn queue),
                  then delivery and its outcome, each written to the
                  destination's record before it is said. Every path ends
                  the record as exactly one of cancelled, queued, sent or
                  failed, and a capture not sent is his to deal with again.

What a destination's "sent" means is its own: for an agent, the bytes left
over its inbox; for a human, the approved envelope is durably in the
outbox. Neither is delivery, which is reported later, by whatever follows
that destination.
"""
from __future__ import annotations

import time
from typing import Callable, Optional, Protocol, runtime_checkable

from jarvis.speech import Priority

# How a send's record ends, whatever the destination keeps it in.
CANCELLED = "cancelled"
QUEUED = "queued"
SENT = "sent"
FAILED = "failed"
NO_VOICE = "no_voice"          # the failure when there is no voice to hear it


def accepted(capture: dict) -> bool:
    """Heard in full, or accepted on screen, since it last changed."""
    return capture["read"] == capture["text"]


def accept(capture: dict) -> None:
    """He has read it on the screen (Accept, Send): it counts as heard."""
    capture["read"] = capture["text"]


@runtime_checkable
class Recipient(Protocol):
    """Who a capture is for, at staging time."""

    def refusal(self) -> Optional[tuple[str, str]]:
        """(the failure recorded, the sentence for him), or None: it can go."""

    def unheard(self) -> str:
        """The sentence when there is no voice to read it back with."""

    def refuse(self, failure: str) -> None:
        """Record a message that is never sent: failed, and why."""

    def open(self, capture: dict) -> tuple[object, object]:
        """Write the durable record (before anything is sent) and return its
        id and the staged send, as the server's drain takes it: a
        `Destination`, or the agent's own `_StagedSteer`."""


@runtime_checkable
class Destination(Protocol):
    """One staged send, at send time."""

    capture_id: Optional[int]
    priority: bool          # he asked for it now: no waiting its turn

    def record(self) -> object:
        """Its record's id, writing the record first if staging did not."""

    def settle(self, status: str, **fields) -> None:
        """Write how it ended: one of CANCELLED, QUEUED, SENT, FAILED."""

    def why_wait(self) -> Optional[str]:
        """Why it must wait its turn rather than go now, or None."""

    async def deliver(self) -> str:
        """Hand it over; the outcome."""

    def delivered(self, outcome: str) -> bool:
        """Whether the outcome is a successful hand-over."""

    async def went(self) -> None:
        """After a successful hand-over: move it up, draw it going."""

    async def say_queued(self, speech, why: str) -> None: ...

    async def say_outcome(self, speech, outcome: str) -> None: ...


class CaptureBox(Protocol):
    """The capture box a send came from, as the send moves along."""

    def transmitting(self, capture_id: Optional[int]) -> None: ...

    def sent(self, capture_id: Optional[int]) -> None:
        """Gone (sent, failed in sending, or queued): close the capture."""

    def reopen(self, capture_id: Optional[int]) -> None:
        """Not sent: if it is still open, it counts as unsent again."""


def stage(recipient: Recipient, capture: dict, *, voice,
          put: Callable[[object], None]) -> tuple[Optional[object], Optional[str]]:
    """Record a capture as a message to `recipient` and stage it to send:
    (its record id, None), or (None, why it cannot go), recorded as failed.
    The caller has already checked `accepted`."""
    refused = recipient.refusal()
    if refused:
        failure, sentence = refused
        recipient.refuse(failure)
        return None, sentence
    if voice is None:
        # Sending unheard is never acceptable: with no voice there is no
        # read-back, and no read-back means no safety gate at all.
        recipient.refuse(NO_VOICE)
        return None, recipient.unheard()
    record_id, staged = recipient.open(capture)
    put(staged)
    capture["staged"] = True
    return record_id, None


async def perform(destination: Destination, *, speech, cancel_window: float,
                  captures: CaptureBox) -> None:
    """One staged send, start to finish. Its record ends as exactly one of
    cancelled, queued, sent or failed.

    Nothing is sent unheard, and that was decided before this runs (only an
    `accepted` capture is staged, and what is sent is exactly it), so this
    says nothing first: it opens the cancel window (the turn's speech is
    done by now), then sends."""
    destination.record()
    settled = False

    def settle(status: str, **fields) -> None:
        nonlocal settled
        if settled:
            return
        settled = True
        destination.settle(status, **fields)

    try:
        if speech is None:
            # The mouth went away between turns.
            settle(FAILED, failure=NO_VOICE)
            return
        if await speech.open_cancel_window(cancel_window):
            settle(CANCELLED)
            await speech.say("Cancelled, sir.", Priority.NORMAL, label="readback-outcome")
            return

        why = None if destination.priority else destination.why_wait()
        if why:
            settle(QUEUED)
            captures.sent(destination.capture_id)
            await destination.say_queued(speech, why)
            return

        captures.transmitting(destination.capture_id)
        try:
            outcome = await destination.deliver()
        finally:
            captures.sent(destination.capture_id)
        if destination.delivered(outcome):
            settle(SENT, sent_at=time.time())
            await destination.went()
        else:
            settle(FAILED, failure=outcome)
        await destination.say_outcome(speech, outcome)
    except Exception:
        # The record must never be left staged.
        settle(FAILED, failure=FAILED)
        raise
    finally:
        captures.reopen(destination.capture_id)
