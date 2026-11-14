"""Knowledge indexing configuration, shared by Settings and every worker.

The engine reads its own record locally during startup; server and workers
read the same record through records. No engine HTTP call to itself. The
setting is `knowledge_enabled` of settings_specs (its record field
`enabled`), changed through settings_service like every other setting.
"""
from __future__ import annotations

from collections.abc import Callable

from jarvis import settings_service

PATH = settings_service.record_path("knowledge")
KEY = "knowledge_enabled"


def read(load: Callable[[], dict | None] | None = None) -> dict:
    held = settings_service.held(KEY)
    if held is not settings_service.NOT_HELD:
        return {"enabled": held}
    if load is None:
        from jarvis import records
        rec = records.get(PATH)
        value = None if rec is None else rec.data
    else:
        value = load()
    # On by default (Tim, 2026-11-12); a saved Settings value wins, false included.
    if value is None or "enabled" not in value:
        return {"enabled": True}
    return {"enabled": value.get("enabled") is True}


def change(enabled: bool) -> dict:
    if type(enabled) is not bool:
        raise ValueError("enabled must be a boolean")
    return {"enabled": settings_service.change(KEY, enabled)}
