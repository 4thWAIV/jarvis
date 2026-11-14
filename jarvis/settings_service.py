"""Every setting's value, and the one way one is changed: what Settings'
routes and the brain's `jarvis_settings` / `set_jarvis_setting` call, and
what every part of JARVIS reads a setting through (`value`).

The settings are `settings_specs.SPECS`. A record setting is a field of
`/settings/<area>.settings` (records.py), read once the records are open
(`load`, from the server's `_start_on_records`, and best-effort by a command
that needs one) and held in memory, refreshed by every change; until then it
reads as its default. A start setting is the start file's
(`start_settings.py`), readable before anything else. A secret is the
Keychain's (team_client's store under ``JARVIS secrets``, one account per
data folder's node), never a record and never shown.

`hold` puts a value in force for this process only, never saved, until the
setting is next changed: the test suite's one way to set a setting, and the
isolated decision-check JARVIS's (decision_check_server.py).
"""
from __future__ import annotations

import ipaddress
import math
import os
import sys
import threading
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlsplit

from jarvis import settings_specs, start_settings
from jarvis.settings_specs import Spec

SECRET_SERVICE = "JARVIS secrets"

_lock = threading.RLock()
_held: dict[str, Any] = {}
# engine key -> {setting key -> value}: what was read from that engine's records.
_loaded: dict[str, dict[str, Any]] = {}
_secrets: dict[str, dict[str, str]] = {}


class Refused(ValueError):
    """A value the setting cannot take; `code` and `values` for the page's words."""

    def __init__(self, code: str, message: str, **values):
        super().__init__(message)
        self.code = code
        self.values = values


def record_path(area: str) -> str:
    return f"/settings/{area}.settings"


def _engine_key() -> Optional[str]:
    try:
        from jarvis import records
        return records.engine_key()
    except Exception:  # noqa: BLE001 - no engine means no records yet
        return None


# -- reading -----------------------------------------------------------------

def _secret_store():
    from jarvis import team_client
    return (team_client.CredentialManagerStore(SECRET_SERVICE) if sys.platform == "win32"
            else team_client.KeychainStore(SECRET_SERVICE))


def _secret_account(key: str) -> str:
    from jarvis import data_paths
    return f"{data_paths.node_id()}:{key}"


def _secret(key: str) -> str:
    from jarvis import data_paths
    folder = str(data_paths.data_dir())
    with _lock:
        known = _secrets.setdefault(folder, {})
        if key not in known:
            try:
                known[key] = _secret_store().get(_secret_account(key)) or ""
            except Exception:  # noqa: BLE001 - a store that cannot be read holds nothing
                known[key] = ""
        return known[key]


def value(key: str) -> Any:
    """The value in force for one setting."""
    s = settings_specs.spec(key)
    with _lock:
        if key in _held:
            return _held[key]
    if s.store == "start":
        found = start_settings.saved(key)
        return s.default if found is None else found
    if s.store == "secret":
        return _secret(key)
    engine = _engine_key()
    with _lock:
        found = (_loaded.get(engine) or {}).get(key) if engine else None
    return s.default if found is None else found


def load() -> None:
    """Read every area's record into memory: from the server once its
    records are open, and from a command that needs a saved setting. Never
    raises: an engine that cannot be reached leaves the defaults."""
    try:
        from jarvis import records
        engine = records.engine_key()
        found: dict[str, Any] = {}
        for area in settings_specs.AREAS:
            rec = records.get(record_path(area))
            data = (rec.data if rec is not None else None) or {}
            for s in settings_specs.in_area(area):
                if s.store == "record" and s.record_field in data:
                    try:
                        found[s.key] = check(s, data[s.record_field])
                    except Refused:
                        continue
    except Exception:  # noqa: BLE001
        return
    with _lock:
        _loaded[engine] = found


def hold(key: str, held: Any) -> None:
    """Put `held` in force for this process, unsaved, until the setting is changed."""
    s = settings_specs.spec(key)
    with _lock:
        _held[key] = check(s, held)


NOT_HELD = object()


def held(key: str) -> Any:
    """The value held for this process (`hold`), or NOT_HELD."""
    with _lock:
        return _held.get(key, NOT_HELD)


