"""The bundle inspection service (jarvis/bundle_inspection.py): classify,
list, read, preview, render and a static preview's files, over a file
service that hands out verified paths only, with every outcome observed.
Every input is synthetic (tests/bundle_fixtures.py); PDF goes through the
fake osascript at the subprocess seam."""
from __future__ import annotations

import asyncio
import hashlib
import os
import stat
import subprocess
import uuid
from pathlib import Path

import pytest

from jarvis import bundle_archive, data_paths, work_packages
from jarvis import messaging_wire as wire
from jarvis.bundle_cache import DerivedCache
from jarvis.bundle_contract import (MEMBER_PAGE_MAX, InspectionRefused, RenderOptions,
                                    member_id)
from jarvis.bundle_inspection import AttachmentKey, BundleInspection, Owner
from jarvis.bundle_readers import _quartz
from tests.bundle_fixtures import (FILE_MODE, HOSTILE_HTML, WRAPPER,
                                   FakeOsascript, docx_bytes, encrypt_entries, jpeg_header,
                                   png_bytes, raw_zip, review_bundle, review_files,
                                   scanned_pdf, two_page_pdf, windows_zip, zip_bytes)

SERVER = "srv-bundles"
TIM, MATT = Owner(SERVER, 863470), Owner(SERVER, 1001)


