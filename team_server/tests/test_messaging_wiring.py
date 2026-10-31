"""The app's messaging wiring: always on (no switch; a switch stored before
is not read), the file settings in the configuration, the capability on
/version, and the body
gate that lets the message send (128 KiB, counted by the route) and a
file's byte upload (counted by the route) past the 16 KiB limit."""
from __future__ import annotations

import pytest

from conftest import run_db
from teamserver import messaging
from teamserver.app import MAX_BODY
from teamserver.config import ConfigError, load
from teamserver.messaging_models import CAPABILITY, MESSAGE_REQUEST_MAX_BYTES

ENV = {"PORT": "8080", "PUBLIC_BASE_URL": "https://team.test",
       "MONGODB_URI": "mongodb://127.0.0.1:1", "MONGODB_DATABASE": "x",
       "GITHUB_CLIENT_ID": "x", "GITHUB_CLIENT_SECRET": "x"}


def test_the_old_messaging_variable_changes_nothing():
    assert not hasattr(load({**ENV, "TEAM_SERVER_MESSAGING": "0"}), "messaging_enabled")


def test_file_settings_may_only_be_lowered_and_are_named_when_wrong():
    lowered = load({**ENV, "TEAM_SERVER_FILE_MAX_BYTES": "1048576"})
    assert lowered.attachment_settings.file_max_bytes == 1048576
    with pytest.raises(ConfigError) as e:
        load({**ENV, "TEAM_SERVER_FILE_MAX_BYTES": str(10 ** 12)})
    assert any("TEAM_SERVER_FILE_MAX_BYTES" in p for p in e.value.problems)


def offered(c) -> bool:
    return CAPABILITY in c.get("/version").json()["capabilities"]


def test_version_always_offers_messaging(make_client):
    assert offered(make_client())


def test_a_stored_switch_from_before_is_not_read(make_client, dbname):
    run_db(dbname, lambda db: db.team_settings.update_one(
        {"_id": "messaging"}, {"$set": {"enabled": False}}, upsert=True))
    c = make_client()
    assert offered(c)
    assert c.get(messaging.PREFIX + "/recipients").status_code == 401   # the session decides


def test_the_file_routes_check_the_session(make_client):
    c = make_client()
    r = c.post("/api/v1/attachments", json={})
    assert r.status_code in (401, 422)
    r = c.get("/api/v1/attachments/00000000-0000-4000-8000-000000000000",
              headers={"Authorization": "Bearer nothing"})
    assert r.status_code == 401        # the device session is checked first


def test_body_gate_lets_the_send_declare_up_to_its_own_limit(make_client):
    c = make_client()
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
    c = make_client()

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
    c = make_client()
    st = c.app.state
    assert st.messaging.attachments is st.attachment_storage
    built, st.messaging = st.messaging, None        # as before startup has built it
    r = c.get(messaging.PREFIX + "/recipients", headers={"Authorization": "Bearer nothing"})
    assert (r.status_code, r.json()["error"]["code"]) == (503, "not_ready")
    assert st.messaging is None, "a request never builds or caches a service"
    st.messaging = built
    assert c.get(messaging.PREFIX + "/recipients").status_code == 401
