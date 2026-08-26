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

#: Preference keys holding the user's manual corrections. Written ONLY through
#: ``PATCH /api/profile`` — the ``profile_fact_`` prefix is not on the allowlist
#: in ``app.routers.preferences``, so ``POST /api/preferences`` refuses it with
#: ``unknown_preference``. This note used to claim the opposite.
FACT_PREFIX = "profile_fact_"
FACT_YEARS = f"{FACT_PREFIX}years_experience"
FACT_EDUCATION = f"{FACT_PREFIX}education_level"
FACT_GRADE = f"{FACT_PREFIX}grade"
#: Comma-separated family names ("informatica,ingegneria"), for the CV whose
#: education section a parser read wrong — or the graduate whose second degree
#: is in something else entirely.
FACT_DEGREE_FIELDS = f"{FACT_PREFIX}degree_fields"
FACT_BASE_CITIES = f"{FACT_PREFIX}base_cities"
FACT_WORK_MODES = f"{FACT_PREFIX}work_modes"
#: "1"/"0" — whether the user is on the protected-categories register (L. 68/99).
#: Absent means unknown, and unknown blocks nothing, like every other fact here.
FACT_PROTECTED_CATEGORY = f"{FACT_PREFIX}protected_category"
#: "1"/"0" — whether the user holds a category B driving licence.
FACT_DRIVING_LICENCE = f"{FACT_PREFIX}driving_licence"

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

    #: Fractional on purpose: five months of internship is ``0.5``, and rounding
    #: it down to zero moved the blocking boundary by a whole year. See
    #: :func:`_as_years`.
    years_experience: float | None = None
    education_level: str | None = None
    grade: int | None = None
    #: On the protected-categories register (L. 68/99). None = not stated.
    protected_category: bool | None = None
    #: Whether the user holds a category B licence. None = never said, and never
    #: said blocks nothing.
    driving_licence: bool | None = None
    #: Degree families the CV shows, e.g. ``{"informatica"}``. A separate fact
    #: from ``education_level``: one answers "how high", this one "in what".
    degree_fields: frozenset[str] = frozenset()
    work_rule: WorkRule = field(default_factory=WorkRule)
    #: Yearly gross the user will not go below. None = never said, and never
    #: said blocks nothing — the rule every other fact here follows.
    ral_min: int | None = None
    #: fact name -> "cv" | "manuale" | "mancante", for the profile panel.
    sources: dict[str, str] = field(default_factory=dict)

    def missing(self) -> list[str]:
        return [name for name, origin in self.sources.items() if origin == "mancante"]


def _as_int(raw: Any) -> int | None:
    """A whole number, from whatever the CV extractor or a preference produced.

    Used for the facts that ARE whole by nature — a degree mark and a yearly
    gross. It parses through ``float`` first because a preference round-trips as
    text and "95.0" must not read as unknown; the floor that follows is correct
    for both, and an unknown is worse than a rounded one because an unknown
    switches the corresponding check off entirely.

    Years of experience are NOT parsed here — see :func:`_as_years`.
    """
    try:
        value = int(float(str(raw).strip()))
    except (TypeError, ValueError):
        return None
    return value if value >= 0 else None


def _as_years(raw: Any) -> float | None:
    """Years of experience, fraction intact.

    This used to go through :func:`_as_int`, whose docstring argued that "half a
    year of experience is not one" and floored it. That was written to fix a
    different bug — ``0.5`` raised on the old ``int(str(...))`` path, became
    ``None``, and an unknown year count disables the experience check entirely —
    and it landed BEFORE the rule that reads a DISTANCE
    (:data:`BLOCKING_EXPERIENCE_GAP`) rather than the requirement's own size.
    Measured together, the two cost exactly one boundary: floored to ``0``, a CV
    with five months of internship is two years away from a posting asking for
    two and gets the door shut; at its real ``0.5`` it is one and a half away and
    passes. On a real 466-posting archive that hid 19 offers, and it hides them
    from every new graduate — which is most of the people this app is for.

    Still floors nothing and invents nothing: an unreadable value stays ``None``,
    which leaves the check off, and a negative one is not a year count.
    """
    try:
        value = float(str(raw).strip())
    except (TypeError, ValueError):
        return None
    return value if value >= 0 else None


