"""Files on this Mac attached to a team-message draft by the brain
(`jarvis/draft_files.py`, `MessagingTools.attach_message_files` and
`draft_message`'s `files`): which files may be read, the exact bytes copied
at a new revision, all or none, bound to the sign-in and the revision it
began under, review required again, and the tool gated and awaited by the
server. The runtime is the real one over a team connection that answers
nothing (tests/test_messaging_signed_in.py), so nothing here sends, uploads
or reaches a team server; the one delivery is to oneself through the
contract's fake team server. Every file is a harmless fixture under
tmp_path, and each test has its own JARVIS_DATA_DIR (conftest)."""
from __future__ import annotations

import asyncio
import hashlib
import os
import stat
import time
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import pytest

from jarvis import data_paths, draft_files, messaging_api, messaging_files
from jarvis import messaging_ports as ports
from jarvis import messaging_wire as wire
from jarvis.messaging_client import Pairing
from jarvis.messaging_wire import CAPABILITY
from tests.test_brain_page_parity import _Brain, wired  # noqa: F401  (the fixture)
from tests.test_messaging_signed_in import SERVER, Connection, runtime


# -- the runtime, signed in and answering nothing -----------------------------------------

class Recording(Connection):
    """Signed in; every team server call is recorded (and unanswered)."""

    def __init__(self):
        super().__init__(signed_in=True)
        self.calls: list[tuple] = []

    async def call(self, method, path, body=None):
        self.calls.append((method, path))
        return await super().call(method, path, body)


class Captures:
    def __init__(self):
        self.opened, self.closed = 0, []

    def open_for_review(self, draft, readback, recipient_label) -> int:
        self.opened += 1
        return self.opened

    def close(self, capture_id) -> None:
        self.closed.append(capture_id)


def signed_in():
    connection = Recording()
    rt, frames = runtime(connection)
    rt.delivery._capabilities[SERVER] = (1, time.monotonic(), [CAPABILITY])
    rt.reviews.captures = Captures()
    return SimpleNamespace(rt=rt, connection=connection, frames=frames,
                           captures=rt.reviews.captures)


def ns(rt):
    return rt.identity().ns


def draft_of(rt, message_id) -> ports.Draft:
    return rt.store.draft(*ns(rt), UUID(str(message_id)))


async def new_draft(rt, text="Here it is.", **more) -> ports.Draft:
    out = await rt.tools.draft_message({"to": "Me", "text": text, **more})
    assert "is saved" in out, out
    return draft_of(rt, out.split()[1])


async def attach(rt, draft, paths, revision=None) -> str:
    return await rt.tools.attach_message_files({
        "message": str(draft.message_id),
        "draft_revision": draft.draft_revision if revision is None else revision,
        "paths": [str(p) for p in paths]})


async def chunks(data: bytes):
    yield data


async def page_adds(rt, draft, name: str, data: bytes) -> ports.Draft:
    """What the page does: upload the file, then save the draft naming it."""
    ref = await rt.files.add_to_draft(*ns(rt), draft.message_id, name, None, chunks(data))
    rt.local.write_draft(draft.message_id, ports.DraftSave.keeping(
        draft, attachment_ids=[*(a.attachment_id for a in draft.attachments),
                               ref.attachment_id]), False)
    return draft_of(rt, draft.message_id)


