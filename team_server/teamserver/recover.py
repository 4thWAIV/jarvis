"""Operator-only recovery, run with the database credentials (MONGODB_URI and
MONGODB_DATABASE in the environment), never reachable over HTTP.

  python -m teamserver.recover status
  python -m teamserver.recover recover-admin --github-id N --confirm-login LOGIN --reason TEXT
  python -m teamserver.recover revoke-all-sessions --reason TEXT

recover-admin restores an administrator who lost access: it looks the
numeric ID up on GitHub and refuses unless the account's current login is
the one the operator named, then makes that member an active administrator
and ends their earlier sessions. revoke-all-sessions ends every session on
the server (run it after restoring a backup, before people reconnect).
Both are recorded in the audit history. Neither reopens first-login
bootstrap.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys

from teamserver import clock, db as dbmod, team as team_mod
from teamserver.config import Config
from teamserver.db import TEAM_ID
from teamserver.github import Identity, RealGitHub


class RecoveryError(Exception):
    pass


def _public_github() -> RealGitHub:
    # Public lookups need no OAuth credentials; without them they are
    # unauthenticated (60 an hour per IP), enough for one recovery.
    return RealGitHub(Config(port=0, public_base_url="", mongodb_uri="",
                             mongodb_database="", github_client_id="",
                             github_client_secret="", team_name=None,
                             initial_admin_github_id=None))


async def _update_team(db, build) -> dict:
    """Apply `build(team) -> (update, event)` conditionally on revision."""
    for _ in range(team_mod.MAX_RETRIES):
        team = await team_mod.get(db)
        if team is None:
            raise RecoveryError("There is no team in this database: restore it from "
                                "backup, or configure bootstrap for a fresh installation.")
        update, event = build(team)
        update.setdefault("$inc", {})["revision"] = 1
        update["$push"] = {"pending_audit": event}
        result = await db.team.update_one({"_id": TEAM_ID, "revision": team["revision"]},
                                          update)
        if result.modified_count == 1:
            await team_mod.drain_audit(db)
            return await team_mod.get(db)
    raise RecoveryError("The team kept changing; try again.")


async def recover_admin(db, identity: Identity, reason: str) -> dict:
    def build(team):
        k = team_mod.key(identity.id)
        now = clock.now()
        m = team["members"].get(k)
        if m is None:
            m = team_mod.new_member(identity, "admin", "active", added_by=None)
        else:
            m = dict(m)
            m.update(role="admin", state="active", updated_at=now,
                     login=identity.login,
                     session_generation=m["session_generation"] + 1)
            m["activated_at"] = m.get("activated_at") or now
        return ({"$set": {f"members.{k}": m}},
                team_mod.audit_event("operator_recovered_admin", None, identity.id,
                                     login=identity.login, reason=reason,
                                     operator=True))
    return await _update_team(db, build)


async def revoke_all_sessions(db, reason: str) -> dict:
    def build(team):
        return ({"$inc": {"session_epoch": 1}},
                team_mod.audit_event("operator_revoked_all_sessions", None, None,
                                     reason=reason, operator=True,
                                     session_epoch=team["session_epoch"] + 1))
    return await _update_team(db, build)


def summary(team: dict | None) -> str:
    if team is None:
        return "team: none (not bootstrapped, or data missing)"
    members = team["members"].values()
    admins = sorted(m["login"] for m in members
                    if m["role"] == "admin" and m["state"] == "active")
    states = {}
    for m in members:
        states[m["state"]] = states.get(m["state"], 0) + 1
    boot = team.get("bootstrap") or {}
    return "\n".join([
        f"team: {team['name']}",
        f"server_id: {team['server_id']}",
        f"revision: {team['revision']}  session_epoch: {team['session_epoch']}",
        f"bootstrap: completed={boot.get('completed')} admin_github_id={boot.get('admin_github_id')}",
        f"members: {len(team['members'])} {states}",
        f"active admins: {', '.join(admins) or 'NONE'}",
        f"pending audit: {len(team.get('pending_audit') or [])}",
    ])


async def _main(args) -> int:
    uri, name = os.environ.get("MONGODB_URI"), os.environ.get("MONGODB_DATABASE")
    if not uri or not name:
        print("MONGODB_URI and MONGODB_DATABASE must be set", file=sys.stderr)
        return 2
    client = dbmod.connect(uri)
    try:
        db = dbmod.database(client, name)
        if args.command == "status":
            print(summary(await team_mod.get(db)))
            return 0
        if args.command == "recover-admin":
            identity = await _public_github().user_by_id(args.github_id)
            if identity is None:
                raise RecoveryError("GitHub has no user with that ID.")
            if identity.login.lower() != args.confirm_login.lower():
                raise RecoveryError(f"GitHub ID {args.github_id} is '{identity.login}', "
                                    f"not '{args.confirm_login}'. Nothing changed.")
            team = await recover_admin(db, identity, args.reason)
        else:
            team = await revoke_all_sessions(db, args.reason)
        print(summary(team))
        return 0
    except RecoveryError as e:
        print(f"refused: {e}", file=sys.stderr)
        return 1
    finally:
        await client.close()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m teamserver.recover")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status")
    ra = sub.add_parser("recover-admin")
    ra.add_argument("--github-id", type=int, required=True)
    ra.add_argument("--confirm-login", required=True)
    ra.add_argument("--reason", required=True)
    rv = sub.add_parser("revoke-all-sessions")
    rv.add_argument("--reason", required=True)
    return asyncio.run(_main(parser.parse_args(argv)))


if __name__ == "__main__":
    sys.exit(main())
