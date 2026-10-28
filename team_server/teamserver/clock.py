"""The one source of "now" (UTC, aware), so tests can move time."""
from datetime import datetime, timezone


def now() -> datetime:
    return datetime.now(timezone.utc)