def write(path: Path, data: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def copy_bytes(rt, ref: wire.AttachmentRef) -> bytes:
    return rt.files.snapshot_path(ref.attachment_id).read_bytes()


HTML = b"<!doctype html><title>Orb study</title><p>transform</p>\n"
BINARY = bytes(range(256)) * 300 + b"\x00\xff"


# -- the happy path ---------------------------------------------------------------------

@pytest.mark.asyncio
async def test_attached_files_are_the_exact_bytes_at_a_new_revision_and_nothing_is_sent(
        tmp_path):
    s = signed_in()
    rt = s.rt
    html = write(tmp_path / "work" / "study.html", HTML)
    clip = write(tmp_path / "work" / "clip.wav", BINARY)
    draft = await new_draft(rt)
    s.frames.clear()

    out = await attach(rt, draft, [html, clip])
    saved = draft_of(rt, draft.message_id)
    assert saved.draft_revision == draft.draft_revision + 1
    assert out == (f"Attached study.html (56 bytes), clip.wav (75 kilobytes) to draft "
                   f"{draft.message_id} to Me. It is now at revision {saved.draft_revision} "
                   f"with study.html (56 bytes), clip.wav (75 kilobytes). Nothing is sent. "
                   f"Review this revision before sending: request_message_review with "
                   f"draft_revision {saved.draft_revision}.")
    assert str(tmp_path) not in out and "transform" not in out, "names and sizes only"
    html_ref, clip_ref = saved.attachments
    for ref, path, media in [(html_ref, html, "text/html"), (clip_ref, clip, "audio/x-wav")]:
        data = path.read_bytes()
        assert (ref.filename, ref.size_bytes, ref.sha256) == (
            path.name, len(data), hashlib.sha256(data).hexdigest())
        assert ref.media_type == media
        assert copy_bytes(rt, ref) == data
    assert saved.agent_drafted and saved.send.state == "draft"
    assert {"type": ports.MESSAGES_HINT} in s.frames, "the page reads the Queue again"
    assert s.connection.calls == [], "attaching neither sends nor uploads"
    assert [f.state for f in rt.files.files(*ns(rt), draft.message_id)] == ["staged"] * 2
    await rt.stop()


# -- which files may be read --------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_named_hidden_folder_document_is_attachable_but_its_private_siblings_are_not(
        tmp_path):
    rt = signed_in().rt
    folder = tmp_path / "repo" / ".agents" / "in-progress"
    study = write(folder / "orb-icon-transform-study.html", HTML)
    blocked = [write(folder / name, b"x") for name in
               (".env", "id_rsa", "server.pem", ".hidden-notes", "aws_credentials.txt")]
    blocked.append(write(tmp_path / "home" / ".ssh" / "config", b"x"))
    blocked.append(write(tmp_path / "home" / ".config" / "gh" / "hosts.yml", b"x"))
    draft = await new_draft(rt)

    for path in blocked:
        out = await attach(rt, draft, [path])
        assert out == (f"JARVIS does not send {path.name} from where it is. Nothing was "
                       f"attached; the draft was unchanged."), path
    assert draft_of(rt, draft.message_id) == draft

    out = await attach(rt, draft, [study])
    assert out.startswith("Attached orb-icon-transform-study.html (56 bytes)"), out
    await rt.stop()


@pytest.mark.asyncio
async def test_jarvis_s_own_data_and_provider_homes_are_never_attached(tmp_path, monkeypatch):
    from jarvis import history_read
    claude = tmp_path / "claude-home"
    monkeypatch.setattr(history_read, "claude_roots", lambda: [claude])
    rt = signed_in().rt
    data_paths.ensure_tool_token()
    own = [data_paths.tool_token_path(), data_paths.db_path(),
           write(data_paths.data_dir() / "notes.txt", b"x"),
           write(claude / "settings.json", b"{}"),
           write(claude / "projects" / "-repo" / "notes.txt", b"x")]
    package = write(data_paths.packages_dir() / "p-1.0.0-aa" / "results" / "summary.md", b"ok")
    draft = await new_draft(rt)
    for path in own:
        with pytest.raises(draft_files.NotAttached, match="does not send"):
            draft_files.source_path(str(path))
    assert draft_files.source_path(str(package)) == package.resolve(), \
        "a package's results are the one part of JARVIS's data that goes back"
    assert (await attach(rt, draft, [package])).startswith("Attached summary.md")
    await rt.stop()


