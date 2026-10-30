"""Attachments on the team server: GridFS staging, streamed uploads and
their limits, compare-and-set publication, the fenced recovery worker and
authorized downloads, against a real MongoDB.

The acceptance service (delivery) is not here: `accept` below is a test
double that takes the contract's steps in the contract's order (pin each
file, insert the message, mark each file published) and can stop between
them, which is what the publication and crash tests need."""
from __future__ import annotations

import asyncio
import hashlib
import os
import uuid
from datetime import timedelta
from typing import Optional

import pytest
from bson import ObjectId
from fastapi.testclient import TestClient

from conftest import (ADMIN, ALICE, BASE, BOB, MALLORY, Clock, browser_token, make_config,
                      mongo_uri, pair, wait_initialized, wait_messaging)
from teamserver import attachment_storage, attachments, clock as clock_mod, db as dbmod
from teamserver.app import create_app
from teamserver.attachment_storage import FenceLost, GridFSAttachmentStorage
from teamserver.attachments import (ATTEMPT_SWEEP_AFTER_SEC, AttachmentService,
                                    AttachmentSettings, recover_once)
from teamserver.errors import Problem
from teamserver.messaging_models import (ORPHAN_PIN_AFTER_SEC, RECOVERY_LEASE_SEC,
                                         STAGING_EXPIRY_SEC, AcceptedMessage, ArmedFaults,
                                         AttachmentCreate, AttachmentRef, ContractError,
                                         InjectedFault, MessageContent, content_digest)
from teamserver.messaging_ports import ATTACHMENT_SETTINGS, AttachmentStorage

SERVER = "srv-attachments-test"
ALICE_ID, BOB_ID = ALICE.id, BOB.id
UPLOAD_CHUNK = 64 * 1024


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


async def chunked(data: bytes, size: int = UPLOAD_CHUNK, fail_after: Optional[int] = None):
    for i in range(0, len(data), size):
        if fail_after is not None and i >= fail_after:
            raise ConnectionResetError("client went away")
        yield data[i:i + size]


class Accepted:
    """The message repository's `get_visible`, over accepted messages in a
    dict: a participant sees a message, nobody else does."""

    def __init__(self):
        self.messages: dict[uuid.UUID, AcceptedMessage] = {}

    async def get_visible(self, server_id, viewer_github_id, message_id):
        m = self.messages.get(message_id)
        if m is None or m.server_id != server_id:
            return None
        return m if viewer_github_id in (m.sender_github_id, m.recipient_github_id) else None


async def accept(storage: AttachmentStorage, visible: Accepted, *, sender: int,
                 recipient: int, refs: list[AttachmentRef], message_id: uuid.UUID,
                 now, stop_after: Optional[str] = None, server_id: str = SERVER,
                 revision: int = 1) -> Optional[AcceptedMessage]:
    """The contract's acceptance order, stoppable after "pin" or "insert"."""
    content = MessageContent(message_id=message_id, thread_id=message_id,
                             recipient_github_id=recipient, content_revision=revision,
                             attachments=refs)
    digest = content_digest(content, server_id=server_id, sender_github_id=sender)
    for ref in refs:
        if not await storage.pin(server_id, sender, ref, message_id, digest, now):
            raise ContractError("attachment_not_ready")
    if stop_after == "pin":
        return None
    m = AcceptedMessage(**content.model_dump(), server_id=server_id, sender_github_id=sender,
                        source_node_id=uuid.uuid4(), content_digest=digest,
                        idempotency_key=uuid.uuid4(), accepted_at=now)
    visible.messages[message_id] = m
    if stop_after == "insert":
        return m
    for ref in refs:
        await storage.mark_published(server_id, ref.attachment_id)
    return m


class Env:
    def __init__(self, db, clock: Clock, settings: AttachmentSettings, faults):
        self.db, self.clock = db, clock
        self.storage = GridFSAttachmentStorage(db, clock=clock,
                                               expiry_sec=settings.staging_expiry_sec,
                                               quota_bytes=settings.staging_quota_bytes)
        self.visible = Accepted()
        self.service = AttachmentService(self.storage, self.visible, settings=settings,
                                         clock=clock, faults=faults)

    async def stage(self, data: bytes, owner: int = ALICE_ID,
                    message_id: Optional[uuid.UUID] = None, filename: str = "notes.txt",
                    media_type: str = "text/plain") -> AttachmentRef:
        """Create, upload and finalize one file; its manifest entry."""
        ref = AttachmentRef(attachment_id=uuid.uuid4(), filename=filename,
                            media_type=media_type, size_bytes=len(data), sha256=sha(data))
        await self.service.create(SERVER, owner, AttachmentCreate(
            attachment_id=ref.attachment_id, message_id=message_id or uuid.uuid4(),
            size_bytes=ref.size_bytes, sha256=ref.sha256))
        await self.service.upload(SERVER, owner, ref.attachment_id, chunked(data), None)
        status = await self.service.finalize(SERVER, owner, ref.attachment_id)
        assert status.state == "verified"
        return ref

    async def counts(self) -> tuple[int, int, int]:
        """GridFS files, GridFS chunks, attempt records."""
        return (await self.db[f"{attachment_storage.BUCKET}.files"].count_documents({}),
                await self.db[f"{attachment_storage.BUCKET}.chunks"].count_documents({}),
                await self.db.attachment_attempts.count_documents({}))

    async def read(self, attachment_id) -> bytes:
        return b"".join([c async for c in self.storage.read(SERVER, attachment_id)])


