import json
import re
import time
from typing import Any

from app.config import AppSettings
from app.db import Database
from app.lifecycle import apply_post_scan_lifecycle
from app.models import ScanRequest
from app.providers.factory import ProviderManager

try:
    from jobspy import scrape_jobs
except Exception:  # pragma: no cover
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
  "note_azienda": "1 frase"
}}
"""


def _fallback_analysis(reason: str) -> dict[str, Any]:
    return {
        "punteggio": 0,
        "programmazione_richiesta": "?",
        "smart_working": "?",
        "contratto": "?",
        "junior_friendly": "?",
        "anni_esperienza_richiesti": "?",
        "punti_forza_per_diego": "?",
        "punti_deboli_per_diego": "?",
        "riassunto": f"Analisi non disponibile: {reason}",
        "consiglio": "Da verificare manualmente",
        "ral_stimata": "Non stimabile",
        "reputazione_azienda": "?",
        "adatta_neolaureati": "?",
        "note_azienda": "?",
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
            return _fallback_analysis("risposta non valida")
        return result
    except Exception as exc:
        return _fallback_analysis(str(exc))


def run_scan(
    db: Database,
    settings: AppSettings,
    provider_manager: ProviderManager,
    payload: ScanRequest,
):
    if scrape_jobs is None:
        yield {"error": "python-jobspy non installato"}
        return

    profile = db.get_active_candidate_profile()
    profile_markdown = profile["markdown"] if profile else "Profilo non caricato."

    linkedin_url = db.get_preference("linkedin_url", "")
    if linkedin_url:
        profile_markdown += f"\n\nProfilo LinkedIn: {linkedin_url}"

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

    for term in terms:
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
        except Exception:
            continue

        df = df.drop_duplicates(subset=["title", "company"])
        total_rows = len(df)
        totale_trovati += total_rows
        yield {"status": "scraped", "term": term, "found": total_rows}

        for _, row in df.iterrows():
            titolo = str(row.get("title", "N/A"))
            azienda = str(row.get("company", "N/A"))
            descrizione = str(row.get("description", ""))

            skip, reason = pre_filtro(titolo=titolo, descrizione=descrizione)
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

            # Se già chiuso dall'utente non lo rianalizziamo.
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

            # Arricchimento con salary raw se disponibile.
            analysis = dict(analysis)
            analysis["stipendio_min"] = row.get("min_amount") or "N/D"
            analysis["stipendio_max"] = row.get("max_amount") or "N/D"

            # Difesa contro campi fuori formato.
            raw_score = analysis.get("punteggio", 0)
            try:
                analysis["punteggio"] = int(raw_score)
            except Exception:
                numbers = re.findall(r"\d+", str(raw_score))
                analysis["punteggio"] = int(numbers[0]) if numbers else 0

            db.update_job_analysis(job_id=job_id, analysis=analysis)
            totale_analizzati += 1
            yield {"status": "analyzed", "job": {"titolo": titolo, "azienda": azienda, "score": analysis.get("punteggio", 0)}}
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