@pytest.mark.asyncio
async def test_what_cannot_be_read_is_refused_and_the_draft_is_unchanged(tmp_path):
    rt = signed_in().rt
    work = tmp_path / "work"
    real = write(work / "real.txt", b"real")
    os.symlink(real, work / "link.txt")
    os.mkfifo(work / "pipe")
    locked = write(work / "locked.txt", b"x")
    os.chmod(locked, 0)
    (work / "folder").mkdir()
    draft = await new_draft(rt)
    opened = []
    real_open = draft_files.Source.open

    def tracking(raw):
        source = real_open(raw)
        opened.append(source)
        return source
    draft_files.Source.open = staticmethod(tracking)
    try:
        cases = [(work / "gone.txt", f"There is no file at {work.resolve() / 'gone.txt'}."),
                 (work / "folder", f"{work.resolve() / 'folder'} is not an ordinary file."),
                 (work / "pipe", f"{work.resolve() / 'pipe'} is not an ordinary file."),
                 (work / "link.txt", f"{work.resolve() / 'link.txt'} is a link or is reached "
                                     f"through one; name the file itself."),
                 (locked, f"macOS does not let JARVIS read {locked.resolve()}."),
                 ("relative/real.txt", "'relative/real.txt' is not a full path.")]
        for path, said in cases:
            out = await rt.tools.attach_message_files({
                "message": str(draft.message_id), "draft_revision": draft.draft_revision,
                "paths": [str(real), str(path)]})
            assert out == f"{said} Nothing was attached; the draft was unchanged.", path
    finally:
        draft_files.Source.open = real_open
        os.chmod(locked, stat.S_IRUSR | stat.S_IWUSR)
    assert draft_of(rt, draft.message_id) == draft
    assert opened and all(s._fd is None for s in opened), "every file opened was closed"
    await rt.stop()


@pytest.mark.asyncio
async def test_a_folder_swapped_for_a_link_after_the_check_is_refused(tmp_path, monkeypatch):
    rt = signed_in().rt
    write(tmp_path / "work" / "plan.txt", b"mine")
    write(tmp_path / "elsewhere" / "plan.txt", b"not the one checked")
    draft = await new_draft(rt)
    checked = draft_files.source_path

    def then_swapped(raw):
        path = checked(raw)
        os.rename(tmp_path / "work", tmp_path / "work-old")
        os.symlink(tmp_path / "elsewhere", tmp_path / "work")
        return path
    monkeypatch.setattr(draft_files, "source_path", then_swapped)
    out = await attach(rt, draft, [tmp_path / "work" / "plan.txt"])
    assert "is a link or is reached through one" in out and "unchanged" in out, out
    assert draft_of(rt, draft.message_id) == draft
    await rt.stop()


# -- what the draft keeps -------------------------------------------------------------------

@pytest.mark.asyncio
async def test_attaching_keeps_page_files_text_and_recipient_and_a_text_edit_keeps_every_file(
        tmp_path):
    rt = signed_in().rt
    draft = await new_draft(rt, text="First words.", subject="The study")
    draft = await page_adds(rt, draft, "from-page.png", b"\x89PNG page bytes")
    out = await attach(rt, draft, [write(tmp_path / "w" / "study.html", HTML)])
    assert "with from-page.png (15 bytes), study.html (56 bytes)" in out, out
    saved = draft_of(rt, draft.message_id)
    for kept in ("subject", "text", "recipient_github_id", "thread_id",
                 "in_reply_to_message_id", "related_message_ids"):
        assert getattr(saved, kept) == getattr(draft, kept), kept
    assert saved.attachments[0] == draft.attachments[0], "the page's file first, unchanged"

    edited = await rt.tools.draft_message({"message": str(saved.message_id),
                                           "draft_revision": saved.draft_revision,
                                           "text": "Better words.", "subject": "The study"})
    assert "is saved with 2 files" in edited, edited
    after = draft_of(rt, saved.message_id)
    assert after.text == "Better words." and after.attachments == saved.attachments

    after = await page_adds(rt, after, "second-page.png", b"more")
    assert [a.filename for a in after.attachments] == ["from-page.png", "study.html",
                                                       "second-page.png"]
    await rt.stop()


@pytest.mark.asyncio
async def test_a_message_of_files_alone_reaches_review(tmp_path):
    rt = signed_in().rt
    draft = await new_draft(rt, text="")
    out = await attach(rt, draft, [write(tmp_path / "w" / "clip.wav", BINARY)])
    saved = draft_of(rt, draft.message_id)
    assert saved.text == "" and len(saved.attachments) == 1, out
    review = rt.tools.request_message_review({"message": str(saved.message_id),
                                              "draft_revision": saved.draft_revision})
    assert review.startswith("Capture 1 holds the message as it would go."), review
    assert draft_of(rt, saved.message_id).send.state == "awaiting_approval"
    await rt.stop()


