"""Which free provider to use — answered with this install's own evidence.

The app has always been able to say which MODEL is best on a provider you already
configured. Which provider to open in the first place was left to a hand-written
table in the Info tab, so a new user faced thirteen identical cards and no reason
to pick any of them, and an existing user kept a provider that had been failing
for a week because nothing said so.

Two sources, in this order:

1. **What this install has watched.** ``usage_log`` holds every scoring call with
   its outcome and duration. A provider that answers here, on these prompts, with
   this key, beats any published number.
2. **What ships in ``app/data/provider_limits.json``.** Free or not, card or not,
   how much a day, where it runs, whether the free tier trains on prompts, and
   when it closes.

The second source is deliberately timid about training: ``trains`` is ``"yes"``
or ``"no"`` only where a primary source says so, and ``"unknown"`` otherwise. An
app that told someone their CV was safe because a list on GitHub implied it would
be doing them harm — so "unknown" is surfaced as "not verified, here are their
terms", never as reassurance.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from app.log import get_logger
from app.services import rate_limits
from app.services.model_scoreboard import scoreboard

log = get_logger(__name__)

#: Calls needed before this install's own record outweighs the shipped facts.
#: Below it a provider has been tried, not measured.
MIN_CALLS_FOR_EVIDENCE = 8

#: Days of ``usage_log`` the advice looks at.
EVIDENCE_DAYS = 14

#: A free tier closing within this many days is worth saying out loud.
SUNSET_WARNING_DAYS = 30


def _facts() -> dict[str, dict[str, Any]]:
    """The shipped provider facts, minus the ``_note`` prose."""
    block = rate_limits._shipped().get("_providers") or {}
    if not isinstance(block, dict):
        return {}
    return {
        name: value
        for name, value in block.items()
        if not name.startswith("_") and isinstance(value, dict)
    }


def _sunset(fact: dict[str, Any]) -> date | None:
    raw = str(fact.get("sunset") or "")
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def _daily_allowance(provider: str) -> int:
    """The most generous daily cap this provider ships with, 0 when unknown.

    Used only to break ties between free tiers: fifty offers a day and a thousand
    are a different proposition, and that difference is the whole answer to
    "which one should I use".
    """
    table = rate_limits._shipped().get(provider) or {}
    if not isinstance(table, dict):
        return 0
    best = 0
    for pattern, entry in table.items():
        if pattern.startswith("_") or not isinstance(entry, dict):
            continue
        best = max(best, int(entry.get("rpd") or 0))
    return best


def _evidence(db: Any) -> dict[str, dict[str, float]]:
    """Per-provider scoring record from this install's own log."""
    out: dict[str, dict[str, float]] = {}
    for record in scoreboard(db, days=EVIDENCE_DAYS):
        bucket = out.setdefault(record.provider, {"calls": 0, "successes": 0, "ms": 0.0})
        bucket["calls"] += record.calls
        bucket["successes"] += record.successes
        # Median of medians, weighted by calls: exact enough to rank providers.
        bucket["ms"] += float(record.median_ms or 0) * record.calls
    for bucket in out.values():
        bucket["success_rate"] = bucket["successes"] / bucket["calls"] if bucket["calls"] else 0.0
        bucket["median_ms"] = bucket["ms"] / bucket["calls"] if bucket["calls"] else 0.0
    return out


