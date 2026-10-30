"""Test adapters for human messaging: in-memory implementations of every
interface in `jarvis/messaging_ports.py`, built on the contract's own rules
(`jarvis/messaging_wire.py`), so a worker's service runs against them
exactly as it will against the real ones. Nothing here is reachable from
the application.

    FixedClock, SeqIds, SeqRandom   time, ids and jitter that tests move
    FakeServe                       one team server: roster, messages,
                                    receipts, work state, sync scans,
                                    announcement leases, attachment staging
                                    and the fault points the server owns;
                                    `.pair(github_id, node_id)` is one
                                    paired node's ServeTransport
    MemoryStore                     a LocalMessagingStore in dicts
    TableGuard                      an ApprovalGuard over a MemoryStore. A
                                    stand-in for unit tests only: integration
                                    tests use the real guard
    StaticSnapshots                 AttachmentSnapshots from a table
    RecordingPresentation           a PresentationDispatcher over the fake
                                    team server's leases
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import UUID

from pydantic import BaseModel

from jarvis import messaging_ports as ports
from jarvis import messaging_wire as wire

T0 = datetime(2026, 10, 29, 12, 0, tzinfo=timezone.utc)
LEASE_SEC = 30.0


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
    return ports.TransportError(code, status=wire.ERRORS[code].status,
                                latest=latest.model_dump(mode="json") if latest else None)


def _higher(old, new):
    """Whichever record has the higher revision (the stored one on a tie)."""
    return new if old is None or new.revision > old.revision else old


class FakeServe:
    """A team server in memory. Authorization is by the transport's own
    (github_id, node_id), never by anything in a request."""

    def __init__(self, *, server_id: str, members: set[int], clock: FixedClock,
                 faults: wire.Faults = wire.NO_FAULTS, ids: Optional[SeqIds] = None):
        self.server_id, self.members, self.clock, self.faults = server_id, set(members), clock, faults
        self.ids = ids or SeqIds(start=0xF000)
        self.nodes: dict[UUID, int] = {}                 # node -> owner, bound for good
        self.messages: dict[UUID, wire.AcceptedMessage] = {}
        self.receipts: dict[UUID, wire.ReceiptFacts] = {}
        self.work: dict[tuple[int, UUID], wire.WorkState] = {}
        self.applied: dict[tuple[int, UUID], set[UUID]] = {}
        self.leases: dict[UUID, wire.AnnouncementLease] = {}
        self.staging: dict[UUID, dict] = {}
        self.capabilities = [wire.CAPABILITY]
        self.epoch = 0

    def pair(self, github_id: int, node_id: UUID) -> "_Transport":
        self.nodes[node_id] = github_id
        return _Transport(self, github_id, node_id)

    def owns(self, github_id: int, node_id: UUID) -> None:
        if self.nodes.get(node_id) != github_id:
            raise _err("source_node_not_owned")

    def visible(self, viewer: int, message_id: UUID) -> Optional[wire.AcceptedMessage]:
        m = self.messages.get(message_id)
        return m if m and viewer in (m.sender_github_id, m.recipient_github_id) else None

    # -- acceptance, in the contract's order ------------------------------------

    def accept(self, sender: int, node_id: UUID, req: wire.SendRequest) -> wire.SendResult:
        self.owns(sender, node_id)
        if req.source_node_id != node_id:
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
            if (earlier.sender_github_id, earlier.content_digest, earlier.idempotency_key) != \
                    (sender, req.content_digest, req.idempotency_key):
                raise _err("idempotency_conflict")
            self.derive(earlier)                      # repair, as a retry would
            return self._result(sender, earlier, duplicate=True)
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
        for ref in req.attachments:
            if not self.pin(sender, ref, req.message_id, req.content_digest):
                raise _err("attachment_not_ready")
        self.faults.check("server_after_pin_before_insert")
        accepted = wire.AcceptedMessage(**req.model_dump(), server_id=self.server_id,
                                        sender_github_id=sender, accepted_at=self.clock())
        self.messages[accepted.message_id] = accepted
        self.faults.check("server_after_insert_before_projection")
        self.derive(accepted)
        self.faults.check("server_after_commit_before_response")
        for ref in req.attachments:
            self.staging[ref.attachment_id]["state"] = "published"
        return self._result(sender, accepted, duplicate=False)

    def derive(self, m: wire.AcceptedMessage) -> None:
        """Receipts and both items, idempotently: each item applies a
        message once, in (accepted_at, message_id) order."""
        self.receipts.setdefault(m.message_id, wire.ReceiptFacts(
            message_id=m.message_id, recipient_github_id=m.recipient_github_id, revision=1))
        for owner in sorted({m.sender_github_id, m.recipient_github_id}):
            key = (owner, m.thread_id)
            applied = self.applied.setdefault(key, set())
            if m.message_id in applied:
                continue
            applied.add(m.message_id)
            self.work[key] = (wire.on_message(self.work[key], m) if key in self.work
                              else wire.new_work_state(server_id=self.server_id,
                                                       owner_github_id=owner, message=m))

    def _result(self, sender, m, *, duplicate) -> wire.SendResult:
        return wire.SendResult(message=m, receipts=self.receipts[m.message_id],
                               work_state=self.work[(sender, m.thread_id)], duplicate=duplicate)

    # -- attachments ------------------------------------------------------------

    def pin(self, sender: int, ref: wire.AttachmentRef, message_id: UUID, digest: str) -> bool:
        rec = self.staging.get(ref.attachment_id)
        if (rec is None or rec["owner"] != sender or rec["message_id"] != message_id
                or (rec["size_bytes"], rec["sha256"]) != (ref.size_bytes, ref.sha256)
                or rec["state"] not in ("verified", "pinned")):
            return False
        rec.update(state="pinned", pinned_digest=digest, expires_at=None)
        return True

    def status(self, rec: dict) -> wire.AttachmentStatus:
        return wire.AttachmentStatus(**{k: rec[k] for k in (
            "attachment_id", "message_id", "state", "size_bytes", "sha256", "received_bytes",
            "expires_at")})

    def staged(self, owner: int, attachment_id: UUID) -> dict:
        rec = self.staging.get(attachment_id)
        if rec is None or rec["owner"] != owner:
            raise _err("attachment_not_found")
        return rec

    # -- sync -------------------------------------------------------------------

    def scan(self, owner: int, node_id: UUID, continuation: Optional[str],
             limit: int) -> wire.SyncPage:
        self.owns(owner, node_id)
        if continuation is None:
            self.epoch += 1
            epoch, start = f"e{self.epoch}", 0
        else:
            epoch, node, start = continuation.split(":")
            if node != str(node_id):
                raise _err("sync_restart")
            start = int(start)
        mine = sorted((m for m in self.messages.values()
                       if owner in (m.sender_github_id, m.recipient_github_id)),
                      key=lambda m: (m.accepted_at, str(m.message_id)))
        chunk = mine[start:start + limit]
        more = start + limit < len(mine)
        return wire.SyncPage(
            epoch=epoch, server_time=self.clock(), scan_complete=not more,
            continuation=f"{epoch}:{node_id}:{start + limit}" if more else None,
            entries=[wire.SyncEntry(message=m, receipts=self.receipts[m.message_id],
                                    work_state=self.work[(owner, m.thread_id)]) for m in chunk])


class _Transport:
    """One paired node's ServeTransport."""

    def __init__(self, serve: FakeServe, github_id: int, node_id: UUID):
        self.serve, self.github_id, self.node_id = serve, github_id, node_id

    async def capabilities(self) -> list[str]:
        return list(self.serve.capabilities)

    async def recipients(self) -> wire.RecipientList:
        return wire.RecipientList(members=[wire.TeamMember(github_id=m, login=f"member{m}")
                                           for m in sorted(self.serve.members)])

    async def send(self, request: wire.SendRequest) -> wire.SendResult:
        return self.serve.accept(self.github_id, self.node_id, request)

    async def get(self, message_id: UUID) -> wire.AcceptedMessage:
        m = self.serve.visible(self.github_id, message_id)
        if m is None:
            raise _err("message_not_found")
        return m

    async def sync(self, node_id: UUID, continuation: Optional[str],
                   limit: int) -> wire.SyncPage:
        return self.serve.scan(self.github_id, node_id, continuation,
                               min(limit, wire.SYNC_PAGE_MAX))

    async def receipts(self, batch: wire.ReceiptBatch) -> wire.ReceiptBatchResult:
        self.serve.owns(self.github_id, batch.node_id)
        facts, not_found = [], []
        for item in batch.receipts:
            m = self.serve.visible(self.github_id, item.message_id)
            if m is None or m.recipient_github_id != self.github_id:
                not_found.append(item.message_id)
                continue
            merged = wire.merge_receipt(self.serve.receipts[m.message_id], item.kind,
                                        batch.node_id, self.serve.clock())
            self.serve.receipts[m.message_id] = merged
            facts.append(merged)
        return wire.ReceiptBatchResult(facts=facts, not_found=not_found)

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

    async def announce(self, message_id: UUID,
                       request: wire.AnnouncementRequest) -> wire.AnnouncementLease:
        m = self.serve.visible(self.github_id, message_id)
        if m is None or m.recipient_github_id != self.github_id:
            raise _err("message_not_found")
        self.serve.owns(self.github_id, request.node_id)
        try:
            lease = wire.apply_announcement(
                self.serve.leases.get(message_id), request, message_id=message_id,
                owner_github_id=self.github_id, now=self.serve.clock(),
                new_token=self.serve.ids.new_id(), lease_sec=LEASE_SEC)
        except wire.ContractError as e:
            raise _err(e.code, e.latest) from None
        self.serve.leases[message_id] = lease
        return lease

    async def create_attachment(self, request: wire.AttachmentCreate) -> wire.AttachmentStatus:
        rec = self.serve.staging.get(request.attachment_id)
        binding = dict(owner=self.github_id, message_id=request.message_id,
                       size_bytes=request.size_bytes, sha256=request.sha256)
        if rec is None:
            rec = self.serve.staging[request.attachment_id] = dict(
                binding, attachment_id=request.attachment_id, state="uploading",
                received_bytes=0, bytes=b"", pinned_digest=None,
                expires_at=self.serve.clock() + timedelta(seconds=wire.STAGING_EXPIRY_SEC))
        elif any(rec[k] != v for k, v in binding.items()):
            raise _err("idempotency_conflict")
        return self.serve.status(rec)

    async def upload_attachment(self, attachment_id: UUID, chunks) -> wire.AttachmentStatus:
        rec = self.serve.staged(self.github_id, attachment_id)
        data = b""
        async for chunk in chunks:
            data += chunk
            if len(data) > wire.FILE_MAX_BYTES:
                raise _err("too_large")
        if rec["state"] == "uploading":
            rec.update(bytes=data, received_bytes=len(data))
        return self.serve.status(rec)

    async def finalize_attachment(self, attachment_id: UUID) -> wire.AttachmentStatus:
        rec = self.serve.staged(self.github_id, attachment_id)
        self.serve.faults.check("server_before_attachment_finalize")
        if rec["state"] == "uploading":
            if (len(rec["bytes"]), hashlib.sha256(rec["bytes"]).hexdigest()) != \
                    (rec["size_bytes"], rec["sha256"]):
                raise _err("attachment_mismatch")
            rec["state"] = "verified"
        self.serve.faults.check("server_after_attachment_finalize")
        return self.serve.status(rec)

    async def attachment_status(self, attachment_id: UUID) -> wire.AttachmentStatus:
        return self.serve.status(self.serve.staged(self.github_id, attachment_id))

    async def download_attachment(self, message_id: UUID, attachment_id: UUID):
        m = self.serve.visible(self.github_id, message_id)
        if m is None or attachment_id not in {a.attachment_id for a in m.attachments}:
            raise _err("attachment_not_found")
        yield self.serve.staging[attachment_id]["bytes"]


