"""A received file downloaded and found again on Windows: the private
folder and held handle of private_files.py, the size and SHA-256 checked
before the file is published, the Mark of the Web kept on it, unsafe
targets (a junction on the way or at the name, a device name, a stream
name) refused before anything is fetched or written, and every failure
leaving no partial file and a state to fetch again from. Real folders and
real Windows handles; the team server is a stand-in that yields bytes."""
from __future__ import annotations

import asyncio
import hashlib
import os
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

_winapi = pytest.importorskip("_winapi", reason="Windows' own file handling")

from jarvis import messaging_files, private_files
from jarvis import messaging_ports as ports
from jarvis import messaging_wire as wire
from jarvis.attachment_locations import AttachmentLocations, Verifier
from jarvis.messaging_files import FileSnapshots

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows' own file handling")

SERVER, OWNER = "srv-windows-test", 1001
MESSAGE = uuid.UUID("00000000-0000-4000-8000-00000000c001")
NODE = uuid.UUID("00000000-0000-4000-8000-0000000000b1")


class Serve:
    """The team server's download: `data`, in chunks, after raising the
    next of `failures` if any are left. `calls` counts the requests. With
    a `gate`, it stops after the first chunk until the gate is set, and
    sets `started` once it is there: a fetch that is live."""

    def __init__(self, data: bytes):
        self.data, self.failures, self.calls = data, [], 0
        self.gate: asyncio.Event | None = None
        self.started = asyncio.Event()

    async def download_attachment(self, message_id, attachment_id):
        self.calls += 1
        if self.failures:
            raise self.failures.pop(0)
        for i in range(0, len(self.data), 65536):
            yield self.data[i:i + 65536]
            if self.gate is not None and i == 0:
                self.started.set()
                await self.gate.wait()


class Store:
    """The one message this owner holds, with its manifest."""

    def __init__(self, refs: list[wire.AttachmentRef]):
        self.held = SimpleNamespace(message=SimpleNamespace(attachments=refs))

    def message(self, server_id, owner, message_id):
        return self.held if (server_id, owner, message_id) == (SERVER, OWNER, MESSAGE) else None


class World:
    def __init__(self, tmp_path: Path, data: bytes, filename: str = "plan.pdf",
                 sent: bytes | None = None):
        self.ref = wire.AttachmentRef(attachment_id=uuid.uuid4(), filename=filename,
                                      media_type="application/pdf", size_bytes=len(data),
                                      sha256=hashlib.sha256(data).hexdigest())
        self.data = data
        self.serve = Serve(data if sent is None else sent)
        self.root = tmp_path / "downloads"
        self.files = FileSnapshots(Store([self.ref]), lambda: self.serve,
                                   downloads_dir=self.root)
        self.locations = AttachmentLocations(self.files, node_id=lambda: str(NODE))

    @property
    def folder(self) -> Path:
        return self.root / str(self.ref.attachment_id)

    @property
    def destination(self) -> Path:
        return self.folder / self.ref.filename

    async def download(self) -> ports.LocalFile:
        return await self.files.download(SERVER, OWNER, MESSAGE, self.ref.attachment_id)

    def lookup(self):
        return self.locations.lookup(SERVER, OWNER, MESSAGE, self.ref.attachment_id)

    def partials(self) -> list[Path]:
        return list(self.folder.glob(messaging_files.PARTIAL + "*")) if self.folder.is_dir() \
            else []


def _zone(path: Path) -> bytes:
    with open(f"{path}:{messaging_files.ZONE_STREAM}", "rb") as stream:
        return stream.read()


# -- success ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_download_is_verified_marked_and_found_again(tmp_path):
    w = World(tmp_path, os.urandom(3 * 65536 + 17))
    got = await w.download()
    assert (got.state, got.bytes_done, got.failure) == ("verified", len(w.data), None)
    assert w.destination.read_bytes() == w.data
    assert b"ZoneId=3" in _zone(w.destination)
    assert w.partials() == []
    found = w.lookup()
    assert (found.state, found.local_path) == ("verified", str(w.destination))
    assert found.actions == ["reveal", "copy_path"]
    assert messaging_files.file_digest(w.destination) == (len(w.data), w.ref.sha256)
    assert await w.files.downloaded(SERVER, OWNER, MESSAGE, w.ref.attachment_id) == w.destination
    assert w.serve.calls == 1                      # a verified file is not fetched again


