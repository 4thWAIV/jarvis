"""The one way an agent makes a package and sends it to Tim.

    python -m jarvis.make_package --handoff HANDOFF.json --engine claude|codex
                                  --session ID [--project P] [--workflow ID] [--work ID]
                                  [--check-only] [--example [decisions|presentation]]

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

The command launches a fresh author on the caller's engine first. Existing source
can accompany the handoff as evidence; --check-only validates source without a model.
The command checks the author's result, builds and zips it, checks it again (a deck
end to end in an isolated JARVIS, decision_check.py; a folder through the
receiver's own reading of the zip, work_packages.recognise), and only then
sends it to the live JARVIS's package inbox (package_inbox.py): unpacked
there, listed in his backlog and counted in his updates, never shown and
never spoken until he asks for it. `--session` is who his answers go back
to; `--workflow` and `--work` name the workflow or work item it is for. Anything it cannot decide it refuses, saying
what to change. Nothing is delivered after any failure.
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

from jarvis import data_paths, decision_package as dp, decision_stamp

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

# What the command prints once JARVIS has taken the package. The inbox id is
# JARVIS's own, answered to the poster alone, so a transcript holding this
# line shows its session delivered that package (server._package_sender).
ARRIVED_AS = "Arrived in Tim's package inbox as {inbox_id}"


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
        if manifest.get("story") and STORY_HELPER not in page:
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


# -- his vote ---------------------------------------------------------------------------

def check_vote(source: Path, work: str) -> None:
    """Every package carries his vote: a deck or a page gets the standard
    one when it asks nothing. An agent or conversation package cannot, so it
    may not name a Work item (package_inbox.no_vote, which the inbox route
    says too)."""
    if not work or not source.is_dir():
        return
    from jarvis import package_inbox
    runs = manifest_of(source)["runs"]
    if runs not in package_inbox.VOTING_RUNS:
        raise dp.Refused([package_inbox.no_vote(work, runs)])


# -- checking ------------------------------------------------------------------------

def _check(made: Made, out: Path) -> bool:
    """A deck end to end in an isolated JARVIS; True when every check passed."""
    from jarvis import decision_check
    print("Checking it in an isolated JARVIS…", flush=True)
    result = decision_check.check(made.archive, made.deck, out / "check")
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
                        help="the work item it is for: its questions show on that item's "
                             "Decisions tab")
    parser.add_argument("--out", type=Path, default=None,
                        help="where the package, its zip and the screenshots go "
                             "(default: a new temporary folder)")
    parser.add_argument("--deliver-to", default=dp.LIVE, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

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
            return 0
        out = (args.out or Path(tempfile.mkdtemp(prefix="package-"))).resolve()
        out.mkdir(parents=True, exist_ok=True)
        from jarvis import package_author
        source = package_author.prepare(args.handoff, args.engine, out, source=source,
                                        session=args.session, work=args.work, workflow=args.workflow)
        check_vote(source, args.work)
        made = make_folder(source, out) if source.is_dir() else make_deck(source, out)
    except dp.Refused as e:
        print("Not built. These need changing:")
        for line in e.problems:
            print(f"  - {line}")
        return 1
    print(f"Built a {made.kind}: {made.archive}")
    if made.deck is not None and not _check(made, out):
        return 1

    try:
        # The tool token, which the stamp needs anyway: the inbox route also
        # takes a `packages` access key, for a sender that has no stamp to make.
        token = data_paths.ensure_tool_token()
        if made.deck is not None and made.deck.get("decisions"):
            # The files checked, stamped for the live JARVIS, which unpacks a
            # decision package only with its own stamp (decision_stamp.py).
            decision_stamp.stamp(made.archive, made.archive, token)
        delivered = dp.deliver(made.archive, args.deliver_to, token, project=args.project,
                               session=args.session, workflow=args.workflow, work=args.work,
                               source=str(args.handoff or args.source))
    except OSError as e:
        print(f"Not delivered: {e}")
        return 1
    package_id, inbox_id = delivered.package["id"], delivered.inbox["id"]
    answers = (data_paths.packages_dir() / package_id / "results" / "decisions.json"
               if made.deck is not None and made.deck.get("decisions") else None)
    record = {"package": package_id, "inbox": inbox_id, "title": made.title, "kind": made.kind,
              "project": args.project, "session": args.session, "workflow": args.workflow,
              "work": args.work, "zip": str(made.archive),
              "answers": str(answers) if answers else None}
    (out / "delivery.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    print(ARRIVED_AS.format(inbox_id=inbox_id) + f" (package {package_id})"
          + ("" if delivered.unpacked else ", already there from the same bytes") + ".")
    if delivered.inbox.get("proof_pending"):
        print(f"JARVIS proves that session {args.session} sent it from this command's output "
              "in that session's own transcript (or its subagent's), within ten minutes; if "
              "it cannot, his answers will not come back to it.")
    elif delivered.inbox.get("unproven_claim"):
        print(f"JARVIS could not prove that session {args.session} ran this command (its own "
              "transcript must show make_package run on this source), so his answers will "
              "not come back to it. Run the command yourself, in your own session.")
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