class MemoryStore:
    """A LocalMessagingStore in dicts, namespaced by (server, owner). Every
    mutable record keeps the higher revision: receipts per message, work
    state per thread."""

    def __init__(self):
        self.drafts: dict = {}
        self.approved_sends: dict = {}
        self.sends: dict = {}
        self.messages: dict = {}
        self.receipt_facts: dict = {}
        self.work: dict = {}
        self.incoming: set = set()
        self.owed: dict = {}
        self.positions: dict = {}
        self.commands: dict = {}
        self.announcements: dict = {}

    # drafts
    def create_draft(self, server_id, owner_github_id, draft):
        self.drafts[(server_id, owner_github_id, draft.message_id)] = draft
        return draft

    def _current(self, server_id, owner_github_id, message_id, expected):
        current = self.drafts.get((server_id, owner_github_id, message_id))
        if current is None:
            raise wire.ContractError("draft_not_found")
        if current.draft_revision != expected:
            raise wire.ContractError("revision_conflict", latest=current)
        return current

    def save_draft(self, server_id, owner_github_id, draft, expected_draft_revision):
        self._current(server_id, owner_github_id, draft.message_id, expected_draft_revision)
        saved = draft.model_copy(update={"draft_revision": expected_draft_revision + 1})
        self.drafts[(server_id, owner_github_id, draft.message_id)] = saved
        return saved

    def draft(self, server_id, owner_github_id, message_id):
        return self.drafts.get((server_id, owner_github_id, message_id))

    def discard_draft(self, server_id, owner_github_id, message_id, expected_draft_revision):
        self._current(server_id, owner_github_id, message_id, expected_draft_revision)
        del self.drafts[(server_id, owner_github_id, message_id)]

    # approval and sending
    def save_approved(self, approved):
        req = approved.request
        ns = (approved.server_id, approved.owner_github_id)
        self.approved_sends[(*ns, req.message_id, req.content_revision)] = approved
        send = ports.LocalSend(message_id=req.message_id, content_revision=req.content_revision,
                               state="approved", content_digest=req.content_digest)
        self.set_send(*ns, send)
        return send

    def approved(self, server_id, owner_github_id, message_id, content_revision):
        return self.approved_sends.get((server_id, owner_github_id, message_id, content_revision))

    def set_send(self, server_id, owner_github_id, send):
        self.sends[(server_id, owner_github_id, send.message_id)] = send
        draft = self.draft(server_id, owner_github_id, send.message_id)
        if draft is not None and draft.send.content_revision == send.content_revision:
            self.drafts[(server_id, owner_github_id, send.message_id)] = \
                draft.model_copy(update={"send": send})

    def unresolved_sends(self, server_id, owner_github_id):
        return [s for (sv, o, _), s in self.sends.items()
                if (sv, o) == (server_id, owner_github_id) and s.state in ports.SEND_UNRESOLVED]

    def record_accepted(self, server_id, owner_github_id, result):
        m = result.message
        self._keep(server_id, owner_github_id, m, result.receipts, result.work_state)
        sent = self.sends.get((server_id, owner_github_id, m.message_id))
        if sent is not None:
            self.set_send(server_id, owner_github_id, sent.model_copy(update={"state": "accepted"}))
        return m.recipient_github_id == owner_github_id and \
            self._first_incoming(server_id, owner_github_id, m.message_id)

    # incoming and sync
    def _keep(self, server_id, owner_github_id, m, receipts, work_state):
        ns = (server_id, owner_github_id)
        self.messages[(*ns, m.message_id)] = m
        self.receipt_facts[(*ns, m.message_id)] = _higher(
            self.receipt_facts.get((*ns, m.message_id)), receipts)
        self.save_work_state(*ns, work_state)

    def _first_incoming(self, server_id, owner_github_id, message_id) -> bool:
        key = (server_id, owner_github_id, message_id)
        if key in self.incoming:
            return False
        self.incoming.add(key)
        self.owe_receipt(server_id, owner_github_id, message_id, "received")
        self.announcements[key] = "due"
        return True

    def store_page(self, server_id, owner_github_id, node_id, page):
        new = []
        for e in page.entries:
            self._keep(server_id, owner_github_id, e.message, e.receipts, e.work_state)
            if e.message.recipient_github_id == owner_github_id and \
                    self._first_incoming(server_id, owner_github_id, e.message.message_id):
                new.append(e.message.message_id)
        self.positions[(server_id, owner_github_id, node_id)] = page.continuation
        return new

    def sync_position(self, server_id, owner_github_id, node_id):
        return self.positions.get((server_id, owner_github_id, node_id))

    def owe_receipt(self, server_id, owner_github_id, message_id, kind):
        self.owed[(server_id, owner_github_id, message_id, kind)] = True

    def receipts_owed(self, server_id, owner_github_id):
        return [ports.OwedReceipt(message_id=m, kind=k) for (sv, o, m, k) in self.owed
                if (sv, o) == (server_id, owner_github_id)]

    def settle_receipts(self, server_id, owner_github_id, sent, result):
        for facts in result.facts:
            key = (server_id, owner_github_id, facts.message_id)
            self.receipt_facts[key] = _higher(self.receipt_facts.get(key), facts)
        for owed in sent:
            self.owed.pop((server_id, owner_github_id, owed.message_id, owed.kind), None)

    # work state
    def save_work_state(self, server_id, owner_github_id, state):
        key = (server_id, owner_github_id, state.thread_id)
        self.work[key] = _higher(self.work.get(key), state)
        return self.work[key]

    def queue_command(self, server_id, owner_github_id, thread_id, request):
        self.commands[(server_id, owner_github_id, thread_id)] = request

    def pending_commands(self, server_id, owner_github_id):
        return [(t, r) for (sv, o, t), r in self.commands.items()
                if (sv, o) == (server_id, owner_github_id)]

    def settle_command(self, server_id, owner_github_id, thread_id):
        self.commands.pop((server_id, owner_github_id, thread_id), None)

    # announcements
    def announcements_due(self, server_id, owner_github_id):
        return [m for (sv, o, m), state in self.announcements.items()
                if (sv, o) == (server_id, owner_github_id) and state == "due"]

    def set_announcement(self, server_id, owner_github_id, message_id, state):
        self.announcements[(server_id, owner_github_id, message_id)] = state

    # reads
    def message(self, server_id, owner_github_id, message_id):
        m = self.messages.get((server_id, owner_github_id, message_id))
        if m is None:
            return None
        outgoing = m.sender_github_id == owner_github_id
        return ports.LocalMessage(
            message=m, direction="outgoing" if outgoing else "incoming",
            self_message=outgoing and m.recipient_github_id == owner_github_id,
            receipts=self.receipt_facts[(server_id, owner_github_id, message_id)])

    def item(self, server_id, owner_github_id, thread_id):
        ns = (server_id, owner_github_id)
        state = self.work.get((*ns, thread_id))
        if state is None:
            return None
        messages = sorted((self.message(*ns, m.message_id)
                           for (sv, o, _), m in self.messages.items()
                           if (sv, o) == ns and m.thread_id == thread_id),
                          key=lambda lm: (lm.message.accepted_at, str(lm.message.message_id)))
        drafts = [d for (sv, o, _), d in self.drafts.items()
                  if (sv, o) == ns and d.thread_id == thread_id and d.send.state != "accepted"]
        editable = [d for d in drafts if d.send.state in ("draft", "awaiting_approval")]
        return ports.LocalItem(
            work_state=state, messages=messages, drafts=drafts,
            pending_command=self.commands.get((*ns, thread_id)),
            dismiss_blockers=ports.dismiss_blockers(
                unsent_text=any(d.text or d.attachments for d in editable),
                send_states=[d.send.state for d in drafts],
                uploads_pending=any(ports.uploads_pending(d.files) for d in drafts)))

    def _items(self, server_id, owner_github_id, inactive: bool):
        states = sorted((w for (sv, o, _), w in self.work.items()
                         if (sv, o) == (server_id, owner_github_id)
                         and (w.placement == "inactive") == inactive),
                        key=wire.item_order_key, reverse=True)
        return [self.item(server_id, owner_github_id, w.thread_id) for w in states]

    def snapshot(self, server_id, owner_github_id, *, availability, stale):
        ns = (server_id, owner_github_id)
        unsent = [d for (sv, o, _), d in self.drafts.items()
                  if (sv, o) == ns and d.thread_id == d.message_id
                  and (*ns, d.thread_id) not in self.work]
        return ports.QueueSnapshot(
            server_id=server_id, owner_github_id=owner_github_id, availability=availability,
            items=self._items(*ns, inactive=False), unsent=unsent,
            history_count=len(self._items(*ns, inactive=True)), stale=stale)

    def history(self, server_id, owner_github_id, continuation, limit):
        items = self._items(server_id, owner_github_id, inactive=True)
        start = int(continuation or 0)
        end = start + min(limit, ports.HISTORY_PAGE_MAX)
        return ports.HistoryPage(items=items[start:end],
                                 continuation=str(end) if end < len(items) else None)


