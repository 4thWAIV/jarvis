"""Test adapters for human messaging: in-memory implementations of every
interface in `jarvis/messaging_ports.py`, built on the contract's own rules
(`jarvis/messaging_wire.py`), so a worker's service runs against them
exactly as it will against the real ones. Nothing here is reachable from
the application.

    FixedClock, SeqIds, SeqRandom   time, ids and jitter that tests move
    FakeServe                       one team server: roster, messages,
                                    receipts, work state, sync scans, and
                                    the fault points the server owns;
                                    `.transport(github_id, node_id)` is one
                                    paired node's ServeTransport
    MemoryStore                     a LocalMessagingStore in dicts
    TableGuard                      an ApprovalGuard over approved revisions
    StaticSnapshots                 AttachmentSnapshots from a table
    RecordingPresentation           a PresentationDispatcher with leases
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import UUID

from pydantic import BaseModel

from jarvis import messaging_ports as ports
from jarvis import messaging_wire as wire

T0 = datetime(2026, 10, 29, 12, 0, tzinfo=timezone.utc)


class FixedClock:
    def __init__(self, at: datetime = T0):
        self.at = at

    def __call__(self) -> datetime:
        return self.at

    def advance(self, **kw) -> None:
        self.at += timedelta(**kw)


class SeqIds:
    def __init__(self, start: int = 1):
        self.n = start

    def new_id(self) -> UUID:
        self.n += 1
        return UUID(f"00000000-0000-4000-8000-{self.n - 1:012x}")


class SeqRandom:
    def __init__(self, values=(0.5,)):
        self.values, self.i = list(values), 0

    def random(self) -> float:
        self.i += 1
        return self.values[(self.i - 1) % len(self.values)]


def _err(code: str, latest: Optional[BaseModel] = None) -> ports.TransportError:
    return ports.TransportError(code, latest=latest.model_dump(mode="json") if latest else None)


class FakeServe:
    """A team server in memory. Authorization is by the transport's own
    (github_id, node_id), never by anything in a request."""

    def __init__(self, *, server_id: str, members: set[int], clock: FixedClock,
                 faults: wire.Faults = wire.NO_FAULTS):
        self.server_id, self.members, self.clock, self.faults = server_id, set(members), clock, faults
        self.nodes: dict[UUID, int] = {}                 # node -> owner
        self.messages: dict[UUID, wire.AcceptedMessage] = {}
        self.receipts: dict[UUID, wire.ReceiptFacts] = {}
        self.work: dict[tuple[int, UUID], wire.WorkState] = {}
        self.capabilities = [wire.CAPABILITY]
        self.epoch = 0

    def pair(self, github_id: int, node_id: UUID) -> "_Transport":
        self.nodes[node_id] = github_id
        return _Transport(self, github_id, node_id)

    def visible(self, viewer: int, message_id: UUID) -> Optional[wire.AcceptedMessage]:
        m = self.messages.get(message_id)
        return m if m and viewer in (m.sender_github_id, m.recipient_github_id) else None

    def accept(self, sender: int, node_id: UUID, req: wire.SendRequest) -> wire.SendResult:
        if self.nodes.get(node_id) != sender or req.source_node_id != node_id:
            raise _err("source_node_not_owned")
        if req.recipient_github_id not in self.members:
            raise _err("recipient_not_allowed")
        content = wire.content_of(req)
        if req.content_digest != wire.content_digest(content, server_id=self.server_id,
                                                     sender_github_id=sender):
            raise _err("digest_mismatch")
        earlier = next((m for m in self.messages.values()
                        if m.sender_github_id == sender and m.idempotency_key == req.idempotency_key),
                       None) or self.messages.get(req.message_id)
        if earlier is not None:
            if (earlier.content_digest, earlier.idempotency_key) != (req.content_digest,
                                                                      req.idempotency_key):
                raise _err("idempotency_conflict")
            return wire.SendResult(message=earlier, duplicate=True)
        pair = {sender, req.recipient_github_id}
        if req.in_reply_to_message_id is not None:
            parent = self.visible(sender, req.in_reply_to_message_id)
            if (parent is None or parent.thread_id != req.thread_id
                    or {parent.sender_github_id, parent.recipient_github_id} != pair):
                raise _err("relation_not_allowed")
        for related in req.related_message_ids:
            m = self.messages.get(related)
            if m is None or not {m.sender_github_id, m.recipient_github_id} <= pair:
                raise _err("relation_not_allowed")
        accepted = wire.AcceptedMessage(**req.model_dump(), server_id=self.server_id,
                                        sender_github_id=sender, accepted_at=self.clock())
        self.messages[accepted.message_id] = accepted
        self.receipts[accepted.message_id] = wire.ReceiptFacts(
            message_id=accepted.message_id, recipient_github_id=accepted.recipient_github_id,
            revision=1)
        for owner in sorted(pair):
            key = (owner, accepted.thread_id)
            self.work[key] = (wire.on_message(self.work[key], accepted) if key in self.work
                              else wire.new_work_state(server_id=self.server_id,
                                                       owner_github_id=owner, message=accepted))
        self.faults.check("server_after_commit_before_response")
        return wire.SendResult(message=accepted, duplicate=False)

    def scan(self, owner: int, continuation: Optional[str], limit: int) -> wire.SyncPage:
        if continuation is None:
            self.epoch += 1
            epoch, start = f"e{self.epoch}", 0
        else:
            epoch, start = continuation.split(":")[0], int(continuation.split(":")[1])
        mine = sorted((m for m in self.messages.values()
                       if owner in (m.sender_github_id, m.recipient_github_id)),
                      key=lambda m: (m.accepted_at, str(m.message_id)))
        chunk = mine[start:start + limit]
        more = start + limit < len(mine)
        return wire.SyncPage(
            epoch=epoch, server_time=self.clock(), scan_complete=not more,
            continuation=f"{epoch}:{start + limit}" if more else None,
            entries=[wire.SyncEntry(message=m, receipts=self.receipts[m.message_id],
                                    work_state=self.work[(owner, m.thread_id)]) for m in chunk])


class _Transport:
    """One paired node's ServeTransport."""

    def __init__(self, serve: FakeServe, github_id: int, node_id: UUID):
        self.serve, self.github_id, self.node_id = serve, github_id, node_id

    async def capabilities(self) -> list[str]:
        return list(self.serve.capabilities)

    async def send(self, request: wire.SendRequest) -> wire.SendResult:
        return self.serve.accept(self.github_id, self.node_id, request)

    async def get(self, message_id: UUID) -> wire.AcceptedMessage:
        m = self.serve.visible(self.github_id, message_id)
        if m is None:
            raise _err("message_not_found")
        return m

    async def sync(self, continuation: Optional[str], limit: int) -> wire.SyncPage:
        return self.serve.scan(self.github_id, continuation, min(limit, wire.SYNC_PAGE_MAX))

    async def receipt(self, message_id: UUID, request: wire.ReceiptRequest) -> wire.ReceiptFacts:
        m = self.serve.visible(self.github_id, message_id)
        if m is None or m.recipient_github_id != self.github_id:
            raise _err("message_not_found")
        if self.serve.nodes.get(request.node_id) != self.github_id:
            raise _err("source_node_not_owned")
        facts = wire.merge_receipt(self.serve.receipts[message_id], request.kind,
                                   request.node_id, self.serve.clock())
        self.serve.receipts[message_id] = facts
        return facts

    async def change_work_state(self, thread_id: UUID,
                                request: wire.WorkCommandRequest) -> wire.WorkState:
        key = (self.github_id, thread_id)
        if key not in self.serve.work:
            raise _err("message_not_found")
        try:
            self.serve.work[key] = wire.apply_work_command(self.serve.work[key], request.change,
                                                           self.serve.clock())
        except wire.ContractError as e:
            raise _err(e.code, e.latest) from None
        return self.serve.work[key]


