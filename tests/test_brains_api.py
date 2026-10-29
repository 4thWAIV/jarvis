"""The brains, for the Settings view: brains.json (which brains are added,
each one's model, the one JARVIS starts with), the /api/brains endpoints,
Codex's sign-in started from the page, usage kept per brain, and the startup
choice read by BrainConfig.from_env."""

import asyncio
import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from jarvis import brain
from jarvis import brains_config

FAKE_CODEX = Path(__file__).parent / "fixtures" / "fake_codex.py"
ORIGIN = {"Origin": "http://localhost:5173"}


@pytest.fixture
def server(monkeypatch):
    monkeypatch.setenv("JARVIS_CODEX_PATH", f"{sys.executable} {FAKE_CODEX}")
    from jarvis import server as server_module

    async def fine(timeout=None):
        from jarvis import preflight
        return preflight.Check(name="claude_login", status=preflight.STATUS_OK, message="Signed in.")

    async def cli(timeout=None):
        from jarvis import preflight
        return preflight.Check(name="claude_cli", status=preflight.STATUS_OK, message="claude 2.1.306 on PATH.")
    from jarvis import claude_provider
    monkeypatch.setattr(claude_provider.ClaudeProvider, "preflight",
                        lambda self: ((cli,), ()))
    from jarvis import preflight
    monkeypatch.setattr(preflight, "_check_claude_login", fine)
    from jarvis import codex_provider

    async def codex_ok(timeout=None):
        return preflight.Check(name="codex_cli", status=preflight.STATUS_OK, message="codex 0.154.0 on PATH.")
    monkeypatch.setattr(codex_provider.CodexProvider, "preflight", lambda self: ((codex_ok,), ()))
    return server_module


def test_with_no_file_claude_alone_is_the_brain(server):
    assert brains_config.load() == {"default": "anthropic", "brains": {"anthropic": {}}}
    cards = TestClient(server.app).get("/api/brains").json()["brains"]
    by = {c["name"]: c for c in cards}
    assert by["anthropic"]["added"] and by["anthropic"]["default"]
    assert by["anthropic"]["account"]["signed_in"] is True
    assert by["anthropic"]["models"] == ["sonnet", "opus", "haiku"]
    assert by["openai"]["added"] is False and "account" not in by["openai"]


def test_adding_codex_shows_its_card_its_models_and_its_account(server):
    client = TestClient(server.app)
    assert client.post("/api/brains/openai", json={"action": "add"}, headers=ORIGIN).status_code == 200
    by = {c["name"]: c for c in client.get("/api/brains").json()["brains"]}
    codex = by["openai"]
    assert codex["added"] and not codex["default"] and codex["title"] == "OpenAI (Codex)"
    assert codex["models"] == ["gpt-5.6-terra", "gpt-5.6-luna"]
    assert codex["account"] == {"signed_in": True, "plan": "pro", "detail": "Signed in with ChatGPT."}
    assert "never-shown" not in json.dumps(codex), "the account's email is not shown"
    assert codex["checks"][0]["status"] == "ok" and codex["signs_in_here"] is True


def test_the_model_and_the_startup_brain_are_saved_and_read_at_start(server, tmp_path, monkeypatch):
    client = TestClient(server.app)
    client.post("/api/brains/openai", json={"action": "add"}, headers=ORIGIN)
    assert client.put("/api/brains/openai", json={"model": "gpt-5.6-luna"}, headers=ORIGIN).status_code == 200
    assert client.put("/api/brains/openai", json={"model": "made-up"}, headers=ORIGIN).status_code == 400
    assert client.post("/api/brains/default", json={"name": "openai"}, headers=ORIGIN).status_code == 200

    monkeypatch.delenv("JARVIS_BRAIN_PROVIDER", raising=False)
    monkeypatch.delenv("JARVIS_BRAIN_MODEL", raising=False)
    cfg = brain.BrainConfig.from_env(tmp_path)
    assert (cfg.provider, cfg.model) == ("openai", "gpt-5.6-luna")
    monkeypatch.setenv("JARVIS_BRAIN_PROVIDER", "anthropic")
    assert brain.BrainConfig.from_env(tmp_path).provider == "anthropic", ".env still wins"


