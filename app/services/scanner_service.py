import functools
import json
import math
import random
import re
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime, timedelta
from typing import Any

import pandas as pd

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


# Map UI experience-level codes to keyword augmentation. We append a token
# to the search term so LinkedIn's relevance algorithm narrows results,
# since jobspy doesn't expose LinkedIn's f_E URL filter directly.
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


def _below_min_salary(max_amount: Any, min_salary: int) -> bool:
    """True only when a job's (known) top salary is below ``min_salary``.

    Jobs with no/unparseable salary are kept (return False) — most listings omit
    pay, so filtering them out would hide almost everything.
    """
    if not min_salary:
        return False
    try:
        amount = float(max_amount)
    except (TypeError, ValueError):
        return False
    return amount < min_salary


def _is_nan(val: Any) -> bool:
    return isinstance(val, float) and math.isnan(val)


def _clean_text(val: Any) -> str:
    """Coerce a jobspy cell to a clean string. ``str()`` of a pandas ``NaN`` or
    ``None`` yields the literal ``"nan"``/``"None"`` which then poisons the LLM
    prompt (and made LinkedIn jobs look like they had a description). Those and
    blank strings collapse to ``""``."""
    if val is None or _is_nan(val):
        return ""
    s = str(val).strip()
    return "" if s.lower() in ("nan", "none") else s


# Bilingual markers for the "requirements" section of a job posting. Used to keep
# that section in the scoring prompt even when it sits past the char budget.
_REQ_MARKERS = (
    "requisiti",
    "requirements",
    "cosa cerchiamo",
    "chi cerchiamo",
    "chi sei",
    "profilo ricercato",
    "profilo ideale",
    "your profile",
    "who you are",
    "qualifiche",
    "competenze richieste",
    "what we",
    "what you",
    "must have",
    "esperienza richiesta",
    "we are looking",
)


def _prep_description(desc: str, limit: int, head: int = 800) -> str:
    """Fit a job description into ``limit`` chars for the scoring prompt WITHOUT
    dropping the requirements. A plain ``desc[:limit]`` cuts off the "Requisiti"
    block (which sits after the intro/responsibilities), so a Master/PhD role was
    scored as junior. When the requirements marker falls beyond ``limit`` we keep
    a head slice + the requirements window instead of the head alone."""
    if not desc or len(desc) <= limit:
        return desc
    low = desc.lower()
    pos = min((low.find(m) for m in _REQ_MARKERS if low.find(m) >= 0), default=-1)
    if pos < 0 or pos + 40 <= limit:
        return desc[:limit]  # requirements already inside the window (or none found)
    head_part = desc[:head].rstrip()
    tail = desc[pos : pos + max(0, limit - len(head_part) - 3)]
    return f"{head_part}\n…\n{tail}"


def _norm_remote(val: Any) -> bool | None:
    """Normalize jobspy's ``is_remote`` (True/False/NaN/missing) to bool|None."""
    if val is None or _is_nan(val):
        return None
    return bool(val)


_HYBRID_RE = re.compile(r"\bibrid[ao]|\bhybrid\b|lavoro ibrido", re.IGNORECASE)
_ONSITE_RE = re.compile(
    r"\bin sede\b|\bon[- ]site\b|\bonsite\b|\bin presenza\b|presenza in sede|\bin office\b",
    re.IGNORECASE,
)
_REMOTE_RE = re.compile(
    r"full remote|100% remot|\bda remoto\b|\bfully remote\b|\bremote[- ]first\b|smart working",
    re.IGNORECASE,
)


def _detect_work_mode(row: Any, descrizione: str, scan_default: str) -> str:
    """Work mode of a single posting, read from the posting itself.

    Used to be a scan-level constant mirroring the ``is_remote`` search flag, so
    every job of a remote-flagged scan was stored as "Full Remote" — including
    plainly on-site ones (measured: 44/44 jobs of one scan, an Orbassano plant
    role among them). jobspy's per-row ``is_remote`` comes first, then the text,
    and only an undecidable row falls back to the scan flag.
    """
    text = descrizione or ""
    if _HYBRID_RE.search(text):
        return "Ibrido"
    is_remote = _norm_remote(row.get("is_remote") if hasattr(row, "get") else None)
    if is_remote is True:
        return "Full Remote"
    if _REMOTE_RE.search(text):
        return "Full Remote"
    if is_remote is False or _ONSITE_RE.search(text):
        return "In sede"
    # No evidence either way. The scan flag is a SEARCH filter, not a fact about
    # the posting — asserting "Full Remote" from it is how an on-site plant role
    # ended up labelled remote — so say so instead of guessing.
    return "Non specificato" if scan_default == "Full Remote" else scan_default


def _row_job_type_ok(row: Any, job_types: list[str]) -> bool:
    """Keep a scraped row when its job_type matches a selected one.

    jobspy takes a single ``job_type``, so multi-select is enforced here (the
    kwarg only narrows for a single pick). Rows whose type jobspy didn't report
    are kept — never over-drop on missing data.
    """
    selected = {j.lower() for j in job_types if j.lower() in _JOBSPY_JOB_TYPE}
    if not selected:
        return True
    raw = row.get("job_type")
    if raw is None or _is_nan(raw):
        return True
    types = {t.strip().lower() for t in re.split(r"[,\s]+", str(raw)) if t.strip()}
    return not types or bool(types & selected)


def _row_work_mode_ok(row: Any, work_types: list[str]) -> bool:
    """Best-effort work-mode filter from jobspy's ``is_remote``.

    'hybrid' isn't distinguishable in jobspy output, so any selection including
    it (or both remote+onsite) keeps everything. Unknown is_remote is kept.
    """
    modes = {w.lower() for w in work_types}
    if not modes or "hybrid" in modes or {"remote", "onsite"} <= modes:
        return True
    is_remote = _norm_remote(row.get("is_remote"))
    if is_remote is None:
        return True
    if "remote" in modes:
        return is_remote
    if "onsite" in modes:
        return not is_remote
    return True


def _augment_search_term(term: str, exp_levels: list[str], work_types: list[str]) -> str:
    bits = [term]
    for lvl in exp_levels:
        kw = _EXPERIENCE_KEYWORDS.get(lvl, "")
        if kw and kw not in term.lower():
            bits.append(kw)
    if "hybrid" in work_types and "hybrid" not in term.lower():
        bits.append("hybrid")
    return " ".join(bits).strip()