def run(dbname: str, fn, settings: Optional[AttachmentSettings] = None, faults=None):
    async def go():
        client = dbmod.connect(mongo_uri())
        try:
            db = dbmod.database(client, dbname)
            await attachment_storage.init(db)
            env = Env(db, Clock(), settings or attachments.DEFAULT_SETTINGS,
                      faults or ArmedFaults())
            return await fn(env)
        finally:
            await client.close()
    return asyncio.run(go())


def settings(**over) -> AttachmentSettings:
    return AttachmentSettings(**{**attachments.DEFAULT_SETTINGS.__dict__, **over})


# -- staging and streamed uploads --------------------------------------------------

def test_a_staged_file_is_verified_by_the_bytes_the_server_hashed(dbname):
    data = os.urandom(3 * 1024 * 1024 + 17)          # several GridFS chunks and a tail

    async def go(env: Env):
        ref = await env.stage(data)
        status = await env.service.status(SERVER, ALICE_ID, ref.attachment_id)
        assert (status.state, status.size_bytes, status.received_bytes) == \
            ("verified", len(data), len(data))
        assert status.expires_at == env.clock() + timedelta(seconds=STAGING_EXPIRY_SEC)
        record = await env.storage.staging(SERVER, ALICE_ID, ref.attachment_id)
        assert await env.read(ref.attachment_id) == data
        assert sha(await env.read(ref.attachment_id)) == record.sha256
    run(dbname, go)


def test_a_zero_byte_file_is_a_file(dbname):
    async def go(env: Env):
        ref = await env.stage(b"")
        assert await env.read(ref.attachment_id) == b""
    run(dbname, go)


def test_staging_is_its_owners_alone(dbname):
    async def go(env: Env):
        ref = await env.stage(b"alice's bytes")
        assert await env.storage.staging(SERVER, BOB_ID, ref.attachment_id) is None
        for call in (env.service.status(SERVER, BOB_ID, ref.attachment_id),
                     env.service.finalize(SERVER, BOB_ID, ref.attachment_id),
                     env.service.upload(SERVER, BOB_ID, ref.attachment_id, chunked(b"x"), 1)):
            with pytest.raises(ContractError) as e:
                await call
            assert e.value.code == "attachment_not_found"
        # Bob reusing Alice's id learns only that it is taken.
        with pytest.raises(ContractError) as e:
            await env.service.create(SERVER, BOB_ID, AttachmentCreate(
                attachment_id=ref.attachment_id, message_id=uuid.uuid4(),
                size_bytes=ref.size_bytes, sha256=ref.sha256))
        assert e.value.code == "idempotency_conflict"
    run(dbname, go)


def test_create_is_idempotent_for_the_same_binding_only(dbname):
    async def go(env: Env):
        body = AttachmentCreate(attachment_id=uuid.uuid4(), message_id=uuid.uuid4(),
                                size_bytes=5, sha256=sha(b"hello"))
        first = await env.service.create(SERVER, ALICE_ID, body)
        assert await env.service.create(SERVER, ALICE_ID, body) == first
        with pytest.raises(ContractError) as e:
            await env.service.create(SERVER, ALICE_ID, body.model_copy(
                update={"sha256": sha(b"world")}))
        assert e.value.code == "idempotency_conflict"
    run(dbname, go)