class Files:
    """FileSnapshots' inspection face: attachment_ref, verified_path and
    downloaded, owner-scoped, with the hash checked on every read."""

    def __init__(self, root: Path):
        self.root, self.held, self.downloads = root, {}, 0

    def add(self, owner: Owner, data: bytes, filename: str, *, local: bool = True,
            fail: str = "") -> AttachmentKey:
        key = AttachmentKey(uuid.uuid4(), uuid.uuid4())
        ref = wire.AttachmentRef(attachment_id=key.attachment_id, filename=filename,
                                 media_type="application/octet-stream", size_bytes=len(data),
                                 sha256=hashlib.sha256(data).hexdigest())
        path = self.root / str(key.attachment_id) / filename
        self.held[(owner.server_id, owner.owner_github_id, key.message_id,
                   key.attachment_id)] = {"ref": ref, "path": path, "data": data,
                                          "local": local, "fail": fail}
        if local:
            self._write(path, data)
        return key

    @staticmethod
    def _write(path: Path, data: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        path.chmod(0o600)

    def _get(self, server, owner, message, attachment):
        held = self.held.get((server, owner, message, attachment))
        if held is None:
            raise wire.ContractError("attachment_not_found")
        return held

    def attachment_ref(self, server, owner, message, attachment):
        return self._get(server, owner, message, attachment)["ref"]

    def verified_path(self, server, owner, message, attachment):
        held = self._get(server, owner, message, attachment)
        if not held["local"]:
            raise wire.ContractError("attachment_not_ready")
        if hashlib.sha256(held["path"].read_bytes()).hexdigest() != held["ref"].sha256:
            raise wire.ContractError("attachment_mismatch")
        return held["path"]

    async def downloaded(self, server, owner, message, attachment):
        held = self._get(server, owner, message, attachment)
        if held["fail"]:
            raise wire.ContractError(held["fail"])
        if not held["local"]:
            self.downloads += 1
            self._write(held["path"], held["data"])
            held["local"] = True
        return self.verified_path(server, owner, message, attachment)


@pytest.fixture
def fake_osascript(monkeypatch):
    fake = FakeOsascript()
    monkeypatch.setattr(_quartz, "RUNNER", fake)
    _quartz.forget_health()
    yield fake
    _quartz.forget_health()


@pytest.fixture
def world(tmp_path, fake_osascript):
    class World:
        files = Files(tmp_path / "downloads")
        cache = DerivedCache(tmp_path / "cache")
        seen: list = []
        service = BundleInspection(files, cache, observer=seen.append)
        osascript = fake_osascript
    return World


def find(service, owner, key, path: str):
    """A member's info by path, walking the listing from the top."""
    folder = path.rsplit("/", 1)[0] if "/" in path else ""
    page = service.list_members(owner, key, folder, limit=MEMBER_PAGE_MAX)
    return next(m for m in page.members if m.path == path)


# -- the incident's bundle -------------------------------------------------------------

def test_the_wrapped_review_bundle_opens_as_an_ordinary_bundle(world):
    key = world.files.add(MATT, review_bundle(), "summary-review-1.0.0.zip")
    c = world.service.classify(MATT, key)
    assert (c.format, c.container, c.support, c.reason) == ("zip", "zip", "readable", None)
    inv = c.inventory
    assert (inv.files, inv.wrapper, inv.readable, inv.blocked) == (8, WRAPPER, 8, 0)
    assert inv.folders == 3 and inv.expanded_bytes == sum(map(len, review_files().values()))
    assert c.package.recognised and c.package.runs == "conversation"
    assert c.root_member_id == member_id(c.attachment.sha256, "")
    top = world.service.list_members(MATT, key)
    assert [(m.path, m.is_dir, m.format) for m in top.members] == [(WRAPPER, True, "folder")]
    inside = world.service.list_members(MATT, key, WRAPPER)
    assert [m.name for m in inside.members] == [
        "evidence", "package.json", "REVIEW-BRIEF.md", "SKILL.md", "summary.html",
        "summary.pdf"]
    formats = {m.name: (m.format, m.support, m.operations) for m in inside.members}
    assert formats["summary.pdf"] == ("pdf", "readable", ["read_text", "render"])
    assert formats["REVIEW-BRIEF.md"] == ("markdown", "readable", ["read_text"])


def test_the_primary_pdf_is_read_and_rendered_in_place(world):
    key = world.files.add(MATT, review_bundle(), "bundle.zip")
    world.service.classify(MATT, key)
    pdf = find(world.service, MATT, key, f"{WRAPPER}/summary.pdf")
    page = world.service.read_member(MATT, key, pdf.member_id)
    assert [(c.location.page, c.text) for c in page.chunks] == [
        (1, "Summary, page one."), (2, "Evidence, page two.")]
    assert page.complete and page.continuation is None and page.extent.pages == 2
    assert page.reader.name == "pdf" and page.member.path == f"{WRAPPER}/summary.pdf"
    rendered = world.service.render_page(MATT, key, pdf.member_id, 2,
                                         RenderOptions(max_edge=400))
    assert rendered.png.startswith(b"\x89PNG") and rendered.info.height == 400
    with pytest.raises(InspectionRefused) as e:
        world.service.render_page(MATT, key, pdf.member_id, 3)
    assert e.value.code == "page_out_of_range"
    preview = world.service.preview_member(MATT, key, pdf.member_id)
    assert [(r.kind, r.page) for r in preview.refs] == [("page_image", 1), ("page_image", 2)]
    assert preview.refs[0].url == (f"/api/team/messages/{key.message_id}/attachments/"
                                   f"{key.attachment_id}/bundle/members/{pdf.member_id}/pages/1")


def test_a_second_read_comes_from_the_cache(world):
    key = world.files.add(MATT, review_bundle(), "bundle.zip")
    pdf = find(world.service, MATT, key, f"{WRAPPER}/summary.pdf")
    first = world.service.read_member(MATT, key, pdf.member_id)
    calls = len(world.osascript.calls)
    assert world.service.read_member(MATT, key, pdf.member_id) == first
    world.service.render_page(MATT, key, pdf.member_id, 1)
    calls = len(world.osascript.calls)
    world.service.render_page(MATT, key, pdf.member_id, 1)
    assert len(world.osascript.calls) == calls


@pytest.mark.parametrize("bundle", [review_bundle(manifest=None),
                                    review_bundle(wrapper=False, manifest=None)])
def test_a_zip_without_a_manifest_has_no_package_badge_and_still_opens(world, bundle):
    key = world.files.add(MATT, bundle, "plain.zip")
    c = world.service.classify(MATT, key)
    assert c.package is None and c.support == "readable" and c.inventory.files == 7


@pytest.mark.parametrize("files,reason", [
    ({"package.json": b'{"name": "lib", "version": "1.0.0", "dependencies": {}}',
      "index.js": b"module.exports = 1"}, "purpose"),
    ({"a/package.json": b"{}", "b/package.json": b"{}"}, "several"),
    ({"package.json": b"{not json"}, ""),
])
def test_a_package_json_that_is_not_a_work_package_is_a_badge_only(world, files, reason):
    key = world.files.add(MATT, zip_bytes(files), "x.zip")
    c = world.service.classify(MATT, key)
    assert c.package is not None and not c.package.recognised and c.package.runs is None
    assert reason in c.package.reason
    assert c.support == "readable"


# -- refusals and limits -------------------------------------------------------------------

def test_a_file_not_downloaded_is_not_local_until_opened(world):
    key = world.files.add(MATT, review_bundle(), "bundle.zip", local=False)
    with pytest.raises(InspectionRefused) as e:
        world.service.classify(MATT, key)
    assert e.value.code == "not_local"
    c = asyncio.run(world.service.open(MATT, key))
    assert c.container == "zip" and world.files.downloads == 1
    asyncio.run(world.service.open(MATT, key))
    assert world.files.downloads == 1, "opened twice, downloaded once"
    ops = [(o.operation, o.support) for o in world.seen]
    assert ops[:2] == [("download", "readable"), ("list_archive", "readable")]


def test_a_failed_download_or_a_changed_file_is_said_and_observed(world):
    key = world.files.add(MATT, b"x", "a.txt", local=False, fail="transport")
    with pytest.raises(InspectionRefused) as e:
        asyncio.run(world.service.open(MATT, key))
    assert e.value.code == "not_local"
    assert (world.seen[-1].operation, world.seen[-1].reason) == ("download", "not_local")
    key = world.files.add(MATT, b"original", "a.txt")
    world.files.held[(SERVER, MATT.owner_github_id, key.message_id,
                      key.attachment_id)]["path"].write_bytes(b"changed")
    with pytest.raises(InspectionRefused) as e:
        world.service.classify(MATT, key)
    assert e.value.code == "corrupt"
    assert (world.seen[-1].operation, world.seen[-1].reason) == ("download", "corrupt")


def test_another_owners_file_is_not_found_and_their_cache_is_apart(world):
    key = world.files.add(TIM, review_bundle(), "bundle.zip")
    world.service.classify(TIM, key)
    with pytest.raises(InspectionRefused) as e:
        world.service.classify(MATT, key)
    assert e.value.code == "attachment_not_found"
    tim_dir = world.cache.root() / os.listdir(world.cache.root())[0]
    assert os.listdir(tim_dir) == [str(TIM.owner_github_id)]


def test_an_encrypted_zip_is_blocked_member_by_member(world):
    data = encrypt_entries(raw_zip([("a.txt", b"x", FILE_MODE), ("b.pdf", two_page_pdf(),
                                                                 FILE_MODE)]))
    key = world.files.add(MATT, data, "locked.zip")
    c = world.service.classify(MATT, key)
    assert (c.support, c.reason, c.inventory.blocked) == ("blocked", "encrypted", 2)
    member = find(world.service, MATT, key, "a.txt")
    assert (member.support, member.reason) == ("blocked", "encrypted")
    with pytest.raises(InspectionRefused) as e:
        world.service.read_member(MATT, key, member.member_id)
    assert (e.value.code, e.value.reason) == ("blocked", "encrypted")


@pytest.mark.parametrize("data,name", [
    (b"7z\xbc\xaf\x27\x1c\x00\x04" + b"\x00" * 30, "archive.7z"),
    (b"Rar!\x1a\x07\x01\x00" + b"\x00" * 30, "archive.rar"),
    (b"x" * 257 + b"ustar\x0000" + b"\x00" * 300, "archive.tar"),
    (b"\x1f\x8b\x08\x00" + b"\x00" * 30, "archive.tar.gz"),
    (zip_bytes({"a.txt": b"x"}), "archive.zip.001"),
    (b"PK\x07\x08" + b"\x00" * 30, "archive.z01"),
])
def test_other_archives_and_split_ones_are_unsupported_not_errors(world, data, name):
    key = world.files.add(MATT, data, name)
    c = world.service.classify(MATT, key)
    assert (c.container, c.support, c.reason) == ("unsupported", "unsupported",
                                                  "reader_unavailable")
    page = world.service.list_members(MATT, key)
    assert [m.path for m in page.members] == [""]
    with pytest.raises(InspectionRefused) as e:
        world.service.read_member(MATT, key, c.root_member_id)
    assert e.value.code == "unsupported"


def test_a_damaged_zip_is_corrupt(world):
    key = world.files.add(MATT, b"PK\x03\x04" + os.urandom(200), "broken.zip")
    c = world.service.classify(MATT, key)
    assert (c.container, c.support, c.reason) == ("zip", "unsupported", "corrupt")


def test_a_nested_archive_is_a_member_never_expanded(world):
    inner = zip_bytes({"secret.txt": b"inside"})
    key = world.files.add(MATT, zip_bytes({"outer.txt": b"x", "inner.zip": inner}), "o.zip")
    c = world.service.classify(MATT, key)
    nested = find(world.service, MATT, key, "inner.zip")
    assert (nested.format, nested.support, nested.reason) == ("zip", "unsupported",
                                                             "nested_archive")
    assert c.support == "partial" and c.inventory.unsupported == 1
    with pytest.raises(InspectionRefused) as e:
        world.service.read_member(MATT, key, nested.member_id)
    assert (e.value.code, e.value.reason) == ("unsupported", "nested_archive")


def test_unsafe_entries_are_blocked_members_and_the_rest_still_read(world):
    data = raw_zip([("docs/brief.md", b"# Brief\n", FILE_MODE),
                    ("../escape.txt", b"x", FILE_MODE),
                    ("docs/link", b"/etc/passwd", 0o120777),
                    ("docs/Brief.MD", b"dup", FILE_MODE)])
    key = world.files.add(MATT, data, "mixed.zip")
    c = world.service.classify(MATT, key)
    assert (c.support, c.inventory.blocked, c.inventory.readable) == ("partial", 3, 1)
    top = {m.path: (m.support, m.reason) for m in world.service.list_members(MATT, key).members}
    assert top["../escape.txt"] == ("blocked", "outside")
    docs = {m.path: (m.support, m.reason)
            for m in world.service.list_members(MATT, key, "docs").members}
    assert docs == {"docs/brief.md": ("readable", None), "docs/link": ("blocked", "link"),
                    "docs/Brief.MD": ("blocked", "duplicate")}
    brief = find(world.service, MATT, key, "docs/brief.md")
    assert world.service.read_member(MATT, key, brief.member_id).chunks[0].text == "# Brief\n"


def test_a_member_that_expands_past_its_budget_is_refused(world, tmp_path):
    service = BundleInspection(world.files, world.cache,
                               budgets=bundle_archive.Budgets(max_ratio=5, ratio_floor=1024))
    key = world.files.add(MATT, zip_bytes({"bomb.txt": b"0" * 2_000_000}), "bomb.zip")
    member = find(service, MATT, key, "bomb.txt")
    with pytest.raises(InspectionRefused) as e:
        service.read_member(MATT, key, member.member_id)
    assert (e.value.code, e.value.reason) == ("budget", "budget")


def test_too_many_entries_blocks_the_whole_archive(world):
    service = BundleInspection(world.files, world.cache,
                               budgets=bundle_archive.Budgets(max_entries=3))
    key = world.files.add(MATT, zip_bytes({f"{n}.txt": b"x" for n in range(5)}), "many.zip")
    c = service.classify(MATT, key)
    assert (c.support, c.reason, c.inventory) == ("blocked", "budget", None)
    assert service.list_members(MATT, key).members == []


def test_unicode_and_windows_made_zips_read_by_their_names(world):
    for data in (windows_zip({"Café notes.txt": b"caf\xe9 au lait\r\n",
                              "Docs/Résumé.md": b"# R\r\n"}),
                 zip_bytes({"Café notes.txt": "naïve ✓\n".encode(),
                            "Docs/Résumé.md": b"# R\n"})):
        key = world.files.add(MATT, data, "intl.zip")
        c = world.service.classify(MATT, key)
        assert c.support == "readable" and c.inventory.files == 2
        note = find(world.service, MATT, key, "Café notes.txt")
        text = world.service.read_member(MATT, key, note.member_id).chunks[0].text
        assert text in ("café au lait\n", "naïve ✓\n")
        assert find(world.service, MATT, key, "Docs/Résumé.md").format == "markdown"


# -- paging --------------------------------------------------------------------------------

def test_long_evidence_is_paged_and_never_silently_cut(world):
    text = "".join(f"Evidence line {n}: " + "detail " * 20 + "\n" for n in range(1, 2001))
    key = world.files.add(MATT, zip_bytes({"long.txt": text.encode()}), "long.zip")
    member = find(world.service, MATT, key, "long.txt")
    pieces, token, pages = [], None, 0
    while True:
        page = world.service.read_member(MATT, key, member.member_id, token, max_chars=20_000)
        pages += 1
        pieces += [c.text for c in page.chunks]
        assert page.complete == (page.continuation is None)
        token = page.continuation
        if page.complete:
            break
    assert "".join(pieces) == text and pages > 10
    assert page.extent.lines == 2000


def test_a_continuation_works_only_for_its_own_member(world):
    key = world.files.add(MATT, zip_bytes({"a.txt": b"a\n" * 5000, "b.txt": b"b\n" * 5000}),
                          "two.zip")
    a, b = (find(world.service, MATT, key, p) for p in ("a.txt", "b.txt"))
    token = world.service.read_member(MATT, key, a.member_id, max_chars=100).continuation
    for bad_member, bad_token in ((b.member_id, token), (a.member_id, "garbage")):
        with pytest.raises(InspectionRefused) as e:
            world.service.read_member(MATT, key, bad_member, bad_token)
        assert e.value.code == "bad_continuation"


def test_a_big_folder_is_listed_a_page_at_a_time(world):
    key = world.files.add(MATT, zip_bytes({f"f{n:03d}.txt": b"x" for n in range(250)}),
                          "many.zip")
    seen, token = [], None
    while True:
        page = world.service.list_members(MATT, key, "", token, limit=100)
        assert page.total == 250 and len(page.members) <= 100
        seen += [m.path for m in page.members]
        token = page.continuation
        if token is None:
            break
    assert seen == [f"f{n:03d}.txt" for n in range(250)]
    with pytest.raises(InspectionRefused) as e:
        world.service.list_members(MATT, key, "no-such-folder")
    assert e.value.code == "member_not_found"


def test_an_unknown_member_is_not_found(world):
    key = world.files.add(MATT, review_bundle(), "bundle.zip")
    with pytest.raises(InspectionRefused) as e:
        world.service.read_member(MATT, key, "m" + "0" * 24)
    assert e.value.code == "member_not_found"


# -- single files ----------------------------------------------------------------------------

def test_a_single_pdf_is_its_own_member(world):
    key = world.files.add(MATT, two_page_pdf(), "summary.pdf")
    c = world.service.classify(MATT, key)
    assert (c.format, c.container, c.support, c.inventory) == ("pdf", "none", "readable", None)
    page = world.service.list_members(MATT, key)
    assert [(m.path, m.name, m.operations) for m in page.members] == [
        ("", "summary.pdf", ["download", "read_text", "render"])]
    text = world.service.read_member(MATT, key, c.root_member_id)
    assert text.chunks[0].text == "Summary, page one."


def test_a_scanned_pdf_reads_as_partial_with_the_reason(world):
    key = world.files.add(MATT, scanned_pdf(2), "scan.pdf")
    c = world.service.classify(MATT, key)
    assert (c.support, c.reason) == ("partial", "scanned_no_text")
    page = world.service.read_member(MATT, key, c.root_member_id)
    assert page.notes == ["scanned_no_text"]
    assert (world.seen[-1].support, world.seen[-1].reason) == ("partial", "scanned_no_text")


def test_an_office_document_is_a_document_though_it_is_a_zip(world):
    key = world.files.add(MATT, docx_bytes(["Para one", "Para two"]), "report")
    c = world.service.classify(MATT, key)
    assert (c.format, c.container, c.package) == ("docx", "none", None)
    assert world.service.read_member(MATT, key, c.root_member_id).chunks[0].text == \
        "Para one\nPara two\n"


def test_legacy_office_and_media_are_honestly_unsupported(world):
    for data, name, reason in ((b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\0" * 64, "old.xls",
                                "reader_unavailable"),
                               (b"ID3\x04\x00" + b"\0" * 64, "talk.mp3", "reader_unavailable"),
                               (b"\x00\x01\x02\x03", "blob.bin", "unknown_format")):
        key = world.files.add(MATT, data, name)
        c = world.service.classify(MATT, key)
        assert (c.container, c.support, c.reason) == ("none", "unsupported", reason)
        page = world.service.list_members(MATT, key)
        assert page.members[0].operations == ["download"]


# -- previews -----------------------------------------------------------------------------

def html_bundle() -> bytes:
    return zip_bytes({"site/index.html": HOSTILE_HTML, "site/style.css": b"p{color:red}",
                      "site/img/chart.png": png_bytes(), "site/app.js": b"alert(1)",
                      "site/fonts/a.woff2": b"wOF2"}, folder="pack")


def test_an_html_preview_is_static_and_its_files_stay_in_the_archive(world):
    key = world.files.add(MATT, html_bundle(), "site.zip")
    page = find(world.service, MATT, key, "pack/site/index.html")
    preview = world.service.preview_member(MATT, key, page.member_id)
    (ref,) = preview.refs
    assert ref.kind == "html" and ref.url.endswith(
        f"/bundle/members/{page.member_id}/html/pack/site/index.html")
    assert "https://evil.example.com/x.js" in preview.blocked_resources
    assert world.service.html_asset(MATT, key, page.member_id, "pack/site/index.html") == \
        (HOSTILE_HTML, "text/html")
    assert world.service.html_asset(MATT, key, page.member_id, "pack/site/style.css") == \
        (b"p{color:red}", "text/css")
    assert world.service.html_asset(MATT, key, page.member_id,
                                    "pack/site/img/chart.png")[1] == "image/png"
    assert world.service.html_asset(MATT, key, page.member_id,
                                    "pack/site/fonts/a.woff2")[1] == "font/woff2"
    for relative, code in (("pack/site/app.js", "unsupported"),
                           ("../outside.txt", "member_not_found"),
                           ("/etc/passwd", "member_not_found"),
                           ("pack/site/missing.css", "member_not_found"),
                           ("pack/site", "member_not_found")):
        with pytest.raises(InspectionRefused) as e:
            world.service.html_asset(MATT, key, page.member_id, relative)
        assert e.value.code == code, relative


def test_an_image_preview_is_the_file_itself(world):
    key = world.files.add(MATT, zip_bytes({"shot.png": png_bytes(7, 5),
                                           "photo.jpg": jpeg_header(800, 600)}), "imgs.zip")
    shot = find(world.service, MATT, key, "shot.png")
    (ref,) = world.service.preview_member(MATT, key, shot.member_id).refs
    assert (ref.kind, ref.width, ref.height) == ("image", 7, 5)
    assert ref.url.endswith("/html/shot.png")
    assert world.service.html_asset(MATT, key, shot.member_id, "shot.png") == \
        (png_bytes(7, 5), "image/png")
    assert world.service.render_page(MATT, key, shot.member_id, 1).png == png_bytes(7, 5)
    photo = find(world.service, MATT, key, "photo.jpg")
    assert photo.operations == []
    (ref,) = world.service.preview_member(MATT, key, photo.member_id).refs
    assert (ref.width, ref.height) == (800, 600)
    with pytest.raises(InspectionRefused) as e:
        world.service.render_page(MATT, key, photo.member_id, 1)
    assert e.value.code == "unsupported"


def test_a_single_html_file_serves_only_itself(world):
    key = world.files.add(MATT, HOSTILE_HTML, "report.html")
    c = world.service.classify(MATT, key)
    (ref,) = world.service.preview_member(MATT, key, c.root_member_id).refs
    assert ref.url.endswith("/html/report.html")
    assert world.service.html_asset(MATT, key, c.root_member_id, "report.html")[0] == \
        HOSTILE_HTML
    with pytest.raises(InspectionRefused):
        world.service.html_asset(MATT, key, c.root_member_id, "style.css")


def test_text_has_no_preview(world):
    key = world.files.add(MATT, b"notes\n", "notes.txt")
    c = world.service.classify(MATT, key)
    with pytest.raises(InspectionRefused) as e:
        world.service.preview_member(MATT, key, c.root_member_id)
    assert e.value.code == "unsupported"


# -- read-only, always ----------------------------------------------------------------------

def test_inspection_runs_nothing_and_never_clears_quarantine(world, monkeypatch):
    ran, cleared = [], []
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: ran.append(a) or None)
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: ran.append(a))
    monkeypatch.setattr(work_packages, "unquarantine", cleared.append)
    monkeypatch.setattr(os, "removexattr", lambda *a, **k: cleared.append(a), raising=False)
    executable = raw_zip([("run.sh", b"#!/bin/sh\nrm -rf ~\n", 0o100755),
                          ("summary.pdf", two_page_pdf(), FILE_MODE),
                          ("package.json", b'{"scripts": {"postinstall": "evil"}}', FILE_MODE),
                          ("page.html", HOSTILE_HTML, FILE_MODE)])
    key = world.files.add(MATT, executable, "bundle.zip")
    world.service.classify(MATT, key)
    for path in ("run.sh", "summary.pdf", "page.html", "package.json"):
        member = find(world.service, MATT, key, path)
        world.service.read_member(MATT, key, member.member_id)
    pdf = find(world.service, MATT, key, "summary.pdf")
    world.service.render_page(MATT, key, pdf.member_id, 1)
    world.service.preview_member(MATT, key, find(world.service, MATT, key,
                                                 "page.html").member_id)
    assert ran == [] and cleared == []
    assert all(c[0] == _quartz.OSASCRIPT for c in world.osascript.calls)
    assert not any(data_paths.packages_dir().iterdir())
    scratch = world.cache.scratch(MATT.server_id, MATT.owner_github_id)
    assert list(scratch.iterdir()) == [], "every private copy is gone"
    for dirpath, _, names in os.walk(world.cache.root()):
        for name in names:
            mode = os.stat(Path(dirpath) / name).st_mode
            assert not mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def test_a_failing_observer_never_breaks_a_read(world):
    def broken(observation):
        raise RuntimeError("observer down")
    service = BundleInspection(world.files, world.cache, observer=broken)
    key = world.files.add(MATT, b"words\n", "a.txt")
    c = service.classify(MATT, key)
    assert service.read_member(MATT, key, c.root_member_id).chunks[0].text == "words\n"


