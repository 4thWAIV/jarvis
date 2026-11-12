"""The one way an agent makes a package and sends it to Tim.

    python -m jarvis.make_package --handoff HANDOFF.json --engine claude|codex
                                  --session ID [--project P] [--workflow ID] [--work ID]
                                  [--check-only] [--example [decisions|presentation]]
                                  [--document ID --revision REV]

docs/making-a-package.md is the whole of how to use it. In short:

- CONTENT.json is a deck, drawn by the shared deck
  (examples/packages/decision-deck/). With `decisions` it is a decision
  package; with only `slides` it is a presentation (an update), which ends
  with the standard vote (package_inbox.understood: Got it / I have
  questions, in the deck's language or the one in force). Everything that can be worked out is (decision_package.expand).
- FOLDER is a hand-made package (a page of its own, an agent's brief or a
  conversation's skill). Its package.json needs only `purpose`; the rest is
  worked out from the files there. A page whose story asks nothing (or that
  has no story) gets the same vote as its story's last slide. An agent or
  conversation package cannot carry a vote, so it may not name a Work item.
- `--document ID --revision REV` is a canonical architecture document an
  agent checked with `python -m jarvis.language_document validate`: the
  package is JARVIS's projection of that exact checked revision
  (language_projection.project, the brain's publish shares it) around the
  fresh author's plain explanation, `package/UNDERSTANDING.md`, which is
  what the author proof covers. It goes on the document's own Work item.

The command launches a fresh author on the caller's engine first. Existing source
can accompany the handoff as evidence; --check-only validates source without a model.
The command checks the author's result, builds and zips it, checks it again (a deck
end to end in an isolated JARVIS, decision_check.py; a folder through the
receiver's own reading of the zip, work_packages.recognise), and only then
sends it to the live JARVIS as a message carrying it (POST /api/messages,
messages_api.py): on the Work item `--work` names (else his Inbox), saying
`--says`, unpacked in his package inbox (package_inbox.py), never shown and
never spoken until he asks for it. `--session` is whose key signs it and
who his answers go back to; `--workflow` names the workflow it is for.
Anything it cannot decide it refuses, saying what to change. Nothing is
delivered after any failure.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import re
import shutil
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from jarvis import (author_proof, data_paths, decision_package as dp, decision_stamp,
                    package_signature)

# The file each way of running needs, by its usual name: a folder holding
# exactly one of them runs that way.
USUAL_FILES = {"page": "index.html", "agent": "BRIEF.md", "conversation": "SKILL.md"}
STORY_FILE = "story.json"
DEFAULT_RETURNS = ["results/**"]
MANIFEST_FIELDS = ("name", "version", "purpose", "runs", "skill", "brief", "entry", "story",
                   "returns", "understanding", "communication")
STORY_HELPER = "/api/packages/story.js"
FIRST_VERSION = "1.0.0"

PRESENTATION_EXAMPLE = {
    "title": "Team lunch moved",
    "slides": [
        {"title": "What changed",
         "blocks": [{"lead": "Friday's lunch is now in the office, at noon."},
                    {"points": ["Food is brought in.", "Nobody has to book a table."]}]},
        {"title": "What you need to do",
         "blocks": [{"p": "Nothing: it is in everyone's calendar."}]},
    ],
}

# What a checked architecture document's package is called (make_package --document).
ARCHITECTURE_KIND = "architecture package"

# What the command prints once JARVIS has taken the package.
ARRIVED_AS = "Sent to Tim as message {message}, carrying it; in his package inbox as {inbox_id}"


@dataclass(frozen=True)
class Made:
    """A built package, ready to check: its zip, its title, and how it runs."""
    archive: Path
    title: str
    kind: str            # "decision package", "presentation" or "<runs> package"
    deck: dict | None    # a deck's full data; None for a folder


# -- versions ---------------------------------------------------------------------------

def next_version(name: str) -> str:
    """One past the highest version of `name` already in the live JARVIS, so
    a package sent again is a new package, opened by its name as the newest;
    1.0.0 for the first."""
    from jarvis import work_packages
    versions = [p.manifest.version for p in work_packages.every() if p.manifest.name == name]
    if not versions:
        return FIRST_VERSION
    top = max(versions, key=work_packages.version_key)
    head, _, last = top.rpartition(".")
    return f"{head}.{int(last) + 1}" if last.isdigit() and head else f"{top}.1"


# -- a deck -----------------------------------------------------------------------------

def deck_of(content: Path) -> dict:
    """The deck in full form, its version worked out when not given, or
    Refused with every problem."""
    from jarvis import package_inbox
    raw = dp.read(content)
    deck = dp.expand(raw)
    if isinstance(deck, dict) and not deck.get("decisions") and deck.get("slides"):
        # Every package carries his vote: one that asks nothing ends with
        # the standard one, drawn after its slides, in its language.
        deck["decisions"] = [package_inbox.understood(raw.get("language") or built_language())]
    if "version" not in raw and dp._text(deck.get("name")).strip():
        deck["version"] = next_version(deck["name"])
    problems = dp.problems_of(deck, content.parent)
    if not problems:
        from jarvis.package_understanding import validate_deck_inputs
        try:
            validate_deck_inputs(deck, dp.builder(), content.parent)
        except ValueError as error:
            problems.append(str(error))
    if problems:
        raise dp.Refused(problems)
    return deck


def built_language() -> str:
    """The language in force as it is built: JARVIS's saved choice
    (language_setting.saved), for a deck or a new story that names none."""
    from jarvis import language_setting
    return language_setting.saved()


def deck_kind(deck: dict) -> str:
    """A presentation asks only the standard vote; a decision package more."""
    from jarvis import package_inbox
    asked = [d.get("id") for d in deck.get("decisions") or [] if isinstance(d, dict)]
    return "presentation" if asked in ([], [package_inbox.UNDERSTOOD_ID]) else "decision package"


def make_deck(content: Path, out: Path) -> Made:
    deck = deck_of(content)
    return Made(dp.build(content, out, deck), deck["title"], deck_kind(deck), deck)


# -- a folder ---------------------------------------------------------------------------

def _zipper():
    """examples/packages/zip_package.py, the zip step every package shares."""
    path = data_paths.SOURCE_ROOT / "examples" / "packages" / "zip_package.py"
    spec = importlib.util.spec_from_file_location("zip_package", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def manifest_of(folder: Path) -> dict:
    """The folder's package.json in full, everything but `purpose` worked
    out from the files there, or Refused with every problem."""
    path = folder / "package.json"
    try:
        given = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    except (OSError, ValueError) as e:
        raise dp.Refused([f"{path} is not readable JSON: {e}"])
    if not isinstance(given, dict):
        raise dp.Refused([f"{path} must be one JSON object."])
    problems = dp.unknown_fields("package.json", given, "package.json", MANIFEST_FIELDS)
    manifest = dict(given)
    try:
        from jarvis.language_projection import folder_gate
        folder_gate(folder, manifest.get("communication"))
    except (ValueError, OSError) as error:
        problems.append(f"Architecture publication refused: {error}")
    if not dp._text(manifest.get("purpose")).strip():
        problems.append(f"{path} needs a purpose: one sentence saying what the package does "
                        f"and why. It is the one field that cannot be worked out.")
    present = [runs for runs, name in USUAL_FILES.items() if (folder / name).is_file()]
    runs = manifest.get("runs")
    if runs is None:
        named = [runs for runs, field in (("page", "entry"), ("agent", "brief"),
                                          ("conversation", "skill")) if field in manifest]
        choices = named or present
        if len(choices) == 1:
            runs = choices[0]
        elif not choices:
            problems.append(f"{folder} has none of {', '.join(USUAL_FILES.values())}, so how "
                            f"it runs cannot be told: add the page, the brief or the skill.")
        else:
            problems.append(f"{folder} could run as {' or '.join(choices)}; say which in "
                            f"package.json's runs.")
    elif runs not in USUAL_FILES:
        problems.append(f"package.json's runs is {runs!r}; it is one of "
                        f"{', '.join(USUAL_FILES)}.")
        runs = None
    if runs:
        field = {"page": "entry", "agent": "brief", "conversation": "skill"}[runs]
        manifest["runs"] = runs
        manifest.setdefault(field, USUAL_FILES[runs])
        if runs == "page" and "story" not in manifest and (folder / STORY_FILE).is_file():
            manifest["story"] = STORY_FILE
    manifest.setdefault("name", dp.slug(folder.name, "package"))
    manifest.setdefault("returns", DEFAULT_RETURNS)
    for key in ("skill", "brief", "entry", "story"):
        named = manifest.get(key)
        if named and not (folder / str(named)).is_file():
            problems.append(f"package.json's {key} is {named!r}, which is not in {folder}.")
    entry = folder / str(manifest.get("entry") or "")
    if runs == "page" and entry.is_file():
        page = entry.read_text(encoding="utf-8", errors="replace")
        if re.search(r"""type\s*=\s*["']?module""", page):
            problems.append(f"{entry.name} loads a script as a module, which the package frame "
                            f"cannot run: use ordinary <script src> tags.")
        # An architecture package's page is the kit's own render, checked
        # byte for byte; its projection refuses a story that points at it
        # while that page cannot load the helper (language_projection.project).
        communication = manifest.get("communication")
        architecture = isinstance(communication, dict) and communication.get("kind") == "architecture"
        if manifest.get("story") and STORY_HELPER not in page and not architecture:
            problems.append(f"{entry.name} does not load the story helper, so the story points "
                            f"at nothing: add <script src=\"{STORY_HELPER}\"></script>.")
    if not problems:
        from jarvis import package_understanding
        try:
            coverage = package_understanding.declaration(manifest.get("understanding"))
            if runs == "page" and coverage and not manifest.get("brief"):
                manifest["brief"] = next(iter(coverage.values()))
            package_understanding.validate(folder, manifest, _zipper().files(folder))
        except ValueError as error:
            problems.append(str(error))
    if problems:
        raise dp.Refused(problems)
    if "version" not in manifest:
        manifest["version"] = next_version(manifest["name"])
    return manifest