from app.config import AppSettings
from app.db import Database
from app.lifecycle import apply_post_scan_lifecycle
from app.log import get_logger
from app.models import ScanRequest
from app.providers.factory import ProviderManager
from app.providers.model_selector import SCORING_MIN_SIZE_B
from app.scoring_schema import (
    ANALYSIS_SOURCE_KEY,
    ANALYSIS_VERSION_KEY,
    CURRENT_ANALYSIS_VERSION,
    HEURISTIC_SOURCE,
)
from app.services.onboarding import RAL_MIN_LABEL, onboarding_context, onboarding_ral
from app.services.pii import redact_pii
from app.services.recruiter_scrape import fetch_linkedin_description, fetch_recruiter

log = get_logger(__name__)

# Speed-biased model policy for scan scoring. Scoring a job (0-10 + JSON) barely
# needs model quality but is high-volume, so we bias hard toward fast/small
# models (flash/turbo/instant, size-penalised) and drop the global preferred_model
# pin. Passed as ``policy_override`` so it re-ranks the provider's LIVE catalog —
# no hardcoded model id, so it survives OpenRouter's changing catalog. Chat and
# letter generation keep the quality default (they don't pass this).
_SCORING_POLICY: dict[str, Any] = {
    # "Fastest among CAPABLE models." A pure speed bias picked 1-20B toys that
    # scored job↔CV matches badly (a Product Owner at 8/10, empty analyses), so
    # we keep a fast lean but with a quality floor: de-rank models under
    # SCORING_MIN_SIZE_B, and reward capability again. The floor is 26 (not 40)
    # so clean mid-size models like gemma-4-26b stay eligible — the 40B floor was
    # de-ranking the models that emit JSON reliably and favouring 120-550B
    # reasoning giants that truncate it. ``reasoning`` weight is 0: for compact
    # JSON scoring a hidden chain-of-thought is a liability, not a plus (and the
    # runtime "truncated" penalty routes around whichever models actually cut off).
    "prefer_fast": True,
    "prefer_quality": True,
    "prefer_free": True,
    "max_cost_tier": "high",
    "min_size_b": SCORING_MIN_SIZE_B,
    # Hard gate on top of the soft floor above: under a 429 storm every decent
    # model ends up penalized and the de-ranked toy wins by default. Measured on
    # the 2026-07-21 scan: a 12B VL model wrote two of the top scores. With
    # hard_floor the unfit models are EXCLUDED, the provider is skipped when none
    # survives, and the offer falls back to the declared heuristic analysis.
    "hard_floor": True,
    "weights": {
        "size": 16,
        "speed": 12,
        "family": 20,
        "instruct": 25,
        "chat": 10,
        "json": 12,
        "reasoning": 0,
        "small_penalty": -150,
    },
}

# Cap on locations per scan: a scan is terms x locations x ~20 jobs, so this
# bounds the volume (and the free-tier LLM scoring time) for a multi-location run.
_MAX_SCAN_LOCATIONS = 8

try:
    from jobspy import scrape_jobs
except ImportError:  # pragma: no cover
    scrape_jobs = None

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


def _scrape_split_indeed(scrape_kwargs: dict[str, Any]) -> Any:
    """Scrape, splitting Indeed away from ``hours_old``.

    jobspy's Indeed filter builder is an if/elif: with ``hours_old`` set the
    ``is_remote``/``job_type`` filters are silently IGNORED, and the date
    filter alone collapses IT results in smaller markets (measured live from
    Italy: 4 rows vs 20 for the same query). So Indeed is scraped WITHOUT
    ``hours_old`` — letting remote/job-type apply server-side again — and
    freshness is enforced locally via :func:`_filter_indeed_freshness`.
    One site failing must not lose the other's rows; if every call fails the
    last error propagates (same contract as a single scrape_jobs call).
    """
    sites = list(scrape_kwargs.get("site_name") or [])
    hours_old = scrape_kwargs.get("hours_old")
    if "indeed" not in sites or not hours_old:
        return scrape_jobs(**scrape_kwargs)

    calls = []
    indeed_kwargs = dict(scrape_kwargs, site_name=["indeed"])
    indeed_kwargs.pop("hours_old", None)
    calls.append(indeed_kwargs)
    others = [s for s in sites if s != "indeed"]
    if others:
        calls.append(dict(scrape_kwargs, site_name=others))

    frames = []
    last_exc: Exception | None = None
    for kwargs in calls:
        try:
            frames.append(scrape_jobs(**kwargs))
        except Exception as exc:
            last_exc = exc
            log.warning("scrape_jobs failed for %s: %s", kwargs.get("site_name"), exc)
    if not frames:
        assert last_exc is not None
        raise last_exc
    df = pd.concat(frames, ignore_index=True) if len(frames) > 1 else frames[0]
    return _filter_indeed_freshness(df, int(hours_old))


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

# Domain vocabulary (bilingual it+en) for the relevance gate: a scraped job whose
# title+description shares NONE of these — nor any of the candidate's own skill
# tokens — is off-topic (kitchen/spa/food-QC/pharma) and dropped before wasting an
# LLM scoring call. Deliberately excludes generic role words (analyst/engineer/
# quality) so it keys on the actual tech/AI/data domain, not the fuzzy match.
_DOMAIN_VOCAB = {
    # AI / data / ML
    "ai",
    "ml",
    "nlp",
    "llm",
    "genai",
    "data",
    "dati",
    "dataset",
    "analytics",
    "analisi",
    "machine",
    "learning",
    "apprendimento",
    "deep",
    "neural",
    "rete",
    "reti",
    "model",
    "modelli",
    "modello",
    "algorithm",
    "algoritmo",
    "algoritmi",
    "intelligenza",
    "artificiale",
    "annotation",
    "annotazione",
    "labeling",
    "etichettatura",
    "linguistic",
    "linguistica",
    "linguistico",
    "computational",
    "computazionale",
    "prompt",
    "embedding",
    # software / dev
    "software",
    "sviluppo",
    "sviluppatore",
    "developer",
    "development",
    "programmazione",
    "programming",
    "coding",
    "informatica",
    "informatico",
    "backend",
    "frontend",
    "fullstack",
    "api",
    "database",
    "cloud",
    "devops",
    "python",
    "java",
    "javascript",
    "typescript",
    "react",
    "node",
    "sql",
    "docker",
}


