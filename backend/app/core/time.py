"""
Time Utilities — UTC storage + Thai display helpers.

All timestamps are stored in UTC. Display functions convert to Asia/Bangkok (UTC+7).
This avoids timezone bugs in trade journaling and analytics.
"""

from datetime import datetime, timezone, timedelta

# Thai timezone offset (UTC+7)
TH_OFFSET = timedelta(hours=7)
TH_TZ = timezone(TH_OFFSET)


def utc_now() -> datetime:
    """Current time in UTC (timezone-aware)."""
    return datetime.now(timezone.utc)


def to_thai(dt: datetime) -> datetime:
    """Convert a UTC datetime to Thai time (UTC+7) for display."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(TH_TZ)


def format_thai(dt: datetime, fmt: str = "%Y-%m-%d %H:%M:%S") -> str:
    """Format a datetime as Thai local time string."""
    return to_thai(dt).strftime(fmt)


def utc_from_timestamp(ts: float) -> datetime:
    """Create UTC datetime from Unix timestamp."""
    return datetime.fromtimestamp(ts, tz=timezone.utc)
