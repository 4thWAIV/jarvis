"""The long-term record of the subscription brains' usage readings.

usage_store keeps only the latest reading per brain provider. Every
observation it takes (`usage_store.record`) is also appended here, one row
per window, in JARVIS's database under its own schema version
(`usage_history_schema`, run_store.migrate):

  usage_readings  every reading as it came: when, provider, account hash,
                  window, utilization (a percentage, NULL when the reading
                  did not measure it), resets-at and status. Kept for
                  RAW_KEEP_SEC.
  usage_hourly    after that, one row per (provider, account, window, hour):
                  how many readings and how many measured, the lowest, the
                  highest and the last percentage, and the last reset time.
                  Kept for good (four rows an hour, about 35,000 a year).

The rule is usage_store's: absence is a state. A window not measured is
NULL, never 0; an hour with no measured reading has NULL percentages; and a
stretch with no readings at all is a gap, never a line drawn through zero.

`read` is the one reader: `GET /api/usage/history` and the brain's
`usage_history` both return what it returns.

The account is brain_accounts' keyed hash of the signed-in subscription
(`brain_accounts.history_ref`), the same reference the Network view shows;
NULL while it is not known (no team key, or the sign-in not read yet).
"""
from __future__ import annotations

import logging
import sqlite3
import threading
import time
from typing import Any, Iterable, Optional

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
# Two neighbouring points further apart than this have nothing measured
# between them: the page draws a gap. The poll reads once a minute, so a
# quarter of an hour missing is a real hole; by the hour, a missing hour.
GAP_SEC = {"raw": 15 * 60, "hour": 2 * HOUR_SEC}
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
_MIGRATIONS = {1: _SCHEMA}

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
    """Once, as the tables are made: the readings usage_store already holds,
    each window's latest and the seven-day window's burn-down history."""
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
            if not isinstance(key, str) or not isinstance(raw, dict):
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
    """Append one observation's windows (usage_store's normalised shape:
    utilization as a percentage or None, resets_at, status, observed_at).
    Returns how many rows went in. Never raises: losing a history row must
    not lose the reading."""
    rows = [(float(w.get("observed_at") or now or time.time()), service, account, key,
             w.get("utilization"), w.get("resets_at"), str(w.get("status") or ""))
            for key, w in windows.items() if isinstance(key, str) and isinstance(w, dict)]
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
        raise Refused(f"{what} must be a time in epoch seconds, not {str(raw)[:40]!r}") from None
    if value != value or value < 0 or value in (float("inf"),):
        raise Refused(f"{what} must be a time in epoch seconds")
    return value


def _resets(points: Iterable[dict], since: float, until: float) -> list[float]:
    """The reset times that fell inside the range, each once."""
    out: list[float] = []
    for p in points:
        r = p.get("resets_at")
        if r is None or not since <= r <= until:
            continue
        if not any(abs(r - seen) <= SAME_RESET_SEC for seen in out):
            out.append(r)
    return sorted(out)


def _raw_points(conn, provider: str, window: str, since: float, until: float) -> list[dict]:
    rows = conn.execute(
        "SELECT at, account, utilization, resets_at, status FROM usage_readings"
        " WHERE provider = ? AND window = ? AND at >= ? AND at <= ? ORDER BY at, id",
        (provider, window, since, until)).fetchall()
    return [{"at": r["at"], "used": r["utilization"], "low": r["utilization"], "high": r["utilization"],
             "resets_at": r["resets_at"], "status": r["status"], "account": r["account"], "samples": 1}
            for r in rows]


def _hour_points(conn, provider: str, window: str, since: float, until: float,
                 *, readings: bool = True) -> list[dict]:
    """By the hour: the kept summaries, and (with `readings`) the readings
    not yet folded, summarised the same way. An hour's `used` is its highest
    reading."""
    hours: dict[tuple, dict] = {}
    for r in conn.execute(
            "SELECT * FROM usage_hourly WHERE provider = ? AND window = ? AND hour >= ? AND hour <= ?",
            (provider, window, _hour(since), until)).fetchall():
        hours[(r["hour"], r["account"])] = dict(r)
    raw: dict[tuple, list] = {}
    for r in conn.execute(
            "SELECT at, account, utilization, resets_at FROM usage_readings"
            " WHERE provider = ? AND window = ? AND at >= ? AND at <= ? ORDER BY at, id",
            (provider, window, _hour(since), until)).fetchall() if readings else []:
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


