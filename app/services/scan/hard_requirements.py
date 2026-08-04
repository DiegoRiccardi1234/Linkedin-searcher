"""Checks the app makes itself, after the model has answered.

The scoring prompt asks the model to weigh these and it repeatedly didn't, so
they are applied deterministically: geographic eligibility, minimum degree
grade, declared salary against the candidate's floor, and gig-vs-employment.
Caps can only LOWER a score, never raise it, and every check records a flag code
(see :func:`_add_flag`) so the UI can show WHY a score is what it is.

Also home to :func:`_normalize_analysis`, which gives every analysis the same
shape whatever the model returned, and stamps the schema version that decides
whether a job gets re-scored.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from app.scoring_schema import (
    ANALYSIS_SOURCE_KEY,
    ANALYSIS_VERSION_KEY,
    CURRENT_ANALYSIS_VERSION,
    HEURISTIC_SOURCE,
    NOT_EVALUATED_SOURCE,
)
from app.services.onboarding import RAL_MIN_LABEL

# ─── Deterministic hard-requirement checks (run AFTER the model) ───────────
# _SCORING_RULES already asks the model to weigh these, and it repeatedly didn't:
# on the 2026-07-21 scan a posting demanding "min. 102/110" scored 10 against a
# 95/110 CV, and ten US-based jobs (candidate has no visa and won't relocate)
# scored up to 8, two of them "Candidati subito". These checks can only LOWER a
# score, never raise it, so a good model is never punished by them.

_GRADE_RE = re.compile(r"(\d{2,3})\s*/\s*110")

# Both caps sit below the "Valutabile" band (>=5): an offer the candidate cannot
# take must never outrank one they can, but stays visible instead of vanishing.
_GEO_INELIGIBLE_CAP = 3
_GRADE_INELIGIBLE_CAP = 3

# Countries/regions the candidate cannot work in without a visa or relocation.
# "DE" is deliberately NOT in the US-state list: "Berlin, DE" (EU) would collide
# with Delaware. The EU allowlist is checked first, so a location naming an EU
# country never reaches these patterns.
_EU_LOCATION_RE = re.compile(
    r"\b(ital(?:y|ia)|spain|espa[nñ]a|france|francia|german(?:y|ia)|deutschland|netherlands"
    r"|paesi bassi|belgium|belgio|portugal|portogallo|ireland|irlanda|austria|poland|polonia"
    r"|sweden|svezia|denmark|danimarca|finland|finlandia|greece|grecia|czech|cechia|romania"
    r"|hungary|ungheria|croatia|croazia|slovak|sloven|bulgaria|estonia|latvia|lithuania"
    r"|luxembourg|lussemburgo|malta|cyprus|cipro|european union|europe|europa)\b",
    re.IGNORECASE,
)
_NON_EU_LOCATION_RE = re.compile(
    r",\s*(?:AL|AK|AZ|AR|CA|CO|CT|DC|FL|GA|HI|ID|IL|IN|IA|KS|KY|LA|MA|MD|ME|MI|MN|MO|MS|MT"
    r"|NC|ND|NE|NH|NJ|NM|NV|NY|OH|OK|OR|PA|RI|SC|SD|TN|TX|UT|VA|VT|WA|WI|WV|WY)\b"
    r"|united states|,\s*usa\b|united kingdom|england|scotland|wales|,\s*uk\b|canada"
    r"|australia|new zealand|india|singapore|japan|brazil|mexico|argentina|switzerland"
    r"|svizzera|dubai|emirates|israel|south africa",
    re.IGNORECASE,
)
# Explicit openness to EU/Italy-based remote workers. Without one of these a
# non-EU posting is treated as not applicable, not as "maybe".
_EU_REMOTE_OK_RE = re.compile(
    r"work from anywhere|anywhere in the world|remote[^.\n]{0,40}(europe|emea|\beu\b)"
    r"|(europe|emea|\beu\b)[^.\n]{0,40}remote|based in (europe|italy|the eu)|ital(?:y|ia)",
    re.IGNORECASE,
)


def _is_non_eu_location(sede: str) -> bool:
    """True when the posting's location is outside the EU (visa/relocation needed)."""
    text = (sede or "").strip()
    if not text or _EU_LOCATION_RE.search(text):
        return False
    return bool(_NON_EU_LOCATION_RE.search(text))