def format_years(value: float | None) -> str:
    """Years of experience as a person would say them, in Italian.

    Not translated, and deliberately consistent with the other facts this module
    hands to the UI — ``education_level`` is already "Triennale" and the blocking
    reasons are already Italian sentences. Under a year reads in months, because
    "0,5 anni" is how a number looks, not how a CV reads.
    """
    if value is None:
        return ""
    if value < 1:
        months = round(value * 12)
        if months <= 0:
            return "meno di un mese"
        return f"{months} mes{'e' if months == 1 else 'i'}"
    if float(value).is_integer():
        whole = int(value)
        return f"{whole} ann{'o' if whole == 1 else 'i'}"
    return f"{value:.1f}".replace(".", ",") + " anni"


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
    years: float | None
    education: str | None
    grade: int | None

    manual_years = _as_years(db.get_preference(FACT_YEARS, ""))
    if manual_years is not None:
        years, sources["years_experience"] = manual_years, "manuale"
    else:
        years = _as_years(summary.get("years_experience"))
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

    raw_protected = (db.get_preference(FACT_PROTECTED_CATEGORY, "") or "").strip().lower()
    protected: bool | None
    if raw_protected in ("1", "si", "sì", "yes", "true"):
        protected, sources["protected_category"] = True, "manuale"
    elif raw_protected in ("0", "no", "false"):
        protected, sources["protected_category"] = False, "manuale"
    else:
        protected, sources["protected_category"] = None, "mancante"

    manual_fields = (db.get_preference(FACT_DEGREE_FIELDS, "") or "").strip()
    if manual_fields:
        fields = frozenset(f.strip().lower() for f in manual_fields.split(",") if f.strip())
        sources["degree_fields"] = "manuale"
    else:
        fields = frozenset(candidate_degree_fields(markdown, summary))
        sources["degree_fields"] = "cv" if fields else "mancante"

    raw_licence = (db.get_preference(FACT_DRIVING_LICENCE, "") or "").strip().lower()
    licence: bool | None
    if raw_licence in ("1", "si", "sì", "yes", "true"):
        licence, sources["driving_licence"] = True, "manuale"
    elif raw_licence in ("0", "no", "false"):
        licence, sources["driving_licence"] = False, "manuale"
    else:
        licence, sources["driving_licence"] = None, "mancante"

    ral_min = _as_int(db.get_preference("onboarding_ral_min", ""))
    sources["ral_min"] = "manuale" if ral_min else "mancante"

    rule = _work_rule_for(db, sources)
    return CandidateFacts(
        years_experience=years,
        education_level=education,
        grade=grade,
        protected_category=protected,
        driving_licence=licence,
        degree_fields=fields,
        work_rule=rule,
        ral_min=ral_min,
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


#: How far below a stated requirement a CV has to sit before the door is really
#: shut. The DISTANCE, not the requirement: Italian postings routinely ask for
#: "1 anno" and hire graduates anyway, and one year of gap is the one people
#: argue across and win. Two is where the filter and the recruiter agree.
#:
#: This replaced a fixed floor on the requirement itself ("two years or more
#: closes the door"), which read the same for everyone — a candidate with two
#: years behind them was shut out of a three-year posting exactly as hard as a
#: new graduate, and that is the difference that gets argued in an interview.
BLOCKING_EXPERIENCE_GAP = 2


def experience_status(descrizione: str, facts: CandidateFacts) -> tuple[str, str | None]:
    """``(years the posting asks for, blocking reason or None)``.

    The band is what the schema shows; the DECISION is taken on the real number,
    because "3+" is where a posting asking three years and one asking ten stop
    being distinguishable and they are not the same distance from anybody.
    """
    from app.services.scan.heuristics import _estimate_experience_band, experience_years_required

    text = str(descrizione or "").lower()
    band = _estimate_experience_band(text)
    required = experience_years_required(text)
    if required is None:  # asks nothing we can measure
        return band, None
    have = facts.years_experience
    if have is None or required - have < BLOCKING_EXPERIENCE_GAP:
        return band, None
    return (
        band,
        f"Richiede {required} anni di esperienza (il profilo ne dichiara {format_years(have)})",
    )


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


# ── the SUBJECT of the degree, which is a different question from its level ──
#
# ``education_status`` compares a bachelor's against a master's and says nothing
# about what the degree is IN. A computer-science graduate was therefore reading
# "hai una laurea in Economia" as satisfied, and PwC's junior auditor sat at 8/10
# in a shortlist built for an IT job hunt. Measured on 423 real descriptions, 245
# of them name a subject: this is the single most stated requirement in the
# archive and the only one nothing read.
#
# Families are named the way the ads name them, and the candidate's own degree is
# matched into the same table — nothing here is written for one person.
_FIELD_FAMILIES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "informatica",
        (
            "informatic",
            "computer",
            "software",
            "ict",
            "cyber",
            "telecomunicazion",
            "information technology",
            "information system",
            "sistemi informativ",
            # Bare "Data" is a subject in its own right in English ads
            # ("Bachelor's degree in Data, Supply Chain Management, ..."), and
            # every accidental match can only make this check quieter, never
            # more aggressive — which is the safe direction for it to err in.
            "data",
            "analytics",
            "intelligenza artificiale",
            "artificial intelligence",
            # "Ingegneria dell'informazione" is the Italian umbrella that
            # contains computer engineering; without it Adgenera's "ingegneria
            # industriale o dell'informazione" read as a foreign subject.
            "informazione",
        ),
    ),
    ("ingegneria", ("ingegneria", "engineering", "ingegner")),
    (
        "economia",
        (
            "economi",
            "business",
            "management",
            "marketing",
            "finanz",
            "finance",
            "accounting",
            "amministrazione",
            "contabil",
            "commercial",
        ),
    ),
    ("giuridica", ("giurisprudenz", "giuridic", "legal", "law")),
    ("matematica", ("matematic", "fisic", "statistic", "mathemat", "physic")),
    (
        "scienze della vita",
        ("biolog", "biomedic", "medicin", "farmac", "life science", "chimic", "chemistr", "health"),
    ),
    (
        "umanistica",
        ("lettere", "filosof", "psicolog", "comunicazione", "lingue", "traduzion", "sociolog"),
    ),
)

