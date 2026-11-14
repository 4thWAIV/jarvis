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
# The fresh author's own story, kept in the package as written (its bytes
# bound in canonical.json) beside the story composed from it with the
# standard vote (make_package.STORY_FILE), which is what is told.
AUTHORED_STORY = "authored-story.json"
# Where the fresh author writes them (package_author: package/), as the
# zip's `.jarvis/author/` carries them (author_proof.attach).
AUTHOR_SOURCE = {MEMBER: "package/" + MEMBER, AUTHORED_STORY: "package/story.json"}
# A fresh author's binding before the author could write a story and before
# the handoff's purpose was bound: kept readable exactly as it was.
LEGACY_BINDING = frozenset({"origin", "member", "markdown_sha256"})
BINDING = LEGACY_BINDING | {"purpose"}
PURPOSE_MAX_CHARS = 300
_VOTE_ONLY = "The canonical package review must be the unchanged factory-generated standard vote."
_COMPOSED = ("The canonical package review must be the factory's composition of the author's "
             "bound story and the standard vote.")
_publish_lock = threading.RLock()
_checked_projections: OrderedDict[str, None] = OrderedDict()
_gate_lock = threading.Lock()


def gate(read, communication: dict | None, author: dict[str, bytes] | None = None) -> None:
    """THE architecture gate, for the factory and every receiver alike:
    `read(name)` gives a package member's bytes (bounded), `author` the
    files the zip carries under `.jarvis/author/` by their path there (None
    where they are not read: a folder, an unpacked package; {} where the zip
    carries none yet), which must then be exactly the fresh author's bound
    explanation and story."""
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
    if readable and readable.get("member") == MEMBER:
        expected = {member: MEMBER for member in ("source.yaml", "check.json", "canonical.json", "index.html")}
        bound_story = readable.get("story_sha256")
        # His approval is asked only where the binding says the handoff
        # asked for it, and then always (package_story.compose).
        approval = readable.get("approval") is True
        story = manifest.get("story")
        if story or bound_story or approval:
            from jarvis import make_package, package_story
            refused = _COMPOSED if bound_story or approval else _VOTE_ONLY
            if story != make_package.STORY_FILE:
                raise docs.DocumentError(refused)
            told = read(story)
            authored = None
            if bound_story:
                written = read(AUTHORED_STORY)
                if kit.sha256(written) != bound_story:
                    raise docs.DocumentError("The authored story differs from its immutable canonical revision.")
                factory_bytes.append(written)
                expected[AUTHORED_STORY] = MEMBER
            try:
                if bound_story:
                    authored = package_story.authored(written)
                composed = package_story.compose(authored, package_story.parse(told).language, approval)
            except ValueError:
                raise docs.DocumentError(refused) from None
            if told != composed:
                raise docs.DocumentError(refused)
            expected[story] = MEMBER
            factory_bytes.append(told)
        if "purpose" in readable and manifest.get("purpose") != readable["purpose"]:
            raise docs.DocumentError("The package purpose differs from the handoff purpose its canonical revision binds.")
        if (manifest.get("brief") != MEMBER or manifest.get("understanding") != expected
                or manifest.get("entry") != "index.html" or manifest.get("runs") != "page"
                or manifest.get("skill")):
            raise docs.DocumentError("The package author-content declaration differs from this canonical projection.")
        if author:
            # The source the fresh author's proof covers is this explanation
            # and story, byte for byte, and nothing else.
            wanted = {AUTHOR_SOURCE[MEMBER]: readable_bytes[0]}
            if bound_story:
                wanted[AUTHOR_SOURCE[AUTHORED_STORY]] = factory_bytes[0]
            if readable.get("origin") != FRESH_AUTHOR or author != wanted:
                raise docs.DocumentError("The author's attached source differs from the explanation and story this canonical revision binds.")
    author_bytes = [part for name in sorted(author or {}) for part in (name.encode(), author[name])]
    key = kit.sha256(b"\0".join((canonical_bytes, source, view, check_bytes, *readable_bytes, *factory_bytes, manifest_bytes, *author_bytes, json.dumps(communication, sort_keys=True).encode())))
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
        keys = set(package) if isinstance(package, dict) else set()
        if (not (keys == LEGACY_BINDING or BINDING <= keys <= BINDING | {"story_sha256", "approval"})
                or ("approval" in keys and package["approval"] is not True)
                or package.get("origin") != FRESH_AUTHOR or package.get("member") != MEMBER
                or any(not isinstance(package.get(key), str) or not re.fullmatch(r"[0-9a-f]{64}", package[key])
                       for key in ("markdown_sha256", "story_sha256") if key in keys)
                or ("purpose" in keys and purpose_of(package["purpose"]) != package["purpose"])):
            raise docs.DocumentError("The fresh author's understanding binding is malformed.")
        return package
    readable = canonical["metadata"].get("readable")
    if readable is not None and (not isinstance(readable, dict) or readable.get("origin") != "author-supplied"):
        raise docs.DocumentError("The readable view provenance is malformed.")
    return readable


