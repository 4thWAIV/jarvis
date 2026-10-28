"""Versioned API models (API major 1). openapi.json beside the package is
generated from these and checked by the tests; clients build against it."""
from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field

GITHUB_LOGIN = r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?$"
Role = Literal["admin", "member"]
State = Literal["approved", "active", "suspended"]


class ErrorDetail(BaseModel):
    code: str
    message: str


class ErrorBody(BaseModel):
    error: ErrorDetail


class Version(BaseModel):
    service: str
    version: str
    commit: str
    api_version: int
    server_id: Optional[str]
    capabilities: list[str]


class Live(BaseModel):
    status: Literal["ok"]


class Ready(BaseModel):
    ready: bool
    reason: Optional[str] = None


class Member(BaseModel):
    github_id: int
    login: str
    name: Optional[str] = None
    avatar_url: Optional[str] = None
    role: Role
    state: State
    added_at: Optional[datetime] = None
    activated_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class Me(BaseModel):
    github_id: int
    login: str
    name: Optional[str] = None
    avatar_url: Optional[str] = None
    role: Role
    state: State


class Team(BaseModel):
    name: str
    server_id: str
    member_count: int
    member_limit: int


class Status(BaseModel):
    server_time: datetime
    server_id: str
    session_expires_at: datetime
    session_kind: Literal["browser", "device"]
    recent_auth_until: datetime
    team: Team
    me: Me


class Roster(BaseModel):
    revision: int
    member_limit: int
    members: list[Member]


class ResolveRequest(BaseModel):
    login: str = Field(pattern=GITHUB_LOGIN)


class Resolved(BaseModel):
    github_id: int
    login: str
    name: Optional[str] = None
    avatar_url: Optional[str] = None
    html_url: str
    on_roster: bool


class MemberChange(BaseModel):
    expected_revision: int = Field(ge=1)
    role: Optional[Role] = None
    status: Optional[Literal["approved", "suspended", "removed"]] = Field(
        default=None,
        description="approved: allow (new entry, or re-enable a suspended one); "
                    "suspended: withdraw access and end their sessions; "
                    "removed: delete an entry that never signed in.")


class MemberChanged(BaseModel):
    revision: int
    member: Optional[Member]


class SessionsRevoked(BaseModel):
    revision: int
    session_generation: int


class DeviceStart(BaseModel):
    device_name: str = Field(min_length=1, max_length=64)
    code_challenge: str = Field(min_length=43, max_length=43,
                                description="BASE64URL(SHA256(verifier)), no padding")
    code_challenge_method: Literal["S256"] = "S256"


class DeviceStarted(BaseModel):
    user_code: str
    verification_uri: str
    verification_uri_complete: str
    poll_token: str
    expires_at: datetime
    interval: int


class DeviceDecision(BaseModel):
    user_code: str = Field(min_length=8, max_length=9)
    decision: Literal["approve", "deny"]


class DeviceDecided(BaseModel):
    status: Literal["approved", "denied"]
    device_name: str


class DeviceExchange(BaseModel):
    poll_token: str = Field(min_length=1, max_length=128)
    code_verifier: str = Field(min_length=43, max_length=128)


class DevicePending(BaseModel):
    status: Literal["authorization_pending"]
    interval: int


class DeviceSession(BaseModel):
    status: Literal["approved"]
    session_token: str
    session_expires_at: datetime
    server_id: str
    team: Team
    me: Me


class LoggedOut(BaseModel):
    revoked: bool