#: Phrases that name no subject in particular and therefore exclude nobody. An
#: ad saying "laurea in ambito tecnico-scientifico" accepts a computer-science
#: graduate without spelling it out, and 24 of the 41 live postings phrase it
#: exactly like this.
_FIELD_UMBRELLAS = (
    "stem",
    "tecnico-scientific",
    "tecnico scientific",
    "tecnico/scientific",
    "scientifiche",
    "scientific discipline",
    "technical field",
    "technical discipline",
    "related technical",
    "ambito tecnico",
    "indirizzo tecnico",
    "materie tecnico",
    "discipline tecniche",
    "quantitative",
)

#: A subject named as a wish is not a gate — the same rule ``education_status``
#: already applies to the level.
_FIELD_PREFERENCE_RE = re.compile(
    # "preferred" bare and post-positioned is how English asks for it, and it was
    # the one shape missing: Rotork's "Bachelor's in Electrical or Mechanical
    # Engineering preferred (other engineering fields or equivalent experience
    # will also be considered)" was read as a closed door.
    r"preferib|preferen|preferred|gradit|desirable|preferably|nice to have"
    r"|costituisce titolo|plus",
    re.IGNORECASE,
)

#: "Laurea in X, Y o Z" — the subject list runs to the end of the clause.
_FIELD_REQUIREMENT_RE = re.compile(
    r"(?:laurea|laureand[oai]|laureat[oai]|titolo di studio|degree|bachelor|master)"
    r"[^.;:\n]{0,60}?\bin\b\s*([^.;\n]{3,140})",
    re.IGNORECASE,
)


#: "IT" is a degree subject in half the English-language ads ("Business,
#: Engineering, IT, or Data Analytics") and two letters everywhere else, so it is
#: matched as a WORD and case-SENSITIVELY — the same rule the title gate learned
#: for short acronyms, where lowercase "ai" is an Italian preposition.
_IT_ACRONYM_RE = re.compile(r"\bIT\b")


def _families_named_in(text: str) -> set[str]:
    low = _norm(text)
    found = {name for name, terms in _FIELD_FAMILIES if any(t in low for t in terms)}
    if _IT_ACRONYM_RE.search(str(text or "")):
        found.add("informatica")
    return found


