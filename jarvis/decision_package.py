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
import copy
import difflib
import importlib.util
import io
import json
import re
import ssl
import sys
import tempfile
import unicodedata
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from jarvis import data_paths

LIVE = "https://127.0.0.1:8340"
LOCAL_ROUTE = "/api/packages/local"
BUILDER = data_paths.SOURCE_ROOT / "examples" / "packages" / "decision-deck" / "new_deck.py"
SLUG = re.compile(r"[a-z0-9][a-z0-9-]*")
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
    problems: list[str] = unknown_keys(deck)
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
        if not isinstance(ident, str) or not SLUG.fullmatch(ident):
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
            if not isinstance(oid, str) or not SLUG.fullmatch(oid) or oid in RESERVED:
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
    """The deck in full form, or Refused with every problem."""
    deck = expand(read(content))
    problems = problems_of(deck, content.parent)
    if problems:
        raise Refused(problems)
    return deck


# -- 1b. the short form ----------------------------------------------------------------
# An agent supplies the decisions, options, reasons and visuals; everything
# the system can work out it works out here, and the result is the full form
# the builder and every older deck already use. A field given is never
# replaced, so a full-form deck passes through unchanged.

KEYS = {
    "deck": ("name", "version", "purpose", "title", "language", "slides", "decisions"),
    "slide": ("id", "kicker", "title", "short", "say", "beats", "blocks", "decision"),
    "decision": ("id", "short", "question", "options", "recommended", "reason", "context",
                 "picture", "say"),
    "option": ("id", "name", "says", "picture", "opens", "recommended"),
}


def slug(text, fallback: str) -> str:
    """Lower-case a-z, 0-9 and -, accents folded, at most 40 characters."""
    folded = unicodedata.normalize("NFKD", _text(text)).encode("ascii", "ignore").decode()
    words = re.sub(r"[^a-z0-9]+", "-", folded.lower()).strip("-")[:40].strip("-")
    return words or fallback


def _unique(base: str, taken: set[str]) -> str:
    ident, n = base, 2
    while ident in taken:
        ident, n = f"{base}-{n}", n + 1
    taken.add(ident)
    return ident


def unknown_keys(deck: dict) -> list[str]:
    """A misspelt field is silently ignored by the deck; name it, with the
    field it was probably meant to be."""
    out: list[str] = []

    def look(where: str, item, kind: str):
        if not isinstance(item, dict):
            return
        for key in item:
            if key not in KEYS[kind]:
                near = difflib.get_close_matches(key, KEYS[kind], n=1)
                out.append(f"{where}: {key!r} is not a field of a {kind}"
                           + (f"; did you mean {near[0]!r}?" if near else
                              f"; the fields are {', '.join(KEYS[kind])}."))

    look("The deck", deck, "deck")
    for n, s in enumerate(deck.get("slides") or [], 1):
        look(f"Slide {n}", s, "slide")
    for n, d in enumerate(deck.get("decisions") or [], 1):
        if not isinstance(d, dict):
            continue
        look(f"Decision {d.get('id') or n}", d, "decision")
        for o in d.get("options") or []:
            look(f"Decision {d.get('id') or n}, option {_text(o.get('id') if isinstance(o, dict) else '')}", o, "option")
    return out


