"""The one way an agent sends Tim a decision package.

    python -m jarvis.decision_package CONTENT.json [--project P] [--session ID]
                                      [--out DIR] [--deliver-to URL]

CONTENT.json is the deck's data in the decision deck's format
(docs/work-packages.md, "Decision packages"): the package fields, the slides
and the decisions, each with its options, the recommended option and the one
reason for it, and pictures by path, found beside CONTENT.json. The command:

1. checks the content, and refuses with every problem listed;
2. builds the package with the shared deck's own builder,
   examples/packages/decision-deck/new_deck.py, which it calls unchanged;
3. checks it end to end in a real, isolated JARVIS (decision_check.py):
   found by its title's words and opened by name through that JARVIS's own
   tools, every slide fitting its real frame at both centre sizes with
   readable text and the vote row in view, its story reaching every
   decision, a Choose saving, and an answer by voice showing on screen,
   with a screenshot of each;
4. only if every check passed, unpacks it into the live JARVIS without
   showing it (POST /api/packages/local with the tool token) and prints the
   package id and the words Tim can say to open it. On any failure nothing
   is delivered, and what failed is printed with the screenshots.

--project and --session name who is asking; they are printed and kept in
DIR/delivery.json beside the package's id and where its answers are written.
--deliver-to sends to another JARVIS in place of the live one on
https://127.0.0.1:8340 (the tests use a stand-in).
"""
from __future__ import annotations

import argparse
import contextlib
import importlib.util
import io
import json
import re
import ssl
import sys
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from jarvis import data_paths, decision_stamp

LIVE = "https://127.0.0.1:8340"
LOCAL_ROUTE = "/api/packages/local"
BUILDER = data_paths.SOURCE_ROOT / "examples" / "packages" / "decision-deck" / "new_deck.py"
SLUG = re.compile(r"^[a-z0-9][a-z0-9-]*$")
RESERVED = ("later", "none", "recommended")
LANGUAGES = ("en", "es")

# The most characters each piece of a slide may have. The deck draws on a
# fixed canvas, so words past these do not fit it at a readable size; the
# check in a real frame (decision_check.py) is the final word on fitting.
CAPS = {
    "title": 80,          # the deck's title and a content slide's title
    "short": 16,          # a slide's name on the jump strip
    "question": 120,
    "reason": 160,
    "context": 200,
    "option name": 40,
    "option says": 140,
    "text": 280,          # one paragraph, point, fact, caption or table cell
    "slide": 700,         # every word on one content slide
}

# A decision whose options are not pictured says why in at least this many
# words ("text_only"); it should be rare.
TEXT_ONLY_WORDS = 5