def _extract_min_grade(text: str) -> int | None:
    """Highest ``NN/110`` degree-grade threshold stated in a posting, if any."""
    grades = [int(g) for g in _GRADE_RE.findall(text or "")]
    grades = [g for g in grades if 60 <= g <= 110]
    return max(grades) if grades else None


def _profile_grade(profile_markdown: str) -> int | None:
    """The candidate's degree grade as written in the CV (first ``NN/110``)."""
    grades = [int(g) for g in _GRADE_RE.findall(profile_markdown or "")]
    grades = [g for g in grades if 60 <= g <= 110]
    return grades[0] if grades else None


def _geo_status(sede: str, descrizione: str) -> tuple[str, str | None]:
    """``(eligibility label, blocking reason or None)`` for a posting's location.

    Single source of truth for "can the candidate legally take this job": it is
    read BEFORE the LLM call (to skip it) and again AFTER (to cap whatever the
    model answered). Those were two separate implementations of the same
    condition that had to be kept in sync by hand.
    """
    if not _is_non_eu_location(sede):
        return ("Italia/UE" if sede.strip() else "Non specificato"), None
    if _EU_REMOTE_OK_RE.search(descrizione or ""):
        return "Fuori UE, ma l'annuncio cita apertura remota UE", None
    return "Fuori UE: non candidabile", f"Sede fuori UE ({sede}): richiede visto/relocation"


def _grade_status(profile_markdown: str, descrizione: str) -> tuple[str, str | None]:
    """``(required grade label, blocking reason or None)``. See :func:`_geo_status`."""
    required = _extract_min_grade(descrizione)
    if required is None:
        return "Non specificato", None
    label = f"{required}/110"
    candidate = _profile_grade(profile_markdown)
    if candidate is None or candidate >= required:
        return label, None
    return label, f"Voto minimo {required}/110 (CV: {candidate}/110)"


def hard_block_reason(
    profile_markdown: str,
    descrizione: str,
    sede: str,
    *,
    facts: Any = None,
    modalita: str = "",
) -> str | None:
    """Why this offer is a non-starter for the candidate, or None.

    Every blocker here is decidable from the text alone, so the caller can skip
    the LLM entirely instead of paying a call and capping the answer afterwards
    (measured on a real scan: 12 offers out of 44 — ~27% of the scoring quota).

    ``facts`` carries what the user declared about themselves (years, degree,
    where they can work). It is optional and defaults to "unknown", which blocks
    nothing: a CV the parser misread must never hide jobs.
    """
    reason = _geo_status(sede, descrizione)[1] or _grade_status(profile_markdown, descrizione)[1]
    if reason:
        return reason
    for _code, detail in _declared_constraint_breaks(descrizione, sede, modalita, facts):
        return detail
    return None


def _declared_constraint_breaks(
    descrizione: str, sede: str, modalita: str, facts: Any
) -> list[tuple[str, str]]:
    """User-declared constraints this offer breaks. Imported late to stay acyclic."""
    from app.services.candidate_facts import blocking_reasons

    return blocking_reasons(descrizione, sede, modalita, facts)


# ── declared salary vs the candidate's floor ─────────────────────────────────
# jobspy returns no salary at all (N/D on 78/78 rows measured), and the model's
# own ``ral_stimata`` is "Non stimabile" half the time, so this is a FLAG, never
# a score cap: capping would punish the rare posting honest enough to publish a
# figure while leaving every silent one untouched.

_RAL_AMOUNT_RE = re.compile(r"(\d{1,3}(?:[.\s]\d{3})+|\d{4,6}|\d{2,3}\s*k)", re.IGNORECASE)


