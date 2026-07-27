"""Talking to jobspy: search terms, job types, country resolution, freshness.

Everything here is about the scraper's quirks rather than about the
candidate: Indeed applies only ONE server-side filter, LinkedIn ignores the
country and reads the location string, and a location no single Indeed domain
can serve has to skip Indeed without losing LinkedIn.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from app.log import get_logger

log = get_logger(__name__)

_EXPERIENCE_KEYWORDS: dict[str, str] = {
    "internship": "internship",
    "entry": "entry level",
    "junior": "junior",
    "mid": "",
    "senior": "senior",
    "director": "director",
    "executive": "executive",
}

# jobspy supports a single ``job_type`` kwarg (string). Map UI codes onto it.
_JOBSPY_JOB_TYPE: dict[str, str] = {
    "fulltime": "fulltime",
    "parttime": "parttime",
    "contract": "contract",
    "temporary": "temporary",
    "internship": "internship",
}


def _resolve_jobspy_job_type(job_types: list[str]) -> str | None:
    """Pick the single ``job_type`` to pass to jobspy.

    jobspy accepts only one type. A single selection narrows the scrape; with
    multiple selections we must NOT silently drop to the first (that would hide
    the other chosen types) — return ``None`` so jobspy returns all types, a
    superset of what the user picked.
    """
    mapped = [_JOBSPY_JOB_TYPE[j.lower()] for j in job_types if j.lower() in _JOBSPY_JOB_TYPE]
    return mapped[0] if len(mapped) == 1 else None


def _augment_search_term(term: str, exp_levels: list[str], work_types: list[str]) -> str:
    bits = [term]
    for lvl in exp_levels:
        kw = _EXPERIENCE_KEYWORDS.get(lvl, "")
        if kw and kw not in term.lower():
            bits.append(kw)
    if "hybrid" in work_types and "hybrid" not in term.lower():
        bits.append("hybrid")
    return " ".join(bits).strip()


try:
    from jobspy.model import Country
except ImportError:  # pragma: no cover
    Country = None


def _filter_indeed_freshness(df: Any, hours_old: int) -> Any:
    """Local freshness filter for Indeed rows (LinkedIn keeps the server-side
    one). Rows with an unknown ``date_posted`` are kept — never over-drop."""
    if df is None or len(df) == 0 or "date_posted" not in df.columns or "site" not in df.columns:
        return df
    cutoff = datetime.now(UTC).date() - timedelta(hours=hours_old)

    def _keep(row: Any) -> bool:
        if str(row.get("site")) != "indeed":
            return True
        d = row.get("date_posted")
        if d is None or d != d:  # None / NaN / NaT (self-inequality)
            return True
        if isinstance(d, datetime):
            d = d.date()
        try:
            return bool(d >= cutoff)
        except TypeError:
            return True

    return df[df.apply(_keep, axis=1)]


# Location placeholders that span countries: Indeed has no cross-country search
# (one domain per country), so it is skipped for these and only LinkedIn runs.
_MULTI_COUNTRY_LOCATIONS = {
    "remote",
    "worldwide",
    "anywhere",
    "europe",
    "european union",
    "eu",
    "emea",
}


def _indeed_country_for(location: str, default_country: str) -> str | None:
    """Indeed country to use for ``location``, or None when Indeed can't serve it.

    Indeed is queried per-country domain, but a scan takes ONE country and many
    locations: with country=italy and location="Germany" Indeed searches the
    Italian domain for a German city and returns nothing (measured: an EU-wide
    run produced 0 Indeed rows out of 44 jobs, all of them LinkedIn). When the
    location names a country jobspy knows, that country wins; when it's a region
    or a placeholder ("European Union", "Remote") Indeed is skipped for that
    location — LinkedIn handles free-text locations and still runs.
    """
    text = (location or "").strip()
    if not text:
        return default_country
    if text.lower() in _MULTI_COUNTRY_LOCATIONS:
        return None
    if Country is None:  # pragma: no cover - jobspy always ships it
        return default_country
    # jobspy locations are "City, Region, Country" — the tail is the country.
    candidates = [text, *[part.strip() for part in reversed(text.split(",")) if part.strip()]]
    for candidate in candidates:
        try:
            Country.from_string(candidate)
        except Exception:
            continue
        return candidate.lower()
    # A bare city ("Torino") carries no country: the scan-level one still applies.
    if "," not in text:
        return default_country
    return None
