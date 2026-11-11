"""usage_history: the long-term record of the brains' weekly usage.

usage_store keeps the latest reading; every reading of the seven-day window
it takes is also kept here, and nothing of the five-hour window (Tim: its
history has no value). The rule is usage_store's: absence is a state. A
week a reading did not measure is NULL, an hour with no measured reading has
no percentage, and nothing unmeasured ever reads as 0.
"""

import base64
import importlib
import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

T = 1_800_000_000.0           # a fixed moment, on an hour boundary
HOUR = 3600
WEEK = 7 * 24 * HOUR


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


def _event(five=None, seven=None, status="allowed", resets=T + WEEK):
    windows = {}
    if five is not None:
        windows["five_hour"] = {"utilization": five, "resetsAt": T + 5 * HOUR}
    if seven is not None:
        windows["seven_day"] = {"utilization": seven, "resetsAt": resets}
    return {"status": status, "unifiedWindows": windows}


def _rows(data_dir):
    conn = sqlite3.connect(str(data_dir / "jarvis.db"))
    try:
        return conn.execute("SELECT at, provider, account, window, utilization, resets_at, status"
                            " FROM usage_readings ORDER BY at, id").fetchall()
    finally:
        conn.close()


def test_every_weekly_reading_is_appended_and_no_five_hour_one(store, tmp_path):
    usage_store, _ = store
    usage_store.record(_event(five=0.25, seven=0.5), now=T)
    usage_store.record(_event(five=0.30), now=T + 60)
    usage_store.record(_event(five=0.40, seven=0.55), now=T + 120,
                       provider={"service": "openai", "subscription": "plus"})
    rows = _rows(tmp_path)
    assert [(r[0], r[1], r[3], r[4]) for r in rows] == [
        (T, "anthropic", "seven_day", 50.0), (T + 120, "openai", "seven_day", 55.0)]
    assert rows[0][5] == T + WEEK and rows[0][6] == "allowed"
    # The latest reading still keeps the five-hour window: only the history drops it.
    assert usage_store.latest()["windows"]["five_hour"]["utilization"] == 30.0


def test_a_week_not_measured_is_kept_as_null_never_zero(store, tmp_path):
    usage_store, usage_history = store
    # A rejection naming the week with no utilisation at all.
    usage_store.record({"status": "rejected", "rateLimitType": "seven_day", "resetsAt": T + WEEK}, now=T)
    rows = _rows(tmp_path)
    assert rows == [(T, "anthropic", None, "seven_day", None, T + WEEK, "rejected")]
    point = usage_history.read("anthropic", T - 10, T + 10, now=T + 10)["weeks"][0]["points"][0]
    assert point["used"] is None and point["low"] is None and point["high"] is None


def test_the_history_carries_the_account_reference_the_network_view_shows(store, tmp_path):
    usage_store, _ = store
    from jarvis import brain_accounts
    from jarvis.brain_provider import Account
    key = base64.urlsafe_b64encode(b"k" * 32).rstrip(b"=").decode()
    brain_accounts._accounts["anthropic"] = Account(True, "max", "", email="Tim@Example.com", org_id="org")
    brain_accounts.brains(key)
    usage_store.record(_event(seven=0.1), now=T)
    expected = brain_accounts.account_ref(b"k" * 32, "anthropic", "tim@example.com", "org")
    assert _rows(tmp_path)[0][2] == expected
    assert "Tim@Example.com" not in json.dumps(_rows(tmp_path))


def test_no_account_is_named_without_the_team_key(store, tmp_path):
    usage_store, _ = store
    usage_store.record(_event(seven=0.1), now=T)
    assert _rows(tmp_path)[0][2] is None


