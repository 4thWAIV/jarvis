"""A deck's content: reading it, working out what the agent left out
(`expand`), checking it (`problems_of`), building it with the shared deck's
own builder (examples/packages/decision-deck/new_deck.py) and delivering a
zip into a JARVIS's package inbox unshown (`deliver`, POST /api/packages/inbox
with the tool token). `python -m jarvis.make_package` is the command that uses them, and
docs/making-a-package.md is how; `python -m jarvis.decision_package` is that
same command's old name.
"""
from __future__ import annotations

import contextlib
import copy
import difflib
import importlib.util
import io
import json
import re
import ssl
import sys
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from jarvis import data_paths

LIVE = "https://127.0.0.1:8340"
INBOX_ROUTE = "/api/packages/inbox"
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
    problems: list[str] = unknown_keys(deck)
    for field in ("name", "version", "purpose", "title"):
        if not _text(deck.get(field)).strip():
            problems.append(f"The deck has no {field}.")
    _capped(problems, "The deck", "title", deck.get("title"))
    if deck.get("language", "en") not in LANGUAGES:
        problems.append(f"The language is {deck.get('language')!r}; it is one of "
                        f"{', '.join(LANGUAGES)}.")

    decisions = deck.get("decisions", [])
    if not isinstance(decisions, list):
        problems.append("The deck's decisions must be a list.")
        decisions = []
    if not decisions and not [s for s in deck.get("slides") or []
                              if isinstance(s, dict) and "decision" not in s]:
        problems.append("The deck has neither decisions nor slides: give `decisions` for a "
                        "decision package, or only `slides` for a presentation.")
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
    problems += too_many_words(deck)

    for pic in builder().pictures_of(deck) if not problems else []:
        p = Path(pic)
        if not pic or p.is_absolute() or ".." in p.parts:
            problems.append(f"Picture {pic!r} must be a path beside the content file.")
        elif not (folder / p).is_file():
            problems.append(f"Picture {pic} is not at {folder / p}.")
        elif not _is_image(folder / p):
            problems.append(f"Picture {pic} is not a PNG, JPEG, GIF, WebP or SVG image.")
    return problems


def too_many_words(deck: dict) -> list[str]:
    """Every slide and decision part over the builder's word limits
    (new_deck.SLIDE_WORDS, DECISION_WORDS), all at once: the builder itself
    stops at the first."""
    deck_builder, out = builder(), []
    for n, s in enumerate(deck.get("slides") or [], 1):
        if not isinstance(s, dict) or "decision" in s:
            continue
        count = sum(deck_builder.block_words(b) for b in s.get("blocks") or []
                    if isinstance(b, dict))
        if count > deck_builder.SLIDE_WORDS:
            out.append(f"Slide {n} ({_text(s.get('title')) or 'no title'}): it has {count} "
                       f"words; at most {deck_builder.SLIDE_WORDS}. Split it into two slides.")
    for n, d in enumerate(deck.get("decisions") or [], 1):
        if not isinstance(d, dict):
            continue
        parts = [(f, d.get(f)) for f in ("question", "reason", "context")]
        parts += [("says", o.get("says")) for o in d.get("options") or [] if isinstance(o, dict)]
        for field, text in parts:
            count, limit = deck_builder.words(text), deck_builder.DECISION_WORDS[field]
            if count > limit:
                out.append(f"Decision {d.get('id') or n}: a {field} has {count} words; "
                           f"at most {limit}.")
    return out


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
                 "picture", "compare", "say", "text_only"),
    "option": ("id", "name", "says", "picture", "opens", "recommended"),
}


def slug(text, fallback: str) -> str:
    """Lower-case a-z, 0-9 and -, accents folded, at most 40 characters."""
    folded = unicodedata.normalize("NFKD", _text(text)).encode("ascii", "ignore").decode()
    words = re.sub(r"[^a-z0-9]+", "-", folded.lower()).strip("-")[:40].strip("-")
    return words or fallback


def short_name(text) -> str:
    """The jump strip's name for a slide: the words of `text` that fit
    CAPS["short"], whole words only, its punctuation at the ends dropped."""
    words, out = _text(text).split(), ""
    for word in words:
        joined = f"{out} {word}".strip()
        if len(joined) > CAPS["short"]:
            break
        out = joined
    return (out or _text(text)[:CAPS["short"]]).strip(" ,.;:?¿!¡")


