"""The app's messaging wiring: the team's stored switch (seeded once from
TEAM_SERVER_MESSAGING, then an administrator's), the file settings in the
configuration, the capability on /version, and the body
gate that lets the message send (128 KiB, counted by the route) and a
file's byte upload (counted by the route) past the 16 KiB limit."""
from __future__ import annotations

import time

import pytest

from conftest import ADMIN, bearer, browser_token, make_config, run_db
from teamserver import messaging
from teamserver.app import MAX_BODY
from teamserver.config import ConfigError, load
from teamserver.messaging_models import CAPABILITY, MESSAGE_REQUEST_MAX_BYTES

ENV = {"PORT": "8080", "PUBLIC_BASE_URL": "https://team.test",
       "MONGODB_URI": "mongodb://127.0.0.1:1", "MONGODB_DATABASE": "x",
       "GITHUB_CLIENT_ID": "x", "GITHUB_CLIENT_SECRET": "x"}


def test_messaging_is_seeded_off_unless_the_variable_is_on():
    assert load(ENV).messaging_enabled is False
    for on in ("1", "true", "yes", "on", "ON"):
        assert load({**ENV, "TEAM_SERVER_MESSAGING": on}).messaging_enabled is True
    assert load({**ENV, "TEAM_SERVER_MESSAGING": "0"}).messaging_enabled is False


def test_file_settings_may_only_be_lowered_and_are_named_when_wrong():
    lowered = load({**ENV, "TEAM_SERVER_FILE_MAX_BYTES": "1048576"})
    assert lowered.attachment_settings.file_max_bytes == 1048576
    with pytest.raises(ConfigError) as e:
        load({**ENV, "TEAM_SERVER_FILE_MAX_BYTES": str(10 ** 12)})
    assert any("TEAM_SERVER_FILE_MAX_BYTES" in p for p in e.value.problems)


def test_version_offers_messaging_only_when_switched_on(make_client):
    assert CAPABILITY not in make_client().get("/version").json()["capabilities"]
    assert CAPABILITY in make_client(messaging_enabled=True).get("/version").json()[
        "capabilities"]


def switch(c, admin, enabled=None):
    if enabled is None:
        return c.get("/api/v1/team/messaging", headers=bearer(admin))
    return c.put("/api/v1/team/messaging", headers=bearer(admin), json={"enabled": enabled})


def offered(c) -> bool:
    return CAPABILITY in c.get("/version").json()["capabilities"]


def test_the_variable_seeds_the_switch_once_and_never_overrides_it(make_client, github):
    c = make_client(messaging_enabled=True)
    admin = browser_token(c, github, ADMIN)
    r = switch(c, admin)
    assert r.status_code == 200
    assert r.json()["enabled"] is True and r.json()["changed_by_github_id"] is None
    assert switch(c, admin, False).json()["enabled"] is False
    again = make_client(messaging_enabled=True)          # restarted with the variable still on
    assert switch(again, admin).json()["enabled"] is False
    assert not offered(again)


def test_an_administrator_switches_messaging_live_and_it_is_audited(make_client, github,
                                                                    dbname):
    c = make_client()
    admin = browser_token(c, github, ADMIN)
    recipients = messaging.PREFIX + "/recipients"
    assert not offered(c)
    assert c.get(recipients).json()["error"]["code"] == "messaging_disabled"
    r = switch(c, admin, True)
    assert r.status_code == 200
    assert (r.json()["enabled"], r.json()["changed_by_github_id"]) == (True, ADMIN.id)
    assert offered(c)
    assert c.get(recipients).status_code == 401         # on: the session is checked now
    assert not switch(c, admin, False).json()["enabled"] and not offered(c)
    events = run_db(dbname, lambda db: db.audit.find(
        {"action": "messaging_switched"}).sort("at", 1).to_list())
    assert [(e["actor_github_id"], e["detail"]["enabled"]) for e in events] == [
        (ADMIN.id, True), (ADMIN.id, False)]


def test_the_switch_needs_a_session_and_setting_it_an_administrator(make_client):
    c = make_client()
    assert c.get("/api/v1/team/messaging").status_code == 401
    assert c.put("/api/v1/team/messaging", json={"enabled": True}).status_code == 401


def test_a_change_stored_elsewhere_reaches_this_instance(make_client, dbname, monkeypatch):
    monkeypatch.setattr(messaging, "SWITCH_REFRESH_SEC", 0.05)
    c = make_client()
    run_db(dbname, lambda db: messaging.set_switch(db, ADMIN.id, True))
    for _ in range(100):
        if offered(c):
            break
        time.sleep(0.05)
    assert offered(c)


def test_switched_off_file_routes_say_so(make_client):
    c = make_client()
    r = c.post("/api/v1/attachments", json={})
    assert r.status_code in (401, 422, 503)
    r = c.get("/api/v1/attachments/00000000-0000-4000-8000-000000000000",
              headers={"Authorization": "Bearer nothing"})
    assert r.status_code == 401        # the device session is checked first


def test_body_gate_lets_the_send_declare_up_to_its_own_limit(make_client):
    c = make_client(messaging_enabled=True)
    between = b"x" * (MAX_BODY + 1)
    r = c.post(messaging.PREFIX, content=between,
               headers={"Content-Type": "application/json"})
    assert r.status_code == 401                     # past the gate, refused for the session
    r = c.post(messaging.PREFIX, content=b"x" * (MESSAGE_REQUEST_MAX_BYTES + 1),
               headers={"Content-Type": "application/json"})
    assert (r.status_code, r.json()["error"]["code"]) == (413, "too_large")
    r = c.post("/api/v1/messages/receipts", content=between,
               headers={"Content-Type": "application/json"})
    assert (r.status_code, r.json()["error"]["code"]) == (413, "too_large")
    r = c.put("/api/v1/attachments/00000000-0000-4000-8000-000000000000/bytes",
              content=between)
    assert r.status_code == 401                     # the upload counts its own bytes


def test_body_gate_counts_a_chunked_body_it_cannot_see_declared(make_client):
    c = make_client(messaging_enabled=True)

    def chunks(total: int):
        for _ in range(total // 4096):
            yield b"x" * 4096

    # (The send reads its body only after the session, counting it itself.)
    for path, size in (("/api/v1/messages/receipts", 2 * 1024 * 1024),
                       ("/api/v1/attachments", MAX_BODY + 8192)):
        r = c.post(path, content=chunks(size), headers={"Content-Type": "application/json"})
        assert (r.status_code, r.json()["error"]["code"]) == (413, "too_large"), path
        assert r.headers["X-Content-Type-Options"] == "nosniff"
    r = c.post("/api/v1/messages/receipts", content=chunks(8192),
               headers={"Content-Type": "application/json"})
    assert r.status_code == 422                     # under the limit: read and judged



def test_the_service_is_built_once_at_startup_with_its_file_storage(make_client):
    c = make_client(messaging_enabled=True)
    st = c.app.state
    assert st.messaging.attachments is st.attachment_storage
    built, st.messaging = st.messaging, None        # as before startup has built it
    r = c.get(messaging.PREFIX + "/recipients", headers={"Authorization": "Bearer nothing"})
    assert (r.status_code, r.json()["error"]["code"]) == (503, "not_ready")
    assert st.messaging is None, "a request never builds or caches a service"
    st.messaging = built
    assert c.get(messaging.PREFIX + "/recipients").status_code == 401
