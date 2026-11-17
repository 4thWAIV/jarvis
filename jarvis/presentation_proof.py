"""The static proof of a presentation/1 deck: no browser, pure Python.

JARVIS draws every slide in one of its named layouts (layout_spec), in its
bundled font (font_metrics), so whether it fits is computed, not measured:

- Text fit. Every title and text slot, at every frame layout_spec.frames
  gives across the range (its corners and the unit's switch from width to
  height among them): its words broken greedily at spaces only, with the
  font's advance widths at the style's size and a small margin for subpixel
  rounding, must hold within its box; a word wider than its box, or a
  character the font has not, is refused. Tables (equal columns) and bar
  lists are measured the same way, cell by cell and line by line.
- Pictures. Each drawn at its slot's size in every frame by
  picture_proof.drawn (contained, never past twice its size), at least
  the picture floor either way, its labels at least the text floor.
- Story targets. Every target the story points at (its beats' targets and
  actions, each waiting slide's question) is in the target index or one of
  the shared deck's own selectors.
- Templates. The renderer files in the built folder are byte for byte
  JARVIS's own (`template_hashes`), so no package carries a changed
  renderer.

`prove` returns the proof, written as .jarvis/layout-proof.json in the
built package; `Refused` lists every failure in the author's terms.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from jarvis import font_metrics, layout_spec, picture_proof
from jarvis.presentation_schema import (BarObject, ContentSlide, ImageObject, TableObject,
                                        TextObject)

PROOF_FILE = ".jarvis/layout-proof.json"
VERSION = "presentation-proof/1"


@dataclass(frozen=True)
class Failure:
    member: str
    frame: tuple[float, float] | None
    message: str


# -- text -------------------------------------------------------------------------------

def _tokens(runs: list[tuple[str, str]]) -> list[list[tuple[str, str]]]:
    """Words, each a list of (text, face) pieces, split at spaces only."""
    words, current = [], []
    for text, face in runs:
        for n, part in enumerate(text.split(" ")):
            if n:
                if current:
                    words.append(current)
                current = []
            if part:
                current.append((part, face))
    if current:
        words.append(current)
    return words


def _word_width(word, px: float) -> float:
    return sum(font_metrics.width(text, px, face) for text, face in word)


def lines(runs: list[tuple[str, str]], px: float, width: float) -> int | str:
    """How many lines `runs` take in `width` px at `px`, or why they cannot."""
    margin = 1 + layout_spec.spec()["text"]["width_margin"]
    space = font_metrics.width(" ", px)
    count, used = 0, None
    for word in _tokens(runs):
        w = _word_width(word, px) * margin
        if w > width:
            return f"the word {''.join(t for t, _ in word)!r} is wider than its place"
        if used is None or used + space * margin + w > width:
            count, used = count + 1, w
        else:
            used += space * margin + w
    return max(count, 1)


def _style(name: str, u: float) -> tuple[float, float, str]:
    style = layout_spec.spec()["styles"][name]
    px = layout_spec.style_px(name, u)
    return px, px * style["line"], style["face"]


def _runs(paragraph) -> tuple[str, list[tuple[str, str]]]:
    style = "heading" if paragraph.role == "heading" else "body"
    base = layout_spec.spec()["styles"][style]["face"]
    return style, [(r.text, "bold" if r.emphasis == "strong" else base) for r in paragraph.runs]


def text_height(obj: TextObject, width: float, u: float) -> float | str:
    """The height the text object takes in `width` px at deck unit `u`."""
    gap = layout_spec.spec()["paragraph_gap"]
    total = 0.0
    for paragraph in obj.paragraphs:
        style, runs = _runs(paragraph)
        px, line, _ = _style(style, u)
        indent = 1.3 * px if paragraph.role in ("bullet", "numbered") else 0
        count = lines(runs, px, width - indent)
        if isinstance(count, str):
            return count
        total += count * line + (0 if indent else gap * px)
    return total


def table_height(obj: TableObject, width: float, u: float) -> float | str:
    px, line, face = _style("cell", u)
    pad_x, pad_y = layout_spec.spec()["styles"]["cell"]["pad"]
    columns = len(obj.head or obj.rows[0].cells)
    cell = width / columns - 2 * pad_x * u
    total = 0.0
    for n, row in enumerate(([obj.head] if obj.head else []) + [r.cells for r in obj.rows]):
        heads = n == 0 and obj.head
        tallest = 1
        for m, text in enumerate(row):
            bold = heads or (m == 0 and obj.rowHeads)
            count = lines([(text, "bold" if bold else face)], px, cell)
            if isinstance(count, str):
                return count
            tallest = max(tallest, count)
        total += tallest * line + 2 * pad_y * u + 1
    return total


def bars_height(obj: BarObject, width: float, u: float) -> float | str:
    px, line, face = _style("body", u)
    gap = 0.6 * px
    label_width = (width - gap) / 3
    total = 0.0
    for bar in obj.bars:
        count = lines([(bar.label, face)], px, label_width)
        value = lines([(f"{bar.value:g} {obj.units}", face)], px, label_width)
        if isinstance(count, str) or isinstance(value, str):
            return count if isinstance(count, str) else value
        total += max((count + value) * line, 1.8 * px) + gap
    return total + line


# -- the proof ------------------------------------------------------------------------

def _fits(failures, member, frame, needed, room, what):
    if isinstance(needed, str):
        failures.append(Failure(member, frame, f"{what}: {needed}."))
    elif needed > room + 0.5:
        failures.append(Failure(member, frame, f"{what} needs {needed:.0f} px of height and has "
                                                f"{room:.0f} px at {frame[0]:.0f} x {frame[1]:.0f}: say less, "
                                                f"or split the slide."))


def _characters(failures, member, text):
    missing = font_metrics.missing(text)
    if missing:
        failures.append(Failure(member, None, f"it uses {' '.join(missing)}, which JARVIS's font "
                                              f"cannot draw; write it in words."))


def prove_slides(source, pictures: dict[str, picture_proof.Picture]) -> tuple[list[Failure], dict]:
    """Text fit and pictures of every content slide at every frame."""
    spec = layout_spec.spec()
    failures: list[Failure] = []
    tightest: dict[str, float] = {}
    frames = layout_spec.frames()
    for slide in source.document.slides:
        if not isinstance(slide, ContentSlide):
            continue
        _characters(failures, slide.id, slide.title)
        for obj in slide.objects:
            if isinstance(obj, TextObject):
                _characters(failures, obj.id, " ".join(r.text for p in obj.paragraphs for r in p.runs))
            elif isinstance(obj, TableObject):
                _characters(failures, obj.id, " ".join([*(obj.head or []), *(c for r in obj.rows for c in r.cells)]))
            elif isinstance(obj, BarObject):
                _characters(failures, obj.id, " ".join(f"{b.label} {b.value:g} {obj.units}" for b in obj.bars))
        if failures:
            continue
        boxes = layout_spec.boxes(slide.layout, {obj.slot for obj in slide.objects})
        for frame in frames:
            u = layout_spec.unit(frame)
            width, height = spec["unit"]["width"], spec["unit"]["height"]
            title = spec["title"]
            px, line, face = _style("heading", u)
            count = lines([(slide.title, face)], px, title[2] / width * frame[0])
            _fits(failures, slide.id, frame, count if isinstance(count, str) else count * line,
                  title[3] / height * frame[1], "Its title")
            for obj in slide.objects:
                room_w, room_h = layout_spec.frame_box(boxes[obj.slot], frame)
                if isinstance(obj, TextObject):
                    needed = text_height(obj, room_w, u)
                elif isinstance(obj, TableObject):
                    needed = table_height(obj, room_w, u)
                elif isinstance(obj, BarObject):
                    needed = bars_height(obj, room_w, u)
                elif isinstance(obj, ImageObject):
                    problem = picture_proof.drawn_problem(pictures[obj.asset], (room_w, room_h))
                    if problem:
                        failures.append(Failure(obj.id, frame, f"Picture {obj.id}: {problem}."))
                    continue
                else:
                    continue
                _fits(failures, obj.id, frame, needed, room_h, f"The {obj.slot} slot")
                if not isinstance(needed, str):
                    tightest[obj.id] = max(tightest.get(obj.id, 0.0), needed / room_h)
        # One failure a member is enough to act on.
    seen, unique = set(), []
    for failure in failures:
        if failure.member not in seen:
            seen.add(failure.member)
            unique.append(failure)
    return unique, {member: round(share, 3) for member, share in sorted(tightest.items())}


def prove_targets(story: dict, targets: list[dict], deck: dict) -> list[Failure]:
    """Every selector the story names is an element the deck draws: a
    target's, or one of the shared deck's own (each slide's #go-N opener and
    section, a decision's #d-<id> and its options' #opt-<id>-<option>, the
    answers)."""
    known = {"#" + t["dom_id"] for t in targets} | {"#answers", "#answer-list"}
    for n, slide in enumerate(deck.get("slides") or [], 1):
        known.add(f"#{slide.get('id') or f's{n}'}")
    for n in range(1, len(deck.get("slides") or []) + len(deck.get("decisions") or []) + 2):
        known.add(f"#go-{n}")
    for d in deck.get("decisions") or []:
        known.add(f"#d-{d['id']}")
        known.update(f"#opt-{d['id']}-{o['id']}" for o in d.get("options") or [])
    failures = []
    for slide in story.get("slides", []):
        beats = slide.get("beats", [])
        selectors = [slide.get("target")] + [t for b in beats for t in b.get("targets", [])] \
            + [a.get("click") or a.get("show") for b in beats for a in b.get("actions", [])]
        for selector in filter(None, selectors):
            if selector.split(" ")[0] not in known:
                failures.append(Failure(slide.get("title", ""), None,
                                        f"The story points at {selector}, which the deck does not draw."))
    return failures


# -- templates ------------------------------------------------------------------------

def template_hashes() -> dict[str, str]:
    """The SHA-256 of every renderer file a built deck carries, as JARVIS
    ships it."""
    from jarvis import decision_package
    deck = decision_package.BUILDER.parent
    files = {name: (deck / name).read_bytes() for name in ("deck.css", "deck.js", "scripts/decide.py")}
    for name in ("presentation.css", "presentation.js"):
        files[name] = (layout_spec.ASSETS / name).read_bytes()
    files[layout_spec.FONT_CSS] = layout_spec.font_css().encode()
    return {name: hashlib.sha256(data).hexdigest() for name, data in files.items()}


def prove_templates(folder: Path) -> list[Failure]:
    failures = []
    for name, digest in template_hashes().items():
        path = folder / name
        if path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            failures.append(Failure(name, None, f"{name} is not JARVIS's own: a package never "
                                                f"carries a changed renderer."))
        elif not path.is_file() and name != "scripts/decide.py":
            failures.append(Failure(name, None, f"{name} is missing."))
    return failures


def record(source, failures: list[Failure], tightest: dict) -> dict:
    return {"version": VERSION, "source_sha256": source.digest,
            "spec_sha256": hashlib.sha256(layout_spec.PATH.read_bytes()).hexdigest(),
            "frames": [list(f) for f in layout_spec.frames()], "templates": template_hashes(),
            "tightest": tightest, "language": source.language, "proven": not failures}


def write(folder: Path, proof: dict) -> None:
    path = folder / PROOF_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(proof, indent=2) + "\n", encoding="utf-8")


def reprove(folder: Path) -> list[str]:
    """The proof made again on a built deck as it arrives (Tim's JARVIS,
    server._deck_problem): from its own source.yaml, pictures and story,
    against JARVIS's own templates. [] when it proves; else why not."""
    from jarvis import decision_package
    from jarvis.presentation_source import DeckCompileError, read_source
    try:
        recorded = json.loads((folder / PROOF_FILE).read_text(encoding="utf-8"))
        source = read_source(folder / "source.yaml", built=True,
                             language=recorded.get("language"))
        story = json.loads((folder / "story.json").read_text(encoding="utf-8"))
        targets = json.loads((folder / "target-index.json").read_text(encoding="utf-8"))
        deck = decision_package.read(folder / "deck-data.js")
    except (DeckCompileError, OSError, ValueError, decision_package.Refused) as error:
        return [f"Its layout proof cannot be made again: {error}"]
    if recorded.get("source_sha256") != source.digest:
        return ["Its layout proof is for another source than the one it carries."]
    failures, _ = prove_slides(source, source.pictures)
    failures += prove_targets(story, targets, deck) + prove_templates(folder)
    return [failure.message for failure in failures]