def _score(
    provider: str,
    fact: dict[str, Any],
    configured: bool,
    evidence: dict[str, float] | None,
) -> tuple[float, list[str]]:
    """A number and the reasons behind it, in the order they were applied."""
    score = 0.0
    why: list[str] = []

    if fact.get("free"):
        score += 40
        why.append("free")
    if fact.get("card") is False and fact.get("free"):
        score += 10
        why.append("no_card")
    if configured:
        score += 15
        why.append("configured")

    allowance = _daily_allowance(provider)
    if allowance:
        # 1000/day is worth about 20 points, 50/day about 6: generous, not linear,
        # because past a few hundred a day the cap stops being the binding limit.
        score += min(20.0, allowance**0.5)
        why.append(f"allowance:{allowance}")

    if fact.get("region") == "local":
        if configured:
            score += 25
            why.append("stays_on_this_machine")
        else:
            # Nothing beats a model on your own machine for privacy, but telling
            # someone to go install one is not an answer to "which key do I get".
            score -= 30
            why.append("needs_a_model_installed_first")
    elif fact.get("region") == "eu":
        score += 5
        why.append("eu_hosted")

    trains = str(fact.get("trains") or "unknown")
    if trains == "yes":
        score -= 20
        why.append("trains_on_prompts")
    elif trains == "depends":
        score -= 5
        why.append("training_depends_on_model")

    if evidence and evidence["calls"] >= MIN_CALLS_FOR_EVIDENCE:
        # Measured beats published, in BOTH directions. An earlier version only
        # added points for success, so a free provider that had failed every call
        # for a fortnight still outranked a paid one that worked — it kept its
        # whole free-tier head start and simply gained nothing. Centred on half:
        # all-success +40, half +0, all-failure -40.
        score += 80 * evidence["success_rate"] - 40
        why.append(f"measured_success:{evidence['success_rate']:.0%}")
        if evidence["median_ms"] and evidence["success_rate"] >= 0.5:
            # Faster is better, capped: 1s ≈ +10, 10s ≈ +1. Only for a provider
            # that answers — being quick to fail is not a quality.
            score += min(10.0, 10_000.0 / max(evidence["median_ms"], 1.0))
            why.append(f"measured_median_ms:{int(evidence['median_ms'])}")
    return score, why


def advise(
    db: Any,
    *,
    keys_status: dict[str, Any] | None = None,
    available: dict[str, bool] | None = None,
    today: date | None = None,
) -> dict[str, Any]:
    """The provider to use, the runners-up, and what the user should know.

    ``keys_status`` is ``container.keys_status()`` (``<name>_configured`` flags);
    ``available`` is optional and only used to say a configured key is currently
    rejected. Never raises: advice is a hint, not a dependency.
    """
    now = today or datetime.now(UTC).date()
    keys = keys_status or {}
    facts = _facts()
    try:
        evidence = _evidence(db)
    except Exception as exc:  # a hint must not take a page down
        log.debug("provider advice: no usage evidence (%s)", exc)
        evidence = {}

    warnings: list[dict[str, Any]] = []
    ranked: list[dict[str, Any]] = []

    for provider, fact in facts.items():
        configured = bool(keys.get(f"{provider}_configured"))
        sunset = _sunset(fact)
        if sunset and sunset <= now:
            # Not a candidate and not a silent omission: a provider that closed
            # is exactly what a user would otherwise keep trying to use.
            warnings.append({"provider": provider, "code": "free_tier_closed", "on": str(sunset)})
            continue
        if not fact.get("free") and not configured:
            continue  # nothing to recommend about a paid provider with no key
        score, why = _score(provider, fact, configured, evidence.get(provider))
        if sunset:
            days_left = (sunset - now).days
            if days_left <= SUNSET_WARNING_DAYS:
                score -= 30
                why.append(f"closes_in_days:{days_left}")
                warnings.append(
                    {"provider": provider, "code": "free_tier_closing", "on": str(sunset)}
                )
        ranked.append(
            {
                "provider": provider,
                "score": round(score, 1),
                "why": why,
                "configured": configured,
                "free": bool(fact.get("free")),
                "region": fact.get("region") or "global",
                "trains": str(fact.get("trains") or "unknown"),
                "signup": fact.get("signup") or "",
                "terms": fact.get("terms") or "",
                "note": fact.get("note") or "",
                "measured_calls": int((evidence.get(provider) or {}).get("calls", 0)),
            }
        )

    ranked.sort(key=lambda row: (-row["score"], row["provider"]))
    best = ranked[0] if ranked else None

    if best:
        if best["trains"] == "yes":
            warnings.append({"provider": best["provider"], "code": "trains_on_prompts"})
        elif best["trains"] == "unknown" and best["region"] != "local":
            # Said plainly rather than dressed up as safety: the app does not know.
            warnings.append({"provider": best["provider"], "code": "training_unverified"})
    if not any(row["configured"] for row in ranked):
        warnings.append({"code": "no_key_configured"})
    if available is not None:
        for row in ranked:
            if row["configured"] and available.get(row["provider"]) is False:
                warnings.append({"provider": row["provider"], "code": "key_rejected"})

    return {
        "recommended": best,
        "alternatives": ranked[1:4],
        "warnings": warnings,
        "evidence_days": EVIDENCE_DAYS,
    }