def test_bytes_past_the_record_size_abort_the_attempt_whatever_content_length_said(dbname):
    async def go(env: Env):
        data = os.urandom(200_000)
        body = AttachmentCreate(attachment_id=uuid.uuid4(), message_id=uuid.uuid4(),
                                size_bytes=len(data), sha256=sha(data))
        await env.service.create(SERVER, ALICE_ID, body)
        # Content-Length claims a small body; the bytes keep coming.
        with pytest.raises(ContractError) as e:
            await env.service.upload(SERVER, ALICE_ID, body.attachment_id,
                                     chunked(data + b"more"), declared_length=10)
        assert e.value.code == "too_large"
        assert await env.counts() == (0, 0, 0)       # the attempt is gone, chunks and all
        # A declared length past the record's size is refused before reading.
        with pytest.raises(ContractError):
            await env.service.upload(SERVER, ALICE_ID, body.attachment_id, chunked(data),
                                     declared_length=len(data) + 1)
        # No Content-Length at all: counted, and accepted at the right size.
        status = await env.service.upload(SERVER, ALICE_ID, body.attachment_id,
                                          chunked(data), declared_length=None)
        assert (status.state, status.received_bytes) == ("uploading", len(data))
    run(dbname, go)


def test_a_file_larger_than_the_setting_is_refused_at_create(dbname):
    async def go(env: Env):
        with pytest.raises(ContractError) as e:
            await env.service.create(SERVER, ALICE_ID, AttachmentCreate(
                attachment_id=uuid.uuid4(), message_id=uuid.uuid4(), size_bytes=1025,
                sha256=sha(b"")))
        assert e.value.code == "too_large"
    run(dbname, go, settings=settings(file_max_bytes=1024))


def test_the_owner_staging_quota_counts_unpinned_bytes(dbname):
    async def go(env: Env):
        await env.stage(os.urandom(600))
        with pytest.raises(ContractError) as e:
            await env.stage(os.urandom(500))
        assert e.value.code == "quota_exceeded"
        await env.stage(os.urandom(500), owner=BOB_ID)      # Bob's quota is his own
    run(dbname, go, settings=settings(staging_quota_bytes=1000))


def test_an_interrupted_upload_stays_invisible_and_the_retry_matches(dbname):
    """file-upload-interrupted: the partial attempt is deleted with its
    chunks, the record stays uploading under the same id, and a retry of
    the same bytes verifies."""
    async def go(env: Env):
        data = os.urandom(1_500_000)
        body = AttachmentCreate(attachment_id=uuid.uuid4(), message_id=uuid.uuid4(),
                                size_bytes=len(data), sha256=sha(data))
        await env.service.create(SERVER, ALICE_ID, body)
        with pytest.raises(ConnectionResetError):
            await env.service.upload(SERVER, ALICE_ID, body.attachment_id,
                                     chunked(data, fail_after=700_000), None)
        assert await env.counts() == (0, 0, 0)
        assert (await env.service.status(SERVER, ALICE_ID, body.attachment_id)).state == \
            "uploading"
        with pytest.raises(ContractError) as e:           # nothing finished to finalize
            await env.service.finalize(SERVER, ALICE_ID, body.attachment_id)
        assert e.value.code == "attachment_not_ready"
        await env.service.upload(SERVER, ALICE_ID, body.attachment_id, chunked(data), None)
        assert (await env.service.finalize(SERVER, ALICE_ID, body.attachment_id)).state == \
            "verified"
        assert await env.read(body.attachment_id) == data
    run(dbname, go)


def test_wrong_bytes_are_a_mismatch_and_the_attachment_id_survives_it(dbname):
    async def go(env: Env):
        data = os.urandom(4096)
        wrong = bytes(reversed(data))
        body = AttachmentCreate(attachment_id=uuid.uuid4(), message_id=uuid.uuid4(),
                                size_bytes=len(data), sha256=sha(data))
        await env.service.create(SERVER, ALICE_ID, body)
        await env.service.upload(SERVER, ALICE_ID, body.attachment_id, chunked(wrong), None)
        with pytest.raises(ContractError) as e:
            await env.service.finalize(SERVER, ALICE_ID, body.attachment_id)
        assert e.value.code == "attachment_mismatch"
        assert await env.counts() == (0, 0, 0)         # the wrong attempt is deleted
        await env.service.upload(SERVER, ALICE_ID, body.attachment_id, chunked(data), None)
        await env.service.finalize(SERVER, ALICE_ID, body.attachment_id)
        assert await env.read(body.attachment_id) == data
    run(dbname, go)


def test_an_attempt_after_verification_does_not_replace_the_chosen_bytes(dbname):
    async def go(env: Env):
        data = os.urandom(1000)
        ref = await env.stage(data)
        status = await env.service.upload(SERVER, ALICE_ID, ref.attachment_id,
                                          chunked(os.urandom(1000)), None)
        assert status.state == "verified"
        assert await env.read(ref.attachment_id) == data
        assert (await env.counts())[0] == 1
    run(dbname, go)