class TableGuard:
    """An ApprovalGuard over a MemoryStore, for unit tests of the services
    that call it. Not the production guard."""

    def __init__(self, store: MemoryStore, *, ids: SeqIds, clock: FixedClock):
        self.store, self.ids, self.clock = store, ids, clock

    def request_review(self, server_id, owner_github_id, message_id, expected_draft_revision):
        draft = self.store.draft(server_id, owner_github_id, message_id)
        if draft is None:
            raise wire.ContractError("draft_not_found")
        if draft.draft_revision != expected_draft_revision:
            raise wire.ContractError("revision_conflict", latest=draft)
        digest = wire.content_digest(ports.draft_content(draft), server_id=server_id,
                                     sender_github_id=owner_github_id)
        send = draft.send.model_copy(update={
            "state": ports.next_send_state(draft.send.state, "review"), "content_digest": digest})
        self.store.set_send(server_id, owner_github_id, send)
        return self.store.draft(server_id, owner_github_id, message_id)

    def approve(self, server_id, owner_github_id, message_id, content_revision, content_digest,
                source_node_id, via):
        done = self.store.approved(server_id, owner_github_id, message_id, content_revision)
        if done is not None:
            return done
        draft = self.store.draft(server_id, owner_github_id, message_id)
        if (draft is None or draft.send.content_revision != content_revision
                or draft.send.content_digest != content_digest):
            raise ports.NotApproved(str(message_id))
        ports.next_send_state(draft.send.state, "approve")
        request = wire.SendRequest(**ports.draft_content(draft).model_dump(),
                                   source_node_id=source_node_id,
                                   idempotency_key=self.ids.new_id(),
                                   content_digest=content_digest)
        approved = ports.ApprovedSend(server_id=server_id, owner_github_id=owner_github_id,
                                      request=request, via=via, approved_at=self.clock())
        self.store.save_approved(approved)
        return approved

    def require_approved_revision(self, server_id, owner_github_id, message_id,
                                  content_revision):
        found = self.store.approved(server_id, owner_github_id, message_id, content_revision)
        if found is None:
            raise ports.NotApproved(str(message_id))
        return found


