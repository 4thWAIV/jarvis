"""Human messaging on the team server (capability `messaging.v1`): the routes
in `messaging_models`' table except the attachment ones (`attachments.py`),
their MongoDB repository and its indexes.

Who may do what is decided here and only here, from the authenticated
device session: the sender is the session's member, the source node must be
that member's (`bind_node`), the recipient an active member, a reply's
parent and every related message ones both participants can see. Every
read names its viewer; a message the viewer is not a participant of is
`message_not_found`, for an administrator too.

Acceptance, crash-safe without a multi-document transaction (the fault
points name the gaps): pin every manifest file -> insert the one
authoritative message document (the visibility commit) -> derive the
receipts record and both participants' work items, idempotently -> answer
-> mark the files published. A crash anywhere leaves either nothing visible
or an accepted message whose projections a retry, a sync or a receipt
repairs (`derive_projections` applies each message to an item once, in
(accepted_at, message_id) order).

Collections (all additive; nothing here deletes a message):
  messages               one accepted message per document, `_id` its id;
                         `participants` the deduplicated pair, `body` the
                         AcceptedMessage exactly as it was answered
  message_receipts       the recipient's facts per message, `_id` its id
  message_work           one owner's item per thread, with the message ids
                         it has applied
  message_announcements  the announcement lease per message, `_id` its id
  messaging_nodes        node id -> the member who first used it, for good
                         (the network record is purged; this is not)
  message_sync           sync continuations, by SHA-256 of the token,
                         purged after they expire
"""
from __future__ import annotations

import json
import logging
import secrets
import uuid
from datetime import datetime, timedelta
from typing import Callable, Optional
from uuid import UUID

from fastapi import Depends, FastAPI, Request
from pydantic import ValidationError
from pymongo import ASCENDING
from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import DuplicateKeyError

from teamserver import clock as clock_mod
from teamserver import sessions
from teamserver import team as team_mod
from teamserver.errors import Problem
from teamserver.messaging_models import (CAPABILITY, MESSAGE_REQUEST_MAX_BYTES, NO_FAULTS,
                                         SYNC_CONTINUATION_TTL_SEC, SYNC_PAGE_DEFAULT,
                                         SYNC_PAGE_MAX, ERRORS, AcceptedMessage,
                                         AnnouncementLease, AnnouncementRequest,
                                         ContractError, Faults, ReceiptBatch,
                                         ReceiptBatchResult, ReceiptFacts, ReceiptKind,
                                         RecipientList, SendRequest, SendResult, SyncEntry,
                                         SyncPage, TeamMember, WorkCommand,
                                         WorkCommandRequest, WorkState, apply_announcement,
                                         apply_work_command, content_digest, content_of,
                                         merge_receipt, new_work_state, on_message,
                                         subject_problem, text_problem)
from teamserver.messaging_ports import AttachmentStorage, Clock, IdSource
from teamserver.models import ErrorBody
from teamserver.secrets_util import digest

log = logging.getLogger("teamserver.messaging")

# How long one node's announcement claim holds before another of the
# owner's nodes may claim it. The signals stream's setting (decision 23);
# it replaces this default through `mount(lease_sec=...)`.
ANNOUNCEMENT_LEASE_SEC = 30.0
# A compare-and-set that keeps losing to concurrent writers gives up.
CAS_RETRIES = 16
PREFIX = "/api/v1/messages"


class _UuidIds:
    def new_id(self) -> UUID:
        return uuid.uuid4()


