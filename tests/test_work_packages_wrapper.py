"""A work package found where people put it (`work_packages.recognise` and
`unpack` over `bundle_archive`): package.json at the zip's root or in its
one wrapper folder, every path it names read from that folder, a zip that
is no package said plainly, and the same bytes opened twice giving the one
package. Every zip is made in code (tests/bundle_fixtures.py); each test
has its own JARVIS_DATA_DIR (conftest) and nothing runs xattr."""
from __future__ import annotations

import json
import os
import stat

import pytest

from jarvis import bundle_archive, data_paths, packages_api, work_packages
from tests.bundle_fixtures import (REVIEW_MANIFEST, WRAPPER, review_bundle, review_files,
                                   two_page_pdf, zip_bytes)
from tests.test_package_modes import AGENT, PAGE
from tests.test_work_packages import MANIFEST, marks, write_zip  # noqa: F401  (the fixture)

ATTACHMENT = "c0ffee00-1111-2222-3333-444455556666"
OTHER = "deadbeef-1111-2222-3333-444455556666"
ATTACHMENT_KEY, OTHER_KEY = "c0ffee00111122223333444455556666", "deadbeef111122223333444455556666"


def recognised(tmp_path, data: bytes) -> work_packages.Recognition:
    with bundle_archive.open_archive(write_zip(tmp_path, data, "probe.zip")) as z:
        return work_packages.recognise(z, bundle_archive.list_members(z))


def package_dirs() -> list[str]:
    return sorted(p.name for p in data_paths.packages_dir().iterdir()
                  if not p.name.startswith("."))


# -- the incident's shape: everything in one folder ------------------------------------

def test_the_review_bundle_in_a_wrapper_folder_opens_as_its_package(tmp_path, marks):
    archive = write_zip(tmp_path, review_bundle(mac_metadata=True))
    package, fresh = work_packages.unpack(archive, ATTACHMENT)
    assert fresh and package.id == "example-summary-review-1.0.0-c0ffee00"
    assert package.manifest.name == REVIEW_MANIFEST["name"]
    assert package.skill_text().startswith("# Review")
    assert (package.folder / "package.json").is_file(), "the wrapper is taken off"
    assert (package.folder / "summary.pdf").read_bytes() == two_page_pdf()
    assert (package.folder / "evidence/notes/interview.txt").is_file()
    assert not (package.folder / WRAPPER).exists()
    assert not (package.folder / ".DS_Store").exists()
    assert not (package.folder / "__MACOSX").exists()
    assert marks == [package.folder]


def test_root_and_wrapper_layouts_give_the_same_files(tmp_path, marks):
    flat, _ = work_packages.unpack(write_zip(tmp_path, review_bundle(wrapper=False), "a.zip"),
                                   ATTACHMENT)
    wrapped_manifest = {**REVIEW_MANIFEST, "version": "1.0.1"}
    wrapped, _ = work_packages.unpack(
        write_zip(tmp_path, review_bundle(manifest=wrapped_manifest), "b.zip"), OTHER)
    def tree(folder):
        return sorted(p.relative_to(folder).as_posix() for p in folder.rglob("*"))
    assert tree(flat.folder) == tree(wrapped.folder)


def test_each_way_a_package_runs_opens_from_a_wrapper_folder(tmp_path, marks):
    for manifest, files in ((MANIFEST, {"SKILL.md": b"x"}), (AGENT, {"BRIEF.md": b"brief"}),
                            (PAGE, {"web/index.html": b"<p>hi</p>"})):
        data = zip_bytes({"package.json": json.dumps(manifest).encode(), **files},
                         folder="wrapped")
        package, fresh = work_packages.unpack(write_zip(tmp_path, data, f"{manifest['name']}.zip"),
                                              f"{manifest['name']}-attachment")
        assert fresh and package.manifest.runs == manifest.get("runs", "conversation")
    agent = work_packages.find("stats", work_packages.AGENT)
    assert agent.brief_text() == "brief"
    page = work_packages.find("check", work_packages.PAGE)
    assert work_packages.page_file(page, "web/index.html") == b"<p>hi</p>"


def test_a_package_s_scripts_keep_their_execute_bit_from_a_wrapper(tmp_path, marks):
    import io
    import zipfile
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        z.writestr("w/package.json", json.dumps(MANIFEST))
        z.writestr("w/SKILL.md", "x")
        info = zipfile.ZipInfo("w/scripts/go.sh")
        info.external_attr = 0o100755 << 16
        z.writestr(info, "#!/bin/sh\n")
    package, _ = work_packages.unpack(write_zip(tmp_path, out.getvalue()), ATTACHMENT)
    assert os.stat(package.folder / "scripts/go.sh").st_mode & stat.S_IXUSR


# -- what is not a package -----------------------------------------------------------------