def candidate_degree_fields(markdown: str, summary: dict[str, Any] | None) -> set[str]:
    """Which degree families the CV shows, e.g. ``{"informatica"}``.

    Read from the education lines rather than the whole CV: a developer's skill
    list mentions half the table, and "PostgreSQL" must not make someone a
    graduate in economics.
    """
    sources: list[str] = []
    if isinstance(summary, dict):
        for key in ("education", "education_level", "degree", "field_of_study"):
            value = summary.get(key)
            if isinstance(value, str):
                sources.append(value)
            elif isinstance(value, list):
                sources.extend(str(v) for v in value)
    for line in str(markdown or "").splitlines():
        if re.search(r"laurea|degree|bachelor|master|diploma", line, re.IGNORECASE):
            sources.append(line)
    return _families_named_in(" ".join(sources))


def degree_field_status(descrizione: str, facts: CandidateFacts) -> tuple[str, str | None]:
    """``(subject the posting asks for, reason or None)``.

    Silent unless the posting names a subject AND none of the subjects it names
    belongs to a family the candidate holds. Deliberately reads EVERY degree
    sentence in the ad before deciding: postings routinely list an acceptable
    subject in one line and a preferred one in another, and refusing on the
    second while the first accepts you is the false positive that matters here.
    """
    mine = facts.degree_fields
    if not mine:
        return "Non specificato", None  # unknown subject blocks nothing
    first_named = ""
    for match in _FIELD_REQUIREMENT_RE.finditer(str(descrizione or "")):
        clause = match.group(1).strip()
        if _FIELD_PREFERENCE_RE.search(match.group(0)):
            continue
        low = _norm(clause)
        if any(u in low for u in _FIELD_UMBRELLAS):
            return clause[:60], None
        named = _families_named_in(clause)
        if not named:
            continue  # "laurea in corso", "degree in progress": no subject stated
        if named & mine:
            return clause[:60], None
        first_named = first_named or clause
    if not first_named:
        return "Non specificato", None
    subject = re.sub(r"\s+", " ", first_named).strip(" ,*")[:70]
    have = ", ".join(sorted(mine))
    return subject, f"Chiede una laurea in {subject} (il profilo e' in {have})"


# ── a driving licence, which is a barrier and not a skill ────────────────────
#
# The comment above ``BLOCKING_FLAGS`` has always listed "no driving licence"
# among the non-arguable constraints, and no check ever read one: the barrier was
# assumed to be covered by the unreachable-office rule, which it is not. A field
# role in your own city still needs the car. Cost of the gap, measured: Siemens'
# Implementation Consultant PLM — "Valid driving license and willingness to
# travel within Italy" — was recommended as a Tier-1 offer and applied to.
#
# Rare enough to be worth reading precisely: 11 of 423 real descriptions mention
# a licence at all, so this is nothing like the L. 68/99 boilerplate trap.
_LICENCE_MENTION_RE = re.compile(
    r"patente(?:\s+di\s+guida)?(?:\s+(?:cat\.?|categoria)\s*)?\s*b?\b"
    r"|automunit|driving licen[cs]e|driver'?s licen[cs]e",
    re.IGNORECASE,
)
#: "Nice to have: inglese e patente B" is a wish. Verbatim from EY's ad, and the
#: only one of the eleven that phrases it that way — which is exactly why the
#: guard is needed rather than assumed.
_LICENCE_PREFERENCE_RE = re.compile(
    r"nice to have|preferib|gradit|costituisce titolo|plus|desirable|preferential",
    re.IGNORECASE,
)


def driving_licence_status(descrizione: str, facts: CandidateFacts) -> tuple[str, str | None]:
    """``(label, blocking reason or None)`` for a posting that needs a car.

    Blocks only when the user has explicitly said they do not hold one — an
    unstated fact hides nothing, exactly like every other check here.

    Fires on 7 of 423 real descriptions, all genuine requirements, and correctly
    spares EY's "Nice to have: ... patente B". Known limit, left in deliberately:
    the veto window keeps its left side wide (a heading governs the list under
    it), so a posting that writes "MICROSOFT OFFICE - preferibile / Patenti:
    Patente B - obbligatorio" has the neighbouring bullet's "preferibile" inside
    the window and is missed. One posting in the archive, already blocked for
    other reasons — and a miss leaves the status quo, while the opposite error
    hides a job someone could take.
    """
    if facts.driving_licence is not False:
        return "Non specificato", None
    from app.services.scan.heuristics import _clause_window

    text = str(descrizione or "")
    for match in _LICENCE_MENTION_RE.finditer(text):
        # A veto window, so the tail is clamped at the clause boundary: the next
        # bullet is the next requirement, about something else.
        window = _clause_window(text, match.start(), match.end(), 90)
        if _LICENCE_PREFERENCE_RE.search(window):
            continue
        return "Patente richiesta", "L'annuncio richiede la patente B (il profilo non la ha)"
    return "Non specificato", None


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


