"""The start file: the few settings JARVIS needs before its records are open.

`<data folder>/settings/start.json`, written only by `change` (and
`remember_served`), read only here. It holds the `store="start"` settings of
`settings_specs.py` (the address, port, HTTPS, whether the brain starts,
the page's extra origins, the docs console, the engine's memory budget),
the last start setup a page reached (`last_good`), a changed setup on trial
(`trial`), the last time JARVIS went back (`reverted`) and when `.env` was
copied in.

The file carries an HMAC-SHA256 of its body keyed with the tool token: only
JARVIS can write one it will read. A file edited by hand, or one it cannot
read, is not trusted: JARVIS starts on the defaults and `problem()` says
why, which Settings, Server shows. Written through one atomic rename, 0600.

Confirm or revert. A start from Settings (no command-line flags) whose
setup (`SETUP_KEYS`: the address, port, HTTPS and the page's extra origins)
differs from the last one a page reached is a trial (`begin_trial`, once it
is listening). A page reaching it (server._page_reached, told by web_auth: a
socket or a request admitted by the page's own origin, never the tool token
or an agent's key) confirms it (`remember_served`), and it becomes
`last_good`; nothing else ever writes `last_good`. No page within
`TRIAL_WINDOW_SEC` and JARVIS goes back (`fall_back`) and restarts. A start
whose address cannot be bound goes back the same way, before it binds. Each
going back is recorded (`reverted`: when, from what, to what, why), shown in
Settings, Server, in Diagnostics and as an attention item, until he next
saves a start setting. With no `last_good` yet nothing is known to work, so
there is no trial: the first page to reach it records it.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import socket
import tempfile
import threading
import time
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

# How long a changed start setup waits for a page to reach it before JARVIS
# goes back to the last setup a page reached (the owner's 60 seconds).
TRIAL_WINDOW_SEC = 60.0
# The start settings that decide whether the page can reach JARVIS at all.
SETUP_KEYS = ("host", "port", "https", "allowed_origins")
# Why JARVIS went back (`reverted["why"]`).
UNREACHED = "unreached"      # no page reached the trial within the window
UNBINDABLE = "unbindable"    # the address could not be bound
# How long a going back is shown as worth his attention (Diagnostics, and
# the attention item at a start).
REVERT_NOTICE_SEC = 7 * 24 * 3600.0


def now() -> float:
    """The clock a trial is timed by (a seam for tests)."""
    return time.time()


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
    return {"values": {}, "last_good": None, "imported_from_env": None, "problem": None,
            "trial": None, "reverted": None}


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
        doc["reverted"] = None
        if imported:
            doc["imported_from_env"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        return _write(doc)


def _iso(at: float) -> str:
    return datetime.fromtimestamp(at, timezone.utc).isoformat(timespec="seconds")


def _default(key: str) -> Any:
    return settings_specs.spec(key).default


def _fallback(doc: dict) -> dict:
    """What going back restores: the last setup a page reached (a key it
    does not name, from a record made before it named the origins, as saved
    now), or the defaults when no page has reached any."""
    good = doc.get("last_good")
    if not good:
        return {k: _default(k) for k in SETUP_KEYS}
    return {k: good[k] if k in good else doc["values"].get(k, _default(k)) for k in SETUP_KEYS}


def address(setup: dict) -> str:
    """A start setup's address as a page would open it."""
    host = str(setup.get("host") or "")
    if ":" in host:
        host = f"[{host}]"
    return f"{'https' if setup.get('https') else 'http'}://{host}:{setup.get('port')}"


def _same(setup: dict, other: dict) -> bool:
    return all(setup.get(k) == other.get(k) for k in SETUP_KEYS if k in setup)


def begin_trial(setup: dict) -> Optional[dict]:
    """A start from Settings is listening on `setup`: when it differs from
    the last setup a page reached, keep it as a trial, timed from now, and
    return it; else None. With no setup reached yet there is no trial."""
    with _lock:
        doc = dict(document())
        if not doc.get("last_good") or _same(setup, _fallback(doc)):
            if doc.get("trial"):
                doc["trial"] = None
                _write(_keeping_problem(doc))
            return None
        doc["trial"] = {"setup": setup, "fallback": _fallback(doc), "began": now()}
        return _write(_keeping_problem(doc))["trial"]


def trial() -> Optional[dict]:
    """The trial pending, with its `deadline` and `seconds_left`, or None."""
    t = document().get("trial")
    if not t:
        return None
    deadline = t["began"] + TRIAL_WINDOW_SEC
    return {**t, "deadline": deadline, "seconds_left": max(0.0, deadline - now())}


def remember_served(values: dict) -> None:
    """A page reached the start `values`: it is the setup that worked, what
    `fall_back` goes back to, and any trial is over. The one writer of
    `last_good`."""
    with _lock:
        doc = dict(document())
        if doc.get("last_good") == values and not doc.get("trial"):
            return
        doc["last_good"] = values
        doc["trial"] = None
        _write(_keeping_problem(doc))


def status() -> dict:
    """The trial pending and the last going back, as Settings, Server, the
    brain and Diagnostics read them."""
    return {"trial": trial(), "reverted": document().get("reverted"),
            "window_sec": TRIAL_WINDOW_SEC}


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


def fall_back(why: str = UNBINDABLE) -> dict:
    """Go back to the last setup a page reached (or the defaults): its
    values saved again in place of the ones that did not bind (`UNBINDABLE`)
    or that no page reached in time (`UNREACHED`), recorded as `reverted`.
    The one rollback."""
    with _lock:
        doc = dict(document())
        good = _fallback(doc)
        was = {k: doc["values"].get(k, _default(k)) for k in SETUP_KEYS}
        tried = (doc.get("trial") or {}).get("setup")
        at = now()
        doc["values"] = {**doc["values"], **good}
        doc["trial"] = None
        doc["reverted"] = {"at": at, "when": _iso(at), "why": why,
                           "from": tried or was, "to": good}
        return _write(_keeping_problem(doc))
