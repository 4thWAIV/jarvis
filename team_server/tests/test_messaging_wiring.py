"""The app's messaging wiring: the TEAM_SERVER_MESSAGING switch and file
settings in the configuration, the capability on /version, and the body
gate that lets the message send (128 KiB, counted by the route) and a
file's byte upload (counted by the route) past the 16 KiB limit."""
from __future__ import annotations

import pytest

from conftest import make_config
from teamserver import messaging
from teamserver.app import MAX_BODY
from teamserver.config import ConfigError, load
from teamserver.messaging_models import CAPABILITY, MESSAGE_REQUEST_MAX_BYTES

ENV = {"PORT": "8080", "PUBLIC_BASE_URL": "https://team.test",
       "MONGODB_URI": "mongodb://127.0.0.1:1", "MONGODB_DATABASE": "x",
       "GITHUB_CLIENT_ID": "x", "GITHUB_CLIENT_SECRET": "x"}


def test_messaging_is_off_unless_switched_on():
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
