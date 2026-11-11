"""The long-term record of the subscription brains' weekly usage.

usage_store keeps only the latest reading per brain provider. Every reading
of the seven-day window it takes (`usage_store.record`) is also appended
here, in JARVIS's database under its own schema version
(`usage_history_schema`, run_store.migrate). Only the week is kept: Tim's
word, the five-hour window's history has no value, so its readings are
never written and migration 2 dropped the ones written before.

  usage_readings  every reading as it came: when, provider, account hash,
                  window, utilization (a percentage, NULL when the reading
                  did not measure it), resets-at and status. Kept for
                  RAW_KEEP_SEC.
  usage_hourly    after that, one row per (provider, account, window, hour):
                  how many readings and how many measured, the lowest, the
                  highest and the last percentage, and the last reset time.
                  Kept for good (four rows an hour, about 35,000 a year).

The rule is usage_store's: absence is a state. A window not measured is
NULL, never 0, and an hour with no measured reading has NULL percentages.
The page joins the measured points into one line and passes over the rest;
nothing unmeasured is drawn as 0.

`read` is the one reader: `GET /api/usage/history` and the brain's
`usage_history` both return what it returns: one brain's weeks, newest
first, each with its readings, for the page to draw each week's burn-down
the way the brain's box draws this one.

The account is brain_accounts' keyed hash of the signed-in subscription
(`brain_accounts.history_ref`), the same reference the Network view shows;
NULL while it is not known (no team key, or the sign-in not read yet).
"""
from __future__ import annotations

import logging
import sqlite3
import threading
import time
from typing import Any, Optional

from jarvis import data_paths, run_store

log = logging.getLogger("jarvis.usage_history")

# Every reading is kept for two weeks: two whole seven-day windows at full
# detail, so this week and last can be compared reading by reading. After
# that a reading lives on only in its hour's summary.
RAW_KEEP_SEC = 14 * 24 * 3600
HOUR_SEC = 3600
# Compaction runs at most once an hour (it only ever moves whole hours).
COMPACT_EVERY_SEC = HOUR_SEC
# A range up to two days long is read reading by reading; a longer one by
# the hour (a week is 168 points per window, not 10,080).
RAW_RANGE_MAX_SEC = 2 * 24 * 3600
# The one window kept, and how long it runs: the page draws each week's
# straight line from all left at its start to none left at its reset, as the
# brain's box does (usage_store.week).
WINDOW = "seven_day"
WEEK_SEC = 7 * 24 * HOUR_SEC
# Two reset times this close are the same reset (usage_store.SAME_WEEK_SEC:
# Codex wobbles its reset time by a few seconds).
SAME_RESET_SEC = 60.0
RESOLUTIONS = ("auto", "raw", "hour")

_SCHEMA = """
    CREATE TABLE IF NOT EXISTS usage_readings (
        id          INTEGER PRIMARY KEY,
        at          REAL NOT NULL,
        provider    TEXT NOT NULL,
        account     TEXT,
        window      TEXT NOT NULL,
        utilization REAL,
        resets_at   REAL,
        status      TEXT NOT NULL DEFAULT ''
    );
    CREATE INDEX IF NOT EXISTS usage_readings_by_time
        ON usage_readings (provider, window, at);
    CREATE TABLE IF NOT EXISTS usage_hourly (
        hour        REAL NOT NULL,
        provider    TEXT NOT NULL,
        account     TEXT NOT NULL DEFAULT '',
        window      TEXT NOT NULL,
        samples     INTEGER NOT NULL,
        measured    INTEGER NOT NULL,
        low         REAL,
        high        REAL,
        last        REAL,
        last_at     REAL NOT NULL,
        resets_at   REAL,
        PRIMARY KEY (provider, window, account, hour)
    );
"""

# Each step is appended, never changed (run_store.migrate).
_MIGRATIONS = {
    1: _SCHEMA,
    # Only the week is kept (Tim, 2026-11-10): the five-hour window's
    # readings, and any other window's, are dropped.
    2: """
        DELETE FROM usage_readings WHERE window <> 'seven_day';
        DELETE FROM usage_hourly WHERE window <> 'seven_day';
    """,
}

