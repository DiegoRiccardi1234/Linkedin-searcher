"""Scoring without a model: keyword overlap against the CV.

Used when the AI is unreachable, when it answers unusably, and when the
posting is too thin to judge on merit. An honest capped estimate beats a
confident invented one — and the result is marked as heuristic so the job is
re-scored properly next time (see app.scoring_schema).
"""

from __future__ import annotations

import re
from typing import Any

from app.log import get_logger
from app.scoring_schema import ANALYSIS_SOURCE_KEY, HEURISTIC_SOURCE
from app.services.scan.hard_requirements import FLAG_SHORT_DESCRIPTION, _add_flag
from app.services.scan.vocab import TECH_KEYWORDS, _tokenize

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
    # ``reason`` (provider error / invalid response) is for diagnostics only —
    # it must never leak into the user-facing ``riassunto`` below.
    log.warning("Heuristic fallback analysis for '%s' @ %s: %s", titolo, azienda, reason)
    return _heuristic_analysis(profile_markdown, titolo, azienda, descrizione)


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


def _heuristic_analysis(
    profile_markdown: str,
    titolo: str,
    azienda: str,
    descrizione: str,
) -> dict[str, Any]:
    offer_text = f"{titolo} {descrizione}".lower()
    profile_tokens = _tokenize(profile_markdown)
    offer_tokens = _tokenize(offer_text)
    overlap = sorted(profile_tokens.intersection(offer_tokens))

    score = 3 + min(4, len(overlap) // 3)
    if "junior" in offer_text or "entry level" in offer_text:
        score += 2
    if any(token in offer_text for token in ["remote", "hybrid", "smart working"]):
        score += 1
    if any(token in offer_text for token in ["senior", "lead", "principal", "staff"]):
        score -= 2
    # Advanced-degree requirement: same weight as a senior title (the real case
    # was a Master's-required posting scored 9 for a Bachelor's profile).
    edu_required = _detect_education_requirement(offer_text)
    if edu_required in ("Magistrale", "PhD"):
        score -= 2

    score = max(1, min(score, 10))

    if score >= 8:
        advice = "Candidati subito"
    elif score >= 6:
        advice = "Valutabile"
    else:
        advice = "Salta"

    overlap_preview = ", ".join(overlap[:5]) if overlap else "competenze base IT"
    weakness_text = (
        "Richieste non completamente allineate al profilo"
        if score < 7
        else "Competenze verificabili in colloquio"
    )
    if edu_required == "Magistrale":
        weakness_text = "Richiesta laurea magistrale. " + weakness_text
    elif edu_required == "PhD":
        weakness_text = "Richiesto PhD/dottorato. " + weakness_text

    return {
        # No model ever saw this offer: the score comes from keyword overlap.
        # The marker keeps it out of "already analysed" (see app.scoring_schema)
        # so the job is re-scored properly the next time it shows up.
        ANALYSIS_SOURCE_KEY: HEURISTIC_SOURCE,
        "punteggio": score,
        "programmazione_richiesta": _estimate_programming_demand(offer_text),
        "smart_working": _estimate_smart_working(offer_text),
        "contratto": _estimate_contract_type(offer_text),
        "anni_esperienza_richiesti": _estimate_experience_band(offer_text),
        "titolo_studio_richiesto": edu_required,
        "punti_forza": f"Match su: {overlap_preview}.",
        "punti_deboli": weakness_text,
        "riassunto": f"Analisi euristica usata (IA non disponibile). Match stimato {score}/10.",
        "consiglio": advice,
        "ral_stimata": "Non stimabile",
        "adatta_neolaureati": "Sì"
        if any(token in offer_text for token in ["junior", "stage", "intern", "entry"])
        else "Non specificato",
        "match_axes": {
            "skills_match": max(0, min(10, score + min(2, len(overlap) // 2))),
            "seniority_match": 8
            if any(t in offer_text for t in ["junior", "entry", "stage", "intern"])
            else (3 if any(t in offer_text for t in ["senior", "lead"]) else 6),
            "remote_match": 9
            if any(t in offer_text for t in ["remote", "smart working"])
            else (6 if "hybrid" in offer_text or "ibrid" in offer_text else 4),
            "salary_match": 5,
            "contract_match": 3 if any(t in offer_text for t in ["partita iva", "p.iva"]) else 7,
        },
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
    Score it heuristically from the little text available so it still gets an
    ordering, but flag it honestly and CAP it — an unread job must never
    surface as a top "Candidati subito"/9. Skips the LLM (no point scoring
    blind, and it would hallucinate requirements)."""
    result = _fallback_analysis(
        "insufficient_description",
        profile_markdown=profile_markdown,
        titolo=titolo,
        azienda=azienda,
        descrizione=descrizione,
    )
    result["punteggio"] = min(int(result.get("punteggio", 3) or 3), 6)
    result["consiglio"] = "Valutabile" if result["punteggio"] >= 5 else "Salta"
    _add_flag(result, FLAG_SHORT_DESCRIPTION)
    if descrizione.strip():
        result["riassunto"] = (
            "Descrizione troppo breve — stima dal titolo. Apri l'annuncio per valutare."
        )
        result["punti_deboli"] = (
            "Descrizione quasi assente: requisiti ed esperienza richiesta non verificati."
        )
    else:
        result["riassunto"] = (
            "Descrizione non disponibile — stima dal titolo. Apri l'annuncio per valutare."
        )
        result["punti_deboli"] = (
            "Descrizione non recuperata: requisiti ed esperienza richiesta non verificati."
        )
    return result