def _parse_ral(raw: Any) -> tuple[int | None, int | None]:
    """(min, max) yearly euros stated in a ``ral_stimata`` string, if any."""
    text = str(raw or "").strip().lower()
    if not text or "non stimabile" in text or "non estimabile" in text:
        return (None, None)
    amounts: list[int] = []
    for match in _RAL_AMOUNT_RE.findall(text):
        digits = re.sub(r"[^\d]", "", match)
        if not digits:
            continue
        value = int(digits)
        if "k" in match.lower():
            value *= 1000
        if 5_000 <= value <= 500_000:
            amounts.append(value)
    if not amounts:
        return (None, None)
    return (min(amounts), max(amounts))


def _ral_min_from_context(extra_context: str) -> int | None:
    """The candidate's minimum salary as rendered by ``onboarding_context``."""
    match = re.search(
        rf"{re.escape(RAL_MIN_LABEL)}\s*:\s*([\d.\s]+)", extra_context or "", re.IGNORECASE
    )
    if not match:
        return None
    digits = re.sub(r"[^\d]", "", match.group(1))
    return int(digits) if digits else None


def _apply_salary_expectation(analysis: dict[str, Any], ral_min: int | None) -> None:
    """Flag (not cap) an offer whose declared salary is under the user's floor."""
    low, high = _parse_ral(analysis.get("ral_stimata"))
    if ral_min and high and high < ral_min:
        detail = f"RAL dichiarata fino a {high} EUR, sotto la tua minima ({ral_min})"
        _add_flag(analysis, FLAG_SALARY_BELOW, detail)
        _add_missing(analysis, detail)
        previous = str(analysis.get("punti_deboli") or "").strip()
        analysis["punti_deboli"] = (
            f"Retribuzione sotto la RAL minima dichiarata ({ral_min} EUR). {previous}".strip()
        )
        axes = analysis.get("match_axes")
        if isinstance(axes, dict):
            axes["salary_match"] = 1
    elif ral_min and low and low >= ral_min:
        axes = analysis.get("match_axes")
        if isinstance(axes, dict):
            axes["salary_match"] = max(int(axes.get("salary_match") or 0), 7)


#: jobspy pay figures come with an ``interval``; normalise everything to a year.
_PAY_PERIODS_PER_YEAR = {"yearly": 1.0, "monthly": 12.0, "weekly": 52.0, "daily": 220.0}


def _annual_amount(value: Any, interval: str = "") -> float | None:
    """A posting's pay figure normalised to EUR/year, or None if unusable."""
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return None
    if amount <= 0 or amount != amount:  # NaN
        return None
    factor = _PAY_PERIODS_PER_YEAR.get(str(interval or "").strip().lower())
    if factor:
        return amount * factor
    if str(interval or "").strip().lower() == "hourly":
        return amount * 1720.0  # ~40h x 43 weeks, deliberately conservative
    # No interval declared: only trust a figure that can only be a yearly one.
    return amount if amount >= 10_000 else None


def _salary_axis(low: float | None, high: float | None, ral_min: int | None) -> int | None:
    """0-10 salary axis from the posting's declared pay vs the user's floor.

    None whenever the comparison cannot be made — the posting says nothing, or
    the user declared no minimum. A number nobody computed is worse than a
    missing axis: the radar draws a confident 5 and the user reads it as
    "average pay" when it means "no idea".
    """
    top = high or low
    if not top or not ral_min:
        return None
    ratio = top / float(ral_min)
    if ratio < 0.8:
        return 1
    if ratio < 1.0:
        return 3
    if ratio < 1.2:
        return 6
    if ratio < 1.5:
        return 8
    return 10


def _has_salary_signal(analysis: dict[str, Any]) -> bool:
    """True when SOMETHING real is known about this offer's pay."""
    if any(_parse_ral(analysis.get(key)) != (None, None) for key in ("ral_stimata",)):
        return True
    for key in ("stipendio_min", "stipendio_max"):
        value = analysis.get(key)
        if value not in (None, "", "N/D") and str(value).strip().lower() != "n/d":
            return True
    return False


