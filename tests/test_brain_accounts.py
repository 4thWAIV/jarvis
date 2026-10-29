"""The node update's brains (brain_accounts.py): a keyed account reference in
place of the email, the role from brains_config, the state from the last
sign-in check and the usage record, and the windows with their original
observation time. And the providers filling in who is signed in."""

import base64
import hashlib
import hmac
import json
import logging
import re
import sys
from pathlib import Path

import pytest

from jarvis import brain_accounts, brain_provider, brains_config, data_paths, usage_store
from jarvis.brain_provider import Account

KEY = base64.urlsafe_b64encode(bytes(range(32))).rstrip(b"=").decode()
OTHER_KEY = base64.urlsafe_b64encode(bytes(range(1, 33))).rstrip(b"=").decode()
EMAIL = "Someone@Example.com"
NOW = 3_999_900_000.0
RESETS = 4_000_000_000
FAKE_CODEX = Path(__file__).parent / "fixtures" / "fake_codex.py"


@pytest.fixture(autouse=True)
def _fresh_cache(monkeypatch):
    monkeypatch.setattr(brain_accounts, "_accounts", {})


@pytest.fixture
def accounts(monkeypatch):
    """Providers that say what the test tells them."""
    said: dict[str, Account] = {}

    class Stand:
        def __init__(self, name):
            self.name = name

        async def account(self):
            return said[self.name]
    monkeypatch.setattr(brain_provider, "provider", Stand)
    return said


def signed_in(email=EMAIL, org_id=None):
    return Account(True, "max", "Signed in.", email=email, org_id=org_id)


def record_usage(service="anthropic", observed=NOW - 120):
    usage_store.record({"status": "allowed", "unifiedWindows": {
        "five_hour": {"utilization": 0.25, "resetsAt": NOW + 3600},
        "seven_day": {"utilization": 0.4, "resetsAt": RESETS}}},
        provider={"service": service, "subscription": "max"}, now=observed)


@pytest.mark.asyncio
async def test_without_the_team_key_there_are_no_brains(accounts):
    accounts["anthropic"] = signed_in()
    await brain_accounts.refresh()
    assert brain_accounts.brains(None) == []
    assert brain_accounts.brains("") == []


@pytest.mark.asyncio
async def test_the_reference_is_the_keyed_hash_of_provider_email_and_organisation(accounts):
    accounts["anthropic"] = signed_in(org_id="org-1")
    await brain_accounts.refresh()
    [brain] = brain_accounts.brains(KEY, now=NOW)
    expected = base64.urlsafe_b64encode(hmac.new(
        bytes(range(32)), b"anthropic\nsomeone@example.com\norg-1", hashlib.sha256).digest()
    ).rstrip(b"=").decode()
    assert brain["account_ref"] == expected
    assert len(expected) == 43 and re.fullmatch(r"[A-Za-z0-9_-]{16,64}", expected)


@pytest.mark.asyncio
async def test_one_account_on_two_data_folders_gives_one_reference(accounts, monkeypatch, tmp_path):
    refs = []
    for folder, email in (("a", EMAIL), ("b", EMAIL.lower())):
        monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path / folder))
        monkeypatch.setattr(brain_accounts, "_accounts", {})
        accounts["anthropic"] = signed_in(email)
        await brain_accounts.refresh()
        refs.append(brain_accounts.brains(KEY, now=NOW)[0]["account_ref"])
    assert refs[0] == refs[1]


@pytest.mark.asyncio
async def test_another_team_s_key_gives_another_reference(accounts):
    accounts["anthropic"] = signed_in()
    await brain_accounts.refresh()
    ours = brain_accounts.brains(KEY, now=NOW)[0]["account_ref"]
    theirs = brain_accounts.brains(OTHER_KEY, now=NOW)[0]["account_ref"]
    assert ours != theirs


