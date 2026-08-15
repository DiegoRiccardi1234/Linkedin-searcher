"""What to search for, and where — resolved from what the user has.

Until 2.0.0 an empty search form did not mean "I have not said yet": it meant
"use the six terms and the city that the person who wrote this app was looking
for". The consequences went further than one odd scan, because the resolved
values are then stored as ``last_scan_*``, which the scheduler replays and
which the work-rule inference reads as evidence of where the user lives. One
scan with an empty form and a stranger's install believed they were an AI QA
engineer in Turin.

So the fallbacks now come from the user's own data, in order of how recently
they said it, and the chain ends in nothing at all. Nothing is a valid answer:
the caller is expected to ask rather than to guess.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from app.db import Database
from app.services import roles_shortlist
from app.services.candidate_facts import candidate_facts

#: Where a resolved value came from. ``none`` means the chain ran out, which is
#: the case the app has to handle by asking a question.
TermsOrigin = str
LocationOrigin = str


class ScanRefused(Exception):
    """Asked to search with nothing to search for, or nowhere to search.

    Deliberately an error and not a default. The alternative — picking
    something plausible — is what taught other people's installs that they were
    AI engineers in Turin, and it did it silently.
    """

    def __init__(self, missing: Sequence[str]):
        self.missing = [str(m) for m in missing]
        super().__init__("missing_essentials: " + ", ".join(self.missing))


@dataclass(frozen=True)
class SearchIntent:
    terms: list[str]
    terms_origin: TermsOrigin
    locations: list[str]
    locations_origin: LocationOrigin

    @property
    def ready(self) -> bool:
        """Enough to run a scan that means something for THIS user."""
        return bool(self.terms) and bool(self.locations)


def _clean(values: Sequence[Any] | None) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values or []:
        text = str(value).strip()
        key = text.lower()
        if text and key not in seen:
            seen.add(key)
            out.append(text)
    return out


def _json_list(db: Database, key: str) -> list[str]:
    raw = db.get_preference(key, "") or ""
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    return _clean(data) if isinstance(data, list) else []


def resolve_search_terms(
    db: Database, explicit: Sequence[str] | None = None
) -> tuple[list[str], TermsOrigin]:
    """The terms this scan should use, and where they came from.

    Order: what was typed → the last scan the user actually ran → the roles
    they shortlisted → the roles read off their CV → nothing.
    """
    typed = _clean(explicit)
    if typed:
        return typed, "explicit"
    last = _json_list(db, "last_scan_terms")
    if last:
        return last, "last_scan"
    shortlist = _clean(roles_shortlist.load(db))
    if shortlist:
        return shortlist, "shortlist"
    # ``preferred_roles`` is what the CV extractor writes; it is a comma-joined
    # string, not JSON, and it is the last thing that is still about this user.
    from_cv = _clean((db.get_preference("preferred_roles", "") or "").split(","))
    if from_cv:
        return from_cv, "cv"
    return [], "none"


def resolve_locations(
    db: Database, explicit: Sequence[str] | None = None, *, is_remote: bool = False
) -> tuple[list[str], LocationOrigin]:
    """Where to search, and where that came from.

    A full-remote search answers the question by itself — there is no city to
    ask for — so it resolves to the user's country if they have one and to
    nothing otherwise, which the caller turns into a question.
    """
    typed = _clean(explicit)
    if typed:
        return typed, "explicit"
    last = _json_list(db, "last_scan_locations")
    if last:
        return last, "last_scan"
    cities = _clean(candidate_facts(db).work_rule.cities)
    if cities:
        # Stored normalised (lowercase, no accents) for comparison; a job board
        # is shown the readable form.
        return [city.title() for city in cities], "profile"
    if is_remote:
        return [], "remote"
    return [], "none"


def search_intent(
    db: Database,
    *,
    terms: Sequence[str] | None = None,
    locations: Sequence[str] | None = None,
    is_remote: bool = False,
) -> SearchIntent:
    resolved_terms, terms_origin = resolve_search_terms(db, terms)
    resolved_locations, locations_origin = resolve_locations(db, locations, is_remote=is_remote)
    return SearchIntent(
        terms=resolved_terms,
        terms_origin=terms_origin,
        locations=resolved_locations,
        locations_origin=locations_origin,
    )
