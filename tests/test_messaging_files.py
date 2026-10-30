"""Files on this JARVIS: private immutable snapshots, upload of an approved
revision's files, verified downloads, Reveal in Finder, cleanup and the
three page routes. Real SQLite and real folders under the test's own data
directory; the team server is the contract's fake (`FakeServe`), and the
messaging store and approval guard are its in-memory adapters."""
from __future__ import annotations

import asyncio
import hashlib
import os
import stat
import uuid
from pathlib import Path
from urllib.parse import quote

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis import data_paths, messaging_files
from jarvis import messaging_ports as ports
from jarvis import messaging_wire as wire
from jarvis.messaging_files import FileSnapshots, FilesContext, local_files
from tests.fixtures import messaging_fakes as fakes

SERVER = "srv-files-test"
TIM, MATT = 863470, 1001
TIM_NODE = uuid.UUID("00000000-0000-4000-8000-0000000000a1")
MATT_NODE = uuid.UUID("00000000-0000-4000-8000-0000000000b1")


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


async def chunks(data: bytes, size: int = 65536, fail_after: int | None = None):
    for i in range(0, len(data), size):
        if fail_after is not None and i >= fail_after:
            raise ConnectionResetError("the page went away")
        yield data[i:i + size]


class World:
    """One fake team server, Tim's and Matt's JARVIS (a store, a guard and
    a FileSnapshots each), and a clock."""

    def __init__(self, faults: wire.Faults = wire.NO_FAULTS):
        self.clock = fakes.FixedClock()
        self.serve = fakes.FakeServe(server_id=SERVER, members={TIM, MATT}, clock=self.clock,
                                     faults=faults)
        self.revealed: list[Path] = []
        self.quarantined: list[Path] = []
        self.changes = 0
        self.tim = self._node(TIM, TIM_NODE, start=0x1000)
        self.matt = self._node(MATT, MATT_NODE, start=0x2000)

    def _node(self, github_id: int, node_id: uuid.UUID, start: int):
        transport = self.serve.pair(github_id, node_id)
        store = fakes.MemoryStore()
        ids = fakes.SeqIds(start=start)
        guard = fakes.TableGuard(store, ids=ids, clock=self.clock)

        def changed():
            self.changes += 1
        files = FileSnapshots(store, lambda: transport, ids=ids, reveal=self.revealed.append,
                              quarantine=self.quarantined.append, on_change=changed)
        return Node(github_id, node_id, transport, store, guard, files, ids)


class Node:
    def __init__(self, github_id, node_id, transport, store, guard, files, ids):
        self.github_id, self.node_id, self.transport = github_id, node_id, transport
        self.store, self.guard, self.files, self.ids = store, guard, files, ids

    def draft(self, recipient: int, refs: list[wire.AttachmentRef], text: str = "") -> ports.Draft:
        message_id = self.ids.new_id()
        return self.store.create_draft(SERVER, self.github_id, ports.Draft(
            message_id=message_id, thread_id=message_id, recipient_github_id=recipient,
            draft_revision=1, text=text, attachments=refs,
            send=ports.LocalSend(message_id=message_id, content_revision=1, state="draft")))

    async def attach(self, message_id: uuid.UUID, data: bytes, name: str = "plan.pdf",
                     media_type: str = "application/pdf") -> wire.AttachmentRef:
        return await self.files.snapshot(SERVER, self.github_id, message_id, name, media_type,
                                         chunks(data))

    async def new_message(self, recipient: int, files: list[bytes]) -> ports.Draft:
        """A draft whose files are snapshotted for it, then saved with them."""
        message_id = self.ids.new_id()
        draft = self.store.create_draft(SERVER, self.github_id, ports.Draft(
            message_id=message_id, thread_id=message_id, recipient_github_id=recipient,
            draft_revision=1,
            send=ports.LocalSend(message_id=message_id, content_revision=1, state="draft")))
        refs = [await self.attach(message_id, data, name=f"file-{i}.bin")
                for i, data in enumerate(files)]
        saved = draft.model_copy(update={"attachments": self.files.refs(
            SERVER, self.github_id, message_id, [r.attachment_id for r in refs])})
        return self.store.save_draft(SERVER, self.github_id, saved, 1)

    def approve(self, draft: ports.Draft) -> ports.ApprovedSend:
        reviewed = self.guard.request_review(SERVER, self.github_id, draft.message_id,
                                             draft.draft_revision)
        return self.guard.approve(SERVER, self.github_id, draft.message_id, 1,
                                  reviewed.send.content_digest, self.node_id, "gui")

    async def send(self, draft: ports.Draft) -> wire.SendResult:
        """Approve, upload the files, submit, store: delivery's steps, with
        the files' part real."""
        approved = self.approve(draft)
        await self.files.ensure_uploaded(SERVER, self.github_id, draft.message_id, 1)
        result = await self.transport.send(approved.request)
        self.store.record_accepted(SERVER, self.github_id, result)
        return result

    async def sync(self) -> None:
        page = await self.transport.sync(self.node_id, None, wire.SYNC_PAGE_MAX)
        self.store.store_page(SERVER, self.github_id, self.node_id, page)