def test_the_latest_stored_weekly_readings_are_backfilled_once(store, tmp_path):
    usage_store, usage_history = store
    from jarvis import data_paths
    data_paths.usage_path().write_text(json.dumps({
        "observed_at": T, "status": "allowed", "rate_limit_type": "",
        "windows": {"five_hour": {"utilization": 12.0, "resets_at": T + HOUR, "status": "allowed",
                                  "observed_at": T},
                    "seven_day": {"utilization": 40.0, "resets_at": T + 86400, "status": "allowed",
                                  "observed_at": T}},
        "week_resets_at": T + 86400, "week_history": [[T - 7200, 30.0], [T - 3600, 35.0], [T, 40.0]]}))
    usage_store.record(_event(seven=0.42, resets=T + 86400), now=T + 60)
    rows = sorted((r[0], r[3], r[4]) for r in _rows(tmp_path))
    assert rows == [(T - 7200, "seven_day", 30.0), (T - 3600, "seven_day", 35.0),
                    (T, "seven_day", 40.0), (T + 60, "seven_day", 42.0)]
    # Never again: a second database connection does not add them twice.
    usage_history._migrated.clear()
    usage_store.record(_event(seven=0.43, resets=T + 86400), now=T + 120)
    assert len(_rows(tmp_path)) == 5


def test_migration_2_drops_the_five_hour_history_already_kept(store, tmp_path):
    _, usage_history = store
    from jarvis import run_store
    conn = sqlite3.connect(str(tmp_path / "jarvis.db"))
    run_store.migrate(conn, "usage_history_schema", {1: usage_history._MIGRATIONS[1]}, "usage history")
    conn.executemany(
        "INSERT INTO usage_readings (at, provider, account, window, utilization, resets_at, status)"
        " VALUES (?, 'anthropic', NULL, ?, 10.0, ?, '')",
        [(T, "five_hour", T + HOUR), (T, "seven_day", T + WEEK)])
    conn.execute("INSERT INTO usage_hourly (hour, provider, account, window, samples, measured, low, high,"
                 " last, last_at, resets_at) VALUES (?, 'anthropic', '', 'five_hour', 1, 1, 5, 5, 5, ?, ?)",
                 (T - WEEK, T - WEEK, T - WEEK + HOUR))
    conn.commit()
    conn.close()
    usage_history._migrated.clear()
    usage_history.read("anthropic", now=T)
    assert [r[3] for r in _rows(tmp_path)] == ["seven_day"]
    conn = sqlite3.connect(str(tmp_path / "jarvis.db"))
    assert conn.execute("SELECT COUNT(*) FROM usage_hourly").fetchone()[0] == 0
    assert conn.execute("SELECT version FROM usage_history_schema").fetchone()[0] == 2
    conn.close()


def test_old_readings_fold_into_hourly_summaries(store, tmp_path):
    usage_store, usage_history = store
    usage_store.record(_event(seven=0.10), now=T + 60)
    usage_store.record(_event(seven=0.30), now=T + 120)
    usage_store.record({"status": "rejected", "rateLimitType": "seven_day", "resetsAt": T + WEEK}, now=T + 180)
    usage_store.record(_event(seven=0.20), now=T + 240)
    usage_store.record(_event(seven=0.50), now=T + HOUR + 60)       # the next hour
    later = T + usage_history.RAW_KEEP_SEC + 2 * HOUR
    folded = usage_history.compact(now=later)
    assert folded == 5 and _rows(tmp_path) == []
    conn = sqlite3.connect(str(tmp_path / "jarvis.db"))
    hours = conn.execute("SELECT hour, window, samples, measured, low, high, last FROM usage_hourly"
                         " ORDER BY hour").fetchall()
    conn.close()
    assert hours == [(T, "seven_day", 4, 3, 10.0, 30.0, 20.0),
                     (T + HOUR, "seven_day", 1, 1, 50.0, 50.0, 50.0)]
    got = usage_history.read("anthropic", T, T + 2 * HOUR, "hour", now=later)
    points = got["weeks"][0]["points"]
    assert [(p["at"], p["used"], p["low"], p["samples"]) for p in points] == [
        (T, 30.0, 10.0, 4), (T + HOUR, 50.0, 50.0, 1)]
    # Read reading by reading, the folded range comes back by the hour.
    raw = usage_history.read("anthropic", T, T + 2 * HOUR, "raw", now=later)
    assert [p["at"] for p in raw["weeks"][0]["points"]] == [T, T + HOUR]


