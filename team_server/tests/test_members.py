"""Administrator-managed membership."""
from __future__ import annotations

import asyncio

import pytest

from conftest import ADMIN, ALICE, BOB, MALLORY, bearer, browser_token, run_db, sign_in
from teamserver import team as team_mod
from teamserver.errors import Problem
from teamserver.github import Identity


def roster(client, token):
    r = client.get("/api/v1/team/members", headers=bearer(token))
    assert r.status_code == 200, r.text
    return r.json()


def put(client, token, github_id, **body):
    if "expected_revision" not in body:
        body["expected_revision"] = roster(client, token)["revision"]
    return client.put(f"/api/v1/team/members/{github_id}", headers=bearer(token), json=body)


def add_and_sign_in(client, github, admin, identity, role="member"):
    assert put(client, admin, identity.id, role=role).status_code == 200
    return browser_token(client, github, identity)


@pytest.fixture
def admin(client, github):
    return browser_token(client, github, ADMIN)


def test_resolve_then_add_member_then_first_login_activates(client, github, admin):
    r = client.post("/api/v1/team/members/resolve", headers=bearer(admin),
                    json={"login": "Alice"})
    assert r.status_code == 200
    resolved = r.json()
    assert resolved["github_id"] == ALICE.id and not resolved["on_roster"]
    r = put(client, admin, ALICE.id)
    assert r.status_code == 200
    assert r.json()["member"]["state"] == "approved" and r.json()["member"]["role"] == "member"
    alice = browser_token(client, github, ALICE)
    me = client.get("/api/v1/me", headers=bearer(alice)).json()
    assert me["state"] == "active" and me["role"] == "member"
    assert client.get("/api/v1/team", headers=bearer(alice)).json()["member_count"] == 2


def test_resolve_unknown_and_invalid_login(client, admin):
    r = client.post("/api/v1/team/members/resolve", headers=bearer(admin), json={"login": "nobody"})
    assert r.status_code == 404
    r = client.post("/api/v1/team/members/resolve", headers=bearer(admin),
                    json={"login": "../users/x"})
    assert r.status_code == 422


def test_members_cannot_manage_membership(client, github, admin):
    alice = add_and_sign_in(client, github, admin, ALICE)
    assert client.get("/api/v1/team/members", headers=bearer(alice)).status_code == 403
    assert client.post("/api/v1/team/members/resolve", headers=bearer(alice),
                       json={"login": "bob"}).status_code == 403
    r = client.put(f"/api/v1/team/members/{BOB.id}", headers=bearer(alice),
                   json={"expected_revision": 1})
    assert r.status_code == 403 and r.json()["error"]["code"] == "admin_required"
    r = client.post(f"/api/v1/team/members/{ADMIN.id}/revoke-sessions", headers=bearer(alice))
    assert r.status_code == 403


def test_stale_revision_is_409(client, admin):
    rev = roster(client, admin)["revision"]
    assert put(client, admin, ALICE.id, expected_revision=rev).status_code == 200
    r = put(client, admin, BOB.id, expected_revision=rev)
    assert r.status_code == 409 and r.json()["error"]["code"] == "revision_conflict"


def test_last_admin_cannot_demote_or_suspend_self(client, admin):
    r = put(client, admin, ADMIN.id, role="member")
    assert r.status_code == 409 and r.json()["error"]["code"] == "last_admin"
    r = put(client, admin, ADMIN.id, status="suspended")
    assert r.status_code == 409


def test_promote_and_demote(client, github, admin):
    alice = add_and_sign_in(client, github, admin, ALICE)
    assert put(client, admin, ALICE.id, role="admin").status_code == 200
    assert client.get("/api/v1/team/members", headers=bearer(alice)).status_code == 200
    # With two active admins one can step down.
    r = put(client, alice, ADMIN.id, role="member")
    assert r.status_code == 200 and r.json()["member"]["role"] == "member"
    assert client.get("/api/v1/team/members", headers=bearer(admin)).status_code == 403


def test_an_approved_but_never_signed_in_admin_does_not_count(client, admin):
    assert put(client, admin, ALICE.id, role="admin").status_code == 200
    r = put(client, admin, ADMIN.id, role="member")
    assert r.status_code == 409 and r.json()["error"]["code"] == "last_admin"


def test_suspension_ends_sessions_and_reenabling_does_not_revive(client, github, admin):
    alice = add_and_sign_in(client, github, admin, ALICE)
    assert client.get("/api/v1/status", headers=bearer(alice)).status_code == 200
    assert put(client, admin, ALICE.id, status="suspended").status_code == 200
    r = client.get("/api/v1/status", headers=bearer(alice))
    assert r.status_code == 401 and r.json()["error"]["code"] == "session_revoked"
    r = put(client, admin, ALICE.id, status="approved")
    assert r.json()["member"]["state"] == "active"
    assert client.get("/api/v1/status", headers=bearer(alice)).status_code == 401
    alice2 = browser_token(client, github, ALICE)
    assert client.get("/api/v1/status", headers=bearer(alice2)).status_code == 200


