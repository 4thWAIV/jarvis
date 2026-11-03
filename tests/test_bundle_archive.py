"""Reading a received ZIP (`jarvis/bundle_archive.py`): every entry checked
before anything is written, budgets counted on what is actually
decompressed, one wrapper folder found with macOS metadata set aside, and
extraction staged privately and renamed into place whole or not at all.
Every zip here is made in code."""
from __future__ import annotations

import io
import os
import stat
import zipfile
from pathlib import Path

import pytest

from jarvis import bundle_archive as ba
from tests.bundle_fixtures import (FILE_MODE, WRAPPER, encrypt_entries, raw_zip, review_bundle,
                                   review_files, two_page_pdf, zip_bytes)


def opened(tmp_path: Path, data: bytes, name: str = "in.zip") -> zipfile.ZipFile:
    path = tmp_path / name
    path.write_bytes(data)
    return ba.open_archive(path)


FILE = FILE_MODE


# -- listing -----------------------------------------------------------------------------

def test_a_flat_zip_has_no_wrapper(tmp_path):
    with opened(tmp_path, review_bundle(wrapper=False)) as z:
        listing = ba.list_members(z)
    assert listing.wrapper == "" and listing.root == ""
    assert set(listing.files()) == set(review_files())


def test_one_folder_holding_everything_is_the_wrapper_and_mac_metadata_is_set_aside(tmp_path):
    with opened(tmp_path, review_bundle(mac_metadata=True)) as z:
        listing = ba.list_members(z)
    assert listing.wrapper == WRAPPER and listing.root == f"{WRAPPER}/"
    assert {m.path for m in listing.ignored} == {f"__MACOSX/{WRAPPER}/._summary.pdf",
                                                f"{WRAPPER}/.DS_Store"}
    assert f"{WRAPPER}/evidence/notes/interview.txt" in listing.files()


@pytest.mark.parametrize("entries,wrapper", [
    ([("a/x.txt", b"x", FILE), ("b/y.txt", b"y", FILE)], ""),
    ([("a/x.txt", b"x", FILE), ("top.txt", b"t", FILE)], ""),
    ([("only.txt", b"x", FILE)], ""),
    ([("a/", b"", 0o40755)], ""),
    ([("a/b/c.txt", b"x", FILE)], "a"),
])
def test_a_wrapper_is_exactly_one_folder_holding_everything(tmp_path, entries, wrapper):
    with opened(tmp_path, raw_zip(entries)) as z:
        assert ba.list_members(z).wrapper == wrapper


def test_a_manifest_free_zip_lists_as_an_ordinary_archive(tmp_path):
    with opened(tmp_path, review_bundle(manifest=None)) as z:
        listing = ba.list_members(z)
        assert "package.json" not in {p.split("/")[-1] for p in listing.files()}
        pdf = listing.files()[f"{WRAPPER}/summary.pdf"]
        assert ba.read_member(z, pdf) == two_page_pdf()


def test_what_is_not_a_zip_is_said(tmp_path):
    (tmp_path / "x.zip").write_bytes(b"not a zip")
    with pytest.raises(ba.ArchiveError, match="not a zip") as e:
        ba.open_archive(tmp_path / "x.zip")
    assert e.value.reason == "not-zip"


@pytest.mark.parametrize("name,reason", [
    ("../escaped.txt", "outside"),
    ("a/../../escaped.txt", "outside"),
    ("/etc/escaped.txt", "outside"),
    ("C:/escaped.txt", "outside"),
    ("C:escaped.txt", "outside"),
    ("..\\escaped.txt", "outside"),
    ("\\\\server\\share\\x.txt", "outside"),
    ("a\x01b.txt", "bad-name"),
])
def test_a_name_that_could_land_anywhere_else_refuses_the_whole_zip(tmp_path, name, reason):
    with opened(tmp_path, raw_zip([("ok.txt", b"x", FILE), (name, b"owned", FILE)])) as z:
        with pytest.raises(ba.ArchiveError) as e:
            ba.list_members(z)
    assert e.value.reason == reason


@pytest.mark.parametrize("mode,said", [
    (0o120777, "is a link"), (0o020644, "device"), (0o060644, "device"),
    (0o010644, "pipe"), (0o140644, "pipe"),
])
def test_links_devices_and_pipes_are_refused(tmp_path, mode, said):
    with opened(tmp_path, raw_zip([("thing", b"/etc/passwd", mode)])) as z:
        with pytest.raises(ba.ArchiveError, match=said):
            ba.list_members(z)


@pytest.mark.parametrize("names", [
    ["a.txt", "a.txt"], ["a.txt", "./a.txt"], ["Read.md", "READ.md"],
    ["caf\u00e9.txt", "cafe\u0301.txt"], ["dir/x", "Dir/y", "DIR"],
    ["a", "a/b.txt"],
])
def test_two_entries_landing_at_one_path_are_refused(tmp_path, names):
    with opened(tmp_path, raw_zip([(n, b"x", FILE) for n in names])) as z:
        with pytest.raises(ba.ArchiveError) as e:
            ba.list_members(z)
    assert e.value.reason == "collision"