# -- snapshots ----------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_chosen_file_becomes_a_private_immutable_copy():
    w = World()
    draft = w.tim.draft(MATT, [])
    data = os.urandom(200_000)
    ref = await w.tim.files.snapshot(SERVER, TIM, draft.message_id, "../../Users/tim/\x07Q3 plan.pdf",
                                     "Application/PDF; charset=binary", chunks(data))
    assert (ref.filename, ref.media_type, ref.size_bytes, ref.sha256) == \
        ("Q3 plan.pdf", "application/pdf", len(data), sha(data))
    folder = data_paths.messaging_snapshots_dir()
    copy = folder / str(ref.attachment_id)
    assert copy.read_bytes() == data
    assert stat.S_IMODE(os.stat(copy).st_mode) == 0o600
    w.tim.files.downloads_dir()
    for d in (folder, folder.parent, data_paths.messaging_downloads_dir()):
        assert stat.S_IMODE(os.stat(d).st_mode) == 0o700, d
    assert [p.name for p in folder.iterdir()] == [str(ref.attachment_id)]   # no partial left
    assert w.tim.files.files(SERVER, TIM, draft.message_id) == [
        ports.LocalFile(attachment_id=ref.attachment_id, state="staged")]
    assert w.tim.files.refs(SERVER, TIM, draft.message_id, [ref.attachment_id]) == [ref]


@pytest.mark.asyncio
async def test_a_copy_that_fails_or_is_too_large_leaves_nothing(monkeypatch):
    w = World()
    draft = w.tim.draft(MATT, [])
    with pytest.raises(ConnectionResetError):
        await w.tim.files.snapshot(SERVER, TIM, draft.message_id, "a.bin", None,
                                   chunks(os.urandom(300_000), fail_after=100_000))
    monkeypatch.setattr(wire, "FILE_MAX_BYTES", 1000)
    with pytest.raises(wire.ContractError) as e:
        await w.tim.files.snapshot(SERVER, TIM, draft.message_id, "b.bin", None,
                                   chunks(os.urandom(1001), size=100))
    assert e.value.code == "too_large"
    assert list(data_paths.messaging_snapshots_dir().iterdir()) == []
    assert w.tim.files.files(SERVER, TIM, draft.message_id) == []


@pytest.mark.asyncio
async def test_a_zero_byte_file_is_a_file():
    w = World()
    draft = await w.tim.new_message(MATT, [b""])
    result = await w.tim.send(draft)
    assert result.message.attachments[0].size_bytes == 0
    await w.matt.sync()
    got = await w.matt.files.download(SERVER, MATT, draft.message_id,
                                      draft.attachments[0].attachment_id)
    assert got.state == "verified"


@pytest.mark.asyncio
async def test_a_file_from_another_draft_is_not_this_drafts(tmp_path):
    w = World()
    one, two = w.tim.draft(MATT, []), w.tim.draft(MATT, [])
    ref = await w.tim.attach(one.message_id, b"for one")
    with pytest.raises(ports.SnapshotMissing):
        w.tim.files.refs(SERVER, TIM, two.message_id, [ref.attachment_id])
    with pytest.raises(ports.SnapshotMissing):
        w.tim.files.refs(SERVER, TIM, one.message_id, [uuid.uuid4()])