@pytest.mark.asyncio
async def test_ensure_local_downloads_then_finds_it_verified(tmp_path):
    w = World(tmp_path, b"hello, windows")
    assert w.lookup().state == "remote_only"
    found = await w.locations.ensure_local(SERVER, OWNER, MESSAGE, w.ref.attachment_id)
    assert (found.state, found.local_path) == ("verified", str(w.destination))


@pytest.mark.asyncio
async def test_a_zero_byte_file_is_a_file(tmp_path):
    w = World(tmp_path, b"")
    assert (await w.download()).state == "verified"
    assert w.destination.read_bytes() == b"" and b"ZoneId=3" in _zone(w.destination)


# -- wrong bytes -------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("sent", [b"Y" * 1000, b"X" * 999, b"X" * 1001])
async def test_wrong_bytes_leave_no_file_and_a_failure(tmp_path, sent):
    w = World(tmp_path, b"X" * 1000, sent=sent)
    got = await w.download()
    assert (got.state, got.failure, got.bytes_done) == ("failed", "attachment_mismatch", 0)
    assert not w.destination.exists() and w.partials() == []
    found = w.lookup()
    assert (found.state, found.reason, found.local_path) == ("failed", "attachment_mismatch",
                                                             None)
    with pytest.raises(wire.ContractError):
        w.files.verified_path(SERVER, OWNER, MESSAGE, w.ref.attachment_id)


# -- unsafe targets ------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("where", ["attachment folder", "downloads folder"])
async def test_a_junction_on_the_way_is_refused_and_its_target_untouched(tmp_path, where):
    w = World(tmp_path, b"payload")
    outside = tmp_path / "outside"
    outside.mkdir()
    if where == "downloads folder":
        _winapi.CreateJunction(str(outside), str(w.root))
    else:
        w.root.mkdir()
        _winapi.CreateJunction(str(outside), str(w.folder))
    got = await w.download()
    assert (got.state, got.failure) == ("failed", "unsafe_location")
    assert list(outside.iterdir()) == []
    assert w.serve.calls == 0                     # nothing fetched for nowhere safe
    assert w.lookup().local_path is None


@pytest.mark.asyncio
async def test_a_junction_planted_at_the_name_is_never_followed_or_replaced(tmp_path):
    w = World(tmp_path, b"payload")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep").write_bytes(b"theirs")
    w.files.downloads_dir()
    messaging_files.private_dir(w.folder)
    _winapi.CreateJunction(str(outside), str(w.destination))
    got = await w.download()
    assert (got.state, got.failure) == ("failed", "unsafe_location")
    assert sorted(p.name for p in outside.iterdir()) == ["keep"]
    assert (outside / "keep").read_bytes() == b"theirs"
    assert w.partials() == []
    assert messaging_files.file_digest(w.destination) is None
    assert w.lookup().local_path is None


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["CON", "nul.txt", "COM1.pdf", "lpt9", "AUX .log", "PRN"])
async def test_a_device_name_is_refused_before_anything_is_written(tmp_path, name):
    w = World(tmp_path, b"payload", filename=name)
    got = await w.download()
    assert (got.state, got.failure) == ("failed", "unsafe_name")
    assert w.serve.calls == 0 and not w.root.exists()
    assert w.lookup().local_path is None


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["report.pdf:hidden", "report.pdf:Zone.Identifier",
                                  "a.txt::$DATA", "trailing."])
async def test_a_stream_name_is_refused_before_anything_is_written(tmp_path, name):
    w = World(tmp_path, b"payload", filename=name)
    got = await w.download()
    assert (got.state, got.failure) == ("failed", "unsafe_name")
    assert w.serve.calls == 0 and not w.root.exists()
    assert messaging_files.file_digest(w.destination) is None


# -- retrying ------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_failed_download_leaves_nothing_and_a_retry_recovers(tmp_path):
    w = World(tmp_path, os.urandom(200_000))
    w.serve.failures.append(ports.TransportError("transport"))
    got = await w.download()
    assert (got.state, got.failure) == ("failed", "transport")
    assert w.partials() == [] and not w.destination.exists()
    found = w.lookup()
    assert (found.state, found.actions) == ("failed", ["make_local"])
    got = await w.download()
    assert got.state == "verified" and w.destination.read_bytes() == w.data
    assert w.lookup().state == "verified" and w.partials() == []


@pytest.mark.asyncio
async def test_damaged_bytes_are_fetched_again_and_verified(tmp_path):
    w = World(tmp_path, b"the real bytes", sent=b"the fake bytes")
    assert (await w.download()).failure == "attachment_mismatch"
    w.serve.data = w.data
    assert (await w.download()).state == "verified"
    assert w.destination.read_bytes() == w.data