def test_an_encrypted_entry_is_refused(tmp_path):
    data = bytearray(raw_zip([("secret.txt", b"x", FILE)]))
    # Set the encrypted bit in the central directory's general purpose flags.
    central = data.rfind(b"PK\x01\x02")
    data[central + 8] |= 0x1
    with opened(tmp_path, bytes(data)) as z:
        with pytest.raises(ba.ArchiveError, match="encrypted"):
            ba.list_members(z)


def test_entry_count_depth_and_claimed_size_budgets(tmp_path):
    many = raw_zip([(f"f{i}.txt", b"x", FILE) for i in range(6)])
    with opened(tmp_path, many) as z:
        with pytest.raises(ba.ArchiveError, match="6 entries"):
            ba.list_members(z, ba.Budgets(max_entries=5))
    deep = raw_zip([("/".join(["d"] * 5) + "/x.txt", b"x", FILE)])
    with opened(tmp_path, deep, "deep.zip") as z:
        with pytest.raises(ba.ArchiveError, match="folders deep"):
            ba.list_members(z, ba.Budgets(max_depth=5))
    big = raw_zip([("a.txt", b"x" * 100, FILE), ("b.txt", b"y" * 100, FILE)])
    with opened(tmp_path, big, "big.zip") as z:
        with pytest.raises(ba.ArchiveError, match="99"):
            ba.list_members(z, ba.Budgets(max_file_bytes=99))
        with pytest.raises(ba.ArchiveError, match="holds 200 bytes"):
            ba.list_members(z, ba.Budgets(max_total_bytes=150))


# -- reading: budgets on what is actually decompressed ---------------------------------

def lying_zip(actual: bytes, claimed: int) -> bytes:
    """A deflated zip whose central directory claims `claimed` bytes for a
    member that really expands to `actual`."""
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("bomb.bin", actual)
    data = bytearray(out.getvalue())
    for signature, offset in ((b"PK\x01\x02", 24), (b"PK\x03\x04", 22)):
        at = data.find(signature)
        data[at + offset:at + offset + 4] = claimed.to_bytes(4, "little")
    return bytes(data)


def test_the_size_budget_is_counted_on_what_is_decompressed_not_what_is_claimed(tmp_path):
    with opened(tmp_path, lying_zip(b"\x00" * 50_000, claimed=10)) as z:
        listing = ba.list_members(z, ba.Budgets(max_file_bytes=1000))
        with pytest.raises(ba.ArchiveError) as e:
            ba.read_member(z, listing.files()["bomb.bin"], ba.Budgets(max_file_bytes=1000))
    assert e.value.reason in ("too-large", "corrupt")


def test_a_member_expanding_far_past_its_compressed_size_is_refused(tmp_path):
    data = zip_bytes({"zeros.bin": b"\x00" * 3_000_000})
    with opened(tmp_path, data) as z:
        member = ba.list_members(z).files()["zeros.bin"]
        with pytest.raises(ba.ArchiveError) as e:
            ba.read_member(z, member, ba.Budgets(max_ratio=50, ratio_floor=1024))
        assert e.value.reason == "ratio"
        assert len(ba.read_member(z, member, ba.Budgets(max_ratio=10_000))) == 3_000_000


def test_a_damaged_member_is_said(tmp_path):
    data = bytearray(zip_bytes({"text.txt": b"hello world " * 200}))
    at = data.find(b"PK\x03\x04") + 30 + len("text.txt") + 5
    data[at:at + 20] = b"\xff" * 20
    with opened(tmp_path, bytes(data)) as z:
        member = ba.list_members(z).files()["text.txt"]
        with pytest.raises(ba.ArchiveError) as e:
            ba.read_member(z, member)
    assert e.value.reason == "corrupt"


# -- extracting --------------------------------------------------------------------------

def test_extract_takes_the_wrapper_off_and_keeps_no_execute_bit_by_default(tmp_path):
    data = zip_bytes({"run.sh": b"#!/bin/sh\n", "doc/a.md": b"a"}, folder="wrap",
                     extra={"__MACOSX/wrap/._a.md": b"x"})
    with opened(tmp_path, data) as z:
        listing = ba.list_members(z)
        out = ba.extract(z, listing, tmp_path / "out", folder=listing.wrapper)
    assert sorted(p.relative_to(out).as_posix() for p in out.rglob("*")) == \
        ["doc", "doc/a.md", "run.sh"]
    assert not os.stat(out / "run.sh").st_mode & stat.S_IXUSR
    assert not any(p.name.startswith(ba.STAGING) for p in tmp_path.iterdir())


