"""GitHub login transactions: one-use state, PKCE verifier, browser binding.

`begin` stores a transaction keyed by SHA-256 of the random `state`, holding
the PKCE verifier and the SHA-256 of a random binding value that goes to the
browser as an HttpOnly cookie. `complete` consumes the transaction
atomically (once), checks the cookie came back from the same browser, and
only then lets the code be exchanged. The verifier is cleared when the
transaction is consumed; the document expires after ten minutes.
"""
from __future__ import annotations

import re
from datetime import timedelta
from typing import Optional

from pymongo import ReturnDocument
from pymongo.asynchronous.database import AsyncDatabase

from teamserver import clock
from teamserver.db import TRANSACTION_PURGE_AFTER
from teamserver.errors import Problem
from teamserver.secrets_util import digest, new_secret, same

LIFETIME = timedelta(minutes=10)
# Where a login may return to: the home page or the device page with a code.
_NEXT = re.compile(r"^/(device(\?code=[A-Za-z-]{1,12})?)?$")


def safe_next(value: Optional[str]) -> str:
    return value if value and _NEXT.match(value) else "/"


def invalid_state() -> Problem:
    return Problem(400, "invalid_state",
                   "This sign-in link is no longer valid; start again.")


async def begin(db: AsyncDatabase, next_path: Optional[str]) -> tuple[str, str, str]:
    """Returns (state, code_verifier, binding)."""
    state, verifier, binding = new_secret(), new_secret(), new_secret()
    now = clock.now()
    await db.oauth_states.insert_one({
        "_id": digest(state), "binding_hash": digest(binding),
        "code_verifier": verifier, "next": safe_next(next_path),
        "created_at": now, "expires_at": now + LIFETIME,
        "purge_at": now + LIFETIME + TRANSACTION_PURGE_AFTER,
        "consumed_at": None})
    return state, verifier, binding


async def complete(db: AsyncDatabase, state: Optional[str],
                   binding: Optional[str]) -> tuple[str, str]:
    """Consume the transaction. Returns (code_verifier, next path)."""
    if not state or len(state) > 128 or not binding or len(binding) > 128:
        raise invalid_state()
    now = clock.now()
    doc = await db.oauth_states.find_one_and_update(
        {"_id": digest(state), "consumed_at": None, "expires_at": {"$gt": now}},
        {"$set": {"consumed_at": now, "code_verifier": None}},
        return_document=ReturnDocument.BEFORE)
    if doc is None or not same(digest(binding), doc["binding_hash"]):
        raise invalid_state()
    return doc["code_verifier"], doc["next"]
