"""Delivery between this JARVIS and its team server: the messaging transport
over the paired team connection, and the loop that submits approved sends,
reconciles the owner's messages, settles owed receipts and replays work
commands made while the team server was out of reach.

The transport (`TeamTransport`) is `ServeTransport` over `team_client`'s
session: the page never holds the credential. It is bound to one pairing
(server, owner and the connection's generation); an answer that comes back
after the generation changed is discarded as if none came, so a response
from the previous pairing can never touch the new owner's records.

The loop (`Delivery`) is independent of the network exchange and of any
page: it runs while messaging is available, every SYNC_INTERVAL_SEC or as
soon as something local wakes it, backing off with jitter while it fails.
It works on the current pairing's records only.

  sends     an approved revision goes out through the one guard
            (`ApprovalGuard.require_approved_revision`) after its files are
            uploaded; the attempt is stored as `submitting` before the
            request leaves. A lost or transient answer is `accept_unknown`
            and is retried under the same idempotency key (full jitter,
            RETRY_MAX_ATTEMPTS, then only by the owner's Retry); a refusal
            is `failed`. A restart finds `submitting` and treats it as a
            lost answer: never as nothing having happened.
  sync      the full keyset scan, a bounded number of pages a pass, each
            page stored with its continuation in one transaction; an expired
            continuation starts a new scan. Absence never prunes.
  receipts  owed receipts go in batches of RECEIPT_BATCH_MAX; the answer
            settles them, `not_found` included.
  commands  the latest work command per item, with its expected revision;
            a conflict keeps the team server's latest and drops it.

A hint goes out (`on_change`) only after a pass committed something, and
an incoming message is presented when the store first holds it.
"""
from __future__ import annotations

import asyncio
import logging
import random
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, AsyncIterator, Callable, Optional, Protocol
from urllib.parse import urlencode
from uuid import UUID

from pydantic import BaseModel, ValidationError

from jarvis import data_paths
from jarvis import messaging_ports as ports
from jarvis import messaging_wire as wire
from jarvis.team_client import TeamError

log = logging.getLogger("jarvis.messaging")

SYNC_INTERVAL_SEC = 5.0
MAX_BACKOFF_SEC = 60.0
# Pages one pass reads before it yields; the scan resumes from its stored
# continuation on the next pass, so a long history never blocks the loop.
SYNC_PAGES_PER_PASS = 20
# How long a team server's capabilities are trusted before they are read
# again (also read again whenever the pairing changes).
CAPABILITIES_TTL_SEC = 300.0
PREFIX = "/api/v1/messages"


# -- the team connection, as delivery uses it -------------------------------------

class TeamCalls(Protocol):
    """What delivery needs of `team_client.TeamConnection`: its saved
    connection, whether it is signed out, its JSON call, and a generation
    that moves whenever the session or the server it speaks for changes."""
    saved: Optional[dict]
    signed_out: bool
    generation: int

    async def call(self, method: str, path: str, body: Any = None) -> tuple[int, Any]: ...


@dataclass(frozen=True)
class Pairing:
    """Whose records a request may touch: the server, the owner, and the
    connection generation the request was made under."""
    server_id: str
    owner_github_id: int
    generation: int

    @property
    def ns(self) -> tuple[str, int]:
        return self.server_id, self.owner_github_id


def pairing_of(connection: TeamCalls) -> Optional[Pairing]:
    saved = connection.saved
    if not saved or connection.signed_out:
        return None
    owner = (saved.get("me") or {}).get("github_id")
    if not isinstance(owner, int) or not saved.get("server_id"):
        return None
    return Pairing(saved["server_id"], owner, connection.generation)


# Codes for answers that carry none of the contract's (a 503 with the
# server's health, a proxy's page).
_STATUS_CODES = {401: "session_invalid", 403: "device_required", 404: "message_not_found",
                 413: "too_large", 429: "rate_limited", 503: "dependency_unavailable"}