# -- through the real files service ------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_received_bundle_is_downloaded_once_quarantined_and_inspected(tmp_path,
                                                                             fake_osascript):
    from tests.test_messaging_files import MATT as MATT_ID, SERVER as FILES_SERVER, TIM as \
        TIM_ID, World
    w = World()
    draft = await w.tim.new_message(MATT_ID, [review_bundle(manifest=None)])
    await w.tim.send(draft)
    await w.matt.sync()
    ref = draft.attachments[0]
    seen = []
    service = BundleInspection(w.matt.files, DerivedCache(tmp_path / "cache"),
                               observer=seen.append)
    owner, key = Owner(FILES_SERVER, MATT_ID), AttachmentKey(draft.message_id,
                                                             ref.attachment_id)
    with pytest.raises(InspectionRefused):
        service.classify(owner, key)
    c = await service.open(owner, key)
    assert c.attachment.sha256 == ref.sha256 and c.inventory.wrapper == WRAPPER
    assert len(w.quarantined) == 1, "kept with the downloaded-file mark"
    for other in (TIM_ID, 999):                       # the sender, and a stranger
        with pytest.raises(InspectionRefused) as e:
            service.classify(Owner(FILES_SERVER, other), key)
        assert e.value.code in ("attachment_not_found", "not_local")
    assert [o.operation for o in seen][:2] == ["download", "list_archive"]
    assert [m.path for m in service.list_members(owner, key).members] == [WRAPPER]
