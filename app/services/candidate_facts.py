"""The few facts about the candidate that decide whether an offer is applicable.

Years of experience, degree level, degree grade and where they can actually
work. All four already existed somewhere — ``summary_json`` carries the first
two, the CV text carries the grade, ``onboarding_work_mode`` carries the last —
and none of them was read by the scoring path. An offer demanding two years, or
a master's, or presence in another city could score 9.

Three rules hold everywhere in this module:

* **Nothing is hardcoded for one person.** Every fact comes from the CV the user
  uploaded or from a field they filled in.
* **A manual correction always wins.** CV parsers misread dates and degrees, so
  the user must be able to overrule them, and the correction must survive
  re-uploading the CV.
* **An unknown fact blocks nothing.** If we cannot tell how many years someone
  has, the experience check stays silent rather than hiding real jobs.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from app.services.scan.heuristics import EDUCATION_LEVELS as EDUCATION_LEVELS
from app.services.scan.heuristics import education_requirement

if TYPE_CHECKING:
    from app.db import Database

#: Preference keys holding the user's manual corrections. The ``profile_fact_``
#: prefix is what makes them writable through ``POST /api/preferences``.
FACT_PREFIX = "profile_fact_"
FACT_YEARS = f"{FACT_PREFIX}years_experience"
FACT_EDUCATION = f"{FACT_PREFIX}education_level"
FACT_GRADE = f"{FACT_PREFIX}grade"
FACT_BASE_CITIES = f"{FACT_PREFIX}base_cities"
FACT_WORK_MODES = f"{FACT_PREFIX}work_modes"

_GRADE_RE = re.compile(r"(\d{2,3})\s*/\s*110")

#: jobspy writes locations in English ("Turin, Piedmont, Italy") while users type
#: them in Italian. Without this, "Torino" never matched a single posting.
_CITY_ALIASES: dict[str, tuple[str, ...]] = {
    "torino": ("turin",),
    "milano": ("milan",),
    "roma": ("rome",),
    "napoli": ("naples",),
    "firenze": ("florence",),
    "venezia": ("venice",),
    "genova": ("genoa",),
    "padova": ("padua",),
    "bologna": (),
    "bari": (),
    "cagliari": (),
    "palermo": (),
    "catania": (),
    "verona": (),
    "trieste": (),
}

# Not city names: work-mode words, and — the one that actually bit — COUNTRIES
# and regions. A scan run over "Italy" put "italy" in the accepted-cities list,
# and since every Italian posting's location ends in ", Italy" the city check
# matched all of them: on-site roles in Rome and Savona sailed through.
_NOT_A_CITY = {
    "remoto",
    "remote",
    "ibrido",
    "ibrida",
    "sede",
    "presenza",
    "smart",
    "working",
    "full",
    "oppure",
    "solo",
    "anche",
    "lavoro",
    "modalita",
    "modalità",
    "casa",
    # countries / supranational areas, in both the languages the app sees
    "italia",
    "italy",
    "europa",
    "europe",
    "european union",
    "unione europea",
    "emea",
    "worldwide",
    "anywhere",
    "eu",
}


def _norm(text: str) -> str:
    """Lowercase, accent-free, for comparing place names across languages."""
    out = unicodedata.normalize("NFD", str(text or ""))
    out = "".join(ch for ch in out if not unicodedata.combining(ch))
    return out.lower().strip()


@dataclass(frozen=True)
class WorkRule:
    """Where the user accepts to work, and in which mode."""

    #: Cities where commuting is acceptable (on-site or hybrid).
    cities: tuple[str, ...] = ()
    allow_onsite: bool = True
    allow_hybrid: bool = True
    allow_remote: bool = True

    @property
    def constrains_location(self) -> bool:
        """False when we know too little to reject anything."""
        return bool(self.cities) and not (
            self.allow_onsite and self.allow_hybrid and not self.cities
        )


@dataclass(frozen=True)
class CandidateFacts:
    """What we know, and where each piece came from."""

    years_experience: int | None = None
    education_level: str | None = None
    grade: int | None = None
    work_rule: WorkRule = field(default_factory=WorkRule)
    #: fact name -> "cv" | "manuale" | "mancante", for the profile panel.
    sources: dict[str, str] = field(default_factory=dict)

    def missing(self) -> list[str]:
        return [name for name, origin in self.sources.items() if origin == "mancante"]


def _as_int(raw: Any) -> int | None:
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return None
    return value if value >= 0 else None


def candidate_education_level(profile_markdown: str, summary: dict[str, Any] | None) -> str | None:
    """Highest degree the CV shows, as one of :data:`EDUCATION_LEVELS`."""
    if isinstance(summary, dict):
        stored = str(summary.get("education_level") or "").strip()
        if stored in EDUCATION_LEVELS:
            return stored
    texts = [str(profile_markdown or "")]
    if isinstance(summary, dict) and summary.get("education"):
        texts.append(str(summary["education"]))
    best: str | None = None
    for text in texts:
        level, _preferred = education_requirement(text)
        if level in EDUCATION_LEVELS and (
            best is None or EDUCATION_LEVELS.index(level) > EDUCATION_LEVELS.index(best)
        ):
            best = level
    return best


def parse_work_rule(work_mode_text: str, scan_locations: list[str] | None = None) -> WorkRule:
    """Read "Remoto, Torino in sede oppure ibrido su Torino" into a rule.

    The declared modes come from the sentence; the cities come from the places
    the user actually searches in, plus any proper noun in the sentence itself.
    Parsing is a starting point the user can overrule, never the last word.
    """
    text = _norm(work_mode_text)
    allow_remote = bool(re.search(r"remot|smart working|da casa", text))
    allow_hybrid = bool(re.search(r"ibrid|hybrid", text))
    allow_onsite = bool(re.search(r"in sede|presenza|on[- ]?site|ufficio", text))
    # A sentence naming none of them constrains nothing.
    if not (allow_remote or allow_hybrid or allow_onsite):
        allow_remote = allow_hybrid = allow_onsite = True

    cities: list[str] = []
    for loc in scan_locations or []:
        # "Torino, Italy" -> "torino"
        head = _norm(str(loc).split(",")[0])
        if head and head not in _NOT_A_CITY and head not in cities:
            cities.append(head)
    for token in re.findall(r"[a-zA-ZÀ-ÿ]{4,}", str(work_mode_text or "")):
        low = _norm(token)
        if low in _CITY_ALIASES and low not in cities:
            cities.append(low)

    return WorkRule(
        cities=tuple(cities),
        allow_onsite=allow_onsite,
        allow_hybrid=allow_hybrid,
        allow_remote=allow_remote,
    )


def city_matches(sede: str, cities: tuple[str, ...]) -> bool:
    """True when a posting's location names one of the accepted cities."""
    if not cities:
        return True
    place = _norm(sede)
    if not place:
        return True  # unknown location decides nothing
    for city in cities:
        names = (city, *_CITY_ALIASES.get(city, ()))
        if any(re.search(rf"\b{re.escape(name)}\b", place) for name in names):
            return True
    return False


