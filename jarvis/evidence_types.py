"""
evidence_types.py — the typed evidence entries of a package's handoff, and
the fields each one names (workflow.yaml request entry_types, approved by
Tim 2026-11-19: "Great").

Every type but `label` is resolved by a program; a `label` (free text) is
the one a model resolves (RetrievalSession), into entries of the types it
can name (`NAMED_BY_LABEL`).
"""
from __future__ import annotations

FIELDS: dict[str, tuple[str, ...]] = {
    "file": ("path",),
    "url": ("url",),
    "content": ("text",),
    "work_item": ("id",),
    "package": ("id",),
    "message": ("id",),
    "workflow": ("id",),
    "run": ("id",),
    "pr": ("repo", "number"),
    "ci_run": ("repo", "id"),
    "commit": ("repo", "sha"),
    "issue": ("repo", "number"),
    "branch": ("repo", "name"),
    "label": ("text",),
}

# What a label can name (sessions.yaml RetrievalSession output entries).
NAMED_BY_LABEL: tuple[str, ...] = tuple(t for t in FIELDS if t not in ("content", "label"))

# Every field any of them names.
ALL_FIELDS: tuple[str, ...] = tuple(dict.fromkeys(f for fs in FIELDS.values() for f in fs))
