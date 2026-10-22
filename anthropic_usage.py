"""The subscription's current limits, asked of Anthropic directly, every minute.

The CLI reports the five-hour and seven-day limits only on a reply, so the
reading went stale between JARVIS's turns. Claude Code's own `/usage` screen
asks Anthropic for them at USAGE_URL with the user's Claude Code login; this
asks the same thing the same way. The address is not publicly documented and
may change: every failure is logged and the last reading kept, never raised.

THE LOGIN
---------
Read from the macOS keychain item Claude Code keeps it in, held in memory for
as long as it is good, and read again only when it is about to expire or
Anthropic turns it away. It is never written anywhere and never logged.

It is NEVER refreshed here. A refresh replaces the refresh token, so doing it
here would leave Claude Code holding a dead one and log it out. Claude Code
refreshes its own login whenever it runs (JARVIS's brain runs on it all the
time) and saves the new one to the keychain, which is where this reads it.
"""

from __future__ import annotations

import json
import logging
import subprocess
import time
import urllib.error
import urllib.request
from typing import Any, Callable, Optional

import brain_provider

log = logging.getLogger("jarvis.anthropic_usage")

USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
KEYCHAIN_SERVICE = "Claude Code-credentials"
# Read the login again this long before it says it expires.
EXPIRY_MARGIN_SEC = 60.0
REQUEST_TIMEOUT_SEC = 15.0
POLL_SEC = 60.0


# No reading this time; the reason is the message (never the login). The
# brain providers' shared kind, so the usage poll catches one for any of them.
UsageUnavailable = brain_provider.UsageUnavailable


SERVICE = "anthropic"


def _read_keychain() -> tuple[str, float, str]:
    """(access token, expiry epoch seconds, subscription type such as "max")
    from Claude Code's keychain item."""
    try:
        out = subprocess.run(
            ["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-w"],
            capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError) as e:
        raise UsageUnavailable(f"could not read the keychain ({type(e).__name__})")
    if out.returncode != 0:
        raise UsageUnavailable(f"keychain item not readable (exit {out.returncode})")
    try:
        oauth = json.loads(out.stdout)["claudeAiOauth"]
        token = str(oauth["accessToken"])
        expires = float(oauth.get("expiresAt") or 0) / 1000.0
        subscription = str(oauth.get("subscriptionType") or "")
    except (ValueError, KeyError, TypeError):
        raise UsageUnavailable("keychain item is not the shape Claude Code writes")
    return token, expires, subscription


def _get(token: str) -> dict:
    req = urllib.request.Request(USAGE_URL, headers={
        "Authorization": f"Bearer {token}",
        "anthropic-beta": "oauth-2025-04-20",
        "User-Agent": "jarvis-usage",
    })
    with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_SEC) as r:
        return json.loads(r.read())


class UsageClient:
    """Holds the login in memory and asks for the limits."""

    def __init__(self, read_keychain: Callable[[], tuple[str, float, str]] = _read_keychain,
                 get: Callable[[str], dict] = _get, clock: Callable[[], float] = time.time):
        self._read_keychain = read_keychain
        self._get = get
        self._clock = clock
        self._token: Optional[str] = None
        self._expires = 0.0
        self._subscription = ""

    def _login(self, fresh: bool = False) -> str:
        if fresh or not self._token or self._clock() >= self._expires - EXPIRY_MARGIN_SEC:
            self._token, self._expires, self._subscription = self._read_keychain()
        return self._token

    def provider(self) -> dict:
        """Who the figures come from: the service and the subscription type,
        as the login says. Read with the login, so known after a fetch."""
        return {"service": SERVICE, "subscription": self._subscription}

    def fetch(self) -> dict:
        """The raw answer. Turned away once: the keychain is read again (Claude
        Code may have renewed the login) and the question asked once more."""
        try:
            return self._get(self._login())
        except urllib.error.HTTPError as e:
            if e.code != 401:
                raise UsageUnavailable(f"Anthropic answered {e.code}")
        except (urllib.error.URLError, OSError, ValueError) as e:
            raise UsageUnavailable(f"no answer from Anthropic ({type(e).__name__})")
        try:
            return self._get(self._login(fresh=True))
        except urllib.error.HTTPError as e:
            raise UsageUnavailable(f"Anthropic answered {e.code} with a fresh login")
        except (urllib.error.URLError, OSError, ValueError) as e:
            raise UsageUnavailable(f"no answer from Anthropic ({type(e).__name__})")


def _epoch(iso: Any) -> Optional[float]:
    if not isinstance(iso, str):
        return None
    from datetime import datetime
    try:
        return datetime.fromisoformat(iso).timestamp()
    except ValueError:
        return None


def as_rate_limit_info(body: dict) -> dict:
    """The answer in the shape usage_store.record takes from the CLI.

    Anthropic answers in PERCENT (3.0 is 3%); the CLI sends a FRACTION, and
    usage_store reads anything up to 1 as one. So it is divided here: passed
    as it is, half a percent (0.5) would be stored as 50%."""
    windows = {}
    for key in ("five_hour", "seven_day"):
        w = body.get(key)
        if not isinstance(w, dict) or not isinstance(w.get("utilization"), (int, float)):
            continue
        windows[key] = {"utilization": float(w["utilization"]) / 100.0,
                        "resetsAt": _epoch(w.get("resets_at")),
                        "status": "allowed"}
    return {"status": "allowed", "unifiedWindows": windows}
