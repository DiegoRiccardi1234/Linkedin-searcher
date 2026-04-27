import json
import random
import re
import time
from typing import Any

# Common technical keywords. If a scrape for one of these returns zero rows,
# it's suspicious — likely a DOM/selector regression on the scraper side
# rather than an actual lack of listings.
_COMMON_CANARY_TERMS = {
    "python",
    "java",
    "javascript",
    "sql",
    "react",
    "data analyst",
    "data engineer",
    "devops",
    "qa tester",
    "cloud engineer",
}


def _is_common_term(term: str) -> bool:
    return term.strip().lower() in _COMMON_CANARY_TERMS


from app.config import AppSettings
from app.db import Database
from app.lifecycle import apply_post_scan_lifecycle
from app.log import get_logger
from app.models import ScanRequest
from app.providers.factory import ProviderManager

log = get_logger(__name__)

try:
    from jobspy import scrape_jobs
except ImportError:  # pragma: no cover
    scrape_jobs = None  # type: ignore[assignment]


BLACKLIST = [
    "senior developer",
    "senior engineer",
    "senior consultant",
    "senior analyst",
    "lead developer",
    "principal engineer",
    "5+ anni",
    "4+ anni",
    "partita iva",
    "p.iva",
    "freelance",
    "cto",
    "ciso",
]

STOPWORDS = {
    "the",
    "and",
    "for",
    "with",
    "from",
    "that",
    "this",
    "have",
    "has",
    "will",
    "your",
    "you",
    "our",
    "all",
    "per",
    "con",
    "dei",
    "delle",
    "della",
    "dell",
    "una",
    "uno",
    "sono",
    "come",
    "sulla",
    "sulle",
    "degli",
    "nella",
    "nelle",
    "into",
    "about",
    "role",
    "lavoro",
    "lavori",
    "offerta",
    "annuncio",
    "candidate",
    "team",
    "company",
}

TECH_KEYWORDS = {
    "python",
    "java",
    "javascript",
    "typescript",
    "react",
    "node",
    "sql",
    "docker",
    "kubernetes",
    "aws",
    "azure",
    "gcp",
    "api",
    "fastapi",
    "django",
    "selenium",
    "playwright",
    "testing",
    "qa",
    "data",
    "analytics",
    "machine",
    "learning",
}


def pre_filtro(titolo: str, descrizione: str) -> tuple[bool, str]:
    testo = (titolo + " " + descrizione).lower()
    for frase in BLACKLIST:
        if frase in testo:
            return True, frase
    return False, ""


def _analysis_prompt(profile_markdown: str, titolo: str, azienda: str, descrizione: str) -> str:
    return f"""Analizza questa offerta IT e rispondi SOLO con JSON valido, senza testo extra.

CV candidato:
{profile_markdown[:3500]}

OFFERTA:
Titolo: {titolo}
Azienda: {azienda}
Descrizione: {descrizione[:1800]}

JSON richiesto:
{{
  "punteggio": <1-10>,
  "programmazione_richiesta": "Bassa|Media|Alta",
  "smart_working": "Sì|No|Non specificato",
  "contratto": "Dipendente|Apprendistato|Stage|Partita IVA|Non specificato",
  "junior_friendly": "Sì|No|Non specificato",
  "anni_esperienza_richiesti": "0|1|2|3+|Non specificato",
  "punti_forza_per_diego": "1 frase",
  "punti_deboli_per_diego": "1 frase",
  "riassunto": "2 righe max",
  "consiglio": "Candidati subito|Valutabile|Salta",
  "ral_stimata": "XX.000€-YY.000€|Non stimabile",
  "reputazione_azienda": "Ottima|Buona|Nella media|Scarsa|Sconosciuta",
  "adatta_neolaureati": "Sì|No|Non specificato",
  "note_azienda": "1 frase",
  "match_axes": {{
    "skills_match": <0-10>,
    "seniority_match": <0-10>,
    "remote_match": <0-10>,
    "salary_match": <0-10>,
    "contract_match": <0-10>
  }}
}}
"""