@pytest.mark.asyncio
async def test_changed_file_after_review_cannot_change_what_is_sent(tmp_path):
    """changed-file-after-review: the original changing is irrelevant (the
    copy is sent); the copy changing is SnapshotMissing, never a silent
    substitute; choosing the file again is a new manifest, so a new digest
    that needs approval again."""
    w = World()
    original = tmp_path / "budget.xlsx"
    original.write_bytes(b"approved numbers")
    draft = w.tim.draft(MATT, [])
    ref = await w.tim.files.snapshot(SERVER, TIM, draft.message_id, original.name, None,
                                     chunks(original.read_bytes()))
    draft = w.tim.store.save_draft(SERVER, TIM, draft.model_copy(update={"attachments": [ref]}),
                                   1)
    approved = w.tim.approve(draft)
    original.write_bytes(b"changed numbers!")              # the owner edits the original
    await w.tim.files.ensure_uploaded(SERVER, TIM, draft.message_id, 1)
    assert w.serve.staging[ref.attachment_id]["bytes"] == b"approved numbers"

    # The private copy itself replaced: refused, and shown as missing.
    (data_paths.messaging_snapshots_dir() / str(ref.attachment_id)).write_bytes(b"other bytes!!!!!")
    with pytest.raises(ports.SnapshotMissing):
        w.tim.files.verified_attachment_manifest(SERVER, TIM, draft.message_id, 1)
    assert w.tim.files.files(SERVER, TIM, draft.message_id)[0].state == "missing"

    # Attached again: another attachment, so another digest than the approved one.
    again = await w.tim.files.snapshot(SERVER, TIM, draft.message_id, original.name, None,
                                       chunks(original.read_bytes()))
    assert again.attachment_id != ref.attachment_id
    changed = ports.draft_content(draft.model_copy(update={"attachments": [again]}))
    assert wire.content_digest(changed, server_id=SERVER, sender_github_id=TIM) != \
        approved.request.content_digest


# -- uploading ------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_an_approved_revisions_files_are_uploaded_once_and_verified():
    w = World()
    payloads = [os.urandom(3 * wire.UPLOAD_CHUNK_BYTES + 5), os.urandom(10)]
    draft = await w.tim.new_message(MATT, payloads)
    w.tim.approve(draft)
    refs = await w.tim.files.ensure_uploaded(SERVER, TIM, draft.message_id, 1)
    assert refs == draft.attachments
    for ref, data in zip(refs, payloads):
        rec = w.serve.staging[ref.attachment_id]
        assert (rec["state"], rec["bytes"], rec["message_id"]) == ("verified", data,
                                                                   draft.message_id)
    assert [f.state for f in w.tim.files.files(SERVER, TIM, draft.message_id)] == \
        ["available", "available"]
    assert [f.bytes_done for f in w.tim.files.files(SERVER, TIM, draft.message_id)] == \
        [len(p) for p in payloads]
    before = {i: dict(r) for i, r in w.serve.staging.items()}
    await w.tim.files.ensure_uploaded(SERVER, TIM, draft.message_id, 1)    # a retry
    assert w.serve.staging == before


@pytest.mark.asyncio
async def test_a_failed_upload_is_shown_and_the_retry_resends_the_same_copy():
    faults = wire.ArmedFaults()
    w = World(faults)
    draft = await w.tim.new_message(MATT, [b"retry me"])
    w.tim.approve(draft)
    faults.arm("server_before_attachment_finalize")
    with pytest.raises(wire.InjectedFault):
        await w.tim.files.ensure_uploaded(SERVER, TIM, draft.message_id, 1)
    assert w.tim.files.files(SERVER, TIM, draft.message_id)[0].state == "failed"
    await w.tim.files.ensure_uploaded(SERVER, TIM, draft.message_id, 1)
    assert w.tim.files.files(SERVER, TIM, draft.message_id)[0].state == "available"


@pytest.mark.asyncio
async def test_a_refusal_is_kept_as_the_files_failure():
    w = World()
    draft = await w.tim.new_message(MATT, [b"conflicted"])
    w.tim.approve(draft)
    ref = draft.attachments[0]
    # The same attachment id already staged under another binding.
    w.serve.staging[ref.attachment_id] = dict(
        owner=TIM, message_id=uuid.uuid4(), size_bytes=1, sha256=sha(b"x"),
        attachment_id=ref.attachment_id, state="uploading", received_bytes=0, bytes=b"",
        pinned_digest=None, expires_at=None)
    with pytest.raises(ports.TransportError) as e:
        await w.tim.files.ensure_uploaded(SERVER, TIM, draft.message_id, 1)
    assert e.value.code == "idempotency_conflict"
    assert w.tim.files.files(SERVER, TIM, draft.message_id)[0] == ports.LocalFile(
        attachment_id=ref.attachment_id, state="failed", failure="idempotency_conflict")