# Nearly every Italian IT posting mentions L. 68/99, and nearly none of them is
# reserved: 29 postings in a real 238-offer archive cite it and exactly ONE is
# restricted. The other 28 are equal-opportunity boilerplate — "aperta ANCHE a
# candidati appartenenti alle categorie protette", "valutiamo candidature
# indipendentemente da ... disabilità" — attached to jobs anyone can apply for,
# including the highest-scoring offer in the whole archive. So the rule here is
# deliberately hard to trigger: an inclusive phrase anywhere near the mention
# vetoes the block, and only a posting that states the requirement AS the
# requirement counts.
_PROTECTED_MENTION_RE = re.compile(
    r"categori[ae]\s+protett|collocamento\s+mirato|legge\s+68/99|l\.?\s*68/99|68/99",
    re.IGNORECASE,
)
_PROTECTED_INCLUSIVE_RE = re.compile(
    r"anche\s+a|rivolta\s+anche|aperta\s+anche|indipendentemente|preferenzial|promuoviamo"
    r"|valutiamo|pari\s+opportunit|do\s+not\s+hesitate|committed|inserimento\s+e\s+l|"
    # "prestiamo attenzione E SENSIBILITA' alle FUTURE risorse appartenenti alle
    # categorie protette" — Capgemini's footer, twice in the archive. The phrase
    # already listed here wanted "attenzione alle risorse" adjacent, and three
    # words in between were enough to lose it.
    r"attenzione\s+alle\s+risorse|sensibilit|ambosessi|entrambi\s+i\s+sessi|valorizzazione",
    re.IGNORECASE,
)
_PROTECTED_REQUIRED_RE = re.compile(
    r"riservat\w*\s+(?:a|ai|alle)|esclusivamente\s+(?:a|ai|alle)"
    r"|(?:cerc\w+|ricerc\w+|selezion\w+|figura\s+di|profilo|risorsa|candidat[oa])"
    # ``appartenent[ei]``: the singular alone missed the one posting in the whole
    # archive that really is reserved. NTT DATA wrote "ricerchiamo giovani
    # neolaureati, diplomati **appartenenti** alle categorie protette legge
    # 68/99" — plural, because it is addressing several people — and it scored
    # 8/10 at the top of a shortlist built for someone not on the register.
    r"[^.;!?]{0,80}appartenent[ei]\s+alle\s+categori",
    re.IGNORECASE,
)


def protected_category_status(descrizione: str, facts: CandidateFacts) -> tuple[str, str | None]:
    """``(label, blocking reason or None)`` for a posting reserved to L. 68/99.

    Blocks only when the user has explicitly said they are NOT on the register:
    an unstated fact hides nothing, exactly like the other checks here.
    """
    if facts.protected_category is not False:
        return "Non pertinente", None
    text = str(descrizione or "")
    mention = _PROTECTED_MENTION_RE.search(text)
    if not mention:
        return "Non pertinente", None
    window = text[max(0, mention.start() - 220) : mention.end() + 220]
    if _PROTECTED_INCLUSIVE_RE.search(window):
        return "Citata come pari opportunità", None
    if not _PROTECTED_REQUIRED_RE.search(window):
        return "Citata", None
    return (
        "Riservata alle categorie protette",
        "Posizione riservata alle categorie protette (L. 68/99), non dichiarate nel profilo",
    )


#: What an ad calls the pay when it is stating the pay. Anchoring on the LABEL is
#: the whole design, and it comes from the census rather than from taste: in 469
#: real ads every honest figure sits after one of these words and none of the
#: noise does. "A number near a money word" instead returns the anti-
#: discrimination statutes ("leggi 903/77 e 125/91"), the decree in the pay
#: transparency footer ("DLgs 215/03"), and the meal vouchers — 267 hits under a
#: hundred euros against 632 real annual figures. Same shape as the body reader
#: in ``app.mail``, anchored for the same reason and measured at 29 out of 30.
_PAY_LABEL_RE = re.compile(
    r"\bral\b|retribu|salari[oe]|stipendi|\bsalary\b|compensation|remunerazion"
    r"|indennit[aà]|rimborso\s+spese|offerta\s+economica|pacchetto\s+retributiv"
    r"|trattamento\s+economico|\bpaga\b|\bcompenso\b",
    re.IGNORECASE,
)

