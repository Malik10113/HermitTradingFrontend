"""UTC-only time helpers used by the data layer.

Every stored timestamp is timezone-aware UTC.  Naive datetimes are rejected at
the boundary rather than silently interpreted in the machine's local zone.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

NS_PER_SECOND = 1_000_000_000
NS_PER_MINUTE = 60 * NS_PER_SECOND
NS_PER_HOUR = 60 * NS_PER_MINUTE
NS_PER_DAY = 24 * NS_PER_HOUR


class NaiveTimestampError(ValueError):
    """A timestamp without an explicit timezone reached the data layer."""


def as_utc(value: datetime | int | float | str) -> datetime:
    """Return ``value`` in UTC, rejecting naive datetimes.

    Numeric values are accepted as Unix seconds (or nanoseconds when the
    magnitude makes that unambiguous); strings must be ISO-8601.
    """
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        number = float(value)
        seconds = number / NS_PER_SECOND if abs(number) > 10**17 else number
        return datetime.fromtimestamp(seconds, tz=timezone.utc)
    if isinstance(value, str):
        return parse_iso_utc(value)
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        raise NaiveTimestampError(
            "naive datetime is not allowed; all timestamps must be timezone-aware UTC"
        )
    return value.astimezone(timezone.utc)


def parse_iso_utc(text: str) -> datetime:
    """Parse an ISO-8601 timestamp, accepting ``Z``, and return aware UTC."""
    raw = text.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    return as_utc(datetime.fromisoformat(raw))


def to_ns(value: datetime) -> int:
    """Aware datetime -> Unix nanoseconds."""
    return int(as_utc(value).timestamp() * NS_PER_SECOND)


def from_ns(value: int) -> datetime:
    """Unix nanoseconds -> aware UTC datetime."""
    return datetime.fromtimestamp(value / NS_PER_SECOND, tz=timezone.utc)


def isoformat_z(value: datetime | int | None) -> str | None:
    """Format an aware datetime or Unix nanoseconds as ``...Z``."""
    if value is None:
        return None
    dt = from_ns(value) if isinstance(value, int) else as_utc(value)
    return dt.isoformat().replace("+00:00", "Z")


def date_to_utc(value: date | datetime) -> datetime:
    """A date means midnight UTC; a datetime must already be aware."""
    if isinstance(value, datetime):
        return as_utc(value)
    return datetime(value.year, value.month, value.day, tzinfo=timezone.utc)


def seconds_to_ns(value: int | float) -> int:
    """Tape timestamps are unix seconds; convert to nanoseconds."""
    return int(value) * NS_PER_SECOND