@pytest.mark.asyncio
async def test_at_most_two_uploads_at_once():
    w = World()
    draft = await w.tim.new_message(MATT, [os.urandom(100) for _ in range(5)])
    w.tim.approve(draft)
    inner = w.tim.transport
    live = peak = 0

    class Counting:
        def __getattr__(self, name):
            return getattr(inner, name)

        async def upload_attachment(self, attachment_id, body):
            nonlocal live, peak
            live += 1
            peak = max(peak, live)
            await asyncio.sleep(0.01)
            try:
                return await inner.upload_attachment(attachment_id, body)
            finally:
                live -= 1
    w.tim.files.transport = lambda: Counting()
    await w.tim.files.ensure_uploaded(SERVER, TIM, draft.message_id, 1)
    assert peak == wire.UPLOAD_STREAMS_MAX == 2


# -- receiving ----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_received_file_is_verified_before_it_can_be_revealed():
    w = World()
    data = os.urandom(150_000)
    draft = await w.tim.new_message(MATT, [data])
    await w.tim.send(draft)
    await w.matt.sync()
    ref = draft.attachments[0]
    assert local_files([ref], w.matt.files.files(SERVER, MATT, draft.message_id),
                       "available")[0].state == "available"
    with pytest.raises(wire.ContractError) as e:          # nothing downloaded yet
        w.matt.files.reveal(SERVER, MATT, draft.message_id, ref.attachment_id)
    assert e.value.code == "attachment_not_ready"

    got = await w.matt.files.download(SERVER, MATT, draft.message_id, ref.attachment_id)
    assert (got.state, got.bytes_done) == ("verified", len(data))
    path = data_paths.messaging_downloads_dir() / str(ref.attachment_id) / ref.filename
    assert path.read_bytes() == data
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert [p.name for p in path.parent.iterdir()] == [ref.filename]
    assert [p.parent for p in w.quarantined] == [path.parent], "marked before it is kept"
    assert w.quarantined[0].name.startswith(messaging_files.PARTIAL)
    w.matt.files.reveal(SERVER, MATT, draft.message_id, ref.attachment_id)
    assert w.revealed == [path]
    # Asking again neither fetches nor duplicates anything.
    assert await w.matt.files.download(SERVER, MATT, draft.message_id, ref.attachment_id) == got