def _unique(base: str, taken: set[str]) -> str:
    ident, n = base, 2
    while ident in taken:
        ident, n = f"{base}-{n}", n + 1
    taken.add(ident)
    return ident


def unknown_fields(where: str, item, kind: str, fields: tuple[str, ...]) -> list[str]:
    """Each key of `item` not in `fields`, named with the field it was
    probably meant to be: a misspelt field would otherwise be silently
    ignored."""
    out: list[str] = []
    for key in item if isinstance(item, dict) else ():
        if key not in fields:
            near = difflib.get_close_matches(key, fields, n=1)
            out.append(f"{where}: {key!r} is not a field of a {kind}"
                       + (f"; did you mean {near[0]!r}?" if near else
                          f"; the fields are {', '.join(fields)}."))
    return out


def unknown_keys(deck: dict) -> list[str]:
    """Every misspelt field in the deck (KEYS)."""
    out: list[str] = []

    def look(where: str, item, kind: str):
        out.extend(unknown_fields(where, item, kind, KEYS[kind]))

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
        deck.setdefault("purpose", f"Decisions: {title}" if deck.get("decisions") else title)
    deck.setdefault("version", "1.0.0")
    decisions = [d for d in deck.get("decisions") or [] if isinstance(d, dict)]
    taken: set[str] = {d["id"] for d in decisions if isinstance(d.get("id"), str)}
    for n, d in enumerate(decisions, 1):
        question = _text(d.get("question")).strip()
        if not d.get("short") and question:
            d["short"] = short_name(question)
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
                      "picture": "pictures/office.png", "recommended": True},
                     {"name": "A restaurant", "says": "A short walk away.",
                      "picture": "pictures/restaurant.png"}],
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
    unpacked: bool         # False: the same bytes were already open in the inbox
    inbox: dict            # its row in the package inbox (package_inbox_api.entry_json)


def _context(url: str) -> ssl.SSLContext | None:
    """HTTPS to JARVIS on this Mac: its own certificate, which names
    localhost, pinned in place of a host name check."""
    if not url.startswith("https:"):
        return None
    cert = data_paths.CONFIG_DIR / "cert.pem"
    context = ssl.create_default_context(cafile=str(cert) if cert.is_file() else None)
    context.check_hostname = False
    return context


def post(url: str, token: str, body: dict | bytes, timeout: float = 60) -> dict:
    """One POST with the tool token, of JSON or of a zip's bytes; the
    answer, or OSError saying why."""
    raw = isinstance(body, bytes)
    request = urllib.request.Request(
        url, data=body if raw else json.dumps(body).encode(), method="POST",
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": "application/zip" if raw else "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout, context=_context(url)) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:300]
        raise OSError(f"{url} answered {e.code}: {detail}") from None
    except urllib.error.URLError as e:
        raise OSError(f"{url} could not be reached: {e.reason}") from None


def deliver(archive: Path, base: str, token: str, project: str = "", session: str = "",
            workflow: str = "", work: str = "") -> Delivered:
    """Send the zip to the package inbox of the JARVIS at `base`: checked,
    unpacked and recorded there, never shown (package_inbox.py). `session`
    is who the answers go back to; `workflow` and `work` link it to the
    workflow or work item it is for."""
    query = urllib.parse.urlencode({k: v for k, v in (
        ("project", project), ("session", session), ("workflow", workflow), ("work", work)) if v})
    url = base.rstrip("/") + INBOX_ROUTE + (f"?{query}" if query else "")
    answer = post(url, token, archive.read_bytes())
    if "package" not in answer or "inbox" not in answer:
        raise OSError(f"JARVIS did not take it: {answer.get('error') or answer}")
    return Delivered(answer["package"], bool(answer.get("arrived")), answer["inbox"])


# -- the command -----------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    """The old name of `python -m jarvis.make_package`, kept for the sessions
    whose installed talk-to-jarvis skill still names it: the same command."""
    from jarvis import make_package
    return make_package.main(argv, prog="python -m jarvis.decision_package")


if __name__ == "__main__":
    sys.exit(main())
