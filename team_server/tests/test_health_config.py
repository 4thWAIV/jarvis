"""Configuration validation, health, readiness and version."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from conftest import ADMIN, BASE, browser_token, make_config
from teamserver import build_info
from teamserver.app import create_app
from teamserver.config import ConfigError, load

GOOD = {"PORT": "8080", "PUBLIC_BASE_URL": "https://team.example",
        "MONGODB_URI": "mongodb+srv://user:hunter2@cluster.example/",
        "MONGODB_DATABASE": "teamdb", "GITHUB_CLIENT_ID": "cid",
        "GITHUB_CLIENT_SECRET": "very-secret-value"}


def test_valid_config_loads_and_repr_hides_credentials():
    cfg = load(GOOD)
    assert cfg.callback_url == "https://team.example/auth/github/callback"
    assert not cfg.bootstrap_configured
    text = repr(cfg) + str(cfg)
    assert "hunter2" not in text and "very-secret-value" not in text


def test_missing_values_are_named_never_shown():
    env = dict(GOOD, GITHUB_CLIENT_SECRET="", MONGODB_URI="")
    with pytest.raises(ConfigError) as e:
        load(env)
    assert "GITHUB_CLIENT_SECRET is missing" in e.value.problems
    assert "MONGODB_URI is missing" in e.value.problems


@pytest.mark.parametrize("url", ["http://team.example", "https://team.example/path",
                                 "ftp://x", "https://u:p@team.example"])
def test_base_url_must_be_an_https_origin(url):
    with pytest.raises(ConfigError):
        load(dict(GOOD, PUBLIC_BASE_URL=url))


def test_plain_http_only_for_loopback_with_dev_flag():
    with pytest.raises(ConfigError):
        load(dict(GOOD, PUBLIC_BASE_URL="http://127.0.0.1:8080"))
    cfg = load(dict(GOOD, PUBLIC_BASE_URL="http://127.0.0.1:8080",
                    TEAM_SERVER_DEV_INSECURE="1"))
    assert not cfg.secure
    with pytest.raises(ConfigError):
        load(dict(GOOD, PUBLIC_BASE_URL="http://10.0.0.5", TEAM_SERVER_DEV_INSECURE="1"))


def test_bootstrap_needs_numeric_id_and_team_name():
    with pytest.raises(ConfigError):
        load(dict(GOOD, INITIAL_ADMIN_GITHUB_ID="tistocks", TEAM_NAME="T"))
    with pytest.raises(ConfigError):
        load(dict(GOOD, INITIAL_ADMIN_GITHUB_ID="863470"))
    cfg = load(dict(GOOD, INITIAL_ADMIN_GITHUB_ID="863470", TEAM_NAME="T"))
    assert cfg.bootstrap_configured and cfg.initial_admin_github_id == 863470


def test_invalid_config_serves_liveness_but_not_readiness(monkeypatch):
    for k in GOOD:
        monkeypatch.delenv(k, raising=False)
    with TestClient(create_app(), base_url=BASE) as c:
        assert c.get("/health/live").json() == {"status": "ok"}
        r = c.get("/health/ready")
        assert r.status_code == 503 and r.json()["reason"] == "config_invalid"


def test_ready_awaiting_bootstrap_then_ready(client, github):
    r = client.get("/health/ready")
    assert r.status_code == 200 and r.json() == {"ready": True, "reason": "awaiting_bootstrap"}
    browser_token(client, github, ADMIN)
    assert client.get("/health/ready").json() == {"ready": True, "reason": None}


def test_missing_team_without_bootstrap_is_recovery_problem(make_client):
    c = make_client(initial_admin_github_id=None)
    r = c.get("/health/ready")
    assert r.status_code == 503 and r.json()["reason"] == "recovery_required"


def test_database_unavailable_is_503_and_fails_closed(github, dbname):
    cfg = make_config(dbname, mongodb_uri="mongodb://127.0.0.1:1/?serverSelectionTimeoutMS=500")
    with TestClient(create_app(cfg, github=github), base_url=BASE) as c:
        r = c.get("/health/ready")
        assert r.status_code == 503 and r.json()["reason"] == "database_unavailable"
        # Authorization fails closed, never open.
        r = c.get("/api/v1/me", headers={"Authorization": "Bearer whatever"})
        assert r.status_code == 503
        r = c.get("/auth/github/start", follow_redirects=False)
        assert r.status_code == 503


def test_version_reports_build_and_omits_messaging(client):
    v = client.get("/version").json()
    assert v["service"] == "jarvis-team-server"
    assert v["api_version"] == 1
    assert v["version"] == build_info.VERSION
    assert v["server_id"]
    assert set(v["capabilities"]) == {"github-login", "membership", "device-pairing",
                                      "status", "network"}
    assert not any("messag" in c or "presence" in c for c in v["capabilities"])


def test_server_id_is_stable_across_restarts(make_client):
    first = make_client().get("/version").json()["server_id"]
    second = make_client().get("/version").json()["server_id"]
    assert first == second


def test_security_headers_and_no_store(client):
    r = client.get("/version")
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"]
    assert r.headers["cache-control"] == "no-store"
    assert r.headers["referrer-policy"] == "no-referrer"
    assert r.headers["strict-transport-security"]


def test_no_interactive_docs(client):
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert client.get(path).status_code == 404


def test_oversized_request_refused(client):
    r = client.post("/api/v1/device-authorizations", content=b"x" * 20000,
                    headers={"content-type": "application/json"})
    assert r.status_code == 413