@pytest.mark.parametrize("data,said", [
    (review_bundle(manifest=None), "no package.json at its root or in one folder"),
    (review_bundle(wrapper=False, manifest=None), "no package.json"),
    (zip_bytes({"a/package.json": json.dumps(MANIFEST).encode(), "a/SKILL.md": b"x",
                "b/package.json": json.dumps(MANIFEST).encode(), "b/SKILL.md": b"x"}),
     r"several package.json files \(a/package.json, b/package.json\)"),
    (zip_bytes({"a/package.json": json.dumps(MANIFEST).encode(), "a/SKILL.md": b"x",
                "README.md": b"hi"}),
     r"package.json is in a/, but that is not the zip's only folder"),
    (zip_bytes({"package.json": b"{not json"}, folder="w"), "not valid JSON"),
    (zip_bytes({"package.json": b"[1, 2]"}, folder="w"), "not an object"),
    (zip_bytes({"package.json": json.dumps({"name": "left-pad", "version": "1.3.0",
                                            "main": "index.js",
                                            "scripts": {"test": "jest"}}).encode(),
                "index.js": b"module.exports = 1;\n"}, folder="left-pad"),
     "not a JARVIS work package's"),
    (zip_bytes({"package.json": json.dumps(MANIFEST).encode()}, folder="w"),
     "skill file 'SKILL.md' is not in the zip"),
    (zip_bytes({"package.json": json.dumps(MANIFEST).encode(), "SKILL.md": b"x"}, folder="w",
               extra={"SKILL.md": b"at the root, not beside package.json"}),
     "package.json is in w/, but that is not the zip's only folder"),
], ids=["wrapper-without-manifest", "flat-without-manifest", "several-manifests",
        "manifest-folder-not-alone", "malformed-json", "not-an-object", "npm-package-json",
        "named-file-missing", "file-outside-the-wrapper"])
def test_what_is_not_a_package_is_said_and_nothing_is_unpacked(tmp_path, marks, data, said):
    found = recognised(tmp_path, data)
    assert found.manifest is None and found.reason
    with pytest.raises(work_packages.PackageError, match=said):
        work_packages.unpack(write_zip(tmp_path, data), ATTACHMENT)
    assert list(data_paths.packages_dir().iterdir()) == [] and marks == []


def test_a_manifest_at_the_root_wins_over_one_in_a_subfolder(tmp_path, marks):
    data = zip_bytes({"package.json": json.dumps(MANIFEST).encode(), "SKILL.md": b"root",
                      "tool/package.json": json.dumps({"name": "tool"}).encode()})
    found = recognised(tmp_path, data)
    assert found.manifest is not None and found.root == ""
    package, _ = work_packages.unpack(write_zip(tmp_path, data), ATTACHMENT)
    assert package.skill_text() == "root" and (package.folder / "tool/package.json").is_file()


def test_an_unsafe_entry_still_refuses_the_whole_zip_in_a_wrapper(tmp_path, marks):
    data = zip_bytes({"package.json": json.dumps(MANIFEST).encode(), "SKILL.md": b"x"},
                     folder="w", extra={"w/../../escaped.txt": b"owned"})
    with pytest.raises(work_packages.PackageError, match="outside the folder"):
        work_packages.unpack(write_zip(tmp_path, data), ATTACHMENT)
    assert list(data_paths.packages_dir().iterdir()) == []


def test_an_archive_budget_refuses_a_package(tmp_path, marks, monkeypatch):
    monkeypatch.setattr(bundle_archive, "DEFAULT_BUDGETS", bundle_archive.Budgets(max_entries=3))
    with pytest.raises(work_packages.PackageError, match="entries"):
        work_packages.unpack(write_zip(tmp_path, review_bundle()), ATTACHMENT)
    assert list(data_paths.packages_dir().iterdir()) == []


# -- opening again --------------------------------------------------------------------------

def test_the_same_bytes_opened_again_are_the_same_package_whatever_the_attachment(tmp_path,
                                                                                  marks):
    data = review_bundle()
    first, fresh = work_packages.unpack(write_zip(tmp_path, data, "one.zip"), ATTACHMENT)
    assert fresh
    again, fresh = work_packages.unpack(write_zip(tmp_path, data, "two.zip"), ATTACHMENT)
    assert not fresh and again.folder == first.folder
    resent, fresh = work_packages.unpack(write_zip(tmp_path, data, "three.zip"), OTHER)
    assert not fresh and resent.folder == first.folder
    assert resent.sources == (ATTACHMENT_KEY, OTHER_KEY)
    assert package_dirs() == [first.id] and marks == [first.folder]
    described = packages_api.describe(resent)
    assert described["source"] == "c0ffee00" and len(described["sources"]) == 2


def test_different_bytes_under_one_name_version_and_id_start_are_two_packages(tmp_path, marks):
    first, _ = work_packages.unpack(write_zip(tmp_path, review_bundle(), "one.zip"),
                                    "c0ffee00-aaaa")
    changed = zip_bytes({**review_files(), "SKILL.md": b"# Review, changed\n"}, WRAPPER)
    second, fresh = work_packages.unpack(write_zip(tmp_path, changed, "two.zip"),
                                         "c0ffee00-bbbb")
    assert fresh and second.folder != first.folder
    assert second.skill_text() == "# Review, changed\n" and first.skill_text() != \
        second.skill_text()
    assert second.source == "c0ffee00"


def test_a_package_unpacked_before_records_were_kept_is_still_used(tmp_path, marks):
    data = review_bundle(wrapper=False)
    old = data_paths.packages_dir() / "example-summary-review-1.0.0-c0ffee00"
    old.mkdir()
    for name, body in review_files().items():
        (old / name).parent.mkdir(parents=True, exist_ok=True)
        (old / name).write_bytes(body)
    (old / "results").mkdir()
    (old / "results" / "notes.md").write_text("earlier notes")
    assert work_packages.find(old.name).sources == ("c0ffee00",)
    package, fresh = work_packages.unpack(write_zip(tmp_path, data), ATTACHMENT)
    assert not fresh and package.folder == old
    assert (old / "results" / "notes.md").read_text() == "earlier notes"
    assert package.sources == (ATTACHMENT_KEY,)
    assert package_dirs() == [old.name] and marks == []