def _refusal(status: int, payload: Any) -> ports.TransportError:
    err = payload.get("error") if isinstance(payload, dict) else None
    err = err if isinstance(err, dict) else {}
    code = err.get("code") if isinstance(err.get("code"), str) else ""
    if not code or code not in wire.ERRORS:
        code = code or _STATUS_CODES.get(status) or ("internal" if status >= 500
                                                     else "invalid_request")
    retry_after = err.get("retry_after")
    latest = err.get("latest")
    return ports.TransportError(code, status=status,
                                latest=latest if isinstance(latest, dict) else None,
                                retry_after=float(retry_after)
                                if isinstance(retry_after, (int, float)) else None)


class TeamTransport:
    """`ServeTransport` over the paired team connection, for one pairing.
    The attachment byte streams are the files stream's one streamed request
    (`streams`, given at wiring); everything else is the JSON call."""

    def __init__(self, connection: TeamCalls, pairing: Pairing, streams: Any = None):
        self.connection, self.pairing, self.streams = connection, pairing, streams

    async def _call(self, method: str, path: str, body: Any = None) -> Any:
        if pairing_of(self.connection) != self.pairing:
            raise ports.TransportError("session_missing", status=401)
        try:
            status, payload = await self.connection.call(method, path, body)
        except TeamError as e:
            if e.unreachable:
                raise ports.TransportError("transport") from None
            raise ports.TransportError("session_missing", status=401) from None
        if status == 401:
            raise _refusal(status, payload)
        if self.connection.generation != self.pairing.generation:
            raise ports.TransportError("transport")       # answered for another pairing
        if not 200 <= status < 300:
            raise _refusal(status, payload)
        return payload

    async def _model(self, model: type[BaseModel], method: str, path: str,
                     body: Optional[BaseModel] = None):
        payload = await self._call(method, path,
                                   body.model_dump(mode="json") if body is not None else None)
        try:
            return model.model_validate(payload)
        except ValidationError:
            # An answer this JARVIS cannot read is no answer: a send stays
            # unknown and is retried under its key.
            raise ports.TransportError("transport") from None

    async def capabilities(self) -> list[str]:
        payload = await self._call("GET", "/version")
        caps = payload.get("capabilities") if isinstance(payload, dict) else None
        return [c for c in caps if isinstance(c, str)] if isinstance(caps, list) else []

    async def recipients(self) -> wire.RecipientList:
        return await self._model(wire.RecipientList, "GET", PREFIX + "/recipients")

    async def send(self, request: wire.SendRequest) -> wire.SendResult:
        return await self._model(wire.SendResult, "POST", PREFIX, request)

    async def get(self, message_id: UUID) -> wire.AcceptedMessage:
        return await self._model(wire.AcceptedMessage, "GET", f"{PREFIX}/{message_id}")

    async def sync(self, node_id: UUID, continuation: Optional[str],
                   limit: int) -> wire.SyncPage:
        query = {"node_id": str(node_id), "limit": limit}
        if continuation:
            query["continuation"] = continuation
        return await self._model(wire.SyncPage, "GET", f"{PREFIX}/sync?{urlencode(query)}")

    async def receipts(self, batch: wire.ReceiptBatch) -> wire.ReceiptBatchResult:
        return await self._model(wire.ReceiptBatchResult, "POST", PREFIX + "/receipts", batch)

    async def change_work_state(self, thread_id: UUID,
                                request: wire.WorkCommandRequest) -> wire.WorkState:
        return await self._model(wire.WorkState, "PUT",
                                 f"{PREFIX}/threads/{thread_id}/work-state", request)

    async def announce(self, message_id: UUID,
                       request: wire.AnnouncementRequest) -> wire.AnnouncementLease:
        return await self._model(wire.AnnouncementLease, "POST",
                                 f"{PREFIX}/{message_id}/announcement", request)

    async def create_attachment(self, request: wire.AttachmentCreate) -> wire.AttachmentStatus:
        return await self._model(wire.AttachmentStatus, "POST", "/api/v1/attachments", request)

    async def finalize_attachment(self, attachment_id: UUID) -> wire.AttachmentStatus:
        return await self._model(wire.AttachmentStatus, "POST",
                                 f"/api/v1/attachments/{attachment_id}/finalize")

    async def attachment_status(self, attachment_id: UUID) -> wire.AttachmentStatus:
        return await self._model(wire.AttachmentStatus, "GET",
                                 f"/api/v1/attachments/{attachment_id}")

    async def upload_attachment(self, attachment_id: UUID,
                                chunks: AsyncIterator[bytes]) -> wire.AttachmentStatus:
        if self.streams is None:
            raise ports.TransportError("transport")
        return await self.streams.upload(self, attachment_id, chunks)

    def download_attachment(self, message_id: UUID,
                            attachment_id: UUID) -> AsyncIterator[bytes]:
        if self.streams is None:
            raise ports.TransportError("transport")
        return self.streams.download(self, message_id, attachment_id)