def candidate_facts(db: Database) -> CandidateFacts:
    """Assemble the facts, manual corrections first, CV second."""
    profile = db.get_active_candidate_profile() or {}
    markdown = str(profile.get("markdown") or "")
    summary = profile.get("summary_json")
    if isinstance(summary, str):
        try:
            summary = json.loads(summary)
        except (TypeError, ValueError):
            summary = None
    if not isinstance(summary, dict):
        summary = {}

    sources: dict[str, str] = {}
    years: int | None
    education: str | None
    grade: int | None

    manual_years = _as_int(db.get_preference(FACT_YEARS, ""))
    if manual_years is not None:
        years, sources["years_experience"] = manual_years, "manuale"
    else:
        years = _as_int(summary.get("years_experience"))
        sources["years_experience"] = "cv" if years is not None else "mancante"

    manual_edu = (db.get_preference(FACT_EDUCATION, "") or "").strip()
    if manual_edu in EDUCATION_LEVELS:
        education, sources["education_level"] = manual_edu, "manuale"
    else:
        education = candidate_education_level(markdown, summary)
        sources["education_level"] = "cv" if education else "mancante"

    manual_grade = _as_int(db.get_preference(FACT_GRADE, ""))
    if manual_grade is not None:
        grade, sources["grade"] = manual_grade, "manuale"
    else:
        found = [int(g) for g in _GRADE_RE.findall(markdown) if 60 <= int(g) <= 110]
        grade = found[0] if found else None
        sources["grade"] = "cv" if grade is not None else "mancante"

    rule = _work_rule_for(db, sources)
    return CandidateFacts(
        years_experience=years,
        education_level=education,
        grade=grade,
        work_rule=rule,
        sources=sources,
    )


def _work_rule_for(db: Database, sources: dict[str, str]) -> WorkRule:
    manual_cities = [c.strip() for c in (db.get_preference(FACT_BASE_CITIES, "") or "").split(",")]
    manual_cities = [_norm(c) for c in manual_cities if c.strip()]
    manual_modes = {
        m.strip().lower()
        for m in (db.get_preference(FACT_WORK_MODES, "") or "").split(",")
        if m.strip()
    }

    try:
        scan_locations = json.loads(db.get_preference("last_scan_locations", "") or "[]")
    except (TypeError, ValueError):
        scan_locations = []
    parsed = parse_work_rule(db.get_preference("onboarding_work_mode", "") or "", scan_locations)

    if manual_cities or manual_modes:
        sources["work_rule"] = "manuale"
        return WorkRule(
            cities=tuple(manual_cities) or parsed.cities,
            allow_onsite="onsite" in manual_modes if manual_modes else parsed.allow_onsite,
            allow_hybrid="hybrid" in manual_modes if manual_modes else parsed.allow_hybrid,
            allow_remote="remote" in manual_modes if manual_modes else parsed.allow_remote,
        )
    sources["work_rule"] = "cv" if parsed.cities else "mancante"
    return parsed


