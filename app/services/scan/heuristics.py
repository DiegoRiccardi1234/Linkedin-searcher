"""What the app can say about an offer WITHOUT a model — and what it cannot.

Used when the AI is unreachable, when it answers unusably, and when the posting
is too thin to judge on merit. Until v1.7.8 this module also produced a score in
those cases, by counting words shared with the CV: on a real scan that put seven
postings no model had ever read above 6/10, a PAYROLL SPECIALIST among them.

A score is a judgement, and this module makes none. It reads the facts a regex
can honestly read from the text (contract, work mode, years, degree) and leaves
``punteggio`` empty. The result is marked as unevaluated so the job is re-scored
on the next scan, or on demand (see app.scoring_schema).
"""

from __future__ import annotations

import re
from typing import Any

from app.log import get_logger
from app.scoring_schema import ANALYSIS_SOURCE_KEY, NOT_EVALUATED_SOURCE
from app.services.scan.hard_requirements import FLAG_SHORT_DESCRIPTION, _add_flag
from app.services.scan.vocab import TECH_KEYWORDS

log = get_logger(__name__)


def _estimate_programming_demand(offer_text: str) -> str:
    hits = sum(1 for kw in TECH_KEYWORDS if kw in offer_text)
    if hits >= 5:
        return "Alta"
    if hits >= 2:
        return "Media"
    return "Bassa"


# A number followed by "anni"/"years", optionally as a range ("3-5 anni") and
# optionally introduced by a minimum marker ("almeno 2 anni"). The range's LOWER
# bound is the bar to clear: "3-5 anni" asks for three, not five.
_YEARS_RE = re.compile(
    r"(?:almeno|minimo|min\.?|at least|oltre|più di|piu di)?\s*"
    r"(\d{1,2})\s*(?:\+|\-|–|—|/|\s+a\s+)?\s*(\d{1,2})?\s*\+?\s*(?:ann[oi]|years?|yrs?)\b",  # noqa: RUF001
    re.IGNORECASE,
)
# The number must be talking about EXPERIENCE. Without this, "azienda fondata 5
# anni fa" and "corso di 3 anni" were read as a seniority requirement.
_EXPERIENCE_CONTEXT_RE = re.compile(
    r"esperienz|experience|maturat|nel ruolo|seniority|anzianit|in ambito|di lavoro",
    re.IGNORECASE,
)

# Job boards hand us markdown-escaped punctuation ("almeno **1\-2 anni**"). The
# backslash sat between the two halves of the range, so _YEARS_RE could not join
# "1" to "anni" and matched the SECOND number instead — reading every escaped
# range at its UPPER bound. Measured on the real archive: "3\-5 anni" demanded
# five, and "1\-2 anni" demanded two, which is exactly the blocking threshold.
_MD_ESCAPE_RE = re.compile(r"\\([-–—/+*_.])")  # noqa: RUF001

# "sei mesi maturati negli ultimi 2 anni" says WHEN the experience was earned,
# not how much of it is demanded. Read as a requirement it hid an apprenticeship.
_TIME_WINDOW_RE = re.compile(r"ultim[oi]\s*$", re.IGNORECASE)

# "una realtà con oltre 30 anni di esperienza nel settore" is the COMPANY's age.
# It needs all three signals to be dismissed: something introducing the company,
# no wording that turns the number into a demand, and the "oltre/più di" shape
# these boasts always take — otherwise "l'azienda cerca almeno 3 anni" would be
# thrown away too.
_COMPANY_SUBJECT_RE = re.compile(
    r"azienda|realt|societ|gruppo|impresa|studio|siamo|fondat|nasce|opera|player|leader",
    re.IGNORECASE,
)
_DEMAND_RE = re.compile(
    r"almeno|minimo|richie|cerchiamo|ricerchiamo|candidat|profilo|risorsa|maturat"
    r"|possiedi|requisit|must have|we (?:are looking|require)|you have",
    re.IGNORECASE,
)
# Matched against the lead INCLUDING the number: _YEARS_RE swallows "oltre" as
# its own optional prefix, so looking only at what precedes the match never sees
# the boast that gives the company's age away.
_BOAST_RE = re.compile(
    r"(?:oltre|pi[uù] di|over|more than)\s*\d{1,2}\s*(?:[-–—/+]\s*\d{1,2})?\s*(?:ann|year)",  # noqa: RUF001
    re.IGNORECASE,
)