def let_go(key: Optional[str] = None) -> None:
    """Undo `hold` for one setting, or for all."""
    with _lock:
        if key is None:
            _held.clear()
        else:
            _held.pop(key, None)


def in_force_at_start() -> dict:
    """The start settings this process started with (set by server.main)."""
    return dict(_started)


_started: dict[str, Any] = {}


def note_started(values: dict) -> None:
    _started.clear()
    _started.update(values)


# -- checking ----------------------------------------------------------------

def _origin(text: str) -> str:
    text = text.strip().rstrip("/")
    parts = urlsplit(text)
    if parts.scheme not in ("http", "https") or not parts.hostname or parts.path \
            or parts.query or parts.fragment or parts.username or parts.password:
        raise Refused("origin", f"{text!r} is not an origin like https://host:port", value=text)
    return f"{parts.scheme}://{parts.netloc}".lower()


def _no_controls(text: str, s: Spec) -> str:
    if len(text) > s.max_chars:
        raise Refused("tooLong", f"At most {s.max_chars} characters", max=s.max_chars)
    # Anything that ends a line (every separator str.splitlines knows, the
    # Unicode ones included) or is a control character: a name reaches the
    # brain's system prompt, and a line break there is a line of somebody's
    # choosing standing in it as JARVIS's own words.
    if (text and text.splitlines() != [text]) or \
            any(ord(c) < 32 or 0x7f <= ord(c) <= 0x9f for c in text):
        raise Refused("control", "A setting cannot contain a control character or line break")
    return text


def check(s: Spec, raw: Any) -> Any:
    """The value `raw` is for setting `s`, normalised, or Refused."""
    kind = s.kind
    if kind == "bool":
        if type(raw) is not bool:
            raise Refused("notBool", "This setting is on or off")
        return raw
    if kind in ("int", "float"):
        if raw is None or raw == "":
            if s.optional:
                return None
            raise Refused("notNumber", "This setting needs a number")
        if type(raw) is bool:
            raise Refused("notNumber", "This setting needs a number")
        try:
            number = float(raw)
        except (TypeError, ValueError):
            raise Refused("notNumber", "This setting needs a number") from None
        if not math.isfinite(number):
            raise Refused("notNumber", "This setting needs a number")
        if kind == "int":
            if number != int(number):
                raise Refused("notWhole", "This setting needs a whole number")
            number = int(number)
        if s.minimum is not None and number < s.minimum or \
                s.maximum is not None and number > s.maximum:
            raise Refused("range", f"Between {s.minimum:g} and {s.maximum:g}",
                          min=s.minimum, max=s.maximum)
        return number
    if kind == "choice":
        allowed = s.choice_values()
        if raw not in allowed:
            raise Refused("choice", "One of: " + ", ".join(map(str, allowed)),
                          choices=", ".join(map(str, allowed)))
        return raw
    if kind in ("paths", "origins"):
        if isinstance(raw, str):
            raw = [part for part in raw.replace("\n", ",").split(",") if part.strip()]
        if not isinstance(raw, list) or len(raw) > 32:
            raise Refused("notList", "A list of at most 32")
        out = []
        for item in raw:
            if not isinstance(item, str):
                raise Refused("notList", "A list of at most 32")
            item = _no_controls(item.strip(), s)
            if kind == "origins":
                item = _origin(item)
            elif not Path(item).expanduser().is_absolute():
                raise Refused("notAbsolute", f"{item!r} is not a full path", value=item)
            if item and item not in out:
                out.append(item)
        return out
    if not isinstance(raw, str):
        raise Refused("notText", "This setting needs text")
    text = _no_controls(raw, s)
    if kind == "path" and text and not Path(text).expanduser().is_absolute():
        raise Refused("notAbsolute", f"{text!r} is not a full path", value=text)
    if kind == "secret" and text and any(c in text for c in " \t\"'\\"):
        raise Refused("secret", "A key has no spaces or quotes")
    if s.key == "host" and text:
        _host(text)
    return text