def _with_vote(built: Path, manifest: dict) -> None:
    """A page whose story asks nothing (or that has none) gets the standard
    vote as its story's last slide, waiting on its answers file."""
    from jarvis import package_inbox, package_story
    named = manifest.get("story")
    path = built / (named or STORY_FILE)
    try:
        given = json.loads(path.read_text(encoding="utf-8")) if named else None
        parsed = package_story.parse(path.read_bytes()) if named else None
        if parsed is not None and package_inbox.asks_a_question(None, parsed):
            return
    except (OSError, ValueError, package_story.StoryError) as e:
        raise dp.Refused([f"Its story cannot be read: {e}"])
    # The vote in the story's language (English when it names none, as it is
    # told), or for a new story the language in force.
    language = parsed.language if parsed is not None else built_language()
    told = package_inbox.with_understood_slide(given, language)
    try:
        package_story.parse(json.dumps(told).encode("utf-8"))
    except package_story.StoryError as e:
        raise dp.Refused([f"The vote could not be added to its story: {e}"])
    path.write_bytes(package_story.encode(told))
    manifest["story"] = path.name if not named else named
    # A story made here is understood as the page it is told over is: the
    # author's understanding names every member it holds.
    understanding = manifest.get("understanding")
    if isinstance(understanding, dict) and manifest["story"] not in understanding:
        about = understanding.get(manifest.get("entry")) or manifest.get("brief")
        if about:
            manifest["understanding"] = {**understanding, manifest["story"]: about}
    if DEFAULT_RETURNS[0] not in manifest.get("returns", []) and \
            told["answers"] not in manifest.get("returns", []):
        manifest["returns"] = [*manifest.get("returns", []), told["answers"]]


