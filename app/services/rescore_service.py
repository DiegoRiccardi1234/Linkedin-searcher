"""Score offers that are ALREADY in the archive, one or many.

Re-scoring existed only as a single button on a single offer, and only when that
offer had never been judged. Everything else — a whole archive scored by a model
that turned out to be too generous, offers left unjudged by a provider outage,
verdicts that predate a fix to the blocking rules — had no path at all: the only
way to refresh a stored analysis was to hope the same posting showed up in a
future scan, which for an expired ad never happens.

The context (CV, declared preferences, the candidate's facts) is built ONCE and
reused for every offer, because it is identical for all of them and rebuilding it
per offer re-reads the CV and re-parses the preferences each time.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from app.log import get_logger
from app.services.candidate_facts import CandidateFacts, candidate_facts
from app.services.onboarding import onboarding_context
from app.services.scanner_service import BLOCKING_FLAGS, analyze_offer, is_unevaluated

if TYPE_CHECKING:
    from app.db import Database

log = get_logger(__name__)

#: Scopes the caller can ask for, resolved by :func:`select_job_ids`.
SCOPES = ("unscored", "applicable", "all")

#: Seconds to leave between two scoring calls. Free tiers cap REQUESTS PER
#: MINUTE, and a fast host answers in about a second — so a bulk run with no
#: pause hits the cap a third of the way in and turns the rest of the archive
#: into 429s. Skipped when the call itself already took this long.
_PAUSE_BETWEEN_CALLS = 2.0


def linkedin_suffix(db: Database) -> str:
    """CV-context suffix from the saved LinkedIn data.

    Prefers the fetched/pasted profile text over the bare URL. Truncated; PII is
    scrubbed downstream by Privacy Mode since this is appended to the CV markdown.
    """
    text = db.get_preference("linkedin_profile_text", "")
    if text and text.strip():
        return f"\n\nProfilo LinkedIn (estratto):\n{text.strip()[:2000]}"
    url = db.get_preference("linkedin_url", "")
    if url:
        return f"\n\nProfilo LinkedIn: {url}"
    return ""


@dataclass(frozen=True)
class RescoreContext:
    """Everything a scoring call needs that does not depend on the offer."""

    profile_markdown: str
    candidate_name: str | None
    onboarding: str
    privacy: bool
    facts: CandidateFacts


def build_context(db: Database, *, privacy: bool) -> RescoreContext:
    profile = db.get_active_candidate_profile()
    markdown = (profile["markdown"] if profile else "Profile not loaded") + linkedin_suffix(db)
    return RescoreContext(
        profile_markdown=markdown,
        candidate_name=(profile.get("name") if profile else None),
        onboarding=onboarding_context(db),
        privacy=privacy,
        facts=candidate_facts(db),
    )


def rescore_job(provider_manager: Any, job: dict[str, Any], ctx: RescoreContext) -> dict[str, Any]:
    """Score one stored offer with the SAME inputs a scan would use.

    ``facts`` and ``modalita`` are the two that were missing here: without them
    ``blocking_reasons`` returns nothing and ``location_status`` never sees a
    work mode, so the three checks the app advertises — years, degree, reachable
    location — were structurally dead on this path. An offer in another city
    could be re-scored to 9 by the same app that hides it in the list.
    """
    return analyze_offer(
        provider_manager=provider_manager,
        profile_markdown=ctx.profile_markdown,
        titolo=job.get("titolo", ""),
        azienda=job.get("azienda", ""),
        descrizione=job.get("descrizione") or "",
        privacy=ctx.privacy,
        extra_context=ctx.onboarding,
        candidate_name=ctx.candidate_name,
        sede=job.get("sede") or "",
        modalita=job.get("modalita") or "",
        facts=ctx.facts,
    )


def select_job_ids(db: Database, scope: str, ids: list[int] | None = None) -> list[int]:
    """Which offers a scope means, cheapest-to-most first.

    ``applicable`` deliberately skips offers held back by a hard block: their
    score is calculated by the cap, so asking a model about them spends a call to
    arrive back at the same 3.
    """
    if ids:
        return list(dict.fromkeys(ids))
    jobs = db.list_jobs(limit=2000)
    if scope == "unscored":
        return [j["id"] for j in jobs if j.get("punteggio_ai") is None]
    if scope == "applicable":
        return [j["id"] for j in jobs if not (set(j.get("flags") or []) & BLOCKING_FLAGS)]
    return [j["id"] for j in jobs]


def rescore_jobs(
    db: Database,
    provider_manager: Any,
    *,
    job_ids: list[int],
    privacy: bool,
    cancel_check: Any = None,
) -> Iterator[dict[str, Any]]:
    """Re-score a list of offers, reporting progress as it goes.

    One offer per call on purpose: a batch prompt is what produced cloned
    verdicts in the first place, and here there is no scan-length budget to save.
    A provider failure on one offer must not abort the rest — the offer is
    reported as failed and the run continues.
    """
    ctx = build_context(db, privacy=privacy)
    total = len(job_ids)
    done = changed = unevaluated = failed = 0
    yield {"status": "start", "total": total}

    for job_id in job_ids:
        if cancel_check and cancel_check():
            break
        job = db.get_job_with_analysis(job_id)
        if not job:
            continue
        before = job.get("punteggio_ai")
        started = time.monotonic()
        try:
            analysis = rescore_job(provider_manager, job, ctx)
        except Exception as exc:  # a dead provider must not kill the whole run
            failed += 1
            done += 1
            log.warning("re-score failed for job %s: %s", job_id, exc)
            yield {"status": "failed", "job_id": job_id, "current": done, "total": total}
            continue
        db.update_job_analysis(job_id=job_id, analysis=analysis)
        after = analysis.get("punteggio")
        done += 1
        idle = _PAUSE_BETWEEN_CALLS - (time.monotonic() - started)
        if idle > 0 and done < total:
            time.sleep(idle)
        if is_unevaluated(analysis):
            unevaluated += 1
        elif after != before:
            changed += 1
        yield {
            "status": "scored",
            "job_id": job_id,
            "titolo": job.get("titolo", ""),
            "azienda": job.get("azienda", ""),
            "before": before,
            "after": after,
            "current": done,
            "total": total,
            "percent": min(99, int(done * 100 / total)) if total else 100,
        }

    yield {
        "status": "complete",
        "total": total,
        "rivalutate": done,
        "cambiate": changed,
        "non_valutate": unevaluated,
        "fallite": failed,
    }