# -- the delivery loop --------------------------------------------------------------

def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _this_node() -> UUID:
    return UUID(data_paths.node_id())


class Delivery:
    """The loop and the one place a send is submitted. Every dependency is
    injected; production passes the real store, connection and guard."""

    def __init__(self, *, store, connection: TeamCalls, guard: ports.ApprovalGuard,
                 snapshots: Optional[ports.AttachmentSnapshots] = None,
                 presentation: Optional[ports.PresentationDispatcher] = None,
                 node_id: Callable[[], UUID] = _this_node, clock: ports.Clock = _utcnow,
                 rand: ports.RandomSource = random.Random(), faults: wire.Faults = wire.NO_FAULTS,
                 on_change: Callable[[], None] = lambda: None,
                 enabled: Callable[[], bool] = ports.messaging_enabled,
                 transport_for: Callable[[TeamCalls, Pairing], Any] = TeamTransport,
                 interval: float = SYNC_INTERVAL_SEC):
        self.store, self.connection, self.guard = store, connection, guard
        self.snapshots, self.presentation = snapshots, presentation
        self.node_id, self.clock, self.rand, self.faults = node_id, clock, rand, faults
        self.on_change, self.enabled = on_change, enabled
        self.transport_for, self.interval = transport_for, interval
        self.stale = False
        self.last_error: Optional[str] = None
        self.failures = 0
        self._capabilities: dict[str, tuple[int, float, list[str]]] = {}
        self._recovered: set[tuple[str, int]] = set()
        self._wake = asyncio.Event()
        self._task: Optional[asyncio.Task] = None

    # -- the pairing and what it offers ----------------------------------------------

    def pairing(self) -> Optional[Pairing]:
        return pairing_of(self.connection)

    def transport(self, pairing: Pairing):
        return self.transport_for(self.connection, pairing)

    async def availability(self) -> ports.Availability:
        """Fail closed: switched off, not paired, or a team server that does
        not offer messaging.v1 (the last capabilities it answered with; none
        known yet is unsupported, and stale)."""
        pairing = self.pairing()
        caps: list[str] = []
        if self.enabled() and pairing is not None:
            caps = await self._capabilities_of(pairing)
        return ports.availability(enabled=self.enabled(), paired=pairing is not None,
                                  capabilities=caps)

    async def _capabilities_of(self, pairing: Pairing) -> list[str]:
        known = self._capabilities.get(pairing.server_id)
        now = asyncio.get_running_loop().time()
        if known and known[0] == pairing.generation and now - known[1] < CAPABILITIES_TTL_SEC:
            return known[2]
        try:
            caps = await self.transport(pairing).capabilities()
        except ports.TransportError as e:
            self.stale, self.last_error = True, e.code
            return known[2] if known else []
        self._capabilities[pairing.server_id] = (pairing.generation, now, caps)
        return caps

    # -- one pass ------------------------------------------------------------------------

    def wake(self) -> None:
        """Run a pass now (an approval, a read, a retry)."""
        self._wake.set()

    def changed(self) -> None:
        try:
            self.on_change()
        except Exception as e:  # a hint that fails must not stop delivery
            log.warning("messaging: hint failed (%s)", type(e).__name__)

    async def tick(self) -> None:
        """One pass over the current pairing. Raises only an injected fault
        (tests); every transport failure is recorded and retried later."""
        pairing = self.pairing()
        if pairing is None or await self.availability() != "available":
            return
        t = self.transport(pairing)
        changed = self._recover(pairing)
        failed: Optional[ports.TransportError] = None
        for step in (self._replay_commands, self._submit_due, self._sync, self._send_receipts):
            try:
                changed = await step(t, pairing) or changed
            except ports.TransportError as e:
                failed = failed or e
                if e.session_ended:
                    break
        self.stale = failed is not None
        self.last_error = failed.code if failed else None
        self.failures = self.failures + 1 if failed else 0
        if changed:
            self.changed()

    def _recover(self, pairing: Pairing) -> bool:
        """Once per pairing per run: a send a crash left `submitting` may
        have been accepted. It is unknown, retried under its key."""
        if pairing.ns in self._recovered:
            return False
        self._recovered.add(pairing.ns)
        stuck = self.store.submitting(*pairing.ns)
        for send in stuck:
            self.store.transition_send(*pairing.ns, send, send.model_copy(update={
                "state": ports.next_send_state(send.state, "answer_lost"),
                "failure": "transport", "next_attempt_at": self.clock()}))
        return bool(stuck)

    async def _replay_commands(self, t, pairing: Pairing) -> bool:
        changed = False
        for thread_id, request in self.store.pending_commands(*pairing.ns):
            try:
                state = await t.change_work_state(thread_id, request)
            except ports.TransportError as e:
                if e.code == "transport" or e.retryable or e.session_ended:
                    raise
                if e.code == "revision_conflict" and e.latest:
                    self.store.save_work_state(*pairing.ns,
                                               wire.WorkState.model_validate(e.latest))
                log.info("messaging: a kept work command was refused (%s)", e.code)
            else:
                self.store.save_work_state(*pairing.ns, state)
            self.store.settle_command(*pairing.ns, thread_id)
            changed = True
        return changed

    def _due(self, send: ports.LocalSend) -> bool:
        if send.state not in ("approved", "accept_unknown", "failed"):
            return False
        if send.next_attempt_at is None:
            return send.state == "approved" and send.failure is None
        return send.next_attempt_at <= self.clock()

    async def _submit_due(self, t, pairing: Pairing) -> bool:
        changed = False
        for send in self.store.unresolved_sends(*pairing.ns):
            if self._due(send):
                changed = await self.submit(t, pairing, send) or changed
        return changed

    def _hold(self, pairing: Pairing, send: ports.LocalSend, code: str,
              retry_at: Optional[datetime]) -> bool:
        """Keep the send where it is, saying why, until `retry_at` (or the
        owner acts)."""
        return self.store.transition_send(*pairing.ns, send, send.model_copy(update={
            "failure": code, "next_attempt_at": retry_at}))

    def _retry_at(self, attempts: int, retry_after: Optional[float]) -> datetime:
        delay = max(wire.retry_delay(attempts, self.rand.random()), retry_after or 0.0)
        return self.clock() + timedelta(seconds=delay)

    async def submit(self, t, pairing: Pairing, send: ports.LocalSend) -> bool:
        """One attempt at one approved revision, through the guard."""
        ns = pairing.ns
        try:
            approved = self.guard.require_approved_revision(*ns, send.message_id,
                                                            send.content_revision)
            if approved.request.attachments and self.snapshots is not None:
                await self.snapshots.ensure_uploaded(*ns, send.message_id, send.content_revision)
        except ports.NotApproved:
            return self._hold(pairing, send, "not_approved", None)
        except ports.SnapshotMissing:
            return self._hold(pairing, send, "snapshot_missing", None)
        except ports.TransportError as e:
            retry = None if e.session_ended or not (e.retryable or e.code == "transport") \
                else self._retry_at(send.attempts + 1, e.retry_after)
            return self._hold(pairing, send, e.code, retry)
        event = "submit" if send.state == "approved" else "retry"
        going = send.model_copy(update={"state": ports.next_send_state(send.state, event),
                                        "attempts": send.attempts + 1, "failure": None,
                                        "next_attempt_at": None})
        if not self.store.transition_send(*ns, send, going):
            return False                         # cancelled or edited meanwhile
        self.faults.check("client_before_send")
        try:
            result = await t.send(approved.request)
        except ports.TransportError as e:
            self._after_refusal(pairing, going, e)
            return True
        self.faults.check("client_before_local_save")
        incoming = self.store.record_accepted(*ns, result)
        self.faults.check("client_after_local_save")
        log.info("messaging: %s accepted (attempt %d)", send.message_id, going.attempts)
        if incoming:
            self._present(pairing, result.message)
        return True

    def _after_refusal(self, pairing: Pairing, going: ports.LocalSend,
                       e: ports.TransportError) -> None:
        if (e.code == "transport" or e.retryable) and not e.session_ended:
            auto = going.attempts < wire.RETRY_MAX_ATTEMPTS
            after = going.model_copy(update={
                "state": ports.next_send_state(going.state, "answer_lost"), "failure": e.code,
                "next_attempt_at": self._retry_at(going.attempts, e.retry_after) if auto
                else None})
        else:
            after = going.model_copy(update={
                "state": ports.next_send_state(going.state, "refused"), "failure": e.code,
                "next_attempt_at": None})
        self.store.transition_send(*pairing.ns, going, after)
        log.info("messaging: %s not accepted (%s)", going.message_id, e.code)

    async def _sync(self, t, pairing: Pairing) -> bool:
        node = self.node_id()
        continuation = self.store.sync_position(*pairing.ns, node)
        changed, restarted = False, False
        for _ in range(SYNC_PAGES_PER_PASS):
            try:
                page = await t.sync(node, continuation, wire.SYNC_PAGE_DEFAULT)
            except ports.TransportError as e:
                if e.code != "sync_restart" or restarted:
                    raise
                restarted, continuation = True, None
                self.store.restart_sync(*pairing.ns, node)
                continue
            new, page_changed = self.store.store_page_changes(*pairing.ns, node, page)
            changed = changed or page_changed
            for message_id in new:
                local = self.store.message(*pairing.ns, message_id)
                self._present(pairing, local.message)
            if page.scan_complete:
                break
            continuation = page.continuation
        return changed

    async def _send_receipts(self, t, pairing: Pairing) -> bool:
        owed = self.store.receipts_owed(*pairing.ns)
        node = self.node_id()
        for start in range(0, len(owed), wire.RECEIPT_BATCH_MAX):
            batch = owed[start:start + wire.RECEIPT_BATCH_MAX]
            self.faults.check("client_before_receipt_ack")
            result = await t.receipts(wire.ReceiptBatch(node_id=node, receipts=[
                wire.ReceiptItem(message_id=o.message_id, kind=o.kind) for o in batch]))
            self.store.settle_receipts(*pairing.ns, batch, result)
        return bool(owed)

    def _present(self, pairing: Pairing, message: wire.AcceptedMessage) -> None:
        """An arrival, once, after it is stored: drawn, and left due for the
        announcer (signals) to claim."""
        if self.presentation is None:
            return
        event = ports.TransferEvent(
            event_id=ports.transfer_event_id(message.message_id, "incoming"),
            message_id=message.message_id, thread_id=message.thread_id, direction="incoming",
            owner_github_id=pairing.owner_github_id, source_node_id=message.source_node_id,
            sender_github_id=message.sender_github_id,
            recipient_github_id=message.recipient_github_id, occurred_at=self.clock(),
            replay=False)
        try:
            self.presentation.present(event)
        except Exception as e:  # drawing must never undo delivery
            log.warning("messaging: presentation failed (%s)", type(e).__name__)

    # -- the lifecycle ------------------------------------------------------------------

    def next_delay(self) -> float:
        if not self.failures:
            return self.interval
        backoff = min(MAX_BACKOFF_SEC, self.interval * (2 ** (self.failures - 1)))
        return backoff * (0.5 + self.rand.random() / 2)

    async def run(self) -> None:
        while True:
            self._wake.clear()
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self.stale, self.last_error = True, type(e).__name__
                self.failures += 1
                log.warning("messaging: pass failed (%s)", type(e).__name__)
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=self.next_delay())
            except asyncio.TimeoutError:
                pass

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self.run())

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