def pre_filtro(titolo: str, descrizione: str) -> tuple[bool, str]:
    testo = (titolo + " " + descrizione).lower()
    for frase in BLACKLIST:
        if frase in testo:
            return True, frase
    return False, ""


# Per-offer analysis schema, shared by the single-offer prompt and the batch
# prompt so both stay identical (job_detail.js depends on this exact shape:
# radar match_axes, skills_match, requisiti…). Plain string with literal braces
# — inserted by concatenation, so no f-string escaping.
#
# Field order is deliberate: the EVIDENCE comes first and the verdict last.
# ``punteggio`` used to be the first key, so the model committed to a number
# before it had written a single requirement or skill — the worst possible
# ordering for a small model, which cannot revise what it already emitted.
#
# Fields the app computes deterministically are NOT asked for (the model's
# answer was overwritten anyway): ``voto_minimo_richiesto`` and
# ``eleggibilita_geografica`` come from the hard-requirement checks. Fields
# nothing ever read were dropped outright (``junior_friendly``, duplicate of
# ``adatta_neolaureati``; ``note_azienda``; ``reputazione_azienda``, which the
# model had no way of knowing and invented). 24 keys -> 19.
_PER_OFFER_SCHEMA = """{
  "requisiti": ["max 5 requisiti chiave dell'offerta, brevi"],
  "responsabilita": ["max 5 responsabilità principali, brevi"],
  "benefit": ["max 5 benefit menzionati, brevi"],
  "titolo_studio_richiesto": "Nessuno|Diploma|Triennale|Magistrale|PhD|Non specificato",
  "anni_esperienza_richiesti": "0|1|2|3+|Non specificato",
  "livello_richiesto": "internship|entry|junior|mid|senior|lead",
  "contratto": "Dipendente|Apprendistato|Stage|Partita IVA|Non specificato",
  "tipo_ingaggio": "Dipendente|Gig a task|Freelance P.IVA|Stage|Non specificato",
  "smart_working": "Sì|No|Non specificato",
  "programmazione_richiesta": "Bassa|Media|Alta",
  "adatta_neolaureati": "Sì|No|Non specificato",
  "ral_stimata": "XX.000€-YY.000€|Non stimabile",
  "skills_match": {
    "hai": ["skills che il candidato ha e l'offerta richiede"],
    "mancano": ["skills richieste che il candidato non ha"]
  },
  "match_axes": {
    "skills_match": <0-10>,
    "seniority_match": <0-10>,
    "remote_match": <0-10>,
    "salary_match": <0-10>,
    "contract_match": <0-10>
  },
  "punti_forza": "1 frase",
  "punti_deboli": "1 frase",
  "riassunto": "2 righe max",
  "punteggio": <1-10>,
  "consiglio": "Candidati subito|Valutabile|Salta"
}"""


# Shared scoring rules appended to both prompts. The education rule exists
# because a posting requiring a Master's scored 9 for a Bachelor's CV with no
# visible gap: the model must compare hard requirements against the CV and
# make any mismatch VISIBLE (lower score + listed in "mancano").
_SCORING_RULES = (
    "REGOLE DI VALUTAZIONE:\n"
    "- Compila i campi NELL'ORDINE dello schema: prima le prove (requisiti, "
    'skills_match, match_axes), poi "punteggio" e "consiglio". Il voto deve '
    "seguire quello che hai scritto, non precederlo.\n"
    "- Scala del punteggio (usala alla lettera, non a sensazione):\n"
    "  9-10 = requisiti soddisfatti, nessun blocco, ruolo in linea con l'obiettivo;\n"
    "  7-8  = buon match, manca qualche dettaglio o UNA skill recuperabile;\n"
    "  5-6  = match parziale: mancano requisiti importanti o la seniority non torna;\n"
    "  3-4  = requisiti chiave assenti, oppure ruolo lontano dall'obiettivo;\n"
    "  1-2  = non candidabile o del tutto fuori target.\n"
    '- "consiglio" segue il punteggio: >=8 "Candidati subito", 6-7 "Valutabile", '
    '<=5 "Salta".\n'
    "- Confronta i REQUISITI dell'offerta con il CV: titolo di studio, voto minimo, "
    "anni di esperienza, livello di lingua.\n"
    "- Se l'offerta richiede un titolo di studio superiore a quello del candidato "
    "(es. laurea magistrale o PhD quando il CV ha una triennale), un voto minimo più alto "
    "del suo, più anni di esperienza, o un livello di lingua superiore: ABBASSA "
    '"punteggio" e "match_axes.seniority_match" e aggiungi il requisito mancante in '
    '"skills_match.mancano". Il gap deve essere sempre visibile, mai ignorato.\n'
    "- Il candidato NON può trasferirsi e non ha visti extra-UE: se la sede è fuori "
    "dall'Unione Europea e l'annuncio non dichiara esplicitamente apertura a chi lavora "
    'da remoto dall\'UE, "punteggio" massimo 3 e "consiglio" = "Salta".\n'
    "- Valuta ogni offerta INDIPENDENTEMENTE dalle altre: due offerte diverse non "
    'possono avere gli stessi valori di "match_axes".\n'
    "- Stipendio: usa la RAL minima/target del candidato (se indicata nelle preferenze) "
    'come metro per "match_axes.salary_match". Se l\'annuncio NON dichiara una retribuzione, '
    'scrivi "ral_stimata": "Non stimabile" e NON inventare una cifra.\n'
    '- "tipo_ingaggio": distingui un\'assunzione da un lavoro a task/piattaforma '
    "(pagamento a task o a ora, nessun monte ore garantito) e dalla partita IVA.\n"
)


def _analysis_prompt(
    profile_markdown: str,
    titolo: str,
    azienda: str,
    descrizione: str,
    extra_context: str = "",
) -> str:
    extra = f"\nPREFERENZE CANDIDATO:\n{extra_context}\n" if extra_context.strip() else ""
    return (
        "Analizza questa offerta IT e rispondi SOLO con JSON valido, senza testo extra.\n\n"
        f"CV candidato:\n{profile_markdown[:3500]}\n{extra}\n"
        f"OFFERTA:\nTitolo: {titolo}\nAzienda: {azienda}\n"
        f"Descrizione: {_prep_description(descrizione, 2600)}\n\n"
        + _SCORING_RULES
        + "\nJSON richiesto:\n"
        + _PER_OFFER_SCHEMA
        + "\n"
    )