# Companies whose "jobs" are platform task work, not employment. Fixed list of
# the channels already mapped for this market; the text markers below catch the
# rest. Deterministic, so it wins over whatever the model guessed.
_GIG_COMPANIES = (
    "toloka",
    "innodata",
    "oneforma",
    "pactera",
    "alignerr",
    "labelbox",
    "invisible",
    "meridial",
    "cntxt",
    "appen",
    "telus international",
    "outlier",
    "mindrift",
    "remotasks",
    "clickworker",
    "prolific",
)
_GIG_TEXT_RE = re.compile(
    r"pay per task|paid per task|per[- ]task basis|hourly rate|project[- ]based work"
    r"|no minimum hours|nessun monte ore|collaborazione occasionale|\bgig\b|freelance marketplace",
    re.IGNORECASE,
)
_PIVA_RE = re.compile(r"partita iva|\bp\.?\s?iva\b|contratto di collaborazione", re.IGNORECASE)


# Not a position: a form. "Candidatura Spontanea in Joinrs | RAL 22K-27K" scored
# 8/10 on a real scan — there is no role, no requirements and nothing to apply
# to, only an invitation to leave your details.
_BAIT_TITLE_RE = re.compile(
    r"candidatura spontanea|autocandidatura|spontaneous application|talent (?:pool|community)"
    r"|entra nella community|iscriviti alla community|open application|general application",
    re.IGNORECASE,
)

#: Job boards that republish other employers' ads under their own name. Unlike
#: the bait titles above these sometimes carry a real role, so they are flagged
#: rather than dropped: the company shown is not the one doing the hiring.
_AGGREGATOR_COMPANIES = (
    "joinrs",
    "jobbydoo",
    "jooble",
    "talent.com",
    "neuvoo",
    "trovit",
    "careerjet",
    "adzuna",
    "jobrapido",
    "whatjobs",
)


def is_bait_posting(titolo: str) -> bool:
    """True when the 'offer' is a lead-capture form rather than a position."""
    return bool(_BAIT_TITLE_RE.search(titolo or ""))


def _is_aggregator(azienda: str) -> bool:
    company = (azienda or "").lower()
    return any(name in company for name in _AGGREGATOR_COMPANIES)


def _detect_engagement(azienda: str, offer_text: str) -> str | None:
    """Engagement type when the posting makes it unambiguous, else None."""
    company = (azienda or "").lower()
    if any(name in company for name in _GIG_COMPANIES) or _GIG_TEXT_RE.search(offer_text or ""):
        return "Gig a task"
    if _PIVA_RE.search(offer_text or ""):
        return "Freelance P.IVA"
    return None


# ── machine-readable flags ───────────────────────────────────────────────────
# Why a score is what it is, as stable codes instead of a sentence glued to the
# front of the weakness line. The app computes all of these deterministically,
# so the UI can badge them, filter on them and translate them — until now
# "you cannot legally take this job" reached the user as a 3/10 and nothing else.
FLAG_GEO_BLOCKED = "geo_non_ue"  # outside the EU, no visa, no relocation
FLAG_GRADE_BLOCKED = "voto_minimo"  # posting demands a degree grade above the CV's
FLAG_SHORT_DESCRIPTION = "descrizione_breve"  # judged on a blurb, capped
FLAG_HEURISTIC = "analisi_locale"  # no model saw this: keyword score
FLAG_GIG = "lavoro_a_task"  # platform/gig work, not employment
FLAG_SALARY_BELOW = "ral_sotto_minima"  # declared pay under the user's floor
FLAG_NOT_EVALUATED = "non_valutato"  # no model judged this: there is no score
FLAG_AGGREGATOR = "annuncio_aggregatore"  # a job board reposting someone else's ad
FLAG_EXPERIENCE = "esperienza_richiesta"  # asks for more years than the CV shows
FLAG_EDUCATION = "titolo_superiore"  # demands a degree above the candidate's
FLAG_LOCATION = "sede_non_raggiungibile"  # on-site/hybrid outside the accepted cities

