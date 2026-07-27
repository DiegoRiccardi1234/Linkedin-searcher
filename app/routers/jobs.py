from __future__ import annotations

import csv
from datetime import datetime
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse

from app.models import (
    FavoriteRequest,
    JobActionRequest,
    JobImportRequest,
    JobNoteRequest,
    ManualJobCreateRequest,
    ReminderRequest,
)
from app.services.generation import generate_with_profile
from app.services.job_import import extract_job_fields, fetch_page_text
from app.services.onboarding import onboarding_context
from app.services.scanner_service import BLOCKING_FLAGS, analyze_offer
from app.services.skill_gap import compute_skill_gap, suggest_learning

if TYPE_CHECKING:
    from app.container import AppContainer


def _linkedin_suffix(db: Any) -> str:
    """CV-context suffix from the saved LinkedIn data (F7).

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


def build_router(container: AppContainer) -> APIRouter:
    router = APIRouter()

    @router.get("/api/jobs")
    def list_jobs(
        status: str | None = Query(default=None),
        only_favorites: bool = Query(default=False),
        only_new: bool = Query(default=False),
        remote_only: bool = Query(default=False),
        search_text: str | None = Query(default=None),
        min_score: int | None = Query(default=None, ge=0, le=10),
        max_age_days: int | None = Query(default=None, ge=1, le=365),
        applicable_only: bool = Query(default=False),
        limit: int = Query(default=200, ge=1, le=2000),
    ) -> dict[str, Any]:
        jobs = container.db.list_jobs(
            status=status,
            only_favorites=only_favorites,
            only_new=only_new,
            remote_only=remote_only,
            search_text=search_text,
            min_score=min_score,
            max_age_days=max_age_days,
            limit=limit,
        )
        if applicable_only:
            jobs = [job for job in jobs if not (set(job.get("flags") or []) & BLOCKING_FLAGS)]
        # The list view renders none of these, and they are by far the heaviest
        # columns (a full posting is ~5k chars; 200 of them is megabytes per
        # refresh). The detail endpoint still serves them.
        for job in jobs:
            for heavy in ("descrizione", "analysis_json", "sources_json"):
                job.pop(heavy, None)
        return {"jobs": jobs}

    @router.get("/api/jobs/{job_id}")
    def get_job_detail(job_id: int) -> dict[str, Any]:
        job = container.db.get_job_with_analysis(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        recruiter = container.db.get_recruiter(job_id)
        return {"job": job, "recruiter": recruiter}

    @router.post("/api/jobs/{job_id}/cover-letter")
    def generate_cover_letter(job_id: int) -> dict[str, Any]:
        job = container.db.get_job_with_analysis(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")

        profile = container.db.get_active_candidate_profile()
        profile_markdown = profile["markdown"] if profile else "CV non disponibile."

        profile_markdown += _linkedin_suffix(container.db)

        titolo = job.get("titolo", "N/A")
        azienda = job.get("azienda", "N/A")
        descrizione = job.get("descrizione", "")

        recruiter = container.db.get_recruiter(job_id)
        if not recruiter and job.get("link") and "linkedin.com" in job.get("link", ""):
            try:
                from app.services.recruiter_scrape import fetch_recruiter

                fetched = fetch_recruiter(job["link"], timeout=3.0)
                if fetched:
                    container.db.upsert_recruiter(job_id, fetched)
                    recruiter = container.db.get_recruiter(job_id)
            except Exception as exc:
                container.log.debug("on-demand recruiter scrape failed: %s", exc)

        recruiter_block = ""
        if recruiter and (recruiter.get("name") or recruiter.get("headline")):
            parts = []
            if recruiter.get("name"):
                parts.append(f"Nome: {recruiter['name']}")
            if recruiter.get("title"):
                parts.append(f"Ruolo: {recruiter['title']}")
            if recruiter.get("headline"):
                parts.append(f"Headline: {recruiter['headline']}")
            recruiter_block = (
                "\nDESTINATARIO (recruiter / hiring manager visibile nell'annuncio):\n"
                + "\n".join(parts)
                + "\nApri la lettera con un saluto nominale rivolto a questa persona "
                "(es. 'Gentile {nome},') e fai un breve riferimento al suo ruolo."
            )

        candidate_name = profile.get("name") if profile else None
        try:
            cover_letter = generate_with_profile(
                container.providers,
                "cover_letter",
                profile_markdown,
                {"titolo": titolo, "azienda": azienda, "descrizione": descrizione},
                extra_block=recruiter_block,
                redact=container.feature_enabled("privacy_mode", True),
                candidate_name=candidate_name,
            )
            container.db.save_cover_letter(job_id, cover_letter)
        except Exception as e:
            # This used to return 200 with the exception text AS the letter, so
            # a provider 401 was rendered in the UI as generated prose. The three
            # sibling generation endpoints below all raise; so does this one now.
            raise HTTPException(status_code=502, detail=f"Cover letter failed: {e}") from e

        return {"cover_letter": cover_letter}

    def _job_generation_context(job_id: int) -> tuple[dict[str, Any], str, str | None]:
        """Resolve (job_info, profile_markdown, candidate_name) for a generation
        endpoint.

        Raises 404 if the job is missing. Shared by interview-prep and
        resume-tailoring. ``candidate_name`` lets Privacy Mode restore the real
        name in the generated text.
        """
        job = container.db.get_job_with_analysis(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        profile = container.db.get_active_candidate_profile()
        profile_markdown = profile["markdown"] if profile else "CV non disponibile."
        candidate_name = profile.get("name") if profile else None
        job_info = {
            "titolo": job.get("titolo", "N/A"),
            "azienda": job.get("azienda", "N/A"),
            "descrizione": job.get("descrizione", ""),
        }
        return job_info, profile_markdown, candidate_name

    @router.post("/api/jobs/{job_id}/interview-prep")
    def generate_interview_prep(job_id: int) -> dict[str, Any]:
        container.require_feature("interview_prep")
        job_info, profile_markdown, candidate_name = _job_generation_context(job_id)
        try:
            content = generate_with_profile(
                container.providers,
                "interview_prep",
                profile_markdown,
                job_info,
                redact=container.feature_enabled("privacy_mode", True),
                candidate_name=candidate_name,
            )
            container.db.save_job_analysis_field(job_id, "interview_prep", content)
        except Exception as e:
            raise HTTPException(
                status_code=502, detail=f"Interview prep generation failed: {e}"
            ) from e
        return {"interview_prep": content}

    @router.post("/api/jobs/{job_id}/tailored-resume")
    def generate_tailored_resume(job_id: int) -> dict[str, Any]:
        container.require_feature("resume_tailoring")
        job_info, profile_markdown, candidate_name = _job_generation_context(job_id)
        try:
            content = generate_with_profile(
                container.providers,
                "resume_tailoring",
                profile_markdown,
                job_info,
                redact=container.feature_enabled("privacy_mode", True),
                candidate_name=candidate_name,
                restore_contact_info=True,
            )
            container.db.save_job_analysis_field(job_id, "tailored_resume", content)
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"Resume tailoring failed: {e}") from e
        return {"tailored_resume": content}

    @router.post("/api/jobs/{job_id}/recruiter-outreach")
    def generate_recruiter_outreach(job_id: int, lang: str = Query(default="")) -> dict[str, Any]:
        """Draft a short outreach message to the posting's recruiter (F6).

        Same recruiter-aware path as the cover letter, but a shorter message and
        language-aware output (follows the UI locale passed as ``lang``).
        """
        job = container.db.get_job_with_analysis(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")

        profile = container.db.get_active_candidate_profile()
        profile_markdown = profile["markdown"] if profile else "CV non disponibile."
        candidate_name = profile.get("name") if profile else None

        recruiter = container.db.get_recruiter(job_id)
        recruiter_block = ""
        if recruiter and (recruiter.get("name") or recruiter.get("headline")):
            parts = []
            if recruiter.get("name"):
                parts.append(f"Nome: {recruiter['name']}")
            if recruiter.get("title"):
                parts.append(f"Ruolo: {recruiter['title']}")
            if recruiter.get("headline"):
                parts.append(f"Headline: {recruiter['headline']}")
            recruiter_block = (
                "\nDESTINATARIO (recruiter / hiring manager visibile nell'annuncio):\n"
                + "\n".join(parts)
            )

        job_info = {
            "titolo": job.get("titolo", "N/A"),
            "azienda": job.get("azienda", "N/A"),
            "descrizione": job.get("descrizione", ""),
        }
        try:
            content = generate_with_profile(
                container.providers,
                "recruiter_outreach",
                profile_markdown,
                job_info,
                extra_block=recruiter_block,
                redact=container.feature_enabled("privacy_mode", True),
                candidate_name=candidate_name,
                language=(lang or None),
            )
            container.db.save_job_analysis_field(job_id, "recruiter_outreach", content)
        except Exception as e:
            raise HTTPException(
                status_code=502, detail=f"Recruiter outreach generation failed: {e}"
            ) from e
        return {"recruiter_outreach": content}

    @router.get("/api/analytics")
    def get_analytics() -> dict[str, Any]:
        return container.db.get_analytics()

    @router.get("/api/skill-gap")
    def skill_gap() -> dict[str, Any]:
        container.require_feature("skill_gap")
        return compute_skill_gap(container.db)

    @router.get("/api/skill-gap/learning")
    def skill_gap_learning(lang: str = Query(default="")) -> dict[str, Any]:
        """On-demand learning resources for the top skill gaps (F8)."""
        container.require_feature("skill_gap")
        container.require_provider()
        gap = compute_skill_gap(container.db)
        return suggest_learning(container.providers, gap.get("gaps", []), language=(lang or None))

    @router.get("/api/recommendations")
    def recommendations(limit: int = Query(default=5, ge=1, le=20)) -> dict[str, Any]:
        jobs = container.db.get_recommended_jobs(limit=limit)
        return {
            "jobs": jobs,
            "message": "Ecco i lavori prioritari da valutare e candidare.",
        }

    @router.post("/api/jobs/manual")
    def add_manual_job(payload: ManualJobCreateRequest) -> dict[str, Any]:
        row = {
            "titolo": payload.titolo,
            "azienda": payload.azienda,
            "descrizione": payload.descrizione,
            "sede": payload.sede,
            "fonte": payload.fonte,
            "link": payload.link,
            "ricerca_usata": payload.ricerca_usata,
            "modalita": payload.modalita,
        }
        job_id = container.db.add_manual_job(row)

        profile = container.db.get_active_candidate_profile()
        profile_markdown = profile["markdown"] if profile else "Profile not loaded"

        profile_markdown += _linkedin_suffix(container.db)

        analysis = analyze_offer(
            provider_manager=container.providers,
            profile_markdown=profile_markdown,
            titolo=payload.titolo,
            azienda=payload.azienda,
            descrizione=payload.descrizione,
            privacy=container.feature_enabled("privacy_mode", True),
            extra_context=onboarding_context(container.db),
            candidate_name=(profile.get("name") if profile else None),
            sede=payload.sede or "",
        )
        container.db.update_job_analysis(job_id=job_id, analysis=analysis)
        return {"job_id": job_id, "analysis": analysis}

    @router.post("/api/jobs/import")
    def import_job(payload: JobImportRequest) -> dict[str, Any]:
        """Import a posting from a URL (fallback: pasted text), LLM-extract its
        fields, store it and AI-score it via the same path as a manual add."""
        container.require_provider()
        url = (payload.url or "").strip()
        text = (payload.text or "").strip()

        fetch_ok = False
        used_fallback = False
        if url:
            page = fetch_page_text(url)
            if page and len(page) >= 400:
                raw, fetch_ok = page, True
            elif text:
                raw, used_fallback = text, True
            else:
                # Fetch blocked/thin (LinkedIn) and nothing pasted to fall back to.
                raise HTTPException(
                    status_code=422,
                    detail={
                        "code": "fetch_failed",
                        "message_key": "manualJob.importFetchFailed",
                    },
                )
        elif text:
            raw = text
        else:
            raise HTTPException(
                status_code=422,
                detail={"code": "no_input", "message_key": "manualJob.importNeedInput"},
            )

        fields = extract_job_fields(container.providers, raw)
        if not fields.get("titolo") and not fields.get("azienda"):
            raise HTTPException(
                status_code=422,
                detail={"code": "extract_failed", "message_key": "manualJob.importExtractFailed"},
            )

        row = {
            "titolo": fields.get("titolo") or "Imported job",
            "azienda": fields.get("azienda") or "N/A",
            "descrizione": fields.get("descrizione", ""),
            "sede": fields.get("sede", ""),
            "fonte": "import",
            "link": url,
            "ricerca_usata": "import",
            "modalita": "Import",
        }
        job_id = container.db.add_manual_job(row)

        profile = container.db.get_active_candidate_profile()
        profile_markdown = profile["markdown"] if profile else "Profile not loaded"
        profile_markdown += _linkedin_suffix(container.db)

        analysis = analyze_offer(
            provider_manager=container.providers,
            profile_markdown=profile_markdown,
            titolo=row["titolo"],
            azienda=row["azienda"],
            descrizione=row["descrizione"],
            privacy=container.feature_enabled("privacy_mode", True),
            extra_context=onboarding_context(container.db),
            candidate_name=(profile.get("name") if profile else None),
            # Without the location the geo-eligibility cap is structurally dead
            # for imported jobs: a US posting could never be flagged.
            sede=row["sede"],
        )
        container.db.update_job_analysis(job_id=job_id, analysis=analysis)
        return {
            "job_id": job_id,
            "analysis": analysis,
            "fields": fields,
            "fetch_ok": fetch_ok,
            "used_fallback": used_fallback,
        }

    @router.post("/api/jobs/{job_id}/action")
    def set_job_action(job_id: int, payload: JobActionRequest) -> dict[str, Any]:
        job = container.db.get_job(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        container.db.set_job_action(job_id=job_id, action=payload.action.value, notes=payload.notes)
        return {"ok": True}

    @router.get("/api/jobs/{job_id}/timeline")
    def job_timeline(job_id: int) -> dict[str, Any]:
        """Chronological status changes + notes for a job (F3)."""
        return {"actions": container.db.list_job_actions(job_id)}

    @router.post("/api/jobs/{job_id}/note")
    def add_job_note(job_id: int, payload: JobNoteRequest) -> dict[str, Any]:
        """Record a free-text note on the job's timeline without changing status."""
        note = payload.notes.strip()
        if not note:
            raise HTTPException(status_code=400, detail="empty_note")
        if not container.db.get_job(job_id):
            raise HTTPException(status_code=404, detail="Job not found")
        container.db.set_job_action(job_id=job_id, action="note", notes=note)
        return {"ok": True}

    @router.post("/api/jobs/{job_id}/reminder")
    def set_job_reminder(job_id: int, payload: ReminderRequest) -> dict[str, Any]:
        """Set (or clear, when reminder_at is empty) a follow-up reminder (F4)."""
        if not container.db.get_job(job_id):
            raise HTTPException(status_code=404, detail="Job not found")
        container.db.set_job_reminder(job_id, payload.reminder_at, payload.note)
        return {"ok": True}

    @router.delete("/api/jobs/{job_id}/reminder")
    def clear_job_reminder(job_id: int) -> dict[str, Any]:
        if not container.db.get_job(job_id):
            raise HTTPException(status_code=404, detail="Job not found")
        container.db.clear_job_reminder(job_id)
        return {"ok": True}

    @router.get("/api/reminders")
    def list_reminders() -> dict[str, Any]:
        """Manual reminders due + auto nudges for stale applications (F4)."""
        raw = container.db.get_preference("reminder_stale_days", "7")
        try:
            stale_days = max(1, int(raw))
        except (TypeError, ValueError):
            stale_days = 7
        return container.db.list_reminders(stale_days=stale_days)

    @router.post("/api/jobs/{job_id}/favorite")
    def set_favorite(job_id: int, payload: FavoriteRequest) -> dict[str, Any]:
        job = container.db.get_job(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        container.db.set_favorite(job_id=job_id, is_favorite=payload.is_favorite)
        return {"ok": True}

    @router.delete("/api/jobs/{job_id}")
    def delete_job(job_id: int) -> dict[str, Any]:
        deleted = container.db.delete_job(job_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="Job not found")
        return {"ok": True, "deleted_id": job_id}

    @router.delete("/api/jobs")
    def delete_all_jobs() -> dict[str, Any]:
        count = container.db.delete_all_jobs()
        return {"ok": True, "deleted": count}

    @router.get("/api/applications/export")
    def export_applications(format: str = "csv") -> StreamingResponse:
        cur = container.db.conn.cursor()
        raw_rows = cur.execute(
            "SELECT titolo, azienda, sede, status, punteggio_ai, consiglio, link, "
            "updated_at, first_seen_at FROM jobs WHERE status IN (?, ?, ?) "
            "ORDER BY updated_at DESC",
            ("applied", "interviewing", "rejected"),
        ).fetchall()

        records = [
            {
                "title": r[0] or "",
                "company": r[1] or "",
                "location": r[2] or "",
                "status": r[3] or "",
                "ai_score": r[4] or 0,
                "advice": r[5] or "",
                "url": r[6] or "",
                "updated_at": r[7] or "",
                "first_seen_at": r[8] or "",
            }
            for r in raw_rows
        ]

        fmt = (format or "csv").lower()
        if fmt == "json":
            import json as _json_export

            body = _json_export.dumps(records, ensure_ascii=False, indent=2)
            filename = f"applications_{datetime.now().strftime('%Y%m%d_%H%M')}.json"
            return StreamingResponse(
                iter([body.encode("utf-8")]),
                media_type="application/json",
                headers={"Content-Disposition": f'attachment; filename="{filename}"'},
            )

        # CSV
        from io import StringIO

        buf = StringIO()
        if records:
            writer = csv.DictWriter(buf, fieldnames=list(records[0].keys()), delimiter=";")
            writer.writeheader()
            writer.writerows(records)
        else:
            buf.write("")
        filename = f"applications_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"
        return StreamingResponse(
            iter([buf.getvalue().encode("utf-8-sig")]),
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @router.get("/api/export/csv")
    def export_csv() -> StreamingResponse:
        """Stream all jobs as a CSV download (Content-Disposition attachment).

        Previously wrote a file into the workspace dir and returned its path,
        forcing the user to hunt the filesystem — now the browser just downloads.
        """
        from io import StringIO

        rows = container.db.export_jobs_for_csv()
        buf = StringIO()
        if rows:
            writer = csv.DictWriter(buf, fieldnames=list(rows[0].keys()), delimiter=";")
            writer.writeheader()
            writer.writerows(rows)
        filename = f"lavori_webapp_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"
        return StreamingResponse(
            iter([buf.getvalue().encode("utf-8-sig")]),
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    return router
