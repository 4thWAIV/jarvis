"""Architecture publication through the existing package factory and receiver.

The shared gate checks both local generation and incoming package submission.
It operates on a bounded byte reader, so an archive and a folder use one gate.
"""
from __future__ import annotations

import json
import re
import shutil
import tempfile
import threading
from collections import OrderedDict
from pathlib import Path

from jarvis import language_documents as docs, language_kit as kit

IDENTITY = ("document_id", "revision", "source_revision", "kit_digest")
# The one authored Markdown member a projection carries, and the origin of a
# fresh author's (make_package --document) as canonical.json binds it.
MEMBER = "UNDERSTANDING.md"
FRESH_AUTHOR = "fresh-author"
_publish_lock = threading.RLock()
_checked_projections: OrderedDict[str, None] = OrderedDict()
_gate_lock = threading.Lock()


def gate(read, communication: dict | None) -> None:
    if communication is None:
        return
    if not isinstance(communication, dict) or communication.get("kind") not in {"general", "architecture"}:
        raise docs.DocumentError("Communication must declare kind general or architecture.")
    if communication["kind"] == "general":
        return
    canonical_bytes = read("canonical.json")
    canonical = json.loads(canonical_bytes)
    if not isinstance(canonical, dict) or any(canonical.get(key) != communication.get(key) for key in IDENTITY):
        raise docs.DocumentError("The architecture package is missing its exact canonical revision binding.")
    if (not isinstance(canonical.get("document_id"), str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,100}", canonical["document_id"])
            or any(not isinstance(canonical.get(key), str) or not re.fullmatch(r"[0-9a-f]{64}", canonical[key]) for key in ("revision", "source_revision", "kit_digest"))
            or not isinstance(canonical.get("metadata"), dict)):
        raise docs.DocumentError("The canonical identity or metadata is malformed.")
    if canonical["revision"] != docs.revision_for(canonical["document_id"], canonical["source_revision"], canonical["kit_digest"], canonical["metadata"], canonical.get("parent_revision")):
        raise docs.DocumentError("The canonical revision does not match its source and provenance.")
    if canonical.get("work_id") != canonical["metadata"].get("work_id") or not canonical.get("work_id"):
        raise docs.DocumentError("The Work linkage differs from the immutable canonical provenance.")
    source, view, check_bytes = read("source.yaml"), read("index.html"), read("check.json")
    evidence = json.loads(check_bytes)
    if (not isinstance(evidence, dict) or kit.sha256(source) != canonical.get("source_revision")
            or kit.sha256(view) != canonical.get("view_sha256")
            or evidence.get("source_sha256") != canonical.get("source_revision")
            or evidence.get("kit_digest") != canonical.get("kit_digest")
            or evidence.get("view_sha256") != canonical.get("view_sha256") or evidence.get("ok") is not True):
        raise docs.DocumentError("The architecture source, checker evidence and rendered page must belong to one checked revision.")
    readable = understanding_of(canonical)
    if communication.get("readable", False) is not bool(readable):
        raise docs.DocumentError("The readable view availability differs from its canonical provenance.")
    readable_bytes = []
    if readable is not None:
        member = readable.get("member", "readable.md")
        if member not in {"UNDERSTANDING.md", "readable.md"}:
            raise docs.DocumentError("The authored understanding member is not a canonical package member.")
        fields = [(member, "markdown_sha256")]
        if readable.get("pdf_sha256"):
            fields.append(("readable.pdf", "pdf_sha256"))
        for member, field in fields:
            body = read(member)
            if kit.sha256(body) != readable.get(field):
                raise docs.DocumentError("The readable view differs from its immutable canonical revision.")
            readable_bytes.append(body)
    manifest_bytes = read("package.json")
    manifest = json.loads(manifest_bytes)
    factory_bytes = []
    if readable and readable.get("member") == "UNDERSTANDING.md":
        expected = {member: "UNDERSTANDING.md" for member in ("source.yaml", "check.json", "canonical.json", "index.html")}
        story = manifest.get("story")
        if story:
            from jarvis import make_package, package_inbox, package_story
            if story != make_package.STORY_FILE:
                raise docs.DocumentError("The canonical package review must be the unchanged factory-generated standard vote.")
            vote_bytes = read(story)
            try:
                language = package_story.parse(vote_bytes).language
                standard = package_story.encode(package_inbox.with_understood_slide(None, language))
            except ValueError:
                raise docs.DocumentError("The canonical package review must be the unchanged factory-generated standard vote.") from None
            if vote_bytes != standard:
                raise docs.DocumentError("The canonical package review must be the unchanged factory-generated standard vote.")
            expected[story] = "UNDERSTANDING.md"
            factory_bytes.append(vote_bytes)
        if (manifest.get("brief") != "UNDERSTANDING.md" or manifest.get("understanding") != expected
                or manifest.get("entry") != "index.html" or manifest.get("runs") != "page"
                or manifest.get("skill")):
            raise docs.DocumentError("The package author-content declaration differs from this canonical projection.")
    key = kit.sha256(b"\0".join((canonical_bytes, source, view, check_bytes, *readable_bytes, *factory_bytes, manifest_bytes, json.dumps(communication, sort_keys=True).encode())))
    with _gate_lock:
        if key in _checked_projections:
            _checked_projections.move_to_end(key)
            return
    installed = kit.active(canonical["kit_digest"])
    checked = installed.run("check", source)
    rendered = installed.run("render", source)
    if not checked.ok or not rendered.ok or rendered.view != view:
        raise docs.DocumentError("The pinned upstream check/render gate rejected this architecture package.")
    parsed = docs.page_data(view)["doc"]
    if canonical.get("metadata", {}).get("title") != docs.document_title(parsed) or communication.get("title") != docs.document_title(parsed):
        raise docs.DocumentError("The package title must derive from the canonical document.")
    if parsed.get("language") != "arch" or canonical.get("language") != "arch":
        raise docs.DocumentError("An architecture package must use the enabled arch language.")
    problems = docs._coverage(parsed, canonical.get("metadata", {}).get("coverage", {}))
    if problems:
        raise docs.DocumentError("Architecture coverage is incomplete or unresolved; inspect its canonical coverage contract.")
    with _gate_lock:
        _checked_projections[key] = None
        while len(_checked_projections) > 256:
            _checked_projections.popitem(last=False)


