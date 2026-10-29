"""Device pairing, sessions, logout and operator recovery."""
from __future__ import annotations

import re

from conftest import (ADMIN, ALICE, BASE, SESSION_COOKIE, bearer, browser_post,
                      browser_token, pair, run_db, start_device)
from teamserver import recover, team as team_mod
from teamserver.secrets_util import csrf_token


def exchange(client, started, verifier):
    return client.post("/api/v1/device-authorizations/exchange",
                       json={"poll_token": started["poll_token"], "code_verifier": verifier})


def decide(client, browser, started, decision="approve"):
    return browser_post(client, browser, "/api/v1/device-authorizations/approve",
                        {"user_code": started["user_code"], "decision": decision})


def test_full_pairing_issues_one_device_session(client, github):
    admin = browser_token(client, github, ADMIN)
    started, verifier = start_device(client)
    assert started["verification_uri"] == BASE + "/device"
    assert started["verification_uri_complete"].endswith(started["user_code"])
    assert started["interval"] == 5
    r = exchange(client, started, verifier)
    assert r.status_code == 202 and r.json()["status"] == "authorization_pending"
    assert decide(client, admin, started).json() == {"status": "approved",
                                                     "device_name": "JARVIS on test-mac"}
    r = exchange(client, started, verifier)
    assert r.status_code == 200
    body = r.json()
    assert body["me"]["login"] == "tistocks" and body["team"]["name"] == "Test Team"
    device = body["session_token"]
    status = client.get("/api/v1/status", headers=bearer(device)).json()
    assert status["session_kind"] == "device" and status["server_id"] == body["server_id"]
    # Replay: never a second session.
    r = exchange(client, started, verifier)
    assert r.status_code == 400 and r.json()["error"]["code"] == "invalid_grant"


def account_key(client, token):
    return client.get("/api/v1/status", headers=bearer(token)).json()["network_account_key"]


def test_device_session_gets_the_teams_stable_network_account_key(client, github, dbname):
    admin = browser_token(client, github, ADMIN)
    device = pair(client, admin)
    first = account_key(client, device)
    assert re.fullmatch(r"[A-Za-z0-9_-]{43}", first)
    assert account_key(client, device) == first
    stored = run_db(dbname, lambda db: db.team.find_one({"_id": "team"}))
    assert stored["network_account_key"] == first


def test_browser_session_gets_no_network_account_key(client, github, dbname):
    admin = browser_token(client, github, ADMIN)
    assert account_key(client, admin) is None
    # Asking from a browser does not make one.
    stored = run_db(dbname, lambda db: db.team.find_one({"_id": "team"}))
    assert "network_account_key" not in stored
    pair(client, admin)
    assert account_key(client, admin) is None


def test_two_device_sessions_of_one_team_get_the_same_key(client, github):
    admin = browser_token(client, github, ADMIN)
    one, two = pair(client, admin), pair(client, admin)
    assert one != two
    assert account_key(client, one) == account_key(client, two)


def test_device_page_shows_name_and_code_after_login(client, github):
    browser_token(client, github, ADMIN)
    started, _ = start_device(client, name="<script>alert(1)</script>")
    r = client.get("/device", params={"code": started["user_code"]}, follow_redirects=False)
    assert r.status_code == 302 and "/auth/github/start" in r.headers["location"]
    from conftest import sign_in
    r = sign_in(client, github, ADMIN, next_path=f"/device?code={started['user_code']}")
    page = client.get(r.headers["location"])
    assert started["user_code"] in page.text
    assert "<script>alert" not in page.text and "&lt;script&gt;" in page.text
    assert 'name="csrf-token"' in page.text


def test_wrong_verifier_fails_the_pairing(client, github):
    admin = browser_token(client, github, ADMIN)
    started, verifier = start_device(client)
    decide(client, admin, started)
    r = exchange(client, started, "x" * 43)
    assert r.status_code == 400
    r = exchange(client, started, verifier)
    assert r.status_code == 400  # the transaction is dead now


def test_denied_and_unknown_and_expired(client, github, frozen):
    admin = browser_token(client, github, ADMIN)
    started, verifier = start_device(client)
    decide(client, admin, started, "deny")
    r = exchange(client, started, verifier)
    assert r.status_code == 403 and r.json()["error"]["code"] == "access_denied"
    r = client.post("/api/v1/device-authorizations/exchange",
                    json={"poll_token": "nope", "code_verifier": verifier})
    assert r.status_code == 400
    started, verifier = start_device(client)
    decide(client, admin, started)
    frozen.advance(minutes=11)
    r = exchange(client, started, verifier)
    assert r.status_code == 410 and r.json()["error"]["code"] == "expired_token"
    started, verifier = start_device(client)
    frozen.advance(minutes=11)
    r = decide(client, admin, started)
    assert r.status_code == 404