def test_an_idle_upload_is_aborted(dbname):
    async def go(env: Env):
        body = AttachmentCreate(attachment_id=uuid.uuid4(), message_id=uuid.uuid4(),
                                size_bytes=10, sha256=sha(b"0123456789"))
        await env.service.create(SERVER, ALICE_ID, body)

        async def stalls():
            yield b"01234"
            await asyncio.sleep(5)
            yield b"56789"
        with pytest.raises(Problem) as e:
            await env.service.upload(SERVER, ALICE_ID, body.attachment_id, stalls(), None)
        assert (e.value.status, e.value.code) == (408, "upload_idle")
        assert await env.counts() == (0, 0, 0)
    run(dbname, go, settings=settings(upload_idle_timeout_sec=1))


def test_an_owner_has_at_most_the_configured_concurrent_uploads(dbname):
    async def go(env: Env):
        release = asyncio.Event()
        ids = []
        for _ in range(3):
            body = AttachmentCreate(attachment_id=uuid.uuid4(), message_id=uuid.uuid4(),
                                    size_bytes=2, sha256=sha(b"ab"))
            await env.service.create(SERVER, ALICE_ID, body)
            ids.append(body.attachment_id)

        async def held():
            yield b"a"
            await release.wait()
            yield b"b"
        first = [asyncio.create_task(env.service.upload(SERVER, ALICE_ID, i, held(), None))
                 for i in ids[:2]]
        await asyncio.sleep(0.2)
        with pytest.raises(Problem) as e:
            await env.service.upload(SERVER, ALICE_ID, ids[2], chunked(b"ab"), None)
        assert (e.value.status, e.value.code) == (429, "rate_limited")
        # Another owner is not held up by Alice's uploads.
        await env.stage(b"bob", owner=BOB_ID)
        release.set()
        await asyncio.gather(*first)
        status = await env.service.upload(SERVER, ALICE_ID, ids[2], chunked(b"ab"), None)
        assert status.received_bytes == 2
    run(dbname, go, settings=settings(upload_streams_max=2))


def test_finalize_fault_points(dbname):
    faults = ArmedFaults()

    async def go(env: Env):
        data = b"fault me"
        body = AttachmentCreate(attachment_id=uuid.uuid4(), message_id=uuid.uuid4(),
                                size_bytes=len(data), sha256=sha(data))
        await env.service.create(SERVER, ALICE_ID, body)
        await env.service.upload(SERVER, ALICE_ID, body.attachment_id, chunked(data), None)
        faults.arm("server_before_attachment_finalize")
        with pytest.raises(InjectedFault):
            await env.service.finalize(SERVER, ALICE_ID, body.attachment_id)
        assert (await env.service.status(SERVER, ALICE_ID, body.attachment_id)).state == \
            "uploading"
        faults.arm("server_after_attachment_finalize")
        with pytest.raises(InjectedFault):      # verified, the answer lost
            await env.service.finalize(SERVER, ALICE_ID, body.attachment_id)
        assert (await env.service.finalize(SERVER, ALICE_ID, body.attachment_id)).state == \
            "verified"
    run(dbname, go, faults=faults)


# -- publication --------------------------------------------------------------------

def test_a_pin_belongs_to_one_message_of_one_sender(dbname):
    async def go(env: Env):
        message = uuid.uuid4()
        ref = await env.stage(b"pinned", message_id=message)
        now = env.clock()
        assert not await env.storage.pin(SERVER, ALICE_ID, ref, uuid.uuid4(), "sha256:" + "a" * 64,
                                         now)                        # another message
        assert not await env.storage.pin(SERVER, BOB_ID, ref, message, "sha256:" + "a" * 64,
                                         now)                        # another sender
        bigger = ref.model_copy(update={"size_bytes": ref.size_bytes + 1})
        assert not await env.storage.pin(SERVER, ALICE_ID, bigger, message, "sha256:" + "a" * 64,
                                         now)                        # another manifest entry
        assert await env.storage.pin(SERVER, ALICE_ID, ref, message, "sha256:" + "a" * 64, now)
        # The same never-accepted message may move to a new digest (a new revision).
        assert await env.storage.pin(SERVER, ALICE_ID, ref, message, "sha256:" + "b" * 64, now)
        await env.storage.mark_published(SERVER, ref.attachment_id)
        # After publication: an identical retry still pins, nothing else does.
        assert await env.storage.pin(SERVER, ALICE_ID, ref, message, "sha256:" + "b" * 64, now)
        assert not await env.storage.pin(SERVER, ALICE_ID, ref, message, "sha256:" + "c" * 64,
                                         now)
        record = await env.storage.staging(SERVER, ALICE_ID, ref.attachment_id)
        assert (record.state, record.expires_at) == ("published", None)
    run(dbname, go)


