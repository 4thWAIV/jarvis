"""brain_accounts.py — this node's brains, for the team server's network.

What the node update's `brains` carries: one entry per brain in
brains_config, each with

  account_ref  base64url, unpadded, of HMAC-SHA256(team key, provider + "\\n"
               + lower-cased email + "\\n" + organisation ID or ""): the same
               subscription on two machines of one team gives the same
               reference, and no email leaves this machine
  provider     "anthropic" or "openai"
  role         "brain" for the brain JARVIS starts with, else "standby"
  state        "sign_in_needed" when the last sign-in check said so,
               "measured" when the usage record has a current seven-day
               window, else "not_measured"
  windows      the usage record's current windows, with their original
               observation time as `age_ms`; the seven-day one's expected
               share from usage_store.week(), the five-hour one's None

The sign-in check, with the email and organisation, is asked of each
provider by `refresh()`: at startup and after a sign-in, never per update.
They are held in memory only, used for the hash and nothing else: never
stored, logged or sent.

The team key comes from the team server's status and is the caller's to
pass; without one there are no brains to send.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import logging
import time
from datetime import datetime, timezone
from typing import Optional

from jarvis import brain_provider, brains_config, usage_store
from jarvis.brain_provider import Account

log = logging.getLogger("jarvis.brain_accounts")

# NodeUpdate.brains, max_length=8; BrainAccount.windows, max_length=4.
MAX_BRAINS = 8
MAX_WINDOWS = 4

# The last sign-in check per provider, from refresh().
_accounts: dict[str, Account] = {}


async def refresh(name: Optional[str] = None) -> None:
    """Ask the provider `name`, or every configured one, who is signed in.
    A provider that cannot be asked keeps what it last said."""
    names = [name] if name else list(brains_config.load()["brains"])
    for each in names:
        try:
            _accounts[each] = await brain_provider.provider(each).account()
        except Exception as e:
            log.warning(f"brain_accounts: {each}'s sign-in could not be checked "
                        f"({type(e).__name__})")


def account_ref(key: bytes, provider: str, email: str, org_id: Optional[str]) -> str:
    said = f"{provider}\n{email.lower()}\n{org_id or ''}".encode()
    digest = hmac.new(key, said, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def _key_bytes(key: Optional[str]) -> Optional[bytes]:
    """The team's key as the status gives it (base64url), or None."""
    if not key:
        return None
    try:
        raw = base64.urlsafe_b64decode(key + "=" * (-len(key) % 4))
    except (binascii.Error, ValueError):
        log.warning("brain_accounts: the team's account key is not base64url; no brains sent")
        return None
    return raw or None


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat().replace("+00:00", "Z")


def _windows(service: str, week: dict, now: float) -> list[dict]:
    out = []
    for view in usage_store.snapshot(now=now, service=service)["windows"]:
        if (view["key"] not in usage_store.KNOWN_WINDOWS or view["expired"]
                or None in (view["utilization"], view["resets_at"], view["age_sec"])):
            continue
        expected = week["expected_used"] if view["key"] == "seven_day" and week["measured"] else None
        out.append({"window": view["key"], "used_percent": view["utilization"],
                    "expected_used_percent": expected, "resets_at": _iso(view["resets_at"]),
                    "age_ms": int(view["age_sec"] * 1000)})
    return out[:MAX_WINDOWS]


def _state(account: Account, week: dict) -> str:
    if not account.signed_in:
        return "sign_in_needed"
    return "measured" if week["measured"] else "not_measured"


def brains(key: Optional[str], *, now: Optional[float] = None) -> list[dict]:
    """The node update's `brains`: empty without the team's key. A brain
    whose signed-in account is not known (never checked, or no email) has
    no reference to give and is left out."""
    secret = _key_bytes(key)
    if secret is None:
        return []
    at = time.time() if now is None else float(now)
    cfg = brains_config.load()
    out = []
    for name in cfg["brains"]:
        account = _accounts.get(name)
        if account is None or not account.email:
            continue
        week = usage_store.week(now=at, service=name)
        out.append({"account_ref": account_ref(secret, name, account.email, account.org_id),
                    "provider": name,
                    "role": "brain" if name == cfg["default"] else "standby",
                    "state": _state(account, week),
                    "windows": _windows(name, week, at)})
    return out[:MAX_BRAINS]