#: Flags that mean "you cannot take this job", as opposed to "read carefully".
#: ``FLAG_NOT_EVALUATED`` is deliberately NOT here: "nobody judged it" is not
#: "you cannot apply" — the offer may well be the best one in the archive.
BLOCKING_FLAGS = frozenset(
    {
        FLAG_GEO_BLOCKED,
        FLAG_GRADE_BLOCKED,
        FLAG_EXPERIENCE,
        FLAG_EDUCATION,
        FLAG_LOCATION,
    }
)


def is_unevaluated(analysis: Mapping[str, Any]) -> bool:
    """True when no score was ever produced for this offer.

    Defined on the invariant (a missing score) rather than on the provenance
    marker, so a deterministic cap applied afterwards — which DOES set a score —
    automatically stops matching, with no cleanup logic to keep in sync.
    """
    return analysis.get("punteggio") is None


def _add_flag(analysis: dict[str, Any], code: str, detail: str = "") -> None:
    """Record a flag, plus the exact sentence explaining it.

    ``detail`` is kept separate from ``skills_match.mancano`` on purpose: the
    blocker is listed there too (the user wants to see it), but the UI needs to
    know which of those entries is a legal blocker and which is a genuinely
    missing skill — otherwise "outside the EU: needs a visa" is rendered as a
    skill the candidate lacks.
    """
    flags = analysis.get("blocchi")
    if not isinstance(flags, list):
        flags = []
        analysis["blocchi"] = flags
    if code not in flags:
        flags.append(code)
    if detail:
        details = analysis.get("blocchi_dettaglio")
        if not isinstance(details, dict):
            details = {}
            analysis["blocchi_dettaglio"] = details
        details[code] = detail


def _add_missing(analysis: dict[str, Any], item: str) -> None:
    """Append a blocking requirement to ``skills_match.mancano`` (created if absent)."""
    skills = analysis.get("skills_match")
    if not isinstance(skills, dict):
        skills = {"hai": [], "mancano": []}
        analysis["skills_match"] = skills
    missing = skills.get("mancano")
    if not isinstance(missing, list):
        missing = []
        skills["mancano"] = missing
    if item not in missing:
        missing.append(item)


def _cap_score(analysis: dict[str, Any], cap: int, weakness: str) -> None:
    """Lower the score to ``cap`` (never raise it) and mark the offer as a skip."""
    try:
        current = int(analysis.get("punteggio", 0) or 0)
    except (TypeError, ValueError):
        current = 0
    analysis["punteggio"] = min(current, cap) if current else cap
    analysis["consiglio"] = "Salta"
    previous = str(analysis.get("punti_deboli") or "").strip()
    analysis["punti_deboli"] = f"{weakness} {previous}".strip()
    # A capped offer HAS been judged — deterministically, by this app. "Not
    # evaluated" and "3/10 because you cannot legally take it" cannot both hold.
    flags = analysis.get("blocchi")
    if isinstance(flags, list) and FLAG_NOT_EVALUATED in flags:
        flags.remove(FLAG_NOT_EVALUATED)


#: Same ceiling as the geo/grade caps: an offer the user cannot take must never
#: outrank one they can, but it stays visible instead of vanishing.
_DECLARED_CONSTRAINT_CAP = 3

#: Short sentence prepended to ``punti_deboli`` per flag, so the list view says
#: why in the user's language instead of showing a bare code.
_CONSTRAINT_WEAKNESS = {
    FLAG_EXPERIENCE: "Chiede più anni di esperienza di quelli dichiarati nel profilo.",
    FLAG_EDUCATION: "Chiede un titolo di studio superiore a quello del profilo.",
    FLAG_LOCATION: "Sede e modalità fuori da quelle accettate.",
}


def _apply_declared_constraints(
    analysis: dict[str, Any], descrizione: str, sede: str, modalita: str, facts: Any
) -> None:
    """Cap offers that break what the USER declared, not what we assumed.

    Years, degree and location were asked of the model in prose and ignored: on a
    real scan 8 offers out of 35 scored >=8 while their own
    ``anni_esperienza_richiesti`` field said 1 or 2, and one demanding a master's
    scored 8 against a bachelor. Reading the same fields deterministically is the
    only thing that made those stop being recommended.
    """
    for code, reason in _declared_constraint_breaks(descrizione, sede, modalita, facts):
        _add_flag(analysis, code, reason)
        _add_missing(analysis, reason)
        _cap_score(analysis, _DECLARED_CONSTRAINT_CAP, _CONSTRAINT_WEAKNESS.get(code, reason))


