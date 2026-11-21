"""The static proof of a presentation/1 deck: no browser, pure Python.

JARVIS draws every slide in one of its named layouts (layout_spec), so
what can be computed is proven here:

- Pictures. Each drawn at its slot's size in every frame layout_spec.frames
  gives across the range (its corners and the unit's switch from width to
  height among them), by picture_proof.drawn (contained, never past twice
  its size), at least the picture floor either way, its labels at least
  the text floor.
- Story targets. Every target the story points at (its beats' targets and
  actions, each waiting slide's question) is in the target index or one of
  the shared deck's own selectors.
- Words' size. Every text style a deck draws in (layout_spec styles) is
  at least the text floor at the smallest and the largest frame.
- Templates. The renderer files in the built folder are byte for byte
  JARVIS's own (`template_hashes`), so no package carries a changed
  renderer.

Words are drawn in a system font stack, so how much room they take is not
computed: the character caps (decision_package.CAPS) keep a slide's words
within reason.

`record` is the proof, written as .jarvis/layout-proof.json in the
built package; each `Failure` is said in the author's terms. It carries
the layout data (`layout_data`): each slide as JARVIS lays it out, its
title, its objects in reading order with their slot, box and size at the
smallest and largest frame, each word with its style and size there, each
picture's drawn size there, and its narration. The deck review of a
package run reads it beside the passed script, with no browser
(workflow.yaml deck deck_review).
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

from jarvis import data_paths, layout_spec, picture_proof
from jarvis.presentation_schema import (BarObject, ContentSlide, ImageObject, TableObject,
                                        TextObject, VectorObject)

PROOF_FILE = ".jarvis/layout-proof.json"
VERSION = "presentation-proof/2"


@dataclass(frozen=True)
class Failure:
    member: str
    frame: tuple[float, float] | None
    message: str


# -- the proof ------------------------------------------------------------------------

def ordered_objects(slide: ContentSlide):
    """The authored reading order governs every generated human traversal."""
    objects = {obj.id: obj for obj in slide.objects}
    return (objects[ident] for ident in slide.readingOrder)


def sizes() -> dict[str, tuple[float, float]]:
    """The two sizes a deck is drawn at, at the ends of the frame range."""
    frame = layout_spec.spec()["frame"]
    return {"smallest": tuple(frame["min"]), "largest": tuple(frame["max"])}


def _style_px(style: str) -> dict[str, float]:
    return {name: round(layout_spec.style_px(style, layout_spec.unit(frame)), 1)
            for name, frame in sizes().items()}


def prove_text() -> list[Failure]:
    """Every text style at least the text floor at both sizes."""
    floor = layout_spec.spec()["text"]["min_px"]
    return [Failure(style, frame, f"Words in the {style} style are drawn at {px:g} px at "
                                  f"{frame[0]:g} x {frame[1]:g}, under {floor} px.")
            for style in layout_spec.spec()["styles"]
            for name, frame in sizes().items()
            if (px := layout_spec.style_px(style, layout_spec.unit(frame))) < floor]


def prove_pictures(source, pictures: dict[str, picture_proof.Picture]) -> list[Failure]:
    """Every picture of every content slide, drawn in its slot at every
    frame; one failure a picture is enough to act on."""
    failures: list[Failure] = []
    seen: set[str] = set()
    for slide in source.document.slides:
        if not isinstance(slide, ContentSlide):
            continue
        boxes = layout_spec.boxes(slide.layout, {obj.slot for obj in slide.objects})
        for frame in layout_spec.frames():
            for obj in slide.objects:
                if not isinstance(obj, ImageObject) or obj.id in seen:
                    continue
                problem = picture_proof.drawn_problem(pictures[obj.asset],
                                                      layout_spec.frame_box(boxes[obj.slot], frame))
                if problem:
                    seen.add(obj.id)
                    failures.append(Failure(obj.id, frame, f"Picture {obj.id}: {problem}."))
    return failures


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


# -- the layout data -------------------------------------------------------------------

def _box_px(box) -> dict[str, list[float]]:
    return {name: [round(v, 1) for v in layout_spec.frame_box(box, frame)]
            for name, frame in sizes().items()}


def _paragraph_style(role: str) -> str:
    return "heading" if role == "heading" else "body"


def _object_layout(obj, box, pictures: dict) -> dict:
    out = {"id": obj.id, "type": obj.type, "slot": obj.slot, "box": list(box),
           "size_px": _box_px(box)}
    if isinstance(obj, TextObject):
        out["paragraphs"] = [{"id": p.id, "role": p.role, "style": _paragraph_style(p.role),
                              "text_px": _style_px(_paragraph_style(p.role)),
                              "runs": [{"id": r.id, "text": r.text, "emphasis": r.emphasis}
                                       for r in p.runs]} for p in obj.paragraphs]
    elif isinstance(obj, ImageObject):
        picture = pictures[obj.asset]
        out.update(asset=obj.asset, alt=obj.alt, natural_px=[picture.width, picture.height],
                   drawn_px={name: [round(v, 1) for v in picture_proof.drawn(
                       picture, layout_spec.frame_box(box, frame))]
                       for name, frame in sizes().items()})
    elif isinstance(obj, TableObject):
        out.update(alt=obj.alt, style="cell", text_px=_style_px("cell"), head=obj.head,
                   rows=[{"id": r.id, "cells": r.cells} for r in obj.rows])
    elif isinstance(obj, BarObject):
        out.update(alt=obj.alt, units=obj.units, domain=obj.domain,
                   bars=[{"id": b.id, "label": b.label, "value": b.value} for b in obj.bars])
    elif isinstance(obj, VectorObject):
        out.update(alt=obj.alt, members=[{"id": m.id, "type": m.type, "alt": m.alt}
                                         for m in obj.members])
    return out


# Where the shared deck draws a decision slide's words, and the rule in its
# deck.css that sizes each: the question its heading, the option names
# their own, every other word the deck's body size.
DECK_CSS = data_paths.SOURCE_ROOT / "examples" / "packages" / "decision-deck" / "deck.css"
DECISION_WORDS = {"question": "h2", "option_name": ".option h3", "context": "--body",
                  "reason": "--body", "says": "--body"}
_CLAMP = r"\s*clamp\(([\d.]+)px,\s*calc\(([\d.]+) \* var\(--u\)\),\s*([\d.]+)px\)"


def deck_words_px(rule: str) -> dict[str, float]:
    """The px size at the smallest and the largest frame of the words a
    deck.css rule sizes (a selector's font-size, or a custom property), as
    the shared deck draws them: clamp(min, units x the deck unit, max)."""
    css = DECK_CSS.read_text(encoding="utf-8")
    found = (re.search(re.escape(rule) + r":" + _CLAMP, css) if rule.startswith("--") else
             re.search(r"(?m)^" + re.escape(rule) + r"\s*\{[^}]*?font-size:" + _CLAMP, css))
    if found is None:
        raise ValueError(f"deck.css sizes no words by {rule!r}")
    low, units, high = (float(g) for g in found.groups())
    return {name: round(min(max(units * layout_spec.unit(frame), low), high), 1)
            for name, frame in sizes().items()}


def _story_of(story: dict | None, decision: str) -> list[dict]:
    """What JARVIS says on a decision's slide: the story's beats on the
    slide that waits for it."""
    for slide in (story or {}).get("slides") or []:
        if slide.get("waits") == decision:
            return [{"say": b.get("say", "")} for b in slide.get("beats") or []]
    return []


def layout_data(source, story: dict | None = None) -> list[dict]:
    """Each slide as JARVIS lays it out (layout_spec), worked out from the
    source, its pictures and the spec alone: a content slide's title with
    its size, its layout, its objects in reading order (each with its slot,
    its box in deck units and its size in px at both sizes; a text's words
    with their style and size; a picture's own and drawn size; a table's
    cells; a bar's values) and its narration; a decision's question,
    options and recommendation, the size of each of its words as the shared
    deck draws them (`deck_words_px`) and, given the deck's `story`, what
    JARVIS says on its slide."""
    decisions = {d.id: d for d in source.document.decisions}
    out = []
    for n, slide in enumerate(source.document.slides, 1):
        if not isinstance(slide, ContentSlide):
            d = decisions[slide.decision]
            out.append({"slide": n, "id": slide.id, "type": "decision", "decision": d.id,
                        "question": d.question, "context": d.context,
                        "options": [{"id": o.id, "name": o.name, "says": o.says,
                                     "picture": o.picture} for o in d.options],
                        "recommended": d.recommended, "reason": d.reason, "say": d.say,
                        "text_px": {k: deck_words_px(rule)
                                    for k, rule in DECISION_WORDS.items()},
                        "narration": _story_of(story, d.id)})
            continue
        boxes = layout_spec.boxes(slide.layout, {obj.slot for obj in slide.objects})
        out.append({"slide": n, "id": slide.id, "type": "content", "title": slide.title,
                    "title_box": list(layout_spec.spec()["title"]),
                    "title_px": _style_px("heading"),
                    "title_size_px": _box_px(layout_spec.spec()["title"]),
                    "layout": slide.layout,
                    "objects": [_object_layout(obj, boxes[obj.slot], source.pictures)
                                for obj in ordered_objects(slide)],
                    "narration": [{"id": c.id, "say": c.say, "focus": list(c.focus)}
                                  for c in slide.narration]})
    return out


def record(source, failures: list[Failure], story: dict | None = None) -> dict:
    return {"version": VERSION, "source_sha256": source.digest,
            "spec_sha256": hashlib.sha256(layout_spec.PATH.read_bytes()).hexdigest(),
            "frames": [list(f) for f in layout_spec.frames()], "templates": template_hashes(),
            "language": source.language, "proven": not failures,
            "sizes": {name: list(frame) for name, frame in sizes().items()},
            "slides": layout_data(source, story)}


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
    failures = prove_pictures(source, source.pictures) + prove_text()
    failures += prove_targets(story, targets, deck) + prove_templates(folder)
    return [failure.message for failure in failures]
