"""What each model ACTUALLY did, read back from ``usage_log``.

Model selection had three signals and none of them was memory:

- the name heuristic (:mod:`app.providers.model_selector`) guesses quality from
  the id — it cannot know that a given model truncates JSON on this host;
- OpenRouter's live health endpoint says whether a model is up, not whether it
  answers usefully;
- the empirical penalty map in :mod:`app.providers.factory` is exactly right,
  but lives in memory with a short TTL: everything learned during a scan is
  forgotten at the next restart, and the same broken model gets re-elected.

Meanwhile every call — success or failure, with its ``error_type`` and duration
— has been written to ``usage_log`` since v1.1.0 and never read back. This
module turns that table into a per-``(provider, model)`` record: how often it
returned valid JSON, how often it truncated or got rate-limited, how fast it
answered. Costs nothing (no inference, no network) and survives restarts.

Used for the scoring context, where "JSON that parses" is the whole job.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from app.log import get_logger

log = get_logger(__name__)

#: Only calls that asked for JSON say anything about scoring fitness — a model
#: can chat happily and still never close a brace.
SCORING_ENDPOINT = "complete_json"

#: Below this many recorded calls a model's record is noise, not evidence.
MIN_SAMPLES = 4

#: A model failing more than this share of its JSON calls is de-ranked.
MAX_FAILURE_RATE = 0.5

#: Truncation is judged harder than a generic failure: it is a property of the
#: model (it thinks in the completion budget), not of the moment, so it will
#: happen again on the next offer. Rate limits are deliberately NOT part of this
#: — a 429 is the host throttling, and the cooldown machinery already handles it.
MAX_TRUNCATION_RATE = 0.25

_TRUNCATION_ERRORS = ("truncatedcompletionerror",)
_TRANSIENT_ERRORS = ("ratelimiterror", "timeouterror")


@dataclass(frozen=True)
class ModelRecord:
    provider: str
    model: str
    calls: int
    successes: int
    truncated: int
    rate_limited: int
    median_ms: int

    @property
    def success_rate(self) -> float:
        return self.successes / self.calls if self.calls else 0.0

    @property
    def truncation_rate(self) -> float:
        return self.truncated / self.calls if self.calls else 0.0

    @property
    def is_unfit(self) -> bool:
        """True when the record is bad enough to de-rank this model.

        Rate-limited calls are excluded from the denominator: they say the host
        was busy, not that the model is bad, and counting them would blacklist
        every good free model on a busy day.
        """
        judged = self.calls - self.rate_limited
        if judged < MIN_SAMPLES:
            return False
        failures = judged - self.successes
        if self.truncated / judged > MAX_TRUNCATION_RATE:
            return True
        return failures / judged > MAX_FAILURE_RATE

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "model": self.model,
            "calls": self.calls,
            "successes": self.successes,
            "truncated": self.truncated,
            "rate_limited": self.rate_limited,
            "median_ms": self.median_ms,
            "success_rate": round(self.success_rate, 3),
            "truncation_rate": round(self.truncation_rate, 3),
            "unfit": self.is_unfit,
        }


def _floor(days: int) -> str:
    return (datetime.now(UTC) - timedelta(days=max(1, days))).isoformat(timespec="seconds")


def scoreboard(
    db: Any, *, endpoint: str | None = SCORING_ENDPOINT, days: int = 14
) -> list[ModelRecord]:
    """Per-model record over the last ``days``, best-first (JSON rate, then speed).

    Never raises: a missing table or a closed DB yields an empty list, and the
    caller behaves exactly as it did before this module existed.
    """
    try:
        conn = db._get_connection() if hasattr(db, "_get_connection") else db.conn
        params: list[Any] = [_floor(days)]
        where = "ts >= ?"
        if endpoint:
            where += " AND endpoint = ?"
            params.append(endpoint)
        # ``where`` is assembled from the literals above; every value is bound.
        rows = conn.execute(
            f"SELECT provider, model, success, error_type, duration_ms FROM usage_log WHERE {where}",
            params,
        ).fetchall()
    except Exception as exc:
        log.debug("scoreboard unavailable: %s", exc)
        return []

    buckets: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        provider = str(row["provider"] or "")
        model = str(row["model"] or "")
        if not provider or not model:
            continue
        bucket = buckets.setdefault(
            (provider, model),
            {"calls": 0, "successes": 0, "truncated": 0, "rate_limited": 0, "durations": []},
        )
        bucket["calls"] += 1
        if row["success"]:
            bucket["successes"] += 1
        error = str(row["error_type"] or "").lower()
        if any(marker in error for marker in _TRUNCATION_ERRORS):
            bucket["truncated"] += 1
        elif any(marker in error for marker in _TRANSIENT_ERRORS):
            bucket["rate_limited"] += 1
        if row["duration_ms"]:
            bucket["durations"].append(int(row["duration_ms"]))

    records = [
        ModelRecord(
            provider=provider,
            model=model,
            calls=data["calls"],
            successes=data["successes"],
            truncated=data["truncated"],
            rate_limited=data["rate_limited"],
            median_ms=_median(data["durations"]),
        )
        for (provider, model), data in buckets.items()
    ]
    records.sort(key=lambda r: (-r.success_rate, r.median_ms or 10**9))
    return records


def _median(values: list[int]) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) // 2


def unfit_ids(db: Any, provider_name: str, *, days: int = 14) -> set[str]:
    """Models this provider has empirically failed to score with.

    Folded into the factory's ``penalized`` set, so they are de-ranked and not
    excluded: if every model has a bad record the ranking still returns one.
    """
    return {
        record.model
        for record in scoreboard(db, days=days)
        if record.provider == provider_name and record.is_unfit
    }
