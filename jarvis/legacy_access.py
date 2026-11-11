"""The cut-over guard: access keys stop only once nobody has used one for
seven days (agent keys design, section 6).

Access keys were bearer keys an agent sent instead of signing
(`data_paths.access_keys_path()`, each a label, its categories and the
SHA-256 of the key). Agent keys replace them; phase 6 removed them. So that
no agent still reporting on one is cut off, this module keeps accepting
them, exactly as before (a key's categories are agent_rights.RIGHTS of the
same names), for as long as `pending`:

  - the cut-over has not happened yet (`cutover` in the
    `/settings/agent-keys.settings` document), and
  - the record holds an access key, and
  - one was used within QUIET_DAYS (counted per day and label in the
    `/settings/legacy-admissions.settings` document, since phase 3), or
    the use has not yet been
    watched for QUIET_DAYS (from the first day counted, or when this guard
    first looked, whichever is earlier).

The first look that finds none of that true records the cut-over, for
good: from then on no access key admits anything, whatever is used later.
Nothing here deletes or rewrites the access keys' record, and nothing
reads the Keychain.
"""
from __future__ import annotations

import hashlib
import json
import secrets
import time
from typing import Optional

from jarvis import agent_key_store, agent_rights, data_paths

QUIET_DAYS = 7
PREFIX = "jarvis_key_"
CUTOVER, WATCHING = "cutover", "access_keys_watched_since"


def _keys() -> list[dict]:
    try:
        data = json.loads(data_paths.access_keys_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    keys = data.get("keys") if isinstance(data, dict) else None
    return [k for k in keys or [] if isinstance(k, dict) and k.get("sha256")]


def status(now: Optional[float] = None) -> dict:
    """Where the cut-over stands: `cut_over_at` once done; else the labels
    used in the last QUIET_DAYS with their counts, and when access keys stop
    at the earliest (`stops_after`, if nothing is used before then)."""
    now = time.time() if now is None else now
    done = agent_key_store.setting(CUTOVER)
    if done is not None:
        return {"cut_over_at": done[1], "used": {}, "stops_after": None}
    agent_key_store.set_setting(WATCHING, "1", now, keep=True)
    watched = min(t for t in (agent_key_store.setting(WATCHING)[1],
                              agent_key_store.first_legacy_day()) if t is not None)
    used = agent_key_store.legacy_counts(QUIET_DAYS, now)
    last = max(watched, agent_key_store.last_legacy_day_end() or 0.0)
    return {"cut_over_at": None, "used": used, "keys": len(_keys()),
            "stops_after": last + QUIET_DAYS * 86400}


def pending(now: Optional[float] = None) -> bool:
    """Whether access keys are still accepted; the first look that finds
    the guard satisfied records the cut-over for good. Never raises: a
    record that cannot be read keeps them (nothing is cut off by an error)."""
    now = time.time() if now is None else now
    try:
        st = status(now)
        if st["cut_over_at"] is not None:
            return False
        if st["keys"] and (st["used"] or now < st["stops_after"]):
            return True
        agent_key_store.set_setting(CUTOVER, "access keys admit nothing", now)
        return False
    except Exception:
        return True


def admit(key: Optional[str], method: str, path: str) -> Optional[str]:
    """The label of the access key `key`, when the guard still accepts
    access keys and one of the key's categories covers this request; else
    None. Each admission is counted (`count_legacy`)."""
    if not key or not key.startswith(PREFIX) or not pending():
        return None
    supplied = hashlib.sha256(key.encode("utf-8")).hexdigest()
    entry = next((k for k in _keys() if secrets.compare_digest(str(k["sha256"]), supplied)),
                 None)
    if entry is None:
        return None
    method = method.upper()
    if not any(c in agent_rights.RIGHTS and agent_rights.RIGHTS[c].covers(method, path)
               for c in entry.get("categories") or []):
        return None
    label = str(entry.get("label") or "")
    agent_key_store.count_legacy(label)
    return label