# -- limits ------------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_the_shared_limits_count_actual_bytes_and_existing_files(tmp_path, monkeypatch):
    rt = signed_in().rt
    monkeypatch.setattr(wire, "FILE_MAX_BYTES", 1000)
    monkeypatch.setattr(wire, "PACKAGE_MAX_BYTES", 2500)
    w = tmp_path / "w"
    at_limit, over = write(w / "a.bin", b"a" * 1000), write(w / "b.bin", b"b" * 1001)
    draft = await new_draft(rt)

    out = await attach(rt, draft, [over])
    assert out.startswith("b.bin is larger than 1000 bytes, the most one file may be."), out
    out = await attach(rt, draft, [at_limit, write(w / "c.bin", b"c" * 1000)])
    assert "now at revision 2" in out, out
    draft = draft_of(rt, draft.message_id)
    out = await attach(rt, draft, [write(w / "d.bin", b"d" * 501)])
    assert out.startswith("Together they are larger than 2.4 kilobytes"), out
    assert (await attach(rt, draft, [write(w / "e.bin", b"e" * 500)])).startswith("Attached")

    draft = draft_of(rt, draft.message_id)
    monkeypatch.setattr(wire, "PACKAGE_MAX_BYTES", 10**6)
    for i in range(wire.ATTACHMENTS_MAX - 3):
        draft = await page_adds(rt, draft, f"p{i}.txt", b"p")
    assert len(draft.attachments) == wire.ATTACHMENTS_MAX
    out = await attach(rt, draft, [write(w / "f.bin", b"f")])
    assert out.startswith("A message holds at most 10 files, and this draft has 10 already."), out
    assert draft_of(rt, draft.message_id) == draft
    await rt.stop()


@pytest.mark.asyncio
async def test_bytes_past_the_limit_are_refused_as_they_are_read(tmp_path, monkeypatch):
    """The size the file had when it was checked is not trusted: the copy
    counts what is read."""
    rt = signed_in().rt
    path = write(tmp_path / "w" / "grows.log", b"x" * 900)
    draft = await new_draft(rt)
    monkeypatch.setattr(wire, "FILE_MAX_BYTES", 1000)
    source = draft_files.Source.open(str(path))
    with open(path, "ab") as f:
        f.write(b"y" * 500)
    with pytest.raises(draft_files.NotAttached, match="changed while it was being copied"):
        await rt.files.add_to_draft(*ns(rt), draft.message_id, "grows.log", None,
                                    source.chunks())
    source.close()
    assert not list(rt.files.snapshots_dir().glob(messaging_files.PARTIAL + "*"))
    await rt.stop()


# -- all or none -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_batch_with_one_bad_file_attaches_none_and_its_copies_are_reclaimed(
        tmp_path, monkeypatch):
    rt = signed_in().rt
    draft = await new_draft(rt)
    draft = await page_adds(rt, draft, "kept.png", b"kept")
    good = write(tmp_path / "w" / "good.txt", b"good")
    bad = write(tmp_path / "w" / "bad.txt", b"bad")
    real = draft_files.Source.chunks

    async def failing(self):
        if self.path.name == "bad.txt":
            raise OSError("the disk went away")
        async for chunk in real(self):
            yield chunk
    monkeypatch.setattr(draft_files.Source, "chunks", failing)
    out = await attach(rt, draft, [good, bad])
    assert out == "A file could not be copied (OSError). Nothing was attached; the draft " \
                  "was unchanged.", out
    assert draft_of(rt, draft.message_id) == draft, "the page's file is still its only one"
    staged = [f for f in rt.files.files(*ns(rt), draft.message_id)
              if f.attachment_id != draft.attachments[0].attachment_id]
    assert len(staged) == 1, "good.txt was copied before bad.txt failed, and named by nothing"

    assert rt.files.cleanup(*ns(rt)) == 0, "kept through its grace period"
    later = time.time() + messaging_files.SNAPSHOT_GRACE_SEC + 1
    monkeypatch.setattr(rt.files, "now", lambda: later)
    assert rt.files.cleanup(*ns(rt)) == 1, "then reclaimed"
    assert [f.attachment_id for f in rt.files.files(*ns(rt), draft.message_id)] == \
        [draft.attachments[0].attachment_id], "the draft's own file is never touched"
    assert copy_bytes(rt, draft.attachments[0]) == b"kept"
    await rt.stop()