def _batch_analysis_prompt(
    profile_markdown: str,
    offers: list[dict[str, Any]],
    extra_context: str = "",
) -> str:
    """Prompt for scoring N offers for the SAME candidate in one LLM call.

    The CV is sent once; offers are numbered 1..N; the model must return a JSON
    object ``{"valutazioni": [ ...N objects... ]}`` in the same order, each with
    the per-offer schema. Wrapped in an object (not a bare array) so the shared
    ``complete_json`` extractor returns a dict as everywhere else.
    """
    extra = f"\nPREFERENZE CANDIDATO:\n{extra_context}\n" if extra_context.strip() else ""
    n = len(offers)
    blocks = [
        f"--- OFFERTA {i} ---\n"
        f"Titolo: {off['titolo']}\nAzienda: {off['azienda']}\n"
        f"Descrizione: {_prep_description(str(off['descrizione']), 2200)}"
        for i, off in enumerate(offers, 1)
    ]
    offers_text = "\n\n".join(blocks)
    return (
        f"Analizza le {n} offerte IT qui sotto per lo STESSO candidato e rispondi "
        "SOLO con JSON valido, senza testo extra.\n\n"
        f"CV candidato:\n{profile_markdown[:3500]}\n{extra}\n"
        f"OFFERTE ({n}):\n{offers_text}\n\n"
        + _SCORING_RULES
        + f'\nRispondi con un oggetto JSON con una sola chiave "valutazioni" = array di '
        f"ESATTAMENTE {n} oggetti, UNO per offerta nello STESSO ordine "
        "(OFFERTA 1 -> primo elemento). Ogni oggetto ha questo schema:\n" + _PER_OFFER_SCHEMA + "\n"
    )


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


def hard_block_reason(profile_markdown: str, descrizione: str, sede: str) -> str | None:
    """Why this offer is a non-starter for the candidate, or None.

    Both blockers are decidable from the text alone, so the caller can skip the
    LLM entirely instead of paying a call and capping the answer afterwards
    (measured on a real scan: 12 offers out of 44 — ~27% of the scoring quota).
    """
    return _geo_status(sede, descrizione)[1] or _grade_status(profile_markdown, descrizione)[1]


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

#: Flags that mean "you cannot take this job", as opposed to "read carefully".
BLOCKING_FLAGS = frozenset({FLAG_GEO_BLOCKED, FLAG_GRADE_BLOCKED})


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
    if str(out.get(ANALYSIS_SOURCE_KEY, "")) == HEURISTIC_SOURCE:
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
) -> dict[str, Any]:
    """Normalise the schema, then apply the deterministic checks.

    Single post-processing point for EVERY scoring path — single offer, batch
    slot and heuristic fallback — so an offer can never be recommended over a
    hard blocker just because a given path skipped the check. Caps (geo, grade)
    can only lower a score; the salary and engagement checks only annotate, and
    every check records a flag code so the UI can say WHY (see ``_add_flag``).
    """
    out = _normalize_analysis(analysis)
    _apply_grade_requirement(out, profile_markdown, descrizione)
    _apply_geo_eligibility(out, sede, descrizione)
    _apply_salary_expectation(out, _ral_min_from_context(extra_context))
    engagement = _detect_engagement(azienda, f"{descrizione} {out.get('contratto', '')}")
    if engagement:
        out["tipo_ingaggio"] = engagement
    if str(out.get("tipo_ingaggio", "")) in ("Gig a task", "Freelance P.IVA"):
        _add_flag(out, FLAG_GIG)
    return out


def _scoring_max_tokens(n_offers: int) -> int:
    """Completion budget for a scoring call — single offer or batch.

    Fixed headroom + per-offer output. The auto-selected model can be a
    REASONING build that spends completion tokens thinking BEFORE emitting the
    JSON, so a tight budget is eaten by the reasoning alone and the completion
    comes back empty or cut off (verified live: 3 offers returned a full valid
    array at ~3k tokens, empty at ~1k).

    The single-offer path passed a flat ``max_tokens=200`` from v1.7.0 to
    v1.7.6, which cannot fit the schema under ANY model: every single-offer call
    truncated (``finish_reason="length"``), failed over through every candidate,
    penalised each of them, and landed on the keyword heuristic — while the job
    was still saved as "analysed". That path is also the batch's own fallback
    for missing/cloned slots, so a degraded batch degraded further.
    """
    return 500 * max(1, n_offers) + 1600


def _scoring_call_kwargs(provider_manager: ProviderManager) -> dict[str, Any]:
    """Provider/model kwargs for a scoring call: pin the user-chosen scoring
    model if set (no failover), else auto-select via the speed-biased policy.
    getattr-guarded so test stubs without ``.settings`` still work.
    """
    settings = getattr(provider_manager, "settings", None)
    scoring_model = getattr(settings, "scoring_model", None)
    if scoring_model:
        order = getattr(settings, "llm_provider_order", None) or []
        return {"provider_name": order[0] if order else None, "model_name": scoring_model}
    return {"policy_override": _SCORING_POLICY}


def analyze_offer(
    provider_manager: ProviderManager,
    profile_markdown: str,
    titolo: str,
    azienda: str,
    descrizione: str,
    *,
    privacy: bool = False,
    extra_context: str = "",
    candidate_name: str | None = None,
    sede: str = "",
) -> dict[str, Any]:
    """Score one offer, then apply the deterministic hard-requirement checks."""
    blocked = hard_block_reason(profile_markdown, descrizione, sede)
    raw = (
        _blocked_analysis(profile_markdown, titolo, azienda, descrizione, blocked)
        if blocked
        else _analyze_offer_raw(
            provider_manager,
            profile_markdown,
            titolo,
            azienda,
            descrizione,
            privacy=privacy,
            extra_context=extra_context,
            candidate_name=candidate_name,
        )
    )
    return enforce_hard_requirements(
        raw,
        profile_markdown=profile_markdown,
        descrizione=descrizione,
        sede=sede,
        azienda=azienda,
        extra_context=extra_context,
    )


def _blocked_analysis(
    profile_markdown: str, titolo: str, azienda: str, descrizione: str, reason: str
) -> dict[str, Any]:
    """Local analysis for an offer the candidate cannot take.

    No LLM call: the blocker (non-EU location, degree grade below the stated
    threshold) is decidable from the text, and the answer would be capped to 3
    anyway. ``enforce_hard_requirements`` still runs afterwards and re-applies
    the cap, so this only has to be honest about WHY.
    """
    log.info("HARD_BLOCK_SKIP: '%s' @ %s (%s)", titolo, azienda, reason)
    result = _heuristic_analysis(profile_markdown, titolo, azienda, descrizione)
    result["riassunto"] = f"Non candidabile: {reason}. Analisi locale, nessuna chiamata IA."
    return result


