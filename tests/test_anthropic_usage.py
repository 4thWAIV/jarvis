"""Asking Anthropic for the limits every minute: the login is read from the
keychain once and held, read again only when it is about to expire or is
turned away, never refreshed here, and the answer is stored as a percent."""

import io
import urllib.error

import pytest

from jarvis import anthropic_usage as au

ANSWER = {"five_hour": {"utilization": 3.0, "resets_at": "2026-10-21T11:10:00+00:00"},
          "seven_day": {"utilization": 0.5, "resets_at": "2026-10-28T03:00:00+00:00"}}


class _Keychain:
    def __init__(self, tokens):
        self.tokens = list(tokens)
        self.reads = 0

    def __call__(self):
        self.reads += 1
        return self.tokens.pop(0)


def _http(code):
    return urllib.error.HTTPError(au.USAGE_URL, code, "x", {}, io.BytesIO(b""))


def test_the_login_is_read_once_and_reused():
    keychain = _Keychain([("tok", 10_000.0, "max")])
    seen = []
    client = au.UsageClient(keychain, lambda t: seen.append(t) or ANSWER, clock=lambda: 1000.0)

    for _ in range(5):
        assert client.fetch() == ANSWER

    assert keychain.reads == 1 and seen == ["tok"] * 5


def test_it_is_read_again_just_before_it_expires():
    now = {"t": 1000.0}
    keychain = _Keychain([("old", 2000.0, "max"), ("new", 9000.0, "max")])
    seen = []
    client = au.UsageClient(keychain, lambda t: seen.append(t) or ANSWER, clock=lambda: now["t"])
    client.fetch()
    now["t"] = 2000.0 - au.EXPIRY_MARGIN_SEC + 1
    client.fetch()
    assert seen == ["old", "new"] and keychain.reads == 2


def test_turned_away_it_reads_the_keychain_again_and_asks_once_more():
    keychain = _Keychain([("stale", 10_000.0, "max"), ("renewed", 20_000.0, "max")])

    def get(token):
        if token == "stale":
            raise _http(401)
        return ANSWER

    client = au.UsageClient(keychain, get, clock=lambda: 1000.0)
    assert client.fetch() == ANSWER
    assert keychain.reads == 2


def test_failures_are_unavailable_never_raised_otherwise():
    client = au.UsageClient(_Keychain([("t", 10_000.0, "max")]), lambda t: (_ for _ in ()).throw(_http(500)),
                            clock=lambda: 1000.0)
    with pytest.raises(au.UsageUnavailable, match="500"):
        client.fetch()


def test_nothing_here_refreshes_the_login():
    """A refresh would replace the refresh token and log Claude Code out."""
    import inspect
    source = inspect.getsource(au).lower()
    code = "\n".join(l for l in source.splitlines() if not l.strip().startswith(("#", '"', "'")))
    assert "refresh_token" not in code and "grant_type" not in code


def test_the_answer_is_stored_as_a_percent(monkeypatch, tmp_path):
    """Anthropic says percent; half a percent must not become 50%."""
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    import importlib
    from jarvis import data_paths
    importlib.reload(data_paths)
    from jarvis import usage_store
    importlib.reload(usage_store)

    usage_store.record(au.as_rate_limit_info(ANSWER), now=1_000.0)

    windows = {w["key"]: w for w in usage_store.snapshot(now=1_000.0)["windows"]}
    assert windows["seven_day"]["utilization"] == 0.5
    assert windows["five_hour"]["utilization"] == 3.0
    assert windows["seven_day"]["resets_at"] == au._epoch("2026-10-28T03:00:00+00:00")


def test_the_test_suite_never_polls():
    import os
    assert os.environ["JARVIS_USAGE_POLL"] == "0"


def test_the_provider_is_the_service_and_the_subscription_from_the_login():
    client = au.UsageClient(_Keychain([("t", 10_000.0, "max")]), lambda t: ANSWER,
                            clock=lambda: 1000.0)
    client.fetch()
    assert client.provider() == {"service": "anthropic", "subscription": "max"}