@pytest.mark.asyncio
async def test_downloaded_retries_a_transient_failure_itself(tmp_path):
    w = World(tmp_path, b"retried")
    w.serve.failures.append(ports.TransportError("transport"))

    async def no_wait(_):
        return None
    w.files.sleep = no_wait
    assert await w.files.downloaded(SERVER, OWNER, MESSAGE, w.ref.attachment_id) == w.destination
    assert w.serve.calls == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["refused", "wrong mark"])
async def test_a_file_that_cannot_be_marked_keeps_nothing(tmp_path, monkeypatch, fault):
    real = private_files.PartialFile.add_stream

    def broken(self, stream, data):
        if fault == "refused":
            raise PermissionError("no mark")
        real(self, stream, b"[ZoneTransfer]\r\nZoneId=0\r\n")
    monkeypatch.setattr(private_files.PartialFile, "add_stream", broken)
    w = World(tmp_path, b"payload")
    got = await w.download()
    assert (got.state, got.failure) == ("failed", "quarantine")
    assert w.partials() == [] and not w.destination.exists()
    monkeypatch.setattr(private_files.PartialFile, "add_stream", real)
    assert (await w.download()).state == "verified"
    assert _zone(w.destination) == messaging_files.ZONE_MARK


# -- what is already there -------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_verified_file_already_there_is_never_overwritten(tmp_path):
    w = World(tmp_path, b"first copy")
    assert (await w.download()).state == "verified"
    before = os.stat(w.destination)
    # The record lost (a failed look elsewhere): a fetch again finds the
    # verified copy at its name and keeps it rather than replacing it.
    w.files._set(SERVER, OWNER, w.ref.attachment_id, messaging_files.DOWNLOAD, state="failed",
                 failure="transport")
    assert (await w.download()).state == "verified"
    after = os.stat(w.destination)
    assert (after.st_ino, after.st_mtime_ns) == (before.st_ino, before.st_mtime_ns)
    assert w.serve.calls == 2 and w.partials() == []
    assert w.destination.read_bytes() == w.data


@pytest.mark.asyncio
async def test_a_changed_file_is_fetched_again_as_on_macos(tmp_path):
    w = World(tmp_path, b"the original")
    await w.download()
    w.destination.write_bytes(b"tampered!!!!")
    assert w.lookup().state == "changed"
    found = await w.locations.ensure_local(SERVER, OWNER, MESSAGE, w.ref.attachment_id)
    assert found.state == "verified" and w.destination.read_bytes() == w.data
    assert b"ZoneId=3" in _zone(w.destination)


# -- a same-size edit with its time put back ---------------------------------------------

def _edit_keeping_size_and_time(path: Path, data: bytes) -> None:
    """Overwrite `path` in place with `data` (the same length) and put its
    access and modification times back as they were."""
    before = os.stat(path)
    assert len(data) == before.st_size
    with open(path, "r+b") as f:
        f.write(data)
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
    after = os.stat(path)
    assert (after.st_size, after.st_mtime_ns) == (before.st_size, before.st_mtime_ns)


@pytest.mark.asyncio
async def test_a_same_size_edit_with_its_time_put_back_is_not_verified(tmp_path):
    w = World(tmp_path, b"A" * 5000)
    await w.download()
    assert w.lookup().state == "verified"          # looked at once, so a cache would have it
    _edit_keeping_size_and_time(w.destination, b"B" * 5000)
    found = w.lookup()
    assert (found.state, found.local_path, found.reason) == ("changed", None, "changed")
    with pytest.raises(wire.ContractError):
        w.files.verified_path(SERVER, OWNER, MESSAGE, w.ref.attachment_id)
    found = await w.locations.ensure_local(SERVER, OWNER, MESSAGE, w.ref.attachment_id)
    assert found.state == "verified" and w.destination.read_bytes() == w.data
    assert w.serve.calls == 2


def test_the_verifier_hashes_again_whatever_the_times_say(tmp_path):
    path = tmp_path / "f.bin"
    path.write_bytes(b"one!")
    verify = Verifier()
    assert verify.digest(path) == (4, hashlib.sha256(b"one!").hexdigest())
    _edit_keeping_size_and_time(path, b"two!")
    assert verify.digest(path) == (4, hashlib.sha256(b"two!").hexdigest())


