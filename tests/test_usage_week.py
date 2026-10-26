"""The week against a straight line: expected use is the share of the week
gone by at the moment of the reading; `ahead` is expected minus used."""

import importlib

import pytest

WEEK = 7 * 24 * 3600


@pytest.fixture
def store(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    from jarvis import data_paths
    importlib.reload(data_paths)
    from jarvis import usage_store
    importlib.reload(usage_store)
    return usage_store


def _reading(store, at, used_fraction, resets_at):
    store.record({"status": "allowed", "unifiedWindows": {"seven_day": {
        "utilization": used_fraction, "resetsAt": resets_at, "status": "allowed"}}},
        now=at)


def test_half_way_through_the_week_with_40_percent_used_is_10_ahead(store):
    resets = 10_000_000.0
    start = resets - WEEK
    _reading(store, start + WEEK / 2, 0.40, resets)

    week = store.week(now=start + WEEK / 2 + 60)

    assert week["expected_used"] == 50.0
    assert week["used"] == 40.0
    assert week["ahead"] == 10.0
    assert week["provider"] == {"service": "anthropic", "subscription": ""}


def test_over_the_line_is_negative(store):
    resets = 10_000_000.0
    start = resets - WEEK
    _reading(store, start + WEEK * 0.25, 0.40, resets)       # 40% used, a quarter gone

    assert store.week(now=start + WEEK * 0.25)["ahead"] == -15.0


def test_it_is_worked_out_at_the_reading_not_now(store):
    """An hours-old reading must not look better than it was."""
    resets = 10_000_000.0
    start = resets - WEEK
    _reading(store, start + WEEK / 2, 0.50, resets)

    week = store.week(now=start + WEEK / 2 + 6 * 3600)        # six hours later

    assert week["ahead"] == 0.0
    assert week["age_sec"] == 6 * 3600


def test_the_glide_path_keeps_each_change_this_week_and_starts_again_next(store):
    resets = 10_000_000.0
    start = resets - WEEK
    _reading(store, start + 100, 0.01, resets)
    _reading(store, start + 200, 0.01, resets)                # unchanged: not kept
    _reading(store, start + 300, 0.03, resets)

    assert [p["used"] for p in store.week(now=start + 400)["points"]] == [1.0, 3.0]

    _reading(store, resets + 100, 0.02, resets + WEEK)         # a new week
    assert [p["used"] for p in store.week(now=resets + 200)["points"]] == [2.0]


def test_a_reset_time_that_wobbles_by_seconds_is_the_same_week(store):
    """Codex reports one week's reset a few seconds differently from reading
    to reading; that must not throw the burn-down's history away."""
    resets = 10_000_000.0
    start = resets - WEEK
    _reading(store, start + 100, 0.01, resets - 3)
    _reading(store, start + 200, 0.02, resets + 1)
    _reading(store, start + 300, 0.03, resets)

    assert [p["used"] for p in store.week(now=start + 400)["points"]] == [1.0, 2.0, 3.0]

    _reading(store, start + 500, 0.00, resets + 3600)          # an hour out: a new week
    assert [p["used"] for p in store.week(now=start + 600)["points"]] == [0.0]


def test_no_reading_or_an_expired_week_is_not_measured(store):
    assert store.week()["measured"] is False
    resets = 10_000_000.0
    _reading(store, resets - 100, 0.5, resets)
    assert store.week(now=resets + 1)["measured"] is False


def test_the_route_serves_it(store, monkeypatch):
    from jarvis import server
    importlib.reload(server)
    from fastapi.testclient import TestClient
    body = TestClient(server.app).get("/api/usage/week").json()
    assert body["measured"] is False


def test_each_report_carries_its_provider_and_one_that_does_not_keeps_the_last(store):
    resets = 10_000_000.0
    start = resets - WEEK
    store.record({"unifiedWindows": {"seven_day": {"utilization": 0.1, "resetsAt": resets}}},
                 now=start + 10, provider={"service": "anthropic", "subscription": "max"})
    _reading(store, start + 20, 0.2, resets)                   # a report that does not say
    assert store.week(now=start + 30)["provider"] == {"service": "anthropic", "subscription": "max"}
