"""Recording that a person's message has come in: the notification adapter.

Which arrivals are owed an announcement is durable in the messaging store
(`announcements_due`: the store marks a message due when it first holds it
as the owner's incoming). Which of the owner's nodes may speak of it is the
team server's lease (`transfer_presentation.ServePresentation`). This module
is the step between, and it says nothing itself: every due arrival becomes
an attention item (`attend`), and whether anything is said, and what, is
attention.py's one admission policy and digest.

    visible      every due arrival is recorded at once as an attention item
                 this node may not speak of (`attend(arrival, False)`), so
                 the pending count and Review updates show it whatever
                 happens to the lease. Every message keeps its own unread
                 cue in the queue.
    the lease    claimed only while this node is engaged (`engaged`: he has
                 used this JARVIS recently and is not away), never held
                 through an away period. Claimed, the item becomes this
                 node's to speak of (`attend(arrival, True)`); `attempted`
                 just before, `completed` just after. A claim another node
                 holds stays due here and is tried again once that lease
                 lapses; a completed one is recorded `elsewhere`. Completed
                 means this node took the presentation, not that anyone
                 heard or read it.
    the facts    trusted facts only as facts (the thread, the number of
                 files, whether it is to himself); the sender's roster name,
                 their JARVIS's network label and the subject are other
                 people's words, kept apart as untrusted. The body and
                 filenames are never in the attention item: the body goes
                 only to the conversation record (`Arrival.text`, one
                 message in the person's thread, the server's to record).
    no replay    only what the store says is due is attended to, so a page
                 reconnecting, a restart or a resync never brings back an
                 arrival already completed.
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Awaitable, Callable, NamedTuple, Optional
from uuid import UUID

from jarvis import messaging_ports as ports
from jarvis import messaging_wire as wire
from jarvis.transfer_presentation import ServePresentation, transfer_event

log = logging.getLogger("jarvis.arrival_announcer")

# The longest a name or a JARVIS label may be when it is said.
LABEL_MAX_CHARS = 60


def said_label(value: Optional[str]) -> Optional[str]:
    """A roster name or network label as it may be said: one line, its
    whitespace collapsed, cut to LABEL_MAX_CHARS. None when nothing is left."""
    text = wire.normalize_subject(value or "")
    if len(text) > LABEL_MAX_CHARS:
        text = text[:LABEL_MAX_CHARS - 1].rstrip() + "…"
    return text or None


class Labels(NamedTuple):
    """Who people and JARVIS instances are, by their ids."""
    person: Callable[[int], Optional[str]]
    jarvis: Callable[[UUID], Optional[str]]


async def serve_labels(transport: ports.ServeTransport,
                       network_raw: Callable[[], Optional[bytes]]) -> Labels:
    """People from the team's roster (`recipients`, the same list the
    recipient picker shows), JARVIS instances from the stored network
    snapshot (`team_client` keeps its bytes). Neither is an authority: a
    missing label is said generically."""
    try:
        members = (await transport.recipients()).members
    except ports.TransportError:
        members = []
    people = {m.github_id: said_label(m.name) or said_label(m.login) for m in members}
    nodes: dict[str, Optional[str]] = {}
    try:
        snapshot = json.loads(network_raw() or b"{}")
        for node in snapshot.get("nodes") or []:
            nodes[str(node.get("node_id"))] = said_label(node.get("label"))
    except (ValueError, AttributeError, TypeError):
        pass
    return Labels(person=people.get, jarvis=lambda node_id: nodes.get(str(node_id)))


@dataclass(frozen=True)
class Arrival:
    message_id: UUID
    thread_id: UUID
    sender: str                    # said name, or a generic one
    jarvis: Optional[str]          # the sending JARVIS's label, when it is another
    subject: str
    files: int
    self_message: bool
    # Who sent it, as the team server bound it to their device session, and
    # what it says: for the person's thread in the conversation record,
    # never for the attention item.
    sender_github_id: int = 0
    text: str = ""

    def attention(self) -> dict:
        """The attention item's facts and untrusted words."""
        return {"facts": {"thread_id": str(self.thread_id), "files": self.files,
                          "self_message": self.self_message},
                "untrusted": {k: v for k, v in (("sender", self.sender),
                                                ("from_jarvis", self.jarvis),
                                                ("subject", self.subject)) if v}}


Scope = Callable[[], Optional[tuple[str, int, UUID]]]
# Record one arrival as an attention item, and whether this node may speak of it.
Attend = Callable[[Arrival, bool], None]