def _apply_geo_eligibility(analysis: dict[str, Any], sede: str, descrizione: str) -> None:
    """Cap offers the candidate legally can't take (no visa, no relocation)."""
    label, reason = _geo_status(sede, descrizione)
    if label != "Non specificato" or not analysis.get("eleggibilita_geografica"):
        analysis["eleggibilita_geografica"] = label
    if not reason:
        return
    _add_flag(analysis, FLAG_GEO_BLOCKED, reason)
    _add_missing(analysis, reason)
    _cap_score(analysis, _GEO_INELIGIBLE_CAP, "Sede fuori UE: non candidabile senza visto.")


def _apply_grade_requirement(
    analysis: dict[str, Any], profile_markdown: str, descrizione: str
) -> None:
    """Cap offers whose stated minimum degree grade is above the candidate's."""
    label, reason = _grade_status(profile_markdown, descrizione)
    analysis["voto_minimo_richiesto"] = label
    if not reason:
        return
    _add_flag(analysis, FLAG_GRADE_BLOCKED, reason)
    _add_missing(analysis, reason)
    candidate = _profile_grade(profile_markdown)
    _cap_score(
        analysis,
        _GRADE_INELIGIBLE_CAP,
        f"Voto minimo richiesto {label}, il CV ne dichiara {candidate}/110.",
    )


# Neutral defaults for every key the frontend reads. The model returned 3
# different key sets within a single scan (18/23/24 keys); job_detail.js then
# rendered an empty radar or no skills for the short variants. Normalising here
# makes the shape a property of the app, not of the model's mood.
_MATCH_AXES_KEYS = (
    "skills_match",
    "seniority_match",
    "remote_match",
    "salary_match",
    "contract_match",
)


#: Old key -> current key. The strengths/weaknesses fields carried the
#: developer's first name in the public schema (and in every user's CSV export);
#: a model given a proper noun in a key also tends to hunt for that name in the
#: CV. Stored analyses written before the rename are mapped on read.
_LEGACY_KEYS = {
    "punti_forza_per_diego": "punti_forza",
    "punti_deboli_per_diego": "punti_deboli",
}


