"""Service version and build commit, embedded in the image at build time.

The deploy package carries build_info.json (written by ../package.sh from a
clean Git checkout); nothing is entered by the operator.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

SERVICE = "jarvis-team-server"
VERSION = "0.1.0"
API_VERSION = 1
# What this server offers. Messaging and presence are deliberately absent:
# the first release does not have them.
CAPABILITIES = ("github-login", "membership", "device-pairing", "status")
_FILE = Path(__file__).resolve().parent.parent / "build_info.json"


@lru_cache(maxsize=1)
def commit() -> str:
    try:
        data = json.loads(_FILE.read_text())
        value = str(data.get("commit", ""))
        return value if value else "unknown"
    except (OSError, ValueError):
        return "development"
