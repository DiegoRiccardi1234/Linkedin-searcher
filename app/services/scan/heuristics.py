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


def _estimate_experience_band(offer_text: str) -> str:
    years_match = re.search(r"(\d+)\s*\+?\s*(?:anni|years)", offer_text)
    if years_match:
        years = int(years_match.group(1))
        if years <= 0:
            return "0"
        if years == 1:
            return "1"
        if years == 2:
            return "2"
        return "3+"

    if any(
        token in offer_text for token in ["junior", "entry level", "neolaureat", "stage", "intern"]
    ):
        return "0"
    return "Non specificato"


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
    r"laurea\s+magistrale|laurea\s+specialistica|master'?s\s+degree|\bmsc\b", re.IGNORECASE
)


def _detect_education_requirement(offer_text: str) -> str:
    """Best-effort read of the required degree from the posting text."""
    if _EDU_PHD.search(offer_text):
        return "PhD"
    if _EDU_MASTERS.search(offer_text):
        return "Magistrale"
    return "Non specificato"


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