def test_extract_keeps_permission_bits_only_when_asked_and_never_set_id(tmp_path):
    data = raw_zip([("run.sh", b"#!/bin/sh\n", 0o104755)])
    with opened(tmp_path, data) as z:
        out = ba.extract(z, ba.list_members(z), tmp_path / "out", keep_modes=True)
    mode = os.stat(out / "run.sh").st_mode
    assert mode & stat.S_IXUSR and not mode & stat.S_ISUID


def test_a_failure_part_way_leaves_nothing_behind(tmp_path):
    data = zip_bytes({"a.txt": b"fine", "zeros.bin": b"\x00" * 3_000_000})
    with opened(tmp_path, data) as z:
        listing = ba.list_members(z)
        with pytest.raises(ba.ArchiveError):
            ba.extract(z, listing, tmp_path / "out",
                       budgets=ba.Budgets(max_ratio=50, ratio_floor=1024))
    assert sorted(p.name for p in tmp_path.iterdir()) == ["in.zip"]


def test_an_interrupted_extraction_leaves_nothing_behind(tmp_path, monkeypatch):
    def interrupted(*_):
        raise KeyboardInterrupt
    with opened(tmp_path, zip_bytes({"a.txt": b"a", "b.txt": b"b"})) as z:
        listing = ba.list_members(z)
        monkeypatch.setattr(ba.os, "chmod", interrupted)
        with pytest.raises(KeyboardInterrupt):
            ba.extract(z, listing, tmp_path / "out")
    assert sorted(p.name for p in tmp_path.iterdir()) == ["in.zip"]


def test_extract_never_writes_over_a_folder_that_exists(tmp_path):
    (tmp_path / "out").mkdir()
    with opened(tmp_path, zip_bytes({"a.txt": b"a"})) as z:
        with pytest.raises(FileExistsError):
            ba.extract(z, ba.list_members(z), tmp_path / "out")
    assert list((tmp_path / "out").iterdir()) == []


# -- per-entry listing, for read-only browsing ---------------------------------------------

def test_a_per_entry_listing_sets_bad_entries_aside_and_keeps_the_rest(tmp_path):
    entries = [("ok/a.txt", b"a", FILE), ("../escaped.txt", b"x", FILE),
               ("ok/link", b"/etc/passwd", 0o120777), ("ok/A.TXT", b"dup", FILE),
               ("C:/x.txt", b"x", FILE), ("ok/b.txt", b"b", FILE)]
    with opened(tmp_path, raw_zip(entries)) as z:
        with pytest.raises(ba.ArchiveError):
            ba.list_members(z)                     # strict, as work packages are
        listing = ba.list_members(z, per_entry=True)
    assert set(listing.files()) == {"ok/a.txt", "ok/b.txt"}
    assert [(r.name, r.reason, r.kind) for r in listing.refused] == [
        ("../escaped.txt", "outside", ""), ("ok/link", "bad-kind", "link"),
        ("ok/A.TXT", "collision", ""), ("C:/x.txt", "outside", "")]


def test_a_per_entry_listing_sets_aside_an_entry_that_needs_a_file_as_a_folder(tmp_path):
    entries = [("a", b"file", FILE), ("a/b.txt", b"x", FILE), ("c.txt", b"c", FILE)]
    with opened(tmp_path, raw_zip(entries)) as z:
        listing = ba.list_members(z, per_entry=True)
    assert set(listing.files()) == {"a", "c.txt"}
    assert [(r.name, r.reason) for r in listing.refused] == [("a/b.txt", "collision")]


def test_encrypted_entries_are_refused_one_by_one_when_browsing(tmp_path):
    data = encrypt_entries(raw_zip([("s1.txt", b"x", FILE), ("s2.txt", b"y", FILE)]))
    with opened(tmp_path, data) as z:
        listing = ba.list_members(z, per_entry=True)
    assert listing.members == ()
    assert {(r.name, r.kind) for r in listing.refused} == {("s1.txt", "encrypted"),
                                                          ("s2.txt", "encrypted")}


def test_whole_archive_budgets_refuse_even_a_per_entry_listing(tmp_path):
    entries = [(f"f{n}.txt", b"x", FILE) for n in range(5)]
    with opened(tmp_path, raw_zip(entries)) as z:
        with pytest.raises(ba.ArchiveError) as e:
            ba.list_members(z, ba.Budgets(max_entries=4), per_entry=True)
    assert e.value.reason == "too-many"


def test_a_head_is_only_the_first_bytes(tmp_path):
    with opened(tmp_path, zip_bytes({"big.txt": b"0123456789" * 10_000})) as z:
        member = ba.list_members(z).files()["big.txt"]
        assert ba.read_head(z, member, 16) == b"0123456789012345"
        assert ba.read_head(z, member, 1 << 20) == b"0123456789" * 10_000
