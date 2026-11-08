"""The entities a question names (design section 7.17.5, the graph half of
hybrid search).

A table of every name the records give an agent, person, project or topic
(its label and its aliases; a topic's exact aliases only), folded as every
matcher folds (`spoken_words.folded_words`), and a scan of a question's
words that takes the longest name first, so "diagnostics planning" is not
also "diagnostics". It is built from the documents when first asked and
again once the store has moved on and `REBUILD_SEC` has passed, so a
question never waits on more than one build.
"""
from __future__ import annotations

import threading
import time
from typing import Optional

from jarvis import spoken_words

KINDS = ("agent", "person", "project", "topic")
REBUILD_SEC = 5.0


def _names(doc: dict) -> list[str]:
    names = [doc.get("_label") or ""]
    aliases = doc.get("aliases")
    if isinstance(aliases, dict):
        names += list(aliases.get("exact") or [])
    elif isinstance(aliases, list):
        names += [a for a in aliases if isinstance(a, str)]
    return names


class Entities:
    def __init__(self, docs) -> None:
        self.docs = docs
        self._table: dict[tuple, str] = {}
        self._longest = 0
        self._built_lsn: Optional[int] = None
        self._built_at = 0.0
        self._lock = threading.Lock()

    def _build(self) -> None:
        table: dict[tuple, str] = {}
        for doc in self.docs.find(kinds_=list(KINDS), limit=1 << 30):
            for name in _names(doc):
                words = tuple(spoken_words.folded_words(name))
                if words:
                    table.setdefault(words, doc["_key"])
        self._table = table
        self._longest = max((len(w) for w in table), default=0)
        self._built_lsn = self.docs.store.lsn
        self._built_at = time.monotonic()

    def _fresh(self) -> None:
        with self._lock:
            moved = self._built_lsn != self.docs.store.lsn
            if self._built_lsn is None or (moved and time.monotonic() - self._built_at >= REBUILD_SEC):
                self._build()

    def named_in(self, text: str) -> list[str]:
        """The entity keys `text` names, in the order it names them, each
        once."""
        self._fresh()
        words = spoken_words.folded_words(text or "")
        found: list[str] = []
        j = 0
        while j < len(words):
            for n in range(min(self._longest, len(words) - j), 0, -1):
                key = self._table.get(tuple(words[j:j + n]))
                if key:
                    if key not in found:
                        found.append(key)
                    j += n
                    break
            else:
                j += 1
        return found
