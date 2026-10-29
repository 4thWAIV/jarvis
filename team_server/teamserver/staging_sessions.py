"""The staging test sign-in: a device session for a synthetic test member,
with no GitHub login and no Approve click, so a workflow can pair test
backends on its own. Production can never have it.

The route (`POST /api/v1/test/device-sessions`) exists only when
`Config.test_sessions_enabled`: RAILWAY_ENVIRONMENT_NAME is `staging` and
TEST_SESSION_KEY_SHA256 is set (`config.load` refuses the digest anywhere
else). The bearer is the test key, compared by its SHA-256; a wrong or
missing key gets the same 404 as a route that does not exist. The route is
not in openapi.json.

Test members are ordinary `member` entries (never `admin`) with reserved
negative GitHub IDs, which no real GitHub account has. They are added on
first use. Their sessions are ordinary device sessions: the normal 12-hour
expiry, and revoked by an administrator's revoke-sessions like anyone's.
Every use is written to the audit history before the session is issued.
"""
from __future__ import annotations

from typing import Literal

from fastapi import HTTPException, Request
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, Field, ValidationError
from pymongo.asynchronous.database import AsyncDatabase

from teamserver import clock, pairing, sessions, team as team_mod
from teamserver.config import Config
from teamserver.errors import forbidden, unavailable
from teamserver.github import Identity
from teamserver.secrets_util import digest, same

PATH = "/api/v1/test/device-sessions"
METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]
StagingLogin = Literal["test-a", "test-b"]
MEMBERS: dict[str, Identity] = {
    "test-a": Identity(id=-1, login="test-a"),
    "test-b": Identity(id=-2, login="test-b"),
}


class StagingSessionRequest(BaseModel):
    member: StagingLogin
    device_name: str = Field(min_length=1, max_length=pairing.DEVICE_NAME_MAX)


def not_found() -> HTTPException:
    """Exactly what the router answers for a path it does not have."""
    return HTTPException(status_code=404)


def check_key(request: Request) -> None:
    """The route's first step: the bearer must be the test key."""
    config: Config = request.app.state.config
    scheme, _, key = (request.headers.get("authorization") or "").partition(" ")
    key = key.strip()
    if scheme.lower() != "bearer" or not key or len(key) > 128:
        raise not_found()
    if not same(digest(key), config.test_session_key_sha256 or ""):
        raise not_found()


def parse(body: bytes) -> StagingSessionRequest:
    """The request body, read only once the key has been checked. A bad body
    is the same 422 as any other route's (`app.on_invalid`)."""
    try:
        return StagingSessionRequest.model_validate_json(body)
    except ValidationError as exc:
        raise RequestValidationError(
            [{**e, "loc": ("body", *e["loc"])} for e in exc.errors(include_url=False)])


def _add(identity: Identity) -> team_mod.Change:
    def apply(members: dict) -> tuple[dict, dict]:
        k = team_mod.key(identity.id)
        if k not in members:
            members[k] = team_mod.new_member(identity, "member", "active", added_by=None)
        return members, team_mod.audit_event("test_member_added", None, identity.id,
                                             login=identity.login)
    return apply


async def issue(db: AsyncDatabase, login: str, device_name: str) -> tuple[str, dict, dict, dict]:
    """Returns (token, session, team, member) for test member `login`."""
    identity = MEMBERS[login]
    name = pairing.clean_device_name(device_name)
    team = await team_mod.get(db)
    if team is None:
        raise unavailable("team_missing", "The team has not been created.")
    k = team_mod.key(identity.id)
    if k not in team["members"]:
        team = await team_mod.mutate(db, _add(identity))
    member = team["members"][k]
    if member["role"] != "member" or member["state"] != "active":
        raise forbidden("test_member_unusable",
                        "This test member is not an active ordinary member.")
    await db.audit.insert_one(team_mod.audit_event(
        "test_session_issued", None, identity.id, login=identity.login,
        device_name=name))
    token, session = await sessions.issue(db, team, member, "device",
                                          auth_at=clock.now(), device_name=name)
    return token, session, team, member