#: Money the job pays that is not the pay. Every one of these sits beside a real
#: salary somewhere in the archive and would be read as one.
_NOT_THE_PAY_RE = re.compile(
    r"buoni\s+pasto|ticket|meal\s+voucher|welfare|variabil|variable\s+pay|bonus|fringe"
    r"|premio|stock\s+option|una\s+tantum|rimborso\s+km|formazione",
    re.IGNORECASE,
)

#: A statute, a decree or a date is not an amount: "leggi 903/77 e 125/91",
#: "DLgs 215/03 e 216/03", "Dlgs del 7 maggio 2026, n.ro 96".
_NOT_AN_AMOUNT_RE = re.compile(r"\d/\d|\bn\.?ro\b|\bd\.?lgs\b|\blegg[ei]\b|\bart\b", re.IGNORECASE)

#: A number that could be money, with the thousands separator written either way
#: and the "25K" shorthand the ads use as often as the digits.
_PAY_AMOUNT_RE = re.compile(r"\d{1,3}(?:[.,\s]\d{3})+|\d{3,6}|\d{1,3}\s*k\b", re.IGNORECASE)

#: Currencies that are not the euro. Their figures are skipped rather than
#: converted: an exchange rate belongs in a table somebody maintains.
_FOREIGN_CURRENCY_RE = re.compile(r"£|\$|\bgbp\b|\busd\b|\bchf\b|\bsek\b", re.IGNORECASE)

#: Said out loud, the period settles it. Unsaid, see :func:`salary_status`.
_MONTHLY_RE = re.compile(r"mensil|al\s+mese|/\s*mese|per\s+month|monthly", re.IGNORECASE)
_YEARLY_RE = re.compile(r"annu|per\s+year|yearly|/\s*anno|\bl\.?a\.?\b", re.IGNORECASE)

#: Labels that carry the period inside them. "RAL" is not jargon to be guessed
#: at: it spells out Retribuzione Annua Lorda, so an ad writing "RAL da 12.000 a
#: 15.000" has stated the period as plainly as one writing "annui". Reading it as
#: possibly-monthly was costing exactly the ads that state their pay properly.
_YEARLY_LABEL_RE = re.compile(r"\bral\b|retribuzione\s+annu", re.IGNORECASE)

#: Where the pay sentence ends. A bullet or a new sentence is the next subject.
_PAY_CLAUSE_END_RE = re.compile(r"[;•]|\.\s+[A-Z]|\*\s")


def _pay_figures(descrizione: str) -> list[tuple[int, bool]]:
    """``(amount, the ad said it is yearly)`` for every euro figure stated as pay.

    The period is reported, never inferred. A figure of 26.000 is almost
    certainly yearly and a figure of 600 almost certainly is not, but "almost
    certainly" is an assumption about one country's pay scales, and
    :func:`salary_status` is built so it never has to make one.
    """
    # Markdown emphasis, flattened before anything reads the text: job boards
    # write "**Retribuzione** Da € 23.000", and an asterisk looked exactly like
    # the bullet that ends the pay clause — so the figure was cut off from its
    # own label. Same class of bug as the escaped hyphen in the years detector.
    text = " ".join(str(descrizione or "").replace("\\", "").replace("*", " ").split())
    out: list[tuple[int, bool]] = []
    for label in _PAY_LABEL_RE.finditer(text):
        window = text[label.end() : label.end() + 120]
        for end in (_PAY_CLAUSE_END_RE, _NOT_THE_PAY_RE):
            # The benefit ENDS the pay clause instead of voiding it: "Ral
            # compresa tra 26k e 29k, buoni pasto 8 euro" states a real salary
            # and then a meal voucher, and discarding the window over the second
            # threw away the first.
            cut = end.search(window)
            if cut:
                window = window[: cut.start()]
        if _FOREIGN_CURRENCY_RE.search(window):
            continue
        around_label = text[max(0, label.start() - 40) : label.end() + 120]
        said_yearly = _YEARLY_RE.search(around_label) or _YEARLY_LABEL_RE.search(label.group(0))
        yearly = bool(said_yearly) and not _MONTHLY_RE.search(around_label)
        for amount in _PAY_AMOUNT_RE.finditer(window):
            around = window[max(0, amount.start() - 12) : amount.end() + 12]
            if _NOT_AN_AMOUNT_RE.search(around):
                continue
            digits = re.sub(r"[^\d]", "", amount.group(0))
            if not digits:
                continue
            value = int(digits)
            if re.search(r"\d\s*k\b", amount.group(0), re.IGNORECASE) and value < 1000:
                value *= 1000
            if value < 100:
                continue
            if 1900 <= value <= 2100 and not re.search(r"€|\beur", around, re.IGNORECASE):
                continue  # a year, not an amount
            out.append((value, yearly))
    return out