def _estimate_experience_band(offer_text: str) -> str:
    """Years of experience the posting demands: ``0|1|2|3+|Non specificato``.

    Takes the HIGHEST requirement stated, not the first one found: a posting
    asking "1 anno di esperienza in QA, 3 anni in automation" demands three, and
    reading only the first number let it through as a one-year role. Ranges are
    read at their lower bound, and every number must sit near a word that means
    "experience" or it is not a seniority requirement at all.

    Three shapes name a number of years without demanding it, and each one was
    hiding real jobs: an escaped range, a time window, and the company boasting
    about its own age.
    """
    offer_text = _MD_ESCAPE_RE.sub(r"\1", offer_text)
    best: int | None = None
    for match in _YEARS_RE.finditer(offer_text):
        window = offer_text[max(0, match.start() - 70) : match.end() + 70]
        if not _EXPERIENCE_CONTEXT_RE.search(window):
            continue
        before = offer_text[max(0, match.start() - 80) : match.start()]
        if _TIME_WINDOW_RE.search(before):
            continue
        if (
            _COMPANY_SUBJECT_RE.search(before)
            and not _DEMAND_RE.search(before)
            and _BOAST_RE.search(before + match.group(0))
        ):
            continue
        low = int(match.group(1))
        # group(2) is the top of a range; the lower bound is what must be cleared.
        if low > 40:  # a year like "2026", not a duration
            continue
        best = low if best is None else max(best, low)

    if best is not None:
        if best <= 0:
            return "0"
        if best == 1:
            return "1"
        if best == 2:
            return "2"
        return "3+"

    if any(
        token in offer_text for token in ["junior", "entry level", "neolaureat", "stage", "intern"]
    ):
        return "0"
    return "Non specificato"


#: Years implied by each band, for comparing a posting against a CV. "Non
#: specificato" is deliberately absent: an unknown requirement blocks nothing.
EXPERIENCE_BAND_YEARS: dict[str, int] = {"0": 0, "1": 1, "2": 2, "3+": 3}


def _estimate_contract_type(offer_text: str) -> str:
    if any(token in offer_text for token in ["apprendistat", "apprenticeship"]):
        return "Apprendistato"
    if any(token in offer_text for token in ["stage", "intern"]):
        return "Stage"
    if any(token in offer_text for token in ["partita iva", "p.iva", "freelance", "contractor"]):
        return "Partita IVA"
    if any(
        token in offer_text
        for token in ["tempo indeterminato", "full-time", "dipendente", "permanent"]
    ):
        return "Dipendente"
    return "Non specificato"


def _estimate_smart_working(offer_text: str) -> str:
    if any(
        token in offer_text
        for token in ["remote", "full remote", "smart working", "hybrid", "ibrid"]
    ):
        return "Sì"
    if any(token in offer_text for token in ["on-site", "onsite", "in office"]):
        return "No"
    return "Non specificato"


def _fallback_analysis(
    reason: str,
    profile_markdown: str,
    titolo: str,
    azienda: str,
    descrizione: str,
) -> dict[str, Any]:
    """No model produced a usable verdict for this offer, so there is no score.

    ``reason`` (provider error / invalid response) is for diagnostics only — it
    must never leak into the user-facing ``riassunto``. ``profile_markdown`` is
    no longer read: matching a CV against the posting is exactly the guesswork
    this path stopped doing. It stays in the signature because every call site
    passes it by keyword.
    """
    log.warning("Unevaluated offer '%s' @ %s: %s", titolo, azienda, reason)
    return _unscored_analysis(titolo, azienda, descrizione, reason=reason)


_EDU_PHD = re.compile(r"\bph\.?d\b|dottorato di ricerca", re.IGNORECASE)
_EDU_MASTERS = re.compile(
    r"laurea\s+magistrale|laurea\s+specialistica|laurea\s+a\s+ciclo\s+unico"
    r"|master'?s\s+degree|\bmsc\b|\bm\.sc\b",
    re.IGNORECASE,
)
_EDU_BACHELOR = re.compile(
    r"laurea\s+triennale|laurea\s+di\s+primo\s+livello|bachelor'?s?\s+degree|\bbsc\b|\bb\.sc\b",
    re.IGNORECASE,
)
# A bare "laurea" with no level named: a three-year degree satisfies it.
_EDU_ANY_DEGREE = re.compile(r"\blaurea\b|\bdegree\b|\blaureat", re.IGNORECASE)
_EDU_DIPLOMA = re.compile(r"\bdiploma\b|\bperito\b|maturit[aà]|high school", re.IGNORECASE)

