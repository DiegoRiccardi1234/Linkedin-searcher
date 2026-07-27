"""The same job, advertised under a different word.

Job boards index the words the employer typed. An Italian searching *tirocinio*
finds nothing on postings titled *stage* — which is what most Italian companies
actually write — and neither matches *internship*, which is what the same role
is called by an international company hiring in Milan. jobspy has no synonym
handling: the term goes to LinkedIn and Indeed verbatim, and the app never
emitted anything but what the user typed.

The synonym is a SEPARATE search, not extra words glued onto the query:
"tirocinio stage internship" is a worse query than either word alone, because
the boards rank on all of the terms together.

Deliberately small and hand-picked. This is not a translation layer: it covers
the cases where the same job carries genuinely different names in the same
market. Each entry costs a scrape (and its scoring) at scan time, so the
expansion is capped.
"""

from __future__ import annotations

import re

#: Groups of interchangeable job-search words. Matching is case-insensitive and
#: on whole words, so "stagista" does not fire on "stage manager"… and yes, that
#: ambiguity is exactly why the list stays hand-picked.
#
# Scope is deliberately narrow: only where the words genuinely do NOT co-occur
# in the same posting, so searching one really does miss the other. Job titles
# that merely have an English and an Italian form ("sviluppatore"/"developer")
# are NOT here — boards match the description too, so both wordings already
# surface, and expanding them would double the scrape and the scoring of the
# most common searches for nothing.
SYNONYM_GROUPS: tuple[tuple[str, ...], ...] = (
    # The case that started this: three words, one job, and an Italian employer
    # writing "stage" never writes "tirocinio".
    ("tirocinio", "stage", "internship", "tirocinante", "stagista"),
    ("neolaureato", "graduate program", "entry level"),
    ("apprendistato", "apprenticeship"),
    ("praticante", "trainee"),
)

#: How many extra searches a single term may generate. One doubles the cost of
#: that term's scrape and scoring; more is rarely worth the quota.
MAX_EXTRA_PER_TERM = 1


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip().lower())


def synonyms_for(term: str) -> list[str]:
    """Alternative wordings for ``term``, best-first, excluding the term itself.

    A term matches a group when the group's word appears in it as a whole word,
    so "tirocinio marketing" still finds "stage" (and keeps the qualifier).
    """
    normalized = _norm(term)
    if not normalized:
        return []
    out: list[str] = []
    for group in SYNONYM_GROUPS:
        hit = next((w for w in group if re.search(rf"\b{re.escape(w)}\b", normalized)), None)
        if not hit:
            continue
        for word in group:
            if word == hit:
                continue
            # Keep whatever else the user typed: "tirocinio marketing" becomes
            # "stage marketing", not a bare "stage".
            candidate = _norm(re.sub(rf"\b{re.escape(hit)}\b", word, normalized))
            if candidate and candidate != normalized and candidate not in out:
                out.append(candidate)
    return out


def expand_terms(terms: list[str], max_extra_per_term: int = MAX_EXTRA_PER_TERM) -> list[str]:
    """``terms`` plus their alternative wordings, de-duplicated, order preserved.

    The user's own words always come first: a synonym is an addition, never a
    replacement.
    """
    seen = {_norm(t) for t in terms if _norm(t)}
    expanded = [t for t in terms if _norm(t)]
    for term in list(terms):
        added = 0
        for candidate in synonyms_for(term):
            if added >= max_extra_per_term:
                break
            if candidate in seen:
                continue
            seen.add(candidate)
            expanded.append(candidate)
            added += 1
    return expanded
