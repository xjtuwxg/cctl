"""Time parsing shared by lease commands. Blazar speaks naive UTC."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from .errors import CctlError

BLAZAR_FMT = "%Y-%m-%d %H:%M"
_DURATION = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([wdhm])\s*$", re.I)


def now_utc() -> datetime:
    return datetime.now(timezone.utc).replace(second=0, microsecond=0)


def parse_duration(text: str) -> timedelta:
    """'7d', '36h', '2w', '90m'. Plain number means hours."""
    if text is None:
        raise CctlError("missing duration")
    text = str(text)
    if text.replace(".", "", 1).isdigit():
        return timedelta(hours=float(text))
    m = _DURATION.match(text)
    if not m:
        raise CctlError(f"bad duration '{text}' (use e.g. 2h, 3d, 1w)")
    n, unit = float(m.group(1)), m.group(2).lower()
    return {"w": timedelta(weeks=n), "d": timedelta(days=n), "h": timedelta(hours=n), "m": timedelta(minutes=n)}[unit]


def parse_when(text: str | None, default: datetime | None = None) -> datetime:
    """Accepts 'now', '+2h', 'YYYY-MM-DD HH:MM' (local time), 'YYYY-MM-DDTHH:MM[Z]'.
    Returns an aware UTC datetime."""
    if text is None or text.strip().lower() in ("", "now"):
        return default or now_utc()
    t = text.strip()
    if t.startswith("+"):
        return (default or now_utc()) + parse_duration(t[1:])
    if t.endswith("Z"):
        dt = datetime.fromisoformat(t[:-1]).replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    try:
        dt = datetime.fromisoformat(t)
    except ValueError:
        raise CctlError(f"cannot parse time '{text}' (use 'YYYY-MM-DD HH:MM' local time, '+3h', or 'now')")
    if dt.tzinfo is None:
        dt = dt.astimezone()  # interpret as local time
    return dt.astimezone(timezone.utc).replace(second=0, microsecond=0)


def to_blazar(dt: datetime) -> str:
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc)
    return dt.strftime(BLAZAR_FMT)


def from_blazar(text: str | None) -> datetime | None:
    """Parse '2026-09-07T20:55:00.000000' or '2026-09-07 20:55' as UTC."""
    if not text:
        return None
    t = text.replace("T", " ").split(".")[0]
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(t, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    try:
        dt = datetime.fromisoformat(text)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None
