"""Centralized Indian Standard Time (IST — Asia/Kolkata) timezone service for TicNet.

Provides application-wide timezone operations ensuring:
- Standard IANA timezone identifier: 'Asia/Kolkata' (UTC+05:30)
- Explicit timezone-aware datetime objects
- Consistent ISO 8601 formatting with '+05:30' offset
- Deterministic parsing and conversion from UTC / naive timestamps
- Calendar day boundary calculations in IST
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
import re
from typing import Any, Optional, Tuple, Union
from zoneinfo import ZoneInfo

# Authoritative IANA timezone identifier and timezone instance
IST_TIMEZONE_ID = "Asia/Kolkata"
IST = ZoneInfo(IST_TIMEZONE_ID)
UTC = timezone.utc

# Default presentation format specified in requirement #6: "30 Oct 2026, 01:30:00 PM IST"
DEFAULT_IST_FORMAT = "%d %b %Y, %I:%M:%S %p IST"
SHORT_IST_FORMAT = "%d %b %Y, %I:%M %p"
TIME_ONLY_IST_FORMAT = "%I:%M:%S %p IST"
TIME_24H_FORMAT = "%H:%M:%S"
DATE_ONLY_IST_FORMAT = "%d %b %Y"


def now_ist() -> datetime:
    """Return the current timezone-aware datetime in Indian Standard Time (Asia/Kolkata)."""
    return datetime.now(IST)


def now_ist_iso() -> str:
    """Return the current IST datetime formatted as an ISO 8601 string with '+05:30' offset."""
    return now_ist().isoformat()


def today_ist() -> date:
    """Return the current calendar date in IST."""
    return now_ist().date()


def to_ist(val: Any) -> Optional[datetime]:
    """Convert an arbitrary timestamp representation to a timezone-aware IST datetime.

    Accepts:
    - datetime (aware or naive, where naive is assumed UTC)
    - float / int (UNIX epoch timestamp in seconds)
    - str (ISO 8601 string, 'Z' suffix, or standard date/time string)
    - None (returns None)

    Idempotent: If the input is already an IST datetime or string with '+05:30',
    its actual wall-clock representation is preserved without double-shifting.
    """
    if val is None:
        return None

    if isinstance(val, (int, float)):
        return datetime.fromtimestamp(val, tz=IST)

    if isinstance(val, datetime):
        if val.tzinfo is None:
            # Interpret naive datetime as UTC
            return val.replace(tzinfo=UTC).astimezone(IST)
        return val.astimezone(IST)

    if isinstance(val, str):
        clean = val.strip()
        if not clean:
            return None

        # Standard ISO 8601 parsing
        try:
            iso_str = clean[:-1] + "+00:00" if clean.endswith("Z") else clean
            dt = datetime.fromisoformat(iso_str)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)
            return dt.astimezone(IST)
        except Exception:
            pass

        # Clean " UTC" suffix if present (e.g. "2026-10-30 08:00:00 UTC")
        if clean.upper().endswith(" UTC"):
            clean = clean[:-4].strip()
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S"):
                try:
                    dt = datetime.strptime(clean, fmt).replace(tzinfo=UTC)
                    return dt.astimezone(IST)
                except Exception:
                    pass

        # Clean " IST" suffix if present
        if clean.upper().endswith(" IST"):
            clean = clean[:-4].strip()
            for fmt in (
                "%d %b %Y, %I:%M:%S %p",
                "%d %b %Y, %I:%M %p",
                "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%d %H:%M",
            ):
                try:
                    dt = datetime.strptime(clean, fmt).replace(tzinfo=IST)
                    return dt
                except Exception:
                    pass

        # Fallback common formats
        for fmt in (
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d %H:%M",
            "%Y-%m-%d",
            "%d-%m-%Y %H:%M:%S",
            "%d/%m/%Y %H:%M:%S",
        ):
            try:
                dt = datetime.strptime(clean, fmt).replace(tzinfo=UTC)
                return dt.astimezone(IST)
            except Exception:
                pass

    return None


def to_ist_iso(val: Any) -> Optional[str]:
    """Convert input to an ISO 8601 string with '+05:30' offset."""
    dt = to_ist(val)
    return dt.isoformat() if dt else None


def format_ist(
    val: Any,
    fmt: str = DEFAULT_IST_FORMAT,
    fallback: str = "—",
) -> str:
    """Format an input timestamp into an IST string conforming to presentation requirements.

    Default format produces: '30 Oct 2026, 01:30:00 PM IST'.
    """
    dt = to_ist(val)
    if dt is None:
        return fallback
    return dt.strftime(fmt)


def format_ist_short(val: Any, fallback: str = "—") -> str:
    """Format as short IST string: '30 Oct 2026, 01:30 PM'."""
    return format_ist(val, fmt=SHORT_IST_FORMAT, fallback=fallback)


def format_ist_time(val: Any, fallback: str = "—") -> str:
    """Format as IST time string: '01:30:00 PM IST'."""
    return format_ist(val, fmt=TIME_ONLY_IST_FORMAT, fallback=fallback)


def format_ist_date(val: Any, fallback: str = "—") -> str:
    """Format as IST date string: '30 Oct 2026'."""
    return format_ist(val, fmt=DATE_ONLY_IST_FORMAT, fallback=fallback)


def ist_day_bounds(
    target: Optional[Union[datetime, date, str]] = None,
) -> Tuple[datetime, datetime]:
    """Calculate the start and end of day in IST for daily aggregation boundaries.

    Returns:
        (day_start_ist, day_end_ist) where start is 00:00:00.000000 and end is 23:59:59.999999.
    """
    if target is None:
        target_date = today_ist()
    elif isinstance(target, datetime):
        target_date = to_ist(target).date() if target.tzinfo else target.date()
    elif isinstance(target, date):
        target_date = target
    elif isinstance(target, str):
        parsed = to_ist(target)
        target_date = parsed.date() if parsed else today_ist()
    else:
        target_date = today_ist()

    day_start = datetime(
        year=target_date.year,
        month=target_date.month,
        day=target_date.day,
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
        tzinfo=IST,
    )
    day_end = datetime(
        year=target_date.year,
        month=target_date.month,
        day=target_date.day,
        hour=23,
        minute=59,
        second=59,
        microsecond=999999,
        tzinfo=IST,
    )
    return day_start, day_end


def parse_daily_time_ist(time_str: Optional[str]) -> Tuple[int, int]:
    """Parse an 'HH:MM' daily scheduled time string in IST into (hour, minute). Defaults to (9, 0)."""
    if not time_str or ":" not in time_str:
        return 9, 0
    try:
        parts = time_str.strip().split(":")
        h = int(parts[0]) % 24
        m = int(parts[1]) % 60
        return h, m
    except Exception:
        return 9, 0


def is_already_ist(val_str: Optional[str]) -> bool:
    """Check if a string timestamp is already explicitly marked with IST offset or identifier."""
    if not val_str:
        return False
    return "+05:30" in val_str or val_str.endswith("IST")