def _analyze_offer_raw(
    provider_manager: ProviderManager,
    profile_markdown: str,
    titolo: str,
    azienda: str,
    descrizione: str,
    *,
    privacy: bool = False,
    extra_context: str = "",
    candidate_name: str | None = None,
) -> dict[str, Any]:
    # Missing or too-short description (LinkedIn blocked the retry, or served a
    # marketing blurb) -> don't LLM-score it blind; honest capped estimate.
    if len(descrizione.strip()) < MIN_DESCRIPTION_CHARS:
        return _insufficient_description_analysis(profile_markdown, titolo, azienda, descrizione)
    # Privacy Mode: scrub the CV before it reaches the LLM. Scoring never needs
    # the name/contacts, so no restore — the token map is discarded. The local
    # keyword fallback below keeps the ORIGINAL markdown for a better match.
    prompt_markdown = profile_markdown
    if privacy:
        prompt_markdown, _ = redact_pii(profile_markdown, candidate_name)
    prompt = _analysis_prompt(prompt_markdown, titolo, azienda, descrizione, extra_context)
    try:
        result = provider_manager.complete_json(
            prompt=prompt,
            max_tokens=_scoring_max_tokens(1),
            **_scoring_call_kwargs(provider_manager),
        )
        # A non-dict, empty dict, or dict without a score is NOT an analysis:
        # persisting it would set analyzed_at with punteggio=0 and the job
        # would never be re-scored (job_has_analysis). Same key check as the
        # batch path.
        if not isinstance(result, dict) or "punteggio" not in result:
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


def _cloned_slots(parsed: list[Any]) -> set[int]:
    """Indexes of batch slots that share their ``match_axes`` with another slot.

    A batched model that runs out of attention copy-pastes one verdict across the
    remaining slots: on the 2026-07-21 scan three different jobs came back with
    axes identical digit for digit (two of them scored 10). Identical axes across
    distinct postings are a tell, not a coincidence, so those slots are dropped
    and re-scored one by one. Slots without axes are left to the normal checks.
    """
    seen: dict[str, list[int]] = {}
    for i, item in enumerate(parsed):
        if not isinstance(item, dict):
            continue
        axes = item.get("match_axes")
        if not isinstance(axes, dict) or not axes:
            continue
        seen.setdefault(json.dumps(axes, sort_keys=True), []).append(i)
    cloned = {i for slots in seen.values() if len(slots) > 1 for i in slots}
    if cloned:
        log.warning("BATCH_CLONE: %d slots share match_axes; re-scoring them singly", len(cloned))
    return cloned


def analyze_offers_batch(
    provider_manager: ProviderManager,
    profile_markdown: str,
    offers: list[dict[str, Any]],
    *,
    privacy: bool = False,
    extra_context: str = "",
    candidate_name: str | None = None,
) -> list[dict[str, Any]]:
    """Score N offers in one LLM call, returning exactly ``len(offers)`` analyses
    in order. A batch that fails, returns non-JSON, or yields too few / invalid
    elements degrades gracefully: each missing slot is filled by a per-offer
    :func:`analyze_offer` (which itself falls back to a heuristic). Never raises.
    """
    if not offers:
        return []

    prompt_markdown = profile_markdown
    if privacy:
        prompt_markdown, _ = redact_pii(profile_markdown, candidate_name)

    # Offers with a hard blocker (non-EU location, degree grade under the stated
    # threshold) are answered locally and kept OUT of the prompt: they wouldn't
    # just waste the reply slot, they'd also cost input tokens for a verdict
    # already known. ``scorable`` keeps the mapping back to the original index.
    blocked: dict[int, str] = {}
    scorable: list[tuple[int, dict[str, Any]]] = []
    for i, off in enumerate(offers):
        reason = hard_block_reason(
            profile_markdown, str(off.get("descrizione", "") or ""), str(off.get("sede", "") or "")
        )
        if reason:
            blocked[i] = reason
        else:
            scorable.append((i, off))

    parsed: list[Any] = []
    if scorable:
        try:
            prompt = _batch_analysis_prompt(
                prompt_markdown, [o for _, o in scorable], extra_context
            )
            result = provider_manager.complete_json(
                prompt=prompt,
                max_tokens=_scoring_max_tokens(len(scorable)),
                **_scoring_call_kwargs(provider_manager),
            )
            if isinstance(result, dict):
                raw = (
                    result.get("valutazioni") or result.get("evaluations") or result.get("results")
                )
                if isinstance(raw, list):
                    parsed = raw
        except Exception as exc:
            log.warning(
                "Batch scoring failed (n=%d): %s; falling back per-offer", len(scorable), exc
            )

    # ``parsed`` follows ``scorable`` order, not the caller's: remap both the
    # replies and the clone verdicts back onto the original offer indexes.
    cloned_positions = _cloned_slots(parsed)
    by_index: dict[int, Any] = {}
    cloned: set[int] = set()
    for position, (original_index, _off) in enumerate(scorable):
        if position < len(parsed):
            by_index[original_index] = parsed[position]
        if position in cloned_positions:
            cloned.add(original_index)

    out: list[dict[str, Any]] = []
    for i, off in enumerate(offers):
        sede_i = str(off.get("sede", "") or "")
        desc_i = str(off.get("descrizione", "") or "")
        finalize = functools.partial(
            enforce_hard_requirements,
            profile_markdown=profile_markdown,
            descrizione=desc_i,
            sede=sede_i,
            azienda=str(off.get("azienda", "") or ""),
            extra_context=extra_context,
        )
        if i in blocked:
            out.append(
                finalize(
                    _blocked_analysis(
                        profile_markdown, off["titolo"], off["azienda"], desc_i, blocked[i]
                    )
                )
            )
            continue
        # A description-less (or near-empty) offer can't be judged on merit —
        # override whatever the batch guessed with the honest capped estimate
        # (never a blind 9). Same threshold as the single path.
        if len(desc_i.strip()) < MIN_DESCRIPTION_CHARS:
            out.append(
                finalize(
                    _insufficient_description_analysis(
                        profile_markdown, off["titolo"], off["azienda"], desc_i
                    )
                )
            )
            continue
        item = by_index.get(i)
        if isinstance(item, dict) and "punteggio" in item and i not in cloned:
            out.append(finalize(item))
        else:
            # Missing / malformed / cloned slot: single-offer scoring for this one.
            out.append(
                analyze_offer(
                    provider_manager=provider_manager,
                    profile_markdown=profile_markdown,
                    titolo=off["titolo"],
                    azienda=off["azienda"],
                    descrizione=off["descrizione"],
                    privacy=privacy,
                    extra_context=extra_context,
                    candidate_name=candidate_name,
                    sede=sede_i,
                )
            )
    return out