def test_a_file_never_finalized_cannot_be_pinned(dbname):
    async def go(env: Env):
        data = b"half"
        message = uuid.uuid4()
        body = AttachmentCreate(attachment_id=uuid.uuid4(), message_id=message,
                                size_bytes=len(data), sha256=sha(data))
        await env.service.create(SERVER, ALICE_ID, body)
        await env.service.upload(SERVER, ALICE_ID, body.attachment_id, chunked(data), None)
        ref = AttachmentRef(attachment_id=body.attachment_id, filename="half.txt",
                            media_type="text/plain", size_bytes=len(data), sha256=sha(data))
        with pytest.raises(ContractError) as e:
            await accept(env.storage, env.visible, sender=ALICE_ID, recipient=BOB_ID,
                         refs=[ref], message_id=message, now=env.clock())
        assert e.value.code == "attachment_not_ready"
        assert env.visible.messages == {}
    run(dbname, go)


def test_pin_cleanup_race_has_one_winner_and_never_deletes_accepted_bytes(dbname):
    """pin-cleanup-race, both orders and then concurrently."""
    async def go(env: Env):
        # Cleanup first: publication fails safely, nothing becomes visible.
        lost_message = uuid.uuid4()
        lost = await env.stage(b"cleanup wins", message_id=lost_message)
        env.clock.advance(seconds=STAGING_EXPIRY_SEC + 1)
        fence = await env.storage.claim_recovery("cleaner", env.clock(), RECOVERY_LEASE_SEC)
        claimed = await env.storage.claim_expired(fence, env.clock(), 10)
        assert [r.attachment_id for r in claimed] == [lost.attachment_id]
        with pytest.raises(ContractError):
            await accept(env.storage, env.visible, sender=ALICE_ID, recipient=BOB_ID,
                         refs=[lost], message_id=lost_message, now=env.clock())
        assert lost_message not in env.visible.messages

        # Publication first: cleanup finds nothing to claim, the bytes stay.
        won_message = uuid.uuid4()
        won = await env.stage(b"publication wins", message_id=won_message)
        await accept(env.storage, env.visible, sender=ALICE_ID, recipient=BOB_ID, refs=[won],
                     message_id=won_message, now=env.clock())
        env.clock.advance(seconds=STAGING_EXPIRY_SEC + 1)
        fence = await env.storage.claim_recovery("cleaner", env.clock(), RECOVERY_LEASE_SEC)
        assert await env.storage.claim_expired(fence, env.clock(), 10) == []
        assert await env.read(won.attachment_id) == b"publication wins"

        # Both at once, many times: exactly one of them wins each record.
        for i in range(20):
            message = uuid.uuid4()
            ref = await env.stage(b"race %d" % i, message_id=message)
            env.clock.advance(seconds=STAGING_EXPIRY_SEC + 1)
            fence = await env.storage.claim_recovery("cleaner", env.clock(),
                                                     RECOVERY_LEASE_SEC)
            content = MessageContent(message_id=message, thread_id=message,
                                     recipient_github_id=BOB_ID, content_revision=1,
                                     attachments=[ref])
            digest = content_digest(content, server_id=SERVER, sender_github_id=ALICE_ID)
            pinned, claimed = await asyncio.gather(
                env.storage.pin(SERVER, ALICE_ID, ref, message, digest, env.clock()),
                env.storage.claim_expired(fence, env.clock(), 10))
            assert pinned != bool(claimed), (pinned, claimed)
            if pinned:
                assert await env.read(ref.attachment_id) == b"race %d" % i
    run(dbname, go)


def test_crash_after_message_insert_leaves_a_readable_message_that_recovery_finishes(dbname):
    """crash-after-message-insert: the message is authoritative; its pinned
    files serve it without the housekeeping flag, and recovery marks them
    published instead of releasing them."""
    async def go(env: Env):
        message = uuid.uuid4()
        data = os.urandom(70_000)
        ref = await env.stage(data, message_id=message)
        await accept(env.storage, env.visible, sender=ALICE_ID, recipient=BOB_ID, refs=[ref],
                     message_id=message, now=env.clock(), stop_after="insert")
        got_ref, chunks = await env.service.download(SERVER, BOB_ID, message, ref.attachment_id)
        assert got_ref == ref and b"".join([c async for c in chunks]) == data

        env.clock.advance(seconds=ORPHAN_PIN_AFTER_SEC + 1)
        done = await recover_once(env.storage, env.visible, worker_id="w1", now=env.clock())
        assert (done.published, done.released) == (1, 0)
        assert (await env.storage.staging(SERVER, ALICE_ID, ref.attachment_id)).state == \
            "published"
        env.clock.advance(seconds=STAGING_EXPIRY_SEC + ATTEMPT_SWEEP_AFTER_SEC)
        done = await recover_once(env.storage, env.visible, worker_id="w1", now=env.clock())
        assert (done.deleted, done.swept) == (0, 0)
        assert await env.read(ref.attachment_id) == data
    run(dbname, go)