_migrated: set[str] = set()
_migrating = threading.Lock()
_last_compact: dict[str, float] = {}


def _connect() -> sqlite3.Connection:
    conn = run_store._connect(data_paths.db_path())
    path = conn.execute("PRAGMA database_list").fetchone()["file"]
    if path not in _migrated:
        with _migrating:
            if path not in _migrated:
                row = None
                try:
                    row = conn.execute("SELECT version FROM usage_history_schema").fetchone()
                except sqlite3.OperationalError:
                    pass
                run_store.migrate(conn, "usage_history_schema", _MIGRATIONS, "usage history")
                if row is None:
                    _backfill(conn)
                conn.commit()
                _migrated.add(path)
    return conn


def _backfill(conn: sqlite3.Connection) -> None:
    """Once, as the tables are made: the seven-day readings usage_store
    already holds, its latest and its burn-down history."""
    from jarvis import usage_store
    for service in _stored_services():
        stored = usage_store.latest(service)
        if not stored:
            continue
        rows = []
        week_resets = usage_store._epoch(stored.get("week_resets_at"))
        for at, used in stored.get("week_history") or []:
            when = usage_store._epoch(at)
            if when is not None:
                rows.append((when, service, None, "seven_day",
                             usage_store._stored_percent(used), week_resets, ""))
        for key, raw in (stored.get("windows") or {}).items():
            if key != WINDOW or not isinstance(raw, dict):
                continue
            when = usage_store._epoch(raw.get("observed_at"))
            if when is None or any(r[0] == when and r[3] == key for r in rows):
                continue
            rows.append((when, service, None, key, usage_store._stored_percent(raw.get("utilization")),
                         usage_store._epoch(raw.get("resets_at")), usage_store._text(raw.get("status"))))
        conn.executemany(
            "INSERT INTO usage_readings (at, provider, account, window, utilization, resets_at, status)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)", rows)


def _stored_services() -> list[str]:
    from jarvis import brains_config
    found = list(brains_config.KNOWN)
    for path in data_paths.data_dir().glob("usage-*.json"):
        name = path.stem[len("usage-"):]
        if name and name not in found:
            found.append(name)
    return found


# ── writing ────────────────────────────────────────────────────────────────

def append(service: str, windows: dict[str, dict], *, account: Optional[str] = None,
           now: Optional[float] = None) -> int:
    """Append one observation's seven-day window (usage_store's normalised
    shape: utilization as a percentage or None, resets_at, status,
    observed_at); any other window it names is not kept. Returns how many
    rows went in. Never raises: losing a history row must not lose the
    reading."""
    rows = [(float(w.get("observed_at") or now or time.time()), service, account, key,
             w.get("utilization"), w.get("resets_at"), str(w.get("status") or ""))
            for key, w in windows.items() if key == WINDOW and isinstance(w, dict)]
    if not rows:
        return 0
    try:
        conn = _connect()
        try:
            with conn:
                conn.executemany(
                    "INSERT INTO usage_readings (at, provider, account, window, utilization, resets_at,"
                    " status) VALUES (?, ?, ?, ?, ?, ?, ?)", rows)
            at = time.time() if now is None else float(now)
            path = str(data_paths.db_path())
            if at - _last_compact.get(path, 0.0) >= COMPACT_EVERY_SEC:
                _last_compact[path] = at
                _compact(conn, at)
        finally:
            conn.close()
    except (sqlite3.Error, OSError, RuntimeError) as e:
        log.warning(f"usage_history: could not keep the reading ({e})")
        return 0
    return len(rows)