@pytest.mark.asyncio
async def test_a_file_that_changes_while_copied_is_refused_and_a_later_change_cannot_touch_the_copy(
        tmp_path, monkeypatch):
    rt = signed_in().rt
    path = write(tmp_path / "w" / "notes.txt", b"before")
    draft = await new_draft(rt)
    real = draft_files.Source.chunks

    async def touched(self):
        os.utime(self.path, ns=(0, self.info.st_mtime_ns + 10**9))
        async for chunk in real(self):
            yield chunk
    monkeypatch.setattr(draft_files.Source, "chunks", touched)
    out = await attach(rt, draft, [path])
    assert out.startswith("notes.txt changed while it was being copied"), out
    assert draft_of(rt, draft.message_id) == draft
    monkeypatch.setattr(draft_files.Source, "chunks", real)

    assert (await attach(rt, draft, [path])).startswith("Attached notes.txt")
    path.write_bytes(b"after, and longer")
    (ref,) = draft_of(rt, draft.message_id).attachments
    assert copy_bytes(rt, ref) == b"before"
    assert rt.files.verified_attachment_manifest(*ns(rt), draft.message_id, 1) == [ref]
    await rt.stop()


@pytest.mark.asyncio
async def test_the_same_file_twice_is_refused_but_equal_contents_are_two_files(tmp_path):
    rt = signed_in().rt
    one = write(tmp_path / "w" / "one.txt", b"same")
    os.link(one, tmp_path / "w" / "also-one.txt")
    two = write(tmp_path / "w" / "two.txt", b"same")
    draft = await new_draft(rt)
    for paths in ([one, one], [one, tmp_path / "w" / "also-one.txt"],
                  [one, tmp_path / "w" / "." / "one.txt"]):
        out = await attach(rt, draft, paths)
        assert "is named twice" in out and "unchanged" in out, (paths, out)
    assert (await attach(rt, draft, [one, two])).startswith("Attached one.txt (4 bytes), "
                                                            "two.txt (4 bytes)")
    await rt.stop()


@pytest.mark.asyncio
async def test_bad_arguments_are_refused_with_the_draft_unchanged(tmp_path):
    rt = signed_in().rt
    path = write(tmp_path / "w" / "a.txt", b"a")
    draft = await new_draft(rt)
    base = {"message": str(draft.message_id), "draft_revision": draft.draft_revision,
            "paths": [str(path)]}
    for change, said in [({"message": "nope"}, "That is not a draft's message id"),
                         ({"draft_revision": True}, "Give the draft_revision you last read"),
                         ({"draft_revision": "1"}, "Give the draft_revision you last read"),
                         ({"draft_revision": 0}, "Give the draft_revision you last read"),
                         ({"paths": []}, "Give paths as a list"),
                         ({"paths": str(path)}, "Give paths as a list"),
                         ({"paths": [""]}, "Give paths as a list"),
                         ({"message": "00000000-0000-4000-8000-000000000999"},
                          "There is no draft by that id.")]:
        out = await rt.tools.attach_message_files({**base, **change})
        assert out.startswith(said) and "unchanged" in out, (change, out)
    assert draft_of(rt, draft.message_id) == draft
    await rt.stop()


# -- revisions, sign-ins and the page at the same time --------------------------------------

@pytest.mark.asyncio
async def test_a_retry_after_a_lost_answer_attaches_nothing_twice(tmp_path):
    rt = signed_in().rt
    path = write(tmp_path / "w" / "study.html", HTML)
    draft = await new_draft(rt)
    await attach(rt, draft, [path])                       # its answer is lost
    out = await attach(rt, draft, [path])                 # the same call again
    saved = draft_of(rt, draft.message_id)
    assert len(saved.attachments) == 1
    assert out == (f"Draft {draft.message_id} is at revision {saved.draft_revision}, not "
                   f"{draft.draft_revision}; nothing was attached. It may already hold these "
                   f"files if an earlier call went through: it has study.html (56 bytes). Tell "
                   f"him what is attached before doing anything else; do not attach again at "
                   f"the new revision unless he asks.")
    await rt.stop()