def test_crash_after_pin_before_insert_is_released_only_after_the_hour(dbname):
    """A crash between the pins and the insert: nothing is visible, a retry
    within the hour reuses the pin, and only an hour-old pin with no
    accepted message is released, then cleaned up after expiry."""
    async def go(env: Env):
        message = uuid.uuid4()
        ref = await env.stage(b"orphan", message_id=message)
        await accept(env.storage, env.visible, sender=ALICE_ID, recipient=BOB_ID, refs=[ref],
                     message_id=message, now=env.clock(), stop_after="pin")
        with pytest.raises(ContractError):
            await env.service.download(SERVER, BOB_ID, message, ref.attachment_id)

        env.clock.advance(seconds=ORPHAN_PIN_AFTER_SEC - 60)
        done = await recover_once(env.storage, env.visible, worker_id="w1", now=env.clock())
        assert done.released == 0                  # a restart never releases a young pin

        env.clock.advance(seconds=120)
        done = await recover_once(env.storage, env.visible, worker_id="w1", now=env.clock())
        assert done.released == 1
        record = await env.storage.staging(SERVER, ALICE_ID, ref.attachment_id)
        assert (record.state, record.pinned_digest) == ("verified", None)
        # A later manual retry may pin it again and publish.
        await accept(env.storage, env.visible, sender=ALICE_ID, recipient=BOB_ID, refs=[ref],
                     message_id=message, now=env.clock())
        assert await env.read(ref.attachment_id) == b"orphan"

        # One that is never retried expires and goes, bytes and all.
        other = await env.stage(b"abandoned", message_id=uuid.uuid4())
        env.clock.advance(seconds=STAGING_EXPIRY_SEC + 1)
        done = await recover_once(env.storage, env.visible, worker_id="w1", now=env.clock())
        assert done.deleted == 1
        assert await env.storage.staging(SERVER, ALICE_ID, other.attachment_id) is None
        assert (await env.counts())[0] == 1          # only the published file's bytes
    run(dbname, go)


def test_recovery_holds_one_fenced_lease(dbname):
    async def go(env: Env):
        now = env.clock()
        first = await env.storage.claim_recovery("w1", now, RECOVERY_LEASE_SEC)
        assert await env.storage.claim_recovery("w2", now, RECOVERY_LEASE_SEC) is None
        assert await recover_once(env.storage, env.visible, worker_id="w2", now=now) is None
        env.clock.advance(seconds=RECOVERY_LEASE_SEC + 1)
        second = await env.storage.claim_recovery("w2", env.clock(), RECOVERY_LEASE_SEC)
        assert second > first
        for stale in (env.storage.stale_pins(first, env.clock(), 10),
                      env.storage.claim_expired(first, env.clock(), 10),
                      env.storage.sweep_attempts(first, env.clock(), 10)):
            with pytest.raises(FenceLost):
                await stale
    run(dbname, go)


def test_the_sweep_deletes_a_crashed_uploads_chunks_and_unchosen_attempts(dbname):
    async def go(env: Env):
        ref = await env.stage(os.urandom(2_000_000))
        # A process that died mid-upload: its attempt record and some chunks,
        # no GridFS files document.
        dead = ObjectId()
        await env.db.attachment_attempts.insert_one({
            "_id": str(dead), "server_id": SERVER, "owner_github_id": ALICE_ID,
            "attachment_id": str(ref.attachment_id), "state": "writing",
            "started_at": env.clock(), "size_bytes": None, "sha256": None})
        await env.db[f"{attachment_storage.BUCKET}.chunks"].insert_many(
            [{"files_id": dead, "n": n, "data": b"x" * 10} for n in range(3)])
        env.clock.advance(seconds=ATTEMPT_SWEEP_AFTER_SEC + 1)
        done = await recover_once(env.storage, env.visible, worker_id="w1", now=env.clock())
        assert done.swept == 1
        assert await env.db[f"{attachment_storage.BUCKET}.chunks"].count_documents(
            {"files_id": dead}) == 0
        assert await env.db.attachment_attempts.count_documents({"_id": str(dead)}) == 0
        assert len(await env.read(ref.attachment_id)) == 2_000_000     # the chosen one stays
    run(dbname, go)


# -- downloads ------------------------------------------------------------------------

