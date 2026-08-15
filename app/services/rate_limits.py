"""How fast the app is allowed to ask, and how it finds out.

Measured on a real free tier: the scan scores with four calls in parallel and no
pause, which on a 15-a-minute ceiling is about ninety requests a minute against
a limit of fifteen. It worked only because the failover caught the 429s — the
app spent the scan being told "no" and retrying. Not hitting the limit is worth
more than recovering from it.

Three sources of truth, in this order:

1. what the user typed in Settings — their console, their numbers;
2. what the app has WATCHED: a 429 with the count of successful calls in the
   minute before it is the real ceiling, for this key, today;
3. the shipped defaults in ``app/data/provider_limits.json``.

Measurement beats the file on purpose. A free-tier limit belongs to a project
and a key, not to a provider: the same model is 500 requests a day for one user
and 50 for another, and Google stopped publishing the numbers outside a
logged-in console. Shipping one person's limits as everyone's would repeat the
mistake this release exists to undo.
"""

from __future__ import annotations

import json
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from fnmatch import fnmatch
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.db import Database
from app.log import get_logger

log = get_logger(__name__)

_LIMITS_FILE = Path(__file__).resolve().parents[1] / "data" / "provider_limits.json"
#: Where the user's own corrections live (one JSON blob, edited in Settings).
PREF_OVERRIDES = "provider_limits_override"

#: Call timestamps per (provider, model), for the pacer. Process-wide because a
#: scan scores on several threads and the limit is counted by the provider, not
#: by the thread.
_calls: dict[tuple[str, str], deque[float]] = {}
_lock = threading.Lock()


@dataclass(frozen=True)
class Limit:
    """What one model is allowed, and where the number came from."""

    rpm: int | None = None
    rpd: int | None = None
    tpm: int | None = None
    source: str = "default"

    def merged_with(self, other: Limit | None) -> Limit:
        """The stricter of the two, field by field."""
        if other is None:
            return self

        def lower(a: int | None, b: int | None) -> int | None:
            values = [v for v in (a, b) if v]
            return min(values) if values else None

        return Limit(
            rpm=lower(self.rpm, other.rpm),
            rpd=lower(self.rpd, other.rpd),
            tpm=lower(self.tpm, other.tpm),
            source=other.source
            if other.rpm and (not self.rpm or other.rpm < self.rpm)
            else self.source,
        )