@pytest.mark.asyncio
async def test_another_account_or_organisation_gives_another_reference(accounts):
    refs = set()
    for email, org in ((EMAIL, None), (EMAIL, "org-1"), ("other@example.com", None)):
        accounts["anthropic"] = signed_in(email, org)
        await brain_accounts.refresh()
        refs.add(brain_accounts.brains(KEY, now=NOW)[0]["account_ref"])
    assert len(refs) == 3


@pytest.mark.asyncio
async def test_no_email_or_organisation_in_any_output_or_log(accounts, caplog):
    caplog.set_level(logging.DEBUG)
    record_usage()
    accounts["anthropic"] = signed_in(org_id="org-secret-1")
    await brain_accounts.refresh()
    said = json.dumps(brain_accounts.brains(KEY, now=NOW))
    seen = said + caplog.text + repr(brain_accounts._accounts)
    for secret in ("someone@example.com", EMAIL, "org-secret-1", KEY):
        assert secret not in seen


@pytest.mark.asyncio
async def test_a_failed_sign_in_check_is_sign_in_needed(accounts):
    record_usage()
    accounts["anthropic"] = Account(False, "", "Expired.", email=EMAIL)
    await brain_accounts.refresh()
    [brain] = brain_accounts.brains(KEY, now=NOW)
    assert brain["state"] == "sign_in_needed"


@pytest.mark.asyncio
async def test_a_current_seven_day_window_is_measured_with_its_own_observation_time(accounts):
    record_usage(observed=NOW - 120)
    accounts["anthropic"] = signed_in()
    await brain_accounts.refresh()
    [brain] = brain_accounts.brains(KEY, now=NOW)
    week = usage_store.week(now=NOW)
    assert brain["state"] == "measured"
    assert brain["windows"] == [
        {"window": "five_hour", "used_percent": 25.0, "expected_used_percent": None,
         "resets_at": "2096-10-01T04:20:00Z", "age_ms": 120_000},
        {"window": "seven_day", "used_percent": 40.0,
         "expected_used_percent": week["expected_used"],
         "resets_at": "2096-10-02T07:06:40Z", "age_ms": 120_000}]


@pytest.mark.asyncio
async def test_no_usage_record_or_an_expired_week_is_not_measured(accounts):
    accounts["anthropic"] = signed_in()
    await brain_accounts.refresh()
    [brain] = brain_accounts.brains(KEY, now=NOW)
    assert brain["state"] == "not_measured" and brain["windows"] == []
    record_usage()
    [brain] = brain_accounts.brains(KEY, now=RESETS + 1)
    assert brain["state"] == "not_measured" and brain["windows"] == []


@pytest.mark.asyncio
async def test_the_brain_it_starts_with_is_the_brain_and_the_others_standby(accounts):
    brains_config.add("openai")
    brains_config.set_default("openai")
    accounts["anthropic"] = signed_in()
    accounts["openai"] = signed_in("someone@chatgpt.example")
    await brain_accounts.refresh()
    roles = {b["provider"]: b["role"] for b in brain_accounts.brains(KEY, now=NOW)}
    assert roles == {"anthropic": "standby", "openai": "brain"}


def node_ref(key=KEY, provider="anthropic"):
    return base64.urlsafe_b64encode(hmac.new(
        base64.urlsafe_b64decode(key + "="), f"{provider}\nnode:{data_paths.node_id()}".encode(),
        hashlib.sha256).digest()).rstrip(b"=").decode()


def test_a_brain_not_read_yet_is_this_node_s_own_and_not_measured():
    record_usage()
    [brain] = brain_accounts.brains(KEY, now=NOW)
    assert brain["account_ref"] == node_ref()
    assert brain["state"] == "not_measured"


@pytest.mark.asyncio
@pytest.mark.parametrize("said", [
    Account(False, "", "Not signed in."),
    Account(False, "", "Expired.", email=EMAIL),
    Account(True, "", "Signed in with an API key.")])
async def test_a_brain_with_no_signed_in_email_is_this_node_s_own_and_sign_in_needed(accounts, said):
    record_usage()
    accounts["anthropic"] = said
    await brain_accounts.refresh()
    [brain] = brain_accounts.brains(KEY, now=NOW)
    assert brain["account_ref"] == node_ref()
    assert brain["state"] == "sign_in_needed"