def test_downloads_need_an_accepted_message_the_viewer_may_see(dbname):
    """two-owner-isolation for files: the opaque id is no permission, and
    neither is being a team administrator."""
    async def go(env: Env):
        message = uuid.uuid4()
        ref = await env.stage(b"for bob", message_id=message)
        with pytest.raises(ContractError):        # staged, not accepted: invisible to Bob
            await env.service.download(SERVER, BOB_ID, message, ref.attachment_id)
        await accept(env.storage, env.visible, sender=ALICE_ID, recipient=BOB_ID, refs=[ref],
                     message_id=message, now=env.clock())
        for viewer in (BOB_ID, ALICE_ID):
            _, chunks = await env.service.download(SERVER, viewer, message, ref.attachment_id)
            assert b"".join([c async for c in chunks]) == b"for bob"
        for viewer, msg, att in ((MALLORY.id, message, ref.attachment_id),
                                 (ADMIN.id, message, ref.attachment_id),
                                 (BOB_ID, message, uuid.uuid4()),
                                 (BOB_ID, uuid.uuid4(), ref.attachment_id)):
            with pytest.raises(ContractError) as e:
                await env.service.download(SERVER, viewer, msg, att)
            assert e.value.code == "attachment_not_found"
    run(dbname, go)


# -- the routes, through the real app -----------------------------------------------------

class Served:
    """The real app with messaging switched on, so its own wiring mounts the
    attachment routes behind its device dependency; Alice and Bob each with
    a paired device session, and Mallory a member too. Accepted messages
    come from the `Accepted` stand-in, given to the app's file service."""

    def __init__(self, dbname: str, github):
        app = create_app(make_config(dbname, messaging_enabled=True), github=github)
        self.visible = Accepted()
        self.client = TestClient(app, base_url=BASE)
        self.client.__enter__()
        wait_initialized(self.client)
        wait_messaging(self.client)
        app.state.attachments = AttachmentService(app.state.attachments.storage, self.visible)
        admin = browser_token(self.client, github, ADMIN)
        roster = self.client.get("/api/v1/team/members", headers=self.auth(admin)).json()
        for who in (ALICE, BOB, MALLORY):
            r = self.client.put(f"/api/v1/team/members/{who.id}", headers=self.auth(admin),
                                json={"expected_revision": roster["revision"]})
            assert r.status_code == 200, r.text
            roster["revision"] = r.json()["revision"]
        self.server_id = self.client.get("/version").json()["server_id"]
        self.tokens = {who.id: pair(self.client, browser_token(self.client, github, who))
                       for who in (ALICE, BOB, MALLORY)}
        self.tokens[ADMIN.id] = pair(self.client, admin)
        self.browser = admin

    @staticmethod
    def auth(token: str) -> dict:
        return {"Authorization": f"Bearer {token}"}

    def as_(self, github_id: int) -> dict:
        return self.auth(self.tokens[github_id])

    def upload(self, github_id: int, data: bytes, message_id: uuid.UUID,
               filename: str = "report.pdf", media_type: str = "application/pdf"
               ) -> AttachmentRef:
        ref = AttachmentRef(attachment_id=uuid.uuid4(), filename=filename,
                            media_type=media_type, size_bytes=len(data), sha256=sha(data))
        r = self.client.post("/api/v1/attachments", headers=self.as_(github_id), json={
            "attachment_id": str(ref.attachment_id), "message_id": str(message_id),
            "size_bytes": len(data), "sha256": ref.sha256})
        assert r.status_code == 200, r.text
        # No Content-Length: a chunked body, so the app's 16 KiB gate (which
        # reads only that header) is not what limits it.
        r = self.client.put(f"/api/v1/attachments/{ref.attachment_id}/bytes",
                            headers={**self.as_(github_id),
                                     "Content-Type": "application/octet-stream"},
                            content=(data[i:i + UPLOAD_CHUNK]
                                     for i in range(0, len(data), UPLOAD_CHUNK)))
        assert r.status_code == 200, r.text
        assert r.json()["received_bytes"] == len(data)
        r = self.client.post(f"/api/v1/attachments/{ref.attachment_id}/finalize",
                             headers=self.as_(github_id))
        assert r.status_code == 200 and r.json()["state"] == "verified", r.text
        return ref

    def accept(self, sender: int, recipient: int, refs, message_id):
        storage = self.client.app.state.attachments.storage
        return self.client.portal.call(lambda: accept(
            storage, self.visible, sender=sender, recipient=recipient, refs=refs,
            message_id=message_id, now=clock_mod.now(), server_id=self.server_id))

    def close(self):
        self.client.__exit__(None, None, None)


@pytest.fixture
def served(dbname, github):
    s = Served(dbname, github)
    yield s
    s.close()