def _hour(at: float) -> float:
    return float(int(at // HOUR_SEC) * HOUR_SEC)


def _compact(conn: sqlite3.Connection, now: float) -> int:
    """Fold every reading older than RAW_KEEP_SEC (whole hours only) into its
    hour's summary and drop it, in one transaction. Returns readings folded."""
    cutoff = _hour(now - RAW_KEEP_SEC)
    with conn:
        old = conn.execute(
            "SELECT at, provider, account, window, utilization, resets_at FROM usage_readings"
            " WHERE at < ? ORDER BY at, id", (cutoff,)).fetchall()
        if not old:
            return 0
        groups: dict[tuple, list] = {}
        for r in old:
            groups.setdefault((r["provider"], r["window"], r["account"] or "", _hour(r["at"])), []).append(r)
        for (provider, window, account, hour), rows in groups.items():
            have = conn.execute(
                "SELECT * FROM usage_hourly WHERE provider = ? AND window = ? AND account = ? AND hour = ?",
                (provider, window, account, hour)).fetchone()
            summary = _summarise(rows, have)
            conn.execute(
                "INSERT OR REPLACE INTO usage_hourly (hour, provider, account, window, samples, measured,"
                " low, high, last, last_at, resets_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (hour, provider, account, window, *summary))
        conn.execute("DELETE FROM usage_readings WHERE at < ?", (cutoff,))
    return len(old)


def _summarise(rows: list, have: Optional[sqlite3.Row]) -> tuple:
    """(samples, measured, low, high, last, last_at, resets_at) of `rows`,
    merged into the hour's summary already kept (`have`)."""
    samples = len(rows) + (have["samples"] if have else 0)
    values = [r["utilization"] for r in rows if r["utilization"] is not None]
    measured = len(values) + (have["measured"] if have else 0)
    lows = values + ([have["low"]] if have and have["low"] is not None else [])
    highs = values + ([have["high"]] if have and have["high"] is not None else [])
    newest = rows[-1]
    last, last_at, resets_at = newest["utilization"], newest["at"], newest["resets_at"]
    if have and have["last_at"] > last_at:
        last, last_at, resets_at = have["last"], have["last_at"], have["resets_at"]
    return (samples, measured, min(lows) if lows else None, max(highs) if highs else None,
            last, last_at, resets_at)


def compact(now: Optional[float] = None) -> int:
    """Fold old readings into hours now (the append does it once an hour)."""
    conn = _connect()
    try:
        return _compact(conn, time.time() if now is None else float(now))
    finally:
        conn.close()


# ── reading ────────────────────────────────────────────────────────────────

class Refused(ValueError):
    """A request that cannot be answered, said plainly."""


def _number(raw: Any, what: str) -> Optional[float]:
    if raw is None or raw == "":
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        raise Refused(f"{what} must be a time in epoch seconds") from None
    if value != value or value < 0 or value in (float("inf"),):
        raise Refused(f"{what} must be a time in epoch seconds")
    return value


def _raw_points(conn, provider: str, since: float, until: float) -> list[dict]:
    rows = conn.execute(
        "SELECT at, account, utilization, resets_at, status FROM usage_readings"
        " WHERE provider = ? AND window = ? AND at >= ? AND at <= ? ORDER BY at, id",
        (provider, WINDOW, since, until)).fetchall()
    return [{"at": r["at"], "used": r["utilization"], "low": r["utilization"], "high": r["utilization"],
             "resets_at": r["resets_at"], "status": r["status"], "account": r["account"], "samples": 1}
            for r in rows]


def _hour_points(conn, provider: str, since: float, until: float,
                 *, readings: bool = True) -> list[dict]:
    """By the hour: the kept summaries, and (with `readings`) the readings
    not yet folded, summarised the same way. An hour's `used` is its highest
    reading: the least left in it."""
    hours: dict[tuple, dict] = {}
    for r in conn.execute(
            "SELECT * FROM usage_hourly WHERE provider = ? AND window = ? AND hour >= ? AND hour <= ?",
            (provider, WINDOW, _hour(since), until)).fetchall():
        hours[(r["hour"], r["account"])] = dict(r)
    raw: dict[tuple, list] = {}
    for r in conn.execute(
            "SELECT at, account, utilization, resets_at FROM usage_readings"
            " WHERE provider = ? AND window = ? AND at >= ? AND at <= ? ORDER BY at, id",
            (provider, WINDOW, _hour(since), until)).fetchall() if readings else []:
        raw.setdefault((_hour(r["at"]), r["account"] or ""), []).append(r)
    for key, rows in raw.items():
        have = hours.get(key)
        samples, measured, low, high, last, last_at, resets_at = _summarise(rows, have)
        hours[key] = {"hour": key[0], "account": key[1], "samples": samples, "measured": measured,
                      "low": low, "high": high, "last": last, "last_at": last_at, "resets_at": resets_at}
    out = [{"at": h["hour"], "used": h["high"], "low": h["low"], "high": h["high"],
            "resets_at": h["resets_at"], "status": "", "account": h["account"] or None,
            "samples": h["samples"]}
           for h in hours.values()]
    return sorted(out, key=lambda p: (p["at"], p["account"] or ""))


def _latest(provider: str) -> dict:
    """usage_store's stored seven-day reading, as facts only (no age)."""
    from jarvis import usage_store
    stored = usage_store.latest(provider) or {}
    windows = stored.get("windows") if isinstance(stored.get("windows"), dict) else {}
    raw = windows.get(WINDOW) if isinstance(windows.get(WINDOW), dict) else {}
    return {"used": usage_store._stored_percent(raw.get("utilization")),
            "resets_at": usage_store._epoch(raw.get("resets_at")),
            "observed_at": usage_store._epoch(raw.get("observed_at")),
            "status": usage_store._text(raw.get("status"))}


def weeks_of(points: list[dict]) -> list[dict]:
    """The readings by the week they belong to, newest week first: a week is
    its reset time (two within SAME_RESET_SEC are one) and starts WEEK_SEC
    before it. A reading that names no reset goes in the week whose span
    holds it; one no week holds is left out, since it cannot be placed on a
    burn-down."""
    weeks: list[dict] = []
    for p in points:
        r = p.get("resets_at")
        if r is None:
            continue
        week = next((w for w in weeks if abs(w["resets_at"] - r) <= SAME_RESET_SEC), None)
        if week is None:
            weeks.append({"resets_at": r, "start": r - WEEK_SEC, "points": []})
    for p in points:
        r = p.get("resets_at")
        if r is not None:
            week = next(w for w in weeks if abs(w["resets_at"] - r) <= SAME_RESET_SEC)
        else:
            week = next((w for w in weeks if w["start"] <= p["at"] <= w["resets_at"]), None)
        if week is not None:
            week["points"].append(p)
    for w in weeks:
        w["points"].sort(key=lambda p: p["at"])
        w["measured"] = sum(1 for p in w["points"] if p["used"] is not None)
    return sorted(weeks, key=lambda w: w["resets_at"], reverse=True)


def read(provider: Optional[str] = None, since: Any = None, until: Any = None,
         resolution: Optional[str] = None, *, now: Optional[float] = None) -> dict:
    """One brain's weekly history: its seven-day readings from `since`
    (the start of the record when left out) to `until` (now), grouped into
    weeks, newest first, each with its reset time, its start and its points
    (`used` a percentage or None: not measured), and the latest reading.
    `resolution` is "raw" (every reading), "hour" (one point per hour, its
    highest reading) or "auto" (raw up to two days, else by the hour)."""
    at = time.time() if now is None else float(now)
    end = _number(until, "until")
    end = at if end is None else end
    start = _number(since, "since")
    start = 0.0 if start is None else start
    if start > end:
        raise Refused("since must be before until")
    how = (resolution or "auto").strip().lower()
    if how not in RESOLUTIONS:
        raise Refused(f"resolution must be one of {', '.join(RESOLUTIONS)}")
    if how == "auto":
        how = "raw" if end - start <= RAW_RANGE_MAX_SEC else "hour"
    name = (provider or "").strip()
    if not name:
        from jarvis import usage_store
        name = usage_store.PROVIDER
    conn = _connect()
    try:
        if how == "raw":
            # Readings already folded into hours (older than two weeks) are
            # read by the hour.
            folded = _hour_points(conn, name, start, end, readings=False)
            points = [p for p in folded if p["at"] >= start] + _raw_points(conn, name, start, end)
        else:
            points = _hour_points(conn, name, start, end)
    finally:
        conn.close()
    return {"provider": name, "window": WINDOW, "week_sec": WEEK_SEC,
            "since": start, "until": end, "resolution": how, "raw_keep_sec": RAW_KEEP_SEC,
            "latest": _latest(name), "weeks": weeks_of(points)}