# ── the three checks ────────────────────────────────────────────────────────
# Same shape as ``_geo_status``/``_grade_status`` in hard_requirements: a label
# for display and a blocking reason or None. They live here, not there, because
# hard_requirements is imported BY heuristics, which this module imports — the
# checks need the facts, so putting them here is what keeps the imports acyclic.


#: Years below which a stated requirement is treated as a wish, not a gate.
#: Italian postings routinely ask for "1 anno" and hire graduates anyway; asking
#: for two or more is where the door actually closes.
BLOCKING_EXPERIENCE_YEARS = 2


def experience_status(descrizione: str, facts: CandidateFacts) -> tuple[str, str | None]:
    """``(years the posting asks for, blocking reason or None)``."""
    from app.services.scan.heuristics import EXPERIENCE_BAND_YEARS, _estimate_experience_band

    band = _estimate_experience_band(str(descrizione or "").lower())
    required = EXPERIENCE_BAND_YEARS.get(band)
    if required is None:  # "Non specificato": asks nothing we can measure
        return band, None
    have = facts.years_experience
    if have is None or have >= required or required < BLOCKING_EXPERIENCE_YEARS:
        return band, None
    return band, f"Richiede {band} anni di esperienza (il profilo ne dichiara {have})"


def education_status(descrizione: str, facts: CandidateFacts) -> tuple[str, str | None]:
    """``(degree the posting asks for, blocking reason or None)``."""
    level, preferred_only = education_requirement(str(descrizione or ""))
    if level not in EDUCATION_LEVELS or preferred_only:
        # "laurea magistrale gradita" is a wish: it must not close the door.
        return level, None
    have = facts.education_level
    if have is None or have not in EDUCATION_LEVELS:
        return level, None
    if EDUCATION_LEVELS.index(have) >= EDUCATION_LEVELS.index(level):
        return level, None
    return level, f"Richiede una laurea {level.lower()} (il profilo ha: {have.lower()})"


def location_status(sede: str, modalita: str, facts: CandidateFacts) -> tuple[str, str | None]:
    """``(label, blocking reason or None)`` for where the job is worked from.

    Full remote is judged on the mode alone — the office address is irrelevant
    when nobody goes there. On-site and hybrid are judged on the city.
    """
    rule = facts.work_rule
    mode = str(modalita or "").strip()
    if mode == "Full Remote":
        if rule.allow_remote:
            return "Full remote: ok", None
        return "Full remote non accettato", "L'utente non ha dichiarato di accettare il full remote"
    if mode in ("Ibrido", "In sede"):
        allowed = rule.allow_hybrid if mode == "Ibrido" else rule.allow_onsite
        if not allowed:
            return f"{mode}: non accettato", f"Modalità {mode.lower()} non accettata dall'utente"
        if city_matches(sede, rule.cities):
            return f"{mode} in zona", None
        where = ", ".join(c.capitalize() for c in rule.cities)
        return (
            f"{mode} fuori zona",
            f"{mode} a {sede or 'sede ignota'}: fuori dalle sedi accettate ({where})",
        )
    return "Non specificato", None  # unknown mode blocks nothing


def blocking_reasons(
    descrizione: str, sede: str, modalita: str, facts: CandidateFacts | None
) -> list[tuple[str, str]]:
    """``(flag code, reason)`` for every user-declared constraint this offer breaks.

    Empty when ``facts`` is None or when nothing is known — an unreadable CV must
    never hide jobs.
    """
    if facts is None:
        return []
    from app.services.scan.hard_requirements import (
        FLAG_EDUCATION,
        FLAG_EXPERIENCE,
        FLAG_LOCATION,
    )

    out: list[tuple[str, str]] = []
    for code, (_label, reason) in (
        (FLAG_EXPERIENCE, experience_status(descrizione, facts)),
        (FLAG_EDUCATION, education_status(descrizione, facts)),
        (FLAG_LOCATION, location_status(sede, modalita, facts)),
    ):
        if reason:
            out.append((code, reason))
    return out


def describe_work_rule(rule: WorkRule) -> str:
    """The rule in the user's words, so they can check we understood it."""
    modes = []
    if rule.allow_onsite:
        modes.append("in sede")
    if rule.allow_hybrid:
        modes.append("ibrido")
    where = ", ".join(c.capitalize() for c in rule.cities) or "ovunque"
    parts = []
    if modes:
        parts.append(f"{' o '.join(modes)} solo a {where}")
    if rule.allow_remote:
        parts.append("full remote ovunque")
    return " · ".join(parts) or "nessun vincolo dichiarato"
