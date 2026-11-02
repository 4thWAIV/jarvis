"""Where a received file is: the shared resolver (attachment_locations.py)
over the real messaging-files service, real SQLite and real folders under
the test's own data directory, with the contract's fake team server
(`tests.test_messaging_files.World`). Synthetic bytes only."""
from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest

from jarvis import attachment_locations, data_paths
from jarvis import messaging_wire as wire
from jarvis.attachment_locations import AttachmentLocations
from tests.test_messaging_files import MATT, SERVER, TIM, World, chunks

NODE = "00000000-0000-4000-8000-00000000c0de"


async def received(w: World, files: list[tuple[str, bytes]]):
    """Tim sends Matt a message with these files; Matt's JARVIS syncs it.
    (the message id, its refs)."""
    message_id = w.tim.ids.new_id()
    draft = w.tim.store.create_draft(SERVER, TIM, _draft(message_id))
    refs = [await w.tim.files.snapshot(SERVER, TIM, message_id, name, "application/pdf",
                                       chunks(data)) for name, data in files]
    saved = draft.model_copy(update={"attachments": w.tim.files.refs(
        SERVER, TIM, message_id, [r.attachment_id for r in refs])})
    await w.tim.send(w.tim.store.save_draft(SERVER, TIM, saved, 1))
    await w.matt.sync()
    return message_id, refs


def _draft(message_id):
    from jarvis import messaging_ports as ports
    return ports.Draft(message_id=message_id, thread_id=message_id, recipient_github_id=MATT,
                       draft_revision=1,
                       send=ports.LocalSend(message_id=message_id, content_revision=1,
                                            state="draft"))


def locations(w: World) -> AttachmentLocations:
    return AttachmentLocations(w.matt.files, node_id=lambda: NODE)


# -- the contract ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_file_not_downloaded_has_only_an_intended_path():
    w = World()
    message_id, (ref,) = await received(w, [("plan.pdf", b"%PDF synthetic")])
    got = locations(w).lookup(SERVER, MATT, message_id, ref.attachment_id)
    assert (got.state, got.local_path, got.reason, got.actions) == \
        ("remote_only", None, "not_downloaded", ["make_local"])
    assert got.intended_path == str(data_paths.messaging_downloads_dir()
                                    / str(ref.attachment_id) / "plan.pdf")
    assert not Path(got.intended_path).exists(), "an intended path is never a saved file"
    assert (got.node_id, got.size_bytes, got.sha256, got.filename) == \
        (uuid.UUID(NODE), ref.size_bytes, ref.sha256, "plan.pdf")


@pytest.mark.asyncio
async def test_two_files_with_one_name_resolve_by_their_ids():
    w = World()
    message_id, refs = await received(w, [("report.pdf", b"first"), ("report.pdf", b"second")])
    loc = locations(w)
    for ref in refs:
        await w.matt.files.download(SERVER, MATT, message_id, ref.attachment_id)
    first, second = (loc.lookup(SERVER, MATT, message_id, r.attachment_id) for r in refs)
    assert first.state == second.state == "verified"
    assert first.local_path != second.local_path
    assert Path(first.local_path).read_bytes() == b"first"
    assert Path(second.local_path).read_bytes() == b"second"
    assert first.actions == ["reveal", "copy_path"] and first.verified_at is not None


@pytest.mark.asyncio
async def test_spaces_and_unicode_in_names_and_the_data_root(monkeypatch, tmp_path):
    root = tmp_path / "Données de JARVIS" / "my data"
    monkeypatch.setenv("JARVIS_DATA_DIR", str(root))
    w = World()
    name = "Résumé — Q3 plan 2026.pdf"
    message_id, (ref,) = await received(w, [(name, b"bytes with spaces")])
    await w.matt.files.download(SERVER, MATT, message_id, ref.attachment_id)
    got = locations(w).lookup(SERVER, MATT, message_id, ref.attachment_id)
    assert got.state == "verified" and got.filename == name
    assert got.local_path == str(root / "messaging" / "downloads" / str(ref.attachment_id) / name)
    assert Path(got.local_path).read_bytes() == b"bytes with spaces"


@pytest.mark.asyncio
async def test_another_owner_never_sees_the_file():
    w = World()
    message_id, (ref,) = await received(w, [("plan.pdf", b"private")])
    await w.matt.files.download(SERVER, MATT, message_id, ref.attachment_id)
    loc = locations(w)
    for server, owner in ((SERVER, 6666), ("another-server", MATT)):
        with pytest.raises(wire.ContractError) as e:
            loc.lookup(server, owner, message_id, ref.attachment_id)
        assert e.value.code == "attachment_not_found"
    with pytest.raises(wire.ContractError):
        loc.lookup(SERVER, MATT, uuid.uuid4(), ref.attachment_id)


@pytest.mark.asyncio
async def test_after_a_restart_the_same_location_is_given():
    w = World()
    message_id, (ref,) = await received(w, [("plan.pdf", os.urandom(3000))])
    await w.matt.files.download(SERVER, MATT, message_id, ref.attachment_id)
    before = locations(w).lookup(SERVER, MATT, message_id, ref.attachment_id)
    again = attachment_locations.AttachmentLocations(
        type(w.matt.files)(w.matt.store, lambda: w.matt.transport), node_id=lambda: NODE)
    after = again.lookup(SERVER, MATT, message_id, ref.attachment_id)
    assert after == before and after.state == "verified"


@pytest.mark.asyncio
async def test_deleted_and_tampered_files_are_never_claimed():
    w = World()
    message_id, (ref,) = await received(w, [("plan.pdf", b"original bytes")])
    await w.matt.files.download(SERVER, MATT, message_id, ref.attachment_id)
    loc = locations(w)
    path = Path(loc.lookup(SERVER, MATT, message_id, ref.attachment_id).local_path)
    path.write_bytes(b"tampered")
    got = loc.lookup(SERVER, MATT, message_id, ref.attachment_id)
    assert (got.state, got.local_path, got.actions) == ("changed", None, ["make_local"])
    path.unlink()
    got = loc.lookup(SERVER, MATT, message_id, ref.attachment_id)
    assert (got.state, got.local_path, got.intended_path) == ("missing", None, str(path))
