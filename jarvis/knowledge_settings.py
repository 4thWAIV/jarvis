"""Knowledge indexing configuration, shared by Settings and every worker.

The engine reads its own record locally during startup; server and workers
read the same record through records. No engine HTTP call to itself.
"""
from __future__ import annotations

from collections.abc import Callable
import os

PATH = "/settings/knowledge.settings"


def read(load: Callable[[], dict | None] | None = None) -> dict:
    if load is None:
        from jarvis import records
        rec = records.get(PATH)
        value = None if rec is None else rec.data
    else:
        value = load()
    # On by default (Tim, 2026-11-12). A saved Settings value wins, including
    # false; with none saved, JARVIS_KNOWLEDGE set to 0/false/no/off turns it
    # off for an installation that relies on the old switch.
    if value is None:
        return {"enabled": os.getenv("JARVIS_KNOWLEDGE", "1").strip().lower()
                not in ("0", "false", "no", "off")}
    return {"enabled": value.get("enabled") is True}


def change(enabled: bool) -> dict:
    if type(enabled) is not bool:
        raise ValueError("enabled must be a boolean")
    from jarvis import records
    saved = records.update(PATH, lambda old: {**(old or {}), "enabled": enabled},
                   change="Change knowledge indexing in Settings", full_sync=True)
    return read(lambda: saved)
