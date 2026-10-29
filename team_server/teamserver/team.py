"""The team: one document holding the team and its roster.

Every roster change is one conditional update against the team's
`revision`, so two concurrent changes cannot both pass the rules checked
against the same read (in particular, "keep one active administrator").
Each change carries its audit event in the same update (`pending_audit`),
which is then drained idempotently into the `audit` collection.

Member states
  approved   an administrator added them; they have not signed in yet
  active     they have signed in at least once and are not suspended
  suspended  access withdrawn; their sessions were revoked

The team's `network_account_key` (random, 256 bits, base64url) is made the
first time a paired JARVIS asks for its status and kept on the team record.
JARVIS keys its brain account references with it, so one subscription used
on two machines counts once and no email leaves the machine. It is given
only to device sessions and is never logged.
"""
from __future__ import annotations

import logging
import uuid
from typing import Callable, Optional

from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import DuplicateKeyError

from teamserver import clock
from teamserver.config import Config
from teamserver.db import SCHEMA_VERSION, TEAM_ID
from teamserver.errors import Problem, conflict, forbidden, unavailable
from teamserver.github import Identity
from teamserver.secrets_util import new_secret

log = logging.getLogger("teamserver.team")

MEMBER_LIMIT = 100
PENDING_AUDIT_MAX = 50
MAX_RETRIES = 8
ROLES = ("admin", "member")


def key(github_id: int) -> str:
    return str(int(github_id))


def active_admins(members: dict) -> list[int]:
    return [m["github_id"] for m in members.values()
            if m["role"] == "admin" and m["state"] == "active"]


def check_invariants(members: dict) -> None:
    if not active_admins(members):
        raise conflict("last_admin",
                       "The team must keep at least one active administrator.")
    if len(members) > MEMBER_LIMIT:
        raise conflict("member_limit",
                       f"This server holds at most {MEMBER_LIMIT} members.")


def audit_event(action: str, actor: Optional[int], target: Optional[int],
                **detail) -> dict:
    return {"_id": uuid.uuid4().hex, "at": clock.now(), "action": action,
            "actor_github_id": actor, "target_github_id": target,
            "detail": detail}


def new_member(identity: Identity, role: str, state: str,
               added_by: Optional[int]) -> dict:
    now = clock.now()
    return {"github_id": identity.id, "login": identity.login,
            "name": identity.name, "avatar_url": identity.avatar_url,
            "role": role, "state": state, "session_generation": 1,
            "added_at": now, "added_by": added_by, "updated_at": now,
            "activated_at": now if state == "active" else None}


async def get(db: AsyncDatabase) -> Optional[dict]:
    return await db.team.find_one({"_id": TEAM_ID})


async def network_account_key(db: AsyncDatabase, team: dict) -> str:
    """The team's network account key, made once on first need. When two
    requests race to make it, the first write wins and both return it."""
    if team.get("network_account_key"):
        return team["network_account_key"]
    await db.team.update_one(
        {"_id": TEAM_ID, "network_account_key": {"$exists": False}},
        {"$set": {"network_account_key": new_secret()}})
    stored = await db.team.find_one({"_id": TEAM_ID}, {"network_account_key": 1})
    if not stored or not stored.get("network_account_key"):
        raise unavailable("team_missing", "The team has not been created.")
    return stored["network_account_key"]


async def drain_audit(db: AsyncDatabase) -> int:
    """Copy pending audit events into `audit`, then remove them from the team
    document. Idempotent: an event already copied is a duplicate key."""
    team = await db.team.find_one({"_id": TEAM_ID}, {"pending_audit": 1})
    pending = (team or {}).get("pending_audit") or []
    if not pending:
        return 0
    for event in pending:
        try:
            await db.audit.insert_one(event)
        except DuplicateKeyError:
            pass
    await db.team.update_one(
        {"_id": TEAM_ID},
        {"$pull": {"pending_audit": {"_id": {"$in": [e["_id"] for e in pending]}}}})
    return len(pending)


async def bootstrap(db: AsyncDatabase, config: Config, server_id: str,
                    identity: Identity) -> Optional[dict]:
    """Create the team for the configured first administrator, once.

    Returns the team (new or already existing), or None when there is no team
    and this identity is not the configured one. The fixed `_id` makes
    creation unique: a competing insert gets DuplicateKeyError and reads the
    winner's team.
    """
    existing = await get(db)
    if existing is not None:
        return existing
    if not config.bootstrap_configured or identity.id != config.initial_admin_github_id:
        return None
    member = new_member(identity, "admin", "active", added_by=None)
    event = audit_event("team_bootstrapped", identity.id, identity.id,
                        team_name=config.team_name, login=identity.login)
    doc = {"_id": TEAM_ID, "server_id": server_id,
           "schema_version": SCHEMA_VERSION, "name": config.team_name,
           "revision": 1, "session_epoch": 1,
           "bootstrap": {"completed": True, "at": clock.now(),
                         "admin_github_id": identity.id},
           "created_at": clock.now(),
           "members": {key(identity.id): member},
           "pending_audit": [event]}
    try:
        await db.team.insert_one(doc)
        log.info("team bootstrapped")
    except DuplicateKeyError:
        pass
    await drain_audit(db)
    return await get(db)


Change = Callable[[dict], tuple[dict, dict]]
"""Given a copy of the members map, return (new members, audit event) or
raise Problem."""