def run_scan(
    db: Database,
    settings: AppSettings,
    provider_manager: ProviderManager,
    payload: ScanRequest,
    cancel_check: Callable[[], bool] | None = None,
) -> Any:
    """Run a job scan and yield progress events for SSE streaming.

    For each requested search term, scrapes listings via python-jobspy,
    deduplicates, applies the pre-filter blacklist, scores the surviving jobs
    concurrently via :func:`analyze_offer` (bounded by ``scan_concurrency``),
    and persists results. Yields dicts describing status transitions
    (``scraped``, ``analyzed``, ``cancelled``, ``complete``, ``error``) that the
    caller forwards to the client as Server-Sent Events. ``cancel_check`` — when
    it returns True the run stops promptly (user hit stop / tab closed).
    """
    cancelled = cancel_check or (lambda: False)
    if scrape_jobs is None:
        yield {"error": "python-jobspy not installed"}
        return

    # Structural penalties are sticky within a scan (long cooldown) but reset
    # between scans, so a model that recovered gets another chance next run.
    for _reason in ("truncated", "malformed", "timeout"):
        provider_manager.clear_model_penalties(_reason)

    profile = db.get_active_candidate_profile()
    profile_markdown = profile["markdown"] if profile else "Profile not loaded."
    # Relevance vocabulary = domain base + the candidate's own skill tokens. A
    # scraped job sharing none of it (kitchen/spa/food-QC…) is dropped before it
    # wastes an LLM scoring call. Conservative: zero-overlap only.
    _summary = profile.get("summary_json") if profile else None
    _skills = _summary.get("skills") if isinstance(_summary, dict) else None
    relevance_vocab = _DOMAIN_VOCAB | (
        _tokenize(" ".join(str(s) for s in _skills)) if isinstance(_skills, list) else set()
    )

    linkedin_url = db.get_preference("linkedin_url", "")
    if linkedin_url:
        profile_markdown += f"\n\nLinkedIn profile: {linkedin_url}"

    # Privacy Mode + onboarding preferences, resolved once for every job in this
    # scan. feature_privacy_mode mirrors container.feature_enabled semantics.
    privacy = db.get_preference("feature_privacy_mode", "1") not in ("0", "false", "off", "")
    onboarding = onboarding_context(db)
    # Read straight from the preferences instead of re-parsing the rendered
    # label: the worker threads need the figure, not the prompt text.
    ral_min, _ral_target = onboarding_ral(db)
    candidate_name = profile.get("name") if profile else None
    log.info(
        "Scan scoring model: %s",
        settings.scoring_model
        or f"auto → {provider_manager.preview_scoring_model(_SCORING_POLICY)}",
    )

    terms = payload.search_terms or settings.default_search_terms
    exp_levels = list(payload.experience_levels or [])
    job_types = list(payload.job_types or [])
    work_types = list(payload.work_types or [])
    min_salary = int(payload.min_salary or 0)

    is_remote_effective = payload.is_remote or ("remote" in work_types)

    # Multi-location: scrape each location. Fall back to the single location (or
    # the settings default) for backward compat / saved searches. Capped to keep
    # a scan bounded (terms x locations x ~20 jobs each).
    default_location = (
        settings.location_remote_default if is_remote_effective else settings.location_default
    )
    locations_list = [loc.strip() for loc in (payload.locations or []) if loc and loc.strip()]
    if not locations_list:
        locations_list = [payload.location.strip()] if payload.location else [default_location]
    if len(locations_list) > _MAX_SCAN_LOCATIONS:
        locations_list = locations_list[:_MAX_SCAN_LOCATIONS]
    country = (payload.country or settings.country_default or "italy").strip()
    primary_location = locations_list[0]
    modalita = "Full Remote" if is_remote_effective else "In sede"

    augmented_terms = [_augment_search_term(t, exp_levels, work_types) for t in terms]

    jobspy_job_type = _resolve_jobspy_job_type(job_types)

    db.set_preference("last_scan_location", primary_location)
    db.set_preference("last_scan_locations", json.dumps(locations_list, ensure_ascii=False))
    db.set_preference("last_scan_country", country)
    db.set_preference("last_scan_is_remote", "1" if is_remote_effective else "0")
    db.set_preference("last_scan_terms", json.dumps(terms, ensure_ascii=False))
    db.set_preference(
        "last_scan_filters",
        json.dumps(
            {"experience_levels": exp_levels, "job_types": job_types, "work_types": work_types},
            ensure_ascii=False,
        ),
    )

    run_id = db.begin_scan(location=primary_location, is_remote=is_remote_effective, terms=terms)

    started_at_ms = int(time.time() * 1000)
    total_batches = max(1, len(terms) * len(locations_list))
    expected_total = max(1, total_batches * max(1, settings.max_annunci))

    yield {
        "status": "started",
        "terms": terms,
        "location": primary_location,
        "locations": locations_list,
        "country": country,
        "is_remote": is_remote_effective,
        "filters": {
            "experience_levels": exp_levels,
            "job_types": job_types,
            "work_types": work_types,
        },
        "expected_total": expected_total,
    }

    totale_trovati = 0
    totale_nuovi = 0
    totale_analizzati = 0
    totale_scartati = 0
    new_flags_cleared = False

    def _finalize_scored(item: dict[str, Any], analysis: dict[str, Any]) -> dict[str, Any]:
        """Attach salary, coerce the score to int, best-effort recruiter fetch.
        Shared by the single- and batch-scoring paths. Runs on a worker thread;
        ALL DB access (reads included) happens back on the generator thread —
        the recruiter presence check is precomputed in Pass 1 (has_recruiter)."""
        row = item["row"]
        analysis = dict(analysis)
        analysis["stipendio_min"] = row.get("min_amount") or "N/D"
        analysis["stipendio_max"] = row.get("max_amount") or "N/D"
        # The posting's own pay arrives only here — AFTER the model's answer went
        # through enforce_hard_requirements, which is why the salary axis could
        # only ever see the model's guess. Compare the real figures against the
        # user's declared floor now; leave the axis null when either side is
        # unknown (it used to be re-enabled with a flat, invented 5).
        interval = str(row.get("interval") or "")
        low = _annual_amount(row.get("min_amount"), interval)
        high = _annual_amount(row.get("max_amount"), interval)
        axes = analysis.get("match_axes")
        if isinstance(axes, dict) and axes.get("salary_match") is None:
            axis = _salary_axis(low, high, ral_min)
            if axis is not None:
                axes["salary_match"] = axis
        if ral_min and high and high < ral_min:
            detail = f"RAL dichiarata fino a {int(high)} EUR, sotto la tua minima ({ral_min})"
            _add_flag(analysis, FLAG_SALARY_BELOW, detail)
            _add_missing(analysis, detail)
        raw_score = analysis.get("punteggio", 0)
        try:
            analysis["punteggio"] = int(raw_score)
        except (TypeError, ValueError):
            numbers = re.findall(r"\d+", str(raw_score))
            analysis["punteggio"] = int(numbers[0]) if numbers else 0

        recruiter = None
        link = item["link"]
        if link and "linkedin.com" in link and not item.get("has_recruiter"):
            try:
                recruiter = fetch_recruiter(link, timeout=3.0)
            except Exception as exc:
                log.debug("recruiter scrape skipped for job %s: %s", item["job_id"], exc)
        return {
            "job_id": item["job_id"],
            "titolo": item["titolo"],
            "azienda": item["azienda"],
            "analysis": analysis,
            "recruiter": recruiter,
        }

    def _score_job(item: dict[str, Any]) -> dict[str, Any]:
        """Score one offer (one LLM call) + finalize."""
        analysis = analyze_offer(
            provider_manager=provider_manager,
            profile_markdown=profile_markdown,
            titolo=item["titolo"],
            azienda=item["azienda"],
            descrizione=item["descrizione"],
            privacy=privacy,
            extra_context=onboarding,
            candidate_name=candidate_name,
            sede=item.get("sede", ""),
        )
        return _finalize_scored(item, analysis)

    def _score_batch(chunk: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Score a chunk of offers in one LLM call (per-offer fallback inside
        analyze_offers_batch) + finalize each. Returns one result per offer."""
        analyses = analyze_offers_batch(
            provider_manager=provider_manager,
            profile_markdown=profile_markdown,
            offers=chunk,
            privacy=privacy,
            extra_context=onboarding,
            candidate_name=candidate_name,
        )
        return [
            _finalize_scored(item, analysis) for item, analysis in zip(chunk, analyses, strict=True)
        ]

    def _score_unit(unit: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Score a work unit: >1 offer → one batched call; a lone offer → single
        call (avoids wasting a batch prompt on the last odd job)."""
        if len(unit) > 1:
            return _score_batch(unit)
        return [_score_job(unit[0])]

    # Flat (location, term) pairs so the existing single-loop body stays intact;
    # batch_no drives global progress across the whole location x term grid.
    scan_pairs = [(loc, ti, term) for loc in locations_list for ti, term in enumerate(terms)]
    for batch_no, (location, idx, term) in enumerate(scan_pairs):
        if cancelled():
            break
        if batch_no > 0:
            time.sleep(random.uniform(0.8, 2.4))

        effective_term = augmented_terms[idx] if idx < len(augmented_terms) else term

        elapsed_ms = int(time.time() * 1000) - started_at_ms
        seen = batch_no * max(1, settings.max_annunci)
        eta_ms = int((elapsed_ms / max(1, seen)) * (expected_total - seen)) if seen > 0 else 0
        yield {
            "status": "progress",
            "step": "scraping",
            "term": effective_term,
            "current": seen,
            "total": expected_total,
            "percent": int(seen * 100 / expected_total) if expected_total else 0,
            "elapsed_ms": elapsed_ms,
            "eta_ms": eta_ms,
        }

        # Indeed is per-country: resolve the country FROM the location, and drop
        # Indeed entirely for locations no single domain can serve.
        sites = list(payload.sites)
        indeed_country = _indeed_country_for(location, country)
        if "indeed" in sites and indeed_country is None:
            sites = [s for s in sites if s != "indeed"]
            log.info("Indeed skipped for location=%r (no single country domain)", location)
            if not sites:
                yield {
                    "status": "scrape_error",
                    "term": effective_term,
                    "error": (
                        f"Indeed non copre la località '{location}': "
                        "usa un paese specifico o aggiungi LinkedIn."
                    ),
                }
                continue

        scrape_kwargs: dict[str, Any] = {
            "site_name": sites,
            "search_term": effective_term,
            "location": location,
            "is_remote": is_remote_effective,
            "results_wanted": settings.max_annunci,
            "hours_old": settings.hours_old,
            "country_indeed": indeed_country or country,
        }
        if jobspy_job_type:
            scrape_kwargs["job_type"] = jobspy_job_type
        # LinkedIn's search API returns only job cards (no description); the text
        # lives on each job's own page. Without this jobspy leaves LinkedIn jobs
        # description-less and the AI scores them blind (title only). Costs one
        # extra fetch per job (~1.6s); Indeed already includes descriptions.
        if "linkedin" in sites:
            scrape_kwargs["linkedin_fetch_description"] = True

        try:
            df = _scrape_split_indeed(scrape_kwargs)
        except Exception as exc:
            log.warning(
                "scrape_jobs failed (term=%r, location=%r): %s", effective_term, location, exc
            )
            yield {"status": "scrape_error", "term": effective_term, "error": str(exc)}
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

        yield {
            "status": "scraped",
            "term": term,
            "found": total_rows,
            "site": ", ".join(sites),
        }

        # Clear the previous run's "new" badges only now that a scrape actually
        # returned rows (a fully-failed scan must not wipe them — see F-3).
        if total_rows > 0 and not new_flags_cleared:
            db.clear_new_flags()
            new_flags_cleared = True

        # Pass 1 (serial, DB-only): filter + upsert, collect jobs needing scoring.
        to_score: list[dict[str, Any]] = []
        for _, row in df.iterrows():
            titolo = _clean_text(row.get("title")) or "N/A"
            azienda = _clean_text(row.get("company")) or "N/A"
            descrizione = _clean_text(row.get("description"))
            fonte = _clean_text(row.get("site"))
            link = _clean_text(row.get("job_url"))
            # LinkedIn's per-job description fetch occasionally 429s on a single
            # job, leaving it description-less; retry that one page (spaced out
            # from jobspy's burst, so the transient throttle has usually cleared).
            if not descrizione and fonte == "linkedin" and link:
                descrizione = fetch_linkedin_description(link)

            # Relevance gate: drop a job whose text shares NOTHING with the
            # candidate's domain/skills (e.g. a spa kitchen helper matched only
            # because the search term said "QC"). Only fires on a non-empty
            # description with zero overlap — conservative, and logged.
            # A too-short description is judged by TITLE only: a stray domain
            # token in an 82-char marketing blurb must not save an off-topic
            # job, nor its missing tokens condemn a good one. Fully empty
            # descriptions stay exempt (LinkedIn 429: we read nothing — the
            # capped estimate the user can inspect beats a silent drop).
            desc_sufficient = len(descrizione) >= MIN_DESCRIPTION_CHARS
            gate_text = f"{titolo} {descrizione}" if desc_sufficient else titolo
            if descrizione and relevance_vocab and not (_tokenize(gate_text) & relevance_vocab):
                totale_scartati += 1
                log.info(
                    "RELEVANCE_SKIP%s: '%s' @ %s (zero domain/skills overlap)",
                    "" if desc_sufficient else " (title-only)",
                    titolo,
                    azienda,
                )
                continue

            skip, _reason = pre_filtro(titolo=titolo, descrizione=descrizione)
            if skip:
                totale_scartati += 1
                continue

            if _below_min_salary(row.get("max_amount"), min_salary):
                totale_scartati += 1
                continue

            # Post-scrape filters for selections jobspy can't honor natively
            # (multiple job_types, on-site work mode). Missing fields are kept.
            if not _row_job_type_ok(row, job_types):
                totale_scartati += 1
                continue
            if not _row_work_mode_ok(row, work_types):
                totale_scartati += 1
                continue

            sede = _clean_text(row.get("location"))
            payload_job = {
                "titolo": titolo,
                "azienda": azienda,
                "descrizione": descrizione,
                "sede": sede,
                "fonte": fonte,
                "link": link,
                "ricerca_usata": term,
                # Per-posting, not the scan-wide search flag (see _detect_work_mode).
                "modalita": _detect_work_mode(row, descrizione, modalita),
            }
            job_id, is_new, status = db.upsert_job(payload_job)

            if is_new:
                totale_nuovi += 1

            # Skip re-analysis if the user already closed the job.
            if status in {"applied", "rejected", "archived"}:
                continue

            # A brand-new job has no analysis yet; only existing ones need the
            # (lightweight) check — avoids a full get_job() per scraped row.
            if not is_new and db.job_has_analysis(job_id):
                continue

            link = payload_job.get("link") or ""
            to_score.append(
                {
                    "job_id": job_id,
                    "titolo": titolo,
                    "azienda": azienda,
                    "descrizione": descrizione,
                    # Carried into scoring: the geo-eligibility check needs it.
                    "sede": sede,
                    "row": row,
                    "link": link,
                    # DB read done here on the generator thread so workers in
                    # _finalize_scored never touch the shared connection.
                    "has_recruiter": bool(
                        link and "linkedin.com" in link and db.get_recruiter(job_id)
                    ),
                }
            )

        # Pass 2 (concurrent): score surviving jobs, emit each as it resolves.
        # Jobs are chunked into work units of ``scan_batch_size`` (default >1 =
        # one LLM call per N offers → fewer free-tier 429s, faster); each unit is
        # one future. Concurrency bounds concurrent LLM *calls*, so batching cuts
        # total calls. A drained unit yields one "analyzed" event per offer.
        if to_score and not cancelled():
            batch_size = max(1, settings.scan_batch_size)
            units = [to_score[i : i + batch_size] for i in range(0, len(to_score), batch_size)]
            workers = max(1, min(settings.scan_concurrency, len(units)))
            pool = ThreadPoolExecutor(max_workers=workers)
            try:
                futures = {pool.submit(_score_unit, unit): unit for unit in units}
                for fut in as_completed(futures):
                    if cancelled():
                        break
                    try:
                        results = fut.result()
                    except Exception as exc:  # analyze_offer(s) degrade internally
                        log.warning("scoring task failed: %s", exc)
                        continue
                    for result in results:
                        db.update_job_analysis(job_id=result["job_id"], analysis=result["analysis"])
                        if result["recruiter"]:
                            db.upsert_recruiter(result["job_id"], result["recruiter"])
                        totale_analizzati += 1

                        elapsed_ms = int(time.time() * 1000) - started_at_ms
                        seen_now = (idx * max(1, settings.max_annunci)) + totale_analizzati
                        eta_ms = (
                            int((elapsed_ms / max(1, seen_now)) * (expected_total - seen_now))
                            if seen_now > 0
                            else 0
                        )
                        yield {
                            "status": "analyzed",
                            "job": {
                                "titolo": result["titolo"],
                                "azienda": result["azienda"],
                                "score": result["analysis"].get("punteggio", 0),
                            },
                            "current": seen_now,
                            "total": expected_total,
                            "percent": (
                                min(99, int(seen_now * 100 / expected_total))
                                if expected_total
                                else 0
                            ),
                            "elapsed_ms": elapsed_ms,
                            "eta_ms": eta_ms,
                        }
            finally:
                pool.shutdown(wait=False, cancel_futures=True)

        if cancelled():
            break
        time.sleep(settings.delay_tra_ricerche)

    was_cancelled = cancelled()
    # Skip retention archiving on a cancelled run (partial data — don't prune).
    archiviati = (
        0
        if was_cancelled
        else apply_post_scan_lifecycle(db=db, retention_days=settings.retention_days)
    )
    db.finish_scan(
        run_id=run_id,
        totale_trovati=totale_trovati,
        totale_nuovi=totale_nuovi,
        totale_analizzati=totale_analizzati,
        totale_scartati=totale_scartati,
    )

    duration_ms = int(time.time() * 1000) - started_at_ms
    yield {
        "status": "complete",
        "run_id": run_id,
        "totale_trovati": totale_trovati,
        "totale_nuovi": totale_nuovi,
        "totale_analizzati": totale_analizzati,
        "totale_scartati": totale_scartati,
        "archiviati": archiviati,
        "duration_ms": duration_ms,
        "percent": 100,
        "cancelled": was_cancelled,
    }