def package_reader(package, expected_revision: str | None = None):
    """Read the fixed canonical members with the existing safe package reader."""
    from jarvis import work_packages
    communication = package.manifest.communication
    if expected_revision is not None and (not communication or communication.get("kind") != "architecture" or communication.get("revision") != expected_revision):
        raise docs.DocumentError("The package no longer names the requested canonical revision.")
    held = {}
    def read(name):
        if name not in held:
            held[name] = work_packages.page_file(package, name)
        return held[name]
    gate(read, communication)
    return read


def checked_member(package, name: str, expected_revision: str | None = None) -> bytes:
    return package_reader(package, expected_revision)(name)


def folder_gate(folder: Path, communication: dict | None) -> None:
    if (folder / "canonical.json").exists() and (not communication or communication.get("kind") != "architecture"):
        raise docs.DocumentError("Canonical source packages must declare architecture communication.")
    def read(name):
        path = folder / name
        if path.is_symlink() or not path.is_file() or path.stat().st_size > kit.MAX_VIEW_BYTES:
            raise docs.DocumentError(f"Architecture package file {name} is absent, linked or oversized.")
        return path.read_bytes()
    gate(read, communication)


def understanding_of(canonical: dict) -> dict | None:
    """Which authored Markdown explains this projection, and its hash: the
    fresh author's (`understanding` in canonical.json, make_package
    --document) or else the one supplied with the revision itself
    (metadata `readable`, the brain's publish). None when there is none."""
    package = canonical.get("understanding")
    if package is not None:
        if (not isinstance(package, dict) or set(package) != {"origin", "member", "markdown_sha256"}
                or package.get("origin") != FRESH_AUTHOR or package.get("member") != MEMBER
                or not isinstance(package.get("markdown_sha256"), str)
                or not re.fullmatch(r"[0-9a-f]{64}", package["markdown_sha256"])):
            raise docs.DocumentError("The fresh author's understanding binding is malformed.")
        return package
    readable = canonical["metadata"].get("readable")
    if readable is not None and (not isinstance(readable, dict) or readable.get("origin") != "author-supplied"):
        raise docs.DocumentError("The readable view provenance is malformed.")
    return readable