def declared_pay(descrizione: str) -> str:
    """What the ad says it pays, in its own terms, or "" when it says nothing.

    Deliberately not a number: the period is only known when the ad gave it, and
    a range is a range. "800€ (periodo non dichiarato)" is the honest rendering
    of an internship reimbursement, and the reader is not going to pretend it
    knows the twelve.
    """
    figures = _pay_figures(descrizione)
    if not figures:
        return ""
    amounts = sorted({value for value, _yearly in figures})
    yearly = any(is_yearly for _value, is_yearly in figures)
    shown = f"{amounts[0]:,}".replace(",", ".")
    if len(amounts) > 1:
        shown += f"-{amounts[-1]:,}".replace(",", ".")
    return f"{shown} EUR" + ("/anno" if yearly else " (periodo non dichiarato)")


def salary_status(descrizione: str, facts: CandidateFacts) -> tuple[str, str | None]:
    """``(what the ad pays, blocking reason or None)``, read from the ad itself.

    This used to read the model's ``ral_stimata`` and nothing else, which made it
    the one check in this file whose answer could change between two runs over
    the same unchanged text. Measured on 469 real ads: 186 print a figure and the
    model returned one for 19 of them — it answered "Non stimabile" to "salary
    range min. RAL 35.000 EUR - max RAL 38.000 EUR".

    **The period is never guessed.** An ad writing "rimborso spese a partire da
    600€" does not say per what, and deciding that it means per month would bake
    an assumption about one country's internship market into an app other people
    install with their own CV. So each figure is annualised the most GENEROUS way
    its own text allows, and the door closes only when even that reading falls
    short: 600 cannot reach a 20.000 floor whatever it meant, 25.000 clears it on
    any reading, and the ambiguous middle stays open. That is the side to be
    wrong on — an offer wrongly kept costs a line to read, an offer wrongly hidden
    costs the job.

    Ranges are read at their TOP for the same reason: an ad offering 18.000 to
    24.000 may pay 24.000, and the user is the one who will negotiate it.
    """
    floor = facts.ral_min
    if not floor:
        return "Non dichiarata", None  # no floor stated: nothing to be under
    figures = _pay_figures(descrizione)
    if not figures:
        return "Non dichiarata", None
    # The most an ad could possibly mean: a figure it called yearly is worth
    # itself, one whose period it never gave is worth twelve of itself.
    ceiling = max(value if yearly else value * 12 for value, yearly in figures)
    stated = max(value for value, _yearly in figures)
    if ceiling >= floor:
        return f"Dichiarata: {stated}", None
    return (
        f"Dichiarata: {stated}",
        f"Dichiara al massimo {stated} ({ceiling} EUR/anno), sotto la tua minima ({floor})",
    )


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
        FLAG_DEGREE_FIELD,
        FLAG_DRIVING_LICENCE,
        FLAG_EDUCATION,
        FLAG_EXPERIENCE,
        FLAG_LOCATION,
        FLAG_PROTECTED_CATEGORY,
        FLAG_SALARY_BELOW,
    )

    out: list[tuple[str, str]] = []
    for code, (_label, reason) in (
        (FLAG_EXPERIENCE, experience_status(descrizione, facts)),
        (FLAG_EDUCATION, education_status(descrizione, facts)),
        (FLAG_DEGREE_FIELD, degree_field_status(descrizione, facts)),
        (FLAG_LOCATION, location_status(sede, modalita, facts)),
        (FLAG_DRIVING_LICENCE, driving_licence_status(descrizione, facts)),
        (FLAG_PROTECTED_CATEGORY, protected_category_status(descrizione, facts)),
        # Joined the deterministic checks once it stopped depending on the
        # model: the pay is in the ad's own words, so the offer no longer has to
        # be sent away and judged before anyone can notice it pays too little.
        (FLAG_SALARY_BELOW, salary_status(descrizione, facts)),
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
