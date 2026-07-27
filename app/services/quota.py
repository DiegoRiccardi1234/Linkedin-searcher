"""How many requests today, against the ceiling the provider actually enforces.

Nothing in the app knew that a free OpenRouter account gets 1000 requests a day,
shared across every key on it. One logical scoring call can become several HTTP
requests (a few models tried, each with retries), so a long scan could walk into
the ceiling and only find out through a wall of 429s — after spending the
morning's budget on jobs it then scored with the local heuristic.

The count is read from ``usage_log``, which has recorded every call since
v1.1.0. It is a *guard rail*, not accounting: a scan that would start past the
ceiling stops with an honest message instead of discovering it mid-run.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.log import get_logger

log = get_logger(__name__)

#: Free-tier OpenRouter: 1000 requests/day per ACCOUNT (not per key). Users on
#: other providers, or paying, can raise or disable it from settings.
DEFAULT_DAILY_LIMIT = 1000

#: Preference key holding the user's own ceiling (0 or empty = no limit).
LIMIT_PREFERENCE = "daily_request_limit"

#: Below this many remaining requests a scan is not worth starting: it would
#: stop a few offers in, leaving a half-scored archive.
MIN_HEADROOM = 20


def requests_today(db: Any) -> int:
    """LLM requests recorded since midnight UTC. 0 when the log is unreadable."""
    try:
        conn = db._get_connection() if hasattr(db, "_get_connection") else db.conn
        floor = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        row = conn.execute(
            "SELECT COUNT(*) FROM usage_log WHERE ts >= ?",
            (floor.isoformat(timespec="seconds"),),
        ).fetchone()
        return int(row[0] if row else 0)
    except Exception as exc:  # pragma: no cover - defensive
        log.debug("quota lookup skipped: %s", exc)
        return 0


def daily_limit(db: Any) -> int:
    """The user's ceiling: their setting, else the free-tier default. 0 = off."""
    try:
        raw = db.get_preference(LIMIT_PREFERENCE, "")
    except Exception:  # pragma: no cover - defensive
        raw = ""
    if raw is None or str(raw).strip() == "":
        return DEFAULT_DAILY_LIMIT
    try:
        return max(0, int(str(raw).strip()))
    except ValueError:
        return DEFAULT_DAILY_LIMIT


def status(db: Any) -> dict[str, Any]:
    """``{used, limit, remaining, exhausted}`` for the UI and the scan gate."""
    limit = daily_limit(db)
    used = requests_today(db)
    remaining = max(0, limit - used) if limit else None
    return {
        "used": used,
        "limit": limit or None,
        "remaining": remaining,
        "exhausted": bool(limit and used >= limit),
    }


def blocks_scan(db: Any) -> str | None:
    """Why a scan should not start now, or None.

    Deliberately not a hard block on single actions (a cover letter, one chat
    turn): those cost one request and the user asked for them right now. A scan
    is the thing that spends hundreds.
    """
    limit = daily_limit(db)
    if not limit:
        return None
    used = requests_today(db)
    if used >= limit:
        return f"daily_limit_reached:{used}/{limit}"
    if limit - used < MIN_HEADROOM:
        return f"daily_limit_near:{used}/{limit}"
    return None