def make_folder(folder: Path, out: Path) -> Made:
    """Copy the folder with its package.json in full, zip it, and read the
    zip back the way the receiver does: the zip's path."""
    from jarvis import bundle_archive, work_packages
    if all((folder / mark).is_file() for mark in decision_stamp.MARKS):
        raise dp.Refused([f"{folder} is a decision deck ({', '.join(decision_stamp.MARKS)}). "
                          f"A deck is made from its content file, never from a folder: give "
                          f"the command the deck's JSON."])
    manifest = manifest_of(folder)
    zipper = _zipper()
    built = out / manifest["name"]
    shutil.rmtree(built, ignore_errors=True)
    shutil.copytree(folder, built, symlinks=True,
                    ignore=shutil.ignore_patterns(*zipper.LEFT_OUT))
    if manifest["runs"] == "page":
        _with_vote(built, manifest)
    (built / "package.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False)
                                        + "\n", encoding="utf-8")
    from jarvis import package_understanding
    try:
        package_understanding.validate(built, manifest, zipper.files(built))
    except ValueError as error:
        raise dp.Refused([str(error)]) from None
    archive = out / f"{manifest['name']}-{manifest['version']}.zip"
    if zipper.main([str(built), str(archive)]) != 0:
        raise dp.Refused([f"It could not be zipped from {built}."])
    try:
        with bundle_archive.open_archive(archive) as z:
            recognised = work_packages.recognise(z, bundle_archive.list_members(z))
    except bundle_archive.ArchiveError as e:
        raise dp.Refused([f"JARVIS would refuse the zip: {e}"])
    if recognised.manifest is None:
        raise dp.Refused([f"JARVIS would not open it as a package: {recognised.reason}"])
    title = manifest["name"].replace("-", " ")
    return Made(archive, title, f"{manifest['runs']} package", None)


