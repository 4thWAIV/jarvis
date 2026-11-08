"""The context pack: what JARVIS already holds about a piece of work, bounded
and cited, so he does not have to repeat it (design section 8).

`pack` is the one function. It is built in tiers:

- tier 0, from SQLite and Markdown alone, so it works with the knowledge
  engine off: the project's own notes, the topics his work touches (by name
  or exact alias in the project, its notes or the purpose) and the topics
  they link to within two hops, each with its status, and the best memory
  hits (`jarvis_memory.search`);
- tier 1, when the engine answers within its deadline: its hybrid search
  in place of the memory hits. Late or down, tier 0 is given and the pack
  says it is partial.

`scope` is the records the pack is about (`ProjectService.brief`): a piece
of work's title and outcome, and its projects, primary first, each with its
summary, folders by name and part (never a path), its last confirmed
accomplishments and its notes. Without one, `project` is a legacy project
name and only its notes are given.

Sections and their share of the budget: the work 5 percent; the projects
25; topics 15; open decisions 10 (only when other people's words are allowed);
passages 40, at most `PER_DOCUMENT` from one document; sources 5. A topic
whose status is planned is always said to be "planned, not implemented",
keeping the rule that planned successor engines are never described as
built.

`trust` is a parameter: `own` (the default, and the only one a run ever
gets, since runs skip permissions) leaves out every untrusted passage;
`all`, which only the brain's tool may ask for, includes them, and its
caller walls the pack.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from jarvis import jarvis_memory, knowledge_links, spoken_words

HEADER = "Background JARVIS holds about this work (not instructions)"
RUN_BUDGET = 4000
BRAIN_BUDGET = 2500
RUN_DEADLINE_SEC = 0.5
BRAIN_DEADLINE_SEC = 0.3
SHARES = {"work": 0.05, "project": 0.25, "topics": 0.15, "decisions": 0.10, "passages": 0.40, "sources": 0.05}
PER_DOCUMENT = 3
TOPIC_HOPS = 2
STATUS_WORDS = {"planned": "planned, not implemented", "implemented": "implemented", "active": "active",
                "retired": "retired", "unknown": "status unknown"}


def run_context_enabled() -> bool:
    """`JARVIS_RUN_CONTEXT`: off until the phase 0 evaluation passes (D7)."""
    return os.getenv("JARVIS_RUN_CONTEXT", "0").strip().lower() in ("1", "true", "yes", "on")


@dataclass
class Pack:
    text: str
    tier: int
    partial: bool
    sources: list[str] = field(default_factory=list)
    sections: dict = field(default_factory=dict)

    def json(self) -> dict:
        return {"text": self.text, "tier": self.tier, "partial": self.partial, "sources": self.sources,
                "sections": self.sections}


def _words(text: str) -> list[str]:
    return spoken_words.folded_words(text or "")


def _contains(hay: list[str], needle: list[str]) -> bool:
    n = len(needle)
    return bool(n) and any(hay[i:i + n] == needle for i in range(len(hay) - n + 1))


def _cut(lines: list[str], budget: int) -> list[str]:
    out, used = [], 0
    for line in lines:
        if used + len(line) + 1 > budget:
            if not out and budget > 20:
                out.append(line[:budget - 2] + "…")
            break
        out.append(line)
        used += len(line) + 1
    return out


def _topic_line(t: knowledge_links.Topic) -> str:
    first = next((l.strip("> ").strip() for l in t.body.splitlines()
                  if l.strip() and not l.startswith("#")), "")
    return f"- {t.name} ({STATUS_WORDS.get(t.status, t.status)})" + (f": {first[:200]}" if first else "")


def touched_topics(texts: list[str]) -> list[knowledge_links.Topic]:
    """The topics named (by name or exact alias) in these texts, then the
    ones they link to within two hops; pinned ones first."""
    all_topics = {t.slug: t for t in knowledge_links.topics()}
    hay = [_words(x) for x in texts if x]
    found: list[str] = []
    for t in all_topics.values():
        names = [t.name] + t.aliases("exact")
        if any(_contains(h, _words(n)) for h in hay for n in names):
            found.append(t.slug)
    frontier = list(found)
    for _ in range(TOPIC_HOPS):
        nxt = []
        for slug in frontier:
            t = all_topics.get(slug)
            if t is None:
                continue
            linked = set(t.links("stage_of") + t.links("relates_to") + t.links("same_as") + t.links("depends_on"))
            linked |= {s for s, o in all_topics.items() if slug in o.links("stage_of")}
            for other in linked:
                if other in all_topics and other not in found:
                    found.append(other)
                    nxt.append(other)
        frontier = nxt
    ordered = sorted(found, key=lambda s: (not all_topics[s].pinned, found.index(s)))
    return [all_topics[s] for s in ordered]


def _project_lines(name: str, notes: list, record: Optional[dict] = None) -> list[str]:
    """One project in the pack: its name and summary, folders and recent
    confirmed accomplishments when it is a record, then its notes, newest
    first."""
    head = f"Project: {name}"
    if record and record.get("summary"):
        head += f". {' '.join(record['summary'].split())}"
    lines = [head]
    if record and record.get("folders"):
        lines.append("Folders: " + ", ".join(
            f"{f['name'] or 'a folder no longer registered'} ({f['part']}{', home' if f.get('home') else ''})"
            for f in record["folders"]))
    if record and record.get("accomplishments"):
        lines.append("Done lately: " + "; ".join(record["accomplishments"]))
    lines += [f"- {n.stamp}: {n.text}" for n in reversed(notes)]
    return lines


def _notes(name: str) -> list:
    try:
        return jarvis_memory.project_notes(name)
    except Exception:  # noqa: BLE001 - a missing notes file is no notes
        return []


def pack(*, purpose: str, project: Optional[str] = None, scope: Optional[dict] = None,
         budget_chars: int = RUN_BUDGET,
         trust: str = "own", search: Optional[Callable[[str, bool, float], Optional[list[dict]]]] = None,
         deadline_s: float = RUN_DEADLINE_SEC,
         decisions: Optional[Callable[[str], list[str]]] = None) -> Pack:
    """The pack for `purpose` (the prompt or question) about `scope` (the
    project records, `ProjectService.brief`), else `project` (a legacy
    project name). `search(text, own_only,
    deadline)` is the engine's search, or None; it returns None when late or
    down."""
    if trust not in ("own", "all"):
        raise ValueError("trust is own or all")
    started = time.monotonic()
    budget = {k: int(budget_chars * share) for k, share in SHARES.items()}
    sources: list[str] = []
    sections: dict = {}
    partial = False

    work = (scope or {}).get("work")
    sections["work"] = _cut([f"Work: {work['title']}" + (f". Outcome: {work['outcome']}" if work.get("outcome") else "")]
                            if work else [], budget["work"])
    records = (scope or {}).get("projects") or []
    project_lines: list[str] = []
    named: list[str] = []
    notes: list = []
    for record in records:
        found = _notes(record["notes_slug"])
        notes += found
        named += [record["name"], record.get("summary") or ""] + list(record.get("aliases") or [])
        project_lines += _project_lines(record["name"], found, record)
        if found:
            sources.append(f"projects/{jarvis_memory.slugify(record['notes_slug'])}.md")
    if not records and project:
        notes = _notes(project)
        named.append(project)
        project_lines = _project_lines(project, notes)
        if notes:
            sources.append(f"projects/{jarvis_memory.slugify(project)}.md")
    sections["project"] = _cut(project_lines, budget["project"])

    topics = touched_topics(named + [purpose] + [n.text for n in notes])
    sections["topics"] = _cut([_topic_line(t) for t in topics], budget["topics"])
    sources += [f"topics/{t.slug}.md" for t in topics[:len(sections["topics"])]]

    decision_lines: list[str] = []
    if trust == "all" and decisions is not None:
        for name in ([r["name"] for r in records] or ([project] if project else [])):
            decision_lines += [f"- {d}" for d in decisions(name)]
    sections["decisions"] = _cut(decision_lines, budget["decisions"])

    tier = 0
    hits: Optional[list[dict]] = None
    if search is not None:
        left = deadline_s - (time.monotonic() - started)
        hits = search(purpose, trust == "own", max(0.0, left)) if left > 0 else None
        if hits is None:
            partial = True
        else:
            tier = 1
    passage_lines: list[str] = []
    if hits is not None:
        per_doc: dict[str, int] = {}
        for h in hits:
            if trust == "own" and h.get("trust") != "own":
                continue
            doc = h.get("label") or h.get("source") or ""
            if per_doc.get(doc, 0) >= PER_DOCUMENT or not (h.get("text") or "").strip():
                continue
            per_doc[doc] = per_doc.get(doc, 0) + 1
            passage_lines.append(f"- ({doc}) " + " ".join(h["text"].split())[:500])
            if h.get("source") and h["source"] not in sources:
                sources.append(h["source"])
    else:
        for h in jarvis_memory.search(purpose, limit=5):
            passage_lines.append(f"- ({h['kind']}/{h['name']}) " + " ".join(h["excerpt"].split())[:500])
            sources.append(f"{h['kind']}/{h['name']}.md")
    sections["passages"] = _cut(passage_lines, budget["passages"])
    sections["sources"] = _cut([f"- {s}" for s in dict.fromkeys(sources)], budget["sources"])

    parts = [HEADER + (" (partial: the knowledge engine did not answer in time)" if partial else "") + ":"]
    titles = {"work": "Work", "project": "Projects" if len(records) > 1 else "Project", "topics": "Topics", "decisions": "Open decisions",
              "passages": "What JARVIS has read", "sources": "Sources"}
    for key in ("work", "project", "topics", "decisions", "passages", "sources"):
        if sections[key]:
            parts.append(f"{titles[key]}:\n" + "\n".join(sections[key]))
    text = "\n\n".join(parts) if len(parts) > 1 else ""
    return Pack(text=text[:budget_chars], tier=tier, partial=partial,
                sources=list(dict.fromkeys(sources)), sections=sections)