@lru_cache(maxsize=1)
def _shipped() -> dict[str, Any]:
    try:
        data: Any = json.loads(_LIMITS_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("provider_limits.json unreadable (%s); no shipped defaults", exc)
        return {}
    # A file that parses but is a list would blow up later, far from here, with
    # an AttributeError nobody could trace back to this file.
    if not isinstance(data, dict):
        log.warning("provider_limits.json is not an object; no shipped defaults")
        return {}
    return data


def _match(table: dict[str, Any], model: str) -> dict[str, Any] | None:
    """The most specific pattern that matches, longest pattern first."""
    best: tuple[int, dict[str, Any]] | None = None
    for pattern, values in table.items():
        if pattern.startswith("_") or not isinstance(values, dict):
            continue
        if fnmatch(model.lower(), pattern.lower()):
            score = len(pattern.replace("*", ""))
            if best is None or score > best[0]:
                best = (score, values)
    return best[1] if best else None


def default_limit(provider: str, model: str) -> Limit | None:
    entry = _match(_shipped().get(provider or "", {}) or {}, model or "")
    if not entry:
        return None
    return Limit(
        rpm=entry.get("rpm"),
        rpd=entry.get("rpd"),
        tpm=entry.get("tpm"),
        source=entry.get("source") or "default",
    )


def user_limit(db: Database, provider: str, model: str) -> Limit | None:
    """What the user typed in Settings, if anything."""
    raw = db.get_preference(PREF_OVERRIDES, "") or ""
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    entry = (data.get(provider) or {}).get(model) if isinstance(data, dict) else None
    if not isinstance(entry, dict):
        return None
    return Limit(rpm=entry.get("rpm"), rpd=entry.get("rpd"), tpm=entry.get("tpm"), source="tuo")


def observed_limit(db: Database, provider: str, model: str, days: int = 7) -> Limit | None:
    """What the app has actually been allowed, read off its own call log.

    A 429 says "you have had enough"; counting the calls that went through in
    the sixty seconds before it says how many that was. The smallest such count
    seen recently is the ceiling this key really has — no documentation, no
    guessing, and it follows the provider when they change their mind.
    """
    rows = db.recent_usage(provider=provider, model=model, days=days)
    if not rows:
        return None
    stamps = [(r["ts"], bool(r["success"]), str(r.get("error_type") or "")) for r in rows]
    stamps.sort(key=lambda r: r[0])

    ceilings: list[int] = []
    for i, (ts, success, error) in enumerate(stamps):
        if success or "rate" not in error.lower():
            continue
        window_start = ts - 60.0
        count = sum(1 for t, ok, _ in stamps[:i] if ok and t >= window_start)
        if count:
            ceilings.append(count)
    rpm = min(ceilings) if ceilings else None

    # A daily ceiling shows up the same way, over a day instead of a minute.
    daily: list[int] = []
    for i, (ts, success, error) in enumerate(stamps):
        if success or "rate" not in error.lower():
            continue
        day_start = ts - 86400.0
        count = sum(1 for t, ok, _ in stamps[:i] if ok and t >= day_start)
        if count:
            daily.append(count)
    rpd = min(daily) if daily else None
    if rpm is None and rpd is None:
        return None
    return Limit(rpm=rpm, rpd=rpd, source="misurato")


def _combine(default: Limit | None, observed: Limit | None, override: Limit | None) -> Limit | None:
    """The precedence itself, given the three sources already read."""
    limit = (default or Limit()).merged_with(observed)
    if override:
        # An explicit answer replaces the guesses rather than being averaged
        # with them: the user is looking at their own console.
        return override
    return limit if (limit.rpm or limit.rpd or limit.tpm) else None


def resolve(db: Database, provider: str, model: str) -> tuple[Limit | None, ...]:
    """All four numbers in one pass: default, measured, user's, and the winner.

    The panel shows the three sources side by side AND needs the effective one
    to say whether today is spent. Reading them separately meant walking a week
    of ``usage_log`` twice per model, forty models per provider, on every load.
    """
    default = default_limit(provider, model)
    observed = observed_limit(db, provider, model)
    override = user_limit(db, provider, model)
    return default, observed, override, _combine(default, observed, override)


def effective_limit(db: Database | None, provider: str, model: str) -> Limit | None:
    """The limit to obey: the user's, then what was measured, then the default."""
    limit = default_limit(provider, model)
    if db is None:
        return limit
    return _combine(limit, observed_limit(db, provider, model), user_limit(db, provider, model))


def pace(
    provider: str,
    model: str,
    limit: Limit | None,
    sleep: Callable[[float], None] = time.sleep,
) -> float:
    """Wait, if asking now would break the per-minute limit. Returns the wait."""
    if not limit or not limit.rpm:
        return 0.0
    key = (provider, model)
    with _lock:
        window = _calls.setdefault(key, deque())
        now = time.time()
        while window and now - window[0] >= 60.0:
            window.popleft()
        if len(window) < limit.rpm:
            window.append(now)
            return 0.0
        wait = 60.0 - (now - window[0]) + 0.05
    if wait > 0:
        log.info("Pacing %s/%s: %.1fs to stay under %d/min", provider, model, wait, limit.rpm)
        sleep(wait)
    with _lock:
        window = _calls.setdefault(key, deque())
        now = time.time()
        while window and now - window[0] >= 60.0:
            window.popleft()
        window.append(now)
    return max(0.0, wait)


def daily_exhausted(db: Database, provider: str, model: str, limit: Limit | None = None) -> bool:
    """True when today's allowance is gone — skip the model instead of failing.

    Twenty requests a day, which is what the bigger Gemini models give away, is
    enough to try a model and never enough to scan with one. Discovering that by
    spending the twenty is the expensive way round.
    """
    limit = limit or effective_limit(db, provider, model)
    if not limit or not limit.rpd:
        return False
    used = db.usage_count_today(provider=provider, model=model)
    return used >= limit.rpd


def reset_pacing() -> None:
    """Forget the call history — for tests, and between scans."""
    with _lock:
        _calls.clear()
