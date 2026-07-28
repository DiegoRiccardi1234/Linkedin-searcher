import functools
import json
import random
import re
import time
import urllib.request
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
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


from app.config import AppSettings
from app.db import Database
from app.lifecycle import apply_post_scan_lifecycle
from app.log import get_logger
from app.models import ScanRequest
from app.providers.factory import ProviderManager
from app.providers.model_selector import SCORING_MIN_SIZE_B
from app.scoring_schema import ANALYSIS_SOURCE_KEY, HEURISTIC_SOURCE
from app.services import quota
from app.services.onboarding import onboarding_context, onboarding_ral
from app.services.pii import redact_pii
from app.services.recruiter_scrape import fetch_linkedin_description, fetch_recruiter
from app.services.scan.companies import canonical_company, company_matches
from app.services.scan.hard_requirements import (
    _MATCH_AXES_KEYS,
    BLOCKING_FLAGS,
    FLAG_GEO_BLOCKED,
    FLAG_GIG,
    FLAG_GRADE_BLOCKED,
    FLAG_HEURISTIC,
    FLAG_NOT_EVALUATED,
    FLAG_SALARY_BELOW,
    FLAG_SHORT_DESCRIPTION,
    _add_flag,
    _add_missing,
    _annual_amount,
    _apply_geo_eligibility,
    _apply_grade_requirement,
    _apply_salary_expectation,
    _detect_engagement,
    _extract_min_grade,
    _geo_status,
    _grade_status,
    _has_salary_signal,
    _is_non_eu_location,
    _normalize_analysis,
    _parse_ral,
    _profile_grade,
    _ral_min_from_context,
    _salary_axis,
    enforce_hard_requirements,
    hard_block_reason,
    is_unevaluated,
)
from app.services.scan.heuristics import (
    MIN_DESCRIPTION_CHARS,
    _detect_education_requirement,
    _estimate_contract_type,
    _estimate_experience_band,
    _estimate_programming_demand,
    _estimate_smart_working,
    _fallback_analysis,
    _insufficient_description_analysis,
    _local_facts,
    _unscored_analysis,
)
from app.services.scan.prompts import (
    _PER_OFFER_SCHEMA,
    _SCORING_RULES,
    _analysis_prompt,
    _batch_analysis_prompt,
    _prep_description,
)
from app.services.scan.rows import (
    _below_min_salary,
    _clean_text,
    _detect_work_mode,
    _row_job_type_ok,
    _row_work_mode_ok,
)
from app.services.scan.scraping import (
    _augment_search_term,
    _filter_indeed_freshness,
    _indeed_country_for,
    _resolve_jobspy_job_type,
)
from app.services.scan.synonyms import expand_terms
from app.services.scan.vocab import (
    _DOMAIN_VOCAB,
    BLACKLIST,
    STOPWORDS,
    TECH_KEYWORDS,
    _tokenize,
    pre_filtro,
)

# This module stays the front door of the scan pipeline: the internals now live
# under ``app.services.scan``, but callers and tests keep importing them from
# here, so the split changed no import anywhere else in the codebase. Names that
# this module no longer uses itself are listed so the linter keeps the
# re-export instead of deleting it.
__all__ = [
    "BLACKLIST",
    "BLOCKING_FLAGS",
    "FLAG_GEO_BLOCKED",
    "FLAG_GIG",
    "FLAG_GRADE_BLOCKED",
    "FLAG_HEURISTIC",
    "FLAG_NOT_EVALUATED",
    "FLAG_SALARY_BELOW",
    "FLAG_SHORT_DESCRIPTION",
    "MIN_DESCRIPTION_CHARS",
    "STOPWORDS",
    "TECH_KEYWORDS",
    "_MATCH_AXES_KEYS",
    "_PER_OFFER_SCHEMA",
    "_SCORING_RULES",
    "_analysis_prompt",
    "_apply_geo_eligibility",
    "_apply_grade_requirement",
    "_apply_salary_expectation",
    "_batch_analysis_prompt",
    "_below_min_salary",
    "_clean_text",
    "_detect_education_requirement",
    "_detect_engagement",
    "_detect_work_mode",
    "_estimate_contract_type",
    "_estimate_experience_band",
    "_estimate_programming_demand",
    "_estimate_smart_working",
    "_extract_min_grade",
    "_fallback_analysis",
    "_filter_indeed_freshness",
    "_geo_status",
    "_grade_status",
    "_has_salary_signal",
    "_indeed_country_for",
    "_insufficient_description_analysis",
    "_is_non_eu_location",
    "_local_facts",
    "_normalize_analysis",
    "_parse_ral",
    "_prep_description",
    "_profile_grade",
    "_ral_min_from_context",
    "_resolve_jobspy_job_type",
    "_row_job_type_ok",
    "_row_work_mode_ok",
    "_scrape_split_indeed",
    "_unscored_analysis",
    "analyze_offer",
    "analyze_offers_batch",
    "enforce_hard_requirements",
    "hard_block_reason",
    "is_unevaluated",
    "pre_filtro",
    "run_scan",
]

