"""Access keys: a key that admits its holder to some categories of JARVIS's
gated routes and to nothing else.

The tool token (data_paths.ensure_tool_token) opens everything, the brain's
own tool channel included, so it is never handed to another agent. An agent
the user wants to let do one kind of thing gets an access key for that
category instead, sent the same way as the token: `Authorization: Bearer
<key>`. web_auth admits a request carrying a key only when one of the key's
categories covers that request's method and path.

Categories are defined here and nowhere else. The voice socket, settings,
brains, restart and /internal/tool are in none of them: those stay with the
browser and the tool token.

Only a digest of each key is stored, in `data_paths.access_keys_path()`
(0600); the key itself is shown once, when it is made. Revoking a label
takes effect on the next request, since every keyed request reads the file.

    python access_keys.py categories
    python access_keys.py create <label> <category> [<category> ...]
    python access_keys.py list
    python access_keys.py revoke <label>
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import sys
import time
from dataclasses import dataclass

import data_paths

# What a WebSocket handshake is called in a category's routes.
WEBSOCKET = "WS"

# Every key starts with this, so one is recognisable in a config file and
# never confused with the tool token.
PREFIX = "jarvis_key_"


@dataclass(frozen=True)
class Category:
    description: str
    routes: tuple[tuple[str, str], ...]   # (method, full-match path pattern)

    def covers(self, method: str, path: str) -> bool:
        return any(m == method and re.fullmatch(p, path) for m, p in self.routes)


CATEGORIES: dict[str, Category] = {
    "captures": Category(
        "Accept, delete, send and read aloud the captures on the voice page.",
        (("POST", r"/api/capture/\d+/(accept|delete|send|read)"),)),
    "agents": Category(
        "Rename agents and switch them active or inactive.",
        (("POST", r"/api/agents/[^/]+/(name|active)"),)),
    "runs": Category(
        "Start, cancel and retry runs, open projects, and watch the runs feed.",
        (("POST", r"/api/runs"), ("DELETE", r"/api/runs/[^/]+"),
         ("POST", r"/api/runs/[^/]+/retry"), ("POST", r"/api/projects/open"),
         (WEBSOCKET, r"/ws/runs"))),
    "workflows": Category(
        "Start workflows and report their steps to the Workflows view.",
        (("POST", r"/api/workflows"), ("POST", r"/api/workflows/[^/]+/events"))),
    "watch": Category(
        "Watch the sessions and specs feeds.",
        ((WEBSOCKET, r"/ws/sessions"), (WEBSOCKET, r"/ws/specs"))),
}

LABEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9 ._-]{0,63}")


class AccessKeyRefused(ValueError):
    """A request to make or revoke a key that cannot be done, with why."""


def _digest(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def _read() -> list[dict]:
    try:
        data = json.loads(data_paths.access_keys_path().read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    keys = data.get("keys") if isinstance(data, dict) else None
    return [k for k in keys or [] if isinstance(k, dict)]


def _write(keys: list[dict]) -> None:
    """Replaced whole, through a 0600 file renamed into place."""
    path = data_paths.access_keys_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(str(tmp), os.O_CREAT | os.O_TRUNC | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump({"keys": keys}, f, indent=2)
    os.replace(tmp, path)


def create(label: str, categories: list[str]) -> str:
    """Make a key for `categories` under `label`. Returns the key, which is
    not stored and cannot be shown again."""
    label = " ".join(label.split())
    if not LABEL.fullmatch(label):
        raise AccessKeyRefused("A label is letters, digits, spaces, dots, dashes and "
                        "underscores, up to 64 characters.")
    unknown = [c for c in categories if c not in CATEGORIES]
    if unknown or not categories:
        raise AccessKeyRefused("Categories are: " + ", ".join(CATEGORIES)
                        + (f". Not {', '.join(unknown)}." if unknown else "."))
    keys = _read()
    if any(k.get("label", "").lower() == label.lower() for k in keys):
        raise AccessKeyRefused(f"There is already a key labelled {label}; revoke it first.")
    key = PREFIX + secrets.token_urlsafe(32)
    keys.append({"label": label, "categories": sorted(set(categories)),
                 "sha256": _digest(key), "created_at": time.time()})
    _write(keys)
    return key


def revoke(label: str) -> bool:
    """Remove the key labelled `label`. Whether there was one."""
    keys = _read()
    kept = [k for k in keys if k.get("label", "").lower() != label.strip().lower()]
    if len(kept) == len(keys):
        return False
    _write(kept)
    return True


def listing() -> list[dict]:
    """Every key's label, categories and when it was made; never the key."""
    return [{"label": k.get("label", ""), "categories": list(k.get("categories") or []),
             "created_at": k.get("created_at")} for k in _read()]


def allows(key: str | None, method: str, path: str) -> bool:
    """Whether `key` is a live access key one of whose categories covers this
    request. Never raises: an unreadable store admits nothing."""
    if not key or not key.startswith(PREFIX):
        return False
    supplied = _digest(key)
    try:
        keys = _read()
    except (OSError, ValueError):
        return False
    for k in keys:
        if secrets.compare_digest(str(k.get("sha256", "")), supplied):
            return any(CATEGORIES[c].covers(method.upper(), path)
                       for c in k.get("categories") or [] if c in CATEGORIES)
    return False


def main(argv: list[str]) -> int:
    command, args = (argv[1], argv[2:]) if len(argv) > 1 else ("", [])
    try:
        if command == "categories" and not args:
            for name, category in CATEGORIES.items():
                print(f"{name}: {category.description}")
            return 0
        if command == "create" and len(args) >= 2:
            key = create(args[0], args[1:])
            print(f"Key for {args[0]} ({', '.join(args[1:])}). It is shown only now:")
            print(key)
            return 0
        if command == "list" and not args:
            for k in listing():
                made = time.strftime("%Y-%m-%d %H:%M", time.localtime(k["created_at"] or 0))
                print(f"{k['label']}: {', '.join(k['categories'])} (made {made})")
            return 0
        if command == "revoke" and len(args) == 1:
            if revoke(args[0]):
                print(f"Revoked {args[0]}.")
                return 0
            print(f"No key is labelled {args[0]}.", file=sys.stderr)
            return 1
    except AccessKeyRefused as e:
        print(e, file=sys.stderr)
        return 1
    print(__doc__.split("\n\n")[-1], file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