#: Ordered from lowest to highest. Used to compare what a posting asks against
#: what the candidate holds — the index IS the ranking.
EDUCATION_LEVELS: tuple[str, ...] = ("Nessuno", "Diploma", "Triennale", "Magistrale", "PhD")

# "laurea magistrale gradita" is a wish, not a gate. Without this an offer that
# explicitly says the bachelor is enough was read as demanding a master's.
# A degree listed under "elementi con attribuzione di punteggio" is the same
# thing said in the language of public-sector rankings: it earns points, it does
# not close the door — and reading it as a gate hid an apprenticeship.
_PREFERRED_RE = re.compile(
    r"preferib|gradit|costituisce\s+(?:un\s+)?(?:titolo\s+)?preferenziale|plus\b"
    r"|nice\s+to\s+have|apprezzat|desiderabil|a\s+plus\b"
    r"|attribuzione\s+di\s+punteggio|titolo\s+preferenziale|costituir[àa]\s+titolo",
    re.IGNORECASE,
)

# "bachelor's or master's degree" and "laurea triennale o magistrale" are open to
# BOTH: the master's pattern matched first and the "bachelor's or" in front of it
# was never read, so a posting that spelled out its openness to a three-year
# degree was treated as closed to one.
_EDU_EITHER = re.compile(
    r"(?:laurea\s+)?(?:triennale|bachelor'?s?(?:\s+degree)?|primo\s+livello|\bbsc\b)"
    r"\s*(?:(?:,\s*)?(?:o|od|or|e/o|and/or|oppure)\s+|\s*/\s*)"
    r"(?:laurea\s+)?(?:magistrale|specialistica|master'?s?(?:\s+degree)?|\bmsc\b)",
    re.IGNORECASE,
)


# Where one requirement ends and the next begins: a line break, a bullet, a
# semicolon, or a full stop that really closes a sentence. The last one needs the
# lookahead: "laurea magistrale in ing. elettronica" would otherwise end the
# clause at the abbreviation, and since this window only ever carries a veto,
# cutting it short means blocking more than the posting asks for.
_CLAUSE_BOUNDARY_RE = re.compile(
    r"[\n\r;•]|(?<=[a-zà-ÿ0-9\)])\.\s+(?=[A-ZÀ-Ý•*\-])",
)


def _clause_window(offer_text: str, start: int, end: int, span: int) -> str:
    """``span`` characters around a match, stopped at the NEXT clause boundary.

    Only the tail is clamped, and the asymmetry is the posting's, not ours: text
    that comes before a requirement often governs it — "Elementi con
    attribuzione di punteggio. Se possiedi: * Laurea Magistrale" is a heading
    ruling the list under it — while the next bullet is simply the next
    requirement, about something else entirely.

    Use only for windows carrying a VETO, a word whose presence cancels the match
    beside it. EY's "Laurea magistrale STEM;" is followed 68 characters later by
    "Fortemente gradita una minima esperienza", and a blind 90-character tail
    read that wish as being about the degree: the posting scored 9/10 with no
    blocker against a three-year degree.

    Deliberately NOT used for windows carrying a QUALIFIER — a word that must be
    present for the match to count, like the "esperienza" that turns a number
    into a seniority demand. Measured on the 238-posting archive, clamping the
    experience window destroyed 8 genuine requirements ("esperienza di almeno 2
    anni nel ruolo", "minimum 6+ years"), because job boards routinely put the
    noun on the line above the number; the same clamp on the protected-category
    veto lost the single truly reserved posting out of the 29 citing the law.
    """
    after = offer_text[end : end + span]
    boundary = _CLAUSE_BOUNDARY_RE.search(after)
    tail = after[: boundary.start()] if boundary else after
    return offer_text[max(0, start - span) : end] + tail


