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


# -- making it local ------------------------------------------------------------------

def zip_bytes() -> bytes:
    """A plain zip with no package.json: just two members."""
    import io
    import zipfile
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        z.writestr("notes/readme.txt", "synthetic")
        z.writestr("run.sh", "#!/bin/sh\necho ran > ran.txt\n")
    return out.getvalue()


@pytest.mark.asyncio
async def test_ensure_local_downloads_verifies_and_never_unpacks_or_runs():
    w = World()
    archive = zip_bytes()
    message_id, (ref,) = await received(w, [("bundle without manifest.zip", archive)])
    got = await locations(w).ensure_local(SERVER, MATT, message_id, ref.attachment_id)
    assert got.state == "verified" and Path(got.local_path).read_bytes() == archive
    folder = Path(got.local_path).parent
    assert [p.name for p in folder.iterdir()] == ["bundle without manifest.zip"]
    assert not any(data_paths.packages_dir().iterdir()), "nothing unpacked"
    assert not list(data_paths.data_dir().rglob("ran.txt")), "nothing run"
    assert got.extraction is None


@pytest.mark.asyncio
async def test_a_failed_download_says_why_and_a_retry_recovers():
    w = World()
    message_id, (ref,) = await received(w, [("plan.pdf", b"right bytes")])
    w.serve.staging[ref.attachment_id]["bytes"] = b"wrong bytes"
    loc = locations(w)
    got = await loc.ensure_local(SERVER, MATT, message_id, ref.attachment_id)
    assert (got.state, got.reason, got.local_path) == ("failed", "attachment_mismatch", None)
    assert got.actions == ["make_local"] and not Path(got.intended_path).exists()
    w.serve.staging[ref.attachment_id]["bytes"] = b"right bytes"
    assert (await loc.ensure_local(SERVER, MATT, message_id, ref.attachment_id)).state \
        == "verified"


@pytest.mark.asyncio
async def test_signed_out_a_download_fails_with_its_reason_and_no_path():
    from jarvis import messaging_ports as ports
    w = World()
    message_id, (ref,) = await received(w, [("plan.pdf", b"x")])

    def unpaired():
        raise ports.TransportError("session_missing", status=401)
    w.matt.files.transport = unpaired
    got = await locations(w).ensure_local(SERVER, MATT, message_id, ref.attachment_id)
    assert (got.state, got.reason, got.local_path) == ("failed", "session_missing", None)


@pytest.mark.asyncio
async def test_deleted_or_tampered_files_are_downloaded_again_on_request():
    w = World()
    data = os.urandom(4000)
    message_id, (ref,) = await received(w, [("plan.pdf", data)])
    loc = locations(w)
    path = Path((await loc.ensure_local(SERVER, MATT, message_id, ref.attachment_id)).local_path)
    for spoil in (lambda: path.write_bytes(b"tampered"), path.unlink):
        spoil()
        assert loc.lookup(SERVER, MATT, message_id, ref.attachment_id).state in \
            ("changed", "missing")
        again = await loc.ensure_local(SERVER, MATT, message_id, ref.attachment_id)
        assert again.state == "verified" and again.local_path == str(path)
        assert path.read_bytes() == data


@pytest.mark.asyncio
async def test_a_changed_downloads_folder_is_moved_not_claimed(tmp_path):
    w = World()
    message_id, (ref,) = await received(w, [("plan.pdf", b"kept")])
    old = await locations(w).ensure_local(SERVER, MATT, message_id, ref.attachment_id)
    elsewhere = type(w.matt.files)(w.matt.store, lambda: w.matt.transport,
                                   downloads_dir=tmp_path / "new downloads")
    loc = AttachmentLocations(elsewhere, node_id=lambda: NODE)
    got = loc.lookup(SERVER, MATT, message_id, ref.attachment_id)
    assert (got.state, got.local_path, got.previous_path) == ("moved", None, old.local_path)
    fresh = await loc.ensure_local(SERVER, MATT, message_id, ref.attachment_id)
    assert fresh.state == "verified"
    assert fresh.local_path == str(tmp_path / "new downloads" / str(ref.attachment_id)
                                   / "plan.pdf")


@pytest.mark.asyncio
async def test_a_leased_file_is_kept_by_cleanup_until_its_holder_finishes():
    w = World()
    clock = [10_000.0]
    active = {"run:r1": True}
    w.tim.files.now = lambda: clock[0]
    loc = AttachmentLocations(w.tim.files, node_id=lambda: NODE, active=lambda h: active[h],
                              now=lambda: clock[0])
    w.tim.files.held = loc.held
    draft = w.tim.draft(MATT, [])
    ref = await w.tim.attach(draft.message_id, b"abandoned but leased")
    loc.hold(SERVER, TIM, ref.attachment_id, "run:r1")
    clock[0] += 10 * 3600
    assert w.tim.files.cleanup(SERVER, TIM) == 0
    assert w.tim.files.snapshot_path(ref.attachment_id).exists()
    active["run:r1"] = False
    assert w.tim.files.cleanup(SERVER, TIM) == 1
    assert loc.held(SERVER, TIM) == set()


def test_a_lease_holder_is_active_only_while_its_run_or_message_is():
    from jarvis import run_store
    run_store.init_db()
    run_id = run_store.create_run("p", "proj", "/tmp/proj", "voice")
    assert attachment_locations.holder_active(f"run:{run_id}")
    run_store.update_run(run_id, status=run_store.RunStatus.SUCCEEDED)
    assert not attachment_locations.holder_active(f"run:{run_id}")
    number = run_store.create_message("s", "claude", "agent", "proj", "hello")
    assert attachment_locations.holder_active(f"message:{number}")
    run_store.update_message(number, status=run_store.MessageStatus.ANSWERED)
    assert not attachment_locations.holder_active(f"message:{number}")
    assert not attachment_locations.holder_active("someone:else")


@pytest.mark.asyncio
async def test_an_extraction_is_recorded_beside_the_zip_never_instead_of_it(tmp_path):
    w = World()
    message_id, (ref,) = await received(w, [("package.zip", zip_bytes())])
    loc = locations(w)
    zipped = await loc.ensure_local(SERVER, MATT, message_id, ref.attachment_id)
    root = tmp_path / "unpacked"
    root.mkdir()
    loc.record_extraction(SERVER, MATT, message_id, ref.attachment_id, root, ["notes/readme.txt"])
    got = loc.lookup(SERVER, MATT, message_id, ref.attachment_id)
    assert got.local_path == zipped.local_path
    assert got.extraction == attachment_locations.AttachmentExtraction(
        root=str(root), source_sha256=ref.sha256, members=["notes/readme.txt"], root_exists=True)
    assert loc.extracted_from(SERVER, MATT, root) == (message_id, ref.attachment_id)
    assert loc.extracted_from(SERVER, 6666, root) is None