def _providers(conn, provider: Optional[str]) -> list[str]:
    if provider:
        return [provider]
    from jarvis import brains_config
    found = list(brains_config.load()["brains"])
    for r in conn.execute("SELECT DISTINCT provider FROM usage_readings UNION"
                          " SELECT DISTINCT provider FROM usage_hourly").fetchall():
        if r[0] not in found:
            found.append(r[0])
    return found


def _windows(conn, provider: str, window: Optional[str]) -> list[str]:
    from jarvis import usage_store
    if window:
        return [window]
    found = list(usage_store.KNOWN_WINDOWS)
    for r in conn.execute("SELECT DISTINCT window FROM usage_readings WHERE provider = ? UNION"
                          " SELECT DISTINCT window FROM usage_hourly WHERE provider = ?",
                          (provider, provider)).fetchall():
        if r[0] not in found:
            found.append(r[0])
    return found


def _latest(provider: str) -> list[dict]:
    """usage_store's stored reading per window, as facts only (no age)."""
    from jarvis import usage_store
    stored = usage_store.latest(provider) or {}
    windows = stored.get("windows") if isinstance(stored.get("windows"), dict) else {}
    out = []
    for key in list(usage_store.KNOWN_WINDOWS) + [k for k in windows if k not in usage_store.KNOWN_WINDOWS]:
        raw = windows.get(key) if isinstance(windows.get(key), dict) else {}
        out.append({"window": key,
                    "used": usage_store._stored_percent(raw.get("utilization")),
                    "resets_at": usage_store._epoch(raw.get("resets_at")),
                    "observed_at": usage_store._epoch(raw.get("observed_at")),
                    "status": usage_store._text(raw.get("status"))})
    return out


def read(provider: Optional[str] = None, window: Optional[str] = None,
         since: Any = None, until: Any = None, resolution: Optional[str] = None,
         *, now: Optional[float] = None) -> dict:
    """The history: one series per brain provider and window, each with its
    points (`used` a percentage or None: not measured), the resets inside the
    range and the latest stored reading. `since` defaults to a day before
    `until`, `until` to now. `resolution` is "raw" (every reading), "hour"
    (one point per hour, its highest reading) or "auto" (raw up to two days,
    else by the hour)."""
    at = time.time() if now is None else float(now)
    end = _number(until, "until")
    end = at if end is None else end
    start = _number(since, "since")
    start = end - 24 * 3600 if start is None else start
    if start > end:
        raise Refused("since must be before until")
    how = (resolution or "auto").strip().lower()
    if how not in RESOLUTIONS:
        raise Refused(f"resolution must be one of {', '.join(RESOLUTIONS)}")
    if how == "auto":
        how = "raw" if end - start <= RAW_RANGE_MAX_SEC else "hour"
    from jarvis import usage_store
    conn = _connect()
    try:
        series = []
        for name in _providers(conn, (provider or "").strip() or None):
            latest = {w["window"]: w for w in _latest(name)}
            for key in _windows(conn, name, (window or "").strip() or None):
                if how == "raw":
                    points = _raw_points(conn, name, key, start, end)
                    # Readings already folded into hours (older than two
                    # weeks) are read by the hour.
                    folded = _hour_points(conn, name, key, start, end, readings=False)
                    points = [p for p in folded if p["at"] >= start] + points
                else:
                    points = _hour_points(conn, name, key, start, end)
                series.append({
                    "provider": name, "window": key, "label": usage_store.window_label(key),
                    "points": points, "resets": _resets(points, start, end),
                    "latest": latest.get(key) or {"window": key, "used": None, "resets_at": None,
                                                  "observed_at": None, "status": ""}})
    finally:
        conn.close()
    return {"since": start, "until": end, "resolution": how, "gap_sec": GAP_SEC[how],
            "raw_keep_sec": RAW_KEEP_SEC, "series": series}
