"""What the app still needs to know before it can search for you.

Two questions are answered here, and they are different. "Can a scan run at
all?" has exactly two requirements — what to search for and where — because
without them the only thing left to search with is somebody else's profile.
"Will the results be any good?" has many more, and none of them blocks: an
unknown fact never hides an offer (see ``candidate_facts``), it just stops
being able to rule one out.

The per-fact provenance already existed and nothing read it: ``matching-facts``
has answered with ``sources`` (cv / manuale / mancante) and a ``missing`` list
since 1.8.0, and the frontend rendered three rows of it. This is that idea
finished — one place that knows what is missing, so the profile page, the
dashboard checklist and the pre-scan gate cannot disagree.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from app.db import Database
from app.services.candidate_facts import candidate_facts
from app.services.search_intent import search_intent

BLOCKING = "blocking"
WARNING = "warning"

#: Which sub-tab of the profile page each item belongs to, so a panel can show
#: what is missing from ITS part instead of one undifferentiated list.
GROUP_TARGET = "target"
GROUP_ABOUT = "about"
GROUP_CONSTRAINTS = "constraints"

#: Facts read by ``candidate_facts``, and the tab that owns them.
_FACT_GROUP = {
    "years_experience": GROUP_CONSTRAINTS,
    "education_level": GROUP_CONSTRAINTS,
    "grade": GROUP_CONSTRAINTS,
    "degree_fields": GROUP_CONSTRAINTS,
    "driving_licence": GROUP_CONSTRAINTS,
    "protected_category": GROUP_CONSTRAINTS,
    "work_rule": GROUP_CONSTRAINTS,
}


@dataclass(frozen=True)
class ReadinessItem:
    """One thing the app knows, or does not."""

    id: str
    group: str
    severity: str
    #: ``ok`` | ``missing``. Deliberately not a boolean: "unknown" and "false"
    #: are different answers and this project has paid for confusing them.
    status: str
    #: Where the value came from when there is one: cv | manuale | dedotto.
    source: str = ""
    #: A short, already-readable value for the UI. Never a whole CV.
    value: str = ""


def _item(
    id_: str, group: str, severity: str, ok: bool, source: str = "", value: str = ""
) -> ReadinessItem:
    return ReadinessItem(
        id=id_,
        group=group,
        severity=severity,
        status="ok" if ok else "missing",
        source=source if ok else "",
        value=value if ok else "",
    )


def _has_watchlist(db: Database) -> bool:
    """Following employers is a way of saying what to look for."""
    if db.get_preference("watchlist_enabled", "0") not in ("1", "true", "on"):
        return False
    return bool(db.list_watchlist_companies(active_only=True))


def profile_readiness(db: Database) -> dict[str, Any]:
    intent = search_intent(db)
    facts = candidate_facts(db)
    profile = db.get_active_candidate_profile()
    watchlist = _has_watchlist(db)

    items: list[ReadinessItem] = [
        # The two that block. Everything else is advice.
        _item(
            "search_terms",
            GROUP_TARGET,
            BLOCKING,
            bool(intent.terms) or watchlist,
            source=intent.terms_origin if intent.terms else "watchlist",
            value=", ".join(intent.terms[:4]),
        ),
        _item(
            "location",
            GROUP_TARGET,
            BLOCKING,
            bool(intent.locations),
            source=intent.locations_origin,
            value=", ".join(intent.locations[:3]),
        ),
        _item(
            "cv",
            GROUP_ABOUT,
            WARNING,
            profile is not None,
            source="cv",
            value=str(profile.get("source_name") or "") if profile else "",
        ),
        _item(
            "goal",
            GROUP_TARGET,
            WARNING,
            bool((db.get_preference("onboarding_goal", "") or "").strip()),
            source="manuale",
            value=(db.get_preference("onboarding_goal", "") or "")[:80],
        ),
        _item(
            "ral_min",
            GROUP_TARGET,
            WARNING,
            bool((db.get_preference("onboarding_ral_min", "") or "").strip()),
            source="manuale",
            value=(db.get_preference("onboarding_ral_min", "") or ""),
        ),
    ]

    # The applicability facts, with the provenance candidate_facts already
    # tracks. A fact nobody stated is a warning: it cannot block an offer, so
    # it cannot block a scan either.
    readable = {
        "years_experience": "" if facts.years_experience is None else str(facts.years_experience),
        "education_level": facts.education_level or "",
        "grade": "" if facts.grade is None else str(facts.grade),
        "degree_fields": ", ".join(sorted(facts.degree_fields)),
        "driving_licence": "" if facts.driving_licence is None else str(facts.driving_licence),
        "protected_category": (
            "" if facts.protected_category is None else str(facts.protected_category)
        ),
        "work_rule": ", ".join(facts.work_rule.cities),
    }
    for name, group in _FACT_GROUP.items():
        origin = facts.sources.get(name, "mancante")
        items.append(
            _item(name, group, WARNING, origin != "mancante", source=origin, value=readable[name])
        )

    blocking = [i.id for i in items if i.severity == BLOCKING and i.status == "missing"]
    warnings = [i.id for i in items if i.severity == WARNING and i.status == "missing"]
    return {
        "ready": not blocking,
        "blocking": blocking,
        "warnings": warnings,
        "items": [asdict(i) for i in items],
        # The same chain the scan will use, so the search form can be filled in
        # with what the app would have used anyway — visibly, and editable.
        "suggested_terms": intent.terms,
        "terms_origin": intent.terms_origin,
        "suggested_locations": intent.locations,
        "locations_origin": intent.locations_origin,
    }
