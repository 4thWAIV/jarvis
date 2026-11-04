"""JARVIS's own words, apart from their language.

A line JARVIS says about itself (a subsystem's state, why the team server
refused, why voice input fell behind) is a code and the values it names
(`Said`), and its English. The page words the code in the interface's
language from its catalog, whose English is the same, word for word and
placeholder for placeholder (each user's tests hold the two together); the
brain, the log and the stored record keep the English. A value is never
translated: a name, a figure, words another program wrote.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# A table of English by code. A dict is plural forms, chosen by `count`.
Template = str | dict[str, str]


@dataclass(frozen=True)
class Said:
    """One of JARVIS's own lines: its code and the values it names."""
    code: str
    values: dict = field(default_factory=dict)


def english(template: Template, values: dict) -> str:
    """`template` in English with `values` put in."""
    if isinstance(template, dict):
        template = template["one" if values.get("count") == 1 else "other"]
    return template.format(**values)