def _ms(at: datetime) -> datetime:
    """MongoDB keeps milliseconds: an acceptance time is cut to them before
    it is answered, so the stored order and every answer agree."""
    return at.replace(microsecond=at.microsecond // 1000 * 1000)


def _refusal(e: ContractError) -> Problem:
    spec = ERRORS[e.code]
    extra = {"latest": e.latest.model_dump(mode="json")} if e.latest is not None else {}
    return Problem(spec.status, e.code, spec.shown, **extra)


def refused(code: str) -> Problem:
    return _refusal(ContractError(code))


# -- indexes ------------------------------------------------------------------

async def ensure_indexes(db: AsyncDatabase) -> None:
    """Idempotent, additive: creates what is missing and drops nothing."""
    await db.messages.create_index(
        [("server_id", ASCENDING), ("sender_github_id", ASCENDING),
         ("idempotency_key", ASCENDING)], unique=True, name="sender_idempotency_unique")
    await db.messages.create_index(
        [("server_id", ASCENDING), ("participants", ASCENDING), ("accepted_at", ASCENDING),
         ("_id", ASCENDING)], name="by_participant_arrival")
    await db.messages.create_index(
        [("thread_id", ASCENDING), ("accepted_at", ASCENDING), ("_id", ASCENDING)],
        name="by_thread_arrival")
    await db.messages.create_index(
        [("in_reply_to_message_id", ASCENDING)], name="by_parent",
        partialFilterExpression={"in_reply_to_message_id": {"$type": "string"}})
    await db.messages.create_index([("attachment_ids", ASCENDING)], name="by_attachment")
    await db.message_receipts.create_index([("recipient_github_id", ASCENDING)],
                                           name="by_recipient")
    await db.message_work.create_index(
        [("server_id", ASCENDING), ("owner_github_id", ASCENDING), ("thread_id", ASCENDING)],
        unique=True, name="owner_thread_unique")
    await db.messaging_nodes.create_index([("owner_github_id", ASCENDING)], name="by_owner")
    await db.message_sync.create_index([("purge_at", ASCENDING)], expireAfterSeconds=0,
                                       name="ttl_purge")


def capabilities(enabled: bool) -> list[str]:
    """What /version adds to its `capabilities`: `messaging.v1` only while
    the switch is on."""
    return [CAPABILITY] if enabled else []


# -- the repository -------------------------------------------------------------

def _work_id(owner: int, thread_id: UUID) -> str:
    return f"{owner}:{thread_id}"


class MongoMessages:
    """`MessageRepository` over MongoDB, majority writes (the database's
    write concern). Every compare-and-set conditions on the revision it
    read; a lost race reads again."""

    def __init__(self, db: AsyncDatabase):
        self.db = db

    # -- nodes -------------------------------------------------------------------

    async def bind_node(self, server_id: str, github_id: int, node_id: UUID) -> bool:
        key = str(node_id)
        bound = await self.db.messaging_nodes.find_one({"_id": key})
        if bound is None:
            published = await self.db.network_nodes.find_one({"_id": key})
            if published is None or published.get("owner_github_id") != github_id:
                return False
            try:
                await self.db.messaging_nodes.insert_one({
                    "_id": key, "server_id": server_id, "owner_github_id": github_id,
                    "bound_at": clock_mod.now()})
                return True
            except DuplicateKeyError:
                bound = await self.db.messaging_nodes.find_one({"_id": key})
        return (bound["server_id"], bound["owner_github_id"]) == (server_id, github_id)

    # -- messages ----------------------------------------------------------------

    @staticmethod
    def _message(doc: Optional[dict]) -> Optional[AcceptedMessage]:
        return None if doc is None else AcceptedMessage.model_validate(doc["body"])

    async def find_by_idempotency(self, server_id: str, sender_github_id: int,
                                  idempotency_key: UUID) -> Optional[AcceptedMessage]:
        return self._message(await self.db.messages.find_one({
            "server_id": server_id, "sender_github_id": sender_github_id,
            "idempotency_key": str(idempotency_key)}))

    async def get_visible(self, server_id: str, viewer_github_id: int,
                          message_id: UUID) -> Optional[AcceptedMessage]:
        return self._message(await self.db.messages.find_one({
            "_id": str(message_id), "server_id": server_id,
            "participants": viewer_github_id}))

    async def insert_accepted(self, message: AcceptedMessage) -> tuple[AcceptedMessage, bool]:
        doc = {
            "_id": str(message.message_id), "server_id": message.server_id,
            "sender_github_id": message.sender_github_id,
            "recipient_github_id": message.recipient_github_id,
            "participants": sorted({message.sender_github_id, message.recipient_github_id}),
            "thread_id": str(message.thread_id),
            "in_reply_to_message_id": (str(message.in_reply_to_message_id)
                                       if message.in_reply_to_message_id else None),
            "attachment_ids": [str(a.attachment_id) for a in message.attachments],
            "idempotency_key": str(message.idempotency_key),
            "content_digest": message.content_digest,
            "accepted_at": message.accepted_at,
            "body": message.model_dump(mode="json"),
        }
        try:
            await self.db.messages.insert_one(doc)
            return message, False
        except DuplicateKeyError:
            pass
        earlier = self._message(await self.db.messages.find_one(
            {"_id": str(message.message_id)})) or await self.find_by_idempotency(
            message.server_id, message.sender_github_id, message.idempotency_key)
        same = earlier is not None and (
            earlier.message_id, earlier.sender_github_id, earlier.content_digest,
            earlier.idempotency_key) == (message.message_id, message.sender_github_id,
                                         message.content_digest, message.idempotency_key)
        if not same:
            raise ContractError("idempotency_conflict")
        return earlier, True

    async def _thread_messages(self, owner: int, thread_id: UUID,
                               upto: AcceptedMessage) -> list[AcceptedMessage]:
        docs = await self.db.messages.find({
            "server_id": upto.server_id, "thread_id": str(thread_id), "participants": owner,
            "$or": [{"accepted_at": {"$lt": upto.accepted_at}},
                    {"accepted_at": upto.accepted_at, "_id": {"$lte": str(upto.message_id)}}],
        }).sort([("accepted_at", ASCENDING), ("_id", ASCENDING)]).to_list(None)
        return [self._message(d) for d in docs]

    async def derive_projections(self, message: AcceptedMessage) -> None:
        await self.db.message_receipts.update_one(
            {"_id": str(message.message_id)},
            {"$setOnInsert": {
                "server_id": message.server_id,
                "recipient_github_id": message.recipient_github_id, "revision": 1,
                "body": ReceiptFacts(message_id=message.message_id,
                                     recipient_github_id=message.recipient_github_id,
                                     revision=1).model_dump(mode="json")}},
            upsert=True)
        for owner in sorted({message.sender_github_id, message.recipient_github_id}):
            await self._apply_to_item(owner, message)

    async def _apply_to_item(self, owner: int, message: AcceptedMessage) -> None:
        """Every message of the thread up to and including this one that the
        owner's item has not applied, in order, by compare-and-set."""
        key = _work_id(owner, message.thread_id)
        for _ in range(CAS_RETRIES):
            doc = await self.db.message_work.find_one({"_id": key})
            applied = set(doc["applied"]) if doc else set()
            if str(message.message_id) in applied:
                return
            pending = [m for m in await self._thread_messages(owner, message.thread_id, message)
                       if str(m.message_id) not in applied]
            state = WorkState.model_validate(doc["body"]) if doc else None
            for m in pending:
                state = (on_message(state, m) if state is not None else
                         new_work_state(server_id=m.server_id, owner_github_id=owner, message=m))
            fields = {"body": state.model_dump(mode="json"), "revision": state.revision,
                      "applied": sorted(applied | {str(m.message_id) for m in pending})}
            try:
                if doc is None:
                    await self.db.message_work.insert_one({
                        "_id": key, "server_id": message.server_id, "owner_github_id": owner,
                        "thread_id": str(message.thread_id), **fields})
                    return
                result = await self.db.message_work.update_one(
                    {"_id": key, "revision": doc["revision"], "applied": doc["applied"]},
                    {"$set": fields})
                if result.matched_count:
                    return
            except DuplicateKeyError:
                pass
        raise RuntimeError("work item kept changing while a message was applied")

    # -- receipts ------------------------------------------------------------------

    async def receipts(self, server_id: str, viewer_github_id: int,
                       message_id: UUID) -> Optional[ReceiptFacts]:
        if await self.get_visible(server_id, viewer_github_id, message_id) is None:
            return None
        doc = await self.db.message_receipts.find_one({"_id": str(message_id)})
        return None if doc is None else ReceiptFacts.model_validate(doc["body"])

    async def add_receipt(self, server_id: str, recipient_github_id: int, message_id: UUID,
                          kind: ReceiptKind, node_id: UUID, now: datetime) -> ReceiptFacts:
        key = str(message_id)
        for _ in range(CAS_RETRIES):
            doc = await self.db.message_receipts.find_one(
                {"_id": key, "server_id": server_id,
                 "recipient_github_id": recipient_github_id})
            if doc is None:
                raise ContractError("message_not_found")
            facts = ReceiptFacts.model_validate(doc["body"])
            merged = merge_receipt(facts, kind, node_id, _ms(now))
            if merged.revision == facts.revision:
                return facts
            result = await self.db.message_receipts.update_one(
                {"_id": key, "revision": facts.revision},
                {"$set": {"revision": merged.revision, "body": merged.model_dump(mode="json")}})
            if result.matched_count:
                return merged
        raise RuntimeError("receipt kept changing")

    # -- work state ------------------------------------------------------------------

    async def work_state(self, server_id: str, owner_github_id: int,
                         thread_id: UUID) -> Optional[WorkState]:
        doc = await self.db.message_work.find_one(
            {"_id": _work_id(owner_github_id, thread_id), "server_id": server_id})
        return None if doc is None else WorkState.model_validate(doc["body"])

    async def change_work_state(self, server_id: str, owner_github_id: int, thread_id: UUID,
                                change: WorkCommand, now: datetime) -> WorkState:
        key = _work_id(owner_github_id, thread_id)
        for _ in range(CAS_RETRIES):
            state = await self.work_state(server_id, owner_github_id, thread_id)
            if state is None:
                raise ContractError("message_not_found")
            changed = apply_work_command(state, change, _ms(now))
            if changed == state:
                return state
            result = await self.db.message_work.update_one(
                {"_id": key, "revision": state.revision},
                {"$set": {"revision": changed.revision, "body": changed.model_dump(mode="json")}})
            if result.matched_count:
                return changed
        raise RuntimeError("work item kept changing")

    # -- sync ------------------------------------------------------------------------

    async def _entry(self, owner: int, message: AcceptedMessage) -> SyncEntry:
        """One entry, repairing the message's projections first if a crash
        left them underived."""
        receipts = await self.db.message_receipts.find_one({"_id": str(message.message_id)})
        work = await self.db.message_work.find_one({"_id": _work_id(owner, message.thread_id)})
        if receipts is None or work is None or str(message.message_id) not in work["applied"]:
            await self.derive_projections(message)
            receipts = await self.db.message_receipts.find_one({"_id": str(message.message_id)})
            work = await self.db.message_work.find_one(
                {"_id": _work_id(owner, message.thread_id)})
        return SyncEntry(message=message, receipts=ReceiptFacts.model_validate(receipts["body"]),
                         work_state=WorkState.model_validate(work["body"]))

    async def sync_page(self, server_id: str, owner_github_id: int, node_id: UUID,
                        continuation: Optional[str], limit: int, now: datetime,
                        new_epoch: Optional[Callable[[], str]] = None) -> SyncPage:
        query: dict = {"server_id": server_id, "participants": owner_github_id}
        if continuation is None:
            epoch = (new_epoch or (lambda: uuid.uuid4().hex))()
        else:
            cursor = await self.db.message_sync.find_one({"_id": digest(continuation)})
            if (cursor is None or cursor["expires_at"] <= now
                    or (cursor["server_id"], cursor["owner_github_id"], cursor["node_id"])
                    != (server_id, owner_github_id, str(node_id))):
                raise ContractError("sync_restart")
            epoch = cursor["epoch"]
            at, last = cursor["last_accepted_at"], cursor["last_message_id"]
            query["$or"] = [{"accepted_at": {"$gt": at}},
                            {"accepted_at": at, "_id": {"$gt": last}}]
        docs = await self.db.messages.find(query).sort(
            [("accepted_at", ASCENDING), ("_id", ASCENDING)]).limit(limit + 1).to_list(None)
        more = len(docs) > limit
        docs = docs[:limit]
        entries = [await self._entry(owner_github_id, self._message(d)) for d in docs]
        token = None
        if more:
            token = secrets.token_urlsafe(32)
            expires = now + timedelta(seconds=SYNC_CONTINUATION_TTL_SEC)
            await self.db.message_sync.insert_one({
                "_id": digest(token), "server_id": server_id,
                "owner_github_id": owner_github_id, "node_id": str(node_id), "epoch": epoch,
                "last_accepted_at": docs[-1]["accepted_at"], "last_message_id": docs[-1]["_id"],
                "expires_at": expires, "purge_at": expires + timedelta(days=1)})
        return SyncPage(epoch=epoch, entries=entries, continuation=token,
                        scan_complete=not more, server_time=now)

    # -- announcements ------------------------------------------------------------------

    async def announce(self, server_id: str, owner_github_id: int, message_id: UUID,
                       request: AnnouncementRequest, now: datetime, new_token: UUID,
                       lease_sec: float) -> AnnouncementLease:
        message = await self.get_visible(server_id, owner_github_id, message_id)
        if message is None or message.recipient_github_id != owner_github_id:
            raise ContractError("message_not_found")
        key = str(message_id)
        for _ in range(CAS_RETRIES):
            doc = await self.db.message_announcements.find_one({"_id": key})
            lease = None if doc is None else AnnouncementLease.model_validate(doc["body"])
            after = apply_announcement(lease, request, message_id=message_id,
                                       owner_github_id=owner_github_id, now=_ms(now),
                                       new_token=new_token, lease_sec=lease_sec)
            if after == lease:
                return lease
            fields = {"revision": after.revision, "body": after.model_dump(mode="json")}
            try:
                if doc is None:
                    await self.db.message_announcements.insert_one(
                        {"_id": key, "server_id": server_id, **fields})
                    return after
                result = await self.db.message_announcements.update_one(
                    {"_id": key, "revision": lease.revision}, {"$set": fields})
                if result.matched_count:
                    return after
            except DuplicateKeyError:
                pass
        raise RuntimeError("announcement lease kept changing")


# -- the service ----------------------------------------------------------------------

def _not_normalized(raw: dict) -> bool:
    return any(isinstance(raw.get(field), str) and check(raw[field]) == "content_not_normalized"
               for field, check in (("text", text_problem), ("subject", subject_problem)))


def parse_send(raw_bytes: bytes) -> SendRequest:
    """The envelope, refused with the code that says why: a newer schema,
    content JARVIS did not normalize, or anything else malformed."""
    try:
        raw = json.loads(raw_bytes)
    except ValueError:
        raise refused("invalid_request") from None
    if not isinstance(raw, dict):
        raise refused("invalid_request")
    if raw.get("schema_version", 1) != 1:
        raise refused("unsupported_schema_version")
    if _not_normalized(raw):
        raise refused("content_not_normalized")
    try:
        return SendRequest.model_validate(raw)
    except ValidationError:
        raise refused("invalid_request") from None


class Messaging:
    """The routes' work, given the authenticated principal. Dependencies are
    injected; production gets the real clock, uuid4 ids and NO_FAULTS."""

    def __init__(self, repo: MongoMessages, *, attachments: Optional[AttachmentStorage] = None,
                 now: Clock = lambda: clock_mod.now(), ids: Optional[IdSource] = None,
                 faults: Faults = NO_FAULTS, lease_sec: float = ANNOUNCEMENT_LEASE_SEC):
        self.repo, self.attachments, self.now = repo, attachments, now
        self.ids, self.faults, self.lease_sec = ids or _UuidIds(), faults, lease_sec

    @staticmethod
    def _server(p: sessions.Principal) -> str:
        return p.team["server_id"]

    async def _own_node(self, p: sessions.Principal, node_id: UUID) -> None:
        if not await self.repo.bind_node(self._server(p), p.github_id, node_id):
            raise refused("source_node_not_owned")

    async def _result(self, sender: int, message: AcceptedMessage,
                      duplicate: bool) -> SendResult:
        receipts = await self.repo.receipts(message.server_id, sender, message.message_id)
        work = await self.repo.work_state(message.server_id, sender, message.thread_id)
        return SendResult(message=message, receipts=receipts, work_state=work,
                          duplicate=duplicate)

    async def _check_relations(self, server: str, sender: int, req: SendRequest) -> None:
        pair = {sender, req.recipient_github_id}
        if req.in_reply_to_message_id is not None:
            parent = await self.repo.get_visible(server, sender, req.in_reply_to_message_id)
            if (parent is None or parent.thread_id != req.thread_id
                    or {parent.sender_github_id, parent.recipient_github_id} != pair):
                raise refused("relation_not_allowed")
        for related in req.related_message_ids:
            m = await self.repo.get_visible(server, sender, related)
            if m is None or not {m.sender_github_id, m.recipient_github_id} <= pair:
                raise refused("relation_not_allowed")

    async def _pin(self, server: str, sender: int, req: SendRequest) -> None:
        for ref in req.attachments:
            if self.attachments is None or not await self.attachments.pin(
                    server, sender, ref, req.message_id, req.content_digest, self.now()):
                raise refused("attachment_not_ready")

    async def _published(self, server: str, message: AcceptedMessage) -> None:
        """Housekeeping after the answer is decided; a failure here never
        makes an accepted message unreadable and the next retry repeats it."""
        for ref in message.attachments:
            if self.attachments is not None:
                await self.attachments.mark_published(server, ref.attachment_id)

    async def accept(self, p: sessions.Principal, req: SendRequest) -> SendResult:
        server, sender = self._server(p), p.github_id
        await self._own_node(p, req.source_node_id)
        if req.content_digest != content_digest(content_of(req), server_id=server,
                                                sender_github_id=sender):
            raise refused("digest_mismatch")
        earlier = await self.repo.find_by_idempotency(server, sender, req.idempotency_key)
        if earlier is not None:
            if (earlier.message_id, earlier.content_digest) != (req.message_id,
                                                                req.content_digest):
                raise refused("idempotency_conflict")
            await self.repo.derive_projections(earlier)
            await self._published(server, earlier)
            return await self._result(sender, earlier, duplicate=True)
        member = p.team["members"].get(team_mod.key(req.recipient_github_id))
        if member is None or member["state"] != "active":
            raise refused("recipient_not_allowed")
        await self._check_relations(server, sender, req)
        await self._pin(server, sender, req)
        self.faults.check("server_after_pin_before_insert")
        message = AcceptedMessage(**req.model_dump(), server_id=server, sender_github_id=sender,
                                  accepted_at=_ms(self.now()))
        try:
            message, duplicate = await self.repo.insert_accepted(message)
        except ContractError as e:
            raise _refusal(e) from None
        self.faults.check("server_after_insert_before_projection")
        await self.repo.derive_projections(message)
        result = await self._result(sender, message, duplicate)
        self.faults.check("server_after_commit_before_response")
        await self._published(server, message)
        log.info("message %s accepted (duplicate=%s)", message.message_id, duplicate)
        return result

    async def get(self, p: sessions.Principal, message_id: UUID) -> AcceptedMessage:
        message = await self.repo.get_visible(self._server(p), p.github_id, message_id)
        if message is None:
            raise refused("message_not_found")
        return message

    async def sync(self, p: sessions.Principal, node_id: UUID, continuation: Optional[str],
                   limit: int) -> SyncPage:
        await self._own_node(p, node_id)
        try:
            return await self.repo.sync_page(self._server(p), p.github_id, node_id,
                                             continuation, limit, self.now(),
                                             new_epoch=lambda: self.ids.new_id().hex)
        except ContractError as e:
            raise _refusal(e) from None

    async def receipts(self, p: sessions.Principal, batch: ReceiptBatch) -> ReceiptBatchResult:
        server, me = self._server(p), p.github_id
        await self._own_node(p, batch.node_id)
        facts: dict[UUID, ReceiptFacts] = {}
        not_found: list[UUID] = []
        for item in batch.receipts:
            message = await self.repo.get_visible(server, me, item.message_id)
            if message is None or message.recipient_github_id != me:
                if item.message_id not in not_found:
                    not_found.append(item.message_id)
                continue
            await self.repo.derive_projections(message)
            facts[item.message_id] = await self.repo.add_receipt(
                server, me, item.message_id, item.kind, batch.node_id, self.now())
        return ReceiptBatchResult(facts=list(facts.values()), not_found=not_found)

    async def change_work_state(self, p: sessions.Principal, thread_id: UUID,
                                request: WorkCommandRequest) -> WorkState:
        try:
            return await self.repo.change_work_state(self._server(p), p.github_id, thread_id,
                                                     request.change, self.now())
        except ContractError as e:
            raise _refusal(e) from None

    async def announce(self, p: sessions.Principal, message_id: UUID,
                       request: AnnouncementRequest) -> AnnouncementLease:
        await self._own_node(p, request.node_id)
        try:
            return await self.repo.announce(self._server(p), p.github_id, message_id, request,
                                            self.now(), self.ids.new_id(), self.lease_sec)
        except ContractError as e:
            raise _refusal(e) from None

    @staticmethod
    def recipients(p: sessions.Principal) -> RecipientList:
        members = sorted((m for m in p.team["members"].values() if m["state"] == "active"),
                         key=lambda m: m["login"].lower())
        return RecipientList(members=[TeamMember(github_id=m["github_id"], login=m["login"],
                                                 name=m.get("name")) for m in members])


# -- the routes -----------------------------------------------------------------------

async def _bounded_body(request: Request, limit: int) -> bytes:
    """The body, counted as it is read: a Content-Length that lies, or none
    (a chunked body), cannot get more than `limit` bytes past this."""
    body = bytearray()
    async for chunk in request.stream():
        body += chunk
        if len(body) > limit:
            raise refused("too_large")
    return bytes(body)


def mount(app: FastAPI, *, device: Callable, enabled: bool,
          attachments: Optional[AttachmentStorage] = None, now: Optional[Clock] = None,
          ids: Optional[IdSource] = None, faults: Faults = NO_FAULTS,
          lease_sec: float = ANNOUNCEMENT_LEASE_SEC) -> None:
    """Register the messaging routes on the team server's app. `device` is
    the app's existing device-session dependency; `enabled` the
    TEAM_SERVER_MESSAGING switch (off: every route answers
    `messaging_disabled`, and nothing is deleted). The fixed paths come
    before `/{message_id}`."""
    responses = {s: {"model": ErrorBody} for s in (401, 403, 404, 409, 410, 413, 422, 503)}

    def service(request: Request) -> Messaging:
        if not enabled:
            raise refused("messaging_disabled")
        st = request.app.state
        if getattr(st, "messaging", None) is None or st.messaging.repo.db is not st.db:
            st.messaging = Messaging(MongoMessages(st.db), attachments=attachments,
                                     now=now or (lambda: clock_mod.now()), ids=ids,
                                     faults=faults, lease_sec=lease_sec)
        return st.messaging

    @app.post(PREFIX, response_model=SendResult, responses=responses)
    async def send_message(request: Request, svc: Messaging = Depends(service),
                           p: sessions.Principal = Depends(device)):
        req = parse_send(await _bounded_body(request, MESSAGE_REQUEST_MAX_BYTES))
        return await svc.accept(p, req)

    @app.get(PREFIX + "/sync", response_model=SyncPage, responses=responses)
    async def sync_messages(node_id: UUID, continuation: Optional[str] = None,
                            limit: int = SYNC_PAGE_DEFAULT, svc: Messaging = Depends(service),
                            p: sessions.Principal = Depends(device)):
        if not 1 <= limit <= SYNC_PAGE_MAX or (continuation and len(continuation) > 512):
            raise refused("invalid_request")
        return await svc.sync(p, node_id, continuation, limit)

    @app.get(PREFIX + "/recipients", response_model=RecipientList, responses=responses)
    async def message_recipients(svc: Messaging = Depends(service),
                                 p: sessions.Principal = Depends(device)):
        return svc.recipients(p)

    @app.post(PREFIX + "/receipts", response_model=ReceiptBatchResult, responses=responses)
    async def message_receipts(body: ReceiptBatch, svc: Messaging = Depends(service),
                               p: sessions.Principal = Depends(device)):
        return await svc.receipts(p, body)

    @app.put(PREFIX + "/threads/{thread_id}/work-state", response_model=WorkState,
             responses=responses)
    async def thread_work_state(thread_id: UUID, body: WorkCommandRequest,
                                svc: Messaging = Depends(service),
                                p: sessions.Principal = Depends(device)):
        return await svc.change_work_state(p, thread_id, body)

    @app.get(PREFIX + "/{message_id}", response_model=AcceptedMessage, responses=responses)
    async def get_message(message_id: UUID, svc: Messaging = Depends(service),
                          p: sessions.Principal = Depends(device)):
        return await svc.get(p, message_id)

    @app.post(PREFIX + "/{message_id}/announcement", response_model=AnnouncementLease,
              responses=responses)
    async def message_announcement(message_id: UUID, body: AnnouncementRequest,
                                   svc: Messaging = Depends(service),
                                   p: sessions.Principal = Depends(device)):
        return await svc.announce(p, message_id, body)
