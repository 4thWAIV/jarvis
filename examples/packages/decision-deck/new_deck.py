#!/usr/bin/env python3
"""Build a decision package from one data file, and zip it.

    python3 examples/packages/decision-deck/new_deck.py DECK.json OUT_FOLDER [OUT.zip]

DECK.json is the deck: its package fields, slides and decisions (the format
is in docs/making-a-package.md, whose command, python -m jarvis.make_package,
is the one way a deck is made and sent; this folder's deck-data.js is an
example, and may be given in its place). A deck with no decisions is a
presentation. OUT_FOLDER gets the shared
deck's own files (index.html, deck.css, deck.js, SKILL.md,
scripts/decide.py) copied from this folder, then deck-data.js, story.json
and package.json made from the data, and every picture the data names,
copied from beside DECK.json. The zip defaults to <name>-<version>.zip
beside OUT_FOLDER. Runs on any python3, without JARVIS.
"""
from __future__ import annotations

import json
import re
import shutil
import sys
from pathlib import Path

TEMPLATE = Path(__file__).resolve().parent
sys.path.insert(0, str(TEMPLATE.parent))
import zip_package  # noqa: E402  (examples/packages/zip_package.py)

FIXED = ["index.html", "deck.css", "deck.js", "SKILL.md", "scripts/decide.py"]
# A presentation (a deck with no decisions) has nothing to answer, so it
# carries neither the answering skill nor decide.py, and returns nothing.
VOTING = ("SKILL.md", "scripts/decide.py")
DATA_PREFIX = "window.DECK = "
SLUG = re.compile(r"^[a-z0-9][a-z0-9-]*$")
RETURNS = ["results/decisions.json", "results/decisions.md"]
SAY_MAX = 800
# Words a slide may carry, so no slide is a wall of text: a content slide's
# body, and each part of a decision. Split a longer slide in two.
SLIDE_WORDS = 60
DECISION_WORDS = {"question": 30, "reason": 40, "context": 40, "says": 40}


class Bad(Exception):
    pass


