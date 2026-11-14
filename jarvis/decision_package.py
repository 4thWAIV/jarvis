"""A deck's content: reading it, working out what the agent left out
(`expand`), checking it (`problems_of`), building it with the shared deck's
own builder (examples/packages/decision-deck/new_deck.py) and delivering a
zip into a JARVIS's package inbox unshown (`deliver`, POST /api/packages/inbox
with the tool token). `python -m jarvis.make_package` is the command that uses them, and
docs/making-a-package.md is how; `python -m jarvis.decision_package` is that
same command's old name.
"""
from __future__ import annotations

import base64
import contextlib
import copy
import difflib
import importlib.util
import io
import json
import re
import shutil
import ssl
import sys
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import dataclasses
from dataclasses import dataclass
from pathlib import Path

from jarvis import data_paths

LIVE = "https://127.0.0.1:8340"
# Every package an agent sends is a message carrying it (messages_api.py).
MESSAGE_ROUTE = "/api/messages"
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
    "purpose": 200,       # what the package is for (deck/0.1 `purpose`)
    "conclusion": 200,    # the answer or the ask, one sentence (deck/0.1 `conclusion`)
    "why_you": 160,       # why a decision is his and not an agent's
    "evidence": 160,      # one line of a decision's evidence
}

# A decision whose options are not pictured says why in at least this many
# words ("text_only"); it should be rare.
TEXT_ONLY_WORDS = 5

# A decision's evidence: this many lines at most (its lead-in slide shows
# them all, under the slide's word limit).
EVIDENCE_LINES = 3

# The opening's close-paraphrase test (opening_problems): of the distinct
# words of at least OPENING_WORD_LETTERS letters, folded (spoken_words), this
# share must be on the first slide: the conclusion's among the words it
# shows, the purpose's among those and its narration together.
OPENING_WORD_LETTERS = 4
CONCLUSION_SHARE = 0.8
PURPOSE_SHARE = 0.6

# The words of the lead-in slide made from a decision's own fields
# (lead_in_slide), in the deck's language.
LEAD_IN_WORDS = {
    "en": {"title": "Before you decide: {short}", "why": "Why it is yours:",
           "evidence": "What it rests on:"},
    "es": {"title": "Antes de decidir: {short}", "why": "Por qué le toca a usted:",
           "evidence": "En qué se apoya:"},
}

