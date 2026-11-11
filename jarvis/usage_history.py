"""The long-term record of the subscription brains' weekly usage.

usage_store keeps only the latest reading per brain provider. Every reading
of the seven-day window it takes (`usage_store.record`) is also kept here,
in JARVIS's records: one `usage` document a brain a day (UTC),
`/usage/<brain>/<date>.usage` (`path_of`). Only the week is kept: Tim's
word, the five-hour window's history has no value, so its readings are
never written.

  readings  every reading as it came: when, provider, account hash, window,
            utilization (a percentage, None when the reading did not
            measure it), resets-at and status. Kept for RAW_KEEP_SEC.
  hourly    after that, one entry per (window, account, hour): how many
            readings and how many measured, the lowest, the highest and the
            last percentage, and the last reset time. Kept for good.

Each entry keeps the column names of the tables it replaced
(`usage_readings`, `usage_hourly`), so the move (`MAPPINGS`) is plain; an
hour's figures for readings not yet folded are worked out when read.

The rule is usage_store's: absence is a state. A window not measured is
None, never 0, and an hour with no measured reading has None percentages.
The page joins the measured points into one line and passes over the rest;
nothing unmeasured is drawn as 0.

`read` is the one reader: `GET /api/usage/history` and the brain's
`usage_history` both return what it returns: one brain's weeks, newest
first, each with its readings, for the page to draw each week's burn-down
the way the brain's box draws this one.

The account is brain_accounts' keyed hash of the signed-in subscription
(`brain_accounts.history_ref`), the same reference the Network view shows;
None while it is not known (no team key, or the sign-in not read yet).
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Iterable, Optional

from jarvis import data_paths, knowledge_move as move, records

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

ROOT = "/usage"
TYPE = "usage"
READING_COLUMNS = ("id", "at", "provider", "account", "window", "utilization", "resets_at", "status")
HOUR_COLUMNS = ("hour", "provider", "account", "window", "samples", "measured", "low", "high",
                "last", "last_at", "resets_at")

_backfilled: set[str] = set()
_backfilling = threading.Lock()
_last_compact: dict[str, float] = {}


def day_of(at: Any) -> str:
    """The UTC date a reading (or an hour) belongs to, YYYY-MM-DD."""
    try:
        return time.strftime("%Y-%m-%d", time.gmtime(float(at)))
    except (TypeError, ValueError, OverflowError, OSError):
        return "0000-00-00"


def path_of(provider: str, day: str) -> str:
    return f"{ROOT}/{records.segment(provider)}/{day}.{TYPE}"


def _day_doc(provider: Any, day: str, readings: list, hourly: list) -> dict:
    return {"provider": move.cell(provider), "date": day, "readings": readings, "hourly": hourly}


# ── the move: the two tables as one document a brain a day ─────────────────

class _UsageMapping:
    name = "usage"
    tables = ("usage_readings", "usage_hourly")
    under = (ROOT,)
    history = False

    def documents(self, conn) -> Iterable[move.Doc]:
        days: dict[tuple, dict] = {}
        for table, kind, when in (("usage_readings", "readings", "at"), ("usage_hourly", "hourly", "hour")):
            for row in move.table_rows(conn, table):
                provider = row.cells["provider"]
                key = (move.storage_class(provider), str(provider) if not isinstance(provider, bytes)
                       else provider.hex(), day_of(row.cells[when]))
                doc = days.setdefault(key, _day_doc(provider, key[2], [], []))
                doc[kind].append(move.row_data(row))
        for (_, name, day), data in days.items():
            yield move.Doc(path_of(name, day), data)

    def rows(self, docs: Iterable[move.Doc]) -> dict[str, list[move.Row]]:
        readings, hourly = [], []
        for doc in docs:
            readings += [move.data_row(r, READING_COLUMNS) for r in doc.data.get("readings") or ()]
            hourly += [move.data_row(r, HOUR_COLUMNS) for r in doc.data.get("hourly") or ()]
        return {"usage_readings": move.numbered(readings, "id"), "usage_hourly": move.numbered(hourly)}


MAPPINGS = [_UsageMapping()]


def _backfill() -> None:
    """Once, into an engine holding no usage history yet: the seven-day
    readings usage_store already holds, its latest and its burn-down
    history."""
    key = records.engine_key()
    if key in _backfilled:
        return
    with _backfilling:
        if key in _backfilled:
            return
        if next(records.scan(ROOT, limit=1), None) is None:
            _write(_stored_readings())
        _backfilled.add(key)


def _stored_readings() -> list[dict]:
    from jarvis import usage_store
    rows: list[dict] = []
    for service in _stored_services():
        stored = usage_store.latest(service)
        if not stored:
            continue
        week_resets = usage_store._epoch(stored.get("week_resets_at"))
        for at, used in stored.get("week_history") or []:
            when = usage_store._epoch(at)
            if when is not None:
                rows.append(_reading(when, service, None, WINDOW, usage_store._stored_percent(used),
                                     week_resets, ""))
        for key, raw in (stored.get("windows") or {}).items():
            if key != WINDOW or not isinstance(raw, dict):
                continue
            when = usage_store._epoch(raw.get("observed_at"))
            if when is None or any(r["at"] == when and r["provider"] == service for r in rows):
                continue
            rows.append(_reading(when, service, None, key, usage_store._stored_percent(raw.get("utilization")),
                                 usage_store._epoch(raw.get("resets_at")), usage_store._text(raw.get("status"))))
    return rows


def _reading(at, provider, account, window, utilization, resets_at, status) -> dict:
    """One reading as its document keeps it (its id is given when the
    record is written back as a table, `numbered`)."""
    return {"at": float(at), "provider": provider, "account": account, "window": window,
            "utilization": utilization, "resets_at": resets_at, "status": status}


def _write(readings: list[dict]) -> None:
    """Add readings to their days' documents, each day in one write."""
    by_day: dict[str, list[dict]] = {}
    for r in readings:
        by_day.setdefault(path_of(r["provider"], day_of(r["at"])), []).append(r)
    for path, new in by_day.items():
        def add(data: Optional[dict], new=new) -> dict:
            data = data or _day_doc(new[0]["provider"], day_of(new[0]["at"]), [], [])
            data["readings"] = list(data.get("readings") or []) + new
            return data
        records.update(path, add, change="reading",
                       summary=f"{len(new)} usage reading{'s' if len(new) != 1 else ''}")


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
    readings went in. Never raises: losing a history reading must not lose
    the reading."""
    rows = [_reading(float(w.get("observed_at") or now or time.time()), service, account, key,
                     w.get("utilization"), w.get("resets_at"), str(w.get("status") or ""))
            for key, w in windows.items() if key == WINDOW and isinstance(w, dict)]
    if not rows:
        return 0
    try:
        _backfill()
        _write(rows)
        at = time.time() if now is None else float(now)
        key = records.engine_key()
        if at - _last_compact.get(key, 0.0) >= COMPACT_EVERY_SEC:
            _last_compact[key] = at
            _compact(at)
    except Exception as e:
        log.warning(f"usage_history: could not keep the reading ({type(e).__name__}: {e})")
        return 0
    return len(rows)


def _hour(at: float) -> float:
    return float(int(at // HOUR_SEC) * HOUR_SEC)


def _compact(now: float) -> int:
    """Fold every reading older than RAW_KEEP_SEC (whole hours only) into its
    hour's summary and drop it, each day in one write. Returns readings
    folded."""
    cutoff = _hour(now - RAW_KEEP_SEC)
    folded = 0
    for rec in records.scan(ROOT, types=[TYPE], where=[["date", "<=", day_of(cutoff)]]):
        if not any(r.get("at", cutoff) < cutoff for r in rec.data.get("readings") or ()):
            continue
        moved = [0]

        def fold(data: Optional[dict]) -> Optional[dict]:
            if data is None:
                return None
            old = [r for r in data.get("readings") or () if r["at"] < cutoff]
            if not old:
                return None
            moved[0] = len(old)
            hourly = list(data.get("hourly") or [])
            groups: dict[tuple, list] = {}
            for r in sorted(old, key=lambda r: (r["at"], r.get("id") or 0)):
                groups.setdefault((r["window"], r.get("account") or "", _hour(r["at"])), []).append(r)
            for (window, account, hour), rows in groups.items():
                at = next((i for i, h in enumerate(hourly) if (h["window"], h["account"], h["hour"])
                           == (window, account, hour)), None)
                have = hourly[at] if at is not None else None
                samples, measured, low, high, last, last_at, resets_at = _summarise(rows, have)
                entry = {**(have or {}), "hour": hour, "provider": rows[0]["provider"], "account": account,
                         "window": window, "samples": samples, "measured": measured, "low": low,
                         "high": high, "last": last, "last_at": last_at, "resets_at": resets_at}
                if at is None:
                    hourly.append(entry)
                else:
                    hourly[at] = entry
            data["readings"] = [r for r in data["readings"] if r["at"] >= cutoff]
            data["hourly"] = hourly
            return data
        if records.update(rec.path, fold, change="compact", summary="readings folded into hours") is not None:
            folded += moved[0]
    return folded


def _summarise(rows: list, have: Optional[dict]) -> tuple:
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
    _backfill()
    return _compact(time.time() if now is None else float(now))


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




def _days(provider: str, since: float, until: float) -> list[dict]:
    """The brain's day documents from the day holding `since` to the day
    holding `until`."""
    where = [["date", ">=", day_of(since)], ["date", "<=", day_of(until)]]
    return [r.data for r in records.scan(f"{ROOT}/{records.segment(provider)}", types=[TYPE], where=where)
            if r.data.get("provider") == provider]


def _readings(days: list[dict], since: float, until: float) -> list[dict]:
    """The seven-day readings from `since` to `until`, in the order they
    were taken."""
    found = [(r["at"], r.get("id") if isinstance(r.get("id"), int) else float("inf"), n, r)
             for d in days for n, r in enumerate(d.get("readings") or ())
             if r.get("window") == WINDOW and since <= r["at"] <= until]
    return [r for *_, r in sorted(found, key=lambda x: x[:3])]


def _raw_points(days: list[dict], since: float, until: float) -> list[dict]:
    return [{"at": r["at"], "used": r["utilization"], "low": r["utilization"], "high": r["utilization"],
             "resets_at": r["resets_at"], "status": r["status"], "account": r["account"], "samples": 1}
            for r in _readings(days, since, until)]


def _hour_points(days: list[dict], since: float, until: float,
                 *, readings: bool = True) -> list[dict]:
    """By the hour: the kept summaries, and (with `readings`) the readings
    not yet folded, summarised the same way. An hour's `used` is its highest
    reading: the least left in it."""
    hours: dict[tuple, dict] = {}
    for d in days:
        for h in d.get("hourly") or ():
            if h.get("window") == WINDOW and _hour(since) <= h["hour"] <= until:
                hours[(h["hour"], h["account"])] = dict(h)
    raw: dict[tuple, list] = {}
    for r in _readings(days, _hour(since), until) if readings else []:
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
    _backfill()
    days = _days(name, _hour(start), end)
    if how == "raw":
        # Readings already folded into hours (older than two weeks) are
        # read by the hour.
        folded = _hour_points(days, start, end, readings=False)
        points = [p for p in folded if p["at"] >= start] + _raw_points(days, start, end)
    else:
        points = _hour_points(days, start, end)
    return {"provider": name, "window": WINDOW, "week_sec": WEEK_SEC,
            "since": start, "until": end, "resolution": how, "raw_keep_sec": RAW_KEEP_SEC,
            "latest": _latest(name), "weeks": weeks_of(points)}