def current_subject(subject) -> bool:
    """Whether {document_id, revision, source_revision, kit_digest} is still
    the revision in force: the newest revision of its document whose check
    passed, under the kit in force now. False when that cannot be read (an
    approval of it then satisfies nothing)."""
    if not isinstance(subject, dict) or not isinstance(subject.get("document_id"), str):
        return False
    try:
        checked = [row for row in docs.revisions(subject["document_id"])
                   if json.loads(row.get("check_json") or "{}").get("ok") is True]
        active = kit.active().release.digest
    except Exception:
        return False
    if not checked:
        return False
    latest = checked[-1]
    return (latest.get("revision") == subject.get("revision")
            and latest.get("source_sha256") == subject.get("source_revision")
            and latest.get("kit_digest") == subject.get("kit_digest") == active)


def purpose_of(text) -> str:
    """A handoff's purpose as a package states it: one line of at most
    PURPOSE_MAX_CHARS, its spacing folded; "" when it is not one."""
    if not isinstance(text, str):
        return ""
    line = " ".join(text.split())
    return line if 0 < len(line) <= PURPOSE_MAX_CHARS and line.isprintable() else ""


def project(envelope: dict, folder: Path, understanding: bytes | None = None,
            story: bytes | None = None, purpose: str | None = None,
            language: str | None = None, approval: bool = False) -> dict:
    """THE projection of one checked revision into a page package folder,
    for the brain's publish and make_package --document alike: the exact
    source, its checker evidence, its rendered page, its canonical identity
    and the authored Markdown explaining it, with package.json naming them.
    `understanding` is a fresh author's Markdown, bound in canonical.json
    with the trusted handoff's `purpose` (the package's purpose) and, when
    the author wrote one, its `story` (kept as written, AUTHORED_STORY, and
    told as package_story.compose makes it with the standard vote, in its
    own language or else `language`). With `approval` (the handoff asked
    for it) the binding says so and the story asks his approval of this
    revision before the vote (package_inbox.APPROVAL_ID). Otherwise the
    revision's own author-supplied view is used. The package.json written
    (its version still to be worked out by the factory)."""
    if not envelope["check"]["ok"] or not envelope.get("view_sha256"):
        raise docs.DocumentError("This draft has no validated render; inspect its checker diagnostics.")
    canonical = {key: envelope[key] for key in (*IDENTITY, "parent_revision", "language", "work_id", "view_sha256", "metadata")}
    told = None
    if understanding is not None:
        if not understanding.strip() or len(understanding) > kit.MAX_SOURCE_BYTES:
            raise docs.DocumentError("The authored understanding must contain text and fit within 1 MiB.")
        if not purpose_of(purpose):
            raise docs.DocumentError(f"The handoff purpose must be one line of at most {PURPOSE_MAX_CHARS} characters.")
        purpose = purpose_of(purpose)
        canonical["understanding"] = {"origin": FRESH_AUTHOR, "member": MEMBER,
                                      "markdown_sha256": kit.sha256(understanding), "purpose": purpose}
        if approval:
            canonical["understanding"]["approval"] = True
        from jarvis import package_story
        if story is None and approval:
            told = package_story.compose(None, language or "en", True)
        if story is not None:
            try:
                authored = package_story.authored(story)
                told = package_story.compose(authored, authored.get("language") or language or "en",
                                             approval)
            except package_story.StoryError as e:
                raise docs.DocumentError(f"The authored story cannot be told: {e}") from None
            pointing = any(beat.get("targets") or beat.get("actions")
                           for slide in authored["slides"] for beat in slide.get("beats") or [])
            view = Path(envelope["references"]["view"]).read_bytes()
            if pointing and package_story.HELPER_PATH.encode() not in view:
                raise docs.DocumentError(
                    "The authored story points at the page, but this kit's rendered page does not "
                    "load the story helper, so nothing could be pointed at: narrate without "
                    "targets or actions until the kit's page loads " + package_story.HELPER_PATH + ".")
            canonical["understanding"]["story_sha256"] = kit.sha256(story)
    elif story is not None or approval:
        raise docs.DocumentError("Only a fresh author's explanation carries an authored story or "
                                 "asks his approval.")
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
        "purpose": purpose or "Inspect the checked architecture and discuss its exact canonical source revision.",
        "returns": ["results/**"], "communication": communication, "brief": MEMBER,
        "understanding": {member: MEMBER for member in ("source.yaml", "check.json", "canonical.json", "index.html")}}
    if told is not None:
        # The story told is the composition; the factory's _with_vote leaves
        # a story that asks a question as it is.
        (folder / make_package.STORY_FILE).write_bytes(told)
        manifest["story"] = make_package.STORY_FILE
        manifest["understanding"][make_package.STORY_FILE] = MEMBER
        if story is not None:
            (folder / AUTHORED_STORY).write_bytes(story)
            manifest["understanding"][AUTHORED_STORY] = MEMBER
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
