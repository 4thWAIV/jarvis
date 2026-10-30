"""Files end to end against the real team server's attachment routes and a
real MongoDB/GridFS: JARVIS's FileSnapshots (real SQLite, real private
folders) uploads an approved revision's files over HTTP, the team server
stores and verifies them, publication pins them, and the recipient's
JARVIS downloads and verifies them.

Needs TEAM_SERVER_TEST_MONGODB_URI (the team server's test MongoDB, e.g.
mongodb://127.0.0.1:27099); without it these are skipped, and with it an
unreachable database fails. Each test has its own database, `hm_files_`
and a random suffix, dropped afterwards.

Between the two: the attachment half of a ServeTransport over httpx (the
production one is delivery's messaging_client, through team_client's
streamed request), and a stand-in for the paired session (a bearer token
naming a member), since sign-in and pairing are not what is tested here."""
from __future__ import annotations

import hashlib
import os
import sys
import uuid
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from jarvis import data_paths
from jarvis import messaging_ports as ports
from jarvis import messaging_wire as wire
from jarvis.messaging_files import FileSnapshots
from tests.fixtures import messaging_fakes as fakes

MONGO = os.environ.get("TEAM_SERVER_TEST_MONGODB_URI")
if not MONGO:
    # Before the team server's imports: JARVIS alone does not install pymongo.
    pytest.skip("needs TEAM_SERVER_TEST_MONGODB_URI and the team server's packages",
                allow_module_level=True)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "team_server"))
from teamserver import attachment_storage, attachments, clock as ts_clock  # noqa: E402
from teamserver import db as ts_db  # noqa: E402
from teamserver.errors import Problem  # noqa: E402
from teamserver.sessions import Principal  # noqa: E402

SERVER = "srv-files-live"
TIM, MATT, OUTSIDER = 863470, 1001, 6666
BASE = "https://team.test"


class Accepted:
    def __init__(self):
        self.messages: dict[uuid.UUID, wire.AcceptedMessage] = {}

    async def get_visible(self, server_id, viewer_github_id, message_id):
        m = self.messages.get(message_id)
        if m is None or m.server_id != server_id:
            return None
        return m if viewer_github_id in (m.sender_github_id, m.recipient_github_id) else None


def serve_app(db, visible: Accepted) -> FastAPI:
    """The attachment routes as the team server mounts them, with the
    app's error body; the device session is a bearer token naming a
    member (`member-<github id>`)."""
    app = FastAPI()
    service = attachments.AttachmentService(
        attachment_storage.GridFSAttachmentStorage(db, clock=ts_clock.now), visible)

    async def device(request: Request) -> Principal:
        token = request.headers.get("authorization", "").removeprefix("Bearer ")
        if not token.startswith("member-"):
            raise Problem(401, "session_missing", "Sign in.")
        return Principal(token=token, session={"kind": "device"},
                         team={"server_id": SERVER},
                         member={"github_id": int(token.removeprefix("member-"))})

    @app.exception_handler(Problem)
    async def on_problem(request, exc: Problem):
        return JSONResponse(exc.body(), status_code=exc.status)

    app.include_router(attachments.router(device=device, service=lambda r: service))
    app.state.service = service
    return app


