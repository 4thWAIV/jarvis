"""Product sessions: opaque 256-bit credentials, stored only as SHA-256.

A session is valid while all of these hold, checked on every protected
request (never left to TTL cleanup):
  - not revoked, and `expires_at` (12 hours after issue) is in the future
  - the team exists and its `session_epoch` equals the session's
    (an operator recovery bumps the epoch: every session ends)
  - the member is on the roster with the session's `generation`
    (suspension or an administrator's revocation bumps it)
  - the member is not suspended
Role checks read the current roster, not the session.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Optional

from pymongo.asynchronous.database import AsyncDatabase

from teamserver import clock, team as team_mod
from teamserver.errors import forbidden, unauthenticated
from teamserver.secrets_util import digest, new_secret

SESSION_LIFETIME = timedelta(hours=12)
RECENT_AUTH = timedelta(minutes=15)


@dataclass
class Principal:
    token: str
    session: dict
    team: dict
    member: dict

    @property
    def github_id(self) -> int:
        return self.member["github_id"]

    @property
    def is_admin(self) -> bool:
        return self.member["role"] == "admin" and self.member["state"] == "active"

    @property
    def recently_authenticated(self) -> bool:
        return clock.now() - self.session["auth_at"] <= RECENT_AUTH


async def issue(db: AsyncDatabase, team: dict, member: dict, kind: str,
                auth_at, device_name: Optional[str] = None) -> tuple[str, dict]:
    token = new_secret()
    now = clock.now()
    doc = {"_id": digest(token), "github_id": member["github_id"],
           "generation": member["session_generation"],
           "epoch": team["session_epoch"], "kind": kind,
           "device_name": device_name, "created_at": now,
           "expires_at": now + SESSION_LIFETIME, "auth_at": auth_at,
           "revoked_at": None}
    await db.sessions.insert_one(doc)
    return token, doc


async def authenticate(db: AsyncDatabase, token: Optional[str]) -> Principal:
    if not token or len(token) > 128:
        raise unauthenticated("session_missing", "Sign in to continue.")
    session = await db.sessions.find_one({"_id": digest(token)})
    if session is None or session.get("revoked_at") is not None:
        raise unauthenticated("session_invalid", "Sign in again.")
    if session["expires_at"] <= clock.now():
        raise unauthenticated("session_expired", "Your session expired; sign in again.")
    team = await team_mod.get(db)
    if team is None or team.get("session_epoch") != session["epoch"]:
        raise unauthenticated("session_revoked", "Sign in again.")
    member = team["members"].get(team_mod.key(session["github_id"]))
    if member is None or member["session_generation"] != session["generation"]:
        raise unauthenticated("session_revoked", "Sign in again.")
    if member["state"] == "suspended":
        raise forbidden("membership_suspended", "Your access to this team is suspended.")
    return Principal(token=token, session=session, team=team, member=member)


async def revoke(db: AsyncDatabase, token: str) -> None:
    await db.sessions.update_one({"_id": digest(token), "revoked_at": None},
                                 {"$set": {"revoked_at": clock.now()}})
