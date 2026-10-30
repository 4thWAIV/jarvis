"""Human transfers on the page and the announcement lease: the production
`messaging_ports.PresentationDispatcher`.

A transfer is drawn with the agents' ray and orb machinery (`RayLane.tsx`)
and has no authority: delivery and read facts come only from the store.
This module only says what to draw and who may speak.

    present        one `{"type": "human_transfer", "event": ...}` frame to the
                   open pages, once per event on this node (a repeat of the
                   same event id is dropped), and kept for RECENT_SEC so a
                   page that (re)connects gets it again marked `replay`: it
                   reconciles and never animates.
    the lease      before anything is said about an arrival, this node claims
                   the team server's lease for it (`ServeTransport.announce`).
                   A page's already-shown set is not a lease: two JARVIS
                   instances each have their own pages. A claim refused
                   because another of the owner's nodes holds a live lease
                   says until when (`elsewhere_until`), so this node may take
                   it over once it has lapsed; a completed one is final.

`transfer_event` is the one way a TransferEvent is made, for whichever
service presents it (outgoing on a successful hand-over, incoming when the
store first holds it as the owner's).
"""
from __future__ import annotations

import logging
from collections import OrderedDict
from datetime import datetime, timedelta
from typing import Callable, Optional
from uuid import UUID

from jarvis import messaging_ports as ports
from jarvis import messaging_wire as wire

log = logging.getLogger("jarvis.transfer_presentation")

# How long a presented transfer is re-sent to a page that (re)connects:
# about one ray's life on the page (RAY_MS, six seconds), so a page that
# reconnects mid-ray knows it was already shown, and nothing older is
# reconciled from here (the store's snapshot is the record).
RECENT_SEC = 8.0
# Never more than this many kept, however fast they come.
RECENT_MAX = 50


def transfer_event(owner_github_id: int, message: wire.AcceptedMessage | wire.SendRequest,
                   direction: ports.Direction, occurred_at: datetime) -> ports.TransferEvent:
    """The presentation event for one message going one way, as every node
    of the owner names it (`transfer_event_id`). A live push: not replay.
    `message` is the accepted message, or, for an outgoing one at its
    hand-over (before the team server has it), the approved envelope, whose
    sender is the owner."""
    return ports.TransferEvent(
        event_id=ports.transfer_event_id(message.message_id, direction),
        message_id=message.message_id, thread_id=message.thread_id, direction=direction,
        owner_github_id=owner_github_id, source_node_id=message.source_node_id,
        sender_github_id=getattr(message, "sender_github_id", owner_github_id),
        recipient_github_id=message.recipient_github_id,
        occurred_at=occurred_at, replay=False)


def transfer_frame(event: ports.TransferEvent, *, replay: bool) -> dict:
    """The /ws/sessions frame for one event."""
    return ports.HumanTransferFrame(
        event=event.model_copy(update={"replay": replay})).model_dump(mode="json")


class ServePresentation:
    """The production PresentationDispatcher for one paired node.

    `send` puts a frame on every open page's /ws/sessions queue (the
    server's `_send_to_session_clients`); `clock` is the messaging clock."""

    def __init__(self, *, transport: ports.ServeTransport, node_id: UUID,
                 send: Callable[[dict], None], clock: ports.Clock):
        self._transport, self._node_id, self._send, self._clock = transport, node_id, send, clock
        self._recent: OrderedDict[str, ports.TransferEvent] = OrderedDict()
        self._presented_at: dict[str, datetime] = {}
        self._elsewhere: dict[UUID, Optional[datetime]] = {}

    # -- drawing -------------------------------------------------------------

    def _forget_old(self) -> None:
        cutoff = self._clock() - timedelta(seconds=RECENT_SEC)
        for event_id in list(self._recent):
            if len(self._recent) > RECENT_MAX or self._presented_at[event_id] < cutoff:
                del self._recent[event_id]
                del self._presented_at[event_id]

    def present(self, event: ports.TransferEvent) -> None:
        """Draw it on every open page, once. Never raises: a page that cannot
        be told loses a ray, never a message."""
        self._forget_old()
        if event.event_id in self._recent:
            return
        self._recent[event.event_id] = event
        self._presented_at[event.event_id] = self._clock()
        try:
            self._send(transfer_frame(event, replay=False))
        except Exception as e:
            log.warning("transfer %s was not drawn: %s", event.event_id, type(e).__name__)

    def replay_frames(self) -> list[dict]:
        """What a page that has just (re)connected is sent: the transfers
        presented in the last RECENT_SEC, marked replay, oldest first."""
        self._forget_old()
        return [transfer_frame(event, replay=True) for event in self._recent.values()]

    # -- the announcement lease ----------------------------------------------

    async def _step(self, event: ports.TransferEvent, step: str,
                    token: Optional[UUID] = None) -> wire.AnnouncementLease:
        return await self._transport.announce(event.message_id, wire.AnnouncementRequest(
            node_id=self._node_id, step=step, lease_token=token))

    async def reserve_announcement(self, event: ports.TransferEvent) -> Optional[UUID]:
        """The lease token, or None when another node holds it or it is done
        (`elsewhere_until` says which). Any other refusal is raised."""
        try:
            lease = await self._step(event, "claim")
        except ports.TransportError as e:
            if e.code != "announcement_claimed":
                raise
            held = wire.AnnouncementLease.model_validate(e.latest) if e.latest else None
            self._elsewhere[event.message_id] = (
                None if held is None or held.state == "completed" else held.expires_at)
            return None
        self._elsewhere.pop(event.message_id, None)
        return lease.lease_token

    def elsewhere_until(self, message_id: UUID) -> Optional[datetime]:
        """After a refused claim: when the other node's live lease lapses, or
        None when the announcement is done (or was never refused here)."""
        return self._elsewhere.get(message_id)

    async def attempted(self, event: ports.TransferEvent, token: UUID) -> None:
        await self._step(event, "attempted", token)

    async def complete_announcement(self, event: ports.TransferEvent, token: UUID) -> None:
        await self._step(event, "completed", token)
