"""Device pairing: how a JARVIS backend gets a session without the browser
ever holding it.

1. The device starts a transaction with a device name and the S256
   challenge of a verifier it keeps. It gets back a short user code, the
   verification URL and a private polling credential. Ten minutes.
2. A signed-in member opens the URL, sees the device name and code, and
   explicitly approves or denies it (CSRF-protected).
3. The device exchanges polling credential + verifier. One successful
   exchange issues one session, sent straight back to the device.

Every transition is an atomic state change on one document:
pending -> approved | denied; approved -> consumed | failed. A wrong
verifier fails the transaction. A crash between "consumed" and the session
insert means a fresh pairing, never a replay.
"""
from __future__ import annotations

import re
import uuid
from datetime import timedelta

from pymongo import ReturnDocument
from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import DuplicateKeyError

from teamserver import clock, sessions, team as team_mod
from teamserver.db import TRANSACTION_PURGE_AFTER
from teamserver.errors import Problem, forbidden
from teamserver.secrets_util import (digest, new_secret, new_user_code,
                                     pkce_challenge, same)

LIFETIME = timedelta(minutes=10)
POLL_INTERVAL_SEC = 5
DEVICE_NAME_MAX = 64
_CHALLENGE = re.compile(r"^[A-Za-z0-9_-]{43}$")


def invalid_grant() -> Problem:
    return Problem(400, "invalid_grant",
                   "This pairing cannot be used; start a new one.")


def clean_device_name(name: str) -> str:
    name = " ".join(name.split())
    if not name or len(name) > DEVICE_NAME_MAX or not name.isprintable():
        raise Problem(422, "invalid_device_name",
                      f"Device names are 1-{DEVICE_NAME_MAX} printable characters.")
    return name


async def start(db: AsyncDatabase, device_name: str, code_challenge: str) -> tuple[dict, str]:
    if not _CHALLENGE.match(code_challenge or ""):
        raise Problem(422, "invalid_code_challenge",
                      "code_challenge must be the S256 challenge of a verifier.")
    name = clean_device_name(device_name)
    poll_token = new_secret()
    now = clock.now()
    for _ in range(5):
        doc = {"_id": uuid.uuid4().hex, "poll_hash": digest(poll_token),
               "challenge": code_challenge, "user_code": new_user_code(),
               "device_name": name, "status": "pending", "created_at": now,
               "expires_at": now + LIFETIME,
               "purge_at": now + LIFETIME + TRANSACTION_PURGE_AFTER,
               "decided_by": None, "decided_at": None, "approver_auth_at": None,
               "consumed_at": None}
        try:
            await db.device_authorizations.insert_one(doc)
            return doc, poll_token
        except DuplicateKeyError:
            continue  # user code collision; draw another
    raise Problem(503, "busy", "Could not allocate a pairing code; try again.")


async def pending_by_code(db: AsyncDatabase, user_code: str) -> dict | None:
    doc = await db.device_authorizations.find_one({"user_code": user_code})
    if doc is None or doc["status"] != "pending" or doc["expires_at"] <= clock.now():
        return None
    return doc


async def decide(db: AsyncDatabase, principal: sessions.Principal,
                 user_code: str, approve: bool) -> dict:
    now = clock.now()
    doc = await db.device_authorizations.find_one_and_update(
        {"user_code": user_code, "status": "pending", "expires_at": {"$gt": now}},
        {"$set": {"status": "approved" if approve else "denied",
                  "decided_by": principal.github_id, "decided_at": now,
                  "approver_auth_at": principal.session["auth_at"]}},
        return_document=ReturnDocument.AFTER)
    if doc is None:
        raise Problem(404, "pairing_not_found",
                      "No pending pairing has that code; it may have expired.")
    return doc


async def exchange(db: AsyncDatabase, poll_token: str, verifier: str) -> dict:
    """Returns {"status": "authorization_pending"} or the issued session, or
    raises (denied 403, expired 410, anything else 400 invalid_grant)."""
    if not poll_token or len(poll_token) > 128 or not verifier or len(verifier) > 128:
        raise invalid_grant()
    doc = await db.device_authorizations.find_one({"poll_hash": digest(poll_token)})
    if doc is None:
        raise invalid_grant()
    now = clock.now()
    status = doc["status"]
    if status in ("pending", "approved") and doc["expires_at"] <= now:
        raise Problem(410, "expired_token", "This pairing expired; start a new one.")
    if status == "pending":
        return {"status": "authorization_pending", "interval": POLL_INTERVAL_SEC}
    if status == "denied":
        raise forbidden("access_denied", "The pairing was denied.")
    if status != "approved":
        raise invalid_grant()  # consumed or failed: never twice
    if not same(pkce_challenge(verifier), doc["challenge"]):
        await db.device_authorizations.update_one(
            {"_id": doc["_id"], "status": "approved"},
            {"$set": {"status": "failed", "consumed_at": now}})
        raise invalid_grant()
    claimed = await db.device_authorizations.find_one_and_update(
        {"_id": doc["_id"], "status": "approved", "expires_at": {"$gt": now}},
        {"$set": {"status": "consumed", "consumed_at": now}},
        return_document=ReturnDocument.AFTER)
    if claimed is None:
        raise invalid_grant()
    team = await team_mod.get(db)
    member = (team or {}).get("members", {}).get(team_mod.key(claimed["decided_by"]))
    if team is None or member is None or member["state"] != "active":
        raise forbidden("membership_inactive",
                        "The person who approved this device no longer has access.")
    token, session = await sessions.issue(
        db, team, member, "device", auth_at=claimed["approver_auth_at"],
        device_name=claimed["device_name"])
    return {"status": "approved", "token": token, "session": session,
            "team": team, "member": member}
