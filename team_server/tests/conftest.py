"""Fixtures: a real MongoDB (one fresh database per test), a fake GitHub at
the adapter seam, a movable clock, and sign-in / pairing helpers.

MongoDB comes from TEAM_SERVER_TEST_MONGODB_URI. Without it the tests try
mongodb://127.0.0.1:27099 (`docker run -d -p 127.0.0.1:27099:27017 mongo`)
and skip if nothing answers; with it set, an unreachable database fails.
"""
from __future__ import annotations

import asyncio
import os
import time
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient
from pymongo import MongoClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from teamserver import clock  # noqa: E402
from teamserver.app import create_app  # noqa: E402
from teamserver.config import Config  # noqa: E402
from teamserver.github import Identity  # noqa: E402
from teamserver.ratelimit import RateLimiter  # noqa: E402
from teamserver.secrets_util import csrf_token, new_secret, pkce_challenge  # noqa: E402

BASE = "https://team.test"
DEFAULT_URI = "mongodb://127.0.0.1:27099"
ADMIN = Identity(id=863470, login="tistocks", name="Tim")
ALICE = Identity(id=1001, login="alice", name="Alice")
BOB = Identity(id=1002, login="bob", name="Bob")
MALLORY = Identity(id=6666, login="mallory", name="Mallory")


def mongo_uri() -> str:
    return os.environ.get("TEAM_SERVER_TEST_MONGODB_URI") or DEFAULT_URI


@pytest.fixture(scope="session")
def mongo() -> MongoClient:
    client = MongoClient(mongo_uri(), serverSelectionTimeoutMS=2000)
    try:
        client.admin.command("ping")
    except Exception:
        if os.environ.get("TEAM_SERVER_TEST_MONGODB_URI"):
            raise
        pytest.skip("no MongoDB at " + DEFAULT_URI)
    yield client
    client.close()


@pytest.fixture
def dbname(mongo) -> str:
    name = "ts_test_" + uuid.uuid4().hex[:12]
    yield name
    mongo.drop_database(name)


class Clock:
    def __init__(self):
        self.t = datetime.now(timezone.utc).replace(microsecond=0)

    def __call__(self) -> datetime:
        return self.t

    def advance(self, **kw) -> None:
        self.t += timedelta(**kw)


@pytest.fixture
def frozen(monkeypatch) -> Clock:
    c = Clock()
    monkeypatch.setattr(clock, "now", c)
    return c


class FakeGitHub:
    """The GitHub adapter seam. Codes are minted per identity; `identify`
    insists on the verifier `authorize_url` was given for that state."""

    def __init__(self, users=(ADMIN, ALICE, BOB, MALLORY)):
        self.users = {u.id: u for u in users}
        self._verifiers: dict[str, str] = {}
        self._codes: dict[str, Identity] = {}
        self.identify_calls = 0

    def authorize_url(self, state: str, code_verifier: str) -> str:
        self._verifiers[state] = code_verifier
        return f"https://github.test/login/oauth/authorize?state={state}"

    def code_for(self, identity: Identity) -> str:
        code = new_secret()
        self._codes[code] = identity
        return code

    async def identify(self, code: str, code_verifier: str) -> Identity:
        self.identify_calls += 1
        assert code_verifier in self._verifiers.values(), "PKCE verifier lost"
        identity = self._codes.pop(code, None)
        if identity is None:
            from teamserver.errors import Problem
            raise Problem(400, "github_login_failed", "bad code")
        return identity

    async def user_by_login(self, login: str) -> Optional[Identity]:
        return next((u for u in self.users.values()
                     if u.login.lower() == login.lower()), None)

    async def user_by_id(self, github_id: int) -> Optional[Identity]:
        return self.users.get(github_id)


def make_config(dbname: str, **over) -> Config:
    values = dict(port=8080, public_base_url=BASE, mongodb_uri=mongo_uri(),
                  mongodb_database=dbname, github_client_id="cid",
                  github_client_secret="not-a-secret", team_name="Test Team",
                  initial_admin_github_id=ADMIN.id)
    values.update(over)
    return Config(**values)