def _tokenize(text: str) -> set[str]:
    tokens = re.findall(r"[a-zA-Z][a-zA-Z0-9+#\.-]{2,}", text.lower())
    return {token for token in tokens if token not in STOPWORDS}


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

    return {
        "punteggio": score,
        "programmazione_richiesta": _estimate_programming_demand(offer_text),
        "smart_working": _estimate_smart_working(offer_text),
        "contratto": _estimate_contract_type(offer_text),
        "junior_friendly": "Sì"
        if any(token in offer_text for token in ["junior", "entry", "stage", "intern"])
        else "Non specificato",
        "anni_esperienza_richiesti": _estimate_experience_band(offer_text),
        "punti_forza_per_diego": f"Match su: {overlap_preview}.",
        "punti_deboli_per_diego": weakness_text,
        "riassunto": f"Analisi euristica usata ({reason[:80]}). Match stimato {score}/10.",
        "consiglio": advice,
        "ral_stimata": "Non stimabile",
        "reputazione_azienda": "Sconosciuta",
        "adatta_neolaureati": "Sì"
        if any(token in offer_text for token in ["junior", "stage", "intern", "entry"])
        else "Non specificato",
        "note_azienda": f"Valutazione automatica fallback per {azienda}.",
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


def analyze_offer(
    provider_manager: ProviderManager,
    profile_markdown: str,
    titolo: str,
    azienda: str,
    descrizione: str,
) -> dict[str, Any]:
    prompt = _analysis_prompt(profile_markdown, titolo, azienda, descrizione)
    try:
        result = provider_manager.complete_json(prompt=prompt, max_tokens=500)
        if not isinstance(result, dict):
            return _fallback_analysis(
                "invalid response",
                profile_markdown=profile_markdown,
                titolo=titolo,
                azienda=azienda,
                descrizione=descrizione,
            )
        return result
    except Exception as exc:
        return _fallback_analysis(
            str(exc),
            profile_markdown=profile_markdown,
            titolo=titolo,
            azienda=azienda,
            descrizione=descrizione,
        )


def run_scan(
    db: Database,
    settings: AppSettings,
    provider_manager: ProviderManager,
    payload: ScanRequest,
) -> Any:
    """Run a job scan and yield progress events for SSE streaming.

    For each requested search term, scrapes listings via python-jobspy,
    deduplicates, applies the pre-filter blacklist, delegates per-job
    analysis to :func:`analyze_offer`, and persists results. Yields dicts
    describing status transitions (``scraped``, ``analyzed``, ``done``,
    ``error``) that the caller forwards to the client as Server-Sent Events.
    """
    if scrape_jobs is None:
        yield {"error": "python-jobspy not installed"}
        return

    profile = db.get_active_candidate_profile()
    profile_markdown = profile["markdown"] if profile else "Profile not loaded."

    linkedin_url = db.get_preference("linkedin_url", "")
    if linkedin_url:
        profile_markdown += f"\n\nLinkedIn profile: {linkedin_url}"

    terms = payload.search_terms or settings.default_search_terms
    location = payload.location or (
        settings.location_remote_default if payload.is_remote else settings.location_default
    )
    modalita = "Full Remote IT" if payload.is_remote else "Torino"

    db.set_preference("last_scan_location", location)
    db.set_preference("last_scan_is_remote", "1" if payload.is_remote else "0")
    db.set_preference("last_scan_terms", json.dumps(terms, ensure_ascii=False))

    run_id = db.begin_scan(location=location, is_remote=payload.is_remote, terms=terms)

    yield {
        "status": "started",
        "terms": terms,
        "location": location,
        "is_remote": payload.is_remote,
    }

    totale_trovati = 0
    totale_nuovi = 0
    totale_analizzati = 0
    totale_scartati = 0

    for idx, term in enumerate(terms):
        # Paced scraping — reduce the chance of rate-limit bans on LinkedIn / Indeed.
        if idx > 0:
            time.sleep(random.uniform(0.8, 2.4))

        try:
            df = scrape_jobs(
                site_name=payload.sites,
                search_term=term,
                location=location,
                is_remote=payload.is_remote,
                results_wanted=settings.max_annunci,
                hours_old=settings.hours_old,
                country_indeed="Italy",
            )
        except Exception as exc:
            log.warning("scrape_jobs failed (term=%r, location=%r): %s", term, location, exc)
            yield {"status": "scrape_error", "term": term, "error": str(exc)}
            continue

        df = df.drop_duplicates(subset=["title", "company"])
        total_rows = len(df)
        totale_trovati += total_rows

        if total_rows == 0 and _is_common_term(term):
            log.warning(
                "SCRAPER_EMPTY_BUT_EXPECTED: term=%r location=%r returned 0 rows. "
                "Possible DOM/selector regression upstream.",
                term,
                location,
            )
            yield {
                "status": "canary_warning",
                "term": term,
                "message": (
                    "The search returned 0 results for a common keyword. "
                    "The source site may have changed its layout — please report this."
                ),
            }

        yield {"status": "scraped", "term": term, "found": total_rows}

        for _, row in df.iterrows():
            titolo = str(row.get("title", "N/A"))
            azienda = str(row.get("company", "N/A"))
            descrizione = str(row.get("description", ""))

            skip, _reason = pre_filtro(titolo=titolo, descrizione=descrizione)
            if skip:
                totale_scartati += 1
                continue

            payload_job = {
                "titolo": titolo,
                "azienda": azienda,
                "descrizione": descrizione,
                "sede": str(row.get("location", "")),
                "fonte": str(row.get("site", "")),
                "link": str(row.get("job_url", "")),
                "ricerca_usata": term,
                "modalita": modalita,
            }
            job_id, is_new, status = db.upsert_job(payload_job)

            if is_new:
                totale_nuovi += 1

            # Skip re-analysis if the user already closed the job.
            if status in {"applied", "rejected", "archived"}:
                continue

            existing = db.get_job(job_id)
            already_analyzed = bool(existing and existing.get("analysis_json"))
            if already_analyzed and not is_new:
                continue

            analysis = analyze_offer(
                provider_manager=provider_manager,
                profile_markdown=profile_markdown,
                titolo=titolo,
                azienda=azienda,
                descrizione=descrizione,
            )

            # Enrich with raw salary fields when available.
            analysis = dict(analysis)
            analysis["stipendio_min"] = row.get("min_amount") or "N/D"
            analysis["stipendio_max"] = row.get("max_amount") or "N/D"

            # Defensive parse: score field may come back as string or garbage.
            raw_score = analysis.get("punteggio", 0)
            try:
                analysis["punteggio"] = int(raw_score)
            except (TypeError, ValueError):
                numbers = re.findall(r"\d+", str(raw_score))
                analysis["punteggio"] = int(numbers[0]) if numbers else 0

            db.update_job_analysis(job_id=job_id, analysis=analysis)
            totale_analizzati += 1
            yield {
                "status": "analyzed",
                "job": {
                    "titolo": titolo,
                    "azienda": azienda,
                    "score": analysis.get("punteggio", 0),
                },
            }
            time.sleep(settings.delay_tra_chiamate)

        time.sleep(settings.delay_tra_ricerche)

    archiviati = apply_post_scan_lifecycle(db=db, retention_days=settings.retention_days)
    db.finish_scan(
        run_id=run_id,
        totale_trovati=totale_trovati,
        totale_nuovi=totale_nuovi,
        totale_analizzati=totale_analizzati,
        totale_scartati=totale_scartati,
    )

    yield {
        "status": "complete",
        "run_id": run_id,
        "totale_trovati": totale_trovati,
        "totale_nuovi": totale_nuovi,
        "totale_analizzati": totale_analizzati,
        "totale_scartati": totale_scartati,
        "archiviati": archiviati,
    }