class MemoryStore:
    """A LocalMessagingStore in dicts, namespaced by (server, owner)."""

    def __init__(self):
        self.drafts: dict = {}
        self.sends: dict = {}
        self.incoming: dict = {}
        self.owed: dict = {}
        self.positions: dict = {}

    def save_draft(self, server_id, owner_github_id, draft):
        self.drafts[(server_id, owner_github_id, draft.message_id)] = draft
        return draft

    def draft(self, server_id, owner_github_id, message_id):
        return self.drafts.get((server_id, owner_github_id, message_id))

    def save_approved(self, server_id, owner_github_id, request):
        send = ports.LocalSend(message_id=request.message_id,
                               content_revision=request.content_revision, state="approved",
                               content_digest=request.content_digest)
        self.sends[(server_id, owner_github_id, request.message_id)] = (request, send)
        return send

    def set_send(self, server_id, owner_github_id, send):
        request, _ = self.sends[(server_id, owner_github_id, send.message_id)]
        self.sends[(server_id, owner_github_id, send.message_id)] = (request, send)

    def unresolved_sends(self, server_id, owner_github_id):
        return [s for (sv, o, _), (_, s) in self.sends.items()
                if (sv, o) == (server_id, owner_github_id) and s.state in ports.SEND_UNRESOLVED]

    def store_incoming(self, server_id, owner_github_id, entry, receipt_owed):
        key = (server_id, owner_github_id, entry.message.message_id)
        new = key not in self.incoming
        old = self.incoming.get(key)
        if old is None or entry.receipts.revision >= old.receipts.revision:
            self.incoming[key] = entry
        if new and receipt_owed:
            self.owed[key] = True
        return new

    def receipts_owed(self, server_id, owner_github_id):
        return [m for (sv, o, m) in self.owed if (sv, o) == (server_id, owner_github_id)]

    def sync_position(self, server_id, owner_github_id, node_id):
        return self.positions.get((server_id, owner_github_id, node_id))

    def set_sync_position(self, server_id, owner_github_id, node_id, continuation):
        self.positions[(server_id, owner_github_id, node_id)] = continuation

    def snapshot(self, server_id, owner_github_id):
        return []


class TableGuard:
    def __init__(self):
        self.approved: dict[tuple[UUID, int], ports.ApprovedSend] = {}

    def require_approved_revision(self, message_id, content_revision):
        found = self.approved.get((message_id, content_revision))
        if found is None:
            raise ports.NotApproved(str(message_id))
        return found


class StaticSnapshots:
    def __init__(self, table=None):
        self.table = table or {}

    def verified_attachment_manifest(self, message_id, content_revision):
        return list(self.table.get((message_id, content_revision), []))


class RecordingPresentation:
    def __init__(self):
        self.presented: list[ports.TransferEvent] = []
        self.leases: dict[str, UUID] = {}
        self.completed: list[str] = []

    def present(self, event):
        self.presented.append(event)

    def reserve_announcement(self, event_id, node_id):
        if event_id in self.leases:
            return None
        self.leases[event_id] = node_id
        return event_id

    def complete_announcement(self, lease):
        self.completed.append(lease)