# -- the Mark of the Web ------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["removed", "trusted zone"])
async def test_a_copy_without_its_mark_is_not_verified(tmp_path, change):
    w = World(tmp_path, b"marked bytes")
    await w.download()
    stream = f"{w.destination}:{messaging_files.ZONE_STREAM}"
    if change == "removed":
        os.remove(stream)
    else:
        with open(stream, "wb") as f:
            f.write(b"[ZoneTransfer]\r\nZoneId=0\r\n")
    assert w.destination.read_bytes() == w.data    # the bytes still match
    found = w.lookup()
    assert (found.state, found.local_path) == ("changed", None)
    assert messaging_files.download_digest(w.destination) is None
    with pytest.raises(wire.ContractError) as e:
        w.files.verified_path(SERVER, OWNER, MESSAGE, w.ref.attachment_id)
    assert e.value.code == "attachment_mismatch"


@pytest.mark.asyncio
async def test_an_unmarked_copy_is_fetched_again_and_replaced_by_a_marked_one(tmp_path):
    w = World(tmp_path, b"marked bytes")
    await w.download()
    os.remove(f"{w.destination}:{messaging_files.ZONE_STREAM}")
    # The record still says verified; the copy is not kept on its hash alone.
    got = await w.download()
    assert got.state == "verified" and w.serve.calls == 2
    assert _zone(w.destination) == messaging_files.ZONE_MARK
    assert w.destination.read_bytes() == w.data and w.partials() == []
    assert w.lookup().state == "verified"


@pytest.mark.asyncio
async def test_an_unmarked_copy_at_the_name_is_not_kept_when_fetching(tmp_path):
    """The record lost and a matching copy at the name with no mark: the
    fresh, marked copy takes its place rather than the unmarked one kept."""
    w = World(tmp_path, b"marked bytes")
    await w.download()
    os.remove(f"{w.destination}:{messaging_files.ZONE_STREAM}")
    w.files._set(SERVER, OWNER, w.ref.attachment_id, messaging_files.DOWNLOAD, state="failed",
                 failure="transport")
    assert (await w.download()).state == "verified"
    assert _zone(w.destination) == messaging_files.ZONE_MARK


# -- a fetch that died, and one that is live ----------------------------------------------------

@pytest.mark.asyncio
async def test_a_row_left_downloading_is_recovered_by_a_retry(tmp_path):
    """A fetch that died with the process: its row says downloading and no
    fetch here owns it. The look says it can be fetched again, and the
    retry does."""
    w = World(tmp_path, os.urandom(100_000))
    w.files._insert(SERVER, OWNER, w.ref, MESSAGE, messaging_files.DOWNLOAD, "downloading")
    found = w.lookup()
    assert (found.state, found.reason, found.actions) == ("failed", "interrupted",
                                                          ["make_local"])
    found = await w.locations.ensure_local(SERVER, OWNER, MESSAGE, w.ref.attachment_id)
    assert (found.state, found.local_path) == ("verified", str(w.destination))
    assert w.serve.calls == 1


@pytest.mark.asyncio
async def test_a_fetch_that_dies_unexpectedly_is_not_left_downloading(tmp_path, monkeypatch):
    w = World(tmp_path, b"payload")

    async def dies(*args, **kw):
        raise RuntimeError("the worker died")
    with monkeypatch.context() as patch:
        patch.setattr(w.files, "_fetch", dies)
        with pytest.raises(RuntimeError):
            await w.download()
        known, _ = w.files.download_record(SERVER, OWNER, w.ref.attachment_id)
        assert (known.state, known.failure) == ("failed", "interrupted")
        assert not w.files.fetching(SERVER, OWNER, w.ref.attachment_id)
    assert (await w.download()).state == "verified"
    assert w.serve.calls == 1


@pytest.mark.asyncio
async def test_a_live_fetch_is_never_started_twice(tmp_path):
    w = World(tmp_path, os.urandom(3 * 65536))
    w.serve.gate = asyncio.Event()
    first = asyncio.create_task(w.download())
    await asyncio.wait_for(w.serve.started.wait(), 10)
    assert w.files.fetching(SERVER, OWNER, w.ref.attachment_id)
    assert w.lookup().state == "downloading"       # live: not offered again
    again = await w.download()
    assert again.state == "downloading"
    found = await w.locations.ensure_local(SERVER, OWNER, MESSAGE, w.ref.attachment_id)
    assert found.state == "downloading"
    assert w.serve.calls == 1
    w.serve.gate.set()
    assert (await asyncio.wait_for(first, 10)).state == "verified"
    assert w.serve.calls == 1 and w.lookup().state == "verified"