class HttpAttachments:
    """The attachment half of a ServeTransport, over HTTP."""

    def __init__(self, app: FastAPI, github_id: int, cut_after: int | None = None):
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE,
                                        headers={"Authorization": f"Bearer member-{github_id}"})
        self.cut_after = cut_after

    @staticmethod
    def _check(resp: httpx.Response) -> dict:
        if resp.status_code >= 400:
            code = resp.json()["error"]["code"]
            raise ports.TransportError(code, status=resp.status_code)
        return resp.json()

    async def create_attachment(self, request: wire.AttachmentCreate) -> wire.AttachmentStatus:
        return wire.AttachmentStatus.model_validate(self._check(await self.client.post(
            "/api/v1/attachments", json=request.model_dump(mode="json"))))

    async def upload_attachment(self, attachment_id, chunks) -> wire.AttachmentStatus:
        async def body():
            sent = 0
            async for chunk in chunks:
                if self.cut_after is not None and sent >= self.cut_after:
                    self.cut_after = None             # one interrupted attempt
                    raise ports.TransportError("transport")
                sent += len(chunk)
                yield chunk
        return wire.AttachmentStatus.model_validate(self._check(await self.client.put(
            f"/api/v1/attachments/{attachment_id}/bytes", content=body())))

    async def finalize_attachment(self, attachment_id) -> wire.AttachmentStatus:
        return wire.AttachmentStatus.model_validate(self._check(await self.client.post(
            f"/api/v1/attachments/{attachment_id}/finalize")))

    async def attachment_status(self, attachment_id) -> wire.AttachmentStatus:
        return wire.AttachmentStatus.model_validate(self._check(await self.client.get(
            f"/api/v1/attachments/{attachment_id}")))

    async def download_attachment(self, message_id, attachment_id):
        async with self.client.stream(
                "GET", f"/api/v1/messages/{message_id}/attachments/{attachment_id}/content") as r:
            if r.status_code >= 400:
                await r.aread()
                self._check(r)
            async for chunk in r.aiter_bytes():
                yield chunk


@pytest_asyncio.fixture
async def live():
    client = ts_db.connect(MONGO)
    name = "hm_files_" + uuid.uuid4().hex[:12]
    db = ts_db.database(client, name)
    await attachment_storage.init(db)
    visible = Accepted()
    try:
        yield db, visible, serve_app(db, visible)
    finally:
        await client.drop_database(name)
        await client.close()


async def chunks(data: bytes, size: int = 65536):
    for i in range(0, len(data), size):
        yield data[i:i + size]


def jarvis(store, transport) -> FileSnapshots:
    return FileSnapshots(store, lambda: transport, reveal=lambda path: None,
                         quarantine=lambda path: None)


async def publish(db, visible: Accepted, approved: ports.ApprovedSend) -> wire.AcceptedMessage:
    """Acceptance's file steps in the contract's order: pin, insert, publish."""
    req = approved.request
    storage = attachment_storage.GridFSAttachmentStorage(db, clock=ts_clock.now)
    for ref in req.attachments:
        assert await storage.pin(SERVER, approved.owner_github_id, ref, req.message_id,
                                 req.content_digest, ts_clock.now())
    m = wire.AcceptedMessage(**req.model_dump(), server_id=SERVER,
                             sender_github_id=approved.owner_github_id,
                             accepted_at=ts_clock.now())
    visible.messages[m.message_id] = m
    for ref in req.attachments:
        await storage.mark_published(SERVER, ref.attachment_id)
    return m


def receive(store: fakes.MemoryStore, owner: int, m: wire.AcceptedMessage) -> None:
    store.store_page(SERVER, owner, uuid.uuid4(), wire.SyncPage(
        epoch="e1", scan_complete=True, server_time=m.accepted_at, entries=[wire.SyncEntry(
            message=m, receipts=wire.ReceiptFacts(message_id=m.message_id,
                                                  recipient_github_id=m.recipient_github_id,
                                                  revision=1),
            work_state=wire.new_work_state(server_id=SERVER, owner_github_id=owner,
                                           message=m))]))


async def approved_draft(store, files: FileSnapshots, payloads: list[bytes],
                         recipient: int) -> tuple[ports.Draft, ports.ApprovedSend]:
    clock = fakes.FixedClock()
    ids = fakes.SeqIds(start=0x3000)
    guard = fakes.TableGuard(store, ids=ids, clock=clock)
    message_id = uuid.uuid4()
    draft = store.create_draft(SERVER, TIM, ports.Draft(
        message_id=message_id, thread_id=message_id, recipient_github_id=recipient,
        draft_revision=1, send=ports.LocalSend(message_id=message_id, content_revision=1,
                                               state="draft")))
    refs = [await files.snapshot(SERVER, TIM, message_id, f"part {i}.bin", None, chunks(p))
            for i, p in enumerate(payloads)]
    draft = store.save_draft(SERVER, TIM, draft.model_copy(update={"attachments": refs}), 1)
    reviewed = guard.request_review(SERVER, TIM, message_id, draft.draft_revision)
    approved = guard.approve(SERVER, TIM, message_id, 1, reviewed.send.content_digest,
                             uuid.uuid4(), "gui")
    return draft, approved