def test_approval_needs_browser_session_origin_and_csrf(client, github):
    admin = browser_token(client, github, ADMIN)
    device = pair(client, admin)
    started, verifier = start_device(client)
    body = {"user_code": started["user_code"], "decision": "approve"}
    url = "/api/v1/device-authorizations/approve"
    r = client.post(url, json=body, headers=bearer(device))
    assert r.status_code == 403 and r.json()["error"]["code"] == "browser_required"
    client.cookies.set(SESSION_COOKIE, admin, domain="team.test")
    r = client.post(url, json=body, headers={"Origin": BASE})
    assert r.status_code == 403 and r.json()["error"]["code"] == "csrf_failed"
    r = client.post(url, json=body, headers={"Origin": "https://evil.example",
                                             "X-CSRF-Token": csrf_token(admin)})
    assert r.status_code == 403 and r.json()["error"]["code"] == "bad_origin"
    client.cookies.clear()
    assert exchange(client, started, verifier).status_code == 202


def test_unapproved_person_cannot_obtain_device_session(client, github):
    from conftest import sign_in, MALLORY
    browser_token(client, github, ADMIN)
    started, verifier = start_device(client)
    client.cookies.clear()
    assert sign_in(client, github, MALLORY).status_code == 403
    r = client.post("/api/v1/device-authorizations/approve",
                    json={"user_code": started["user_code"], "decision": "approve"},
                    headers={"Origin": BASE})
    assert r.status_code == 401
    assert exchange(client, started, verifier).status_code == 202


def test_session_expires_after_12_hours_even_if_ttl_has_not_run(client, github, frozen):
    admin = browser_token(client, github, ADMIN)
    frozen.advance(hours=11, minutes=59)
    assert client.get("/api/v1/me", headers=bearer(admin)).status_code == 200
    frozen.advance(minutes=2)
    r = client.get("/api/v1/me", headers=bearer(admin))
    assert r.status_code == 401 and r.json()["error"]["code"] == "session_expired"


def test_logout_revokes(client, github):
    admin = browser_token(client, github, ADMIN)
    device = pair(client, admin)
    r = client.post("/api/v1/logout", headers=bearer(device))
    assert r.status_code == 200 and r.json() == {"revoked": True}
    assert client.get("/api/v1/me", headers=bearer(device)).status_code == 401
    assert client.get("/api/v1/me", headers=bearer(admin)).status_code == 200
    r = browser_post(client, admin, "/api/v1/logout", {})
    assert r.status_code == 200
    assert client.get("/api/v1/me", headers=bearer(admin)).status_code == 401


def test_garbage_credentials(client, github):
    browser_token(client, github, ADMIN)
    for h in ({"Authorization": "Bearer "}, {"Authorization": "Basic abc"},
              {"Authorization": "Bearer " + "a" * 500}, {}):
        assert client.get("/api/v1/status", headers=h).status_code == 401


def test_recovery_restores_admin_and_ends_old_sessions(client, github, dbname):
    admin = browser_token(client, github, ADMIN)
    rev = client.get("/api/v1/team/members", headers=bearer(admin)).json()["revision"]
    client.put(f"/api/v1/team/members/{ALICE.id}", headers=bearer(admin),
               json={"expected_revision": rev})
    alice = browser_token(client, github, ALICE)

    team = run_db(dbname, lambda db: recover.recover_admin(db, ALICE, "test"))
    assert team["members"][str(ALICE.id)]["role"] == "admin"
    assert client.get("/api/v1/me", headers=bearer(alice)).status_code == 401
    assert client.get("/api/v1/me", headers=bearer(admin)).status_code == 200

    team = run_db(dbname, lambda db: recover.revoke_all_sessions(db, "restored backup"))
    assert team["session_epoch"] == 2
    assert client.get("/api/v1/me", headers=bearer(admin)).status_code == 401
    fresh = browser_token(client, github, ALICE)
    assert client.get("/api/v1/team/members", headers=bearer(fresh)).status_code == 200
    summary = recover.summary(team)
    assert "active admins: alice, tistocks" in summary

    async def actions(db):
        return [e["action"] async for e in db.audit.find()]
    acts = run_db(dbname, actions)
    assert "operator_recovered_admin" in acts and "operator_revoked_all_sessions" in acts


def test_recovery_refuses_without_team(dbname):
    import pytest
    with pytest.raises(recover.RecoveryError):
        run_db(dbname, lambda db: recover.revoke_all_sessions(db, "x"))
    assert team_mod is not None