def test_the_quarantine_mark_is_the_one_a_browser_download_carries(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(messaging_files.subprocess, "run",
                        lambda argv, **kw: calls.append((argv, kw)))
    monkeypatch.setattr(messaging_files.time, "time", lambda: 0x6543210F)
    messaging_files.quarantine(tmp_path / "f")
    ((argv, kw),) = calls
    assert argv == ["/usr/bin/xattr", "-w", "com.apple.quarantine", "0081;6543210f;JARVIS;",
                    str(tmp_path / "f")]
    assert kw["check"] is True


@pytest.mark.asyncio
async def test_a_file_that_cannot_be_marked_is_not_kept():
    w = World()
    data = os.urandom(5000)
    draft = await w.tim.new_message(MATT, [data])
    await w.tim.send(draft)
    await w.matt.sync()
    ref = draft.attachments[0]

    def refuse(path):
        raise OSError("no xattr")
    w.matt.files.quarantine = refuse
    got = await w.matt.files.download(SERVER, MATT, draft.message_id, ref.attachment_id)
    assert (got.state, got.failure) == ("failed", "quarantine")
    assert list((data_paths.messaging_downloads_dir() / str(ref.attachment_id)).iterdir()) == []


@pytest.mark.asyncio
async def test_download_hash_failure_never_leaves_an_openable_file():
    """download-hash-failure: wrong bytes, short bytes and too many bytes
    all end failed, with nothing in the downloads folder; a later good
    download succeeds."""
    w = World()
    data = os.urandom(5000)
    draft = await w.tim.new_message(MATT, [data])
    await w.tim.send(draft)
    await w.matt.sync()
    ref = draft.attachments[0]
    for corrupt in (bytes(reversed(data)), data[:-1], data + b"!"):
        w.serve.staging[ref.attachment_id]["bytes"] = corrupt
        got = await w.matt.files.download(SERVER, MATT, draft.message_id, ref.attachment_id)
        assert (got.state, got.failure) == ("failed", "attachment_mismatch")
        assert list((data_paths.messaging_downloads_dir() / str(ref.attachment_id)).iterdir()) \
            == []
        with pytest.raises(wire.ContractError):
            w.matt.files.reveal(SERVER, MATT, draft.message_id, ref.attachment_id)
    w.serve.staging[ref.attachment_id]["bytes"] = data
    got = await w.matt.files.download(SERVER, MATT, draft.message_id, ref.attachment_id)
    assert got.state == "verified"
    # A verified file altered on disk afterwards is refused, not revealed.
    path = data_paths.messaging_downloads_dir() / str(ref.attachment_id) / ref.filename
    path.write_bytes(b"tampered")
    with pytest.raises(wire.ContractError) as e:
        w.matt.files.reveal(SERVER, MATT, draft.message_id, ref.attachment_id)
    assert e.value.code == "attachment_mismatch"
    assert w.revealed == []


@pytest.mark.asyncio
async def test_a_cancelled_download_keeps_the_message_and_can_be_retried():
    w = World()
    data = os.urandom(4 * 65536)
    draft = await w.tim.new_message(MATT, [data])
    await w.tim.send(draft)
    await w.matt.sync()
    ref = draft.attachments[0]
    started = asyncio.Event()

    class Slow:
        async def download_attachment(self, message_id, attachment_id):
            yield data[:65536]
            started.set()
            await asyncio.sleep(30)
            yield data[65536:]
    w.matt.files.transport = lambda: Slow()
    task = asyncio.create_task(w.matt.files.download(SERVER, MATT, draft.message_id,
                                                     ref.attachment_id))
    await started.wait()
    assert (await w.matt.files.download(SERVER, MATT, draft.message_id, ref.attachment_id)
            ).state == "downloading"                # not fetched twice
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert w.matt.files.files(SERVER, MATT, draft.message_id)[0].state == "available"
    assert list((data_paths.messaging_downloads_dir() / str(ref.attachment_id)).iterdir()) == []
    assert w.matt.store.message(SERVER, MATT, draft.message_id) is not None
    w.matt.files.transport = lambda: w.matt.transport
    got = await w.matt.files.download(SERVER, MATT, draft.message_id, ref.attachment_id)
    assert got.state == "verified"


@pytest.mark.asyncio
async def test_a_refused_download_is_failed_with_its_code():
    w = World()
    draft = await w.tim.new_message(MATT, [b"secret"])
    await w.tim.send(draft)
    await w.matt.sync()
    ref = draft.attachments[0]
    stranger = fakes.MemoryStore()
    # Someone who holds the message record but is not a participant.
    outsider = FileSnapshots(stranger, lambda: w.serve.pair(6666, uuid.uuid4()))
    stranger.messages = {(SERVER, 6666, k[2]): v for k, v in w.matt.store.messages.items()}
    stranger.receipt_facts = {(SERVER, 6666, k[2]): v
                              for k, v in w.matt.store.receipt_facts.items()}
    got = await outsider.download(SERVER, 6666, draft.message_id, ref.attachment_id)
    assert (got.state, got.failure) == ("failed", "attachment_not_found")
    with pytest.raises(wire.ContractError):     # a message this JARVIS does not hold
        await w.matt.files.download(SERVER, MATT, uuid.uuid4(), ref.attachment_id)


@pytest.mark.asyncio
async def test_a_message_to_oneself_carries_its_files_both_ways():
    """self-one-node with a file: sent from the copy, received back through
    the team server, verified as a download."""
    w = World()
    data = os.urandom(70_000)
    draft = await w.tim.new_message(TIM, [data])
    await w.tim.send(draft)
    ref = draft.attachments[0]
    assert w.tim.files.files(SERVER, TIM, draft.message_id)[0].state == "available"
    got = await w.tim.files.download(SERVER, TIM, draft.message_id, ref.attachment_id)
    assert got.state == "verified"
    assert (data_paths.messaging_downloads_dir() / str(ref.attachment_id) /
            ref.filename).read_bytes() == data
    assert w.tim.files.files(SERVER, TIM, draft.message_id) == [got]


# -- cleanup ---------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cleanup_keeps_what_a_draft_or_pending_send_names(monkeypatch):
    w = World()
    now = [1_000_000.0]
    w.tim.files.now = lambda: now[0]
    pending = await w.tim.new_message(MATT, [b"approved, not yet sent"])
    w.tim.approve(pending)
    editing = await w.tim.new_message(MATT, [b"still being written"])
    abandoned = w.tim.draft(MATT, [])
    lost = await w.tim.attach(abandoned.message_id, b"never saved into the draft")
    w.tim.store.discard_draft(SERVER, TIM, abandoned.message_id, 1)
    sent = await w.tim.new_message(MATT, [b"accepted already"])
    await w.tim.send(sent)
    partial = data_paths.messaging_snapshots_dir() / f"{messaging_files.PARTIAL}{uuid.uuid4()}"
    partial.write_bytes(b"half")
    os.utime(partial, (now[0] - messaging_files.PARTIAL_AFTER_SEC - 1,) * 2)

    assert w.tim.files.cleanup(SERVER, TIM) == 1                 # the grace period holds
    assert not partial.exists()
    now[0] += messaging_files.SNAPSHOT_GRACE_SEC + 1
    assert w.tim.files.cleanup(SERVER, TIM) == 2                 # abandoned and sent
    left = {p.name for p in data_paths.messaging_snapshots_dir().iterdir()}
    assert left == {str(pending.attachments[0].attachment_id),
                    str(editing.attachments[0].attachment_id)}
    assert str(lost.attachment_id) not in left
    # The sent message's file still shows: it is on the team server.
    assert local_files(sent.attachments, w.tim.files.files(SERVER, TIM, sent.message_id),
                       "available")[0].state == "available"


# -- the page's routes ------------------------------------------------------------------------

def test_the_page_routes():
    w = World()
    draft = w.tim.draft(MATT, [])
    available = {"on": True}
    app = FastAPI()
    app.include_router(messaging_files.router(
        lambda: FilesContext(w.tim.files, SERVER, TIM) if available["on"] else None))
    client = TestClient(app)
    data = os.urandom(100_000)
    r = client.post(f"/api/team/messages/drafts/{draft.message_id}/files", content=data,
                    headers={"X-Filename": quote("Grüße/../über.png"),
                             "Content-Type": "image/png"})
    assert r.status_code == 201, r.text
    added = ports.LocalFile.model_validate(r.json())
    assert added.state == "staged"
    ref = w.tim.files.refs(SERVER, TIM, draft.message_id, [added.attachment_id])[0]
    assert (ref.filename, ref.media_type, ref.size_bytes) == ("über.png", "image/png", len(data))

    r = client.post(f"/api/team/messages/drafts/{uuid.uuid4()}/files", content=b"x")
    assert (r.status_code, r.json()["error"]["code"]) == (404, "draft_not_found")
    r = client.post(f"/api/team/messages/drafts/{draft.message_id}/files", content=b"x",
                    headers={"Content-Length": str(wire.FILE_MAX_BYTES + 1)})
    assert (r.status_code, r.json()["error"]["code"]) == (413, "too_large")

    sent = asyncio.run(w.tim.new_message(MATT, [data]))
    asyncio.run(w.tim.send(sent))
    asyncio.run(w.matt.sync())
    att = sent.attachments[0].attachment_id
    app2 = FastAPI()
    app2.include_router(messaging_files.router(lambda: FilesContext(w.matt.files, SERVER, MATT)))
    matt = TestClient(app2)
    r = matt.post(f"/api/team/messages/{sent.message_id}/files/{att}/reveal")
    assert (r.status_code, r.json()["error"]["code"]) == (409, "attachment_not_ready")
    r = matt.post(f"/api/team/messages/{sent.message_id}/files/{att}/download")
    assert r.status_code == 200 and r.json()["state"] == "verified", r.text
    r = matt.post(f"/api/team/messages/{sent.message_id}/files/{att}/reveal")
    assert r.status_code == 204
    assert w.revealed == [data_paths.messaging_downloads_dir() / str(att) /
                          sent.attachments[0].filename]

    available["on"] = False
    r = client.post(f"/api/team/messages/drafts/{draft.message_id}/files", content=b"x")
    assert (r.status_code, r.json()["error"]["code"]) == (503, "messaging_unavailable")


def test_the_production_snapshots_satisfy_the_interface():
    files = FileSnapshots(fakes.MemoryStore(), lambda: None)
    assert isinstance(files, ports.AttachmentSnapshots)
