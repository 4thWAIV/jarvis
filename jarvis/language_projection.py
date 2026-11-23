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
# A view set: one package of several checked revisions, each a view of the
# same system (one per document or language), tied together by one scenario
# walked through every view (package_story.walk_problem). The package's own
# revision (canonical.json's top level, communication's identity) is its
# first view, at the package's root; `set` lists the others in order, each
# its own canonical revision (VIEW_CANONICAL) whose files are under
# views/<document id>/ (`view_prefix`). Each view is in one of
# language_documents.VIEW_LANGUAGES, so a further view language slots in
# there. A package with no `set` is one view, exactly as before.
SET = "set"
SET_MAX = 6
VIEW_FILES = ("source.yaml", "check.json", "index.html")
VIEW_CANONICAL = (*IDENTITY, "parent_revision", "language", "work_id", "view_sha256", "metadata")

# The one authored Markdown member a projection carries, and the origin of a
# fresh author's (make_package --document) as canonical.json binds it.
MEMBER = "UNDERSTANDING.md"
FRESH_AUTHOR = "fresh-author"
# The fresh author's own story, kept in the package as written (its bytes
# bound in canonical.json) beside the story composed from it with the
# standard vote (make_package.STORY_FILE), which is what is told.
AUTHORED_STORY = "authored-story.json"
# A fresh author's binding before the author could write a story and before
# the handoff's purpose was bound: kept readable exactly as it was.
LEGACY_BINDING = frozenset({"origin", "member", "markdown_sha256"})
BINDING = LEGACY_BINDING | {"purpose"}
_VOTE_ONLY = "The canonical package review must be the unchanged factory-generated standard vote."
_COMPOSED = ("The canonical package review must be the factory's composition of the author's "
             "bound story and the standard vote.")
_publish_lock = threading.RLock()
_checked_projections: OrderedDict[str, None] = OrderedDict()
_gate_lock = threading.Lock()


def gate(read, communication: dict | None) -> None:
    """THE architecture gate, for the factory and every receiver alike:
    `read(name)` gives a package member's bytes (bounded)."""
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
    views = views_of(canonical)
    if communication.get(SET) != set_identity(canonical):
        raise docs.DocumentError("The view set the package names differs from the one its canonical revision binds.")
    view_bytes = []
    for prefix, one in views:
        _check_identity(one)
        if one.get("work_id") != canonical.get("work_id"):
            raise docs.DocumentError("Every view of a set belongs to the first view's Work item.")
        files = tuple(read(prefix + name) for name in VIEW_FILES)
        _check_files(one, *files)
        view_bytes.append(files)
    readable = understanding_of(canonical)
    if communication.get("readable", False) is not bool(readable):
        raise docs.DocumentError("The readable view availability differs from its canonical provenance.")
    if len(views) > 1 and not (readable and readable.get("member") == MEMBER and readable.get("story_sha256")):
        raise docs.DocumentError("A view set is told by its fresh author's story, walking one scenario through every view.")
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
        expected = {member: MEMBER for member in understood(canonical)}
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
                language = package_story.parse(told).language
                walked = package_story.walk_problem(authored, tuple(v["document_id"] for _, v in views))
                composed = package_story.compose(authored, language, approval)
                # A package delivered before Got it was retired carries the
                # composition of then: still its own, asked as the standard
                # vote now (package_story.current).
                before = package_story.compose(authored, language, approval, legacy=True)
            except ValueError:
                raise docs.DocumentError(refused) from None
            if told not in (composed, before):
                raise docs.DocumentError(refused)
            if walked:
                raise docs.DocumentError(walked)
            expected[story] = MEMBER
            factory_bytes.append(told)
        if "purpose" in readable and manifest.get("purpose") != readable["purpose"]:
            raise docs.DocumentError("The package purpose differs from the handoff purpose its canonical revision binds.")
        if (manifest.get("brief") != MEMBER or manifest.get("understanding") != expected
                or manifest.get("entry") != "index.html" or manifest.get("runs") != "page"
                or manifest.get("skill")):
            raise docs.DocumentError("The package author-content declaration differs from this canonical projection.")
    key = kit.sha256(b"\0".join((canonical_bytes, *(part for files in view_bytes for part in files), *readable_bytes, *factory_bytes, manifest_bytes, json.dumps(communication, sort_keys=True).encode())))
    with _gate_lock:
        if key in _checked_projections:
            _checked_projections.move_to_end(key)
            return
    for (_, one), (source, _, view) in zip(views, view_bytes):
        parsed = _checked_render(one, source, view)
        if one.get("metadata", {}).get("title") != docs.document_title(parsed):
            raise docs.DocumentError("The package title must derive from the canonical document.")
    if communication.get("title") != canonical["metadata"].get("title"):
        raise docs.DocumentError("The package title must derive from the canonical document.")
    with _gate_lock:
        _checked_projections[key] = None
        while len(_checked_projections) > 256:
            _checked_projections.popitem(last=False)


def view_prefix(document_id: str) -> str:
    """Where a further view of a set keeps its files in the package."""
    return f"views/{document_id}/"