def education_requirement(offer_text: str) -> tuple[str, bool]:
    """``(required level, is only preferred)`` read from the posting.

    The second value matters as much as the first: a posting where the master's
    is "preferibile" must not be treated as closed to a bachelor. It is read
    within the degree's own clause (see :func:`_clause_window`) — a preference
    voiced in the next bullet is a preference about the next bullet.
    """
    for level, pattern in (
        ("PhD", _EDU_PHD),
        ("Magistrale", _EDU_MASTERS),
        ("Triennale", _EDU_BACHELOR),
    ):
        match = pattern.search(offer_text)
        if match:
            window = _clause_window(offer_text, match.start(), match.end(), 90)
            if level == "Magistrale" and _EDU_EITHER.search(window):
                # The same sentence offers the bachelor as an alternative.
                return "Triennale", bool(_PREFERRED_RE.search(window))
            return level, bool(_PREFERRED_RE.search(window))
    if _EDU_ANY_DEGREE.search(offer_text):
        # "laurea in informatica" with no level: a bachelor clears it.
        return "Triennale", False
    if _EDU_DIPLOMA.search(offer_text):
        return "Diploma", False
    return "Non specificato", False


def _detect_education_requirement(offer_text: str) -> str:
    """Required degree as the analysis schema spells it."""
    return education_requirement(offer_text)[0]


def _local_facts(titolo: str, descrizione: str) -> dict[str, Any]:
    """What the posting states about itself, read with regexes.

    Facts, not judgements: every field here is something the text says, so it
    stays true whether or not a model ever looked at the offer. Shared by the
    unevaluated path and by the hard-blocked path, which has a score of its own.
    """
    offer_text = f"{titolo} {descrizione}".lower()
    return {
        "programmazione_richiesta": _estimate_programming_demand(offer_text),
        "smart_working": _estimate_smart_working(offer_text),
        "contratto": _estimate_contract_type(offer_text),
        "anni_esperienza_richiesti": _estimate_experience_band(offer_text),
        "titolo_studio_richiesto": _detect_education_requirement(offer_text),
        "adatta_neolaureati": "Sì"
        if any(token in offer_text for token in ["junior", "stage", "intern", "entry"])
        else "Non specificato",
    }


def _unscored_analysis(
    titolo: str, azienda: str, descrizione: str, *, reason: str
) -> dict[str, Any]:
    """An offer nobody judged: the facts it states, and no verdict.

    ``punteggio`` is None rather than a low number — a low number is still a
    judgement, and it would sort among real ones. ``_normalize_analysis``
    enforces the rest (empty advice, null axes, the ``non_valutato`` flag) and
    leaves out the schema version, which is what gets the job re-scored later.
    """
    return {
        ANALYSIS_SOURCE_KEY: NOT_EVALUATED_SOURCE,
        "punteggio": None,
        "consiglio": "",
        # Diagnostics: why no model produced a verdict. Not user-facing copy.
        "motivo_non_valutazione": reason,
        "punti_forza": "",
        "punti_deboli": "",
        "riassunto": "",
        "ral_stimata": "Non stimabile",
        **_local_facts(titolo, descrizione),
    }


# Below this many chars a description carries no requirements/seniority signal
# (real case: an 82-char marketing blurb) — LLM-scoring it just hallucinates.
# Such jobs take the honest capped path instead. Length measured post-clean.
MIN_DESCRIPTION_CHARS = 300


def _insufficient_description_analysis(
    profile_markdown: str, titolo: str, azienda: str, descrizione: str = ""
) -> dict[str, Any]:
    """A job whose description is missing or too short to judge on merit
    (LinkedIn blocked the page, or served a marketing blurb without the JD).

    Nobody can judge a posting nobody could read, so it gets no score — not even
    a capped one. Sending it to the LLM anyway is worse: on a real 82-character
    blurb the model invented requirements wholesale. The offer stays visible,
    flagged, with the one useful instruction: open the ad.
    """
    result = _unscored_analysis(titolo, azienda, descrizione, reason="insufficient_description")
    _add_flag(result, FLAG_SHORT_DESCRIPTION)
    if descrizione.strip():
        result["riassunto"] = "Descrizione troppo breve per valutare. Apri l'annuncio."
        result["punti_deboli"] = (
            "Descrizione quasi assente: requisiti ed esperienza richiesta non verificati."
        )
    else:
        result["riassunto"] = "Descrizione non disponibile. Apri l'annuncio."
        result["punti_deboli"] = (
            "Descrizione non recuperata: requisiti ed esperienza richiesta non verificati."
        )
    return result
