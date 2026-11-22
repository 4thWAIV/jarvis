"""Decks for tests written the one way an author writes them now,
presentation.yaml: `presentation_of` turns a deck in the shared deck's own
data form (slides of blocks, decisions) into presentation/1, its pictures
filled in from their files, and `write_yaml` writes it with its pictures."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import yaml

from jarvis import picture_proof

# A presentation in the shared deck's data form, for the deck policy tests
# (make_package.deck_of, decision_package.expand).
PRESENTATION = {
    "title": "Team lunch moved",
    "purpose": "Tell the team where Friday's lunch is now.",
    "conclusion": "Friday's lunch is now in the office, at noon.",
    "slides": [
        {"title": "Friday's lunch has moved",
         "blocks": [{"lead": "Friday's lunch is now in the office, at noon."},
                    {"p": "Nobody has to book a table, and nothing is asked of you."}],
         "say": "This tells you where Friday's lunch is now. It is in the office, at noon, "
                "so nobody has to book a table."},
        {"title": "What changed",
         "blocks": [{"points": ["Food is brought in.", "Nobody has to book a table."]}]},
    ],
}


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60] or "x"


def _paragraphs(ident: str, blocks: list[dict]) -> list[dict]:
    out = []
    for block in blocks:
        texts = [(t, "paragraph") for k in ("lead", "p", "note") if k in block for t in [block[k]]]
        texts += [(t if isinstance(t, str) else t.get("text", ""), "bullet") for t in block.get("points", [])]
        for text, role in texts:
            n = len(out) + 1
            out.append({"id": f"{ident}-{n}", "role": role,
                        "runs": [{"id": f"{ident}-{n}-r", "text": text.replace("**", "").replace("`", ""),
                                  "emphasis": "normal"}]})
    return out


def presentation_of(deck: dict, folder: Path) -> dict:
    assets: dict[str, str] = {}

    def asset(path: str) -> str:
        if path not in assets:
            raw = (folder / path).read_bytes()
            assets[path] = f"pic-{len(assets) + 1}"
            pictures.append({"id": assets[path], "path": path, "sha256": hashlib.sha256(raw).hexdigest(),
                             "source": {"reference": "evidence"},
                             **_size(picture_proof.prove_bytes(raw))})
        return assets[path]

    pictures: list[dict] = []
    slides, placed = [], set()
    for n, s in enumerate(deck.get("slides") or [], 1):
        if "decision" in s:
            placed.add(s["decision"])
            slides.append({"id": f"place-{s['decision']}", "type": "decision", "decision": s["decision"]})
            continue
        sid = s.get("id") or f"slide-{n}"
        words = f"{sid}-words"
        objects = [{"id": words, "type": "text", "slot": "words", "source": {"reference": "evidence"},
                    "paragraphs": _paragraphs(words, s.get("blocks") or []) or _paragraphs(
                        words, [{"p": s["title"]}])}]
        picture = next((b["picture"] for b in s.get("blocks") or [] if b.get("picture")), None)
        if picture:
            layout = "picture-and-words"
            objects.append({"id": f"{sid}-picture", "type": "image", "slot": "picture",
                            "source": {"reference": "evidence"}, "asset": asset(picture),
                            "alt": "A picture", "fit": "contain"})
        else:
            layout = "opening" if n == 1 else "title-and-body"
            if layout == "title-and-body":
                objects[0]["slot"] = "body"
        slide = {"id": sid, "type": "content", "title": s["title"], "layout": layout, "objects": objects,
                 "readingOrder": [o["id"] for o in objects],
                 "narration": [{"id": f"{sid}-cue", "say": s.get("say") or s["title"], "focus": [words]}],
                 "notes": ""}
        if s.get("short"):
            slide["short"] = s["short"]
        slides.append(slide)
    decisions = []
    for d in deck.get("decisions") or []:
        d = dict(d)
        options = []
        for o in d.get("options") or []:
            o = dict(o)
            o.setdefault("id", _slug(o.get("name", "")))
            if o.pop("recommended", False):
                d["recommended"] = o["id"]
            if o.get("picture"):
                o["picture"] = asset(o["picture"])
            options.append(o)
        d["options"] = options
        d.setdefault("id", _slug(d.get("question", "")))
        for field in ("picture", "compare"):
            if d.get(field):
                d[field] = asset(d[field])
        decisions.append(d)
        if d["id"] not in placed:
            slides.append({"id": f"place-{d['id']}", "type": "decision", "decision": d["id"]})
    out = {"version": "presentation/1", "id": deck.get("name") or _slug(deck.get("title", "deck")),
           "title": deck.get("title", ""), "purpose": deck.get("purpose", ""),
           "conclusion": deck.get("conclusion", ""), "language": deck.get("language", "en"),
           "requires": ["presentation-core/1"], "assets": pictures,
           "references": [{"id": "evidence", "kind": "authored-example", "locator": "A test",
                           "revision": "1", "description": "Test evidence."}],
           "slides": slides, "decisions": decisions}
    if "language" not in deck:
        out.pop("language")
    return out


def _size(picture) -> dict:
    return {"mime": picture.mime, "width": picture.width, "height": picture.height}


def write_yaml(folder: Path, deck: dict, svg: str | None = None) -> Path:
    """`deck` as folder/presentation.yaml, with pictures/plate.svg beside it."""
    from tests.test_decision_package import SVG
    (folder / "pictures").mkdir(parents=True, exist_ok=True)
    (folder / "pictures" / "plate.svg").write_text(svg or SVG)
    source = folder / "presentation.yaml"
    source.write_text(yaml.safe_dump(presentation_of(deck, folder), sort_keys=False, allow_unicode=True))
    return source


def fitting_every_frame(folder: Path, only=None) -> dict:
    """A built deck's fit as deck_fit.measure gives it, every slide fitting
    every frame: the deck step's measure outside the page tests, which draw
    decks in Chromium themselves."""
    from jarvis import deck_fit, presentation_proof
    proof = json.loads((Path(folder) / presentation_proof.PROOF_FILE).read_text())
    ends = [list(f) for f in (deck_fit.frames()[0], deck_fit.frames()[-1])]
    slides = [{"slide": s["slide"], "id": s["id"], "fits": True, "cut": [],
               "drawn": [{"frame": f, "scale": 1.5, "reaches": 0.8,
                          "text_px": {"least": 18.0, "most": 26.0, "typical": 18.0}}
                         for f in ends]}
              for s in proof.get("slides") or [] if only is None or s["id"] in only]
    return {"version": deck_fit.VERSION, "frames": [list(f) for f in deck_fit.frames()],
            "fits": True, "slides": slides}