def identity_of(view: dict) -> dict:
    return {key: view.get(key) for key in IDENTITY}


def views_of(canonical: dict) -> list[tuple[str, dict]]:
    """Every view a canonical.json binds, in order, as (where its files are
    in the package, its canonical revision): the package's own revision at
    the root first, then its `set`. Refused when the set is malformed: not
    a list of 1 to SET_MAX - 1 objects, or a document twice."""
    further = canonical.get(SET)
    if further is None:
        return [("", canonical)]
    if (not isinstance(further, list) or not 1 <= len(further) < SET_MAX
            or any(not isinstance(v, dict) or set(v) != set(VIEW_CANONICAL) for v in further)):
        raise docs.DocumentError(f"The view set is malformed: 2 to {SET_MAX} checked views.")
    ids = [canonical.get("document_id"), *(v.get("document_id") for v in further)]
    if len(set(map(str, ids))) != len(ids):
        raise docs.DocumentError("A view set holds each document once.")
    return [("", canonical), *((view_prefix(v["document_id"]), v) for v in further)]


def set_identity(canonical: dict) -> list[dict] | None:
    """The further views' identities, as communication and his approval
    name them; None for a package of one view."""
    views = views_of(canonical)
    return [identity_of(v) for _, v in views[1:]] if len(views) > 1 else None


def understood(canonical: dict) -> list[str]:
    """The canonical members the explanation explains, every view's files
    and canonical.json, as package.json's `understanding` names them."""
    return [*(prefix + name for prefix, _ in views_of(canonical) for name in ("source.yaml", "check.json")),
            "canonical.json", *(prefix + "index.html" for prefix, _ in views_of(canonical))]


def _check_identity(view: dict) -> None:
    """One view's canonical revision: well formed and its revision the one
    its source and provenance give."""
    if (not isinstance(view.get("document_id"), str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,100}", view["document_id"])
            or any(not isinstance(view.get(key), str) or not re.fullmatch(r"[0-9a-f]{64}", view[key]) for key in ("revision", "source_revision", "kit_digest"))
            or not isinstance(view.get("metadata"), dict)):
        raise docs.DocumentError("The canonical identity or metadata is malformed.")
    if view["revision"] != docs.revision_for(view["document_id"], view["source_revision"], view["kit_digest"], view["metadata"], view.get("parent_revision")):
        raise docs.DocumentError("The canonical revision does not match its source and provenance.")
    if view.get("work_id") != view["metadata"].get("work_id") or not view.get("work_id"):
        raise docs.DocumentError("The Work linkage differs from the immutable canonical provenance.")


def _check_files(view: dict, source: bytes, check_bytes: bytes, page: bytes) -> None:
    """One view's source, checker evidence and rendered page: one checked
    revision, the one its canonical revision names."""
    try:
        evidence = json.loads(check_bytes)
    except ValueError:
        evidence = None
    if (not isinstance(evidence, dict) or kit.sha256(source) != view.get("source_revision")
            or kit.sha256(page) != view.get("view_sha256")
            or evidence.get("source_sha256") != view.get("source_revision")
            or evidence.get("kit_digest") != view.get("kit_digest")
            or evidence.get("view_sha256") != view.get("view_sha256") or evidence.get("ok") is not True):
        raise docs.DocumentError("The architecture source, checker evidence and rendered page must belong to one checked revision.")


def _checked_render(view: dict, source: bytes, page: bytes) -> dict:
    """One view checked and drawn again by the kit it names, in a view
    language JARVIS carries, its coverage complete: its parsed document."""
    installed = kit.active(view["kit_digest"])
    checked = installed.run("check", source)
    rendered = installed.run("render", source)
    if not checked.ok or not rendered.ok or rendered.view != page:
        raise docs.DocumentError("The pinned upstream check/render gate rejected this architecture package.")
    parsed = docs.page_data(page)["doc"]
    if parsed.get("language") not in docs.VIEW_LANGUAGES or view.get("language") != parsed.get("language"):
        raise docs.DocumentError("An architecture package must use the enabled arch language.")
    problems = docs._coverage(parsed, view.get("metadata", {}).get("coverage", {}))
    if problems:
        raise docs.DocumentError("Architecture coverage is incomplete or unresolved; inspect its canonical coverage contract.")
    return parsed


