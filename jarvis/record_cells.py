"""Small helpers the Work, workflow and package-inbox documents share
(round 3, records-as-documents): a JSON cell of an old table kept exactly
while it says the same, the events a version records, and the one place a
unit's first read of a document starts that version's events afresh.

A column that held JSON text (a piece's `evidence`, an event's `detail`)
is a value in the document. The engine gives an object's keys back sorted,
so the text a value is written as again may differ from the text the table
held: the original text is kept beside it (`kept`) only while it still
parses to the value, so the move back gives every row exactly as it was,
and a value changed since is written as it is now.
"""
from __future__ import annotations

import json
import weakref
from typing import Any, Callable, Optional

EVENTS = "events"
KEPT = "kept"


def sorted_text(value: Any) -> str:
    """How a value read back from the engine is written as JSON again."""
    return json.dumps(_sorted(value))


def _sorted(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _sorted(value[k]) for k in sorted(value)}
    if isinstance(value, list):
        return [_sorted(v) for v in value]
    return value


def keep(holder: dict, name: str, text: str, value: Any,
         dump: Callable[[Any], str] = json.dumps) -> None:
    """Remember `text`, the cell `value` was read from, on `holder` when the
    value read back from the engine would not be written as that text."""
    if dump(_sorted(value)) != text:
        holder.setdefault(KEPT, {})[name] = text


def text_of(holder: dict, name: str, value: Any, parse: Callable[[str], Any] = json.loads,
            dump: Callable[[Any], str] = json.dumps) -> str:
    """The cell for `value`: the text it was moved from while that text
    still says it, else the value as JSON."""
    kept = (holder.get(KEPT) or {}).get(name)
    if isinstance(kept, str):
        try:
            if parse(kept) == value:
                return kept
        except ValueError:
            # Moved text that was never JSON stands as an empty value
            # (`loads`) until a writer gives the cell a value of its own.
            if value in (None, {}, []):
                return kept
    return dump(value)


def loads(text: Any, empty: Any = None) -> Any:
    """A JSON cell's value; text that is not JSON (a row written outside
    the store) is `empty` (None, {} or []), its text kept by `keep` so it
    moves back as it was."""
    if not isinstance(text, str):
        return text
    try:
        return json.loads(text)
    except ValueError:
        return empty


# ── one version's events ─────────────────────────────────────────────────

_taken: "weakref.WeakKeyDictionary[Any, set]" = weakref.WeakKeyDictionary()


def take(unit, path: str) -> Optional[dict]:
    """The document at `path` as `unit` holds it (staged writes seen), to
    change: its data, with `events` empty the first time this unit takes it
    (a new version records only its own events) and as staged after that.
    None when there is none."""
    rec = unit.get(path)
    if rec is None:
        return None
    seen = _taken.setdefault(unit, set())
    if path not in seen:
        seen.add(path)
        rec.data[EVENTS] = []
    return rec.data


def stage(unit, path: str, data: dict, change: Optional[str] = None,
          summary: Optional[str] = None) -> None:
    """Stage the document, its version tagged with its first event's kind
    and summary (the change it records), else `change` and `summary`."""
    events = data.get(EVENTS) or []
    if events:
        change, summary = events[0].get("kind") or change, events[0].get("summary") or summary
    _taken.setdefault(unit, set()).add(path)
    unit.put(path, data, change=change, summary=None if summary is None else str(summary)[:2000])


def next_seq(data: dict) -> int:
    """The next event's number in this document (its own order; moved
    events keep the numbers the table gave them)."""
    data["last_seq"] = int(data.get("last_seq") or 0) + 1
    return data["last_seq"]


def resolve_seqs(events: list[dict], key: str = "seq") -> None:
    """Every event a unique number, for rows again: the first of each number
    (oldest `at`) keeps it; one written since the move under a number
    another record's event has (each record numbers its own) gets the next
    number after the largest, oldest first."""
    order = sorted(range(len(events)), key=lambda i: (events[i].get(key) is None,
                                                      events[i].get(key) or 0,
                                                      events[i].get("at") or 0.0, i))
    seen: set = set()
    again: list[int] = []
    for i in order:
        n = events[i].get(key)
        if n is None or n in seen:
            again.append(i)
        else:
            seen.add(n)
    top = max(seen, default=0)
    for i in sorted(again, key=lambda i: (events[i].get("at") or 0.0, i)):
        top += 1
        events[i][key] = top


def touched(unit, under: str, of_type: str) -> set:
    """The paths of type `of_type` under `under` this unit has taken or
    staged through `take` and `stage`."""
    return {p for p in _taken.get(unit, ()) if p.startswith(under.rstrip("/") + "/")
            and p.endswith("." + of_type)}


# ── the move (knowledge_move) ────────────────────────────────────────────

def table_rows(conn, table: str) -> list:
    """Every row of `table` in a SQLite file being moved, as the move kit
    reads it (its cells by column, its rowid when kept apart); [] when the
    file has no such table."""
    from jarvis import knowledge_move
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                        (table,)).fetchone():
        return []
    return list(knowledge_move.read_rows(conn, knowledge_move.table_info(conn, table)))


def history_events(doc) -> list[dict]:
    """Every event a moved-back document (`knowledge_move.Doc`) records:
    each earlier version's, oldest first, then its current version's."""
    found: list[dict] = []
    for data in [r.data for r in doc.history] + [doc.data]:
        found.extend(dict(e) for e in (data or {}).get(EVENTS) or [])
    return found
