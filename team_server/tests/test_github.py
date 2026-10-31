"""The real GitHub adapter against a faked GitHub (httpx2.MockTransport):
lookups authenticate as the OAuth App, a rate limit is its own refusal, and
the refusal's log line carries GitHub's headers but no login or secret."""
from __future__ import annotations

import asyncio
import base64
import logging
from datetime import datetime, timedelta, timezone

import httpx2
import pytest

from conftest import ADMIN, bearer, browser_token, make_config
from teamserver.errors import Problem
from teamserver.github import RealGitHub

LOGIN = "octo-person"
RESET = datetime(2026, 10, 31, 23, 45, tzinfo=timezone.utc)
RATE_LIMITED = {"X-RateLimit-Limit": "60", "X-RateLimit-Remaining": "0",
                "X-RateLimit-Used": "60", "X-RateLimit-Resource": "core",
                "X-RateLimit-Reset": str(int(RESET.timestamp())),
                "X-GitHub-Request-Id": "ABCD:1234:5678"}
USER = {"id": 4242, "login": LOGIN, "name": "Octo", "type": "User"}


class FakeGitHubApi:
    """GitHub's API, answering every request with one response."""

    def __init__(self, status=200, headers=None, body=None):
        self.status, self.headers = status, headers or {}
        self.body = USER if body is None else body
        self.requests: list[httpx2.Request] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        return httpx2.Response(self.status, headers=self.headers, json=self.body)


def real(api: FakeGitHubApi) -> RealGitHub:
    return RealGitHub(make_config("unused", github_client_secret="s3cret-value"),
                      transport=httpx2.MockTransport(api))


def lookup(api: FakeGitHubApi):
    return asyncio.run(real(api).user_by_login(LOGIN))


def refusal(api: FakeGitHubApi) -> Problem:
    with pytest.raises(Problem) as e:
        lookup(api)
    return e.value


def test_lookup_sends_the_apps_basic_auth():
    api = FakeGitHubApi()
    assert lookup(api).id == 4242
    expected = "Basic " + base64.b64encode(b"cid:s3cret-value").decode()
    assert api.requests[0].headers["Authorization"] == expected
    assert api.requests[0].url.path == "/users/" + LOGIN


def test_lookup_by_id_sends_the_apps_basic_auth():
    api = FakeGitHubApi()
    asyncio.run(real(api).user_by_id(4242))
    assert api.requests[0].headers["Authorization"].startswith("Basic ")


def test_no_credentials_no_auth():
    api = FakeGitHubApi()
    github = RealGitHub(make_config("unused", github_client_id="", github_client_secret=""),
                        transport=httpx2.MockTransport(api))
    asyncio.run(github.user_by_login(LOGIN))
    assert "Authorization" not in api.requests[0].headers


def test_not_found_is_none():
    assert lookup(FakeGitHubApi(404, body={"message": "Not Found"})) is None


def test_rate_limited_403_says_when_to_try_again(frozen):
    frozen.t = RESET - timedelta(minutes=14)
    p = refusal(FakeGitHubApi(403, RATE_LIMITED,
                              {"message": "API rate limit exceeded for 1.2.3.4."}))
    assert (p.status, p.code) == (503, "github_rate_limited")
    assert "23:45 UTC" in p.message
    assert p.extra["retry_after"] == 14 * 60 + 1


def test_retry_after_is_a_rate_limit(frozen):
    p = refusal(FakeGitHubApi(429, {"Retry-After": "120"},
                              {"message": "You have exceeded a secondary rate limit."}))
    assert p.code == "github_rate_limited"
    assert f"{frozen.t + timedelta(seconds=120):%H:%M} UTC" in p.message


@pytest.mark.parametrize("status,headers", [
    (403, {"X-RateLimit-Remaining": "4999"}),  # a refusal with allowance left
    (401, {}),
    (500, {}),
    (502, RATE_LIMITED),  # an error, not a rate limit, whatever the headers
])
def test_other_answers_are_unavailable(status, headers):
    p = refusal(FakeGitHubApi(status, headers, {"message": "nope"}))
    assert (p.status, p.code) == (503, "github_unavailable")


def test_refusal_log_line_has_the_headers_and_no_login_or_secret(caplog):
    caplog.set_level(logging.DEBUG)
    refusal(FakeGitHubApi(403, RATE_LIMITED,
                          {"message": f"Rate limit for {LOGIN.upper()} via cid s3cret-value"}))
    lines = [r.getMessage() for r in caplog.records if r.name == "teamserver.github"]
    assert len(lines) == 1
    line = lines[0]
    for part in ("answered 403", "limit=60", "remaining=0", "used=60", "resource=core",
                 "reset=2026-10-31T23:45:00Z", "request_id=ABCD:1234:5678",
                 "message='Rate limit for [redacted] via [redacted] [redacted]'"):
        assert part in line, part
    # The HTTP client's own INFO lines name the URL; __main__ keeps them out.
    every = "\n".join(r.getMessage() for r in caplog.records
                      if r.name.startswith("teamserver"))
    assert LOGIN not in every.lower() and "s3cret-value" not in every
    assert base64.b64encode(b"cid:s3cret-value").decode() not in every


def test_log_message_is_cut_to_200_characters(caplog):
    refusal(FakeGitHubApi(500, {}, {"message": "x" * 500}))
    line = next(r.getMessage() for r in caplog.records if r.name == "teamserver.github")
    assert "x" * 200 in line and "x" * 201 not in line


def test_success_logs_the_allowance_at_debug(caplog):
    caplog.set_level(logging.DEBUG, logger="teamserver.github")
    lookup(FakeGitHubApi(200, {**RATE_LIMITED, "X-RateLimit-Remaining": "4321",
                               "X-RateLimit-Limit": "5000"}))
    debug = [r for r in caplog.records if r.name == "teamserver.github"]
    assert [r.levelno for r in debug] == [logging.DEBUG]
    assert "4321 of 5000 left" in debug[0].getMessage()


def test_resolve_route_returns_the_rate_limit(client, github, frozen):
    admin = browser_token(client, github, ADMIN)
    frozen.t = RESET - timedelta(minutes=5)
    github.user_by_login = real(FakeGitHubApi(403, RATE_LIMITED,
                                              {"message": "API rate limit exceeded"})).user_by_login
    r = client.post("/api/v1/team/members/resolve", headers=bearer(admin),
                    json={"login": LOGIN})
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "github_rate_limited"
    assert "try again after 23:45 UTC" in r.json()["error"]["message"]
    assert r.headers["Retry-After"] == str(5 * 60 + 1)
