"""The one way an agent makes a package and sends it to Tim.

    python -m jarvis.make_package CONTENT.json | FOLDER [--project P] [--session ID]
                                  [--workflow ID] [--work ID]
                                  [--check-only] [--example [decisions|presentation]]

docs/making-a-package.md is the whole of how to use it. In short:

- CONTENT.json is a deck, drawn by the shared deck
  (examples/packages/decision-deck/). With `decisions` it is a decision
  package; with only `slides` it is a presentation (an update), which has no
  votes. Everything that can be worked out is (decision_package.expand).
- FOLDER is a hand-made package (a page of its own, an agent's brief or a
  conversation's skill). Its package.json needs only `purpose`; the rest is
  worked out from the files there.

Either way the command checks it, builds and zips it, checks it again (a deck
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

from jarvis import data_paths, decision_package as dp, decision_stamp, package_signature

# The file each way of running needs, by its usual name: a folder holding
# exactly one of them runs that way.
USUAL_FILES = {"page": "index.html", "agent": "BRIEF.md", "conversation": "SKILL.md"}
STORY_FILE = "story.json"
DEFAULT_RETURNS = ["results/**"]
MANIFEST_FIELDS = ("name", "version", "purpose", "runs", "skill", "brief", "entry", "story",
                   "returns", "understanding")
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

# What the command prints once JARVIS has taken the package.
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
    raw = dp.read(content)
    deck = dp.expand(raw)
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


def deck_kind(deck: dict) -> str:
    return "decision package" if deck.get("decisions") else "presentation"


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


# -- signing --------------------------------------------------------------------------

def sign(archive: Path, signer: tuple[Path, str], session: str,
         statements: tuple[str, ...] = (), work_key: dict | None = None) -> None:
    """Sign the zip in place with the session's own key (package_signature)."""
    package_signature.sign(archive, archive, signer[0], signer[1], session, statements,
                           work_key)


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
                        help="a deck's content (one JSON file) or a hand-made package's folder")
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
    if args.source is None:
        parser.error("give the content file or the package's folder (or --example)")
    source = args.source.resolve()
    if not source.exists():
        print(f"There is nothing at {source}.")
        return 1
    try:
        if args.check_only:
            if source.is_dir():
                manifest = manifest_of(source)
                print(f"The folder is right: a {manifest['runs']} package, "
                      f"{manifest['name']}-{manifest['version']}.")
            else:
                deck = deck_of(source)
                print(f"The content is right: a {deck_kind(deck)}, "
                      f"{len(deck.get('decisions') or [])} decision(s), "
                      f"package {deck['name']}-{deck['version']}.")
            return 0
        out = (args.out or Path(tempfile.mkdtemp(prefix="package-"))).resolve()
        out.mkdir(parents=True, exist_ok=True)
        made = make_folder(source, out) if source.is_dir() else make_deck(source, out)
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
             (package_signature.DECISION_CHECKED,) if decision else (), grant)
        delivered = dp.deliver(made.archive, args.deliver_to, "", project=args.project,
                               session=args.session, workflow=args.workflow, work=args.work,
                               source=str(args.source), signer=signer)
    except (OSError, agent_keys.AgentKeyError, package_signature.SignatureError) as e:
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