def read_source(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".js":
        start = text.index(DATA_PREFIX) + len(DATA_PREFIX)
        return json.JSONDecoder().raw_decode(text, start)[0]
    return json.loads(text)


def pictures_of(deck: dict) -> list[str]:
    """Every picture path the data names."""
    found = []

    def take(pic):
        if pic:
            found.append(pic if isinstance(pic, str) else pic.get("src", ""))

    for s in deck.get("slides", []):
        for b in s.get("blocks", []):
            take(b.get("picture"))
            for p in b.get("pictures", []):
                take(p)
    for d in deck.get("decisions", []):
        take(d.get("picture"))
        take(d.get("compare"))
        for o in d.get("options", []):
            take(o.get("picture"))
    return found


def words(text) -> int:
    return len(re.sub(r"\*\*|`", "", str(text or "")).split())


def block_words(b: dict) -> int:
    n = sum(words(b.get(k)) for k in ("p", "lead", "note", "caption"))
    for pt in b.get("points", []):
        n += words(pt) if isinstance(pt, str) else words(pt.get("b")) + words(pt.get("text"))
    for f in b.get("facts", []):
        n += words(f.get("n")) + words(f.get("text"))
    for pic in b.get("pictures", []):
        n += words(pic.get("caption"))
    table = b.get("table") or {}
    for row in [table.get("head") or []] + list(table.get("rows") or []):
        n += sum(words(c) for c in row)
    return n


def check_words(deck: dict) -> None:
    for s in deck.get("slides", []):
        if "decision" in s:
            continue
        n = sum(block_words(b) for b in s.get("blocks", []))
        if n > SLIDE_WORDS:
            raise Bad(f"Slide {s.get('title')!r} has {n} words; at most {SLIDE_WORDS}. Split it in two.")
    for d in deck.get("decisions", []):
        parts = [("question", d.get("question")), ("reason", d.get("reason")), ("context", d.get("context"))]
        parts += [("says", o.get("says")) for o in d["options"]]
        for field, text in parts:
            if words(text) > DECISION_WORDS[field]:
                raise Bad(f"{d['id']}'s {field} has {words(text)} words; at most {DECISION_WORDS[field]}.")


def check(deck: dict, source_dir: Path) -> None:
    for field in ("name", "version", "purpose", "title"):
        if not str(deck.get(field) or "").strip():
            raise Bad(f"The deck has no {field}.")
    decisions = deck.get("decisions") or []
    if not decisions and not [s for s in deck.get("slides", []) if "decision" not in s]:
        raise Bad("The deck has neither decisions nor slides.")
    ids = [d.get("id", "") for d in decisions]
    for d in decisions:
        ident = d.get("id", "")
        if not SLUG.match(ident) or ids.count(ident) > 1:
            raise Bad(f"Decision id {ident!r} is not a unique slug (a-z, 0-9 and -).")
        for field in ("question", "reason", "recommended"):
            if not str(d.get(field) or "").strip():
                raise Bad(f"{ident} has no {field}.")
        options = d.get("options") or []
        if len(options) < 2:
            raise Bad(f"{ident} needs at least two options.")
        oids = [o.get("id", "") for o in options]
        for o in options:
            if not SLUG.match(o.get("id", "")) or oids.count(o["id"]) > 1 or o["id"] in ("later", "none", "recommended"):
                raise Bad(f"{ident}'s option id {o.get('id')!r} is not a unique slug, or is a reserved word.")
            if not o.get("name") or not o.get("says"):
                raise Bad(f"{ident}'s option {o['id']} needs a name and says.")
            for child in o.get("opens", []):
                if child not in ids or child == ident:
                    raise Bad(f"{ident}'s option {o['id']} opens {child!r}, which is not another decision.")
        if d["recommended"] not in oids:
            raise Bad(f"{ident} recommends {d['recommended']!r}, which is not one of its options.")
    parents: dict = {}
    for d in decisions:
        for o in d["options"]:
            for child in o.get("opens", []):
                if parents.setdefault(child, d["id"]) != d["id"]:
                    raise Bad(f"{child} is opened by two decisions; a child has one parent.")
    for start in parents:
        seen, at = set(), start
        while at in parents:
            if at in seen:
                raise Bad(f"The decision tree loops through {at}.")
            seen.add(at)
            at = parents[at]
    for s in deck.get("slides", []):
        if "decision" in s and s["decision"] not in ids:
            raise Bad(f"A slide places decision {s['decision']!r}, which is not one.")
        if "decision" not in s and not s.get("title"):
            raise Bad("Every content slide needs a title.")
    check_words(deck)
    for pic in pictures_of(deck):
        p = Path(pic)
        if not pic or p.is_absolute() or ".." in p.parts:
            raise Bad(f"Picture {pic!r} must be a path inside the package.")
        if not (source_dir / p).is_file():
            raise Bad(f"Picture {pic} is not beside the data, at {source_dir / p}.")


def spoken(text: str) -> str:
    """A line of the deck's text as words to say: no ** or ` marks."""
    return re.sub(r"\*\*|`", "", str(text or "")).strip()


def order(deck: dict) -> list[dict]:
    """The slides in the order deck.js draws them: content and placed
    decisions, then every decision not placed, then the answers slide."""
    placed = {s["decision"] for s in deck.get("slides", []) if "decision" in s}
    out = list(deck.get("slides", []))
    out += [{"decision": d["id"]} for d in deck.get("decisions", []) if d["id"] not in placed]
    return out + ([{"answers": True}] if deck.get("decisions") else [])


# What JARVIS asks at the end of every decision slide, before it waits.
ASK = {"en": "Which do you choose? Press Choose on one, or tell me.",
       "es": "¿Cuál elige? Pulse el botón de una, o dígamelo."}


def parents(deck: dict) -> dict:
    """{child decision: {"question": parent, "answers": [options that open it]}}."""
    out: dict = {}
    for d in deck.get("decisions", []):
        for o in d["options"]:
            for child in o.get("opens", []):
                out.setdefault(child, {"question": d["id"], "answers": []})["answers"].append(o["id"])
    return out


def story(deck: dict) -> dict:
    """The story JARVIS tells over the deck: one decision at a time. Each
    decision slide is explained, then asks, then waits (`waits`) until
    results/decisions.json has its answer; a child question is asked only
    once its parent has an answer that opens it (`asks_when`). A
    presentation (no decisions) is told straight through."""
    decisions = deck.get("decisions", [])
    by_id = {d["id"]: d for d in decisions}
    opened = parents(deck)
    ask = ASK.get(deck.get("language", "en"), ASK["en"])
    slides = []
    for n, s in enumerate(order(deck), 1):
        go = {"click": f"#go-{n}"}
        if "decision" in s:
            d = by_id[s["decision"]]
            k = decisions.index(d) + 1
            rec = next(o for o in d["options"] if o["id"] == d["recommended"])
            beats = [
                {"say": f"Decision {k} of {len(decisions)}: {spoken(d['question'])}",
                 "actions": [go], "targets": [f"#d-{d['id']} .question"]},
                {"say": spoken(d.get("say") or f"I recommend {rec['name']}. {d['reason']}"),
                 "targets": [f"#opt-{d['id']}-{rec['id']}"]},
                {"say": ask, "targets": [f"#d-{d['id']} .options"]},
            ]
            slide = {"title": d.get("short") or d["id"], "target": f"#d-{d['id']}",
                     "waits": d["id"], "beats": beats}
            if d["id"] in opened:
                slide["asks_when"] = opened[d["id"]]
            slides.append(slide)
        elif "answers" in s:
            slides.append({"title": "Your answers", "target": "#answers", "beats": [
                {"say": "Your answers are recorded here. You can change any of them, "
                        "or approve every recommended option at once.",
                 "actions": [go], "targets": ["#answer-list"]}]})
        else:
            sid = s.get("id") or f"s{n}"
            if s.get("beats"):
                beats = [dict(b) for b in s["beats"]]
                beats[0]["actions"] = [go] + list(beats[0].get("actions", []))
            else:
                beats = [{"say": spoken(s.get("say") or s["title"]), "actions": [go],
                          "targets": [f"#{sid} h2"]}]
            slide = {"title": s.get("short") or s["title"], "target": f"#{sid}", "beats": beats}
            # A decision's lead-in is told only when its decision is asked.
            if s.get("lead_in") in opened:
                slide["asks_when"] = opened[s["lead_in"]]
            slides.append(slide)
    for slide in slides:
        for b in slide["beats"]:
            if len(b["say"]) > SAY_MAX:
                raise Bad(f"A beat on {slide['title']} is over {SAY_MAX} characters.")
    return {"title": deck["title"], "language": deck.get("language", "en"),
            "answers": RETURNS[0], "slides": slides}


def manifest(deck: dict) -> dict:
    voting = {"skill": "SKILL.md", "returns": RETURNS} if deck.get("decisions") else {"returns": []}
    return {"name": deck["name"], "version": deck["version"], "purpose": deck["purpose"],
            "runs": "page", "entry": "index.html", "story": "story.json", **voting}


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def build(source: Path, out: Path) -> Path:
    return build_deck(read_source(source), source.parent, out)


def build_deck(deck: dict, source_dir: Path, out: Path) -> Path:
    """Build `deck` (already read, pictures beside `source_dir`) into `out`."""
    source = source_dir / "deck.json"
    check(deck, source_dir)
    out.mkdir(parents=True, exist_ok=True)
    for name in [f for f in FIXED if deck.get("decisions") or f not in VOTING]:
        if (TEMPLATE / name).resolve() != (out / name).resolve():
            (out / name).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(TEMPLATE / name, out / name)
    for pic in pictures_of(deck):
        if (source.parent / pic).resolve() != (out / pic).resolve():
            (out / pic).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source.parent / pic, out / pic)
    data = json.dumps(deck, indent=1, ensure_ascii=False)
    write(out / "deck-data.js", "// The deck's slides and decisions, read by deck.js and scripts/decide.py.\n"
                                "// Made by examples/packages/decision-deck/new_deck.py. To change the deck,\n"
                                "// edit this data and run new_deck.py on this file again.\n"
                                + DATA_PREFIX + data + ";\n")
    write(out / "story.json", json.dumps(story(deck), indent=1, ensure_ascii=False) + "\n")
    write(out / "package.json", json.dumps(manifest(deck), indent=2, ensure_ascii=False) + "\n")
    return out


def main(argv: list[str]) -> int:
    if len(argv) not in (2, 3):
        print(__doc__.strip().splitlines()[2])
        return 2
    source, out = Path(argv[0]).resolve(), Path(argv[1]).resolve()
    try:
        build(source, out)
    except (Bad, KeyError, ValueError, OSError) as e:
        print(f"Not built: {e}")
        return 1
    deck = read_source(source)
    target = Path(argv[2]) if len(argv) == 3 else out.parent / f"{deck['name']}-{deck['version']}.zip"
    return zip_package.main([str(out), str(target)])


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
