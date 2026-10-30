"""team_api.py — the Team section's routes, /api/team*, over team_client.

The page reads status (`GET /api/team`, polled while Settings is open),
starts and cancels pairing, disconnects, reads the team's messaging switch
and — for administrators — sets it and manages members, which is forwarded
to the team server with the session from the Keychain. No route ever returns the session credential. The routes sit
behind web_auth.OriginGuard like every other, and no access-key category
includes them.

`GET /api/team/network` is the Network view's one source: the last network
snapshot the team server sent, unchanged, in team_client's envelope.
`node_update` is what this backend sends it, from the local collectors
(host_resources, brain_accounts, human_activity), which no route returns.
The team lifecycle (`start`, `stop`) owns the status poll, the network
exchange and the host sampler.
"""
from __future__ import annotations

import asyncio
import importlib.metadata
import itertools
import platform
import socket
import uuid
from typing import Callable, Literal, Optional

from fastapi import FastAPI
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from jarvis import (brain_accounts, data_paths, host_resources, human_activity,
                    team_client)
from jarvis.team_client import TeamError

PREFIX = "/api/team"


class ConnectBody(BaseModel):
    server_url: str = Field(min_length=1, max_length=300)


class ResolveBody(BaseModel):
    login: str = Field(min_length=1, max_length=39)


class TeamSwitchBody(BaseModel):
    enabled: bool


class MemberBody(BaseModel):
    expected_revision: int = Field(ge=1)
    role: Optional[Literal["admin", "member"]] = None
    status: Optional[Literal["approved", "suspended", "removed"]] = None


def device_name() -> str:
    host = socket.gethostname().split(".")[0] or "this Mac"
    return f"JARVIS on {host}"[:64]


def jarvis_version() -> str:
    """This JARVIS's package version (NodeUpdate.jarvis_version, max 32)."""
    try:
        return importlib.metadata.version("jarvis")[:32]
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


def os_name() -> str:
    """The operating system, as `platform` words it (NodeUpdate.os, max 40)."""
    return platform.platform(terse=True)[:40]


# One per process: the team server takes a new boot ID as a restart, and
# refuses updates from an older one (data_paths.node_id is the lasting ID).
BOOT_ID = str(uuid.uuid4())
_update_seq = itertools.count()


def next_update_seq() -> int:
    """The next node update's `seq`: rising within this boot, from 0."""
    return next(_update_seq)


def node_update(account_key: Optional[str]) -> dict:
    """This backend's NodeUpdate for the team server's network, built from
    the local collectors; `account_key` is the team's key from the status
    (none yet: no brains)."""
    return {"node_id": data_paths.node_id(), "boot_id": BOOT_ID,
            "seq": next_update_seq(), "label": device_name(),
            "jarvis_version": jarvis_version(), "os": os_name(),
            "resources": host_resources.sampler.current(),
            "brains": brain_accounts.brains(account_key),
            "activity": human_activity.recorder.current()}


def _refused(e: TeamError, status: int = 400) -> JSONResponse:
    return JSONResponse({"error": {"code": "team", "message": str(e)}}, status_code=status)


async def _forward(method: str, path: str, body=None) -> JSONResponse:
    try:
        status, payload = await team_client.connection.call(method, path, body)
    except TeamError as e:
        return _refused(e, 502 if e.unreachable else 409)
    return JSONResponse(payload, status_code=status)


async def get_team():
    return team_client.connection.snapshot()


async def get_network():
    return Response(team_client.connection.network_status(), media_type="application/json")


async def connect(body: ConnectBody):
    try:
        return await team_client.connection.begin_pairing(body.server_url, device_name())
    except TeamError as e:
        return _refused(e)


async def cancel():
    await team_client.connection.cancel_pairing()
    return team_client.connection.snapshot()


async def disconnect():
    result = await team_client.connection.disconnect()
    return {**result, "team": team_client.connection.snapshot()}


async def refresh():
    await team_client.connection.poll_once()
    return team_client.connection.snapshot()


async def list_members():
    return await _forward("GET", "/api/v1/team/members")


async def resolve_member(body: ResolveBody):
    return await _forward("POST", "/api/v1/team/members/resolve", body.model_dump())


async def change_member(github_id: int, body: MemberBody):
    return await _forward("PUT", f"/api/v1/team/members/{int(github_id)}",
                          body.model_dump(exclude_none=True))


async def revoke_sessions(github_id: int):
    return await _forward("POST", f"/api/v1/team/members/{int(github_id)}/revoke-sessions")


async def get_team_messaging():
    return await _forward("GET", "/api/v1/team/messaging")


def mount(app: FastAPI, on_team_messaging: Callable[[], None] = lambda: None) -> None:
    """`on_team_messaging` runs after an administrator's change to the
    team's messaging switch was accepted, so this JARVIS asks the team
    server what it offers again instead of waiting for its cached answer."""

    async def set_team_messaging(body: TeamSwitchBody):
        answer = await _forward("PUT", "/api/v1/team/messaging", body.model_dump())
        if answer.status_code == 200:
            on_team_messaging()
        return answer

    app.add_api_route(PREFIX, get_team, methods=["GET"])
    app.add_api_route(PREFIX + "/network", get_network, methods=["GET"])
    app.add_api_route(PREFIX + "/connect", connect, methods=["POST"])
    app.add_api_route(PREFIX + "/cancel", cancel, methods=["POST"])
    app.add_api_route(PREFIX + "/disconnect", disconnect, methods=["POST"])
    app.add_api_route(PREFIX + "/refresh", refresh, methods=["POST"])
    app.add_api_route(PREFIX + "/messaging/team", get_team_messaging, methods=["GET"])
    app.add_api_route(PREFIX + "/messaging/team", set_team_messaging, methods=["PUT"])
    app.add_api_route(PREFIX + "/members", list_members, methods=["GET"])
    app.add_api_route(PREFIX + "/members/resolve", resolve_member, methods=["POST"])
    app.add_api_route(PREFIX + "/members/{github_id}", change_member, methods=["PUT"])
    app.add_api_route(PREFIX + "/members/{github_id}/revoke-sessions", revoke_sessions,
                      methods=["POST"])


_sampler: Optional[asyncio.Task] = None


def start(on_network: Optional[Callable[[], None]] = None) -> None:
    """The status poll, the network exchange and the host sampler, each its
    own task. `on_network` is told each time the network cache changes."""
    global _sampler
    if _sampler is None or _sampler.done():
        _sampler = asyncio.create_task(host_resources.sampler.run())
    team_client.connection.start(node_update=node_update, on_network=on_network)


async def stop() -> None:
    global _sampler
    await team_client.connection.stop()
    await team_client.cancel_task(_sampler)
    _sampler = None