class StaticSnapshots:
    """AttachmentSnapshots from a table {(message_id, content_revision): [AttachmentRef]};
    `missing` holds attachment ids whose private copy is gone."""

    def __init__(self, table=None, ids: Optional[SeqIds] = None):
        self.table = table or {}
        self.missing: set[UUID] = set()
        self.bytes: dict[UUID, bytes] = {}
        self.revealed: list[UUID] = []
        self.ids = ids or SeqIds(start=0xA000)

    async def snapshot(self, server_id, owner_github_id, message_id, filename, media_type,
                       chunks):
        data = b"".join([c async for c in chunks])
        ref = wire.AttachmentRef(attachment_id=self.ids.new_id(),
                                 filename=wire.normalize_filename(filename),
                                 media_type=wire.normalize_media_type(media_type),
                                 size_bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
        self.bytes[ref.attachment_id] = data
        return ref

    def verified_attachment_manifest(self, server_id, owner_github_id, message_id,
                                     content_revision):
        refs = list(self.table.get((message_id, content_revision), []))
        for ref in refs:
            if ref.attachment_id in self.missing:
                raise ports.SnapshotMissing(ref.attachment_id)
        return refs

    def refs(self, server_id, owner_github_id, message_id, attachment_ids):
        known = {r.attachment_id: r for (m, _), refs in self.table.items() if m == message_id
                 for r in refs}
        for attachment_id in attachment_ids:
            if attachment_id not in known or attachment_id in self.missing:
                raise ports.SnapshotMissing(attachment_id)
        return [known[a] for a in attachment_ids]

    def cleanup(self, server_id, owner_github_id):
        return 0

    async def ensure_uploaded(self, server_id, owner_github_id, message_id, content_revision):
        return self.verified_attachment_manifest(server_id, owner_github_id, message_id,
                                                 content_revision)

    def files(self, server_id, owner_github_id, message_id):
        return [ports.LocalFile(attachment_id=r.attachment_id,
                                state="missing" if r.attachment_id in self.missing else "staged")
                for (m, _), refs in self.table.items() if m == message_id for r in refs]

    async def download(self, server_id, owner_github_id, message_id, attachment_id):
        return ports.LocalFile(attachment_id=attachment_id, state="verified")

    def reveal(self, server_id, owner_github_id, message_id, attachment_id):
        self.revealed.append(attachment_id)


class RecordingPresentation:
    """Records what it drew and takes the fake team server's lease for this
    node, as a production dispatcher would."""

    def __init__(self, transport: "_Transport"):
        self.transport = transport
        self.presented: list[ports.TransferEvent] = []
        self.steps: list[tuple[str, str]] = []

    def present(self, event):
        self.presented.append(event)

    async def _step(self, event, step, token=None):
        return await self.transport.announce(event.message_id, wire.AnnouncementRequest(
            node_id=self.transport.node_id, step=step, lease_token=token))

    async def reserve_announcement(self, event):
        try:
            lease = await self._step(event, "claim")
        except ports.TransportError as e:
            if e.code == "announcement_claimed":
                return None
            raise
        self.steps.append((event.event_id, "claimed"))
        return lease.lease_token

    async def attempted(self, event, token):
        await self._step(event, "attempted", token)
        self.steps.append((event.event_id, "attempted"))

    async def complete_announcement(self, event, token):
        await self._step(event, "completed", token)
        self.steps.append((event.event_id, "completed"))
