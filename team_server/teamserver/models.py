"""Versioned API models (API major 1). openapi.json beside the package is
generated from these and checked by the tests; clients build against it."""
from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional
from uuid import UUID

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
    team: Team
    me: Me
    network_account_key: Optional[str] = Field(
        default=None,
        description="The team's key for brain account references, base64url; "
                    "device sessions only, null for a browser session")


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


# -- the network (POST /api/v1/network/nodes/self) ---------------------------

Provider = Literal["anthropic", "openai"]
Measured = Literal["measured", "warming_up", "unsupported", "error"]
INT64_MAX = 2**63 - 1           # the largest integer BSON stores
WEEK_MS = 7 * 86_400_000        # a quota reading older than a week is useless


class Resources(BaseModel):
    state: Measured
    scope: Literal["host", "container"]
    cpu_percent: Optional[float] = Field(default=None, ge=0, le=100)
    memory_used_bytes: Optional[int] = Field(default=None, ge=0, le=INT64_MAX)
    memory_total_bytes: Optional[int] = Field(default=None, gt=0, le=INT64_MAX)
    age_ms: int = Field(ge=0, le=60_000)


class QuotaWindow(BaseModel):
    window: Literal["five_hour", "seven_day"]
    used_percent: float = Field(ge=0, le=100)
    expected_used_percent: Optional[float] = Field(default=None, ge=0, le=100)
    resets_at: datetime
    age_ms: int = Field(ge=0, le=WEEK_MS)


class BrainAccount(BaseModel):
    account_ref: str = Field(pattern=r"^[A-Za-z0-9_-]{16,64}$")  # keyed hash, never an email
    provider: Provider
    role: Literal["brain", "standby"]
    state: Literal["measured", "not_measured", "sign_in_needed"]
    windows: list[QuotaWindow] = Field(max_length=4)


class HumanActivity(BaseModel):
    activity_seq: int = Field(ge=0, le=INT64_MAX)
    age_ms: int = Field(ge=0, le=86_400_000)
    source: Literal["gui", "speech", "typed"]


class NodeUpdate(BaseModel):
    node_id: UUID
    boot_id: UUID
    seq: int = Field(ge=0, le=INT64_MAX)
    label: str = Field(min_length=1, max_length=64)
    jarvis_version: str = Field(max_length=32)
    os: str = Field(max_length=40)
    resources: Resources
    brains: list[BrainAccount] = Field(max_length=8)
    activity: Optional[HumanActivity] = None


class AccountPace(BaseModel):
    account_ref: str
    provider: Provider
    role: Literal["brain", "standby"]
    remaining_percent: Optional[float]
    pace_points: Optional[float]           # + more left than an even pace, − heading for the limit
    resets_at: Optional[datetime]
    left_at_reset_percent: Optional[float]  # at the current rate; None when it runs out first
    runs_out_hours_before_reset: Optional[float]
    age_ms: Optional[int]


class BrainSummary(BaseModel):
    configured: int
    measured: int
    average_remaining_percent: Optional[float]
    pace_points: Optional[float]            # the node tile's signed figure
    accounts: list[AccountPace]


class NodeView(BaseModel):
    node_id: UUID
    owner_github_id: int
    owner_login: str
    label: str
    jarvis_version: str
    os: str
    presence: Literal["online", "delayed", "offline"]
    last_seen_at: datetime
    resources: Resources
    brains: BrainSummary
    activity_state: Literal["never_observed", "available", "unsupported"]
    last_human_activity_at: Optional[datetime] = None
    last_human_activity_source: Optional[Literal["gui", "speech", "typed"]] = None


class HealthPoint(BaseModel):
    at: datetime
    database_ping_ms: Optional[float]
    write_ok: Optional[bool]


class ServerHealth(BaseModel):
    database: Literal["connected", "unreachable"]
    database_ping_ms: Optional[float]
    database_checked_at: datetime
    last_write_ok_at: Optional[datetime]
    last_database_error: Optional[str]      # error class name only
    database_failures_in_a_row: int
    uptime_sec: int
    version: str
    commit: str
    resources: Resources
    history: list[HealthPoint] = Field(max_length=30)


class NetworkSnapshot(BaseModel):
    revision: int
    server_time: datetime
    you_node_id: UUID
    nodes: list[NodeView]
    server: ServerHealth