@pytest.fixture
def github() -> FakeGitHub:
    return FakeGitHub()


@pytest.fixture
def make_client(dbname, github):
    opened = []

    def make(**over) -> TestClient:
        app = create_app(make_config(dbname, **over), github=github,
                         limiter=RateLimiter(limits={k: (10_000, 60) for k in (
                             "login", "pairing_start", "pairing_exchange",
                             "pairing_decide", "admin")}))
        client = TestClient(app, base_url=BASE)
        client.__enter__()
        opened.append(client)
        wait_initialized(client)
        if over.get("messaging_enabled"):
            wait_messaging(client)
        return client
    yield make
    for c in opened:
        c.__exit__(None, None, None)


@pytest.fixture
def client(make_client) -> TestClient:
    return make_client()


def wait_initialized(client: TestClient) -> None:
    for _ in range(100):
        if client.app.state.initialized:
            return
        client.get("/health/live")
        time.sleep(0.05)
    raise AssertionError("service did not initialize")


def wait_messaging(client: TestClient) -> None:
    """Messaging on: its services are built once the database is ready."""
    for _ in range(100):
        if getattr(client.app.state, "attachments", None) is not None:
            return
        client.get("/health/live")
        time.sleep(0.05)
    raise AssertionError("messaging did not start")


SESSION_COOKIE = "__Host-ts_session"


def sign_in(client: TestClient, github: FakeGitHub, identity: Identity,
            next_path: Optional[str] = None):
    """Run the browser login. Returns the callback response; the session
    cookie (if any) is left in the client's jar."""
    params = {"next": next_path} if next_path else None
    start = client.get("/auth/github/start", params=params, follow_redirects=False)
    assert start.status_code == 302, start.text
    state = parse_qs(urlsplit(start.headers["location"]).query)["state"][0]
    return client.get("/auth/github/callback",
                      params={"code": github.code_for(identity), "state": state},
                      follow_redirects=False)


def browser_token(client: TestClient, github: FakeGitHub, identity: Identity) -> str:
    """Sign in and return the browser session token, clearing the jar."""
    client.cookies.clear()
    r = sign_in(client, github, identity)
    assert r.status_code == 303, r.text
    token = client.cookies.get(SESSION_COOKIE)
    assert token
    client.cookies.clear()
    return token


def browser_post(client: TestClient, token: str, path: str, json: dict):
    """A browser-cookie mutation with Origin and CSRF, as the page sends it."""
    client.cookies.clear()
    client.cookies.set(SESSION_COOKIE, token, domain="team.test")
    try:
        return client.post(path, json=json, headers={
            "Origin": BASE, "X-CSRF-Token": csrf_token(token)})
    finally:
        client.cookies.clear()


def bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def start_device(client: TestClient, name: str = "JARVIS on test-mac"):
    verifier = new_secret()
    r = client.post("/api/v1/device-authorizations",
                    json={"device_name": name, "code_challenge": pkce_challenge(verifier)})
    assert r.status_code == 200, r.text
    return r.json(), verifier


def pair(client: TestClient, browser: str) -> str:
    """A whole device pairing approved by the holder of `browser`; returns the
    device session token."""
    started, verifier = start_device(client)
    r = browser_post(client, browser, "/api/v1/device-authorizations/approve",
                     {"user_code": started["user_code"], "decision": "approve"})
    assert r.status_code == 200, r.text
    r = client.post("/api/v1/device-authorizations/exchange",
                    json={"poll_token": started["poll_token"], "code_verifier": verifier})
    assert r.status_code == 200, r.text
    return r.json()["session_token"]


def run_db(dbname: str, fn):
    """Run `fn(db)` (a coroutine function) against the test database on a
    fresh event loop and async client."""
    from teamserver import db as dbmod

    async def go():
        client = dbmod.connect(mongo_uri())
        try:
            db = dbmod.database(client, dbname)
            await dbmod.init(db)
            return await fn(db)
        finally:
            await client.close()
    return asyncio.run(go())