async def mutate(db: AsyncDatabase, change: Change,
                 expected_revision: Optional[int] = None) -> dict:
    """Apply a roster change atomically and return the updated team.

    With `expected_revision` the caller is acting on what it last saw: a
    different revision is 409 `revision_conflict` (the caller rereads).
    Without it, a lost race is retried against a fresh read, re-checking
    every rule.
    """
    for _ in range(MAX_RETRIES):
        team = await get(db)
        if team is None:
            raise unavailable("team_missing", "The team has not been created.")
        if expected_revision is not None and team["revision"] != expected_revision:
            raise conflict("revision_conflict",
                           "The roster changed; reload it and try again.",
                           revision=team["revision"])
        if len(team.get("pending_audit") or []) >= PENDING_AUDIT_MAX:
            await drain_audit(db)
            team = await get(db)
            if len(team.get("pending_audit") or []) >= PENDING_AUDIT_MAX:
                raise unavailable("audit_backlog",
                                  "Administrative changes are paused until "
                                  "the audit history catches up.")
        members = {k: dict(v) for k, v in team["members"].items()}
        new_members, event = change(members)
        check_invariants(new_members)
        result = await db.team.update_one(
            {"_id": TEAM_ID, "revision": team["revision"]},
            {"$set": {"members": new_members},
             "$inc": {"revision": 1},
             "$push": {"pending_audit": event}})
        if result.modified_count == 1:
            await drain_audit(db)
            return await get(db)
        if expected_revision is not None:
            current = await get(db)
            raise conflict("revision_conflict",
                           "The roster changed; reload it and try again.",
                           revision=(current or {}).get("revision"))
    raise conflict("busy", "The roster is changing too quickly; try again.")


async def admit(db: AsyncDatabase, config: Config, server_id: str,
                identity: Identity) -> tuple[dict, dict]:
    """Decide a GitHub login. Returns (team, member) for an admitted person,
    activating an approved entry on its first sign-in, or raises 403."""
    team = await bootstrap(db, config, server_id, identity)
    if team is None:
        raise forbidden("not_approved",
                        "This team server has not been set up yet.")
    member = team["members"].get(key(identity.id))
    if member is None:
        raise forbidden("not_approved",
                        "An administrator must add you to the team first.")
    if member["state"] == "suspended":
        raise forbidden("membership_suspended",
                        "Your access to this team is suspended.")
    if member["state"] == "approved":
        def activate(members: dict) -> tuple[dict, dict]:
            m = members.get(key(identity.id))
            if m is None:
                raise forbidden("not_approved",
                                "An administrator must add you to the team first.")
            if m["state"] == "suspended":
                raise forbidden("membership_suspended",
                                "Your access to this team is suspended.")
            if m["state"] == "approved":
                m.update(state="active", activated_at=clock.now(),
                         updated_at=clock.now(), login=identity.login,
                         name=identity.name, avatar_url=identity.avatar_url)
            members[key(identity.id)] = m
            return members, audit_event("member_activated", identity.id,
                                        identity.id, login=identity.login)
        # Activation does not depend on any other member, so it never fails
        # the active-administrator rule unless the roster already broke it.
        team = await mutate(db, activate)
        member = team["members"][key(identity.id)]
    return team, member


def change_member(actor: int, target: int, identity: Optional[Identity],
                  role: Optional[str], status: Optional[str]) -> Change:
    """An administrator's change to one roster entry (see PUT members)."""
    def apply(members: dict) -> tuple[dict, dict]:
        k = key(target)
        m = members.get(k)
        now = clock.now()
        if m is None:
            if status not in (None, "approved"):
                raise Problem(404, "not_a_member", "That person is not on the roster.")
            if identity is None:
                raise Problem(404, "github_user_not_found",
                              "No GitHub user has that ID.")
            if len(members) >= MEMBER_LIMIT:
                raise conflict("member_limit",
                               f"This server holds at most {MEMBER_LIMIT} members.")
            members[k] = new_member(identity, role or "member", "approved", actor)
            return members, audit_event("member_added", actor, target,
                                        login=identity.login, role=role or "member")
        before = {"role": m["role"], "state": m["state"]}
        if status == "removed":
            if m["state"] != "approved":
                raise conflict("member_was_active",
                               "Someone who has signed in can be suspended, not removed.")
            del members[k]
            return members, audit_event("member_removed", actor, target,
                                        login=m["login"], before=before)
        if role is not None:
            m["role"] = role
        if status == "suspended" and m["state"] != "suspended":
            m["state"] = "suspended"
            m["session_generation"] += 1
        elif status == "approved" and m["state"] == "suspended":
            # Re-enabling never revives sessions: the generation stays bumped.
            m["state"] = "active" if m.get("activated_at") else "approved"
        m["updated_at"] = now
        members[k] = m
        after = {"role": m["role"], "state": m["state"]}
        return members, audit_event("member_changed", actor, target,
                                    login=m["login"], before=before, after=after)
    return apply


def revoke_member_sessions(actor: Optional[int], target: int,
                           action: str = "sessions_revoked") -> Change:
    def apply(members: dict) -> tuple[dict, dict]:
        m = members.get(key(target))
        if m is None:
            raise Problem(404, "not_a_member", "That person is not on the roster.")
        m["session_generation"] += 1
        m["updated_at"] = clock.now()
        members[key(target)] = m
        return members, audit_event(action, actor, target, login=m["login"],
                                    session_generation=m["session_generation"])
    return apply
