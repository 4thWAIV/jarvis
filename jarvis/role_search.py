"""A role's batch search, on its own tool server (role_mcp.py).

construction-drafts.md "The author works from a prioritized working set and
calls back for more" (Tim, 2026-11-21): the author is given a working set,
not everything, and when it needs more it calls back to the system during
its turn, including a batch search that runs several searches at once.

    search_at_once   several searches of JARVIS's knowledge, each its words
                     and the path to search under, all run at the same time
                     (work_research.at_once), straight on the knowledge
                     engine with the run's own patience
                     (work_research.engine_search, words and meaning, read
                     as Tim), each hit given short: its path, source, score,
                     which index found it and the opening of the words where
                     it matched.

The whole answer is logged in the run's search log by the server
(role_mcp.call), so the paths of its hits are there as any search's.
"""
from __future__ import annotations

import json
from typing import Callable, Optional

NAME = "search_at_once"
NAMES: tuple[str, ...] = (NAME,)
# The most searches one call runs, and the words of a hit it gives (the
# rest is read with read_package_file or knowledge_search).
MOST = 12
HIT_CHARS = 280

SPECS: list[dict] = [
    {"name": NAME,
     "description": ("Run several searches of JARVIS's knowledge at once, words and meaning, "
                     f"at most {MOST} in one call. Each search is its words and the path to "
                     "search under (the package's evidence, a Work item's folder), or no path "
                     "to search everywhere. Each hit comes back short: its path, source, score, "
                     "which index found it and the opening of the words where it matched; read "
                     "more with read_package_file or knowledge_search."),
     "inputSchema": {"type": "object", "properties": {
         "searches": {"type": "array", "minItems": 1, "maxItems": MOST, "items": {
             "type": "object", "properties": {
                 "words": {"type": "string", "description": "What to search for."},
                 "under": {"type": "string",
                           "description": "The path in JARVIS's knowledge to search under, "
                                          "or empty to search everywhere."}},
             "required": ["words"], "additionalProperties": False}}},
         "required": ["searches"], "additionalProperties": False}},
]


def _patience() -> float:
    """A search is waited for as the run's own role calls are: Settings'
    run_timeout_sec (run_executor.bounds)."""
    from jarvis import run_executor
    return run_executor.bounds()[0]


def _engine():
    from jarvis import work_research
    return work_research.engine_search(_patience())


# How the searches reach the engine (tests give their own). work_research
# is imported only when a search runs: the tool server starts light.
SEARCH: Callable[[], Callable] = _engine


def short(hit: dict) -> dict:
    """A hit as the batch search gives it."""
    words = hit.get("text")
    words = words if isinstance(words, str) else json.dumps(words, ensure_ascii=False,
                                                            default=str)
    out = {"path": hit.get("path"), "source": hit.get("source"), "score": hit.get("score"),
           "why": hit.get("why"), "text": " ".join(words.split())[:HIT_CHARS]}
    return {k: v for k, v in out.items() if v not in (None, "", [])}


def run(searches: list[dict], search: Optional[Callable] = None) -> list[dict]:
    """Every search at once, each with its short hits, in the order given."""
    from jarvis import work_research
    wanted = [(str(s.get("words") or "").strip(), (str(s.get("under") or "").strip() or None))
              for s in searches]
    wanted = [(w, u) for w, u in wanted if w]
    if not wanted:
        raise ValueError("Give at least one search with its words.")
    if len(wanted) > MOST:
        raise ValueError(f"At most {MOST} searches in one call; you gave {len(wanted)}.")
    search = search or SEARCH()
    looked = work_research.at_once([
        (lambda w=w, u=u: [work_research._search(search, NAME, w, u)]) for w, u in wanted])
    out = []
    for q in looked:
        entry = {"words": q["query"], "under": q.get("under"), "seconds": q.get("seconds"),
                 "hits": [short(h) for h in q["hits"]]}
        if q.get("error"):
            entry["error"] = q["error"]
        if q.get("partial"):
            entry["partial"] = True
        out.append({k: v for k, v in entry.items() if v is not None})
    return out


def call(arguments: dict) -> tuple[bool, str]:
    """The batch search's answer, as JSON text, or why it was refused."""
    try:
        found = run(list(arguments.get("searches") or []))
    except ValueError as e:
        return False, str(e)
    return True, json.dumps({"searches": found}, ensure_ascii=False, default=str)