def _during_copy(monkeypatch, rt, then):
    """Run `then` once, after the first file is copied and before the save."""
    real = rt.files.add_to_draft

    async def copying(*args):
        ref = await real(*args)
        if not getattr(copying, "done", False):
            copying.done = True
            result = then()
            if asyncio.iscoroutine(result):
                await result
        return ref
    monkeypatch.setattr(rt.files, "add_to_draft", copying)


@pytest.mark.asyncio
async def test_a_page_edit_during_the_copy_wins(tmp_path, monkeypatch):
    rt = signed_in().rt
    draft = await new_draft(rt, text="Draft words.")
    _during_copy(monkeypatch, rt, lambda: rt.local.write_draft(
        draft.message_id, ports.DraftSave.keeping(draft, text="Typed on the page."), False))
    out = await attach(rt, draft, [write(tmp_path / "w" / "a.txt", b"a")])
    assert out.startswith(f"Draft {draft.message_id} is at revision 2, not 1; nothing was "
                          f"attached."), out
    saved = draft_of(rt, draft.message_id)
    assert saved.text == "Typed on the page." and saved.attachments == []
    await rt.stop()


@pytest.mark.asyncio
async def test_a_page_discard_during_the_copy_is_not_undone(tmp_path, monkeypatch):
    rt = signed_in().rt
    draft = await new_draft(rt)
    _during_copy(monkeypatch, rt, lambda: rt.local.discard_draft(draft.message_id,
                                                                 draft.draft_revision))
    out = await attach(rt, draft, [write(tmp_path / "w" / "a.txt", b"a"),
                                   write(tmp_path / "w" / "b.txt", b"b")])
    assert out == "There is no draft by that id. Nothing was attached; the draft was unchanged."
    assert draft_of(rt, draft.message_id) is None, "not brought back"
    await rt.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["sign_out", "other_team", "signed_in_again"])
async def test_a_sign_in_change_during_the_copy_saves_nothing(tmp_path, monkeypatch, change):
    s = signed_in()
    rt = s.rt
    draft = await new_draft(rt)
    who = rt.identity()

    def switch():
        if change == "sign_out":
            s.connection.signed_out = True
        elif change == "other_team":
            s.connection.saved = {"server_id": "another-team", "me": {"github_id": 1}}
        else:
            s.connection.generation += 1
    _during_copy(monkeypatch, rt, switch)
    out = await attach(rt, draft, [write(tmp_path / "w" / "a.txt", b"a")])
    assert out == ("The team sign-in changed while the files were being copied. Nothing "
                   "was attached; the draft was unchanged.")
    assert rt.store.draft(*who.ns, draft.message_id) == draft
    assert rt.store.draft("another-team", 1, draft.message_id) is None
    await rt.stop()


@pytest.mark.asyncio
async def test_attaching_after_review_needs_a_new_review(tmp_path):
    s = signed_in()
    rt = s.rt
    draft = await new_draft(rt)
    rt.tools.request_message_review({"message": str(draft.message_id),
                                     "draft_revision": draft.draft_revision})
    reviewed = draft_of(rt, draft.message_id)
    assert (reviewed.send.state, reviewed.capture_id) == ("awaiting_approval", 1)

    await attach(rt, reviewed, [write(tmp_path / "w" / "late.txt", b"late")])
    after = draft_of(rt, draft.message_id)
    assert s.captures.closed == [1], "the old review's capture is closed"
    assert (after.send.state, after.send.content_revision, after.capture_id) == \
        ("draft", 2, None)
    with pytest.raises(ports.NotApproved):
        rt.approvals.approve(*ns(rt), draft.message_id, 1, reviewed.send.content_digest,
                             rt.node, "voice")
    with pytest.raises(ports.NotApproved):
        rt.approvals.approve(*ns(rt), draft.message_id, 2, reviewed.send.content_digest,
                             rt.node, "voice")
    review = rt.tools.request_message_review({"message": str(draft.message_id),
                                              "draft_revision": after.draft_revision})
    assert review.startswith("Capture 2 holds the message"), review
    await rt.stop()