def test_routes_upload_publish_and_download_with_inert_headers(served: Served, mongo, dbname):
    data = os.urandom(300 * 1024)                       # far past the 16 KiB gate
    message = uuid.uuid4()
    ref = served.upload(ALICE_ID, data, message, filename="Grüße \"draft\".svg",
                        media_type="image/svg+xml")
    r = served.client.get(f"/api/v1/attachments/{ref.attachment_id}", headers=served.as_(BOB_ID))
    assert (r.status_code, r.json()["error"]["code"]) == (404, "attachment_not_found")
    path = f"/api/v1/messages/{message}/attachments/{ref.attachment_id}/content"
    assert served.client.get(path, headers=served.as_(BOB_ID)).status_code == 404

    served.accept(ALICE_ID, BOB_ID, [ref], message)
    r = served.client.get(path, headers=served.as_(BOB_ID))
    assert r.status_code == 200, r.text
    assert r.content == data
    assert r.headers["content-type"] == "application/octet-stream"   # never the sender's svg
    assert r.headers["content-length"] == str(len(data))
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["content-disposition"] == (
        "attachment; filename=\"Gr__e _draft_.svg\"; "
        "filename*=UTF-8''Gr%C3%BC%C3%9Fe%20%22draft%22.svg")
    assert r.headers["content-security-policy"] == attachments.DOWNLOAD_CSP
    for who in (MALLORY.id, ADMIN.id):
        r = served.client.get(path, headers=served.as_(who))
        assert (r.status_code, r.json()["error"]["code"]) == (404, "attachment_not_found")
    # A browser session is not a device session.
    served.client.cookies.set("__Host-ts_session", served.browser, domain="team.test")
    try:
        r = served.client.get(path)
        assert (r.status_code, r.json()["error"]["code"]) == (403, "device_required")
    finally:
        served.client.cookies.clear()
    # Nothing on disk says where it came from: GridFS holds one file, by attempt.
    assert mongo[dbname][f"{attachment_storage.BUCKET}.files"].count_documents({}) == 1


def test_routes_refuse_a_declared_body_larger_than_the_record(served: Served):
    message = uuid.uuid4()
    attachment = uuid.uuid4()
    r = served.client.post("/api/v1/attachments", headers=served.as_(ALICE_ID), json={
        "attachment_id": str(attachment), "message_id": str(message), "size_bytes": 4,
        "sha256": sha(b"four")})
    assert r.status_code == 200, r.text
    r = served.client.put(f"/api/v1/attachments/{attachment}/bytes",
                          headers=served.as_(ALICE_ID), content=b"five!")
    assert (r.status_code, r.json()["error"]["code"]) == (413, "too_large")
    r = served.client.put(f"/api/v1/attachments/{attachment}/bytes",
                          headers=served.as_(ALICE_ID),
                          content=(c for c in (b"fi", b"ve!")))         # chunked: counted
    assert (r.status_code, r.json()["error"]["code"]) == (413, "too_large")


# -- settings and the body gate ---------------------------------------------------------

def test_settings_may_only_be_lowered():
    defaults = AttachmentSettings.from_env({})
    assert [getattr(defaults, n.removeprefix("TEAM_SERVER_").lower())
            for n in ATTACHMENT_SETTINGS] == list(ATTACHMENT_SETTINGS.values())
    lowered = AttachmentSettings.from_env({"TEAM_SERVER_FILE_MAX_BYTES": "1048576",
                                           "TEAM_SERVER_UPLOAD_STREAMS_MAX": "1"})
    assert (lowered.file_max_bytes, lowered.upload_streams_max) == (1048576, 1)
    for bad in ({"TEAM_SERVER_FILE_MAX_BYTES": str(ATTACHMENT_SETTINGS[
                    "TEAM_SERVER_FILE_MAX_BYTES"] + 1)},
                {"TEAM_SERVER_STAGING_EXPIRY_SEC": "0"},
                {"TEAM_SERVER_UPLOAD_IDLE_TIMEOUT_SEC": "ten"}):
        with pytest.raises(ValueError) as e:
            AttachmentSettings.from_env(bad)
        assert next(iter(bad)) in str(e.value)


def test_only_the_byte_upload_passes_the_small_body_gate():
    attachment = uuid.uuid4()
    assert attachments.is_upload("PUT", f"/api/v1/attachments/{attachment}/bytes")
    for method, path in (("POST", f"/api/v1/attachments/{attachment}/bytes"),
                         ("PUT", f"/api/v1/attachments/{attachment}/bytes/x"),
                         ("PUT", f"/api/v1/attachments/{attachment}"),
                         ("POST", "/api/v1/attachments"),
                         ("POST", "/api/v1/messages"),
                         ("PUT", f"/api/v1/attachments/../messages/bytes")):
        assert not attachments.is_upload(method, path), (method, path)