def project(envelope: dict, folder: Path, understanding: bytes | None = None) -> dict:
    """THE projection of one checked revision into a page package folder,
    for the brain's publish and make_package --document alike: the exact
    source, its checker evidence, its rendered page, its canonical identity
    and the authored Markdown explaining it, with package.json naming them.
    `understanding` is a fresh author's Markdown (bound in canonical.json),
    else the revision's own author-supplied view is used. The package.json
    written (its version still to be worked out by the factory)."""
    if not envelope["check"]["ok"] or not envelope.get("view_sha256"):
        raise docs.DocumentError("This draft has no validated render; inspect its checker diagnostics.")
    canonical = {key: envelope[key] for key in (*IDENTITY, "parent_revision", "language", "work_id", "view_sha256", "metadata")}
    if understanding is not None:
        if not understanding.strip() or len(understanding) > kit.MAX_SOURCE_BYTES:
            raise docs.DocumentError("The authored understanding must contain text and fit within 1 MiB.")
        canonical["understanding"] = {"origin": FRESH_AUTHOR, "member": MEMBER,
                                      "markdown_sha256": kit.sha256(understanding)}
    elif envelope["metadata"].get("readable"):
        understanding = Path(envelope["references"]["readable_markdown"]).read_bytes()
    else:
        raise docs.DocumentError("Publication needs an author-supplied readable Markdown view; submit a proposed revision with readable_markdown.")
    communication = {"kind": "architecture", "title": envelope["metadata"]["title"], "readable": True, **{key: canonical[key] for key in IDENTITY}}
    folder.mkdir(parents=True, exist_ok=True)
    for source_name, destination in (("source", "source.yaml"), ("check", "check.json"), ("view", "index.html")):
        shutil.copyfile(envelope["references"][source_name], folder / destination)
    (folder / MEMBER).write_bytes(understanding)
    (folder / "canonical.json").write_text(json.dumps(canonical, sort_keys=True))
    from jarvis import make_package
    manifest = {
        "name": make_package.dp.slug(envelope["metadata"]["title"], "architecture") + "-" + envelope["document_id"][-8:],
        "runs": "page", "entry": "index.html",
        "purpose": "Inspect the checked architecture and discuss its exact canonical source revision.",
        "returns": ["results/**"], "communication": communication, "brief": MEMBER,
        "understanding": {member: MEMBER for member in ("source.yaml", "check.json", "canonical.json", "index.html")}}
    (folder / "package.json").write_text(json.dumps(manifest))
    return manifest


def checked_revision(service: docs.Documents, document_id: str, revision: str) -> dict:
    """The envelope of one exact checked revision, ready to project."""
    envelope = service.retrieve(document_id=document_id, revision=revision)
    return service.render(document_id=document_id, revision=revision, source_revision=envelope["source_revision"])


def publish(service: docs.Documents, *, document_id: str, revision: str, source_revision: str) -> dict:
    with _publish_lock:
        return _publish(service, document_id=document_id, revision=revision, source_revision=source_revision)


def _publish(service: docs.Documents, *, document_id: str, revision: str, source_revision: str) -> dict:
    from jarvis import make_package, work_packages
    envelope = service.render(document_id=document_id, revision=revision, source_revision=source_revision)
    previous = docs.publication(document_id, revision)
    if previous:
        path = Path(previous["archive_path"])
        if not path.is_file() or kit.sha256(path.read_bytes()) != previous["archive_sha256"]:
            raise docs.DocumentError("The published archive changed; restore its immutable package.")
        package, _ = work_packages.unpack(path, previous["archive_sha256"], clear_mark=lambda p: None)
        return {**envelope, "package_id": package.id, "archive": str(path)}
    output = docs.storage() / revision / "publication"
    output.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="architecture-package-") as name:
        folder = Path(name) / "architecture"
        project(envelope, folder)
        made = make_package.make_folder(folder, output)
    digest = kit.sha256(made.archive.read_bytes())
    package, _ = work_packages.unpack(made.archive, digest, clear_mark=lambda p: None)
    docs.record_publication(document_id, revision, digest, str(made.archive))
    return {**service.retrieve(document_id=document_id, revision=revision), "package_id": package.id,
            "archive": str(made.archive)}