def test_claude_cannot_be_removed_nor_the_brain_jarvis_starts_with(server):
    client = TestClient(server.app)
    assert client.post("/api/brains/anthropic", json={"action": "remove"}, headers=ORIGIN).status_code == 400
    client.post("/api/brains/openai", json={"action": "add"}, headers=ORIGIN)
    client.post("/api/brains/default", json={"name": "openai"}, headers=ORIGIN)
    assert client.post("/api/brains/openai", json={"action": "remove"}, headers=ORIGIN).status_code == 400
    client.post("/api/brains/default", json={"name": "anthropic"}, headers=ORIGIN)
    assert client.post("/api/brains/openai", json={"action": "remove"}, headers=ORIGIN).status_code == 200
    assert "openai" not in brains_config.load()["brains"]


def test_a_change_needs_the_page_s_origin(server):
    client = TestClient(server.app)
    assert client.post("/api/brains/openai", json={"action": "add"}).status_code in (401, 403)


@pytest.mark.asyncio
async def test_codex_signs_in_from_the_page_and_the_page_hears_it_finish(server, monkeypatch):
    from jarvis import brain_accounts
    monkeypatch.setattr(brain_accounts, "_accounts", {})
    sent = []

    async def emit(msg):
        sent.append(msg)
    monkeypatch.setattr(server, "_voice_emit", emit)
    import httpx
    transport = httpx.ASGITransport(app=server.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8340") as client:
        r = await client.post("/api/brains/openai/sign-in", headers=ORIGIN)
        assert r.status_code == 200
        assert r.json()["url"].startswith("https://auth.openai.com/")
        for _ in range(100):
            if sent:
                break
            await asyncio.sleep(0.05)
        assert sent == [{"type": "brain_sign_in", "name": "openai", "ok": True}]
        for _ in range(100):
            if "openai" in brain_accounts._accounts:
                break
            await asyncio.sleep(0.05)
        assert brain_accounts._accounts["openai"].email == "never-shown@example.com", (
            "who signed in is read again for the network's account reference")
        r = await client.post("/api/brains/anthropic/sign-in", headers=ORIGIN)
        assert r.status_code == 400, "Claude signs in on its own"


def test_usage_is_kept_and_read_per_brain(server):
    from jarvis import usage_store
    usage_store.record({"status": "allowed", "unifiedWindows": {
        "seven_day": {"utilization": 0.3, "resetsAt": 4_000_000_000}}},
        provider={"service": "openai", "subscription": "pro"}, now=3_999_900_000)
    client = TestClient(server.app)
    week = client.get("/api/usage/week", params={"provider": "openai"}).json()
    assert week["measured"] and week["provider"] == {"service": "openai", "subscription": "pro"}
    assert client.get("/api/usage/week", params={"provider": "anthropic"}).json()["measured"] is False
    assert client.get("/api/usage/week", params={"provider": "nobody"}).status_code == 404


def test_the_brief_list_says_which_are_added_and_which_runs_without_asking_anyone(server, monkeypatch):
    from jarvis import codex_provider
    monkeypatch.setattr(codex_provider.CodexProvider, "account",
                        lambda self: (_ for _ in ()).throw(AssertionError("not asked")))
    client = TestClient(server.app)
    client.post("/api/brains/openai", json={"action": "add"}, headers=ORIGIN)
    cards = client.get("/api/brains", params={"brief": 1}).json()["brains"]
    assert [(c["name"], c["added"], c["default"]) for c in cards] == [
        ("anthropic", True, True), ("openai", True, False)]
    assert all("checks" not in c and "account" not in c for c in cards)


@pytest.mark.asyncio
@pytest.mark.parametrize("state, told", [("ready", True), ("failed", True), ("rotated", True),
                                         ("restarting", False), ("rate_limited", False)])
async def test_the_page_is_told_when_a_brain_is_ready_fails_or_is_replaced(server, monkeypatch, state, told):
    sent = []

    async def emit(msg):
        sent.append(msg)
    monkeypatch.setattr(server, "_voice_emit", emit)
    monkeypatch.setattr(server, "speech", None)
    await server._on_brain_state(state, {})
    assert (sent == [{"type": "brain_state", "state": state}]) is told, sent