@pytest.mark.asyncio
async def test_files_travel_from_snapshot_to_verified_download(live):
    db, visible, app = live
    payloads = [os.urandom(3 * 1024 * 1024 + 11), b""]
    tim_store = fakes.MemoryStore()
    tim = jarvis(tim_store, HttpAttachments(app, TIM, cut_after=1024 * 1024))
    draft, approved = await approved_draft(tim_store, tim, payloads, MATT)

    # file-upload-interrupted: the first attempt is cut off mid-way.
    with pytest.raises(ports.TransportError):
        await tim.ensure_uploaded(SERVER, TIM, draft.message_id, 1)
    assert [f.state for f in tim.files(SERVER, TIM, draft.message_id)][0] == "failed"
    status = await app.state.service.status(SERVER, TIM, draft.attachments[0].attachment_id)
    assert status.state == "uploading"
    assert await db[f"{attachment_storage.BUCKET}.chunks"].count_documents({}) == 0

    # The retry sends the same copy, and the server's hash is the snapshot's.
    await tim.ensure_uploaded(SERVER, TIM, draft.message_id, 1)
    assert [f.state for f in tim.files(SERVER, TIM, draft.message_id)] == \
        ["available", "available"]
    storage = app.state.service.storage
    for ref, data in zip(draft.attachments, payloads):
        record = await storage.staging(SERVER, TIM, ref.attachment_id)
        assert (record.state, record.sha256) == ("verified", hashlib.sha256(data).hexdigest())

    m = await publish(db, visible, approved)
    matt_store = fakes.MemoryStore()
    receive(matt_store, MATT, m)
    matt = jarvis(matt_store, HttpAttachments(app, MATT))
    for ref, data in zip(m.attachments, payloads):
        got = await matt.download(SERVER, MATT, m.message_id, ref.attachment_id)
        assert got.state == "verified", got
        path = data_paths.messaging_downloads_dir() / str(ref.attachment_id) / ref.filename
        assert path.read_bytes() == data

    # Someone who is not a participant gets nothing, even holding the record.
    outsider_store = fakes.MemoryStore()
    receive(outsider_store, OUTSIDER, m)
    outsider = jarvis(outsider_store, HttpAttachments(app, OUTSIDER))
    got = await outsider.download(SERVER, OUTSIDER, m.message_id, m.attachments[0].attachment_id)
    assert (got.state, got.failure) == ("failed", "attachment_not_found")


@pytest.mark.asyncio
async def test_corrupt_stored_bytes_never_become_a_local_file(live):
    """download-hash-failure, with the corruption in GridFS itself."""
    db, visible, app = live
    data = os.urandom(500_000)
    tim_store = fakes.MemoryStore()
    tim = jarvis(tim_store, HttpAttachments(app, TIM))
    draft, approved = await approved_draft(tim_store, tim, [data], MATT)
    await tim.ensure_uploaded(SERVER, TIM, draft.message_id, 1)
    m = await publish(db, visible, approved)
    chunk = await db[f"{attachment_storage.BUCKET}.chunks"].find_one({"n": 0})
    flipped = bytes(b ^ 0xFF for b in chunk["data"][:1000]) + chunk["data"][1000:]
    await db[f"{attachment_storage.BUCKET}.chunks"].update_one(
        {"_id": chunk["_id"]}, {"$set": {"data": flipped}})       # same length, other bytes
    matt_store = fakes.MemoryStore()
    receive(matt_store, MATT, m)
    matt = jarvis(matt_store, HttpAttachments(app, MATT))
    ref = m.attachments[0]
    got = await matt.download(SERVER, MATT, m.message_id, ref.attachment_id)
    assert (got.state, got.failure) == ("failed", "attachment_mismatch")
    assert list((data_paths.messaging_downloads_dir() / str(ref.attachment_id)).iterdir()) == []
