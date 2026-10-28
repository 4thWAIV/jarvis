"""team_api.py — the Team section's routes, /api/team*, over team_client.

The page reads status (`GET /api/team`, polled while Settings is open),
starts and cancels pairing, disconnects, and — for administrators — manages
members, which is forwarded to the team server with the session from the
Keychain. No route ever returns the session credential. The routes sit
behind web_auth.OriginGuard like every other, and no access-key category
includes them.
"""
from __future__ import annotations

import socket
from typing import Literal, Optional

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from jarvis import team_client
from jarvis.team_client import TeamError

PREFIX = "/api/team"


class ConnectBody(BaseModel):
    server_url: str = Field(min_length=1, max_length=300)


class ResolveBody(BaseModel):
    login: str = Field(min_length=1, max_length=39)


class MemberBody(BaseModel):
    expected_revision: int = Field(ge=1)
    role: Optional[Literal["admin", "member"]] = None
    status: Optional[Literal["approved", "suspended", "removed"]] = None


def device_name() -> str:
    host = socket.gethostname().split(".")[0] or "this Mac"
    return f"JARVIS on {host}"[:64]


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


def mount(app: FastAPI) -> None:
    app.add_api_route(PREFIX, get_team, methods=["GET"])
    app.add_api_route(PREFIX + "/connect", connect, methods=["POST"])
    app.add_api_route(PREFIX + "/cancel", cancel, methods=["POST"])
    app.add_api_route(PREFIX + "/disconnect", disconnect, methods=["POST"])
    app.add_api_route(PREFIX + "/refresh", refresh, methods=["POST"])
    app.add_api_route(PREFIX + "/members", list_members, methods=["GET"])
    app.add_api_route(PREFIX + "/members/resolve", resolve_member, methods=["POST"])
    app.add_api_route(PREFIX + "/members/{github_id}", change_member, methods=["PUT"])
    app.add_api_route(PREFIX + "/members/{github_id}/revoke-sessions", revoke_sessions,
                      methods=["POST"])


def start() -> None:
    team_client.connection.start()


async def stop() -> None:
    await team_client.connection.stop()