def test_revoke_sessions(client, github, admin):
    alice = add_and_sign_in(client, github, admin, ALICE)
    r = client.post(f"/api/v1/team/members/{ALICE.id}/revoke-sessions", headers=bearer(admin))
    assert r.status_code == 200 and r.json()["session_generation"] == 2
    assert client.get("/api/v1/me", headers=bearer(alice)).status_code == 401
    assert client.get("/api/v1/me", headers=bearer(admin)).status_code == 200


def test_remove_only_before_first_sign_in(client, github, admin):
    assert put(client, admin, ALICE.id).status_code == 200
    r = put(client, admin, ALICE.id, status="removed")
    assert r.status_code == 200 and r.json()["member"] is None
    assert sign_in(client, github, ALICE).status_code == 403
    add_and_sign_in(client, github, admin, BOB)
    r = put(client, admin, BOB.id, status="removed")
    assert r.status_code == 409 and r.json()["error"]["code"] == "member_was_active"


def test_unknown_github_id_cannot_be_added(client, admin):
    r = put(client, admin, 424242)
    assert r.status_code == 404


def test_admin_changes_need_recent_github_sign_in(client, github, admin, frozen):
    frozen.advance(minutes=16)
    r = put(client, admin, ALICE.id)
    assert r.status_code == 403 and r.json()["error"]["code"] == "reauth_required"
    # Reading is still allowed for the whole session.
    assert client.get("/api/v1/team/members", headers=bearer(admin)).status_code == 200
    fresh = browser_token(client, github, ADMIN)
    assert put(client, fresh, ALICE.id).status_code == 200


def test_member_limit(client, admin, dbname):
    async def fill(db):
        def add_many(members):
            for i in range(team_mod.MEMBER_LIMIT - len(members)):
                ident = Identity(id=200000 + i, login=f"m{i}")
                members[team_mod.key(ident.id)] = team_mod.new_member(ident, "member",
                                                                        "approved", ADMIN.id)
            return members, team_mod.audit_event("test_fill", ADMIN.id, None)
        await team_mod.mutate(db, add_many)
    run_db(dbname, fill)
    r = put(client, admin, ALICE.id)
    assert r.status_code == 409 and r.json()["error"]["code"] == "member_limit"


def test_concurrent_demotions_keep_one_active_admin(client, github, admin, dbname):
    add_and_sign_in(client, github, admin, ALICE, role="admin")

    async def race(db):
        async def demote(actor, target):
            return await team_mod.mutate(
                db, team_mod.change_member(actor, target, None, "member", None))
        results = await asyncio.gather(*[demote(ADMIN.id, ALICE.id), demote(ALICE.id, ADMIN.id)] * 5,
                                       return_exceptions=True)
        return results, await team_mod.get(db)

    results, team = run_db(dbname, race)
    assert team_mod.active_admins(team["members"]) in ([ADMIN.id], [ALICE.id])
    failures = [r for r in results if isinstance(r, Exception)]
    assert failures and all(isinstance(f, Problem) and f.code == "last_admin" for f in failures)


def test_concurrent_changes_with_same_revision_one_wins(client, github, admin, dbname):
    alice = add_and_sign_in(client, github, admin, ALICE, role="admin")
    rev = roster(client, admin)["revision"]

    async def race(db):
        async def change(actor, target, expected):
            return await team_mod.mutate(
                db, team_mod.change_member(actor, target, None, "member", None),
                expected_revision=expected)
        return await asyncio.gather(change(ADMIN.id, ALICE.id, rev),
                                    change(ALICE.id, ADMIN.id, rev),
                                    return_exceptions=True)

    results = run_db(dbname, race)
    assert sum(1 for r in results if isinstance(r, dict)) == 1
    assert sum(1 for r in results if isinstance(r, Problem) and r.status == 409) == 1


def test_every_change_is_audited(client, github, admin, mongo, dbname):
    put(client, admin, ALICE.id)
    browser_token(client, github, ALICE)
    put(client, admin, ALICE.id, role="admin")
    actions = [e["action"] for e in mongo[dbname].audit.find().sort("at", 1)]
    assert actions[0] == "team_bootstrapped"
    assert {"member_added", "member_activated", "member_changed"} <= set(actions)
    assert mongo[dbname].team.find_one()["pending_audit"] == []


def test_audit_backlog_pauses_admin_changes(client, admin, mongo, dbname, monkeypatch):
    async def never(db):
        return 0
    monkeypatch.setattr(team_mod, "drain_audit", never)
    filler = [{"_id": f"e{i}", "at": None, "action": "x"} for i in range(team_mod.PENDING_AUDIT_MAX)]
    mongo[dbname].team.update_one({"_id": "team"}, {"$set": {"pending_audit": filler}})
    r = put(client, admin, ALICE.id)
    assert r.status_code == 503 and r.json()["error"]["code"] == "audit_backlog"


def test_mallory_cannot_be_added_by_spoofed_body(client, admin):
    # The roster entry's login always comes from GitHub, not the caller.
    r = client.put(f"/api/v1/team/members/{MALLORY.id}", headers=bearer(admin),
                   json={"expected_revision": roster(client, admin)["revision"],
                         "login": "tistocks"})
    assert r.status_code == 200 and r.json()["member"]["login"] == "mallory"