class ArrivalAnnouncer:
    """Attends to what the store says is due, for one owner on one node.

    `scope` gives (server_id, owner_github_id, this node's id) while
    messaging is available, else None. `attend(arrival, audible)` records
    it; `engaged()` says whether this node may claim presentation now."""

    def __init__(self, *, store: ports.LocalMessagingStore, presentation: ServePresentation,
                 scope: Scope, labels: Callable[[], Awaitable[Labels]], attend: Attend,
                 engaged: Callable[[], bool], clock: ports.Clock):
        self._store, self._presentation, self._scope = store, presentation, scope
        self._labels, self._attend, self._engaged = labels, attend, engaged
        self._clock = clock
        self._busy = asyncio.Lock()
        self._shown: set[UUID] = set()

    def _due(self, server_id: str, owner: int) -> list[UUID]:
        now = self._clock()
        return [m for m in self._store.announcements_due(server_id, owner)
                if (self._presentation.elsewhere_until(m) or now) <= now]

    async def announce_due(self) -> int:
        """Record every due arrival, and claim the ones this node may speak
        of; how many it claimed. A call while one is under way returns 0 at
        once: that one gathers it."""
        if self._busy.locked():
            return 0
        async with self._busy:
            scope = self._scope()
            if scope is None:
                return 0
            server_id, owner, node_id = scope
            # The store's calls go to a worker thread: the loop is the voice
            # path's too, and the store waits on the knowledge engine.
            due = await asyncio.to_thread(self._due, server_id, owner)
            if not due:
                return 0
            labels = await self._labels()
            for message_id in due:
                local = await asyncio.to_thread(self._store.message, server_id, owner,
                                                message_id)
                if local is not None and message_id not in self._shown:
                    self._attend(self._arrival(local, labels, node_id), False)
                    self._shown.add(message_id)
            if not self._engaged():
                return 0
            return await self._announce(server_id, owner, node_id, labels)

    async def _claim(self, server_id: str, owner: int, due: list[UUID]
                     ) -> list[tuple[ports.LocalMessage, ports.TransferEvent, UUID]]:
        claimed = []
        for message_id in due:
            local = await asyncio.to_thread(self._store.message, server_id, owner, message_id)
            if local is None:
                continue
            event = transfer_event(owner, local.message, "incoming", self._clock())
            try:
                token = await self._presentation.reserve_announcement(event)
            except ports.TransportError as e:
                # Unreachable or refused: it stays due and is tried again.
                log.warning("announcement claim for %s failed: %s", message_id, e.code)
                break
            if token is not None:
                await asyncio.to_thread(self._store.set_announcement, server_id, owner,
                                        message_id, "claimed")
                claimed.append((local, event, token))
            elif self._presentation.elsewhere_until(message_id) is None:
                await asyncio.to_thread(self._store.set_announcement, server_id, owner,
                                        message_id, "elsewhere")
        return claimed

    def _arrival(self, local: ports.LocalMessage, labels: Labels, node_id: UUID) -> Arrival:
        m = local.message
        return Arrival(
            message_id=m.message_id, thread_id=m.thread_id,
            sender=labels.person(m.sender_github_id) or "A teammate",
            jarvis=None if m.source_node_id == node_id else labels.jarvis(m.source_node_id),
            subject=m.subject, files=len(m.attachments), self_message=local.self_message,
            sender_github_id=m.sender_github_id,
            text=f"{m.subject}\n\n{m.text}".strip())

    async def _announce(self, server_id: str, owner: int, node_id: UUID,
                        labels: Labels) -> int:
        claimed = await self._claim(server_id, owner,
                                    await asyncio.to_thread(self._due, server_id, owner))
        if not claimed:
            return 0
        steps = []
        for local, event, token in claimed:
            if await self._step(self._presentation.attempted, event, token):
                await asyncio.to_thread(self._store.set_announcement, server_id, owner,
                                        event.message_id, "attempted")
                steps.append((local, event, token))
            else:
                # Still this node's to claim: due again, tried on the next look.
                await asyncio.to_thread(self._store.set_announcement, server_id, owner,
                                        event.message_id, "due")
        attended = 0
        for local, event, token in steps:
            try:
                self._attend(self._arrival(local, labels, node_id), True)
            except Exception as e:
                # Not completed: the lease lapses and it may be claimed again.
                log.warning("arrival attention failed: %s", type(e).__name__)
                continue
            attended += 1
            if await self._step(self._presentation.complete_announcement, event, token):
                await asyncio.to_thread(self._store.set_announcement, server_id, owner,
                                        event.message_id, "completed")
        return attended

    @staticmethod
    async def _step(step, event: ports.TransferEvent, token: UUID) -> bool:
        """One lease step; False (logged) when the team server refused it or
        could not be reached. The lease then lapses on its own."""
        try:
            await step(event, token)
            return True
        except ports.TransportError as e:
            log.warning("announcement step for %s failed: %s", event.message_id, e.code)
            return False