log = get_logger(__name__)

try:
    from jobspy import scrape_jobs
except ImportError:  # pragma: no cover
    scrape_jobs = None


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

# Cap on followed employers searched by name in one scan. Each one costs a
# scrape plus the scoring of whatever it returns, on top of the keyword grid.
_MAX_WATCHLIST_COMPANIES = 8


def _watchlist_for_scan(db: Database, payload: ScanRequest) -> list[dict[str, str]]:
    """Employers to search by name in this scan, capped.

    An explicit ``payload.companies`` wins (a one-off scan of one employer);
    otherwise the followed list is used, and only when the user turned it on —
    following a company should not silently add searches to every scan.
    """
    names = [str(c).strip() for c in (payload.companies or []) if str(c).strip()]
    if names:
        rows = [{"name": n, "canonical": canonical_company(n)} for n in names]
    elif db.get_preference("watchlist_enabled", "0") in ("1", "true", "on"):
        rows = [
            {"name": str(r["name"]), "canonical": str(r["canonical"])}
            for r in db.list_watchlist_companies(active_only=True)
        ]
    else:
        rows = []
    return [r for r in rows if r["canonical"]][:_MAX_WATCHLIST_COMPANIES]


def _local_scoring(call_kwargs: dict[str, Any]) -> bool:
    """True when this scoring call runs on the user's own machine."""
    return str(call_kwargs.get("provider_name") or "") == "custom"


def _local_server_reachable(settings: Any, timeout: float = 2.0) -> bool:
    """Is the configured local endpoint actually answering right now?

    A HEAD-weight check against the OpenAI-compatible ``/models`` list: no
    inference, no tokens, and it distinguishes "Ollama is not running" (the
    common case after a reboot) from "the model is slow".
    """
    base = str(getattr(settings, "custom_base_url", "") or "").rstrip("/")
    if not base:
        return False
    try:
        with urllib.request.urlopen(f"{base}/models", timeout=timeout) as resp:
            return bool(resp.status == 200)
    except Exception as exc:
        log.debug("local endpoint %s unreachable: %s", base, exc)
        return False