@pytest.mark.asyncio
async def test_an_approved_message_takes_no_more_files(tmp_path, monkeypatch):
    rt = signed_in().rt
    draft = await new_draft(rt)
    monkeypatch.setattr(rt.store, "draft", lambda *a: draft.model_copy(update={
        "send": draft.send.model_copy(update={"state": "approved"})}))
    out = await attach(rt, draft, [write(tmp_path / "w" / "a.txt", b"a")])
    assert out.startswith("That message is past editing"), out
    await rt.stop()


@pytest.mark.asyncio
async def test_cancelled_before_the_save_leaves_the_draft_and_closes_the_files(
        tmp_path, monkeypatch):
    rt = signed_in().rt
    draft = await new_draft(rt)
    first, second = write(tmp_path / "w" / "a.txt", b"a"), write(tmp_path / "w" / "b.txt", b"b")
    copying, release = asyncio.Event(), asyncio.Event()
    real = rt.files.add_to_draft
    opened = []
    real_open = draft_files.Source.open

    def tracking(raw):
        opened.append(real_open(raw))
        return opened[-1]
    monkeypatch.setattr(draft_files.Source, "open", staticmethod(tracking))

    async def slow(*args):
        ref = await real(*args)
        copying.set()
        await release.wait()
        return ref
    monkeypatch.setattr(rt.files, "add_to_draft", slow)
    task = asyncio.create_task(attach(rt, draft, [first, second]))
    await copying.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert draft_of(rt, draft.message_id) == draft
    assert len(opened) == 2 and all(s._fd is None for s in opened)

    monkeypatch.setattr(rt.files, "add_to_draft", real)
    task = asyncio.create_task(attach(rt, draft, [first]))
    out = await task                                     # saved; then its caller goes
    assert "now at revision 2" in out
    again = await attach(rt, draft, [first])
    assert "is at revision 2, not 1; nothing was attached" in again
    assert len(draft_of(rt, draft.message_id).attachments) == 1
    await rt.stop()


# -- draft_message's files: the same batch ------------------------------------------------

@pytest.mark.asyncio
async def test_draft_message_files_are_checked_first_and_attached_as_one_batch(
        tmp_path, monkeypatch):
    rt = signed_in().rt
    good = write(tmp_path / "w" / "good.txt", b"good")
    out = await rt.tools.draft_message({"to": "Me", "text": "x",
                                        "files": [str(good), str(tmp_path / "w" / "gone")]})
    gone = (tmp_path / "w").resolve() / "gone"
    assert out == f"There is no file at {gone}. Nothing was drafted.", out
    assert not rt.store.snapshot(*ns(rt), availability="available", stale=False).unsent

    real = draft_files.Source.chunks

    async def failing(self):
        if self.path.name == "bad.txt":
            raise OSError("gone")
        async for chunk in real(self):
            yield chunk
    monkeypatch.setattr(draft_files.Source, "chunks", failing)
    bad = write(tmp_path / "w" / "bad.txt", b"bad")
    out = await rt.tools.draft_message({"to": "Me", "text": "x",
                                        "files": [str(good), str(bad)]})
    assert "is saved at revision 1 without its files: A file could not be copied " \
           "(OSError). Nothing is sent." in out, out
    (saved,) = rt.store.snapshot(*ns(rt), availability="available", stale=False).unsent
    assert saved.attachments == [], "neither file: all or none"
    await rt.stop()


# -- the brain's tool, through the server ---------------------------------------------------

def _post(w, tool, brain, **arguments) -> dict:
    w.server.brain_instance = brain
    r = w.client.post("/internal/tool",
                      headers={"Authorization": f"Bearer {data_paths.ensure_tool_token()}"},
                      json={"tool": tool, "arguments": arguments})
    assert r.status_code == 200, r.text
    return r.json()


