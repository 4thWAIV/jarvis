"""The staging test sign-in: only in staging with the key's digest set, a
wrong key is a missing route, test members are ordinary members, and their
sessions expire and revoke like anyone's."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from conftest import (ADMIN, BASE, bearer, browser_post, browser_token,
                      make_config, run_db)
from teamserver import staging_sessions
from teamserver.app import create_app
from teamserver.config import ConfigError, load
from teamserver.secrets_util import digest, new_secret

from test_health_config import GOOD

KEY = new_secret()
STAGING = {"environment_name": "staging", "test_session_key_sha256": digest(KEY)}
PATH = staging_sessions.PATH


@pytest.fixture
def staging(make_client) -> TestClient:
    return make_client(**STAGING)


def mint(client: TestClient, member: str = "test-a", key: str = KEY,
         device_name: str = "JARVIS test node"):
    return client.post(PATH, json={"member": member, "device_name": device_name},
                       headers=bearer(key))


def audit(dbname: str, action: str) -> list[dict]:
    async def read(db):
        return await db.audit.find({"action": action}).to_list(None)
    return run_db(dbname, read)


# -- configuration ------------------------------------------------------------

def test_digest_is_refused_outside_staging():
    for env in ({}, {"RAILWAY_ENVIRONMENT_NAME": "production"},
                {"RAILWAY_ENVIRONMENT_NAME": "Staging"}):
        with pytest.raises(ConfigError) as e:
            load(dict(GOOD, TEST_SESSION_KEY_SHA256=digest(KEY), **env))
        assert e.value.problems == [
            "TEST_SESSION_KEY_SHA256 is set outside the staging environment"]


def test_digest_must_be_a_sha256_and_never_shown():
    with pytest.raises(ConfigError) as e:
        load(dict(GOOD, RAILWAY_ENVIRONMENT_NAME="staging",
                  TEST_SESSION_KEY_SHA256="not-a-digest"))
    assert e.value.problems == ["TEST_SESSION_KEY_SHA256 must be a SHA-256 hex digest"]
    cfg = load(dict(GOOD, RAILWAY_ENVIRONMENT_NAME="staging",
                    TEST_SESSION_KEY_SHA256=digest(KEY).upper()))
    assert cfg.test_sessions_enabled
    assert digest(KEY) not in repr(cfg) + str(cfg)


def test_enabled_only_with_both_conditions():
    assert not load(GOOD).test_sessions_enabled
    assert not load(dict(GOOD, RAILWAY_ENVIRONMENT_NAME="staging")).test_sessions_enabled
    assert not make_config("x", environment_name="production",
                           test_session_key_sha256=digest(KEY)).test_sessions_enabled


def test_start_refused_with_the_digest_outside_staging(monkeypatch):
    for k, v in GOOD.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("RAILWAY_ENVIRONMENT_NAME", "production")
    monkeypatch.setenv("TEST_SESSION_KEY_SHA256", digest(KEY))
    with TestClient(create_app(), base_url=BASE) as c:
        r = c.get("/health/ready")
        assert r.status_code == 503 and r.json()["reason"] == "config_invalid"
        assert c.post(PATH, json={}, headers=bearer(KEY)).status_code == 404


# -- the route ------------------------------------------------------------------

@pytest.mark.parametrize("over", [{}, {"environment_name": "staging"},
                                  {"environment_name": "production",
                                   "test_session_key_sha256": digest(KEY)}])
def test_no_route_without_both_conditions(make_client, github, over):
    c = make_client(**over)
    browser_token(c, github, ADMIN)
    assert mint(c).status_code == 404
    assert PATH not in {r.path for r in c.app.routes}


def test_route_is_left_out_of_the_contract(staging):
    assert PATH in {r.path for r in staging.app.routes}
    assert PATH not in staging.app.openapi()["paths"]


def test_wrong_key_gets_the_same_404_as_a_missing_route(staging, github):
    browser_token(staging, github, ADMIN)
    missing = staging.post("/api/v1/test/no-such-route", json={})
    assert missing.status_code == 404
    for r in (mint(staging, key=new_secret()),
              staging.post(PATH, json={"member": "test-a", "device_name": "x"}),
              staging.post(PATH, json={"member": "test-a", "device_name": "x"},
                           headers={"Authorization": f"Basic {KEY}"}),
              staging.post(PATH, content=b"not json", headers=bearer(new_secret()))):
        assert r.status_code == 404
        assert r.json() == missing.json()
        assert r.headers["content-type"] == missing.headers["content-type"]


@pytest.mark.parametrize("key", [None, "wrong"])
def test_without_the_key_no_method_or_body_shows_the_route(staging, github, key):
    """Neither a 422 for a bad body nor a 405 for another method may come
    before the key is checked: both would show that the route exists."""
    browser_token(staging, github, ADMIN)
    headers = {} if key is None else bearer(new_secret())
    json_headers = {**headers, "Content-Type": "application/json"}
    missing = staging.post("/api/v1/test/no-such-route", json={})
    for r in (staging.post(PATH, content=b"{not json", headers=json_headers),
              staging.post(PATH, json={"member": "nobody"}, headers=headers),
              staging.get(PATH, headers=headers),
              staging.put(PATH, json={}, headers=headers),
              staging.delete(PATH, headers=headers),
              staging.options(PATH, headers=headers)):
        assert r.status_code == 404, (r.request.method, r.text)
        assert r.json() == missing.json()
    assert staging.head(PATH, headers=headers).status_code == 404


def test_with_the_key_only_post_is_the_route(staging, github):
    browser_token(staging, github, ADMIN)
    missing = staging.post("/api/v1/test/no-such-route", json={})
    r = staging.get(PATH, headers=bearer(KEY))
    assert r.status_code == 404 and r.json() == missing.json()


def test_with_the_key_a_bad_body_is_an_ordinary_422(staging, github):
    browser_token(staging, github, ADMIN)
    headers = {**bearer(KEY), "Content-Type": "application/json"}
    bad = staging.post(PATH, content=b"{not json", headers=headers)
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "invalid_request"
    r = staging.post(PATH, json={"member": "nobody", "device_name": "x"}, headers=bearer(KEY))
    assert r.status_code == 422 and r.json()["error"]["fields"] == ["member"]


def test_issues_an_ordinary_device_session_for_a_test_member(staging, github, dbname):
    browser_token(staging, github, ADMIN)
    r = mint(staging)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "approved"
    assert body["me"] == {"github_id": -1, "login": "test-a", "name": None,
                          "avatar_url": None, "role": "member", "state": "active"}
    token = body["session_token"]
    status = staging.get("/api/v1/status", headers=bearer(token)).json()
    assert status["session_kind"] == "device" and status["me"]["login"] == "test-a"
    # A second use is another session for the same roster entry.
    again = mint(staging, device_name="JARVIS test node 2")
    assert again.status_code == 200 and again.json()["session_token"] != token
    assert again.json()["team"]["member_count"] == 2
    assert mint(staging, member="test-b").json()["me"]["github_id"] == -2
    assert len(audit(dbname, "test_member_added")) == 2
    issued = audit(dbname, "test_session_issued")
    assert [e["detail"]["login"] for e in issued] == ["test-a", "test-a", "test-b"]
    assert all(KEY not in repr(e) and token not in repr(e) for e in issued)


def test_no_admin_can_be_minted(staging, github):
    browser_token(staging, github, ADMIN)
    for member in ("admin", ADMIN.login, "test-c"):
        r = mint(staging, member=member)
        assert r.status_code == 422, member
    token = mint(staging).json()["session_token"]
    r = staging.get("/api/v1/team/members", headers=bearer(token))
    assert r.status_code == 403 and r.json()["error"]["code"] == "admin_required"


def test_an_administrator_made_test_member_is_refused(staging, github, dbname):
    browser_token(staging, github, ADMIN)
    mint(staging)

    async def promote(db):
        await db.team.update_one({"_id": "team"},
                                 {"$set": {"members.-1.role": "admin"}})
    run_db(dbname, promote)
    r = mint(staging)
    assert r.status_code == 403 and r.json()["error"]["code"] == "test_member_unusable"


def test_needs_the_team(staging):
    r = mint(staging)
    assert r.status_code == 503 and r.json()["error"]["code"] == "team_missing"


def test_session_expires_after_12_hours(staging, github, frozen):
    browser_token(staging, github, ADMIN)
    token = mint(staging).json()["session_token"]
    frozen.advance(hours=11, minutes=59)
    assert staging.get("/api/v1/me", headers=bearer(token)).status_code == 200
    frozen.advance(minutes=1)
    r = staging.get("/api/v1/me", headers=bearer(token))
    assert r.status_code == 401 and r.json()["error"]["code"] == "session_expired"


def test_administrator_revokes_test_sessions(staging, github):
    admin = browser_token(staging, github, ADMIN)
    tokens = [mint(staging).json()["session_token"] for _ in range(2)]
    r = browser_post(staging, admin, "/api/v1/team/members/-1/revoke-sessions", {})
    assert r.status_code == 200, r.text
    for token in tokens:
        r = staging.get("/api/v1/me", headers=bearer(token))
        assert r.status_code == 401 and r.json()["error"]["code"] == "session_revoked"
    # A new use after revocation is a fresh session on the new generation.
    fresh = mint(staging).json()["session_token"]
    assert staging.get("/api/v1/me", headers=bearer(fresh)).status_code == 200
