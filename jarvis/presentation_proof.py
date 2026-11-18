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
- Templates. The renderer files in the built folder are byte for byte
  JARVIS's own (`template_hashes`), so no package carries a changed
  renderer.

Words are drawn in a system font stack, so how much room they take is not
computed: the character caps (decision_package.CAPS) keep a slide's words
within reason.

`record` is the proof, written as .jarvis/layout-proof.json in the
built package; each `Failure` is said in the author's terms.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from jarvis import layout_spec, picture_proof
from jarvis.presentation_schema import ContentSlide, ImageObject

PROOF_FILE = ".jarvis/layout-proof.json"
VERSION = "presentation-proof/2"


@dataclass(frozen=True)
class Failure:
    member: str
    frame: tuple[float, float] | None
    message: str


# -- the proof ------------------------------------------------------------------------

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


def record(source, failures: list[Failure]) -> dict:
    return {"version": VERSION, "source_sha256": source.digest,
            "spec_sha256": hashlib.sha256(layout_spec.PATH.read_bytes()).hexdigest(),
            "frames": [list(f) for f in layout_spec.frames()], "templates": template_hashes(),
            "language": source.language, "proven": not failures}


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
    failures = prove_pictures(source, source.pictures)
    failures += prove_targets(story, targets, deck) + prove_templates(folder)
    return [failure.message for failure in failures]