@pytest.mark.asyncio
async def test_a_brain_with_no_email_is_never_merged_across_data_folders(accounts, monkeypatch, tmp_path):
    refs = []
    for folder in ("a", "b"):
        monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path / folder))
        monkeypatch.setattr(brain_accounts, "_accounts", {})
        accounts["anthropic"] = Account(False, "", "Not signed in.")
        await brain_accounts.refresh()
        refs.append(brain_accounts.brains(KEY, now=NOW)[0]["account_ref"])
    assert refs[0] != refs[1]
    assert brain_accounts.brains(OTHER_KEY, now=NOW)[0]["account_ref"] != refs[1]


@pytest.mark.asyncio
async def test_a_provider_that_cannot_be_asked_keeps_what_it_said_last(accounts, monkeypatch, caplog):
    accounts["anthropic"] = signed_in()
    await brain_accounts.refresh()

    class Broken:
        def __init__(self, name):
            pass

        async def account(self):
            raise OSError(f"cannot reach {EMAIL}")
    monkeypatch.setattr(brain_provider, "provider", Broken)
    await brain_accounts.refresh("anthropic")
    assert brain_accounts.brains(KEY, now=NOW)[0]["state"] == "not_measured"
    assert "OSError" in caplog.text and EMAIL not in caplog.text


def test_a_key_that_is_not_base64url_sends_no_brains(caplog):
    assert brain_accounts.brains("not base64!") == []
    assert "not base64!" not in caplog.text


# -- the providers fill in who is signed in --------------------------------

@pytest.mark.asyncio
async def test_claude_reads_the_email_and_organisation_from_auth_status(monkeypatch):
    from jarvis import claude_provider, preflight
    asked = []

    async def run(*args, timeout, env=None):
        asked.append(args[1:])
        return 0, json.dumps({"loggedIn": True, "email": EMAIL, "orgId": "org-1"}), ""

    async def fine(timeout=None):
        return preflight.Check(name="claude_login", status=preflight.STATUS_OK, message="Signed in.")
    monkeypatch.setattr(preflight, "_run_subprocess", run)
    monkeypatch.setattr(preflight, "_check_claude_login", fine)
    monkeypatch.setattr(claude_provider.shutil, "which", lambda name: "/usr/local/bin/claude")
    account = await claude_provider.ClaudeProvider().account()
    assert asked == [("auth", "status", "--json")]
    assert (account.signed_in, account.email, account.org_id) == (True, EMAIL, "org-1")
    assert EMAIL not in repr(account)


@pytest.mark.asyncio
async def test_claude_signed_out_or_unreadable_has_no_email(monkeypatch):
    from jarvis import claude_provider, preflight
    replies = iter([(0, json.dumps({"loggedIn": False}), ""), (1, "", "boom"), (0, "not json", "")])

    async def run(*args, timeout, env=None):
        return next(replies)

    async def failed(timeout=None):
        return preflight.Check(name="claude_login", status=preflight.STATUS_FAIL, message="No.")
    monkeypatch.setattr(preflight, "_run_subprocess", run)
    monkeypatch.setattr(preflight, "_check_claude_login", failed)
    monkeypatch.setattr(claude_provider.shutil, "which", lambda name: "/usr/local/bin/claude")
    for _ in range(3):
        account = await claude_provider.ClaudeProvider().account()
        assert (account.email, account.org_id) == (None, None)


@pytest.mark.asyncio
async def test_codex_reads_the_email_from_its_account_read(monkeypatch):
    from jarvis import codex_provider
    monkeypatch.setenv("JARVIS_CODEX_PATH", f"{sys.executable} {FAKE_CODEX}")
    account = await codex_provider.CodexProvider().account()
    assert account.signed_in and account.email == "never-shown@example.com"
    assert account.org_id is None and "never-shown" not in repr(account)