def test_readings_within_the_two_weeks_are_kept_as_they_were(store, tmp_path):
    usage_store, usage_history = store
    usage_store.record(_event(seven=0.10), now=T)
    assert usage_history.compact(now=T + usage_history.RAW_KEEP_SEC - HOUR) == 0
    assert len(_rows(tmp_path)) == 1


def test_the_readings_come_back_week_by_week_newest_first(store):
    usage_store, usage_history = store
    first, second = T + 86400, T + 86400 + WEEK
    usage_store.record(_event(seven=0.20, resets=first), now=T)
    usage_store.record(_event(seven=0.60, resets=first), now=T + 3600)
    # Codex's wobble: a reset a few seconds off is the same week.
    usage_store.record(_event(seven=0.70, resets=first + 5), now=T + 7200)
    usage_store.record(_event(seven=0.05, resets=second), now=first + 600)
    usage_store.record(_event(seven=0.15, resets=second), now=first + 7200)
    got = usage_history.read("anthropic", now=first + 7200)
    assert got["window"] == "seven_day" and got["week_sec"] == WEEK
    assert got["resolution"] == "hour"            # the whole record is read by the hour
    assert [(w["resets_at"], w["start"], w["measured"]) for w in got["weeks"]] == [
        (second, second - WEEK, 2), (first, first - WEEK, 3)]
    assert [p["used"] for p in got["weeks"][1]["points"]] == [20.0, 60.0, 70.0]
    assert got["latest"] == {"used": 15.0, "resets_at": second, "observed_at": first + 7200,
                             "status": "allowed"}


def test_a_reading_naming_no_reset_goes_in_the_week_that_holds_it(store):
    _, usage_history = store
    weeks = usage_history.weeks_of([
        {"at": T, "used": 10.0, "resets_at": T + 86400},
        {"at": T + 60, "used": 11.0, "resets_at": None},
        {"at": T - 2 * WEEK, "used": 5.0, "resets_at": None}])      # no week holds it
    assert [len(w["points"]) for w in weeks] == [2]


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
    usage_store.record(_event(seven=0.4), now=T)
    assert usage_store.latest()["windows"]["seven_day"]["utilization"] == 40.0


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
        usage_store.record(_event(five=0.5, seven=i / 100 if i % 3 else None), now=T + i * 60)
    query = {"since": T - 60, "until": T + 3600}
    page = TestClient(server.app).get("/api/usage/history", params=query).json()
    assert page["resolution"] == "raw" and len(page["weeks"]) == 1
    assert len(page["weeks"][0]["points"]) == 20 and "window" not in query
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


def test_the_route_and_the_tool_give_one_brain_never_both(wired):
    server, usage_store = wired
    usage_store.record(_event(seven=0.1), now=T)
    usage_store.record(_event(seven=0.2), now=T + 60, provider={"service": "openai", "subscription": ""})
    query = {"since": T - 60, "until": T + 3600}
    client = TestClient(server.app)
    running = server._brain_provider().name
    for asked, expected, used in ((None, running, 10.0), ("openai", "openai", 20.0),
                                  ("anthropic", "anthropic", 10.0)):
        params = dict(query, **({"provider": asked} if asked else {}))
        page = client.get("/api/usage/history", params=params).json()
        assert page["provider"] == expected
        assert [p["used"] for w in page["weeks"] for p in w["points"]] == [used]
        said = _tool(server, **params)
        assert json.loads(said.partition("\n")[2]) == page


def test_the_route_and_the_tool_refuse_alike(wired):
    server, _ = wired
    r = TestClient(server.app).get("/api/usage/history", params={"provider": "gemini"})
    assert r.status_code == 400 and "not a brain JARVIS knows" in r.json()["error"]
    said = _tool(server, provider="gemini")
    assert said.startswith("Refused: that is not a brain JARVIS knows") and "gemini" not in said
    r = TestClient(server.app).get("/api/usage/history", params={"since": "soon"})
    assert r.status_code == 400 and "soon" not in r.json()["error"]
