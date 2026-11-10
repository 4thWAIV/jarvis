"""usage_history: the long-term record of the brains' usage readings.

usage_store keeps the latest reading; every reading it takes is also kept
here. The rule is the same: absence is a state. A window a reading did not
measure is NULL, an hour with no measured reading has no percentage, and
nothing unmeasured ever reads as 0.
"""

import base64
import importlib
import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

T = 1_800_000_000.0           # a fixed moment, on an hour boundary
HOUR = 3600


@pytest.fixture
def store(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    from jarvis import data_paths
    importlib.reload(data_paths)
    from jarvis import usage_store, usage_history, brain_accounts
    importlib.reload(usage_store)
    importlib.reload(usage_history)
    monkeypatch.setattr(brain_accounts, "_team_key", None)
    monkeypatch.setattr(brain_accounts, "_accounts", {})
    return usage_store, usage_history


def _event(five=None, seven=None, status="allowed"):
    windows = {}
    if five is not None:
        windows["five_hour"] = {"utilization": five, "resetsAt": T + 5 * HOUR}
    if seven is not None:
        windows["seven_day"] = {"utilization": seven, "resetsAt": T + 7 * 24 * HOUR}
    return {"status": status, "unifiedWindows": windows}


def _rows(data_dir):
    conn = sqlite3.connect(str(data_dir / "jarvis.db"))
    try:
        return conn.execute("SELECT at, provider, account, window, utilization, resets_at, status"
                            " FROM usage_readings ORDER BY at, id").fetchall()
    finally:
        conn.close()


def test_every_reading_record_takes_is_appended(store, tmp_path):
    usage_store, _ = store
    usage_store.record(_event(five=0.25, seven=0.5), now=T)
    usage_store.record(_event(five=0.30), now=T + 60)
    usage_store.record(_event(five=0.40, seven=0.55), now=T + 120,
                       provider={"service": "openai", "subscription": "plus"})
    rows = _rows(tmp_path)
    assert [(r[0], r[1], r[3], r[4]) for r in rows] == [
        (T, "anthropic", "five_hour", 25.0), (T, "anthropic", "seven_day", 50.0),
        (T + 60, "anthropic", "five_hour", 30.0),
        (T + 120, "openai", "five_hour", 40.0), (T + 120, "openai", "seven_day", 55.0)]
    assert rows[0][5] == T + 5 * HOUR and rows[0][6] == "allowed"


def test_a_window_not_measured_is_kept_as_null_never_zero(store, tmp_path):
    usage_store, usage_history = store
    # A rejection naming its window with no utilisation at all.
    usage_store.record({"status": "rejected", "rateLimitType": "five_hour", "resetsAt": T + HOUR}, now=T)
    rows = _rows(tmp_path)
    assert rows == [(T, "anthropic", None, "five_hour", None, T + HOUR, "rejected")]
    point = usage_history.read("anthropic", "five_hour", T - 10, T + 10, now=T + 10)["series"][0]["points"][0]
    assert point["used"] is None and point["low"] is None and point["high"] is None


def test_the_history_carries_the_account_reference_the_network_view_shows(store, tmp_path):
    usage_store, _ = store
    from jarvis import brain_accounts
    from jarvis.brain_provider import Account
    key = base64.urlsafe_b64encode(b"k" * 32).rstrip(b"=").decode()
    brain_accounts._accounts["anthropic"] = Account(True, "max", "", email="Tim@Example.com", org_id="org")
    brain_accounts.brains(key)
    usage_store.record(_event(five=0.1), now=T)
    expected = brain_accounts.account_ref(b"k" * 32, "anthropic", "tim@example.com", "org")
    assert _rows(tmp_path)[0][2] == expected
    assert "Tim@Example.com" not in json.dumps(_rows(tmp_path))


def test_no_account_is_named_without_the_team_key(store, tmp_path):
    usage_store, _ = store
    usage_store.record(_event(five=0.1), now=T)
    assert _rows(tmp_path)[0][2] is None


def test_the_latest_stored_readings_are_backfilled_once(store, tmp_path):
    usage_store, usage_history = store
    from jarvis import data_paths
    data_paths.usage_path().write_text(json.dumps({
        "observed_at": T, "status": "allowed", "rate_limit_type": "",
        "windows": {"five_hour": {"utilization": 12.0, "resets_at": T + HOUR, "status": "allowed",
                                  "observed_at": T},
                    "seven_day": {"utilization": 40.0, "resets_at": T + 86400, "status": "allowed",
                                  "observed_at": T}},
        "week_resets_at": T + 86400, "week_history": [[T - 7200, 30.0], [T - 3600, 35.0], [T, 40.0]]}))
    usage_store.record(_event(five=0.2), now=T + 60)
    rows = sorted((r[0], r[3], r[4]) for r in _rows(tmp_path))
    assert rows == [(T - 7200, "seven_day", 30.0), (T - 3600, "seven_day", 35.0),
                    (T, "five_hour", 12.0), (T, "seven_day", 40.0),
                    (T + 60, "five_hour", 20.0)]
    # Never again: a second database connection does not add them twice.
    usage_history._migrated.clear()
    usage_store.record(_event(five=0.21), now=T + 120)
    assert len(_rows(tmp_path)) == 6


def test_old_readings_fold_into_hourly_summaries(store, tmp_path):
    usage_store, usage_history = store
    usage_store.record(_event(five=0.10), now=T + 60)
    usage_store.record(_event(five=0.30), now=T + 120)
    usage_store.record({"status": "rejected", "rateLimitType": "five_hour"}, now=T + 180)
    usage_store.record(_event(five=0.20), now=T + 240)
    usage_store.record(_event(five=0.50), now=T + HOUR + 60)       # the next hour
    later = T + usage_history.RAW_KEEP_SEC + 2 * HOUR
    folded = usage_history.compact(now=later)
    assert folded == 5 and _rows(tmp_path) == []
    conn = sqlite3.connect(str(tmp_path / "jarvis.db"))
    hours = conn.execute("SELECT hour, window, samples, measured, low, high, last FROM usage_hourly"
                         " ORDER BY hour").fetchall()
    conn.close()
    assert hours == [(T, "five_hour", 4, 3, 10.0, 30.0, 20.0),
                     (T + HOUR, "five_hour", 1, 1, 50.0, 50.0, 50.0)]
    got = usage_history.read("anthropic", "five_hour", T, T + 2 * HOUR, "hour", now=later)
    points = got["series"][0]["points"]
    assert [(p["at"], p["used"], p["low"], p["samples"]) for p in points] == [
        (T, 30.0, 10.0, 4), (T + HOUR, 50.0, 50.0, 1)]
    # Read reading by reading, the folded range comes back by the hour.
    raw = usage_history.read("anthropic", "five_hour", T, T + 2 * HOUR, "raw", now=later)
    assert [p["at"] for p in raw["series"][0]["points"]] == [T, T + HOUR]


def test_readings_within_the_two_weeks_are_kept_as_they_were(store, tmp_path):
    usage_store, usage_history = store
    usage_store.record(_event(five=0.10), now=T)
    assert usage_history.compact(now=T + usage_history.RAW_KEEP_SEC - HOUR) == 0
    assert len(_rows(tmp_path)) == 1


def test_an_hour_with_no_measured_reading_has_no_percentage(store):
    usage_store, usage_history = store
    usage_store.record({"status": "rejected", "rateLimitType": "five_hour"}, now=T + 60)
    got = usage_history.read("anthropic", "five_hour", T - 3 * 86400, T + HOUR, now=T + HOUR)
    assert got["resolution"] == "hour"
    assert got["series"][0]["points"] == [{"at": T, "used": None, "low": None, "high": None,
                                           "resets_at": None, "status": "", "account": None,
                                           "samples": 1}]


def test_the_range_picks_the_resolution_and_the_gap(store):
    _, usage_history = store
    day = usage_history.read(since=T - 86400, until=T, now=T)
    month = usage_history.read(since=T - 30 * 86400, until=T, now=T)
    assert (day["resolution"], day["gap_sec"]) == ("raw", 15 * 60)
    assert (month["resolution"], month["gap_sec"]) == ("hour", 2 * HOUR)
    # Every brain and both windows, measured or not.
    assert [(s["provider"], s["window"]) for s in day["series"]] == [("anthropic", "five_hour"),
                                                                     ("anthropic", "seven_day")]
    assert day["series"][0]["points"] == [] and day["series"][0]["latest"]["used"] is None


def test_resets_inside_the_range_are_marked_once(store):
    usage_store, usage_history = store
    for i, reset in enumerate((T + 100, T + 130, T + 5000)):
        usage_store.record({"status": "allowed", "unifiedWindows": {
            "five_hour": {"utilization": 0.1, "resetsAt": reset}}}, now=T + i * 60)
    got = usage_history.read("anthropic", "five_hour", T, T + 1000, now=T + 1000)["series"][0]
    assert got["resets"] == [T + 100]
    assert got["latest"] == {"window": "five_hour", "used": 10.0, "resets_at": T + 5000,
                             "observed_at": T + 120, "status": "allowed"}


def test_a_request_that_is_not_one_is_refused(store):
    _, usage_history = store
    with pytest.raises(usage_history.Refused):
        usage_history.read(since="yesterday")
    with pytest.raises(usage_history.Refused):
        usage_history.read(since=T, until=T - 1)
    with pytest.raises(usage_history.Refused):
        usage_history.read(resolution="minute")


def test_a_failing_history_never_loses_the_reading(store, monkeypatch):
    usage_store, usage_history = store

    def broken(*a, **k):
        raise sqlite3.OperationalError("disk I/O error")
    monkeypatch.setattr(usage_history, "_connect", broken)
    usage_store.record(_event(five=0.4), now=T)
    assert usage_store.latest()["windows"]["five_hour"]["utilization"] == 40.0


# ── the route and the brain's tool give the same answer ──────────────────

@pytest.fixture
def wired(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("JARVIS_BRAIN_AUTOSTART", "0")
    from jarvis import data_paths
    importlib.reload(data_paths)
    from jarvis import run_store, usage_store, usage_history
    importlib.reload(run_store)
    importlib.reload(usage_store)
    importlib.reload(usage_history)
    from jarvis import server as server_module
    importlib.reload(server_module)
    run_store.init_db()
    return server_module, usage_store


def _tool(server, **arguments) -> str:
    from jarvis import data_paths
    client = TestClient(server.app)
    r = client.post("/internal/tool", headers={"Authorization": f"Bearer {data_paths.ensure_tool_token()}"},
                    json={"tool": "usage_history", "arguments": arguments})
    assert r.status_code == 200, r.text
    return r.json()["text"]


def test_the_route_and_the_tool_return_the_same_history(wired):
    server, usage_store = wired
    for i in range(30):
        usage_store.record(_event(five=i / 100, seven=0.5 if i % 3 else None), now=T + i * 60)
    query = {"since": T - 60, "until": T + 3600}
    page = TestClient(server.app).get("/api/usage/history", params=query).json()
    assert page["resolution"] == "raw" and len(page["series"][0]["points"]) == 30
    # The tool gives the route's JSON, a page at a time; joined, it is the same.
    text, pages, n = "", None, 1
    while True:
        said = _tool(server, page=n, **query)
        head, _, chunk = said.partition("\n")
        assert head.startswith("The usage history")
        text += chunk
        pages = pages or int(head.split(" of ")[1].split(" ")[0])
        if n == pages:
            break
        n += 1
    assert json.loads(text) == page


def test_the_route_and_the_tool_refuse_alike(wired):
    server, _ = wired
    r = TestClient(server.app).get("/api/usage/history", params={"provider": "gemini"})
    assert r.status_code == 400 and "no brain called" in r.json()["error"]
    assert _tool(server, provider="gemini").startswith("Refused: there is no brain called")
    r = TestClient(server.app).get("/api/usage/history", params={"since": "soon"})
    assert r.status_code == 400