def views_shown(package) -> list[dict] | None:
    """A view set as the page shows it: each view in order, its document
    id, title, language, the page it opens (`entry`) and the ids it
    declares, so the page lights the same element in every view that shows
    it. None for a package of one view (or none that can be read)."""
    from jarvis import work_packages
    communication = package.manifest.communication or {}
    if communication.get("kind") != "architecture" or not communication.get(SET):
        return None
    try:
        canonical = json.loads(work_packages.page_file(package, "canonical.json"))
        views = views_of(canonical)
    except (ValueError, work_packages.PackageError):
        return None
    return [{"id": view["document_id"], "title": view["metadata"].get("title") or view["document_id"],
             "language": view.get("language"), "entry": prefix + "index.html",
             "declarations": [d for d in view["metadata"].get("declarations") or [] if isinstance(d, str)]}
            for prefix, view in views]


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
    passed, checked by a kit whose language files (kit.language_digests)
    are the kit in force's. A kit release that changes nothing in the
    language leaves it in force; a change to any language file does not
    (and that kit waits for his review before it is in force). False when
    that cannot be read, its kit no longer installed included (an approval
    of it then satisfies nothing)."""
    if not isinstance(subject, dict) or not isinstance(subject.get("document_id"), str) \
            or not isinstance(subject.get("kit_digest"), str):
        return False
    try:
        checked = [row for row in docs.revisions(subject["document_id"])
                   if json.loads(row.get("check_json") or "{}").get("ok") is True]
        same_language = (kit.language_digests(kit.active(subject["kit_digest"]))
                         == kit.language_digests(kit.active()))
    except Exception:
        return False
    if not checked or not same_language:
        return False
    latest = checked[-1]
    return (latest.get("revision") == subject.get("revision")
            and latest.get("source_sha256") == subject.get("source_revision")
            and latest.get("kit_digest") == subject.get("kit_digest"))


def purpose_of(text) -> str:
    """A handoff's purpose as a package states it: its spacing folded into
    one line (the canonical form the receiver checks); "" when it has no
    text."""
    if not isinstance(text, str):
        return ""
    line = " ".join(text.split())
    return line if line and line.isprintable() else ""


def project(envelope: dict, folder: Path, understanding: bytes | None = None,
            story: bytes | None = None, purpose: str | None = None,
            language: str | None = None, approval: bool = False,
            more: tuple[dict, ...] = ()) -> dict:
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
    revision's own author-supplied view is used.
    `more` makes it a view set: further checked revisions (of other
    documents on the same Work item), each a view after the first, under
    views/<document id>/, bound in canonical.json's `set`. Only a fresh
    author's explanation and story tell a set, the story walking one
    scenario through every view (package_story.walk_problem), and his
    approval, when asked, is of the exact set. The package.json written
    (its version still to be worked out by the factory)."""
    envelopes = (envelope, *more)
    for one in envelopes:
        if not one["check"]["ok"] or not one.get("view_sha256"):
            raise docs.DocumentError("This draft has no validated render; inspect its checker diagnostics.")
    if len(envelopes) > SET_MAX:
        raise docs.DocumentError(f"A view set holds at most {SET_MAX} views.")
    if len({one["document_id"] for one in envelopes}) != len(envelopes):
        raise docs.DocumentError("A view set holds each document once.")
    if any(one["work_id"] != envelope["work_id"] for one in more):
        raise docs.DocumentError("Every view of a set belongs to the first view's Work item.")
    if more and (understanding is None or story is None):
        raise docs.DocumentError("A view set is told by its fresh author's story, walking one scenario through every view.")
    canonical = {key: envelope[key] for key in VIEW_CANONICAL}
    if more:
        canonical[SET] = [{key: one[key] for key in VIEW_CANONICAL} for one in more]
    told = None
    if understanding is not None:
        if not understanding.strip() or len(understanding) > kit.MAX_SOURCE_BYTES:
            raise docs.DocumentError("The authored understanding must contain text and fit within 1 MiB.")
        if not purpose_of(purpose):
            raise docs.DocumentError("The handoff purpose (its question) has no text that can be stated.")
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
            walked = package_story.walk_problem(authored, tuple(one["document_id"] for one in envelopes))
            if walked:
                raise docs.DocumentError(f"The authored story cannot be told: {walked}")
            pointing = any(beat.get("targets") or beat.get("actions")
                           for slide in authored["slides"] for beat in slide.get("beats") or [])
            if pointing and any(package_story.HELPER_PATH.encode() not in Path(one["references"]["view"]).read_bytes()
                                for one in envelopes):
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
    if more:
        communication[SET] = set_identity(canonical)
    folder.mkdir(parents=True, exist_ok=True)
    for (prefix, _), one in zip(views_of(canonical), envelopes):
        (folder / prefix).mkdir(parents=True, exist_ok=True)
        for source_name, destination in (("source", "source.yaml"), ("check", "check.json"), ("view", "index.html")):
            shutil.copyfile(one["references"][source_name], folder / prefix / destination)
    (folder / MEMBER).write_bytes(understanding)
    (folder / "canonical.json").write_text(json.dumps(canonical, sort_keys=True))
    from jarvis import make_package
    manifest = {
        "name": make_package.dp.slug(envelope["metadata"]["title"], "architecture") + "-" + envelope["document_id"][-8:],
        "runs": "page", "entry": "index.html",
        "purpose": purpose or "Inspect the checked architecture and discuss its exact canonical source revision.",
        "returns": ["results/**"], "communication": communication, "brief": MEMBER,
        "understanding": {member: MEMBER for member in understood(canonical)}}
    if told is None:
        # An architecture package's story is always the factory's
        # composition: with nothing authored and no approval asked, the
        # standard vote alone, in the language in force as it is built.
        from jarvis import package_story
        told = package_story.compose(None, language or make_package.built_language())
    if told is not None:
        # The story told is the composition; the factory's _with_vote leaves
        # a story that ends with his one vote as it is.
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