IMAGE_MAGIC = (b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff", b"GIF87a", b"GIF89a")


class Refused(Exception):
    """The content cannot become a decision package; `problems` says why."""

    def __init__(self, problems: list[str]):
        super().__init__("\n".join(problems))
        self.problems = problems


def builder():
    """The shared deck's builder, new_deck.py, imported as it is."""
    spec = importlib.util.spec_from_file_location("new_deck", BUILDER)
    if spec is None or not BUILDER.is_file():
        raise Refused([f"The decision deck's builder is not at {BUILDER}."])
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# -- 1. the content --------------------------------------------------------------------

def read(content: Path) -> dict:
    try:
        deck = builder().read_source(content)
    except (OSError, ValueError) as e:
        raise Refused([f"{content} is not readable deck data: {e}"])
    if not isinstance(deck, dict):
        raise Refused([f"{content} holds no deck: it must be one JSON object."])
    return deck


def _text(value) -> str:
    return value if isinstance(value, str) else ""


def _capped(problems: list[str], where: str, kind: str, value) -> None:
    text = _text(value)
    if len(text) > CAPS[kind]:
        problems.append(f"{where}: the {kind} is {len(text)} characters; at most {CAPS[kind]}.")


def _block_texts(block: dict) -> list[str]:
    """Every line of words one content block shows."""
    out = [_text(block.get(k)) for k in ("p", "lead", "note", "caption")]
    for point in block.get("points") or []:
        out.append(point if isinstance(point, str)
                   else f"{_text(point.get('b'))} {_text(point.get('text'))}".strip())
    for fact in block.get("facts") or []:
        out.append(f"{_text(fact.get('n'))} {_text(fact.get('text'))}".strip())
    table = block.get("table") or {}
    for row in [table.get("head") or []] + list(table.get("rows") or []):
        out += [_text(cell) for cell in row]
    for pic in block.get("pictures") or []:
        out.append(_text(pic.get("caption")) if isinstance(pic, dict) else "")
    return [t for t in out if t]


def _is_image(path: Path) -> bool:
    head = path.read_bytes()[:1024]
    if head.startswith(IMAGE_MAGIC) or (head[:4] == b"RIFF" and head[8:12] == b"WEBP"):
        return True
    return path.suffix.lower() == ".svg" and b"<svg" in head


def problems_of(deck: dict, folder: Path) -> list[str]:
    """Everything wrong with the content, each in a line; [] when it is right."""
    problems: list[str] = []
    for field in ("name", "version", "purpose", "title"):
        if not _text(deck.get(field)).strip():
            problems.append(f"The deck has no {field}.")
    _capped(problems, "The deck", "title", deck.get("title"))
    if deck.get("language", "en") not in LANGUAGES:
        problems.append(f"The language is {deck.get('language')!r}; it is one of "
                        f"{', '.join(LANGUAGES)}.")

    decisions = deck.get("decisions")
    if not isinstance(decisions, list) or not decisions:
        problems.append("The deck has no decisions.")
        decisions = []
    ids = [d.get("id") for d in decisions if isinstance(d, dict)]
    for n, d in enumerate(decisions, 1):
        if not isinstance(d, dict):
            problems.append(f"Decision {n} is not an object.")
            continue
        ident = d.get("id")
        where = f"Decision {ident or n}"
        if not isinstance(ident, str) or not SLUG.match(ident):
            problems.append(f"{where}: its id must be a slug (a-z, 0-9 and -).")
        elif ids.count(ident) > 1:
            problems.append(f"{where}: two decisions have this id.")
        if not _text(d.get("question")).strip():
            problems.append(f"{where}: it has no question.")
        _capped(problems, where, "question", d.get("question"))
        _capped(problems, where, "short", d.get("short"))
        _capped(problems, where, "context", d.get("context"))
        reason = _text(d.get("reason")).strip()
        if not reason:
            problems.append(f"{where}: it has no reason for its recommendation.")
        elif "\n" in reason:
            problems.append(f"{where}: its reason must be one line.")
        _capped(problems, where, "reason", reason)

        options = d.get("options")
        if not isinstance(options, list) or len(options) < 2:
            problems.append(f"{where}: it needs at least two options.")
            options = []
        oids = [o.get("id") for o in options if isinstance(o, dict)]
        for o in options:
            if not isinstance(o, dict):
                problems.append(f"{where}: an option is not an object.")
                continue
            oid = o.get("id")
            owhere = f"{where}, option {oid}"
            if not isinstance(oid, str) or not SLUG.match(oid) or oid in RESERVED:
                problems.append(f"{owhere}: its id must be a slug and not "
                                f"{', '.join(RESERVED)}.")
            elif oids.count(oid) > 1:
                problems.append(f"{owhere}: two options have this id.")
            for field in ("name", "says"):
                if not _text(o.get(field)).strip():
                    problems.append(f"{owhere}: it has no {field}.")
            _capped(problems, owhere, "option name", o.get("name"))
            _capped(problems, owhere, "option says", o.get("says"))
            for child in o.get("opens") or []:
                if child not in ids or child == ident:
                    problems.append(f"{owhere}: it opens {child!r}, which is not another "
                                    f"decision.")
        if d.get("recommended") not in oids:
            problems.append(f"{where}: its recommendation {d.get('recommended')!r} is not one "
                            f"of its options.")
        # Tim chooses by looking: every option is a picture of what it
        # would look like. Rarely a choice cannot be pictured; it then says
        # so, and why, as text_only.
        unpictured = [o.get("id") for o in options if isinstance(o, dict) and not o.get("picture")]
        reason = d.get("text_only")
        if unpictured and not (isinstance(reason, str) and len(reason.split()) >= TEXT_ONLY_WORDS):
            problems.append(
                f"{where}: option{'s' if len(unpictured) > 1 else ''} "
                f"{', '.join(map(str, unpictured))} {'have' if len(unpictured) > 1 else 'has'} "
                f"no picture. Every option needs a picture of what he would see; only a "
                f"choice that truly cannot be pictured may say so with \"text_only\": "
                f"\"<why, in at least {TEXT_ONLY_WORDS} words>\". Only real product choices "
                f"go to Tim: decide technical details yourself.")

    for n, s in enumerate(deck.get("slides") or [], 1):
        if not isinstance(s, dict):
            problems.append(f"Slide {n} is not an object.")
            continue
        if "decision" in s:
            if s["decision"] not in ids:
                problems.append(f"Slide {n} places decision {s['decision']!r}, which is not one.")
            continue
        where = f"Slide {n} ({_text(s.get('title')) or 'no title'})"
        if not _text(s.get("title")).strip():
            problems.append(f"Slide {n}: it has no title.")
        _capped(problems, where, "title", s.get("title"))
        _capped(problems, where, "short", s.get("short"))
        texts = [t for b in s.get("blocks") or [] if isinstance(b, dict) for t in _block_texts(b)]
        for t in texts:
            if len(t) > CAPS["text"]:
                problems.append(f"{where}: a line is {len(t)} characters; at most "
                                f"{CAPS['text']}: {t[:40]}…")
        total = sum(map(len, texts)) + len(_text(s.get("title")))
        if total > CAPS["slide"]:
            problems.append(f"{where}: it has {total} characters of words; at most "
                            f"{CAPS['slide']}. Split it into two slides.")

    for pic in builder().pictures_of(deck) if not problems else []:
        p = Path(pic)
        if not pic or p.is_absolute() or ".." in p.parts:
            problems.append(f"Picture {pic!r} must be a path beside the content file.")
        elif not (folder / p).is_file():
            problems.append(f"Picture {pic} is not at {folder / p}.")
        elif not _is_image(folder / p):
            problems.append(f"Picture {pic} is not a PNG, JPEG, GIF, WebP or SVG image.")
    return problems


def validate(content: Path) -> dict:
    """The deck, or Refused with every problem."""
    deck = read(content)
    problems = problems_of(deck, content.parent)
    if problems:
        raise Refused(problems)
    return deck


# -- 2. building -----------------------------------------------------------------------

def build(content: Path, out: Path, deck: dict) -> Path:
    """The package's folder and zip, made by new_deck.py: the zip's path."""
    folder = out / deck["name"]
    archive = out / f"{deck['name']}-{deck['version']}.zip"
    said = io.StringIO()
    with contextlib.redirect_stdout(said):
        code = builder().main([str(content), str(folder), str(archive)])
    if code != 0 or not archive.is_file():
        raise Refused([f"The builder refused it: {said.getvalue().strip()}"])
    return archive


# -- 4. delivering ---------------------------------------------------------------------

@dataclass(frozen=True)
class Delivered:
    package: dict          # the package as JARVIS describes it (packages_api.describe)
    unpacked: bool         # False: the same bytes were already there


def _context(url: str) -> ssl.SSLContext | None:
    """HTTPS to JARVIS on this Mac: its own certificate, which names
    localhost, pinned in place of a host name check."""
    if not url.startswith("https:"):
        return None
    cert = data_paths.CONFIG_DIR / "cert.pem"
    context = ssl.create_default_context(cafile=str(cert) if cert.is_file() else None)
    context.check_hostname = False
    return context


def post(url: str, token: str, body: dict, timeout: float = 60) -> dict:
    """One JSON POST with the tool token; the answer, or OSError saying why."""
    request = urllib.request.Request(
        url, data=json.dumps(body).encode(), method="POST",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout, context=_context(url)) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:300]
        raise OSError(f"{url} answered {e.code}: {detail}") from None
    except urllib.error.URLError as e:
        raise OSError(f"{url} could not be reached: {e.reason}") from None


