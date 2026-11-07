"""Seeds: topics JARVIS offers to create, never creates on its own (design
section 9, invariant I9).

A seed is a proposal shown once (an attention item and the Knowledge view's
Suggested topics) listing exactly what would be created. Nothing is written
without his yes; he answers on the page or by voice through the same calls
(`seeds`, `answer`). A declined seed is written in his settings note and
never offered again; an accepted one creates its topic files through
knowledge_links and never overwrites a topic that already exists.

The Denice seed reads this repository's own README and roadmap and quotes
them, never paraphrasing: `topics/denice.md` (status planned, exact alias
"Denice", ambiguous alias "Denise", since the recogniser hears it that way
and it is also a person's name), and one topic per stage in the roadmap's
table, each `stage_of: denice` and `precedes` the next, its body the
roadmap's line for it with the file as its source. Only the stage the
roadmap calls the current prototype is `implemented`; the rest are
`planned`. Nothing personal goes into the seed: only those quotations.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from jarvis import data_paths, knowledge_links

ROADMAP = Path("docs") / "roadmap.md"
README = Path("README.md")
ROADMAP_SECTION = "From the prototype to Denice"
README_SECTION = "The path to Denice"
_ROW = re.compile(r"\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|")


class SeedError(ValueError):
    pass


def _read(rel: Path) -> Optional[str]:
    path = data_paths.SOURCE_ROOT / rel
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def _section(text: str, heading: str) -> str:
    lines = text.splitlines()
    start = next((i for i, l in enumerate(lines) if l.lstrip("#").strip() == heading and l.startswith("#")), None)
    if start is None:
        return ""
    level = len(lines[start]) - len(lines[start].lstrip("#"))
    out = []
    for line in lines[start + 1:]:
        if line.startswith("#") and len(line) - len(line.lstrip("#")) <= level:
            break
        out.append(line)
    return "\n".join(out)


def _sentence_with(text: str, words: str) -> Optional[str]:
    for sentence in re.split(r"(?<=[.!?])\s+", " ".join(text.split())):
        if words in sentence:
            return sentence.removeprefix("**Future:** ").strip()
    return None


def _stage_slug(name: str) -> str:
    from jarvis import jarvis_memory
    return jarvis_memory.slugify(name)


def denice() -> Optional[dict]:
    """What the Denice seed would create, or None when this copy of JARVIS
    has no README and roadmap to quote."""
    roadmap, readme = _read(ROADMAP), _read(README)
    if not roadmap or not readme:
        return None
    road = _section(roadmap, ROADMAP_SECTION)
    read = _section(readme, README_SECTION)
    quotes = []
    for text, rel, section in ((read, README, README_SECTION), (road, ROADMAP, ROADMAP_SECTION)):
        s = _sentence_with(text, "Denice, pronounced Denise")
        if s:
            quotes.append(f'> "{s}" ({rel.as_posix()}, "{section}")')
    stages = []
    for line in road.splitlines():
        m = _ROW.match(line.strip())
        if not m or m.group(1) in ("Engine",) or set(m.group(1)) <= {"-", " "}:
            continue
        name, role = m.group(1), m.group(2)
        stages.append((name, role))
    if not quotes or not stages:
        return None
    topics = [{"slug": "denice", "name": "Denice", "status": "planned", "pinned": True,
               "exact": ["Denice"], "ambiguous": ["Denise"], "links": {},
               "body": "# Denice\n\n" + "\n\n".join(quotes) + "\n"}]
    slugs = [_stage_slug(name) for name, _ in stages]
    for i, (name, role) in enumerate(stages):
        short = name.removesuffix(" Engine")
        current = "current" in role.lower() and "prototype" in role.lower()
        links = {"stage_of": ["denice"]}
        if i + 1 < len(stages):
            links["precedes"] = [slugs[i + 1]]
        topics.append({"slug": slugs[i], "name": name, "status": "implemented" if current else "planned",
                       "pinned": False, "exact": [name], "ambiguous": [] if current else [short],
                       "links": links,
                       "body": f'# {name}\n\n> "{role}" ({ROADMAP.as_posix()}, "{ROADMAP_SECTION}")\n'})
    return {"id": "denice", "title": "Denice and its stages", "topics": topics}


SEEDS = {"denice": denice}


def status_of(seed: dict) -> str:
    if seed["id"] in knowledge_links.declined_seeds():
        return "declined"
    if all(knowledge_links.read(t["slug"]) is not None for t in seed["topics"]):
        return "accepted"
    return "open"


def seeds() -> list[dict]:
    out = []
    for seed_id, build in SEEDS.items():
        seed = build()
        if seed is None:
            continue
        out.append({**seed, "status": status_of(seed),
                    "exists": [t["slug"] for t in seed["topics"] if knowledge_links.read(t["slug"]) is not None]})
    return out


def open_seeds() -> list[dict]:
    return [s for s in seeds() if s["status"] == "open"]


def answer(seed_id: str, action: str) -> dict:
    """accept or decline one seed, on his word."""
    build = SEEDS.get(seed_id)
    seed = build() if build else None
    if seed is None:
        raise SeedError("there is no such suggestion")
    state = status_of(seed)
    if state != "open":
        raise SeedError(f"that suggestion is already {state}")
    if action == "decline":
        knowledge_links.decline_seed(seed_id)
        return {"done": "decline", "seed": seed_id, "created": []}
    if action != "accept":
        raise SeedError("the answer is accept or decline")
    created = []
    for t in seed["topics"]:
        if knowledge_links.read(t["slug"]) is not None:
            continue                       # his own topic stays as he wrote it
        knowledge_links.create_topic(t["name"], status=t["status"], exact=t["exact"],
                                     ambiguous=t["ambiguous"], pinned=t["pinned"], body=t["body"])
        created.append(t["slug"])
    for t in seed["topics"]:
        if t["slug"] not in created:
            continue
        for kind, targets in t["links"].items():
            for target in targets:
                if knowledge_links.read(target) is not None:
                    knowledge_links.link(t["slug"], kind, target)
    return {"done": "accept", "seed": seed_id, "created": created}
