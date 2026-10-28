"""GitHub: identity-only OAuth App login (authorization code + PKCE S256) and
public account lookup.

The GitHub access token lives only inside `identify` and is dropped once
`GET /user` has answered. No scope is requested: the token can read public
profile data and nothing else.
"""
from __future__ import annotations

import logging
from typing import Optional, Protocol

import httpx2
from authlib.integrations.httpx_client import AsyncOAuth2Client
from authlib.common.errors import AuthlibBaseError
from pydantic import BaseModel

from teamserver.config import Config
from teamserver.errors import Problem

log = logging.getLogger("teamserver.github")
TIMEOUT = httpx2.Timeout(10.0, connect=5.0)


class Identity(BaseModel):
    id: int
    login: str
    name: Optional[str] = None
    avatar_url: Optional[str] = None


class GitHub(Protocol):
    def authorize_url(self, state: str, code_verifier: str) -> str: ...
    async def identify(self, code: str, code_verifier: str) -> Identity: ...
    async def user_by_login(self, login: str) -> Optional[Identity]: ...
    async def user_by_id(self, github_id: int) -> Optional[Identity]: ...


def _identity(data: dict) -> Identity:
    return Identity(id=int(data["id"]), login=str(data["login"]),
                    name=data.get("name") or None,
                    avatar_url=data.get("avatar_url") or None)


def github_unavailable() -> Problem:
    return Problem(503, "github_unavailable",
                   "GitHub did not answer; try again shortly.")


class RealGitHub:
    def __init__(self, config: Config):
        self._c = config

    def _oauth(self) -> AsyncOAuth2Client:
        return AsyncOAuth2Client(
            client_id=self._c.github_client_id,
            client_secret=self._c.github_client_secret,
            redirect_uri=self._c.callback_url,
            code_challenge_method="S256",
            token_endpoint_auth_method="client_secret_post",
            timeout=TIMEOUT,
            follow_redirects=False,
        )

    def authorize_url(self, state: str, code_verifier: str) -> str:
        client = self._oauth()
        url, _ = client.create_authorization_url(
            self._c.github_web_url + "/login/oauth/authorize",
            state=state, code_verifier=code_verifier, allow_signup="false")
        return url

    async def identify(self, code: str, code_verifier: str) -> Identity:
        try:
            async with self._oauth() as client:
                token = await client.fetch_token(
                    self._c.github_web_url + "/login/oauth/access_token",
                    code=code, code_verifier=code_verifier,
                    headers={"Accept": "application/json"})
                access = token.get("access_token")
                if not access:
                    raise Problem(400, "github_login_failed",
                                  "GitHub did not complete the sign-in.")
                resp = await client.get(
                    self._c.github_api_url + "/user",
                    headers={"Accept": "application/vnd.github+json",
                             "X-GitHub-Api-Version": "2022-11-28"})
        except AuthlibBaseError as e:
            log.warning("github token exchange refused: %s", type(e).__name__)
            raise Problem(400, "github_login_failed",
                          "GitHub did not complete the sign-in.") from None
        except httpx2.HTTPError as e:
            log.warning("github unreachable during login: %s", type(e).__name__)
            raise github_unavailable() from None
        if resp.status_code != 200:
            log.warning("github /user answered %s", resp.status_code)
            raise github_unavailable()
        return _identity(resp.json())

    async def _get(self, path: str) -> Optional[Identity]:
        try:
            async with httpx2.AsyncClient(timeout=TIMEOUT,
                                         follow_redirects=False) as client:
                resp = await client.get(
                    self._c.github_api_url + path,
                    headers={"Accept": "application/vnd.github+json",
                             "X-GitHub-Api-Version": "2022-11-28",
                             "User-Agent": "jarvis-team-server"})
        except httpx2.HTTPError as e:
            log.warning("github lookup failed: %s", type(e).__name__)
            raise github_unavailable() from None
        if resp.status_code == 404:
            return None
        if resp.status_code != 200:
            log.warning("github lookup answered %s", resp.status_code)
            raise github_unavailable()
        data = resp.json()
        if data.get("type") not in (None, "User"):
            return None  # organizations and bots cannot sign in
        return _identity(data)

    async def user_by_login(self, login: str) -> Optional[Identity]:
        return await self._get("/users/" + login)

    async def user_by_id(self, github_id: int) -> Optional[Identity]:
        return await self._get(f"/user/{int(github_id)}")
