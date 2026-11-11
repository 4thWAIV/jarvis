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
    # Compatibility for existing installations only. A saved Settings value
    # wins, including false, regardless of inherited environment variables.
    if value is None:
        return {"enabled": os.getenv("JARVIS_KNOWLEDGE", "0").strip().lower()
                in ("1", "true", "yes", "on")}
    return {"enabled": value.get("enabled") is True}


def change(enabled: bool) -> dict:
    if type(enabled) is not bool:
        raise ValueError("enabled must be a boolean")
    from jarvis import records
    saved = records.update(PATH, lambda old: {**(old or {}), "enabled": enabled},
                   change="Change knowledge indexing in Settings", full_sync=True)
    return read(lambda: saved)
