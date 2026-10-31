"""GitHub: identity-only OAuth App login (authorization code + PKCE S256) and
public account lookup.

The GitHub access token lives only inside `identify` and is dropped once
`GET /user` has answered. No scope is requested: the token can read public
profile data and nothing else.

Lookups authenticate as the OAuth App itself (HTTP Basic, client ID and
secret), which GitHub allows for public data at 5,000 requests an hour per
app; unauthenticated they share 60 an hour with everyone on the same
outbound IP. Any answer but 200 or 404 is logged in one line by `_refusal`:
the status, GitHub's rate-limit headers and request ID and the start of its
message, never the login looked up or any credential.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Optional, Protocol

import httpx2
from authlib.integrations.httpx_client import AsyncOAuth2Client
from authlib.common.errors import AuthlibBaseError
from pydantic import BaseModel

from teamserver import clock
from teamserver.config import Config
from teamserver.errors import Problem

log = logging.getLogger("teamserver.github")
TIMEOUT = httpx2.Timeout(10.0, connect=5.0)
MESSAGE_CHARS = 200


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


def github_rate_limited(retry_at: datetime) -> Problem:
    seconds = max(1, int((retry_at - clock.now()).total_seconds()) + 1)
    return Problem(503, "github_rate_limited",
                   "GitHub is limiting the team server's requests; try again after "
                   f"{retry_at:%H:%M} UTC.", retry_after=seconds)


def _int_header(resp: httpx2.Response, name: str) -> Optional[int]:
    try:
        return int(resp.headers[name])
    except (KeyError, ValueError):
        return None


def _reset_at(resp: httpx2.Response) -> Optional[datetime]:
    reset = _int_header(resp, "X-RateLimit-Reset")
    return None if reset is None else datetime.fromtimestamp(reset, timezone.utc)


def _retry_at(resp: httpx2.Response) -> Optional[datetime]:
    """When GitHub says to try again, if this answer is its rate limit:
    a 403 or 429 with no allowance left (until X-RateLimit-Reset), or any
    answer with Retry-After (that many seconds from now)."""
    retry_after = _int_header(resp, "Retry-After")
    if retry_after is not None:
        return clock.now() + timedelta(seconds=retry_after)
    if resp.status_code in (403, 429) and _int_header(resp, "X-RateLimit-Remaining") == 0:
        return _reset_at(resp) or clock.now() + timedelta(minutes=1)
    return None


def _message(resp: httpx2.Response, redact: tuple[str, ...]) -> str:
    try:
        text = str(resp.json().get("message") or "")
    except (ValueError, AttributeError):
        text = ""
    for secret in redact:
        if secret:
            text = re.sub(re.escape(secret), "[redacted]", text, flags=re.IGNORECASE)
    return text[:MESSAGE_CHARS]


def _refusal(resp: httpx2.Response, what: str, redact: tuple[str, ...] = ()) -> Problem:
    """Log one GitHub answer that was neither 200 nor 404 and say what to
    refuse with. Only GitHub's own headers and message are logged; anything
    in `redact` (the login looked up, the app's credentials) is taken out of
    the message first."""
    h = resp.headers
    reset = _reset_at(resp)
    log.warning("github %s answered %s: limit=%s remaining=%s used=%s resource=%s "
                "reset=%s retry_after=%s request_id=%s message=%r",
                what, resp.status_code, h.get("X-RateLimit-Limit"),
                h.get("X-RateLimit-Remaining"), h.get("X-RateLimit-Used"),
                h.get("X-RateLimit-Resource"),
                reset.strftime("%Y-%m-%dT%H:%M:%SZ") if reset else None,
                h.get("Retry-After"), h.get("X-GitHub-Request-Id"),
                _message(resp, redact))
    retry_at = _retry_at(resp)
    return github_rate_limited(retry_at) if retry_at else github_unavailable()


def _allowance(resp: httpx2.Response, what: str) -> None:
    h = resp.headers
    log.debug("github %s: %s of %s left (%s)", what, h.get("X-RateLimit-Remaining"),
              h.get("X-RateLimit-Limit"), h.get("X-RateLimit-Resource"))


class RealGitHub:
    def __init__(self, config: Config,
                 transport: Optional[httpx2.AsyncBaseTransport] = None):
        self._c = config
        self._transport = transport  # tests only: GitHub faked

    def _credentials(self) -> tuple[str, str]:
        return self._c.github_client_id, self._c.github_client_secret

    def _app_auth(self) -> Optional[httpx2.BasicAuth]:
        """The OAuth App's own Basic auth for lookups; none without
        credentials (`recover-admin`), which leaves them unauthenticated."""
        credentials = self._credentials()
        return httpx2.BasicAuth(*credentials) if all(credentials) else None

    def _oauth(self) -> AsyncOAuth2Client:
        return AsyncOAuth2Client(
            client_id=self._c.github_client_id,
            client_secret=self._c.github_client_secret,
            redirect_uri=self._c.callback_url,
            code_challenge_method="S256",
            token_endpoint_auth_method="client_secret_post",
            timeout=TIMEOUT,
            follow_redirects=False,
            transport=self._transport,
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
            raise _refusal(resp, "/user", self._credentials())
        _allowance(resp, "/user")
        return _identity(resp.json())

    async def _get(self, path: str, subject: str) -> Optional[Identity]:
        """One lookup, as the OAuth App. `subject` (the login or ID in
        `path`) is never logged."""
        try:
            async with httpx2.AsyncClient(timeout=TIMEOUT, follow_redirects=False,
                                         transport=self._transport) as client:
                resp = await client.get(
                    self._c.github_api_url + path,
                    auth=self._app_auth(),
                    headers={"Accept": "application/vnd.github+json",
                             "X-GitHub-Api-Version": "2022-11-28",
                             "User-Agent": "jarvis-team-server"})
        except httpx2.HTTPError as e:
            log.warning("github lookup failed: %s", type(e).__name__)
            raise github_unavailable() from None
        if resp.status_code == 404:
            return None
        if resp.status_code != 200:
            raise _refusal(resp, "lookup", (subject, *self._credentials()))
        _allowance(resp, "lookup")
        data = resp.json()
        if data.get("type") not in (None, "User"):
            return None  # organizations and bots cannot sign in
        return _identity(data)

    async def user_by_login(self, login: str) -> Optional[Identity]:
        return await self._get("/users/" + login, login)

    async def user_by_id(self, github_id: int) -> Optional[Identity]:
        return await self._get(f"/user/{int(github_id)}", str(int(github_id)))