# -- an architecture document ------------------------------------------------------------

@dataclass(frozen=True)
class Explanation:
    """A fresh author's explanation of an architecture document: its
    UNDERSTANDING.md and, when it wrote one, its story's bytes."""
    markdown: bytes
    story: bytes | None


def understanding_in(source: Path) -> Explanation:
    """The fresh author's explanation of an architecture document: a folder
    holding UNDERSTANDING.md, readable author Markdown within 1 MiB, and
    optionally story.json, a story read strictly (package_story.authored)
    that composes with the standard vote (package_story.compose), and
    nothing else (JARVIS projects the checked revision around them); or
    Refused."""
    from jarvis import language_kit, language_projection, package_story, package_understanding
    member = language_projection.MEMBER
    try:
        files = author_proof.source_files(source)
    except ValueError as e:
        raise dp.Refused([str(e)]) from None
    allowed = {f"{source.name}/{member}", f"{source.name}/{STORY_FILE}"}
    if not source.is_dir() or f"{source.name}/{member}" not in files or set(files) - allowed:
        raise dp.Refused([f"An architecture document's explanation is a folder holding only "
                          f"{member} and, optionally, {STORY_FILE}; {source} holds "
                          f"{', '.join(sorted(files)) or 'nothing'}."])
    try:
        package_understanding.markdown_text(source, member)
    except ValueError as e:
        raise dp.Refused([str(e)]) from None
    body = (source / member).read_bytes()
    if len(body) > language_kit.MAX_SOURCE_BYTES:
        raise dp.Refused([f"{member} must fit within 1 MiB."])
    story = None
    if f"{source.name}/{STORY_FILE}" in files:
        story = (source / STORY_FILE).read_bytes()
        try:
            authored = package_story.authored(story)
            package_story.compose(authored, authored.get("language") or built_language())
        except package_story.StoryError as e:
            raise dp.Refused([f"{STORY_FILE}: {e}"]) from None
    return Explanation(body, story)