def expand(deck: dict) -> dict:
    """The deck with what can be worked out filled in; the argument is not changed.

    - `name` from the title, `version` 1.0.0, `purpose` from the title.
    - A decision's `id` from its `short` or question; `short` from the question.
    - An option's `id` from its name.
    - `recommended`: the option's id, or its name, or `"recommended": true` on
      exactly one option.
    - `opens` and a slide's `decision`: a decision's id, short name or question.
    """
    deck = copy.deepcopy(deck)
    if not isinstance(deck, dict):
        return deck
    title = _text(deck.get("title")).strip()
    if title:
        deck.setdefault("name", slug(title, "decisions"))
        deck.setdefault("purpose", f"Decisions: {title}")
    deck.setdefault("version", "1.0.0")
    decisions = [d for d in deck.get("decisions") or [] if isinstance(d, dict)]
    taken: set[str] = {d["id"] for d in decisions if isinstance(d.get("id"), str)}
    for n, d in enumerate(decisions, 1):
        question = _text(d.get("question")).strip()
        if not d.get("short") and question:
            d["short"] = question[:CAPS["short"]].rstrip(" ,.;:?¿")
        if not d.get("id"):
            d["id"] = _unique(slug(d.get("short") or question, f"decision-{n}"), taken)
    refs: dict[str, str] = {}
    for d in decisions:
        for key in (d["id"], _text(d.get("short")), _text(d.get("question"))):
            if key.strip():
                refs.setdefault(key.strip().casefold(), d["id"])

    def resolve(ref):
        return refs.get(_text(ref).strip().casefold(), ref)

    for s in deck.get("slides") or []:
        if isinstance(s, dict) and "decision" in s:
            s["decision"] = resolve(s["decision"])
    for d in decisions:
        options = [o for o in d.get("options") or [] if isinstance(o, dict)]
        used: set[str] = {o["id"] for o in options if isinstance(o.get("id"), str)}
        flagged = [o for o in options if o.get("recommended") is True]
        for n, o in enumerate(options, 1):
            if not o.get("id"):
                base = slug(o.get("name"), f"option-{n}")
                o["id"] = _unique(base + "-option" if base in RESERVED else base, used)
        rec = d.get("recommended")
        if rec not in {o["id"] for o in options}:
            named = [o for o in options if _text(o.get("name")).strip().casefold()
                     == _text(rec).strip().casefold() and rec]
            pick = named or (flagged if len(flagged) == 1 and not rec else [])
            if pick:
                d["recommended"] = pick[0]["id"]
        for o in options:
            if isinstance(o.get("recommended"), bool):
                del o["recommended"]
            if isinstance(o.get("opens"), list):
                o["opens"] = [resolve(c) for c in o["opens"]]
    return deck


EXAMPLE = {
    "title": "Team lunch",
    "decisions": [
        {"question": "Where do we eat on Friday?",
         "options": [{"name": "In the office", "says": "Food is brought in.",
                      "recommended": True},
                     {"name": "A restaurant", "says": "A short walk away."}],
         "reason": "Nobody has to book a table."},
    ],
}


# -- 2. building -----------------------------------------------------------------------

def build(content: Path, out: Path, deck: dict) -> Path:
    """The package's folder and zip, made by new_deck.py: the zip's path."""
    folder = out / deck["name"]
    archive = out / f"{deck['name']}-{deck['version']}.zip"
    module = builder()
    said = io.StringIO()
    try:
        with contextlib.redirect_stdout(said):
            module.build_deck(deck, content.parent, folder)
            code = module.zip_package.main([str(folder), str(archive)])
    except (module.Bad, KeyError, ValueError, OSError) as e:
        raise Refused([f"The builder refused it: {e}"])
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
    parser.add_argument("content", type=Path, nargs="?",
                        help="the deck's data, one JSON file")
    parser.add_argument("--example", action="store_true",
                        help="print a minimal content file to start from, and stop")
    parser.add_argument("--check-only", action="store_true",
                        help="validate the content and stop: nothing is built or delivered")
    parser.add_argument("--project", default="", help="the project asking")
    parser.add_argument("--session", default="", help="the session asking")
    parser.add_argument("--out", type=Path, default=None,
                        help="where the package, its zip and the screenshots go "
                             "(default: a new temporary folder)")
    parser.add_argument("--deliver-to", default=LIVE, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    if args.example:
        print(json.dumps(EXAMPLE, indent=2))
        return 0
    if args.content is None:
        parser.error("give the content file (or --example to see one)")
    content = args.content.resolve()
    if args.check_only:
        try:
            deck = validate(content)
        except Refused as e:
            print("The content needs these changes:")
            for line in e.problems:
                print(f"  - {line}")
            return 1
        print(f"The content is right: {len(deck['decisions'])} decision(s), "
              f"package {deck['name']}-{deck['version']}.")
        return 0
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
        delivered = deliver(archive, args.deliver_to, data_paths.ensure_tool_token())
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
