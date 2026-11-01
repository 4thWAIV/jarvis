"""Words as they are spoken, in each language JARVIS hears.

This branch (spanish-speaking) carries only the part of this module that
Spanish build step 2 (spoken-words) also defines: the language codes and
`active_language()`, the one place the language in force is decided. When
the branches meet, step 2's file is the whole of it; nothing here differs
from it.
"""

from __future__ import annotations

ENGLISH = "en"
SPANISH = "es"


def active_language() -> str:
    """The language the user speaks to JARVIS in: the one place it is decided.

    English until the language setting exists; that setting answers here and
    nowhere else."""
    return ENGLISH