def document_of(document_id: str, revision: str, base: str,
                signer: tuple[Path, str]) -> dict:
    """The exact checked revision named, as the JARVIS at `base` holds it,
    or Refused saying why it cannot be packaged. Read over its signed route
    (retrieve, then render with its source revision), as the agent's own
    language_document command reads it: the records live in JARVIS's
    engine, which this process need not reach."""
    from jarvis import language_document

    def post(body: dict) -> dict:
        return dp.post(base.rstrip("/") + language_document.ROUTE, "", body,
                       timeout=language_document.TIMEOUT_SEC, signer=signer)

    def whole(operation: str, arguments: dict) -> dict:
        envelope = language_document.call(post, operation, arguments)
        if not envelope.get("ok"):
            raise ValueError((envelope.get("error") or {}).get("cause") or envelope)
        return envelope["result"]
    try:
        found = whole("retrieve", {"document_id": document_id, "revision": revision})
        return whole("render", {"document_id": document_id, "revision": revision,
                                "source_revision": found["source_revision"]})
    except (ValueError, OSError, KeyError, SystemExit) as e:
        raise dp.Refused([f"Document {document_id} revision {revision} cannot be packaged: "
                          f"{e}"]) from None


def document_evidence(envelope: dict, out: Path) -> Path:
    """The checked revision as its fresh author reads it, under `out`: the
    YAML, its rendered page, its check and document.json (identity,
    provenance and coverage)."""
    folder = out / "document"
    shutil.rmtree(folder, ignore_errors=True)
    folder.mkdir(parents=True)
    for name, copied in (("source", "source.yaml"), ("view", "view.html"), ("check", "check.json")):
        shutil.copyfile(envelope["references"][name], folder / copied)
    keys = ("document_id", "revision", "source_revision", "parent_revision", "work_id",
            "language", "kit_digest")
    (folder / "document.json").write_text(json.dumps(
        {**{k: envelope[k] for k in keys},
         **{k: envelope["metadata"].get(k) for k in ("title", "author", "scope_revision",
                                                      "coverage", "declarations", "evidence")}},
        indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return folder


def make_architecture(envelope: dict, source: Path, out: Path, purpose: str,
                      approval: bool = False) -> Made:
    """JARVIS's projection of the checked revision (language_projection.project,
    as the brain's publish) around the fresh author's explanation and story,
    stating the handoff's `purpose` and, when the handoff asks for it
    (`approval`), asking his approval of this exact revision, built and
    read back as any folder package (make_folder)."""
    from jarvis import language_projection
    explained = understanding_in(source)
    folder = Path(tempfile.mkdtemp(prefix="projection-", dir=out)) / "architecture"
    try:
        language_projection.project(envelope, folder, explained.markdown, explained.story,
                                    purpose, built_language(), approval)
    except (ValueError, OSError) as e:
        raise dp.Refused([f"The checked revision cannot be projected: {e}"]) from None
    made = make_folder(folder, out)
    return Made(made.archive, envelope["metadata"]["title"], ARCHITECTURE_KIND, None)


# -- signing --------------------------------------------------------------------------

def sign(archive: Path, signer: tuple[Path, str], session: str,
         statements: tuple[str, ...] = (), work_key: dict | None = None,
         author: dict | None = None) -> None:
    """Sign the zip in place with the session's own key (package_signature)."""
    package_signature.sign(archive, archive, signer[0], signer[1], session, statements,
                           work_key, author)


def with_author(made: Made, source: Path, session: str) -> author_proof.Claim:
    """The fresh author's claim for this package: its authored source put in
    the zip (`.jarvis/author/`), and the claim checked here first exactly as
    the receiving JARVIS checks it (author_proof.problem), so a source
    changed after the author's own last check is never delivered."""
    import time
    from jarvis import package_author
    claim = package_author.claim_of(source)
    try:
        author_proof.attach(made.archive, source)
    except (OSError, ValueError) as e:
        raise dp.Refused([f"The authored source could not be added: {e}"]) from None
    why = author_proof.problem(claim.as_dict(), session, made.archive, time.time())
    if why:
        raise dp.Refused([why])
    if made.kind == ARCHITECTURE_KIND:
        # The receiver's gate holds the attached source to the bound
        # explanation and story; read the zip back as it will.
        from jarvis import bundle_archive, work_packages
        try:
            with bundle_archive.open_archive(made.archive) as z:
                recognised = work_packages.recognise(z, bundle_archive.list_members(z))
        except bundle_archive.ArchiveError as e:
            raise dp.Refused([f"JARVIS would refuse the zip: {e}"]) from None
        if recognised.manifest is None:
            raise dp.Refused([f"JARVIS would not open it as a package: {recognised.reason}"])
    return claim


def add_work_key(archive: Path, work_id: str) -> dict:
    """Make a key pair for one Work item and put its private half in the
    package at .jarvis/work-key, for whoever works in it: the grant the
    signature carries. Only an agent package gets one: a package Tim
    answers needs none."""
    from jarvis import agent_keys
    with tempfile.TemporaryDirectory(prefix="jarvis-work-key-") as folder:
        private, public = agent_keys.make_pair(Path(folder) / "key", f"jarvis-work {work_id}",
                                               name=agent_keys.WORK_KEY[1])
        package_signature.add_files(archive, {
            "/".join(agent_keys.WORK_KEY): private.read_bytes(),
            "/".join(agent_keys.WORK_KEY) + ".pub": (public + "\n").encode()})
    return {"public_key": public, "work": work_id}


# -- his vote ---------------------------------------------------------------------------

def check_vote(source: Path, work: str) -> None:
    """Every package carries his vote: a deck or a page gets the standard
    one when it asks nothing. An agent package for a Work item carries it on
    the item itself (its work-item key's package; server._receive_package_zip
    asks the standard vote there as it arrives). A conversation package
    cannot, so it may not name a Work item (package_inbox.no_vote, which the
    inbox route says too)."""
    if not work or not source.is_dir():
        return
    from jarvis import package_inbox, work_packages
    runs = manifest_of(source)["runs"]
    if runs not in package_inbox.VOTING_RUNS + (work_packages.AGENT,):
        raise dp.Refused([package_inbox.no_vote(work, runs)])


# -- checking ------------------------------------------------------------------------

def _check(made: Made, out: Path, signer: tuple[Path, str], session: str) -> bool:
    """A deck end to end in an isolated JARVIS; True when every check passed."""
    from jarvis import decision_check
    print("Checking it in an isolated JARVIS…", flush=True)
    result = decision_check.check(made.archive, made.deck, out / "check", signer, session)
    if not result.ok:
        if result.passed:
            print("These checks passed:")
            for line in result.passed:
                print(f"  ok: {line}")
        print("Not delivered. These checks failed:")
        for line in result.failures:
            print(f"  - {line}")
        print("Screenshots:")
        for shot in result.screenshots:
            print(f"  {shot}")
        return False
    print(f"Every check passed ({len(result.passed)}). Screenshots are in {out / 'check'}.")
    for line in result.passed:
        print(f"  ok: {line}")
    return True


# -- the command -----------------------------------------------------------------------

def main(argv: list[str] | None = None, prog: str = "python -m jarvis.make_package") -> int:
    parser = argparse.ArgumentParser(
        prog=prog,
        description="Make a package, check it, and deliver it to Tim's JARVIS. "
                    "docs/making-a-package.md has everything.")
    parser.add_argument("source", type=Path, nargs="?",
                        help="existing source to validate with --check-only or give the fresh author as evidence")
    parser.add_argument("--handoff", type=Path, help="JSON evidence, audience and questions for the fresh author")
    parser.add_argument("--engine", choices=("claude", "codex"), help="the originating session’s own engine")
    parser.add_argument("--example", nargs="?", const="decisions",
                        choices=("decisions", "presentation"),
                        help="print a minimal deck to start from, and stop")
    parser.add_argument("--check-only", action="store_true",
                        help="check the content and stop: nothing is built or delivered")
    parser.add_argument("--project", default=Path.cwd().name,
                        help="the project asking (default: this folder's name)")
    parser.add_argument("--session", default="",
                        help="the session asking: his answers come back to it as a message")
    parser.add_argument("--workflow", default="",
                        help="the workflow it is for: it waits on his answers, which come "
                             "back as its decisions")
    parser.add_argument("--work", default="",
                        help="the work item it is for: it is a message in that item's "
                             "Messages tab (required while you have a piece on an open item)")
    parser.add_argument("--says", default="",
                        help="what the message carrying it says, one or two short sentences "
                             "(default: its kind and title)")
    parser.add_argument("--out", type=Path, default=None,
                        help="where the package, its zip and the screenshots go "
                             "(default: a new temporary folder)")
    parser.add_argument("--document", default="",
                        help="a canonical architecture document (python -m "
                             "jarvis.language_document validate): the package is JARVIS's "
                             "projection of its checked --revision, explained by the fresh "
                             "author")
    parser.add_argument("--revision", default="",
                        help="the exact checked revision of --document")
    parser.add_argument("--deliver-to", default=dp.LIVE, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if bool(args.document) != bool(args.revision):
        parser.error("--document and --revision go together")

    if args.example:
        print(json.dumps(dp.EXAMPLE if args.example == "decisions" else PRESENTATION_EXAMPLE,
                         indent=2))
        return 0
    if args.source is None and (args.check_only or args.handoff is None):
        parser.error("give source for --check-only, or --handoff for creation")
    source = args.source.resolve() if args.source else None
    if source is not None and not source.exists():
        print(f"There is nothing at {source}.")
        return 1
    try:
        if args.check_only and args.document:
            # The author's own check of its explanation; the factory projects
            # and checks the revision around it once the author is done.
            explained = understanding_in(source)
            told = f" and {source.name}/{STORY_FILE}" if explained.story is not None else ""
            print(f"The explanation is right: {source.name}/UNDERSTANDING.md{told}, for "
                  f"architecture document {args.document} revision {args.revision}.")
            print(author_proof.MARK + author_proof.source_digest(source))
            return 0
        if args.check_only:
            check_vote(source, args.work)
            if source.is_dir():
                manifest = manifest_of(source)
                print(f"The folder is right: a {manifest['runs']} package, "
                      f"{manifest['name']}-{manifest['version']}.")
            else:
                deck = deck_of(source)
                kind = deck_kind(deck)
                asks = ("ending with the standard vote (Got it / I have questions)"
                        if kind == "presentation"
                        else f"{len(deck.get('decisions') or [])} decision(s)")
                print(f"The content is right: a {kind}, {asks}, "
                      f"package {deck['name']}-{deck['version']}.")
            # What a fresh author's own last check shows (author_proof.MARK).
            print(author_proof.MARK + author_proof.source_digest(source))
            return 0
        out = (args.out or Path(tempfile.mkdtemp(prefix="package-"))).resolve()
        out.mkdir(parents=True, exist_ok=True)
        from jarvis import package_author
        envelope = document = None
        if args.document:
            from jarvis import agent_keys
            reader = agent_keys.signer_for(args.session)
            if reader is None:
                raise dp.Refused(["The document is read with your key: give --session <your "
                                  "session id>, once Tim has switched your agent on."])
            envelope = document_of(args.document, args.revision, args.deliver_to, reader)
            if args.work and args.work != envelope["work_id"]:
                raise dp.Refused([f"Document {args.document} belongs to Work item "
                                  f"{envelope['work_id']}, not {args.work}: send it there."])
            args.work = envelope["work_id"]
            document = package_author.Document(args.document, args.revision,
                                               document_evidence(envelope, out))
        source = package_author.prepare(args.handoff, args.engine, out, source=source,
                                        session=args.session, work=args.work, workflow=args.workflow,
                                        document=document)
        if envelope is not None:
            # The package states the handoff's purpose: the originating
            # session's, never the author's (it is bound in canonical.json).
            handed = package_author.read_handoff(args.handoff)
            made = make_architecture(envelope, source, out, handed["purpose"],
                                     handed.get("approval") is True)
        else:
            check_vote(source, args.work)
            made = make_folder(source, out) if source.is_dir() else make_deck(source, out)
        author = with_author(made, source, args.session)
    except dp.Refused as e:
        print("Not built. These need changing:")
        for line in e.problems:
            print(f"  - {line}")
        return 1
    from jarvis import agent_keys
    # The session's own key, else (an agent package's run, working in it) the
    # work-item key in this folder or above. Nothing goes without one.
    signer = agent_keys.signer_for(args.session)
    if signer is None:
        print("Not delivered: there is no key to sign it with. Give --session <your session "
              "id>, having made and submitted your key (python -m jarvis.agent_key init, then "
              "submit), and once Tim has switched your agent on.")
        return 1
    print(f"Built a {made.kind}: {made.archive}")
    if made.deck is not None and not _check(made, out, signer, args.session):
        return 1
    try:
        decision = made.deck is not None and bool(made.deck.get("decisions"))
        # Signed with the session's own key (package_signature.py): who sent
        # it, and, for a deck, that the files checked are these. An agent
        # package for a Work item carries a key for that item alone.
        grant = (add_work_key(made.archive, args.work) if args.work and
                 made.kind == "agent package" and args.session else None)
        sign(made.archive, signer, args.session,
             (package_signature.DECISION_CHECKED,) if decision else (), grant,
             author.as_dict())
        delivered = dp.deliver(made.archive, args.deliver_to,
                               args.says.strip() or dp.says_of(made.title, made.kind),
                               project=args.project, workflow=args.workflow, work=args.work,
                               source=str(args.handoff or args.source), signer=signer)
    except (OSError, agent_keys.AgentKeyError, package_signature.SignatureError) as e:
        print(f"Not delivered: {e}")
        return 1
    package_id, inbox_id = delivered.package["id"], delivered.inbox["id"]
    answers = (data_paths.packages_dir() / package_id / "results" / "decisions.json"
               if made.deck is not None and made.deck.get("decisions") else None)
    record = {"package": package_id, "inbox": inbox_id, "message": delivered.message.get("id"),
              "title": made.title, "kind": made.kind,
              "project": args.project, "session": args.session, "workflow": args.workflow,
              "work": args.work, "zip": str(made.archive),
              "document": args.document or None, "revision": args.revision or None,
              "answers": str(answers) if answers else None}
    (out / "delivery.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    print(ARRIVED_AS.format(message=delivered.message.get("id"), inbox_id=inbox_id)
          + f" (package {package_id})"
          + ("" if delivered.unpacked else ", already there from the same bytes") + ".")
    if delivered.inbox.get("unproven_claim"):
        print(f"JARVIS did not take it as session {args.session}'s, so his answers will not "
              "come back to it: sign it with that session's own onboarded key.")
    print("Nothing is shown until he asks for it. "
          + ("His answers come back to your session as a message"
             + (" and to the workflow as its decisions" if args.workflow else "") + "."
             if args.session or args.workflow else
             "Give --session next time so his answers come back to you."))
    if answers:
        print(f"The answers are recorded in {answers}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
