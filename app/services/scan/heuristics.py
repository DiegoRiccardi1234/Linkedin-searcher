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


def _estimate_experience_band(offer_text: str) -> str:
    """Years of experience the posting demands: ``0|1|2|3+|Non specificato``.

    Takes the HIGHEST requirement stated, not the first one found: a posting
    asking "1 anno di esperienza in QA, 3 anni in automation" demands three, and
    reading only the first number let it through as a one-year role. Ranges are
    read at their lower bound, and every number must sit near a word that means
    "experience" or it is not a seniority requirement at all.
    """
    best: int | None = None
    for match in _YEARS_RE.finditer(offer_text):
        window = offer_text[max(0, match.start() - 70) : match.end() + 70]
        if not _EXPERIENCE_CONTEXT_RE.search(window):
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
_PREFERRED_RE = re.compile(
    r"preferib|gradit|costituisce\s+(?:un\s+)?(?:titolo\s+)?preferenziale|plus\b"
    r"|nice\s+to\s+have|apprezzat|desiderabil|a\s+plus\b",
    re.IGNORECASE,
)


def education_requirement(offer_text: str) -> tuple[str, bool]:
    """``(required level, is only preferred)`` read from the posting.

    The second value matters as much as the first: a posting where the master's
    is "preferibile" must not be treated as closed to a bachelor.
    """
    for level, pattern in (
        ("PhD", _EDU_PHD),
        ("Magistrale", _EDU_MASTERS),
        ("Triennale", _EDU_BACHELOR),
    ):
        match = pattern.search(offer_text)
        if match:
            window = offer_text[max(0, match.start() - 90) : match.end() + 90]
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