def _normalize_analysis(analysis: dict[str, Any]) -> dict[str, Any]:
    """Return ``analysis`` with every documented key present and well-typed."""
    out = dict(analysis)

    for old, new in _LEGACY_KEYS.items():
        value = out.pop(old, None)
        if value and not out.get(new):
            out[new] = value

    # The model sometimes emits a top-level "mancano" instead of nesting it.
    stray_missing = out.pop("mancano", None)

    skills = out.get("skills_match")
    if not isinstance(skills, dict):
        skills = {"hai": [], "mancano": []}
    for key in ("hai", "mancano"):
        if not isinstance(skills.get(key), list):
            skills[key] = []
    stray_items = (
        stray_missing
        if isinstance(stray_missing, list)
        else [stray_missing]
        if isinstance(stray_missing, str) and stray_missing.strip()
        else []
    )
    skills["mancano"] = skills["mancano"] + [m for m in stray_items if m not in skills["mancano"]]
    out["skills_match"] = skills

    axes = out.get("match_axes")
    if not isinstance(axes, dict):
        axes = {}
    for key in _MATCH_AXES_KEYS:
        try:
            axes[key] = max(0, min(10, int(axes.get(key, 5))))
        except (TypeError, ValueError):
            axes[key] = 5
    # An axis with no underlying data is worse than a missing one: it draws a
    # confident "5" on the radar. Measured: 37 of 78 analyses had exactly that,
    # because no source (jobspy or model) knew any salary. None = "N/D", and the
    # frontend drops the axis instead of plotting a number nobody computed.
    if not _has_salary_signal(out):
        axes["salary_match"] = None
    out["match_axes"] = axes

    for key in ("requisiti", "responsabilita", "benefit"):
        if not isinstance(out.get(key), list):
            out[key] = []
    for key, default in (
        ("livello_richiesto", "Non specificato"),
        ("titolo_studio_richiesto", "Non specificato"),
        ("voto_minimo_richiesto", "Non specificato"),
        ("eleggibilita_geografica", "Non specificato"),
        ("tipo_ingaggio", "Non specificato"),
        ("ral_stimata", "Non stimabile"),
        ("punti_forza", ""),
        ("punti_deboli", ""),
        ("riassunto", ""),
        ("consiglio", "Valutabile"),
    ):
        if not isinstance(out.get(key), str) or not out.get(key):
            out[key] = default

    # Provenance and schema version (see app.scoring_schema). A heuristic result
    # is a legitimate thing to show the user, but never a reason to skip
    # re-scoring the job later: it keeps its source marker and carries NO
    # version, so the scan loop treats it as "not analysed yet". Previously the
    # marker was one of the keys injected right above, which every analysis got
    # — heuristics included — freezing keyword scores forever.
    if not isinstance(out.get("blocchi"), list):
        out["blocchi"] = []
    source = str(out.get(ANALYSIS_SOURCE_KEY, ""))
    if source == NOT_EVALUATED_SOURCE:
        # Nobody judged this offer, so the defaults filled in above — a score,
        # "Valutabile", five axes at 5 — would be inventions. Strip them here,
        # after every other branch has run, so no producer can leak a made-up
        # verdict by forgetting a check of its own.
        out.pop(ANALYSIS_VERSION_KEY, None)
        out["punteggio"] = None
        out["consiglio"] = ""
        out["match_axes"] = dict.fromkeys(_MATCH_AXES_KEYS)
        _add_flag(out, FLAG_NOT_EVALUATED)
    elif source == HEURISTIC_SOURCE:
        out.pop(ANALYSIS_VERSION_KEY, None)
        _add_flag(out, FLAG_HEURISTIC)
    else:
        out.pop(ANALYSIS_SOURCE_KEY, None)
        out[ANALYSIS_VERSION_KEY] = CURRENT_ANALYSIS_VERSION
    return out


def enforce_hard_requirements(
    analysis: dict[str, Any],
    *,
    profile_markdown: str,
    descrizione: str,
    sede: str = "",
    azienda: str = "",
    extra_context: str = "",
    facts: Any = None,
    modalita: str = "",
) -> dict[str, Any]:
    """Normalise the schema, then apply the deterministic checks.

    Single post-processing point for EVERY scoring path — single offer, batch
    slot and heuristic fallback — so an offer can never be recommended over a
    hard blocker just because a given path skipped the check. Caps (geo, grade,
    experience, degree, location) can only lower a score; the salary and
    engagement checks only annotate, and every check records a flag code so the
    UI can say WHY (see ``_add_flag``).

    ``facts``/``modalita`` carry what the user declared about themselves and how
    the posting is worked. Both default to "unknown", which blocks nothing.
    """
    out = _normalize_analysis(analysis)
    _apply_grade_requirement(out, profile_markdown, descrizione)
    _apply_geo_eligibility(out, sede, descrizione)
    _apply_declared_constraints(out, descrizione, sede, modalita, facts)
    _apply_salary_expectation(out, _ral_min_from_context(extra_context))
    engagement = _detect_engagement(azienda, f"{descrizione} {out.get('contratto', '')}")
    if engagement:
        out["tipo_ingaggio"] = engagement
    if str(out.get("tipo_ingaggio", "")) in ("Gig a task", "Freelance P.IVA"):
        _add_flag(out, FLAG_GIG)
    if _is_aggregator(azienda):
        _add_flag(
            out,
            FLAG_AGGREGATOR,
            "Annuncio ripubblicato da un aggregatore: l'azienda mostrata non è quella che assume.",
        )
    return out