def _host(text: str) -> None:
    try:
        ipaddress.ip_address(text.strip("[]"))
        return
    except ValueError:
        pass
    labels = text.split(".")
    if not all(label and len(label) <= 63 and all(c.isalnum() or c == "-" for c in label)
               for label in labels):
        raise Refused("host", f"{text!r} is not an address or host name", value=text)


def parse_text(s: Spec, text: str) -> Any:
    """A value as `.env` held it (text), for the one-time copy; Refused when
    it does not make one."""
    text = (text or "").strip()
    if s.kind == "bool":
        low = text.lower()
        if low in ("1", "true", "yes", "on"):
            return True
        if low in ("0", "false", "no", "off", ""):
            return False
        raise Refused("notBool", "This setting is on or off")
    if s.kind in ("int", "float"):
        return check(s, text or None)
    if s.kind == "paths":
        sep = ":" if s.key == "project_roots" else os.pathsep
        return check(s, [p for p in text.split(sep) if p.strip()])
    if s.kind == "choice":
        return check(s, text.lower())
    return check(s, text)


# -- changing ----------------------------------------------------------------

def change(key: str, new: Any, *, by: str = "tim") -> Any:
    """Save one setting (checked), put it in force and return it. A secret
    goes to the Keychain; a start setting to the start file; any other to
    its area's record."""
    s = settings_specs.spec(key)
    checked = check(s, new)
    if s.key == "fish_voice_id" and checked and checked == value("fish_api_key"):
        raise Refused("voiceIsKey", "Use a Fish Audio voice model ID in Voice ID")
    if s.key == "fish_api_key" and checked and checked == value("fish_voice_id"):
        raise Refused("keyIsVoice", "Replace the key in Voice ID with a Fish Audio voice model "
                                    "ID first")
    if s.store == "start":
        start_settings.change({key: checked})
    elif s.store == "secret":
        from jarvis import data_paths
        store = _secret_store()
        if checked:
            store.set(_secret_account(key), checked)
        else:
            store.delete(_secret_account(key))
        with _lock:
            _secrets.setdefault(str(data_paths.data_dir()), {})[key] = checked
    else:
        from jarvis import records
        records.update(record_path(s.area),
                       lambda old: {**(old or {}), s.record_field: checked},
                       by=records.by(by), change=f"Change {key} in Settings", full_sync=True)
        engine = records.engine_key()
        with _lock:
            _loaded.setdefault(engine, {})[key] = checked
    with _lock:
        _held.pop(key, None)
    return checked


def save_imported(found: dict) -> None:
    """The one-time copy's record settings, written in one write per area."""
    from jarvis import records
    by_area: dict[str, dict] = {}
    for key, v in found.items():
        s = settings_specs.spec(key)
        by_area.setdefault(s.area, {})[s.record_field] = v
    for area, fields in by_area.items():
        records.update(record_path(area), lambda old, fields=fields: {**fields, **(old or {})},
                       change="Copy settings from .env once", full_sync=True)
    load()


# -- the view ----------------------------------------------------------------

def described(s: Spec) -> dict:
    v = value(s.key)
    out = {"key": s.key, "area": s.area, "kind": s.kind, "default": s.default,
           "store": s.store, "restart": s.restart, "page_only": s.page_only,
           "control": s.control, "target": s.target, "optional": s.optional}
    if s.kind == "secret":
        out["set"] = bool(v)
        out["default"] = None
    else:
        out["value"] = v
    if s.minimum is not None:
        out["min"], out["max"] = s.minimum, s.maximum
    if s.kind == "choice":
        out["choices"] = list(s.choice_values())
    if s.store == "start" and s.restart and _started:
        out["pending_restart"] = _started.get(s.key, s.default) != v
    return out


def view(area: Optional[str] = None) -> dict:
    """Every setting (or one area's) as Settings and the brain read them."""
    if area is not None and area not in settings_specs.AREAS:
        raise Refused("area", "No such area: " + ", ".join(settings_specs.AREAS))
    specs = settings_specs.SPECS if area is None else settings_specs.in_area(area)
    doc = start_settings.document()
    return {"settings": [described(s) for s in specs],
            "start": {"problem": start_settings.problem(),
                      "imported_from_env": doc.get("imported_from_env"),
                      "file": str(start_settings.path())}}