def deliver(archive: Path, base: str, token: str) -> Delivered:
    """Unpack the zip into the JARVIS at `base`, never shown: a tool-token
    caller only unpacks (packages_api.NOT_HIS_ACT)."""
    answer = post(base.rstrip("/") + LOCAL_ROUTE, token, {"path": str(archive)})
    if "package" not in answer:
        raise OSError(f"JARVIS did not unpack it: {answer.get('error') or answer}")
    return Delivered(answer["package"], bool(answer.get("unpacked")))


def words_to_open(deck: dict) -> str:
    return f"Open the {deck['title']} package."


# -- the command -----------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m jarvis.decision_package",
        description="Validate, build, check in an isolated JARVIS, and deliver a decision "
                    "package. docs/work-packages.md, \"Decision packages\", has the format.")
    parser.add_argument("content", type=Path, help="the deck's data, one JSON file")
    parser.add_argument("--project", default="", help="the project asking")
    parser.add_argument("--session", default="", help="the session asking")
    parser.add_argument("--out", type=Path, default=None,
                        help="where the package, its zip and the screenshots go "
                             "(default: a new temporary folder)")
    parser.add_argument("--deliver-to", default=LIVE, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    content = args.content.resolve()
    out = (args.out or Path(tempfile.mkdtemp(prefix="decision-package-"))).resolve()
    out.mkdir(parents=True, exist_ok=True)
    try:
        deck = validate(content)
        archive = build(content, out, deck)
    except Refused as e:
        print("Not built. The content needs these changes:")
        for line in e.problems:
            print(f"  - {line}")
        return 1
    print(f"Built {archive}")

    from jarvis import decision_check
    print("Checking it in an isolated JARVIS…", flush=True)
    result = decision_check.check(archive, deck, out / "check")
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
        return 1
    print(f"Every check passed ({len(result.passed)}). Screenshots are in {out / 'check'}.")
    for line in result.passed:
        print(f"  ok: {line}")

    try:
        # The same files, stamped for the live JARVIS (decision_stamp.py),
        # which unpacks a decision package only with its own stamp.
        token = data_paths.ensure_tool_token()
        decision_stamp.stamp(archive, archive, token)
        delivered = deliver(archive, args.deliver_to, token)
    except OSError as e:
        print(f"Not delivered: {e}")
        return 1
    package_id = delivered.package["id"]
    record = {"package": package_id, "title": deck["title"], "project": args.project,
              "session": args.session, "zip": str(archive),
              "answers": str(data_paths.packages_dir() / package_id / "results"
                             / "decisions.json")}
    (out / "delivery.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    print(f"Delivered: package {package_id}"
          + ("" if delivered.unpacked else " (it was already there)")
          + (f", asked by {args.project}" if args.project else "")
          + (f" session {args.session}" if args.session else "") + ".")
    print(f"Tim can say: \"{words_to_open(deck)}\" (or \"Open package {package_id}.\")")
    return 0


if __name__ == "__main__":
    sys.exit(main())
