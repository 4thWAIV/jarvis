"""The start file: the few settings JARVIS needs before its records are open.

`<data folder>/settings/start.json`, written only by `change` (and
`remember_served`), read only here. It holds the `store="start"` settings of
`settings_specs.py` (the address, port, HTTPS, whether the brain starts,
the page's extra origins, the docs console, the engine's memory budget),
the last start that served (`last_good`) and when `.env` was copied in.

The file carries an HMAC-SHA256 of its body keyed with the tool token: only
JARVIS can write one it will read. A file edited by hand, or one it cannot
read, is not trusted: JARVIS starts on the defaults and `problem()` says
why, which Settings, Server shows. Written through one atomic rename, 0600.

A restart that cannot bind the address and port it holds goes back to the
last start that served (`fall_back`), so the page always comes back.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import socket
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from jarvis import data_paths, settings_specs

_lock = threading.RLock()
# path -> (mtime_ns, size, document): the file read once per change of it.
_read_cache: dict[str, tuple[int, int, dict]] = {}
_problem: dict[str, Optional[str]] = {}

TAMPERED = "tampered"
UNREADABLE = "unreadable"


def path() -> Path:
    return data_paths.data_dir() / "settings" / "start.json"


def _key() -> bytes:
    return data_paths.ensure_tool_token().encode()


def _body_bytes(body: dict) -> bytes:
    return json.dumps(body, sort_keys=True, separators=(",", ":")).encode()


def _sign(body: dict) -> str:
    return hmac.new(_key(), _body_bytes(body), hashlib.sha256).hexdigest()


def _empty() -> dict:
    # `problem`: why the file found at a start was not trusted, and when,
    # kept in the file JARVIS wrote in its place until he saves in Settings.
    return {"values": {}, "last_good": None, "imported_from_env": None, "problem": None}


def document() -> dict:
    """The trusted body of the start file, or an empty one (with `problem()`
    saying why when a file was there and not trusted)."""
    p = path()
    key = str(p)
    with _lock:
        try:
            st = p.stat()
        except FileNotFoundError:
            _problem[key] = None
            return _empty()
        except OSError:
            _problem[key] = UNREADABLE
            return _empty()
        cached = _read_cache.get(key)
        if cached and cached[0] == st.st_mtime_ns and cached[1] == st.st_size:
            return cached[2]
        try:
            raw = json.loads(p.read_text())
            body, sig = raw["body"], raw["sig"]
            if not isinstance(body, dict) or not isinstance(sig, str):
                raise ValueError
        except (OSError, ValueError, KeyError, TypeError):
            _problem[key] = UNREADABLE
            _read_cache[key] = (st.st_mtime_ns, st.st_size, _empty())
            return _empty()
        if not hmac.compare_digest(sig, _sign(body)):
            _problem[key] = TAMPERED
            _read_cache[key] = (st.st_mtime_ns, st.st_size, _empty())
            return _empty()
        doc = {**_empty(), **body}
        _problem[key] = None
        _read_cache[key] = (st.st_mtime_ns, st.st_size, doc)
        return doc


def problem() -> Optional[str]:
    """`tampered` or `unreadable` when the file there was not trusted, or
    when the file JARVIS wrote in its place still says so (until he saves
    in Settings), else None."""
    doc = document()
    found = _problem.get(str(path()))
    if found:
        return found
    kept = doc.get("problem")
    return kept.get("kind") if isinstance(kept, dict) else None


def _keeping_problem(doc: dict) -> dict:
    """`doc` to be written over the file there: an untrusted file's problem
    is kept in what replaces it, so Settings, Server can still say why."""
    found = _problem.get(str(path()))
    if found:
        doc["problem"] = {"kind": found,
                          "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    return doc


def saved(key: str) -> Any:
    """The value saved for one start setting, or None when none is."""
    return document()["values"].get(key)


def _write(doc: dict) -> dict:
    p = path()
    p.parent.mkdir(parents=True, exist_ok=True)
    body = {k: doc.get(k) for k in _empty()}
    text = json.dumps({"body": body, "sig": _sign(body)}, indent=2, sort_keys=True)
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=".start-", suffix=".json")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
        os.chmod(tmp, 0o600)
        os.replace(tmp, p)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    with _lock:
        _read_cache.pop(str(p), None)
    return document()


def change(values: dict, *, imported: bool = False) -> dict:
    """Save start settings (already checked by settings_service), merged
    into what is saved. `imported` records the one-time copy from .env."""
    with _lock:
        doc = dict(document())
        doc["values"] = {**doc["values"], **values}
        doc["problem"] = None       # his save in Settings: what he sees now is what he chose
        if imported:
            doc["imported_from_env"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        return _write(doc)


def remember_served(values: dict) -> None:
    """The start that served: what `fall_back` goes back to."""
    with _lock:
        doc = dict(document())
        if doc.get("last_good") == values:
            return
        doc["last_good"] = values
        _write(_keeping_problem(doc))


def bindable(host: str, port: int) -> bool:
    """Whether this process could listen on host:port now."""
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except (OSError, UnicodeError):
        return False
    for family, kind, proto, _name, address in infos:
        try:
            with socket.socket(family, kind, proto) as s:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                s.bind(address)
                return True
        except OSError:
            continue
    return False


def fall_back() -> dict:
    """Go back to the last start that served (or the defaults): its values
    saved again in place of the ones that did not bind."""
    with _lock:
        doc = dict(document())
        good = doc.get("last_good") or {}
        doc["values"] = {**doc["values"], **{k: good.get(k, settings_specs.spec(k).default)
                                             for k in ("host", "port", "https")}}
        return _write(_keeping_problem(doc))
