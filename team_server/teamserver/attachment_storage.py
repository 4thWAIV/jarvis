"""Attachment bytes in GridFS and their staging records in MongoDB: the
production `messaging_ports.AttachmentStorage`.

GridFS takes part in no transaction, so nothing here pretends to make a
message and its files one write. Each step is its own compare-and-set on a
staging record, in the contract's order (messaging_models, "attachments"):

    create_staging   uploading, bound to owner, message, size and SHA-256
    write_attempt    one GridFS file per attempt; an attempt that fails is
                     aborted and deleted, the record stays uploading
    finalize         uploading -> verified on the attempt whose size and
                     hash the server computed are the record's
    pin              verified -> pinned for one message (the acceptance
                     service, before it inserts the message)
    mark_published   pinned -> published (housekeeping after the insert)

Recovery and cleanup run under one fenced lease (`claim_recovery`): a call
carrying an older fence than the newest raises FenceLost. Every write the
cleanup makes is still a compare-and-set on the record, so a stale worker
that passed the fence check a moment before losing it changes nothing a
newer worker or a publication has since changed.

Collections
  attachment_staging     one record per (server, attachment), `_id`
                         "<server_id>/<attachment_id>"
  attachment_attempts    one per upload attempt, written BEFORE its first
                         chunk, so a crash mid-upload leaves a record the
                         sweep finds; deleted last, after its chunks
  attachment_bytes.files / .chunks   the GridFS bucket, one file per attempt
  attachment_recovery    one document, the recovery lease and its fence
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timedelta
from typing import AsyncIterator, Callable, Optional
from uuid import UUID

from bson import ObjectId
from gridfs import AsyncGridFSBucket
from pymongo import ASCENDING, ReturnDocument
from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import DuplicateKeyError

from teamserver.messaging_models import (OWNER_STAGING_QUOTA_BYTES, STAGING_EXPIRY_SEC,
                                         UPLOAD_CHUNK_BYTES, AttachmentRef, ContractError)
from teamserver.messaging_ports import Attempt, StagingRecord

BUCKET = "attachment_bytes"
RECOVERY_ID = "recovery"
# Staged bytes that count against the owner's quota: not yet pinned to a
# message. Pinned and published bytes belong to a message.
UNPINNED = ("uploading", "verified")


class FenceLost(Exception):
    """A newer recovery worker holds the lease; this pass must stop."""


def _key(server_id: str, attachment_id: UUID) -> str:
    return f"{server_id}/{attachment_id}"


def _record(doc: dict) -> StagingRecord:
    return StagingRecord(
        server_id=doc["server_id"], owner_github_id=doc["owner_github_id"],
        attachment_id=UUID(doc["attachment_id"]), message_id=UUID(doc["message_id"]),
        size_bytes=doc["size_bytes"], sha256=doc["sha256"], state=doc["state"],
        received_bytes=doc["received_bytes"], expires_at=doc.get("expires_at"),
        verified_attempt=doc.get("verified_attempt"), pinned_digest=doc.get("pinned_digest"),
        pinned_at=doc.get("pinned_at"))


async def init(db: AsyncDatabase) -> None:
    """The indexes the storage reads by. Idempotent, like db.init."""
    await db.attachment_staging.create_index(
        [("state", ASCENDING), ("expires_at", ASCENDING)], name="by_state_expiry")
    await db.attachment_staging.create_index(
        [("state", ASCENDING), ("pinned_at", ASCENDING)], name="by_state_pinned")
    await db.attachment_staging.create_index(
        [("server_id", ASCENDING), ("owner_github_id", ASCENDING), ("state", ASCENDING)],
        name="by_owner_state")
    await db.attachment_attempts.create_index([("started_at", ASCENDING)], name="by_started")
    await db.attachment_attempts.create_index(
        [("server_id", ASCENDING), ("attachment_id", ASCENDING)], name="by_attachment")


class GridFSAttachmentStorage:
    """`AttachmentStorage` over one database. `clock` is the service's (the
    team server's `clock.now`, or a test's); `expiry_sec` and `quota_bytes`
    are the deployment's settings (attachments.AttachmentSettings)."""

    def __init__(self, db: AsyncDatabase, *, clock: Callable[[], datetime],
                 expiry_sec: int = STAGING_EXPIRY_SEC,
                 quota_bytes: int = OWNER_STAGING_QUOTA_BYTES,
                 chunk_bytes: int = UPLOAD_CHUNK_BYTES):
        self.db, self.clock = db, clock
        self.expiry = timedelta(seconds=expiry_sec)
        self.quota_bytes, self.chunk_bytes = quota_bytes, chunk_bytes
        self.records = db.attachment_staging
        self.attempts = db.attachment_attempts
        self.lease = db.attachment_recovery
        self.bucket = AsyncGridFSBucket(db, bucket_name=BUCKET, chunk_size_bytes=chunk_bytes)
        self.files = db[f"{BUCKET}.files"]
        self.chunks = db[f"{BUCKET}.chunks"]

    # -- staging ------------------------------------------------------------------

    async def _doc(self, server_id: str, owner_github_id: int,
                   attachment_id: UUID) -> Optional[dict]:
        return await self.records.find_one({"_id": _key(server_id, attachment_id),
                                            "owner_github_id": owner_github_id})

    async def create_staging(self, server_id: str, owner_github_id: int, attachment_id: UUID,
                             message_id: UUID, size_bytes: int, sha256: str,
                             now: datetime) -> StagingRecord:
        """The record, or the identical earlier one. Someone else's record
        under this id, or this owner's with another binding, is
        `idempotency_conflict`; more unpinned bytes than the owner's quota
        is `quota_exceeded` (checked before the insert, so two concurrent
        creates may together pass it by one file)."""
        binding = {"owner_github_id": owner_github_id, "message_id": str(message_id),
                   "size_bytes": size_bytes, "sha256": sha256}
        key = _key(server_id, attachment_id)
        existing = await self.records.find_one({"_id": key})
        if existing is None:
            staged = await self._staged_bytes(server_id, owner_github_id)
            if staged + size_bytes > self.quota_bytes:
                raise ContractError("quota_exceeded")
            doc = {"_id": key, "server_id": server_id, "attachment_id": str(attachment_id),
                   **binding, "state": "uploading", "received_bytes": 0,
                   "expires_at": now + self.expiry, "verified_attempt": None,
                   "latest_attempt": None, "pinned_digest": None, "pinned_at": None,
                   "created_at": now, "revision": 1}
            try:
                await self.records.insert_one(doc)
                return _record(doc)
            except DuplicateKeyError:
                existing = await self.records.find_one({"_id": key})
        if existing is None or any(existing[k] != v for k, v in binding.items()):
            raise ContractError("idempotency_conflict")
        return _record(existing)

    async def _staged_bytes(self, server_id: str, owner_github_id: int) -> int:
        total = 0
        async for doc in self.records.find(
                {"server_id": server_id, "owner_github_id": owner_github_id,
                 "state": {"$in": list(UNPINNED)}}, {"size_bytes": 1}):
            total += doc["size_bytes"]
        return total

    async def staging(self, server_id: str, owner_github_id: int,
                      attachment_id: UUID) -> Optional[StagingRecord]:
        doc = await self._doc(server_id, owner_github_id, attachment_id)
        return _record(doc) if doc else None

    async def latest_attempt(self, server_id: str, owner_github_id: int,
                             attachment_id: UUID) -> Optional[str]:
        """The attempt that last finished writing, which finalize checks.
        (Not in the contract's Protocol: the finalize route names no
        attempt, so the service asks the storage which one.)"""
        doc = await self._doc(server_id, owner_github_id, attachment_id)
        return doc.get("latest_attempt") if doc else None

    async def write_attempt(self, server_id: str, owner_github_id: int, attachment_id: UUID,
                            chunks: AsyncIterator[bytes], max_bytes: int) -> Attempt:
        """One attempt from byte 0. The bytes are counted as they are read,
        whatever the request's Content-Length said: past `max_bytes` it is
        `too_large`. Any failure (too large, disconnect, idle timeout,
        cancellation) deletes the attempt's chunks and then its record, and
        leaves the staging record uploading for a retry."""
        if await self._doc(server_id, owner_github_id, attachment_id) is None:
            raise ContractError("attachment_not_found")
        attempt_id = str(ObjectId())
        await self.attempts.insert_one({
            "_id": attempt_id, "server_id": server_id, "owner_github_id": owner_github_id,
            "attachment_id": str(attachment_id), "state": "writing",
            "started_at": self.clock(), "size_bytes": None, "sha256": None})
        grid_in = self.bucket.open_upload_stream_with_id(
            ObjectId(attempt_id), str(attachment_id),
            metadata={"server_id": server_id, "attachment_id": str(attachment_id)})
        hasher, size = hashlib.sha256(), 0
        try:
            async for chunk in chunks:
                size += len(chunk)
                if size > max_bytes:
                    raise ContractError("too_large")
                hasher.update(chunk)
                await grid_in.write(chunk)
            await grid_in.close()
        except BaseException:
            await self._discard_attempt(attempt_id)
            raise
        attempt = Attempt(attempt_id=attempt_id, size_bytes=size, sha256=hasher.hexdigest())
        await self.attempts.update_one({"_id": attempt_id}, {"$set": {
            "state": "complete", "size_bytes": size, "sha256": attempt.sha256}})
        await self.records.update_one(
            {"_id": _key(server_id, attachment_id), "state": "uploading"},
            {"$set": {"received_bytes": size, "latest_attempt": attempt_id},
             "$inc": {"revision": 1}})
        return attempt

    async def _discard_attempt(self, attempt_id: str) -> None:
        """Chunks, then the files document, then the attempt's own record:
        until the last step the sweep can still find it."""
        oid = ObjectId(attempt_id)
        await self.chunks.delete_many({"files_id": oid})
        await self.files.delete_one({"_id": oid})
        await self.attempts.delete_one({"_id": attempt_id})

    async def finalize(self, server_id: str, owner_github_id: int, attachment_id: UUID,
                       attempt_id: str, now: datetime) -> StagingRecord:
        doc = await self._doc(server_id, owner_github_id, attachment_id)
        if doc is None or doc["state"] == "expired":
            raise ContractError("attachment_not_found")
        if doc["state"] != "uploading":
            return _record(doc)
        attempt = await self.attempts.find_one({"_id": attempt_id, "state": "complete",
                                                "attachment_id": str(attachment_id)})
        if attempt is None:
            raise ContractError("attachment_not_ready")
        if (attempt["size_bytes"], attempt["sha256"]) != (doc["size_bytes"], doc["sha256"]):
            await self._discard_attempt(attempt_id)
            raise ContractError("attachment_mismatch")
        updated = await self.records.find_one_and_update(
            {"_id": doc["_id"], "state": "uploading", "revision": doc["revision"]},
            {"$set": {"state": "verified", "verified_attempt": attempt_id},
             "$inc": {"revision": 1}}, return_document=ReturnDocument.AFTER)
        if updated is None:      # someone else moved it first: whatever it is now
            return await self.finalize(server_id, owner_github_id, attachment_id,
                                       attempt_id, now)
        return _record(updated)

    # -- publication ----------------------------------------------------------------

    async def pin(self, server_id: str, sender_github_id: int, ref: AttachmentRef,
                  message_id: UUID, digest: str, now: datetime) -> bool:
        binding = {"_id": _key(server_id, ref.attachment_id),
                   "owner_github_id": sender_github_id, "message_id": str(message_id),
                   "size_bytes": ref.size_bytes, "sha256": ref.sha256}
        pinned = await self.records.update_one(
            {**binding, "state": {"$in": ["verified", "pinned"]}},
            {"$set": {"state": "pinned", "pinned_digest": digest, "pinned_at": now,
                      "expires_at": None}, "$inc": {"revision": 1}})
        if pinned.matched_count:
            return True
        # An identical retry after publication: the same message and digest.
        return await self.records.count_documents(
            {**binding, "state": "published", "pinned_digest": digest}, limit=1) > 0

    async def mark_published(self, server_id: str, attachment_id: UUID) -> None:
        await self.records.update_one(
            {"_id": _key(server_id, attachment_id), "state": "pinned"},
            {"$set": {"state": "published"}, "$inc": {"revision": 1}})

    async def read(self, server_id: str, attachment_id: UUID) -> AsyncIterator[bytes]:
        """The chosen attempt's bytes, in bounded chunks. The service calls
        this only for a file an accepted message the viewer may see names,
        so it is not refused by state: a record cleanup claimed by mistake
        still serves its accepted message (the recovery never deletes one)."""
        doc = await self.records.find_one({"_id": _key(server_id, attachment_id)})
        if doc is None or not doc.get("verified_attempt"):
            raise ContractError("attachment_not_found")
        grid_out = await self.bucket.open_download_stream(ObjectId(doc["verified_attempt"]))
        while True:
            chunk = await grid_out.readchunk()
            if not chunk:
                return
            yield chunk

    # -- recovery and cleanup, under the fenced lease ----------------------------------

    async def claim_recovery(self, worker_id: str, now: datetime,
                             lease_sec: float) -> Optional[int]:
        try:
            doc = await self.lease.find_one_and_update(
                {"_id": RECOVERY_ID,
                 "$or": [{"expires_at": {"$lte": now}}, {"holder": worker_id}]},
                {"$set": {"holder": worker_id,
                          "expires_at": now + timedelta(seconds=lease_sec)},
                 "$inc": {"fence": 1}},
                upsert=True, return_document=ReturnDocument.AFTER)
        except DuplicateKeyError:          # held, live, by someone else
            return None
        return doc["fence"]

    async def _fenced(self, fence: int) -> None:
        doc = await self.lease.find_one({"_id": RECOVERY_ID})
        if doc is None or doc["fence"] != fence or doc["expires_at"] <= self.clock():
            raise FenceLost(str(fence))

    async def stale_pins(self, fence: int, older_than: datetime,
                         limit: int) -> list[StagingRecord]:
        await self._fenced(fence)
        cursor = self.records.find({"state": "pinned", "pinned_at": {"$lt": older_than}})
        return [_record(d) async for d in cursor.limit(limit)]

    async def release_pin(self, fence: int, record: StagingRecord) -> bool:
        await self._fenced(fence)
        released = await self.records.update_one(
            {"_id": _key(record.server_id, record.attachment_id), "state": "pinned",
             "owner_github_id": record.owner_github_id, "message_id": str(record.message_id),
             "pinned_digest": record.pinned_digest, "pinned_at": record.pinned_at,
             "verified_attempt": record.verified_attempt},
            {"$set": {"state": "verified", "pinned_digest": None, "pinned_at": None,
                      "expires_at": self.clock() + self.expiry}, "$inc": {"revision": 1}})
        return released.modified_count == 1

    async def claim_expired(self, fence: int, now: datetime, limit: int) -> list[StagingRecord]:
        await self._fenced(fence)
        claimed = []
        cursor = self.records.find({"state": {"$in": list(UNPINNED)},
                                    "expires_at": {"$lte": now}}).limit(limit)
        async for doc in cursor:
            updated = await self.records.find_one_and_update(
                {"_id": doc["_id"], "state": doc["state"], "revision": doc["revision"]},
                {"$set": {"state": "expired"}, "$inc": {"revision": 1}},
                return_document=ReturnDocument.AFTER)
            if updated is not None:
                claimed.append(_record(updated))
        return claimed

    async def delete(self, fence: int, record: StagingRecord) -> None:
        """Every attempt's chunks and files document, then the record, which
        must still be expired."""
        await self._fenced(fence)
        key = _key(record.server_id, record.attachment_id)
        if await self.records.count_documents({"_id": key, "state": "expired"}, limit=1) == 0:
            return
        async for attempt in self.attempts.find({"server_id": record.server_id,
                                                 "attachment_id": str(record.attachment_id)}):
            await self._discard_attempt(attempt["_id"])
        if record.verified_attempt:       # in case its attempt record went first
            await self._discard_attempt(record.verified_attempt)
        await self.records.delete_one({"_id": key, "state": "expired"})

    async def sweep_attempts(self, fence: int, older_than: datetime, limit: int) -> int:
        """Attempts started before `older_than` that no record holds: not
        its chosen (verified) attempt, and not the finished attempt of a
        record still waiting to be finalized. A crashed upload's chunks go
        with it."""
        await self._fenced(fence)
        swept = 0
        cursor = self.attempts.find({"started_at": {"$lt": older_than}}).limit(limit)
        async for attempt in cursor:
            doc = await self.records.find_one(
                {"_id": _key(attempt["server_id"], UUID(attempt["attachment_id"]))})
            held = doc is not None and (
                doc.get("verified_attempt") == attempt["_id"]
                or (doc["state"] == "uploading" and doc.get("latest_attempt") == attempt["_id"]))
            if not held:
                await self._discard_attempt(attempt["_id"])
                swept += 1
        return swept