def test_the_tool_is_defined_gated_and_awaited(wired, tmp_path, monkeypatch):
    from jarvis import claude_provider, jarvis_mcp
    server = wired.server
    (spec,) = [t for t in jarvis_mcp.TOOL_SPECS if t["name"] == "attach_message_files"]
    schema = spec["inputSchema"]
    assert schema["required"] == ["message", "draft_revision", "paths"]
    assert schema["properties"]["paths"]["maxItems"] == wire.ATTACHMENTS_MAX
    assert schema["additionalProperties"] is False
    assert "mcp__jarvis__attach_message_files" in claude_provider.ALLOWED_TOOLS
    assert "attach_message_files" in server.ACTING_TOOLS
    assert "attach_message_files" in server.TAINT_EXEMPT_TOOLS
    assert "attach_message_files" not in server.TAINTING_TOOLS
    assert jarvis_mcp.TIMEOUT_FOR["attach_message_files"] > jarvis_mcp.TIMEOUT_SEC

    assert _post(wired, "attach_message_files", _Brain(), message="x", draft_revision=1,
                 paths=["/x"])["text"] == "Messaging isn't available on this JARVIS."

    s = signed_in()
    server.messaging = s.rt
    path = write(tmp_path / "w" / "study.html", HTML)
    out = asyncio.run(s.rt.tools.draft_message({"to": "Me", "text": "x"}))
    message = out.split()[1]
    args = {"message": message, "draft_revision": 1, "paths": [str(path)]}

    refused = _post(wired, "attach_message_files", _Brain("event"), **args)
    assert not refused["ok"] and refused["text"].startswith("not_allowed_from_event")
    monkeypatch.setenv("JARVIS_REFUSE_UNTRUSTED_ACTIONS", "1")
    tainted = _Brain()
    tainted.turn_untrusted_source = "a web page"
    refused = _post(wired, "attach_message_files", tainted, **args)
    assert not refused["ok"] and refused["text"].startswith("untrusted_content_in_this_turn")
    assert draft_of(s.rt, message).attachments == []

    monkeypatch.delenv("JARVIS_REFUSE_UNTRUSTED_ACTIONS")
    brain = _Brain()
    got = _post(wired, "attach_message_files", brain, **args)
    assert got["ok"] and got["text"].startswith("Attached study.html (56 bytes) to draft"), got
    assert "transform" not in got["text"] and brain.marks == [], "no content, no taint"
    assert len(draft_of(s.rt, message).attachments) == 1

    listed = _post(wired, "people_messages", _Brain())["text"]
    assert f"new message {message} to Me, revision 2, not sent (draft): x; File: " \
           f"study.html (56 bytes), attachment" in listed, listed


# -- delivered to oneself -------------------------------------------------------------------

@pytest.mark.asyncio
async def test_an_attached_file_goes_to_oneself_as_its_exact_snapshot(tmp_path):
    """The attacher over the contract's fake team server: the reviewed copy
    is uploaded, sent to oneself, received back and verified."""
    from tests.test_messaging_files import SERVER as FILES_SERVER, TIM, World
    w = World()
    pairing = Pairing(FILES_SERVER, TIM, 1)
    delivery = SimpleNamespace(pairing=lambda: pairing, changed=lambda: None)
    local = messaging_api.LocalMessaging(store=w.tim.store, delivery=delivery,
                                         guard=w.tim.guard, snapshots=w.tim.files)
    attacher = draft_files.DraftFileAttacher(
        w.tim.store, w.tim.files, lambda: pairing,
        lambda message_id, save: local.write_draft(message_id, save, True))
    data = os.urandom(70_000)
    path = write(tmp_path / "w" / "recording.wav", data)
    draft = w.tim.draft(TIM, [], text="Self test")
    who = SimpleNamespace(ns=pairing.ns)

    saved = await attacher.attach(who, draft.message_id, 1, [str(path)])
    path.write_bytes(b"changed after attaching")
    (ref,) = saved.attachments
    await w.tim.send(saved)
    assert w.tim.files.files(FILES_SERVER, TIM, saved.message_id)[0].state == "available"
    got = await w.tim.files.download(FILES_SERVER, TIM, saved.message_id, ref.attachment_id)
    assert got.state == "verified"
    received = data_paths.messaging_downloads_dir() / str(ref.attachment_id) / ref.filename
    assert received.read_bytes() == data
    assert ref.sha256 == hashlib.sha256(data).hexdigest()