IMAGE_MAGIC = (b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff", b"GIF87a", b"GIF89a")

# An SVG says its own size: its root <svg> has width and height, each a
# positive number of pixels ("320" or "320px"). One without them (a viewBox
# alone, or a percentage) has no size of its own in an <img> and can draw as
# nothing, so a slide showing it is blank (docs/making-a-package.md).
SVG_HEAD_BYTES = 64 * 1024
_SVG_ROOT = re.compile(rb"<svg\b([^>]*)>", re.IGNORECASE)
_SVG_ATTR = re.compile(rb"""([A-Za-z_:][\w:.-]*)\s*=\s*(?:"([^"]*)"|'([^']*)')""")
_SVG_PIXELS = re.compile(r"(\d+(?:\.\d+)?|\.\d+)(?:px)?")


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
    return _is_svg(path, head)


def _is_svg(path: Path, head: bytes) -> bool:
    return path.suffix.lower() == ".svg" and b"<svg" in head


def svg_size_problem(path: Path) -> str:
    """Why an SVG has no size of its own ("" when its root <svg> gives width
    and height in pixels, or it is no SVG), by the rule above SVG_HEAD_BYTES."""
    with open(path, "rb") as f:
        head = f.read(SVG_HEAD_BYTES)
    if not _is_svg(path, head[:1024]):
        return ""
    root = _SVG_ROOT.search(head)
    attrs = {m.group(1).lower(): (m.group(2) if m.group(2) is not None else m.group(3))
             for m in _SVG_ATTR.finditer(root.group(1))} if root else {}
    bad = []
    for name in (b"width", b"height"):
        value = (attrs.get(name) or b"").decode("utf-8", "replace")
        size = _SVG_PIXELS.fullmatch(value.strip())
        if not size or float(size.group(1)) <= 0:
            bad.append(f"{name.decode()}={value!r}" if value else f"no {name.decode()}")
    if not bad:
        return ""
    return (f"its <svg> has {' and '.join(bad)}: give the root <svg> width and height in "
            f"pixels (for example width=\"320\" height=\"220\"), or it can draw as nothing")


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
        where = _slide_where(n, s)
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
        elif (unsized := svg_size_problem(folder / p)):
            problems.append(f"Picture {pic}: {unsized}.")
    return problems


def _slide_where(n: int, s: dict) -> str:
    """How a problem names a content slide: a lead-in slide made from a
    decision's fields by the decision, since those are what the author
    wrote."""
    if s.get("lead_in"):
        return (f"Decision {s['lead_in']}'s lead-in (its question, why_you and evidence "
                f"together)")
    return f"Slide {n} ({_text(s.get('title')) or 'no title'})"


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
            out.append(f"{_slide_where(n, s)}: it has {count} words; at most "
                       f"{deck_builder.SLIDE_WORDS}. "
                       + ("Shorten them." if s.get("lead_in") else "Split it into two slides."))
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
    "deck": ("name", "version", "purpose", "conclusion", "title", "language", "slides",
             "decisions", "understanding"),
    "slide": ("id", "kicker", "title", "short", "say", "beats", "blocks", "decision", "lead_in"),
    "decision": ("id", "short", "question", "options", "recommended", "reason", "context",
                 "picture", "compare", "say", "text_only", "why_you", "evidence"),
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


def _unique(wanted: str, taken: set[str]) -> str:
    ident, n = wanted, 2
    while ident in taken:
        ident, n = f"{wanted}-{n}", n + 1
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
    - `evidence` given as one line: a list of that line.
    - A decision with `why_you` and `evidence`: its lead-in slide
      (lead_in_slide), placed just before the decision's own slide.
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
        if isinstance(d.get("evidence"), str):
            d["evidence"] = [d["evidence"]]
    return with_lead_ins(deck)


# -- 1c. the briefing ------------------------------------------------------------------
# Every deck an agent sends is a conclusion-first briefing (deck/0.1's
# document `purpose` and `conclusion`): it opens with what it is for and its
# answer or ask, and every decision is preceded by its lead-in. The fields
# are checked here, and so is what the slides show of them: metadata alone
# says nothing about what he sees.

def lead_in_slide(deck: dict, d: dict) -> dict:
    """The slide before a decision's own, made from its fields: the
    question, why it is his (`why_you`) and what it rests on (`evidence`),
    and JARVIS saying the same."""
    words = LEAD_IN_WORDS.get(deck.get("language", "en"), LEAD_IN_WORDS["en"])
    question, why = _text(d.get("question")).strip(), _text(d.get("why_you")).strip()
    evidence = [_text(e).strip() for e in d.get("evidence") or []]
    return {"id": f"lead-{d['id']}", "lead_in": d["id"],
            "title": words["title"].format(short=_text(d.get("short")) or d["id"]),
            "short": short_name(d.get("short") or question),
            "say": " ".join([question, why, *evidence]),
            "blocks": [{"lead": question},
                       {"p": f"**{words['why']}** {why}"},
                       {"note": words["evidence"]},
                       {"points": evidence}]}


def with_lead_ins(deck: dict) -> dict:
    """The deck with each decision that gives `why_you` and `evidence` led
    in by its lead-in slide, just before where its own slide is drawn: its
    placement in `slides`, else (placed nowhere) after every slide, in the
    order deck.js draws unplaced decisions. A deck already led in is left
    as it is."""
    slides = deck.get("slides")
    if slides is not None and not isinstance(slides, list):
        return deck
    slides = list(slides or [])
    led = {s.get("lead_in") for s in slides if isinstance(s, dict)}
    placed = {s.get("decision") for s in slides if isinstance(s, dict)}
    for d in deck.get("decisions") or []:
        if (not isinstance(d, dict) or d.get("id") in led or not _text(d.get("why_you")).strip()
                or not d.get("evidence") or not isinstance(d.get("evidence"), list)):
            continue
        slide = lead_in_slide(deck, d)
        if d["id"] in placed:
            at = next(n for n, s in enumerate(slides)
                      if isinstance(s, dict) and s.get("decision") == d["id"])
            slides.insert(at, slide)
        else:
            slides += [slide, {"decision": d["id"]}]
    if slides:
        deck["slides"] = slides
    return deck


def _content_words(text: str) -> set[str]:
    """The words the opening's paraphrase test counts: folded, at least
    OPENING_WORD_LETTERS letters, no ** or ` marks."""
    from jarvis import spoken_words
    found = spoken_words.folded_words(re.sub(r"\*\*|`", "", _text(text)))
    return {w for w in found if len(w) >= OPENING_WORD_LETTERS} or set(found)


def carries(text: str, shown: str, share: float) -> bool:
    """Whether `shown` carries `text`, word for word or closely
    paraphrased: at least `share` of `text`'s distinct content words
    (_content_words) are among `shown`'s."""
    wanted = _content_words(text)
    if not wanted:
        return False
    return len(wanted & _content_words(shown)) >= share * len(wanted)


def _said(slide: dict) -> str:
    """What JARVIS says over a content slide: its `say`, or its beats'."""
    beats = [_text(b.get("say")) for b in slide.get("beats") or [] if isinstance(b, dict)]
    return " ".join([_text(slide.get("say")), *beats]).strip()


def one_sentence(text: str) -> bool:
    """One line, ending at most once in . ! ? or …, whatever follows it."""
    return "\n" not in text and len(re.split(r"(?<=[.!?…])\s+(?=\S)", text.strip())) == 1


def briefing_problems(given: dict, deck: dict) -> list[str]:
    """Everything that keeps the deck from being a briefing: the author's
    own `purpose` and `conclusion` (`given`, the content as written, since
    expand works out a purpose from the title), the opening and each
    decision's lead-in (`deck`, expanded). [] when it is one."""
    problems: list[str] = []
    for field, tells in (
            ("purpose", "one sentence saying what the package is for, in the reader's "
                        "terms (\"Decide how JARVIS opens a package\")"),
            ("conclusion", "one sentence giving the answer or the ask (\"Approve the "
                           "opening check so every package says why it came\")")):
        value = _text(given.get(field)).strip() if isinstance(given, dict) else ""
        if not value:
            problems.append(f"The deck has no {field}: write \"{field}\": {tells}.")
            continue
        _capped(problems, "The deck", field, value)
    conclusion = _text(given.get("conclusion")).strip() if isinstance(given, dict) else ""
    if conclusion and not one_sentence(conclusion):
        problems.append("The deck's conclusion is more than one sentence: give the answer or "
                        "the ask in one; the rest belongs on the slides.")
    for n, slide in enumerate(given.get("slides") or [] if isinstance(given, dict) else [], 1):
        if isinstance(slide, dict) and "lead_in" in slide:
            problems.append(f"Slide {n}: a lead-in slide is made from its decision's question, "
                            f"why_you and evidence; remove this one and give those fields.")
    return problems + opening_problems(deck) + lead_in_problems(deck)


def opening_problems(deck: dict) -> list[str]:
    """The first slide drawn is the opening: not a decision or a lead-in,
    words first (no table or facts as its first block), its title and blocks
    showing the conclusion, those and its narration carrying the purpose,
    and a narration."""
    from jarvis import package_inbox
    slides = [s for s in deck.get("slides") or [] if isinstance(s, dict)]
    asks = [d for d in deck.get("decisions") or []
            if isinstance(d, dict) and d.get("id") != package_inbox.UNDERSTOOD_ID]
    fix = ("Start with an opening slide: its title says what the package is for, a `lead` "
           "block states the conclusion, and its `say` tells both and why they matter to him.")
    first = slides[0] if slides else None
    if first is None or "decision" in first:
        what = f"decision {first['decision']!r}" if first else (
            f"decision {asks[0].get('id')!r}" if asks else "nothing")
        return [f"The deck opens on {what}, not on its purpose. {fix}"]
    if first.get("lead_in"):
        return [f"The deck opens on decision {first['lead_in']!r}'s lead-in, not on its "
                f"purpose. {fix}"]
    where = f"Slide 1 ({_text(first.get('title')) or 'no title'}), the opening"
    problems: list[str] = []
    blocks = [b for b in first.get("blocks") or [] if isinstance(b, dict)]
    if not blocks:
        problems.append(f"{where}: it has no blocks. {fix}")
    elif "table" in blocks[0] or "facts" in blocks[0]:
        problems.append(f"{where}: it starts with {'a table' if 'table' in blocks[0] else 'facts'}"
                        f", data before its purpose. Say the conclusion in words first, and "
                        f"move the data to a later slide titled with the claim it supports.")
    said = _said(first)
    if not said:
        problems.append(f"{where}: it has no `say`. JARVIS tells it: give what he hears, the "
                        f"purpose, the conclusion and why it matters, in two or three sentences.")
    shown = " ".join([_text(first.get("title"))]
                     + [t for b in blocks for t in _block_texts(b)])
    conclusion, purpose = _text(deck.get("conclusion")), _text(deck.get("purpose"))
    if conclusion.strip() and not carries(conclusion, shown, CONCLUSION_SHARE):
        problems.append(f"{where}: it does not show the conclusion ({conclusion.strip()!r}). "
                        f"Put it, or a close paraphrase of it, in the slide's title or a block.")
    if purpose.strip() and not carries(purpose, f"{shown} {said}", PURPOSE_SHARE):
        problems.append(f"{where}: neither it nor its narration says the purpose "
                        f"({purpose.strip()!r}). Say what the package is for in its title, a "
                        f"block or its `say`.")
    return problems


def lead_in_problems(deck: dict) -> list[str]:
    """Each decision but the standard vote needs its lead-in: `why_you`,
    one line, and `evidence`, one to EVIDENCE_LINES lines; expand draws
    them, with the question, on the slide before the decision's."""
    from jarvis import package_inbox
    problems: list[str] = []
    for n, d in enumerate(deck.get("decisions") or [], 1):
        if not isinstance(d, dict) or d.get("id") == package_inbox.UNDERSTOOD_ID:
            continue
        where = f"Decision {d.get('id') or n}"
        why, evidence = _text(d.get("why_you")).strip(), d.get("evidence")
        lines = ([_text(e).strip() for e in evidence] if isinstance(evidence, list) else [])
        missing = [f for f, ok in (("why_you", why), ("evidence", lines and all(lines)))
                   if not ok]
        if missing:
            problems.append(
                f"{where}: it has no lead-in ({' and '.join(missing)}). Before its choices he "
                f"needs the question, why it is his to decide and not an agent's "
                f"(\"why_you\": one line), and what it rests on (\"evidence\": one to "
                f"{EVIDENCE_LINES} lines of fact).")
            continue
        if "\n" in why:
            problems.append(f"{where}: its why_you must be one line.")
        _capped(problems, where, "why_you", why)
        if len(lines) > EVIDENCE_LINES:
            problems.append(f"{where}: it has {len(lines)} lines of evidence; at most "
                            f"{EVIDENCE_LINES}. Keep the ones that change his answer.")
        for line in lines:
            _capped(problems, where, "evidence", line)
    return problems


EXAMPLE = {
    "title": "Team lunch",
    "purpose": "Choose where the team eats on Friday.",
    "conclusion": "Choose lunch in the office on Friday, so nobody has to book a table.",
    "slides": [
        {"title": "Where should Friday's lunch be?",
         "blocks": [{"lead": "Choose lunch in the office on Friday, so nobody has to book "
                             "a table."},
                    {"p": "The restaurant needs a booking by Wednesday."}],
         "say": "This is about where the team eats on Friday. I suggest the office, so "
                "nobody has to book a table; the restaurant needs a booking by Wednesday."},
    ],
    "decisions": [
        {"question": "Where do we eat on Friday?",
         "why_you": "It is your team's day and your budget.",
         "evidence": ["The restaurant needs a booking by Wednesday.",
                      "The office caterer needs a day's notice."],
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
            from jarvis.package_understanding import declaration, prepare_deck
            if (folder.is_symlink() or folder.resolve().parent != out.resolve()
                    or archive.parent != out):
                raise ValueError("Generated output must stay in the selected output folder.")
            inputs = [content] + [content.parent / name for name in (
                module.pictures_of(deck) + list(declaration(deck.get("understanding")).values()))]
            if any(path.resolve().is_relative_to(folder.resolve()) for path in inputs):
                raise ValueError("The generated output overlaps author inputs; choose a separate output folder.")
            shutil.rmtree(folder, ignore_errors=True)
            module.build_deck(deck, content.parent, folder)
            prepare_deck(folder, deck, module, content.parent)
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
    message: dict = dataclasses.field(default_factory=dict)  # the message carrying it


def _context(url: str) -> ssl.SSLContext | None:
    """HTTPS to JARVIS on this Mac: its own certificate, which names
    localhost, pinned in place of a host name check."""
    if not url.startswith("https:"):
        return None
    cert = data_paths.CONFIG_DIR / "cert.pem"
    context = ssl.create_default_context(cafile=str(cert) if cert.is_file() else None)
    context.check_hostname = False
    return context


def post(url: str, token: str, body: dict | bytes, timeout: float = 60,
         signer: tuple[Path, str] | None = None) -> dict:
    """One POST, of JSON or of a zip's bytes, signed by the agent's key
    (`signer`, agent_keys.request_header) or with the tool token; the
    answer, or OSError saying why."""
    from jarvis import agent_keys
    raw = isinstance(body, bytes)
    data = body if raw else json.dumps(body).encode()
    authorization = (agent_keys.request_header(signer[0], signer[1], "POST", url, data)
                     if signer else f"Bearer {token}")
    request = urllib.request.Request(
        url, data=data, method="POST",
        headers={"Authorization": authorization,
                 "Content-Type": "application/zip" if raw else "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout, context=_context(url)) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:300]
        raise OSError(f"{url} answered {e.code}: {detail}") from None
    except urllib.error.URLError as e:
        raise OSError(f"{url} could not be reached: {e.reason}") from None


def says_of(title: str, kind: str = "package") -> str:
    """What the message carrying a package says when its sender gives no
    words of its own: its kind and title."""
    return f"{kind[:1].upper()}{kind[1:]}: {' '.join(str(title).split())}"[:200]


def deliver(archive: Path, base: str, says: str, project: str = "", workflow: str = "",
            work: str = "", source: str = "",
            signer: tuple[Path, str] | None = None) -> Delivered:
    """Send the zip to the JARVIS at `base` as a message carrying it (POST
    /api/messages, signed by the agent's key, `signer`): checked, unpacked
    and recorded in his package inbox there, never shown, and the message
    on the Work item `work` (else his Inbox) saying `says`. His answers go
    to the session the signature proves; `workflow` links it to the
    workflow it is for, `source` is what make_package was given (a
    subagent that ran it on that is named beside its parent)."""
    query = urllib.parse.urlencode({k: v for k, v in (
        ("work", work), ("project", project), ("workflow", workflow), ("source", source))
        if v})
    url = base.rstrip("/") + MESSAGE_ROUTE + (f"?{query}" if query else "")
    body = {"says": says, "package": base64.b64encode(archive.read_bytes()).decode("ascii")}
    answer = post(url, "", body, signer=signer)
    if "package" not in answer or "inbox" not in answer:
        raise OSError(f"JARVIS did not take it: {answer.get('error') or answer}")
    return Delivered(answer["package"], bool(answer.get("arrived")), answer["inbox"],
                     answer.get("message") or {})


# -- the command -----------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    """The old name of `python -m jarvis.make_package`, kept for the sessions
    whose installed talk-to-jarvis skill still names it: the same command."""
    from jarvis import make_package
    return make_package.main(argv, prog="python -m jarvis.decision_package")


if __name__ == "__main__":
    sys.exit(main())
