"""GitHub login, bootstrap and admission."""
from __future__ import annotations

import asyncio
import logging
from urllib.parse import parse_qs, urlsplit

from conftest import (ADMIN, ALICE, MALLORY, SESSION_COOKIE, bearer,
                      browser_token, run_db, sign_in)
from teamserver import team as team_mod
from teamserver.config import Config
from teamserver.github import Identity


def test_configured_admin_creates_the_team(client, github):
    r = sign_in(client, github, ADMIN)
    assert r.status_code == 303 and r.headers["location"] == "/?welcome=1"
    cookie = r.headers["set-cookie"]
    for flag in ("Secure", "HttpOnly", "SameSite=lax", "Path=/"):
        assert flag.lower() in cookie.lower()
    home = client.get("/?welcome=1")
    assert "You created this team" in home.text and "Test Team" in home.text
    team = client.get("/api/v1/team").json()
    assert team["name"] == "Test Team" and team["member_count"] == 1
    me = client.get("/api/v1/me").json()
    assert me == {"github_id": ADMIN.id, "login": "tistocks", "name": "Tim",
                  "avatar_url": None, "role": "admin", "state": "active"}


def test_unrelated_account_never_bootstraps(client, github):
    r = sign_in(client, github, MALLORY)
    assert r.status_code == 403
    assert SESSION_COOKIE not in client.cookies
    assert client.get("/health/ready").json()["reason"] == "awaiting_bootstrap"


def test_unapproved_person_gets_no_session_and_no_team_data(client, github):
    browser_token(client, github, ADMIN)
    r = sign_in(client, github, MALLORY)
    assert r.status_code == 403
    assert "administrator must add you" in r.text
    assert SESSION_COOKIE not in client.cookies
    for path in ("/api/v1/team", "/api/v1/team/members", "/api/v1/me", "/api/v1/status"):
        assert client.get(path).status_code == 401


def test_invalid_or_replayed_state_creates_no_session(client, github):
    start = client.get("/auth/github/start", follow_redirects=False)
    state = parse_qs(urlsplit(start.headers["location"]).query)["state"][0]
    bad = client.get("/auth/github/callback",
                     params={"code": github.code_for(ADMIN), "state": "forged"},
                     follow_redirects=False)
    assert bad.status_code == 400
    ok = client.get("/auth/github/callback",
                    params={"code": github.code_for(ADMIN), "state": state},
                    follow_redirects=False)
    assert ok.status_code == 303
    client.cookies.clear()
    client.cookies.set("ts_oauth_binding", "x", domain="team.test", path="/auth/github")
    replay = client.get("/auth/github/callback",
                        params={"code": github.code_for(ADMIN), "state": state},
                        follow_redirects=False)
    assert replay.status_code == 400
    assert SESSION_COOKIE not in client.cookies


def test_state_is_bound_to_the_browser_that_started(client, github):
    start = client.get("/auth/github/start", follow_redirects=False)
    state = parse_qs(urlsplit(start.headers["location"]).query)["state"][0]
    client.cookies.clear()  # a different browser finishing the login
    r = client.get("/auth/github/callback",
                   params={"code": github.code_for(ADMIN), "state": state},
                   follow_redirects=False)
    assert r.status_code == 400 and github.identify_calls == 0


def test_expired_state_refused(client, github, frozen):
    start = client.get("/auth/github/start", follow_redirects=False)
    state = parse_qs(urlsplit(start.headers["location"]).query)["state"][0]
    frozen.advance(minutes=11)
    r = client.get("/auth/github/callback",
                   params={"code": github.code_for(ADMIN), "state": state},
                   follow_redirects=False)
    assert r.status_code == 400


def test_next_only_returns_to_known_pages(client, github):
    browser_token(client, github, ADMIN)
    r = sign_in(client, github, ADMIN, next_path="https://evil.example/")
    assert r.headers["location"] == "/"
    r = sign_in(client, github, ADMIN, next_path="//evil.example")
    assert r.headers["location"] == "/"
    r = sign_in(client, github, ADMIN, next_path="/device?code=BCDF-GHJK")
    assert r.headers["location"] == "/device?code=BCDF-GHJK"


def test_oauth_code_never_logged(client, github, caplog):
    caplog.set_level(logging.DEBUG)
    code = github.code_for(ADMIN)
    start = client.get("/auth/github/start", follow_redirects=False)
    state = parse_qs(urlsplit(start.headers["location"]).query)["state"][0]
    client.get("/auth/github/callback", params={"code": code, "state": state},
               follow_redirects=False)
    token = client.cookies.get(SESSION_COOKIE)
    # The test client's own request log (httpx) is not the server's.
    text = "\n".join(r.getMessage() for r in caplog.records
                     if not r.name.startswith("httpx"))
    assert code not in text and state not in text and token not in text


def test_restart_with_changed_admin_setting_changes_nothing(make_client, github):
    c = make_client()
    browser_token(c, github, ADMIN)
    c2 = make_client(initial_admin_github_id=MALLORY.id, team_name="Hijacked")
    r = sign_in(c2, github, MALLORY)
    assert r.status_code == 403
    token = browser_token(c2, github, ADMIN)
    team = c2.get("/api/v1/team", headers=bearer(token)).json()
    assert team["name"] == "Test Team" and team["member_count"] == 1
    roster = c2.get("/api/v1/team/members", headers=bearer(token)).json()
    assert [m["login"] for m in roster["members"]] == ["tistocks"]


def test_simultaneous_bootstraps_make_one_team(dbname):
    config = Config(port=1, public_base_url="https://t", mongodb_uri="", mongodb_database=dbname,
                    github_client_id="", github_client_secret="", team_name="Race",
                    initial_admin_github_id=ADMIN.id)

    async def race(db):
        others = [Identity(id=5000 + i, login=f"x{i}") for i in range(10)]
        results = await asyncio.gather(
            *[team_mod.bootstrap(db, config, "sid", ADMIN) for _ in range(10)],
            *[team_mod.bootstrap(db, config, "sid", o) for o in others],
            return_exceptions=True)
        teams = await db.team.count_documents({})
        doc = await team_mod.get(db)
        audits = await db.audit.count_documents({"action": "team_bootstrapped"})
        return results, teams, doc, audits

    results, teams, doc, audits = run_db(dbname, race)
    assert teams == 1 and audits == 1
    assert list(doc["members"]) == [str(ADMIN.id)]
    assert not any(isinstance(r, Exception) for r in results)


def test_suspended_member_cannot_sign_in(client, github):
    admin = browser_token(client, github, ADMIN)
    rev = client.get("/api/v1/team/members", headers=bearer(admin)).json()["revision"]
    r = client.put(f"/api/v1/team/members/{ALICE.id}", headers=bearer(admin),
                   json={"expected_revision": rev})
    assert r.status_code == 200
    browser_token(client, github, ALICE)
    rev = r.json()["revision"] + 1  # her first login activated her
    r = client.put(f"/api/v1/team/members/{ALICE.id}", headers=bearer(admin),
                   json={"expected_revision": rev, "status": "suspended"})
    assert r.status_code == 200, r.text
    r = sign_in(client, github, ALICE)
    assert r.status_code == 403 and "suspended" in r.text
