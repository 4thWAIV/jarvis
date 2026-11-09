"""Run the shipped upstream bundle when JARVIS_LANGUAGE_KIT_ASSET is supplied.

The release asset belongs in external runtime storage, never repository source.
The same tests run in the isolated review and the dedicated runtime CI job.
"""
import json
import os
import zipfile
from pathlib import Path

import pytest

from jarvis import language_kit as lk


@pytest.fixture
def kit(tmp_path, monkeypatch):
    asset = os.getenv("JARVIS_LANGUAGE_KIT_ASSET")
    if not asset:
        pytest.skip("Supply the pinned external release asset for upstream integration tests")
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    return lk.stage(Path(asset), lk.Release(lk.PINNED_VERSION, lk.PINNED_COMMIT,
                                           lk.PINNED_DIGEST, ("gate", "release")))


def test_release_evidence_requires_publisher_commit_and_ci():
    with pytest.raises(lk.KitError, match="required gate"):
        lk.Release(lk.PINNED_VERSION, lk.PINNED_COMMIT, lk.PINNED_DIGEST, ()).validate()
    with pytest.raises(lk.KitError, match="publisher"):
        lk.Release(lk.PINNED_VERSION, lk.PINNED_COMMIT, lk.PINNED_DIGEST,
                   ("gate", "release"), "someone/else").validate()


def test_digest_failure_keeps_active_version(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    archive = tmp_path / "bad.zip"
    archive.write_bytes(b"wrong asset")
    with pytest.raises(lk.KitError, match="SHA256"):
        lk.stage(archive, lk.Release(lk.PINNED_VERSION, lk.PINNED_COMMIT,
                                    lk.PINNED_DIGEST, ("gate", "release")))
    assert not (lk.root() / "active.json").exists()


def test_archive_path_escape_is_refused(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    archive = tmp_path / "escape.zip"
    with zipfile.ZipFile(archive, "w") as zipped:
        zipped.writestr("../../escape", "no")
    with pytest.raises(lk.KitError, match="archive was refused"):
        lk.stage(archive, lk.Release("test", lk.PINNED_COMMIT,
                                    lk.sha256(archive.read_bytes()), ("gate", "release")))
    assert not (tmp_path.parent / "escape").exists()


def test_upstream_check_and_render_and_document_diagnostics(kit):
    source = (kit.directory / "arch.yaml").read_bytes()
    assert kit.run("check", source).ok
    rendered = kit.run("render", source)
    assert rendered.ok and b"<html" in rendered.view
    invalid = kit.run("check", b"language: arch\nscope: [test]\ndeclare: [garbage]\n")
    assert not invalid.ok and invalid.diagnostics
    missing = kit.run("check", b"language: evolang\nscope: [test]\ndeclare: []\n")
    assert not missing.ok and "names no file" in missing.diagnostics
    malformed = kit.run("check", b"language: [\n")
    assert not malformed.ok and malformed.diagnostics


def test_import_escape_cannot_read_outside_the_allowed_roots(kit, tmp_path):
    outside = tmp_path / "secret.yaml"
    outside.write_text("scope: [secret]\n")
    source = f"language: {outside.with_suffix('')}\nscope: [test]\ndeclare: []\n".encode()
    result = kit.run("check", source)
    assert not result.ok
    source = b"language: arch\nscope: [test]\nimport: [[.., .., secret]]\ndeclare: []\n"
    assert not kit.run("check", source).ok


def test_version_selection_survives_restart_and_checks_tampering(kit):
    lk.activate(kit)
    assert lk.active().release.digest == kit.release.digest
    source = kit.directory / "arch.yaml"
    source.write_bytes(source.read_bytes() + b"\n# changed\n")
    with pytest.raises(lk.KitError, match="changed"):
        lk.active()


def test_source_size_is_bounded_before_execution(kit):
    with pytest.raises(lk.KitError, match="exceeds"):
        kit.run("check", b" " * (lk.MAX_SOURCE_BYTES + 1))


def updated_asset(original, path, *, language_change=False):
    version = original.release.version + ".review"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zipped:
        for name in lk.REQUIRED_FILES:
            raw = (original.directory / name).read_bytes()
            if name == "package.json":
                manifest = json.loads(raw)
                manifest["version"] = version
                raw = json.dumps(manifest).encode()
            if name == "style.css":
                raw += b"\n/* reviewed stylesheet update */\n"
            if name == "arch.yaml" and language_change:
                raw += b"\n# language compatibility fixture\n"
            zipped.writestr("evolang-kit/" + name, raw)
    return lk.Release(version, lk.PINNED_COMMIT, lk.sha256(path.read_bytes()), ("gate", "release"))


def test_update_and_rollback_keep_document_kits_pinned(kit, tmp_path):
    lk.activate(kit)
    archive = tmp_path / "update.zip"
    candidate = lk.stage(archive, updated_asset(kit, archive))
    lk.activate(candidate)
    assert lk.active().release.digest == candidate.release.digest
    assert lk.active(kit.release.digest).run("check", (kit.directory / "arch.yaml").read_bytes()).ok
    lk.rollback()
    assert lk.active().release.digest == kit.release.digest
    with pytest.raises(lk.KitError, match="held after rollback"):
        lk.activate(candidate)
    assert lk.active().release.digest == kit.release.digest
    lk.activate(candidate, compatibility_reviewed=True)
    assert lk.active().release.digest == candidate.release.digest


def test_language_changes_wait_for_compatibility_review(kit, tmp_path):
    lk.activate(kit)
    archive = tmp_path / "breaking.zip"
    candidate = lk.stage(archive, updated_asset(kit, archive, language_change=True))
    with pytest.raises(lk.KitError, match="compatibility review"):
        lk.activate(candidate)
    assert lk.active().release.digest == kit.release.digest
    lk.activate(candidate, compatibility_reviewed=True)
    assert lk.active().release.digest == candidate.release.digest