def _scoring_max_tokens(n_offers: int, local: bool = False) -> int:
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

    ``local``: a model on the user's own GPU spends no quota and costs nothing
    per token, and the smaller builds that fit on consumer hardware are wordier
    than the hosted ones — a 12B wrote past this ceiling on the first real offer
    it was given and had its JSON cut off. Being generous there is free; being
    tight there just re-creates the truncation this budget exists to prevent.
    """
    if local:
        return 1200 * max(1, n_offers) + 3000
    return 500 * max(1, n_offers) + 1600


def _scoring_call_kwargs(provider_manager: ProviderManager) -> dict[str, Any]:
    """Provider/model kwargs for a scoring call: pin the user-chosen scoring
    model if set (no failover), else auto-select via the speed-biased policy.
    getattr-guarded so test stubs without ``.settings`` still work.
    """
    settings = getattr(provider_manager, "settings", None)
    scoring_model = getattr(settings, "scoring_model", None)
    if scoring_model:
        # The pinned model does not necessarily live on the primary provider:
        # a local Ollama tag asked of OpenRouter is a 404. ``scoring_provider``
        # is set by the local-models panel; without it the primary still wins.
        order = getattr(settings, "llm_provider_order", None) or []
        provider = getattr(settings, "scoring_provider", None) or (order[0] if order else None)
        # A pin has no failover, which is the point — except when the pinned
        # provider is a local server that simply is not running. Then every
        # single offer would fall to the keyword estimate, one dead call at a
        # time. The check costs nothing (no inference) and the answer is
        # unambiguous, so fall back to the normal cloud selection instead.
        if provider == "custom" and not _local_server_reachable(settings):
            log.warning(
                "Scoring pinned to a local model (%s) but the endpoint is not "
                "answering: falling back to the usual provider selection.",
                scoring_model,
            )
            return {"policy_override": _SCORING_POLICY}
        return {"provider_name": provider, "model_name": scoring_model}
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


#: Raw ``motivo_non_valutazione`` -> the bucket the summary reports. The raw
#: value is a provider exception message, which is useless to the user and
#: unbounded in shape; what they need is which of the four fixable situations
#: they are in.
_UNEVALUATED_BUCKETS = (
    ("descrizione_breve", ("insufficient_description",)),
    ("quota_finita", ("daily_limit", "quota", "insufficient_quota")),
    ("modello_non_disponibile", ("429", "rate", "timeout", "connection", "503", "502", "403")),
    ("risposta_inutilizzabile", ("invalid response", "truncat", "empty", "json")),
)


def _unevaluated_reason(analysis: dict[str, Any]) -> str:
    """Which situation left this offer unjudged, as a stable code."""
    raw = str(analysis.get("motivo_non_valutazione") or "").lower()
    for bucket, markers in _UNEVALUATED_BUCKETS:
        if any(marker in raw for marker in markers):
            return bucket
    return "errore_provider"


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
    return {
        # Not "unevaluated": this offer HAS a verdict, computed by the app from
        # the posting itself. ``enforce_hard_requirements`` caps it to 3 right
        # after (``_cap_score`` reads a missing score as 0 and applies the cap).
        ANALYSIS_SOURCE_KEY: HEURISTIC_SOURCE,
        "riassunto": f"Non candidabile: {reason}. Analisi locale, nessuna chiamata IA.",
        **_local_facts(titolo, descrizione),
    }


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
    call_kwargs = _scoring_call_kwargs(provider_manager)
    try:
        result = provider_manager.complete_json(
            prompt=prompt,
            max_tokens=_scoring_max_tokens(1, local=_local_scoring(call_kwargs)),
            **call_kwargs,
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
            batch_kwargs = _scoring_call_kwargs(provider_manager)
            result = provider_manager.complete_json(
                prompt=prompt,
                max_tokens=_scoring_max_tokens(len(scorable), local=_local_scoring(batch_kwargs)),
                **batch_kwargs,
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


class _ScanCancelled(Exception):
    """Raised on a worker that was about to call the model after a stop."""


# The onboarding answers are free text ("junior", "da remoto", "hybrid"). These
# read them loosely enough to be useful as scan defaults and strictly enough to
# stay quiet when the user wrote something else entirely.
_GOAL_LEVELS = {
    "internship": ("tirocinio", "stage", "internship", "tirocinante"),
    "entry": ("entry", "neolaureat", "graduate", "primo impiego"),
    "junior": ("junior", "1-2 anni", "1-3 anni"),
    "mid": ("mid", "intermedio", "3-5 anni"),
    "senior": ("senior", "esperto", "5+"),
}
_GOAL_WORK_MODES = {
    "remote": ("remot", "da casa", "smart working", "full remote"),
    "hybrid": ("ibrid", "hybrid", "misto"),
    "onsite": ("sede", "ufficio", "presenza", "on-site", "onsite"),
}


def _levels_from_goal(raw: str) -> list[str]:
    text = str(raw or "").lower()
    return [level for level, markers in _GOAL_LEVELS.items() if any(m in text for m in markers)]


def _work_types_from_goal(raw: str) -> list[str]:
    text = str(raw or "").lower()
    return [mode for mode, markers in _GOAL_WORK_MODES.items() if any(m in text for m in markers)]


#: Conservative context ceilings (prompt + completion) per provider, used to
#: size a batch. Cerebras' free tier caps context at 8K — a batch of three long
#: descriptions plus the CV and the schema goes straight past it and the call
#: fails, which looked like "the model is down". Only providers with a known
#: small ceiling need an entry; everything else gets the generous default.
_CONTEXT_LIMITS = {"cerebras": 8192}
_DEFAULT_CONTEXT_LIMIT = 32000

#: Rough token estimate from characters. Fine for sizing decisions: the point is
#: to stay clear of the ceiling, not to predict the tokenizer.
_CHARS_PER_TOKEN = 4


def _context_budget(provider_manager: ProviderManager) -> int:
    """Prompt tokens a batch may occupy on the provider that will serve it."""
    settings = getattr(provider_manager, "settings", None)
    order = getattr(settings, "llm_provider_order", None) or []
    primary = str(order[0]).lower() if order else ""
    ceiling = _CONTEXT_LIMITS.get(primary, _DEFAULT_CONTEXT_LIMIT)
    # Leave room for the answer: the completion budget is per offer.
    return max(1500, ceiling - _scoring_max_tokens(1))


def _scoring_units(
    items: list[dict[str, Any]], batch_size: int, budget_tokens: int, profile_chars: int
) -> list[list[dict[str, Any]]]:
    """Split offers into work units that fit both the batch size AND the context.

    Chunking by count alone ignores how long the postings are: three 5000-char
    descriptions do not fit where three 800-char ones do.
    """
    units: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    base_tokens = (profile_chars + 2000) // _CHARS_PER_TOKEN  # CV + schema + rules
    current_tokens = base_tokens
    for item in items:
        cost = (len(str(item.get("descrizione") or "")) + 200) // _CHARS_PER_TOKEN
        too_many = len(current) >= batch_size
        too_big = current and (current_tokens + cost) > budget_tokens
        if too_many or too_big:
            units.append(current)
            current = []
            current_tokens = base_tokens
        current.append(item)
        current_tokens += cost
    if current:
        units.append(current)
    return units


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

    # A scan is the one action that spends hundreds of requests: if today's
    # budget is already gone it stops here, with the numbers, instead of finding
    # out through a wall of 429s halfway through and scoring the rest locally.
    quota_block = quota.blocks_scan(db)
    if quota_block:
        yield {"error": quota_block, "quota": quota.status(db)}
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

    # The same job is advertised under different words: an Italian searching
    # "tirocinio" never sees the postings titled "stage" or "internship", which
    # is most of them. Each alternative is a separate search, because the boards
    # rank on all the words in a query together.
    terms = expand_terms(list(payload.search_terms or settings.default_search_terms))
    exp_levels = list(payload.experience_levels or [])
    job_types = list(payload.job_types or [])
    work_types = list(payload.work_types or [])
    # The search goals are not decoration: what the user said they want is the
    # default for the filters they did not set on this particular scan.
    if not exp_levels:
        exp_levels = _levels_from_goal(db.get_preference("onboarding_seniority", ""))
    if not work_types:
        work_types = _work_types_from_goal(db.get_preference("onboarding_work_mode", ""))
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

    # Followed employers get their own search, by name, on the primary location
    # only: a company search is about WHO is hiring, not where, and repeating it
    # per location would multiply the cost for the same handful of postings.
    watchlist = _watchlist_for_scan(db, payload)

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
    total_batches = max(1, len(terms) * len(locations_list) + len(watchlist))
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
    totale_non_valutati = 0
    # Why offers went unjudged, bucketed — the summary needs ONE reason to show,
    # and "the provider is rate-limited" and "the ad had no text" call for
    # completely different actions from the user.
    motivi_non_valutati: dict[str, int] = {}
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
        unevaluated = is_unevaluated(analysis)
        axes = analysis.get("match_axes")
        # Only for an offer someone judged: a lone salary axis on an unevaluated
        # analysis draws a one-spoke radar, which reads as "it WAS analysed".
        if not unevaluated and isinstance(axes, dict) and axes.get("salary_match") is None:
            axis = _salary_axis(low, high, ral_min)
            if axis is not None:
                axes["salary_match"] = axis
        if ral_min and high and high < ral_min:
            # A fact about the posting, not a judgement of it: flag it either way.
            detail = f"RAL dichiarata fino a {int(high)} EUR, sotto la tua minima ({ral_min})"
            _add_flag(analysis, FLAG_SALARY_BELOW, detail)
            _add_missing(analysis, detail)
        # Coerce the score to int — but never resurrect a missing one as 0:
        # int(None) raises, and the regex fallback below would read "None" as no
        # digits and write 0, silently turning "nobody judged it" into a verdict.
        raw_score = analysis.get("punteggio")
        if raw_score is None:
            analysis["punteggio"] = None
        else:
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
        call (avoids wasting a batch prompt on the last odd job).

        Checked here, on the worker, right before the call: cancelling a scan
        only stopped futures that had not STARTED yet, so every worker already
        running went on to spend its quota on results nobody would read (and the
        pool holds several at a time).
        """
        if cancelled():
            raise _ScanCancelled
        if len(unit) > 1:
            return _score_batch(unit)
        return [_score_job(unit[0])]

    # Flat (location, term) pairs so the existing single-loop body stays intact;
    # batch_no drives global progress across the whole location x term grid.
    # A fourth slot carries the followed employer when this pass is a company
    # search: same loop body, three decisions inside it read it (search term,
    # relevance gate, which rows are kept).
    scan_pairs: list[tuple[str, int, str, dict[str, str] | None]] = [
        (loc, ti, term, None) for loc in locations_list for ti, term in enumerate(terms)
    ]
    scan_pairs += [(primary_location, -1, w["name"], w) for w in watchlist]
    for batch_no, (location, idx, term, watched) in enumerate(scan_pairs):
        if cancelled():
            break
        if batch_no > 0:
            time.sleep(random.uniform(0.8, 2.4))

        # A company name takes no level/mode qualifiers: "RWS junior hybrid"
        # searches for a posting that says all three, not for RWS.
        effective_term = (
            term if watched else (augmented_terms[idx] if 0 <= idx < len(augmented_terms) else term)
        )

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
        watch_hit = False
        for _, row in df.iterrows():
            titolo = _clean_text(row.get("title")) or "N/A"
            azienda = _clean_text(row.get("company")) or "N/A"
            descrizione = _clean_text(row.get("description"))

            # A company search is a keyword search to the board: searching "RWS"
            # returns postings that merely MENTION it (agencies reselling the
            # role, "clients such as…"). Keep only what this employer published.
            if watched and not company_matches(azienda, watched["canonical"]):
                totale_scartati += 1
                continue
            if watched and not watch_hit:
                watch_hit = True
                db.touch_watchlist_seen(watched["canonical"])
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
            # A followed employer is itself the relevance signal: the user said
            # they want to see what THIS company posts, and the vocabulary check
            # would drop a language-data role at RWS for not sounding technical.
            # The hard blockers (geo, degree grade, pay) still apply downstream.
            desc_sufficient = len(descrizione) >= MIN_DESCRIPTION_CHARS
            gate_text = f"{titolo} {descrizione}" if desc_sufficient else titolo
            if (
                not watched
                and descrizione
                and relevance_vocab
                and not (_tokenize(gate_text) & relevance_vocab)
            ):
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
                # Tagged so the archive says where a job came from: a keyword
                # search or an employer the user follows.
                "ricerca_usata": f"watchlist:{watched['name']}" if watched else term,
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
            # Progress is measured over the whole grid, so this counts what THIS
            # (location, term) pair contributed; the cumulative total lives in
            # ``batch_no``.
            pair_analyzed = 0
            batch_size = max(1, settings.scan_batch_size)
            units = _scoring_units(
                to_score,
                batch_size,
                _context_budget(provider_manager),
                len(profile_markdown),
            )
            workers = max(1, min(settings.scan_concurrency, len(units)))
            pool = ThreadPoolExecutor(max_workers=workers)
            try:
                futures = {pool.submit(_score_unit, unit): unit for unit in units}
                for fut in as_completed(futures):
                    if cancelled():
                        break
                    try:
                        results = fut.result()
                    except _ScanCancelled:
                        continue  # the user pressed stop; nothing was spent
                    except Exception as exc:  # analyze_offer(s) degrade internally
                        log.warning("scoring task failed: %s", exc)
                        continue
                    for result in results:
                        db.update_job_analysis(job_id=result["job_id"], analysis=result["analysis"])
                        if result["recruiter"]:
                            db.upsert_recruiter(result["job_id"], result["recruiter"])
                        # "Analysed" must mean judged. An offer that came back
                        # unevaluated (provider down, unusable answer, no
                        # description) is counted apart, so the summary cannot
                        # report 36 analysed when 11 of them were never read.
                        # ``pair_analyzed`` still counts everything: it drives
                        # the progress bar, which must keep moving.
                        if is_unevaluated(result["analysis"]):
                            totale_non_valutati += 1
                            _bucket = _unevaluated_reason(result["analysis"])
                            motivi_non_valutati[_bucket] = motivi_non_valutati.get(_bucket, 0) + 1
                        else:
                            totale_analizzati += 1
                        pair_analyzed += 1

                        elapsed_ms = int(time.time() * 1000) - started_at_ms
                        # batch_no, not idx: the scraping phase counts progress
                        # over the whole location x term grid, while ``idx`` is
                        # the TERM index and resets at every new location — so on
                        # a multi-location scan the bar walked backwards. And it
                        # is the pair's own count that is added, not the running
                        # total, which would be counted twice.
                        seen_now = min(
                            expected_total,
                            (batch_no * max(1, settings.max_annunci)) + pair_analyzed,
                        )
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
                                # None (not 0) when nobody judged it: the feed
                                # renders it as "da valutare", never as 0/10.
                                "score": result["analysis"].get("punteggio"),
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
        "totale_non_valutati": totale_non_valutati,
        # The single reason to show the user, with what it would take to fix it.
        "motivo_non_valutati": (
            max(motivi_non_valutati.items(), key=lambda kv: kv[1])[0] if motivi_non_valutati else ""
        ),
        "totale_scartati": totale_scartati,
        "archiviati": archiviati,
        "duration_ms": duration_ms,
        "percent": 100,
        "cancelled": was_cancelled,
    }
